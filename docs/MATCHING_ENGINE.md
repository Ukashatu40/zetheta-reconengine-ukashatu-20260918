# Matching engine

Per-bank reconciliation of the internal ledger against the bank's statement. Entry point:
`MatchingOrchestrator` (`src/recon/matching/orchestrator.py`), invoked by `RunService`.

## Stage order

| # | Stage | Module | Result type | Outcome |
|---|---|---|---|---|
| 1 | Exact | `strategies/exact_set_based.py` (default), `exact.py` | `EXACT` | auto-matched |
| 2 | Cross-currency | `strategies/cross_currency.py` | `CROSS_CURRENCY` | auto-matched within the FX tolerance |
| 2 | Split and netted groups | `strategies/group.py` | `SPLIT`, `NETTED` | auto-matched if the group is unique |
| 3 | Fuzzy | `strategies/fuzzy.py` | `FUZZY` | auto-matched at confidence of 0.85 or more, otherwise pending review down to 0.60 |
| 4 | Rule stages | `strategies/rule_based.py`, `rules/` | `RULE` | auto-matched under per-bank tolerance |

`FAST` mode runs stage 1 only; `FULL` runs all. The orchestrator takes the unmatched pool in
`(txn_date, source_line_no, id)` order, so the same input always gives the same result (IB-08).
Groups run before fuzzy because a fuzzy pair in the review band would otherwise claim one part
of a split settlement and make the group unreachable (DD-23).

## Exact

Key: `(normalised_reference, amount_minor, currency, direction)` (AE-16). Date is deliberately
not part of the key, so a legitimate T+1 settlement still matches exactly. The default
implementation is one SQL statement that pairs every key group holding exactly one available
internal and one available external transaction (DD-21, DD-22); a group with several candidates
is left for later stages rather than guessed. The Python implementation (`exact.py`) is kept as
the reference and chooses the nearest date among candidates.

## Fuzzy

Candidates come from blocking, not from a full cross product: amount bucket, date window and
currency/direction constraints narrow the pool before any string comparison
(`blocking/buckets.py`, `blocking/candidates.py`). Direction and currency are hard gates
evaluated first (AE-09), so they cannot inflate a score. The remaining fields are scored
(`scoring/fields.py`, `scoring/weighted.py`) with the weights from `config/matching/weights.yaml`:

| Field | Weight | Scorer |
|---|---|---|
| reference | 0.35 | RapidFuzz Jaro-Winkler, with Levenshtein distance of 2 or less for references of 12+ characters |
| amount | 0.25 | within the bank's tolerance (default 1 minor unit) |
| date | 0.15 | business-day distance |
| counterparty | 0.10 | Token Set Ratio, 0.80 threshold |
| direction | 0.10 | equality |
| currency | 0.05 | equality |

A pair is auto-matched only if its confidence is at least 0.85 **and** at least two
independent signals agree (AE-37: a Jaro-Winkler score alone lets sequential identifiers such as
`INV000123` and `INV000124` match). The confidence is decided on the rounded value that is
stored (IB-04). Between 0.60 and 0.85 the pair becomes `PENDING_REVIEW`; below 0.60 nothing is
written. Every result stores a per-field explanation (`bench/explain.py` formats it).

## Groups

Split (one internal, several external) and netted (several internal, one external) matching.
Candidates must be **linked**: same reference stem (`PAY-100` for `PAY-100-1`, `PAY-100-2`) or
same counterparty within the date window. Inside a linked set the whole set is tried first, then
a bounded subset-sum dynamic programme (`subset_sum.py`, integer minor units). A group is
claimed only if the solution is unique; ambiguous or too-complex sets are counted and left for a
human. Same-currency sums must agree within the amount tolerance. When currencies differ, each
leg is converted to INR at the rate for its own booking date and the sums must agree within the
bank's `fx_tolerance_percent`, measured as `|a-b|/mean` (B4.2). The rate used goes into the
rationale and the audit entry. Each member pairing is one `match_results` row sharing a
`match_group_id`. Groups are auto only: there is no review band for them (DD-24).

The B4.2 example in the PDF does not reconcile as written (AE-32); the sample data contains both
the corrected variant (matched, `NETTED_SETTLEMENT`) and the as-written one (`FX_VARIANCE`).

## Rule stages

An ordered, per-bank registry (`stage_config.py`): `AmountToleranceRule`, then `DateOffsetRule`
(business-day offset against `config/calendars/in-2026.yaml`). Because the exact key excludes the
date, `DateOffsetRule` is effectively redundant behind the exact stage in this pipeline (DD-23);
it remains for banks configured with an exact-date key and is unit tested on its own.

## Claims and exclusivity

`match_claims` is a ledger: every proposed or confirmed match takes a claim on each transaction
it uses, and a partial unique index allows only one ACTIVE claim per transaction. A stage that
loses a race gets a conflict, not a duplicate match (IB-03, IB-05..07). Claims are released when
a match is rejected or unmatched.

## Review, manual matching and overrides

`matching/review.py`: confirm (both transactions become MATCHED) and reject (claims released, the
pair is remembered and is never proposed again by any stage, DD-18). `matching/manual.py`:
`POST /matches/manual` links two transactions an analyst identified, `POST /matches/{id}/unmatch`
undoes any match. All are audited with the actor.

## Known limits

- Fuzzy and group stages are unit and integration tested and exercised by the golden dataset, but
  their precision and recall are measured only on synthetic data (see `PERFORMANCE.md`).
- No phonetic or transliteration matching (DD-05).
- Multi-currency `:61:` lines are out of scope (DD-03).
