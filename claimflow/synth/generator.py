"""Build a reproducible synthetic dataset: providers, members, benefit history,
submitted claims with receipt images, and the correct decision for each claim.

Output (in out_dir):
    receipts/<claim_id>.jpg   receipt images to submit through the pipeline
    ground_truth.jsonl        one line per submitted claim: true fields + expected decision
    seed.sql                  reference data and prior benefit usage for Postgres
    summary.json              counts by scenario and expected outcome
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from faker import Faker

from . import sql
from .catalog import (CATEGORIES, CLINIC_SUFFIXES, CREDENTIALS, PLANS, PLANS_BY_ID,
                      SERVICE_DESCRIPTIONS, TYPICAL_FEE_CENTS)
from .oracle import ReferenceAdjudicator, pct_of
from .receipts import LAYOUTS, degrade, render_receipt, to_jpeg

# Share of submitted claims per scenario. Whatever rounding leaves over goes to "clean".
SCENARIO_WEIGHTS = {
    "clean": 0.55,
    "poor_scan": 0.10,
    "duplicate": 0.05,
    "not_covered": 0.06,
    "coverage_inactive": 0.06,
    "provider_not_eligible": 0.06,
    "annual_max": 0.06,
    "amount_anomaly": 0.06,
}

# What each planted scenario should produce. Used by tests to check the generator
# plants what it says it plants.
EXPECTED_BY_SCENARIO = {
    "clean": ({"approve"}, None),
    "poor_scan": ({"approve"}, None),
    "duplicate": ({"deny"}, "DUPLICATE_SUBMISSION"),
    "not_covered": ({"deny"}, "SERVICE_NOT_COVERED"),
    "coverage_inactive": ({"deny"}, "COVERAGE_INACTIVE"),
    "provider_not_eligible": ({"deny"}, "PROVIDER_NOT_ELIGIBLE"),
    "annual_max": ({"approve_partial", "deny"}, "ANNUAL_MAX_REACHED"),
    "amount_anomaly": ({"review"}, "AMOUNT_ANOMALY"),
}

PAYMENT_METHODS = ["Visa", "Mastercard", "Debit", "Amex", "Cash", "E-transfer"]
PROVIDERS_PER_CATEGORY = 8
UNREGISTERED_PER_CATEGORY = 2


@dataclass
class ClaimSpec:
    scenario: str
    member: dict
    provider: dict
    category: str
    service_date: date
    amount_cents: int
    submitted_at: datetime
    duplicate_of: "ClaimSpec | None" = None
    claim_id: str = ""
    fields: dict = field(default_factory=dict)
    layout: str = ""
    image_bytes: bytes = b""


class Generator:
    def __init__(self, n_claims: int, n_members: int, seed: int, as_of: date) -> None:
        if n_claims < 20:
            raise ValueError("Use at least 20 claims so every scenario appears.")
        self.n_claims, self.n_members, self.as_of = n_claims, n_members, as_of
        self.rng = random.Random(seed)
        self.fake = Faker("en_CA")
        self.fake.seed_instance(seed)

        # Service dates fall in one benefit year so annual maximums are meaningful.
        year = as_of.year if as_of.month >= 3 else as_of.year - 1
        self.year_start = date(year, 1, 1)
        self.period_end = min(as_of - timedelta(days=7), date(year, 12, 31))

        self.providers: list[dict] = []
        self.members: list[dict] = []
        self.history: list[dict] = []            # prior approved claims, no images
        self.claims: list[ClaimSpec] = []
        self._used_pairs: set[tuple[str, str]] = set()
        self._next_member = 0

    # ------------------------------------------------------------------ ids
    def _uuid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def _random_date(self, start: date, end: date) -> date:
        return start + timedelta(days=self.rng.randint(0, max((end - start).days, 0)))

    def _amount(self, category: str, low_mult: float = 1.0, high_mult: float = 1.0) -> int:
        lo, hi = TYPICAL_FEE_CENTS[category]
        cents = self.rng.randint(int(lo * low_mult), int(hi * high_mult))
        step = self.rng.choice([500, 500, 500, 250, 100])   # most fees are round numbers
        return max(step, round(cents / step) * step)

    # ------------------------------------------------------------ entities
    def build_providers(self) -> None:
        n = 0
        for category in CATEGORIES:
            prefix_cred, prefix_reg = CREDENTIALS[category]
            for i in range(PROVIDERS_PER_CATEGORY):
                n += 1
                registered = i < PROVIDERS_PER_CATEGORY - UNREGISTERED_PER_CATEGORY
                city = self.fake.city()
                name_base = self.rng.choice([self.fake.last_name(), city, self.fake.street_name()])
                province = "ON" if self.rng.random() < 0.8 else self.fake.province_abbr()
                self.providers.append({
                    "provider_id": f"PRV-{n:04d}",
                    "clinic_name": f"{name_base} {self.rng.choice(CLINIC_SUFFIXES[category])}",
                    "practitioner_name": f"{self.fake.first_name()} {self.fake.last_name()}",
                    "credential": prefix_cred,
                    "category": category,
                    "registration_number": f"{prefix_reg}-{self.rng.randint(10000, 99999)}" if registered else None,
                    "is_registered": registered,
                    "address": f"{self.fake.street_address()}, {city}, {province} {self.fake.postcode()}",
                    "phone": self.fake.phone_number(),
                    "province": province,
                })

    def _new_member(self, plan_id: str | None = None) -> dict:
        self._next_member += 1
        plan_id = plan_id or self.rng.choices(["BASIC", "STANDARD", "ENHANCED"], [0.35, 0.40, 0.25])[0]
        member = {
            "member_id": f"M{100000 + self._next_member}",
            "plan_id": plan_id,
            "first_name": self.fake.first_name(),
            "last_name": self.fake.last_name(),
            "date_of_birth": self._random_date(date(1960, 1, 1), date(2004, 12, 31)),
            "province": "ON" if self.rng.random() < 0.8 else self.fake.province_abbr(),
            "coverage_start": self._random_date(date(2018, 1, 1), self.year_start - timedelta(days=30)),
            "coverage_end": None,
        }
        self.members.append(member)
        return member

    def build_members(self) -> None:
        for _ in range(self.n_members):
            self._new_member()

    def _provider(self, category: str, registered: bool = True) -> dict:
        pool = [p for p in self.providers if p["category"] == category and p["is_registered"] == registered]
        return self.rng.choice(pool)

    def _fresh_covered_pair(self) -> tuple[dict, str]:
        """A (member, category) the member's plan covers and no other claim uses,
        so benefit usage from one claim can't change another claim's outcome."""
        base = self.members[: self.n_members]
        for _ in range(10_000):
            member = self.rng.choice(base)
            category = self.rng.choice(PLANS_BY_ID[member["plan_id"]].rules).category
            if (member["member_id"], category) not in self._used_pairs:
                self._used_pairs.add((member["member_id"], category))
                return member, category
        raise RuntimeError("Ran out of unused member/category pairs; add more members.")

    def _submitted(self, service_date: date) -> datetime:
        day = min(service_date + timedelta(days=self.rng.randint(0, 21)), self.as_of - timedelta(days=1))
        return datetime.combine(day, time(self.rng.randint(7, 22), self.rng.randint(0, 59), self.rng.randint(0, 59)))

    # --------------------------------------------------------------- claims
    def _scenario_counts(self) -> dict[str, int]:
        counts = {s: max(1, round(w * self.n_claims)) for s, w in SCENARIO_WEIGHTS.items() if s != "clean"}
        counts["clean"] = self.n_claims - sum(counts.values())
        return counts

    def _spec(self, scenario: str, member: dict, category: str, *, registered: bool = True,
              service_date: date | None = None, amount: int | None = None) -> ClaimSpec:
        service_date = service_date or self._random_date(self.year_start, self.period_end)
        return ClaimSpec(
            scenario=scenario, member=member, provider=self._provider(category, registered),
            category=category, service_date=service_date,
            amount_cents=amount if amount is not None else self._amount(category),
            submitted_at=self._submitted(service_date),
        )

    def build_claims(self) -> None:
        counts = self._scenario_counts()

        for scenario in ("clean", "poor_scan"):
            for _ in range(counts[scenario]):
                member, category = self._fresh_covered_pair()
                self.claims.append(self._spec(scenario, member, category))

        for _ in range(counts["amount_anomaly"]):
            member, category = self._fresh_covered_pair()
            hi = TYPICAL_FEE_CENTS[category][1]
            amount = round(hi * self.rng.uniform(3.0, 5.0) / 500) * 500
            self.claims.append(self._spec("amount_anomaly", member, category, amount=amount))

        for _ in range(counts["provider_not_eligible"]):
            member, category = self._fresh_covered_pair()
            self.claims.append(self._spec("provider_not_eligible", member, category, registered=False))

        for _ in range(counts["not_covered"]):
            candidates = [(m, c) for m in self.members[: self.n_members] for c in CATEGORIES
                          if PLANS_BY_ID[m["plan_id"]].rule_for(c) is None]
            member, category = self.rng.choice(candidates)
            self.claims.append(self._spec("not_covered", member, category))

        for _ in range(counts["coverage_inactive"]):
            member = self._new_member()
            category = self.rng.choice(PLANS_BY_ID[member["plan_id"]].rules).category
            service_date = self._random_date(self.year_start + timedelta(days=60), self.period_end)
            member["coverage_end"] = service_date - timedelta(days=self.rng.randint(5, 50))
            self.claims.append(self._spec("coverage_inactive", member, category, service_date=service_date))

        for _ in range(counts["annual_max"]):
            self.claims.append(self._annual_max_claim())

        # Duplicates resubmit an earlier clean claim's exact file.
        originals = self.rng.sample([c for c in self.claims if c.scenario == "clean"], counts["duplicate"])
        for original in originals:
            dup = ClaimSpec(
                scenario="duplicate", member=original.member, provider=original.provider,
                category=original.category, service_date=original.service_date,
                amount_cents=original.amount_cents,
                submitted_at=original.submitted_at + timedelta(days=self.rng.randint(1, 20),
                                                               minutes=self.rng.randint(1, 600)),
                duplicate_of=original,
            )
            self.claims.append(dup)

        for c in self.claims:
            c.claim_id = self._uuid()
        self.claims.sort(key=lambda c: c.submitted_at)

    def _annual_max_claim(self) -> ClaimSpec:
        """A member who has nearly used up a yearly maximum, followed by a claim
        that goes over it."""
        member = self._new_member()
        rule = self.rng.choice(PLANS_BY_ID[member["plan_id"]].rules)
        category = rule.category
        service_date = self._random_date(self.year_start + timedelta(days=150), self.period_end)

        def payout_for(amount: int) -> int:
            p = pct_of(amount, rule.coinsurance_pct)
            return min(p, rule.per_visit_max_cents) if rule.per_visit_max_cents else p

        used, history = 0, []
        while True:
            amount = self._amount(category)
            paid = payout_for(amount)
            if used + paid > rule.annual_max_cents:
                break
            used += paid
            history.append((amount, paid))

        # Spread the prior visits across the year before the new claim.
        dates = sorted(self._random_date(self.year_start, service_date - timedelta(days=1))
                       for _ in history)
        provider = self._provider(category)
        for (amount, paid), d in zip(history, dates):
            self.history.append({
                "claim_id": self._uuid(), "member_id": member["member_id"],
                "provider_id": provider["provider_id"], "category": category,
                "service_date": d, "amount_cents": amount, "payout_cents": paid,
                "submitted_at": datetime.combine(d + timedelta(days=self.rng.randint(0, 7)), time(12, 0)),
                "document_sha256": hashlib.sha256(self.rng.randbytes(32)).hexdigest(),
            })

        # Amount at least the typical high fee, so the payout exceeds what's left.
        new_amount = self._amount(category, low_mult=1.0, high_mult=1.6)
        new_amount = max(new_amount, TYPICAL_FEE_CENTS[category][1])
        spec = self._spec("annual_max", member, category, service_date=service_date, amount=new_amount)
        spec.provider = provider
        return spec

    # -------------------------------------------------------------- render
    def render(self, receipts_dir: Path) -> None:
        receipts_dir.mkdir(parents=True, exist_ok=True)
        for c in self.claims:
            if c.duplicate_of is not None:
                continue
            p = c.provider
            c.fields = {
                "clinic_name": p["clinic_name"],
                "clinic_address": p["address"],
                "clinic_phone": p["phone"],
                "practitioner_name": p["practitioner_name"],
                "credential": p["credential"],
                "registration_number": p["registration_number"],
                "patient_name": f"{c.member['first_name']} {c.member['last_name']}",
                "service_date": c.service_date,
                "service_description": self.rng.choice(SERVICE_DESCRIPTIONS[c.category]),
                "amount_cents": c.amount_cents,
                "receipt_number": self.rng.choice([
                    str(self.rng.randint(10000, 99999)),
                    f"INV-{c.service_date.year}-{self.rng.randint(1, 9999):04d}",
                    f"R{self.rng.randint(100000, 999999)}",
                ]),
                "payment_method": self.rng.choice(PAYMENT_METHODS),
            }
            severity = "heavy" if c.scenario == "poor_scan" else "mild"
            c.layout = self.rng.choice(LAYOUTS)
            img = render_receipt(c.fields, self.rng, c.layout)
            c.image_bytes = to_jpeg(degrade(img, self.rng, severity), self.rng, severity)

        for c in self.claims:
            if c.duplicate_of is not None:
                c.fields, c.layout, c.image_bytes = c.duplicate_of.fields, c.duplicate_of.layout, c.duplicate_of.image_bytes
            (receipts_dir / f"{c.claim_id}.jpg").write_bytes(c.image_bytes)

    # --------------------------------------------------------- ground truth
    def ground_truth(self) -> list[dict]:
        oracle = ReferenceAdjudicator()
        for h in self.history:
            oracle.record_history(h["member_id"], h["category"], h["service_date"], h["payout_cents"])

        records = []
        for c in self.claims:     # already in submission order
            sha = hashlib.sha256(c.image_bytes).hexdigest()
            decision = oracle.decide(document_sha256=sha, member=c.member, provider=c.provider,
                                     category=c.category, service_date=c.service_date,
                                     amount_cents=c.amount_cents)
            f = c.fields
            records.append({
                "claim_id": c.claim_id,
                "scenario": c.scenario,
                "member_id": c.member["member_id"],
                "provider_id": c.provider["provider_id"],
                "submitted_at": c.submitted_at.isoformat(),
                "channel": self.rng.choices(["web", "mobile", "paper"], [0.45, 0.45, 0.10])[0],
                "document": f"receipts/{c.claim_id}.jpg",
                "document_sha256": sha,
                "layout": c.layout,
                "image_quality": "heavy" if c.scenario == "poor_scan" else "mild",
                "duplicate_of": c.duplicate_of.claim_id if c.duplicate_of else None,
                "fields": {
                    "clinic_name": f["clinic_name"],
                    "practitioner_name": f["practitioner_name"],
                    "registration_number": f["registration_number"],
                    "patient_name": f["patient_name"],
                    "service_date": f["service_date"].isoformat(),
                    "service_description": f["service_description"],
                    "service_category": c.category,
                    "amount_cents": f["amount_cents"],
                },
                "expected": {
                    "outcome": decision.outcome,
                    "payout_cents": decision.payout_cents,
                    "reason_codes": decision.reason_codes,
                    "explanation": decision.explanation,
                },
            })
        return records

    # ----------------------------------------------------------------- run
    def run(self, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.build_providers()
        self.build_members()
        self.build_claims()
        self.render(out_dir / "receipts")
        records = self.ground_truth()

        with open(out_dir / "ground_truth.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        (out_dir / "seed.sql").write_text(
            sql.seed_script(PLANS, self.providers, self.members, self.history))

        summary = {
            "claims": len(records),
            "members": len(self.members),
            "providers": len(self.providers),
            "history_claims": len(self.history),
            "by_scenario": dict(Counter(r["scenario"] for r in records)),
            "by_expected_outcome": dict(Counter(r["expected"]["outcome"] for r in records)),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary
