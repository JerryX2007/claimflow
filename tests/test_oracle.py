from datetime import date

from claimflow.synth.oracle import ReferenceAdjudicator, pct_of

REGISTERED = {"is_registered": True}
UNREGISTERED = {"is_registered": False}


def member(plan_id="BASIC", start=date(2020, 1, 1), end=None):
    return {"member_id": "M1", "plan_id": plan_id, "coverage_start": start, "coverage_end": end}


def decide(adj, *, sha="a", m=None, provider=REGISTERED, category="physiotherapy",
           service_date=date(2026, 5, 1), amount=10_000):
    return adj.decide(document_sha256=sha, member=m or member(), provider=provider,
                      category=category, service_date=service_date, amount_cents=amount)


def test_pct_rounds_half_up():
    assert pct_of(12_345, 80) == 9_876          # 9876.0
    assert pct_of(10_001, 50) == 5_001          # 5000.5 -> 5001


def test_clean_claim_paid_at_coinsurance():
    # STANDARD physio: 80%, $80 per-visit max. $90 visit -> $72.
    d = decide(ReferenceAdjudicator(), m=member("STANDARD"), amount=9_000)
    assert (d.outcome, d.payout_cents, d.reason_codes) == ("approve", 7_200, [])


def test_per_visit_max_caps_payout():
    # BASIC physio: 80% of $120 = $96, capped at $60.
    d = decide(ReferenceAdjudicator(), amount=12_000)
    assert (d.outcome, d.payout_cents) == ("approve", 6_000)
    assert d.reason_codes == ["PER_VISIT_MAX_APPLIED"]


def test_annual_max_gives_partial_then_denies():
    adj = ReferenceAdjudicator()
    adj.record_history("M1", "physiotherapy", date(2026, 2, 1), 48_000)   # $500 max, $480 used
    first = decide(adj, sha="a", amount=12_000)
    assert (first.outcome, first.payout_cents) == ("approve_partial", 2_000)
    assert "ANNUAL_MAX_REACHED" in first.reason_codes
    second = decide(adj, sha="b", amount=12_000)
    assert (second.outcome, second.payout_cents, second.reason_codes) == ("deny", 0, ["ANNUAL_MAX_REACHED"])


def test_annual_max_resets_each_year():
    adj = ReferenceAdjudicator()
    adj.record_history("M1", "physiotherapy", date(2025, 6, 1), 50_000)
    assert decide(adj, amount=9_000).outcome == "approve"


def test_duplicate_document_denied():
    adj = ReferenceAdjudicator()
    assert decide(adj, sha="same").outcome == "approve"
    assert decide(adj, sha="same").reason_codes == ["DUPLICATE_SUBMISSION"]


def test_service_after_coverage_end_denied():
    d = decide(ReferenceAdjudicator(), m=member(end=date(2026, 4, 1)))
    assert d.reason_codes == ["COVERAGE_INACTIVE"]


def test_uncovered_category_denied():
    d = decide(ReferenceAdjudicator(), category="vision")
    assert d.reason_codes == ["SERVICE_NOT_COVERED"]


def test_unregistered_provider_denied():
    d = decide(ReferenceAdjudicator(), provider=UNREGISTERED)
    assert d.reason_codes == ["PROVIDER_NOT_ELIGIBLE"]


def test_unusual_amount_goes_to_review_not_denial():
    d = decide(ReferenceAdjudicator(), amount=50_000)   # typical physio max is $130
    assert (d.outcome, d.payout_cents, d.reason_codes) == ("review", 0, ["AMOUNT_ANOMALY"])
