"""Database tests. Skipped unless DATABASE_URL points at a Postgres you don't mind wiping
(the schema script drops and recreates the `claimflow` schema)."""

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture
def db(dataset):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA.read_text())
        conn.execute((dataset["dir"] / "seed.sql").read_text())
        conn.execute("SET search_path TO claimflow")
        yield conn


def test_seed_loads(db, dataset):
    members = db.execute("SELECT count(*) FROM members").fetchone()[0]
    assert members == dataset["summary"]["members"]


def test_benefit_usage_matches_history(db, dataset):
    # Members in the annual_max scenario should show prior usage close to their maximum.
    rows = db.execute("""
        SELECT u.paid_cents, r.annual_max_cents
        FROM benefit_usage u
        JOIN members m USING (member_id)
        JOIN coverage_rules r ON r.plan_id = m.plan_id AND r.category = u.category
    """).fetchall()
    assert rows
    for paid, annual_max in rows:
        assert 0 < paid <= annual_max


def test_audit_log_is_append_only(db):
    db.execute("INSERT INTO audit_log (actor, action) VALUES ('test', 'ping')")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE audit_log SET action = 'changed'")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("DELETE FROM audit_log")


def test_denial_cannot_carry_a_payout(db):
    claim = db.execute("SELECT claim_id FROM claims LIMIT 1").fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("""INSERT INTO decisions (claim_id, outcome, payout_cents, explanation, decided_by, rules_version)
                      VALUES (%s, 'deny', 500, 'x', 'system', 'test')""", (claim,))
