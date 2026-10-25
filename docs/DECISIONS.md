# Decisions

The full text of every decision, with its reasoning and evidence, is in
[ASSESSMENT_ERRORS_AND_CORRECTIONS.md](ASSESSMENT_ERRORS_AND_CORRECTIONS.md), in three numbered
series that code comments cite:

- **AE-nn**: an error, contradiction or ambiguity in the assessment PDF, and how it was resolved.
- **IB-nn**: a bug in this codebase, with its regression test.
- **DD-nn**: a deliberate design or scope decision.

This page is the index of the ones that shape the system.

## Reading the PDF

| Topic | Entries |
|---|---|
| Exact-match key stated two ways; date excluded | AE-16, DD-23 |
| Direction and currency inflate every candidate; hard gates first | AE-09 |
| Confidence boundaries and the "two independent signals" rule | AE-08, AE-37 |
| Money precision and rounding | AE-04, AE-17, DD-01 |
| Reversals cannot be expressed by DR/CR alone | AE-12, IB-16 |
| The audit "hash over its own fields" is not a chain | AE-18, AE-19, DD-12, DD-31 |
| PAN masking belongs at the ingestion boundary | AE-26, DD-25 |
| Severity defined circularly from SLA | AE-07 |
| B4.2 netting figures do not reconcile | AE-32 |
| Timezone example is arithmetically wrong | AE-33, IB-01, IB-12 |
| Auto-resolve "if < 0.01" | AE-36, DD-27 |

## Architecture

| Decision | Entries |
|---|---|
| Stage order: exact, groups and cross-currency, fuzzy, rules | DD-23, DD-24 |
| Claims ledger: one ACTIVE claim per transaction | IB-03, IB-05, IB-06, IB-07 |
| Set-based exact stage as the default | DD-21, DD-22, IB-11, IB-17 |
| Deterministic ordering everywhere | IB-08, IB-15 |
| Synchronous runs, queued and background runs, no worker | DD-16, DD-26, DD-30 |
| API keys, users, roles | DD-14, DD-15, DD-29 |
| Auto-resolution never forces an unsafe action; Tier 3 value rules | DD-08, DD-28, IB-14 |
| Hot-swappable, versioned bank configuration | DD-29 |
| Sample and golden data | DD-32 |

## Deliberately not done

| Not done | Why | Entry |
|---|---|---|
| CAMT.053 XSD validation (R29) | Structural checks instead; schema vendoring and fetch risk | DD-02 |
| Multi-currency `:61:` lines | Base MT940 has no per-line currency | DD-03 |
| Phonetic matching | Poor fit for English transliteration; Token Set Ratio instead | DD-05 |
| Separate audit database | Separate schema and role; the anchors close the gap | AE-19, DD-12 |
| TLS termination, encryption at rest, retention tiering | Deployment concerns, documented | `SECURITY.md` |
| A separate worker, queue, metrics, tracing | Out of scope for a single-service build | DD-16, DD-30 |
| Frontend | Not built yet | README |
