# Assessment Errors and Corrections

This document tracks every deliberate error, contradiction, or
questionable claim identified in the assessment PDF, per the format the
PDF itself specifies. Each entry is referenced from the test that
demonstrates our handling — search the codebase for the entry's ID (e.g.
`AE-02`) to find it.

Entries are added as they're encountered during implementation, not
front-loaded speculatively — this list will grow through every remaining
work package.

---

## AE-01: MT940 counterparty_account cannot be a fixed `:86:` line offset

**PDF says:** A2.4's canonical field table maps `counterparty_account` to
"`:86:` line 2-3".

**Why it's wrong:** `:86:` is unstructured free text in base MT940. Its
internal layout is bank-defined — some banks use structured sub-fields,
some use `/TAG/` conventions, some use nothing at all. A fixed line index
will silently extract narration fragments as account numbers on any bank
that doesn't happen to match the assumed layout.

**Correction implemented:** The MT940 parser (`recon.ingestion.parsers.mt940`)
captures `:86:` content as raw narrative text only. No sub-field
extraction (counterparty name, account number) happens at the parsing
stage. Structured extraction, where it's ever needed, is deferred to
normalisation as a per-bank configurable strategy — never a fixed offset
in the parser.

**Test:** `test_86_narration_is_captured_raw_without_subfield_extraction`
(`tests/unit/ingestion/parsers/test_mt940_parser.py`) — asserts
`"counterparty_name" not in rows[0].mapped` and that the full raw `:86:`
text, including embedded `/TAG/` conventions, survives unmodified.

---

## AE-02: CAMT.053 settlement_date must be per-entry, not statement-level

**PDF says:** A2.4 maps `settlement_date` to
`Stmt/Bal[Tp/CdOrPrtry/Cd='CLBD']/Dt/Dt` — a statement-level closing
balance element — then adds a note asking the intern to verify whether
CLBD or CLAV is the correct balance-type code.

**Why it's wrong:** The note is circular (it states the code is CLBD,
then confirms CLBD is correct for Closing Booked Balance). The real
error is structural: `Stmt/Bal` is one balance for the _entire
statement_. Mapping a per-transaction canonical field to it would assign
the identical date to every transaction in the file, regardless of when
each individual transaction actually settled.

**Correction implemented:** `settlement_date` for a transaction is
sourced from `Ntry/ValDt/Dt` — the per-entry value date — never from the
statement-level balance. The statement's closing/opening balances are
captured separately (`CAMT053Balance`, `CAMT053StatementHeader`) for a
future balance-level reconciliation control (see Case Study C5's
lesson), not conflated with per-transaction data.

**Test:**
`test_settlement_date_is_per_entry_val_dt_not_statement_balance_date`
(`tests/unit/ingestion/parsers/camt053/test_parser.py`) — the fixture
deliberately gives the entry's `BookgDt`, `ValDt`, and the statement's
`CLBD` balance date three _different_ values, proving `settlement_date`
tracks `ValDt` specifically and not either of the other two.

---

## AE-03: field extraction must be structural, never a fixed byte offset

**Applies to:** MT940 `:61:` field parsing generally (not a single PDF
quote, but the pattern AE-01 exemplifies).

**Why it matters:** The `:61:` line packs value date, entry date, D/C
mark, an _optional_ funds code, amount, transaction type, and reference
into one line with variable-length sub-fields. A parser that slices by
fixed character position breaks the instant a bank omits the optional
funds code (exactly SBI's documented deviation) or uses a
different-length amount.

**Correction implemented:** `recon.ingestion.parsers.mt940.field_61`
extracts every sub-field via a single regex built on character-class
transitions (digits vs. letters vs. the mark alphabet), not offsets. This
is what let the "missing funds code" deviation (D2's item 1) require
_zero_ special-case code — its absence is structurally unambiguous from
the grammar itself, not something that needs detecting.

**Test:** `test_missing_funds_code_parses_correctly` and
`test_parse_field_61_extracts_all_structural_parts`
(`tests/unit/ingestion/parsers/test_mt940_parser.py`).

---

## AE-04: exact-match rate is stated two incompatible ways

**PDF says:** A3.1 states exact matching "resolves 70-85% of all
records." Day 3 sets a target of "> 95% of matchable records resolved in
exact matching phase."

**Why it's questionable:** These use different denominators ("all
records" vs. "matchable records"), but even accounting for that, the
PDF gives no way to reconcile 70-85% against 95%+ without redefining one
of the terms.

**Correction planned (not yet implemented — matching engine is WP4):**
Report both metrics separately and explicitly —
`exact_match_rate_of_total` and `exact_match_rate_of_matchable`, where
"matchable" means a record that ultimately received _any_ match by the
end of the full pipeline. The engine will not be tuned toward the 95%
figure specifically; doing so risks the exact failure mode Scenario B4.4
warns against (a high match rate achieved by inflating false positives).

**Status:** Documented ahead of implementation since the decision affects
matching-engine design from the start; no test yet, as no matching code
exists yet.

---

## Format-level scope decisions (not PDF errors, but decisions worth recording)

These aren't corrections of something the PDF got wrong — they're
implementation choices made where the PDF was silent or where full
compliance was judged not worth the cost yet. Recorded here so they're
visible rather than discovered by a reviewer reading code.

- **R29 (CAMT.053 XSD validation) is not implemented.** Full validation
  against the ISO 20022 `camt.053.001.08` XSD requires vendoring a
  multi-file schema with external imports, or fetching it at parse time
  (a real XXE-adjacent risk and a hard runtime dependency on network
  availability). Structural validation — root element namespace/name
  check, required child elements — is implemented instead
  (`CAMT053Parser.parse_with_header`'s root-element check). Full XSD
  validation remains an open gap.

- **Multi-currency `:61:` lines are out of scope.** Base MT940 does not
  carry a currency code per statement line; a bank whose export embeds
  one there is not currently supported. None of the sample bank
  configs require it. If encountered, the statement's `:60F:`/`:60M:`
  currency (already extracted via `MT940Balance`) is the available
  fallback.

- **A standalone `:86:` field with no preceding `:61:`** (account-level
  narrative rather than per-transaction) is lexed correctly by
  `recon.ingestion.parsers.mt940.lexer` but is currently dropped rather
  than attached to any row or persisted. None of the current sample
  data requires it.
