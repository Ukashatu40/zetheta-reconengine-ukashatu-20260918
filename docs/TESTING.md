# Testing

```bash
source .venv/bin/activate
export TEST_DATABASE_URL="postgresql+psycopg://recon_app:<password>@localhost:5432/recon_test"
make migrate-test          # once: creates and migrates recon_test inside the compose stack
pytest -q                  # everything (about 80 s)
pytest -q -m golden        # the golden gate only
make coverage              # with the 80% floor CI enforces
```

Without `TEST_DATABASE_URL` the database tests skip (they do not fail); the unit tests need
nothing. Integration, golden, chaos and security tests run in a rolled-back savepoint, so they
leave no data behind.

## Layers

| Directory | Tests | What it proves |
|---|---|---|
| `tests/unit/` | ~520 | Pure logic: parsers, normalisation, money, scoring, subset-sum, calendar, FX conversion, routing, anomaly statistics, PAN masking, report rendering, config validation, sample generator, CLIs. No database. |
| `tests/integration/` | ~400 | The real PostgreSQL schema and the API: repositories, claims, every matching stage, classifier categories, auto-resolver, runs, uploads, reports, backlog, manual matching, admin, audit chain and append-only trigger. |
| `tests/golden/` | 6 | The planted dataset end to end (below). |
| `tests/chaos/` | 13 | Damaged and hostile input: truncated and binary files, invalid UTF-8, missing columns, XXE and billion-laughs XML, duplicate uploads; no leftover files or rows. |
| `tests/security/` | 5 | Role matrix on real routes, key hashes (raw key never stored or audited), deactivated users, no full card number in any table. |
| `tests/performance/` | 1 (3 cases) | Parsing meets the A6.1 per-format targets on generated files. |

About 950 test functions, around 1,270 test cases with parametrisation; line and branch coverage
of `src/recon` is 95%.

## The golden gate (A8.3)

`recon.samples` generates, from a seed, 17 statement files (CSV, MT940 and CAMT.053 across seven
banks, ledger and statement sides) and an exchange-rate file, and plants **194 scenarios**:
115 exact matches, 25 fuzzy auto-matches, 20 fuzzy review-band pairs, 5 tolerance matches, 5 split
and 5 netted settlements, the B4.2 cross-currency netting (corrected, and as written in the PDF),
a cross-currency pair, and 16 category scenarios so that all 19 exception categories appear.
`data/golden/manifest.json` records, for every scenario, the expected match outcome and
exception categories.

`tests/golden/test_golden.py` regenerates the data, ingests it through the real
`IngestionPipeline`, runs one `FULL` run per bank, and compares every scenario with the manifest;
a deviation fails with the scenario id and expected versus actual. It also checks that the
generator still reproduces the committed manifest (so it cannot go stale), that the gate really
fails when an expectation is corrupted, and that the same cases hold in a different calendar week.
`make golden` and a pre-commit hook run it; CI runs it before the full suite.

Refresh after an intended generator change: `make golden-manifest`, then review the diff.
The files themselves: `python -m recon.cli.golden --write-files data/samples [--scale 50]`.

The gate found four defects in the engine while it was being built (IB-12, IB-14, IB-15, and a
fixture collision); they are in `ASSESSMENT_ERRORS_AND_CORRECTIONS.md`.

## Honest limits

- The golden dataset and the synthetic benchmark were written by the same author as the engine,
  so they test the scenarios that author thought of. They are a regression gate, not an independent
  accuracy measurement.
- There is no test against real bank files, which the repository must not contain.
- Concurrency is tested for idempotent run creation and claim exclusivity (including the race
  simulation), not by a multi-process stress test.
- The performance test asserts parsing only; end-to-end figures are reported in `PERFORMANCE.md`.
- Static checks: `black`, `ruff` (including the PL rules), `mypy --strict` over `src` and `tests`
  (`make check`); CI also validates every bank configuration, migrates an empty database and
  builds the Docker image.
