import hashlib
import json
from datetime import date

from claimflow.synth.generator import EXPECTED_BY_SCENARIO, SCENARIO_WEIGHTS, Generator

from .conftest import AS_OF


def test_every_scenario_is_present(dataset):
    assert set(dataset["summary"]["by_scenario"]) == set(SCENARIO_WEIGHTS)
    assert dataset["summary"]["claims"] == 80


def test_planted_scenarios_produce_expected_decisions(dataset):
    for r in dataset["records"]:
        outcomes, reason = EXPECTED_BY_SCENARIO[r["scenario"]]
        exp = r["expected"]
        assert exp["outcome"] in outcomes, r
        if reason:
            assert reason in exp["reason_codes"], r
        else:
            # Clean claims may hit the per-visit cap, never anything else.
            assert set(exp["reason_codes"]) <= {"PER_VISIT_MAX_APPLIED"}, r


def test_payouts_are_sane(dataset):
    for r in dataset["records"]:
        exp, amount = r["expected"], r["fields"]["amount_cents"]
        assert isinstance(amount, int) and amount > 0
        assert 0 <= exp["payout_cents"] <= amount
        if exp["outcome"] in ("deny", "review"):
            assert exp["payout_cents"] == 0
        else:
            assert exp["payout_cents"] > 0
        assert exp["explanation"]


def test_receipt_files_match_recorded_hash(dataset):
    for r in dataset["records"]:
        data = (dataset["dir"] / r["document"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == r["document_sha256"]


def test_duplicates_resubmit_an_approved_original(dataset):
    by_id = {r["claim_id"]: r for r in dataset["records"]}
    dups = [r for r in dataset["records"] if r["scenario"] == "duplicate"]
    assert dups
    for d in dups:
        original = by_id[d["duplicate_of"]]
        assert original["document_sha256"] == d["document_sha256"]
        assert original["submitted_at"] < d["submitted_at"]
        assert original["expected"]["outcome"] == "approve"


def test_claims_are_in_submission_order(dataset):
    times = [r["submitted_at"] for r in dataset["records"]]
    assert times == sorted(times)


def test_service_dates_before_submission_and_today(dataset):
    for r in dataset["records"]:
        service = date.fromisoformat(r["fields"]["service_date"])
        assert service <= date.fromisoformat(r["submitted_at"][:10]) < AS_OF


def test_seed_sql_written(dataset):
    sql = (dataset["dir"] / "seed.sql").read_text()
    assert "INSERT INTO members" in sql and sql.rstrip().endswith("COMMIT;")


def test_same_seed_gives_same_data(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    Generator(20, 30, seed=3, as_of=AS_OF).run(a)
    Generator(20, 30, seed=3, as_of=AS_OF).run(b)
    assert (a / "ground_truth.jsonl").read_text() == (b / "ground_truth.jsonl").read_text()
    first = json.loads((a / "ground_truth.jsonl").read_text().splitlines()[0])
    assert (a / first["document"]).read_bytes() == (b / first["document"]).read_bytes()
