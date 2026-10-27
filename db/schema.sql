-- ClaimFlow schema
-- Money is stored as integer cents. Timestamps are timestamptz.

DROP SCHEMA IF EXISTS claimflow CASCADE;
CREATE SCHEMA claimflow;
SET search_path TO claimflow;

-- ---------------------------------------------------------------------------
-- Reference data: plans, coverage rules, providers, members
-- ---------------------------------------------------------------------------

CREATE TYPE service_category AS ENUM (
    'physiotherapy', 'massage_therapy', 'chiropractic',
    'psychology', 'dental_cleaning', 'vision'
);

CREATE TABLE plans (
    plan_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    sponsor      TEXT NOT NULL            -- the employer offering the plan
);

-- What a plan pays for each service category.
-- A category with no row here is not covered by that plan.
CREATE TABLE coverage_rules (
    plan_id                 TEXT NOT NULL REFERENCES plans(plan_id),
    category                service_category NOT NULL,
    coinsurance_pct         SMALLINT NOT NULL CHECK (coinsurance_pct BETWEEN 0 AND 100),
    per_visit_max_cents     BIGINT CHECK (per_visit_max_cents > 0),   -- NULL = no per-visit cap
    annual_max_cents        BIGINT NOT NULL CHECK (annual_max_cents > 0),
    requires_registered_provider BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (plan_id, category)
);

CREATE TABLE providers (
    provider_id          TEXT PRIMARY KEY,
    clinic_name          TEXT NOT NULL,
    practitioner_name    TEXT NOT NULL,
    category             service_category NOT NULL,
    registration_number  TEXT,               -- NULL if not registered with a regulatory college
    is_registered        BOOLEAN NOT NULL,
    address              TEXT NOT NULL,
    province             CHAR(2) NOT NULL
);

CREATE TABLE members (
    member_id        TEXT PRIMARY KEY,
    plan_id          TEXT NOT NULL REFERENCES plans(plan_id),
    first_name       TEXT NOT NULL,
    last_name        TEXT NOT NULL,
    date_of_birth    DATE NOT NULL,
    province         CHAR(2) NOT NULL,
    coverage_start   DATE NOT NULL,
    coverage_end     DATE,                   -- NULL = still active
    CHECK (coverage_end IS NULL OR coverage_end >= coverage_start)
);

-- ---------------------------------------------------------------------------
-- Claims lifecycle
-- ---------------------------------------------------------------------------

CREATE TYPE claim_status AS ENUM (
    'received',      -- stored, waiting in queue
    'extracting',    -- OCR / field extraction in progress
    'adjudicating',  -- rules engine running
    'needs_review',  -- waiting for a human
    'approved',      -- payout decided (full or partial)
    'denied',
    'paid',
    'failed'         -- processing error; see audit_log, retried or sent to dead-letter
);

CREATE TABLE claims (
    claim_id          UUID PRIMARY KEY,
    member_id         TEXT NOT NULL REFERENCES members(member_id),
    submitted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel           TEXT NOT NULL DEFAULT 'web' CHECK (channel IN ('web', 'mobile', 'paper')),
    status            claim_status NOT NULL DEFAULT 'received',
    -- Client-supplied key so a retried upload doesn't create a second claim.
    idempotency_key   TEXT UNIQUE,
    document_uri      TEXT NOT NULL,          -- e.g. s3://claims/<claim_id>.jpg
    -- Hash of the uploaded file; the same receipt submitted twice has the same hash.
    document_sha256   CHAR(64) NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX claims_member_idx   ON claims (member_id);
CREATE INDEX claims_status_idx   ON claims (status, submitted_at);   -- review queue ordering
CREATE INDEX claims_document_idx ON claims (document_sha256);        -- duplicate detection

-- One row per field the extractor pulled out, with its confidence.
-- Kept separate so we can compare extractor versions and measure accuracy.
CREATE TABLE extracted_fields (
    claim_id           UUID NOT NULL REFERENCES claims(claim_id),
    extractor_version  TEXT NOT NULL,
    field_name         TEXT NOT NULL CHECK (field_name IN (
                           'clinic_name', 'practitioner_name', 'registration_number',
                           'patient_name', 'service_date', 'service_description',
                           'service_category', 'amount_cents')),
    value              TEXT,
    confidence         NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    extracted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, extractor_version, field_name)
);

-- The service actually adjudicated, after extraction (and any reviewer corrections).
CREATE TABLE claim_lines (
    claim_id        UUID PRIMARY KEY REFERENCES claims(claim_id),
    provider_id     TEXT REFERENCES providers(provider_id),   -- NULL if provider couldn't be matched
    category        service_category NOT NULL,
    service_date    DATE NOT NULL,
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0)
);

CREATE TYPE decision_outcome AS ENUM ('approve', 'approve_partial', 'deny', 'review');

-- A claim can have several decisions over time (system routes to review,
-- then a reviewer approves). The latest one is the current decision.
CREATE TABLE decisions (
    decision_id     BIGSERIAL PRIMARY KEY,
    claim_id        UUID NOT NULL REFERENCES claims(claim_id),
    outcome         decision_outcome NOT NULL,
    payout_cents    BIGINT NOT NULL DEFAULT 0 CHECK (payout_cents >= 0),
    -- Machine-readable reasons, e.g. {ANNUAL_MAX_REACHED}. Empty for a clean approval.
    reason_codes    TEXT[] NOT NULL DEFAULT '{}',
    -- Plain-language explanation shown to the member or reviewer.
    explanation     TEXT NOT NULL,
    decided_by      TEXT NOT NULL,           -- 'system' or 'reviewer:<id>'
    rules_version   TEXT NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (outcome IN ('approve', 'approve_partial') OR payout_cents = 0)
);
CREATE INDEX decisions_claim_idx ON decisions (claim_id, decided_at DESC);

-- ---------------------------------------------------------------------------
-- Audit log: append-only record of everything that happens to a claim
-- ---------------------------------------------------------------------------

CREATE TABLE audit_log (
    event_id     BIGSERIAL PRIMARY KEY,
    claim_id     UUID REFERENCES claims(claim_id),
    actor        TEXT NOT NULL,              -- 'system', 'worker:extraction-2', 'reviewer:r17'
    action       TEXT NOT NULL,              -- 'status_change', 'override', 'retry', ...
    details      JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_claim_idx ON audit_log (claim_id, created_at);

CREATE FUNCTION forbid_audit_changes() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_changes();

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- Current (latest) decision per claim.
CREATE VIEW current_decisions AS
SELECT DISTINCT ON (claim_id) *
FROM decisions
ORDER BY claim_id, decided_at DESC, decision_id DESC;

-- How much of each annual maximum a member has used, per calendar year.
-- The rules engine reads this to calculate remaining benefit.
CREATE VIEW benefit_usage AS
SELECT c.member_id,
       l.category,
       EXTRACT(YEAR FROM l.service_date)::INT AS benefit_year,
       SUM(d.payout_cents)                   AS paid_cents
FROM claims c
JOIN claim_lines l       ON l.claim_id = c.claim_id
JOIN current_decisions d ON d.claim_id = c.claim_id
WHERE d.outcome IN ('approve', 'approve_partial')
GROUP BY c.member_id, l.category, EXTRACT(YEAR FROM l.service_date);

-- Review queue health: how many claims are waiting and how old they are.
CREATE VIEW review_queue_age AS
SELECT COUNT(*)                                                         AS waiting,
       COUNT(*) FILTER (WHERE submitted_at < now() - INTERVAL '5 days')  AS older_than_5_days,
       COUNT(*) FILTER (WHERE submitted_at < now() - INTERVAL '16 days') AS older_than_16_days,
       MAX(now() - submitted_at)                                        AS oldest_wait
FROM claims
WHERE status = 'needs_review';
