zetheta-reconengine-ukashatu-20260918/
├── README.md
├── LICENSE # "Proprietary - Zetheta Algorithms Private Limited"
├── CONFIDENTIAL.md
├── pyproject.toml # deps, black, ruff, mypy, pytest config
├── .pre-commit-config.yaml
├── .env.example
├── .gitignore
├── .dockerignore
├── alembic.ini
├── Makefile # make up / seed / recon / bench / test / verify-audit
├── docker-compose.yml
├── docker-compose.test.yml
│
├── docker/
│ ├── api.Dockerfile # multi-stage, slim, non-root, HEALTHCHECK
│ ├── worker.Dockerfile
│ ├── frontend.Dockerfile
│ └── entrypoint.sh
│
├── config/
│ ├── banks/ # hot-swappable, versioned
│ │ ├── \_schema.json # JSON Schema the loader validates against
│ │ ├── hdfc.v1.yaml
│ │ ├── icici.v1.yaml
│ │ ├── icici.v2.yaml # the B4.1 "midnight format change"
│ │ ├── axis.v1.yaml
│ │ ├── sbi.v1.yaml
│ │ ├── kotak.v1.yaml
│ │ └── internal.v1.yaml
│ ├── matching/
│ │ ├── weights.yaml # the A3.4 table, externalised
│ │ ├── thresholds.yaml
│ │ └── rules.yaml
│ ├── exceptions/
│ │ └── taxonomy.yaml # 18 categories + SLA + severity + tier + auto-resolve
│ ├── calendars/
│ │ └── in-2026.yaml # Indian bank holidays
│ └── fx/
│ └── sources.yaml
│
├── migrations/
│ └── versions/
│
├── src/recon/
│ ├── **init**.py
│ ├── domain/ # PURE. no sqlalchemy, no fastapi, no io
│ │ ├── money.py # Money value object, Decimal + minor units
│ │ ├── canonical.py # CanonicalTransaction
│ │ ├── enums.py # Direction, Format, MatchType, ExceptionCategory...
│ │ ├── match.py # MatchResult, FieldScores, MatchExplanation
│ │ ├── exceptions_model.py
│ │ ├── errors.py # domain exception hierarchy
│ │ └── ports.py # repository Protocols
│ │
│ ├── config/
│ │ ├── loader.py # JSON-Schema validated, versioned
│ │ ├── watcher.py # hot-swap without restart (B4.1)
│ │ ├── models.py # Pydantic v2 config models
│ │ └── validator_cli.py # the ops column-mapping validation tool (B4.1)
│ │
│ ├── ingestion/
│ │ ├── protocol.py # Parser Protocol
│ │ ├── registry.py
│ │ ├── integrity.py # SHA-256, size, dedupe
│ │ ├── schema_fingerprint.py # FORMAT_CHANGE_DETECTED
│ │ ├── upload_guard.py # size limits, extensions, path traversal
│ │ ├── pan_guard.py # mask BEFORE persistence (see AE-26)
│ │ └── parsers/
│ │ ├── csv_parser.py
│ │ ├── mt940/
│ │ │ ├── lexer.py
│ │ │ ├── state_machine.py
│ │ │ ├── field_61.py # structural, never offset-based
│ │ │ └── field_86.py
│ │ └── camt053/
│ │ ├── parser.py # lxml iterparse, XXE disabled
│ │ └── xpath.py
│ │
│ ├── normalisation/
│ │ ├── pipeline.py # the exact 5-function chain from Day 2
│ │ ├── timezones.py
│ │ ├── currency.py
│ │ ├── reference.py
│ │ ├── amounts.py
│ │ ├── counterparty.py
│ │ └── abbreviations.py
│ │
│ ├── matching/
│ │ ├── orchestrator.py # MatchingOrchestrator (R57)
│ │ ├── blocking/
│ │ │ ├── keys.py
│ │ │ └── index.py
│ │ ├── strategies/
│ │ │ ├── base.py
│ │ │ ├── exact.py
│ │ │ ├── fuzzy.py
│ │ │ ├── split.py
│ │ │ ├── netted.py
│ │ │ └── cross_currency.py
│ │ ├── scoring/
│ │ │ ├── weighted.py # A3.4 formula
│ │ │ ├── constraints.py # hard gates, evaluated first
│ │ │ └── explanation.py
│ │ ├── claims.py # exclusivity ledger: prevents double consumption
│ │ └── resolution.py # global conflict resolution, deterministic
│ │
│ ├── rules/
│ │ ├── registry.py
│ │ ├── base.py
│ │ └── builtin/
│ │
│ ├── reconciliation/
│ │ ├── runner.py # run state machine + checkpoints
│ │ ├── modes.py # FAST vs FULL (B4.3)
│ │ ├── balance.py # balance-level reconciliation (C5)
│ │ └── metrics.py
│ │
│ ├── excmgmt/
│ │ ├── classifier.py
│ │ ├── taxonomy.py
│ │ ├── sla.py
│ │ ├── escalation.py
│ │ └── autoresolve/
│ │
│ ├── audit/
│ │ ├── logger.py
│ │ ├── canonical_serialisation.py # pinned, documented, versioned
│ │ ├── chain.py
│ │ └── verifier.py
│ │
│ ├── anomaly/
│ │ ├── amount_deviation.py
│ │ ├── velocity.py
│ │ ├── absence.py
│ │ └── baselines.py
│ │
│ ├── fx/
│ │ ├── rates.py
│ │ └── variance.py
│ │
│ ├── reporting/
│ │ ├── daily.py
│ │ ├── ageing.py
│ │ ├── compliance.py
│ │ ├── recovery.py # B4.3 Reconciliation Recovery Report
│ │ └── export.py # CSV
│ │
│ ├── persistence/
│ │ ├── session.py
│ │ ├── models/
│ │ ├── repositories/
│ │ ├── bulk.py # COPY / executemany
│ │ └── unit_of_work.py
│ │
│ ├── security/
│ │ ├── auth.py # JWT users + API keys for services
│ │ ├── rbac.py # VIEWER / ANALYST / ADMIN / SYSTEM
│ │ ├── ratelimit.py
│ │ └── redaction.py
│ │
│ ├── observability/
│ │ ├── logging.py # structlog, redacting processor
│ │ ├── metrics.py
│ │ └── health.py # /health/live, /health/ready
│ │
│ ├── api/
│ │ ├── main.py
│ │ ├── deps.py
│ │ ├── errors.py # RFC 7807 style problem responses
│ │ ├── idempotency.py
│ │ └── v1/
│ │ ├── reconciliation.py
│ │ ├── exceptions.py
│ │ ├── audit.py
│ │ ├── files.py
│ │ ├── dashboard.py
│ │ ├── reports.py
│ │ └── schemas/
│ │
│ └── cli/
│ ├── seed_data.py
│ ├── generate_dataset.py
│ ├── run_recon.py
│ ├── verify_audit.py
│ └── benchmark.py
│
├── frontend/
│ └── src/
│ ├── pages/ # Overview, Exceptions, Audit, Files, Runs
│ ├── components/
│ ├── api/
│ └── lib/
│
├── tests/
│ ├── conftest.py
│ ├── unit/
│ ├── integration/
│ ├── golden/
│ │ ├── manifest.json
│ │ └── test_golden_dataset.py
│ ├── performance/
│ ├── chaos/
│ ├── security/
│ └── fixtures/
│
├── data/
│ ├── golden/
│ ├── samples/
│ └── benchmarks/ # measured results, committed
│
├── docs/
│ ├── ARCHITECTURE.md
│ ├── DATABASE.md
│ ├── MATCHING_ENGINE.md
│ ├── EXCEPTIONS.md
│ ├── AUDIT.md
│ ├── SECURITY.md
│ ├── PERFORMANCE.md
│ ├── TESTING.md
│ ├── API.md
│ ├── OPERATIONS.md
│ ├── DECISIONS.md # ADR index
│ ├── ASSESSMENT_ERRORS_AND_CORRECTIONS.md
│ ├── REQUIREMENTS_MATRIX.md
│ ├── SIMULATION.md # Part B deliverable
│ ├── DEMO.md
│ ├── adr/
│ └── diagrams/
│
├── scripts/
└── .github/workflows/ci.yml
