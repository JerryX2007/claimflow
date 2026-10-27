"""Reference adjudicator used to compute the *correct* answer for each synthetic claim.

It works on the true receipt values, so it represents what a perfect system would
decide. The real pipeline (Phase 4) works on *extracted* values; comparing the two
shows how much accuracy is lost to extraction errors.

Checks run in this order and the first failure decides the outcome:
    1. DUPLICATE_SUBMISSION    same document already submitted       -> deny
    2. COVERAGE_INACTIVE       service date outside coverage period  -> deny
    3. SERVICE_NOT_COVERED     plan has no rule for this category    -> deny
    4. PROVIDER_NOT_ELIGIBLE   provider not registered, rule needs it -> deny
    5. AMOUNT_ANOMALY          amount far above typical fee          -> review
    6. payout calculation      coinsurance, per-visit max, annual max
"""

from dataclasses import dataclass, field
from datetime import date

from .catalog import ANOMALY_MULTIPLIER, PLANS_BY_ID, TYPICAL_FEE_CENTS


@dataclass
class Decision:
    outcome: str                       # approve | approve_partial | deny | review
    payout_cents: int = 0
    reason_codes: list[str] = field(default_factory=list)
    explanation: str = ""


def dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def pct_of(amount_cents: int, pct: int) -> int:
    """Percentage of an amount in cents, rounded half up."""
    return (amount_cents * pct + 50) // 100


class ReferenceAdjudicator:
    """Stateful: remembers documents seen and benefits used, so claims must be
    fed in submission order."""

    def __init__(self) -> None:
        self.seen_hashes: set[str] = set()
        self.usage: dict[tuple[str, str, int], int] = {}   # (member, category, year) -> paid cents

    def record_history(self, member_id: str, category: str, service_date: date, paid_cents: int) -> None:
        key = (member_id, category, service_date.year)
        self.usage[key] = self.usage.get(key, 0) + paid_cents

    def decide(self, *, document_sha256: str, member: dict, provider: dict,
               category: str, service_date: date, amount_cents: int) -> Decision:
        if document_sha256 in self.seen_hashes:
            return Decision("deny", 0, ["DUPLICATE_SUBMISSION"],
                            "This receipt has already been submitted.")
        self.seen_hashes.add(document_sha256)

        start, end = member["coverage_start"], member["coverage_end"]
        if service_date < start or (end is not None and service_date > end):
            return Decision("deny", 0, ["COVERAGE_INACTIVE"],
                            f"Coverage was not active on {service_date.isoformat()}.")

        plan = PLANS_BY_ID[member["plan_id"]]
        rule = plan.rule_for(category)
        if rule is None:
            label = category.replace("_", " ")
            return Decision("deny", 0, ["SERVICE_NOT_COVERED"],
                            f"The {plan.name} plan does not cover {label}.")

        if rule.requires_registered_provider and not provider["is_registered"]:
            return Decision("deny", 0, ["PROVIDER_NOT_ELIGIBLE"],
                            "The provider is not a registered practitioner, which this plan requires.")

        typical_high = TYPICAL_FEE_CENTS[category][1]
        if amount_cents > typical_high * ANOMALY_MULTIPLIER:
            return Decision("review", 0, ["AMOUNT_ANOMALY"],
                            f"Amount {dollars(amount_cents)} is well above the typical "
                            f"fee (up to {dollars(typical_high)}); needs a reviewer.")

        reasons: list[str] = []
        payout = pct_of(amount_cents, rule.coinsurance_pct)
        if rule.per_visit_max_cents is not None and payout > rule.per_visit_max_cents:
            payout = rule.per_visit_max_cents
            reasons.append("PER_VISIT_MAX_APPLIED")

        key = (member["member_id"], category, service_date.year)
        used = self.usage.get(key, 0)
        remaining = max(rule.annual_max_cents - used, 0)

        if remaining == 0:
            return Decision("deny", 0, ["ANNUAL_MAX_REACHED"],
                            f"The {service_date.year} annual maximum of "
                            f"{dollars(rule.annual_max_cents)} has been used.")

        outcome = "approve"
        if payout > remaining:
            payout = remaining
            outcome = "approve_partial"
            reasons.append("ANNUAL_MAX_REACHED")

        self.usage[key] = used + payout

        explanation = (f"Paid {dollars(payout)} of {dollars(amount_cents)} "
                       f"({rule.coinsurance_pct}% coverage")
        if "PER_VISIT_MAX_APPLIED" in reasons:
            explanation += f", capped at the {dollars(rule.per_visit_max_cents)} per-visit maximum"
        if outcome == "approve_partial":
            explanation += f", limited by the remaining {service_date.year} annual maximum"
        explanation += ")."
        return Decision(outcome, payout, reasons, explanation)
