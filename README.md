# ClaimFlow

**Automated adjudication for health and dental benefit claims, built around the claims that automation usually can't handle.**

## The problem

Simple electronic health claims are already fast at most Canadian insurers, often paid within a day. The cost and the complaints live somewhere else: in the **manual-review queue**. Claims fall out of the automated path when the receipt is a blurry phone photo, the data doesn't match, the provider can't be verified, or a benefit rule needs interpretation. Those claims wait for a human examiner, and when volume spikes the queue becomes a backlog.

In 2024, members of the federal Public Service Health Care Plan saw exactly this: thousands of claims sitting more than 16 days while a public dashboard reported one to five days. Delays like that put real financial pressure on people paying out of pocket for care.

## What ClaimFlow does

ClaimFlow aims to do two things:

1. **Safely raise the straight-through processing rate.** More clean claims get paid with no human touch, and every automated decision carries a plain-language reason.
2. **Make the human queue faster.** Reviewers see the receipt beside the extracted fields and the specific reason each claim was flagged, so a decision takes seconds instead of minutes.

The system never auto-denies a claim it isn't confident about. Low confidence always routes to a person.

## Architecture

```
Member upload -> Intake API -> Queue -> Extraction worker (OCR + LLM)
                                              |
                                              v
             Auto-approve / Human review / Deny <- Decision router <- Adjudication (rules + fraud checks)
```

| Component | Tech | Why |
|---|---|---|
| Intake API | FastAPI | Accepts uploads, returns a claim ID immediately, doesn't block on processing |
| Document storage | MinIO (S3-compatible) | Same API as AWS S3, runs locally |
| Queue | RabbitMQ | Absorbs volume spikes; workers scale horizontally |
| Extraction | Tesseract OCR + LLM | Pulls provider, date, service, amount, each with a confidence score |
| Adjudication | Python rules engine | Coverage, annual maximums, provider eligibility, dates, payout calculation |
| Database | PostgreSQL | Claims, decisions and an append-only audit log |
| Reviewer UI | Streamlit | Receipt side-by-side with extracted fields and flag reasons |

## Roadmap

- [x] **Phase 1 — Foundation:** database schema, synthetic data and receipt generator with ground truth
- [ ] **Phase 2 — Intake:** upload endpoint, object storage, queue, idempotency
- [ ] **Phase 3 — Extraction:** OCR + field extraction with confidence scores, accuracy measured against ground truth
- [ ] **Phase 4 — Adjudication:** rules engine, payout calculation, fraud checks (duplicates, outlier amounts)
- [ ] **Phase 5 — Review:** decision router, reviewer dashboard, audit trail of overrides
- [ ] **Phase 6 — Scale:** Docker Compose, multiple workers, load testing, CI

## Results

*To be filled in as phases complete.*

| Metric | Value |
|---|---|
| Field extraction accuracy | — |
| Straight-through processing rate | — |
| Planted issues caught | — |
| False positive rate (flags on clean claims) | — |
| Median processing time per claim | — |
| Throughput, 1 worker vs. 4 workers | — |

## Getting started

```bash
# 1. Install dependencies (Python 3.11+)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Start Postgres, MinIO and RabbitMQ
cp .env.example .env
docker compose up -d

# 3. Generate synthetic data (receipts, members, plans, ground truth)
#    --preview also saves data/preview.png, a grid of sample receipts
python scripts/generate_data.py --claims 300 --seed 42 --preview

# 4. Load the schema and seed data
psql "$DATABASE_URL" -f db/schema.sql
psql "$DATABASE_URL" -f data/seed.sql

# 5. Run tests
pytest
```

Database tests run only when `DATABASE_URL` is set. They drop and recreate the `claimflow` schema, so point them at a development database.

## Project layout

```
claimflow/
  synth/
    catalog.py     plans, coverage rules, service categories, typical fees
    generator.py   builds members, providers, history and claims; writes outputs
    oracle.py      reference adjudicator: the correct decision for each claim
    receipts.py    renders receipts in three layouts and degrades them
    sql.py         writes seed.sql
db/schema.sql      Postgres schema, views and the append-only audit trigger
scripts/           command-line entry points
tests/             pytest suite
docs/decisions.md  design decision log
```

## Synthetic data

No real personal or health information is used anywhere in this project. `scripts/generate_data.py` creates fake plans, members, providers and receipt images, and records the true answer for every claim in `data/ground_truth.jsonl`. That makes extraction accuracy and decision accuracy measurable.

Roughly a third of generated claims contain a planted problem so the system has something real to catch:

| Scenario | What's wrong | Expected outcome |
|---|---|---|
| `clean` | Nothing | Approve |
| `poor_scan` | Blurry, rotated, noisy or faded image | Approve (if extraction succeeds) |
| `duplicate` | Same receipt submitted twice | Deny the second |
| `not_covered` | Service isn't in the member's plan | Deny |
| `coverage_inactive` | Service date after coverage ended | Deny |
| `provider_not_eligible` | Provider isn't a registered practitioner | Deny |
| `annual_max` | Member has almost used their yearly maximum | Approve a partial payout |
| `amount_anomaly` | Amount far above normal for the service | Human review |

## Design decisions

See [`docs/decisions.md`](docs/decisions.md) for the reasoning behind major choices and the tradeoffs considered.

## Privacy and security

- Synthetic data only; no real PII or PHI.
- Secrets come from environment variables, never committed (`.env` is git-ignored).
- Logs reference claim IDs, not member names.
- Every decision and human override is written to an append-only audit log.
