<!-- docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md -->

# Assessment Errors, Implementation Bugs and Design Decisions

Three ID series, so a code comment always resolves to exactly one entry:

- **AE-nn**: error or contradiction in the assessment PDF.
- **IB-nn**: bug in this codebase, with its regression test.
- **DD-nn**: deliberate scope or design decision.

Status is one of Implemented, Planned (not built yet) or Documented only.
Corrections marked "not independently verified" rest on the PDF's own
instruction, not on an external source.

---

## AE: PDF errors and contradictions

### AE-01: MT940 counterparty_account is not a fixed `:86:` line offset

- **PDF (A2.4):** `counterparty_account` comes from ":86: line 2-3".
- **Problem:** `:86:` is free text with a bank-defined layout; a fixed line index extracts narration fragments as account numbers.
- **Correction:** the parser keeps `:86:` as raw narrative and extracts no counterparty fields. Structured extraction, if ever needed, is a per-bank configurable strategy at normalisation.
- **Status:** Implemented.
- **Test:** `test_86_narration_is_captured_raw_without_subfield_extraction`.

### AE-02: CAMT.053 settlement_date is per-entry, not the CLBD balance date

- **PDF (A2.4):** `settlement_date` = `Stmt/Bal[...='CLBD']/Dt/Dt`, plus a circular CLBD/CLAV note.
- **Problem:** `Stmt/Bal` is one balance for the whole statement, so every transaction would get the same date.
- **Correction:** use `Ntry/ValDt/Dt`. Statement balances are captured separately for balance-level reconciliation.
- **Status:** Implemented.
- **Test:** `test_settlement_date_is_per_entry_val_dt_not_statement_balance_date`.

### AE-03: DIRECTION_REVERSAL SLA of 1 hour

- **PDF (A4.1 #8):** SLA 1 hour. The PDF itself instructs correcting it to 30 minutes with mandatory Compliance escalation.
- **Reasoning:** a reversed DR/CR indicator is a booking defect or manipulation. Totals can still balance in absolute terms while the net position is wrong by twice the amount, so time to containment is the whole control.
- **Correction:** 30 minutes, Tier 4, not auto-resolvable. SLA and severity are independent fields (AE-07).
- **Status:** Planned (exception engine). No test yet.

### AE-04: NPCI rounding attribution and float-drift conflation

- **PDF (C3):** NPCI used "round half up". A later note says this is reversed: NPCI used half-even and member banks were inconsistent. The same paragraph presents `0.1 + 0.2` as an example of banker's rounding.
- **Problem:** `0.1 + 0.2 != 0.3` is a binary-representation problem that exists before any rounding decision. It is not a rounding-mode problem. The reversal is the PDF's own instruction and is not independently verified.
- **Correction:** two independent controls. Money is never a float (Decimal and integer minor units), and the rounding mode is an explicit configured parameter defaulting to ROUND_HALF_EVEN.
- **Status:** Implemented.
- **Tests:** `test_float_representation_drift_demonstration`, `test_rounding_mode_is_configured_not_default`, `test_sum_of_10000_amounts_is_exact`.

### AE-05: Target breach timeline

- **PDF (C6):** the breach is dated "November-December 2013". The PDF's own note distinguishes malware installation, active theft period and public disclosure. Dates not independently verified.
- **Consequence for design:** the audit trail must separate event time from recorded time.
- **Status:** Implemented at schema level (`audit.audit_log` has `occurred_at` and `recorded_at`). No AuditLogger yet.

### AE-06: Blocking window narrower than the scoring window

- **PDF:** Day 3 blocks on "same amount ± tolerance AND date ± 2 days"; A3.4 scores up to T+3; A5.3 gives windows from T+0 to T+5.
- **Problem:** a conjunctive ±2-day block makes T+3 unreachable, makes split/netted matching impossible (their amounts differ by design), and hard-codes a UPI-shaped window.
- **Correction:** three independent blocking passes, unioned and deduplicated, with a per-bank configurable window and a capped, flagged candidate list.
- **Status:** Implemented (split/netted candidate generation not built).
- **Tests:** `tests/unit/matching/test_candidates.py`.

### AE-07: Severity is defined circularly from SLA

- **PDF (Day 4):** severity derives from SLA, which derives from category, so severity carries no independent information. A ₹50 lakh DATE_MISMATCH would be CRITICAL by value and LOW by SLA.
- **Correction:** severity = max(category base severity, value-derived severity), computed independently of SLA, on the INR-equivalent amount.
- **Status:** Planned (enum exists; classifier not built).

### AE-08: Confidence band boundaries are ambiguous

- **PDF (A3.4):** "above 0.85" and "between 0.60 and 0.85" both plausibly include 0.85.
- **Correction:** `> 0.85` auto-match, `0.60 <= x <= 0.85` review, `< 0.60` no match. Ties go to the safer bucket.
- **Status:** Implemented.
- **Tests:** `test_confidence_exactly_at_auto_threshold_goes_to_review_not_auto_match`, `test_confidence_exactly_at_review_threshold_goes_to_review`.

### AE-09: Direction and currency inflate every surviving candidate

- **PDF (A3.4):** direction and currency carry weight 0.15 combined while also being "must match", so they always score 1.0 and add a constant 0.15.
- **Correction:** both are hard gates checked before scoring. Auto-match additionally requires at least two of {reference, amount, date, counterparty} to clear their own thresholds.
- **Addendum:** with A3.4's actual weights, the branch `confidence > 0.85 and signals < 2` cannot be reached through `score_candidate()`. The only continuous field that can score without clearing its threshold is reference, and its non-clearing maximum contribution is about 0.32; adding the 0.15 gate total gives roughly 0.47. The rule is kept as defence against future reweighting and is tested directly against `_decide()`.
- **Status:** Implemented.
- **Test:** `test_decide_directly_exercises_the_ae09_branch`.

### AE-10: Day 3 writes every score above 0.60 as a match

- **PDF:** Day 3 writes matches above 0.60 to `match_results`; A3.4 routes 0.60 to 0.85 to human review.
- **Correction:** rows are written but with `status = 'PENDING_REVIEW'`, and excluded from match metrics until confirmed. A check constraint requires `confidence > 0.85` for `AUTO_MATCHED`.
- **Status:** Implemented. No reviewer workflow exists yet, so PENDING_REVIEW rows are never confirmed or rejected.
- **Test:** `test_review_band_pair_is_written_pending_review_claimed_but_not_matched`.

### AE-12: The DR/CR enum cannot represent reversals

- **PDF:** A2.1 lists MT940 marks D, C, RD, RC; the A2.4 canonical enum has only DR and CR.
- **Correction:** keep `Direction` as DR/CR; carry `is_reversal` and `reverses_reference` separately.
- **Status:** Implemented.
- **Tests:** `test_reversal_with_reference_is_valid`, `test_reversal_debit_mark_is_recognised`, `test_normalise_direction_recognises_all_format_vocabularies`.

### AE-13: Currency sourced from statement-level `:60F:` conflicts with multi-currency statements

- **Correction:** resolution order entry-level, then statement-level, then bank default. The source is recorded per transaction as `currency_source`.
- **Status:** Implemented.
- **Tests:** `test_resolve_currency_*`.

### AE-14: Fixed byte offsets in `:61:` break on bank deviations

- **PDF (A2.4):** `txn_id` is ":61: ref (pos 17-32)".
- **Problem:** the optional funds code and variable-length amount shift every later position.
- **Correction:** extract every sub-field with one regex built on character-class transitions. The missing-funds-code deviation then needs no special code.
- **Status:** Implemented.
- **Tests:** `test_missing_funds_code_parses_correctly`, `test_parse_field_61_extracts_all_structural_parts`.

### AE-15: `txn_id` is not a unique identifier

- **Problem:** CAMT `EndToEndId` is often `NOTPROVIDED`; MT940 references are bank-scoped and reused.
- **Correction:** system-generated UUID primary key; `txn_id` is indexed but never unique.
- **Status:** Implemented structurally. No dedicated test.

### AE-16: The exact-match key is stated two incompatible ways

- **PDF:** A3.1 says reference+amount+currency+date; Day 3 says txn_id+amount+currency+direction.
- **Correction:** key is (normalised_reference, amount_minor, currency, direction). Date is a secondary disambiguator, not a key part.
- **Status:** Implemented.
- **Tests:** `tests/integration/test_exact_matching.py`.

### AE-17: DECIMAL(18,4) ignores per-currency precision

- **Correction:** store NUMERIC(20,4) plus `amount_minor` (BIGINT) and `currency_exponent` from an ISO 4217 table; all comparison and summation runs on integer minor units.
- **Status:** Implemented.
- **Tests:** `test_per_currency_exponent`, `test_jpy_quantizes_to_zero_decimal_places`.

### AE-26: PAN masking specified at the wrong layer

- **PDF (A9.3):** mask in the normalisation layer, but Day 1 persists raw data before normalisation, so full PANs would be stored.
- **Correction:** mask at the ingestion boundary, before any persistence.
- **Status:** Planned. No PAN guard exists yet.

### AE-31: Timeline stated as 15 days and 7 days

- **PDF:** cover says 15 days, Part D says 7. Interpreted as seven work packages across a 15-day window; documented in README.
- **Status:** Documented only.

### AE-32: The B4.2 netting figures do not reconcile

- **PDF:** GBP 142,857.14 at 84.00 is ₹1,19,99,999.76 (about ₹1.2 crore), not the stated ₹1,50,00,000. The "0.63%" figure is the difference between two rates (84.00 vs 83.47), not between the two amounts.
- **Plan:** the golden dataset carries both the corrected case (expected NETTED match) and the as-written case (expected FX_VARIANCE).
- **Status:** Planned (cross-currency matching).

### AE-33: A5.2's timezone example is arithmetically wrong

- **PDF:** "11:30 PM IST on 15 March will be recorded as 16 March in UTC+0". 23:30 IST is 18:00 UTC on 15 March.
- **Status:** Implemented.
- **Tests:** `test_ist_2330_does_not_roll_over_to_next_day_in_utc`, `test_singapore_utc_plus_8_does_roll_over_for_a_late_transaction`.

### AE-34: UPI December 2024 statistics

- **PDF (A1.4):** 14.96 billion transactions worth ₹20.64 lakh crore.
- **Found by search:** NPCI reported about 16.73 billion transactions and about ₹23.25 lakh crore for December 2024 (newsonair.gov.in report of 2 January 2025). No design consequence.
- **Status:** Documented only.

### AE-35: Exact-match rate stated as 70-85% and as above 95%

- **PDF:** A3.1 says 70-85% of all records; Day 3 targets above 95% of matchable records.
- **Status:** Implemented, with a caveat. `RunMetrics` reports `exact_match_rate_of_total` and `exact_match_rate_of_matchable` separately. "Matchable" here means resolved by exact or fuzzy auto-match within the same orchestrator run — it does not yet account for a PENDING_REVIEW row later confirmed by a human, since no reviewer workflow exists. The engine is not tuned toward either rate.
- **Test:** `test_match_rates_are_reported_separately_and_review_rows_are_excluded`.

### Other Phase 0 observations, not yet individually numbered

Unverified or lower-impact: the "Section 26A" penalty citation could not be verified; community PostgreSQL has no TDE, so encryption at rest is volume-level; the Paytm deadline was extended from 29 February to 15 March 2024; subset-sum is many-to-one, not many-to-many, so netted matching will be a bounded heuristic.

---

## IB: implementation bugs

### IB-01: date-only timestamps shifted a day back for positive-UTC banks

- **What:** midnight IST converts to 18:30 UTC the previous day; using `.utc.date()` as `txn_date` would have moved every Indian transaction back a day.
- **Fix:** `NormalisedTimestamp.local_date`; `txn_date` and `settlement_date` use it.
- **Caught:** while designing the pipeline, before persistence.
- **Tests:** `test_local_date_reflects_source_statement_date_not_utc_shifted_date`, `test_txn_date_uses_local_calendar_date_not_utc_shifted_date`.

### IB-02: check-constraint names doubled, truncated and hash-suffixed (3 occurrences)

- **What:** hand-written migrations passed already-prefixed names; the naming convention added its prefix again; PostgreSQL silently truncated names over 63 bytes.
- **Fix:** migrations always pass the bare name. `normalised_transactions` needed a rename migration (`3f392c4a49dd`).
- **Standing rule:** verify constraint names against `\d <table>`, never against the migration source.
- **Test:** none; a schema-authoring discipline issue.

### IB-03: a claim conflict rolled back the whole session, and a half-claimed pair was left behind

- **What:** `ClaimsService.claim()` called `session.rollback()`, discarding earlier uncommitted matches. Separately, an internal claim could succeed while the external claim conflicted, leaving an orphaned ACTIVE claim.
- **Fix:** savepoint per claim (`begin_nested`); `claim_pair()` claims both sides atomically.
- **Tests:** `test_failed_claim_does_not_discard_earlier_uncommitted_claims`, `test_claim_pair_conflict_leaves_no_orphaned_claim_on_the_other_side`, `test_two_externals_competing_for_one_internal_only_first_wins_and_keeps_its_match`.

### IB-04: confidence decided on the unrounded value, stored rounded

- **What:** a raw 0.85004 would be decided AUTO_MATCH and stored as 0.850, violating the `auto_matched_requires_high_confidence` check.
- **Fix:** quantise to 3 decimals once, before deciding.
- **Test:** `test_confidence_is_quantised_to_three_decimals_before_deciding`.

### IB-05: exact matching reused an already-matched candidate (fixed)

- **What:** found by reading, not by a failing test. `_disambiguate` picks from the whole hash bucket, including internals already matched earlier in the same run. With two externals and two internals sharing one key, the second external can pick the same internal, hit a claim conflict and be skipped even though a free internal exists.
- **Impact:** a valid pair is missed at the exact level (fuzzy may recover it). It is not a false match.
- **Fix:** the matched internal is removed from its hash bucket after a successful match.
- **Test:** `test_second_external_uses_a_free_internal_instead_of_reusing_a_matched_one`.

---

## DD: design and scope decisions

- **DD-01: sign lives on `direction`, never on `amount`.** The parser captures a negative amount as text; `CanonicalTransaction` rejects it. Parsing and normalising stay separate stages.
- **DD-02: PDF requirement R29 (CAMT.053 XSD validation) is not implemented.** Structural checks (namespace, root element, `Stmt` present) are used instead. Vendoring the schema, or fetching it at parse time, was judged not worth the cost or the XXE-adjacent risk.
- **DD-03: multi-currency `:61:` lines are out of scope.** Base MT940 carries no per-line currency.
- **DD-04: a standalone `:86:` with no preceding `:61:` is lexed and then dropped.**
- **DD-05: no phonetic or transliteration matching.** Soundex fits English poorly and RapidFuzz ships no phonetic algorithms; name variance is left to Token Set Ratio.
- **DD-06: reference truncate and pad are opt-in only.** Silent truncation would make REFERENCE_TRUNCATED undetectable for references we shortened ourselves.
