# Design decisions

A running log of choices made while building ClaimFlow, with the alternatives considered. Each entry doubles as a ready-made answer for "tell me about a technical decision you made."

## 001 — Focus on the manual-review queue, not simple claims

**Context:** Electronic health claims at major Canadian insurers are often already paid within a day.
**Decision:** Measure success by straight-through processing rate and reviewer time per claim, not raw speed on clean claims.
**Why:** That's where the cost and the backlogs are. Pitching "claims are slow" would ignore what insurers already do well.

## 002 — Store money as integer cents

**Decision:** All amounts are `BIGINT` cents, never floats.
**Why:** Floating-point rounding errors in payouts are unacceptable. $0.1 + $0.2 must equal $0.3.

## 003 — Generate synthetic receipts with ground truth

**Alternatives:** Public receipt datasets (mostly retail, wrong domain); real receipts (privacy risk).
**Decision:** Render fake clinic receipts with known values and planted problems.
**Why:** Every claim has a known correct answer, so extraction and decision accuracy are measurable. No privacy risk.

## 004 — Append-only audit log

**Decision:** Every state change and human override writes a row to `audit_log`; rows are never updated or deleted (enforced by a trigger).
**Why:** Regulated industry. Someone must be able to reconstruct why any claim was paid or denied.

## 005 — Ambiguous dates are kept on purpose

**Context:** Receipts write dates as `08/02/2026`, which could be August 2 or February 8.
**Decision:** The generator randomly uses day-first and month-first formats, and ground truth records the true date.
**Why:** Real Canadian receipts mix both conventions. The extractor must handle the ambiguity (e.g. cross-check against the submission date, or flag low confidence), and ground truth shows when it gets it wrong.

## 006 — A test caught an unrealistic plan design

**Context:** A "clean" claim came back as a partial payment. The Enhanced plan's $400 vision maximum was below the price of a typical pair of eyeglasses, so an ordinary claim already exceeded it.
**Decision:** Raised the vision maximum to $500 and documented the constraint in `catalog.py`.
**Why:** Planted scenarios must be the only source of problems, or accuracy numbers become meaningless. The test that compares each scenario's intended outcome against the reference adjudicator is what caught it.

## 007 — Never auto-deny on low confidence

**Decision:** If extraction confidence is low, the claim goes to human review, even if the rules would deny it.
**Why:** A wrong denial harms a member far more than a slightly slower approval. Automation should be confident before it says no.
