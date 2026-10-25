# Automated Reconciliation Engine for Multi-Bank Settlement

**Strictly Private and Confidential - Not for Circulation.**
Property of Zetheta Algorithms Private Limited. See [LICENSE](LICENSE) and
[CONFIDENTIAL.md](CONFIDENTIAL.md).

Assessment project: _Automated Reconciliation Engine for Multi-Bank Settlement_
(Banking Infrastructure | Payment Systems).
Implementation: Ukashatu Abdullahi.

> **Repository naming.** Named per the Part D, Day 1 convention
> `zetheta-reconengine-[yourname]-[YYYYMMDD]`. The date component is the
> repository creation date, 18 September 2026.

> **Timeline interpretation.** The specification states a 15-day timeline on the
> cover page and a 7-day sprint in Part D. The seven "Days" of Part D are
> treated as seven work packages with the stated checkpoint deliverables and
> commit counts, executed across the 15-day window, with the consolidated
> submission on Day 15. Commit messages follow the Part D wording exactly.

## What this is

A reconciliation engine for a payment aggregator settling through several banks. It ingests
internal ledger files and bank statements (CSV, SWIFT MT940, ISO 20022 CAMT.053), normalises them
to one canonical model, matches them in stages (exact, cross-currency and split/netted groups,
fuzzy, rule-based), classifies everything left over into 19 exception categories, routes and
auto-resolves those exceptions by tier and SLA, raises anomaly alerts, and records every step in a
hash-chained audit trail. Seven banks are configured (`config/banks/`); adding one is a YAML file.

## Status

**Backend: complete to the specification except where stated below. Frontend: not built.**

| Area | State |
|---|---|
| Ingestion, normalisation, matching, exceptions, audit, anomalies, FX, reports, API | Built and tested (about 1,270 test cases, 95% coverage) |
| Golden dataset and CI gate | Built (`data/golden/manifest.json`, 194 planted scenarios) |
| Performance (A6.1) | Parsing and end-to-end ingestion (118,000 records/min) meet their targets; fuzzy matching meets 60 s at 188,000 records. **Not met:** exact matching of 500,000 records (34.6 s against 10 s) and fuzzy at 500,000 (75.9 s), and memory at 1 million records is unmeasured. Details and method in [docs/PERFORMANCE.md](docs/PERFORMANCE.md) |
| Web dashboard | Not built (`frontend/` is empty) |
| TLS, encryption at rest, retention tiering | Deployment responsibilities, see [docs/SECURITY.md](docs/SECURITY.md) |
| CAMT.053 XSD validation, RBI escrow tracking | Not implemented ([DD-02](docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md)) |
| Part B simulation write-up, consolidated PDF | Not written |

Requirement-by-requirement status: [docs/REQUIREMENTS_MATRIX.md](docs/REQUIREMENTS_MATRIX.md).
The specification has errors and contradictions; each one found, and how it was resolved, is in
[docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md](docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md).

## Quick start

Needs Docker, and Python 3.11 for running tests on the host.

```bash
cp .env.example .env                       # set POSTGRES_PASSWORD, and the password inside DATABASE_URL
docker compose up -d --build               # postgres 15, redis 7, api on :8000
docker compose exec api alembic upgrade head
docker compose exec api python -m recon.cli.seed          # demo data through the real pipeline
curl localhost:8000/health/ready
```

`seed` prints three API keys (viewer, analyst, admin) once. Then browse `http://localhost:8000/docs`,
or try `curl -H "X-API-Key: <key>" "localhost:8000/api/v1/dashboard/summary"`. Without the seed
script create a key with `make api-key NAME=me ROLE=ADMIN`. `make seed SCALE=20` loads a larger volume.

### Development

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && pre-commit install
make migrate-test                          # creates recon_test in the compose postgres
export TEST_DATABASE_URL="postgresql+psycopg://recon_app:<password>@localhost:5432/recon_test"
make check                                 # black, ruff, mypy --strict, pytest
make golden                                # the golden-dataset gate
make bench                                 # pipeline benchmark against the A6.1 targets
```

## A worked example

```bash
KEY=<an analyst or admin key>
curl -H "X-API-Key: $KEY" -F file=@internal_ledger_hdfc_<date>.csv -F bank_code=HDFC -F format_type=CSV \
     -F source=INTERNAL localhost:8000/api/v1/files/upload
curl -H "X-API-Key: $KEY" -F file=@hdfc_settlement_<date>.csv -F bank_code=HDFC -F format_type=CSV \
     -F source=EXTERNAL -F auto_reconcile=true localhost:8000/api/v1/files/upload
curl -H "X-API-Key: $KEY" "localhost:8000/api/v1/exceptions?status=OPEN"
curl -H "X-API-Key: $KEY" "localhost:8000/api/v1/audit/verify"
```

`python -m recon.cli.golden --write-files data/samples` writes example statement files to try this
with.

## How it is organised

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the diagrams and the module map. In short:
`src/recon/` holds `ingestion`, `normalisation`, `matching`, `excmgmt`, `anomaly`, `fx`, `audit`,
`runs`, `reporting`, `api`, `persistence`; `config/` holds everything that varies per bank or
policy; `migrations/` the hand-written Alembic history; `tests/` the unit, integration, golden,
chaos, security and performance suites.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data flow, module map |
| [docs/DATABASE.md](docs/DATABASE.md) | Schema, partitioning, money and time handling |
| [docs/MATCHING_ENGINE.md](docs/MATCHING_ENGINE.md) | Stage order, scoring, groups, claims |
| [docs/EXCEPTIONS.md](docs/EXCEPTIONS.md) | 19-category taxonomy, routing, auto-resolution, SLAs |
| [docs/AUDIT.md](docs/AUDIT.md) | Hash chain, canonical serialisation, verification, anchors |
| [docs/SECURITY.md](docs/SECURITY.md) | RBAC, PAN masking, upload controls, what the deployer must add |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Targets versus measured results |
| [docs/TESTING.md](docs/TESTING.md) | Test strategy and the golden dataset |
| [docs/API.md](docs/API.md) | Endpoint reference (generated) |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running, recovery, maintenance |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Index of decisions |
| [docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md](docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md) | Specification errors (AE), bugs (IB), decisions (DD) |
| [docs/REQUIREMENTS_MATRIX.md](docs/REQUIREMENTS_MATRIX.md) | Requirement traceability |
