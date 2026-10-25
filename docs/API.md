# API reference

Automated Reconciliation Engine, version 0.1.0.

Generated from the application's OpenAPI schema by `scripts/gen_api_docs.py`;
do not edit by hand. The interactive reference is served at `/docs` (Swagger UI)
and `/openapi.json`.

**Authentication.** Every `/api/v1` endpoint takes an `X-API-Key` header. Roles are
cumulative: VIEWER reads, ANALYST also resolves exceptions, uploads files and starts
runs, ADMIN also administers users, bank configurations and overrides.
`/health/*` is public.
Every state-changing endpoint is written to the audit trail.

**Conventions.** Lists return `{items, total, limit, offset}`. Errors are `{detail}` with
400/401/403/404/409/422/429. Run creation requires an `Idempotency-Key` header.
Parameters marked `*` are required. Deprecated routes are kept as aliases of the
spec-named ones.

## admin

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/admin/banks` | VIEWER | none | List Bank Configs |
| POST | `/api/v1/admin/banks/reload` | ADMIN | none | Reload Bank Configs |
| POST | `/api/v1/admin/banks/validate` | ANALYST | none | Validate Bank Config |
| GET | `/api/v1/admin/banks/{bank_code}/versions` | VIEWER | none | Bank Config Versions |
| POST | `/api/v1/admin/users` | ADMIN | none | Create User |
| GET | `/api/v1/admin/users` | ADMIN | `limit`, `offset` | List Users |
| POST | `/api/v1/admin/users/{user_id}/api-keys` | ADMIN | none | Issue Api Key |
| POST | `/api/v1/admin/users/{user_id}/deactivate` | ADMIN | none | Deactivate User |

## anomalies

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/anomalies` | VIEWER | `alert_type`, `severity`, `status`, `bank_code`, `route_to`, `limit`, `offset` | List Alerts |
| POST | `/api/v1/anomalies/scan` | ANALYST | none | Scan For Absence |
| PUT | `/api/v1/anomalies/{alert_id}/acknowledge` | ANALYST | none | Acknowledge Alert |

## audit

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/audit` | VIEWER | `chain_id`, `action_type`, `actor_id`, `date_from`, `date_to`, `limit`, `offset` | List Audit |
| GET | `/api/v1/audit/anchors` | VIEWER | none | Audit Anchors |
| GET | `/api/v1/audit/export` | VIEWER | `chain_id`, `action_type`, `actor_id`, `date_from`, `date_to` | Export Audit |
| GET | `/api/v1/audit/verify` | VIEWER | `expected` | Verify Audit |

## dashboard

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/dashboard/banks` | VIEWER | none | Banks |
| GET | `/api/v1/dashboard/summary` | VIEWER | `as_of` | Summary |
| GET | `/api/v1/dashboard/timeline` | VIEWER | `date` | Timeline |

## exceptions

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/exceptions` | VIEWER | `status`, `severity`, `category`, `bank_code`, `tier`, `assigned_to`, `date_from`, `date_to`, `limit`, `offset` | List Exceptions |
| GET | `/api/v1/exceptions/export` | VIEWER | `status`, `severity`, `category`, `bank_code`, `tier` | Export Exceptions |
| POST | `/api/v1/exceptions/sla-scan` | ADMIN | none | Run Sla Scan |
| GET | `/api/v1/exceptions/{exception_id}` | VIEWER | none | Get Exception |
| PUT | `/api/v1/exceptions/{exception_id}/assign` | ANALYST | none | Assign Exception |
| PUT | `/api/v1/exceptions/{exception_id}/override` | ADMIN | none | Override Exception |
| POST | `/api/v1/exceptions/{exception_id}/reopen` | ANALYST | none | Reopen Exception |
| PUT | `/api/v1/exceptions/{exception_id}/resolve` | ANALYST | none | Resolve Exception |

## files

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/files` | VIEWER | `bank_code`, `source`, `format_type`, `status`, `limit`, `offset` | List Files |
| POST | `/api/v1/files/verify` | ADMIN | none | Verify All Files |
| GET | `/api/v1/files/{file_id}` | VIEWER | none | Get File |
| POST | `/api/v1/files/{file_id}/verify` | ANALYST | none | Verify File |

## fx

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/fx-rates` | VIEWER | `currency`, `date_from`, `date_to`, `suspect`, `limit`, `offset` | List Fx Rates |
| POST | `/api/v1/fx-rates/upload` | ANALYST | none | Upload Fx Rates |

## matches

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/matches` | VIEWER | `status`, `match_type`, `limit`, `offset` | List Matches |
| POST | `/api/v1/matches/manual` | ANALYST | none | Add Manual Match |
| PUT | `/api/v1/matches/{match_id}/confirm` | ANALYST | none | Confirm Match |
| PUT | `/api/v1/matches/{match_id}/reject` | ANALYST | none | Reject Match |
| POST | `/api/v1/matches/{match_id}/unmatch` | ANALYST | none | Unmatch |

## other

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/health/live` | public | none | Health Live |
| GET | `/health/ready` | public | none | Health Ready |

## reports

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| GET | `/api/v1/reports/audit-compliance` | ANALYST | `format`, `date_from`, `date_to` | Audit Compliance Report |
| GET | `/api/v1/reports/daily` | ANALYST | `format`, `date` | Daily Report |
| GET | `/api/v1/reports/exception-ageing` | ANALYST | `format` | Exception Ageing Report |
| GET | `/api/v1/reports/recovery` | ANALYST | `format`, `window_hours` | Recovery Report |

## runs

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| POST | `/api/v1/reconcile` | ANALYST | `Idempotency-Key`* | Start Reconciliation (deprecated) |
| POST | `/api/v1/reconciliation/backlog` | ANALYST | `Idempotency-Key`* | Recover Backlog |
| POST | `/api/v1/reconciliation/run` | ANALYST | `Idempotency-Key`* | Start Reconciliation |
| GET | `/api/v1/reconciliation/runs` | VIEWER | `bank_code`, `status`, `limit`, `offset` | List Runs |
| GET | `/api/v1/reconciliation/runs/{run_id}/results` | VIEWER | `status`, `match_type`, `limit`, `offset` | Run Results |
| GET | `/api/v1/runs` | VIEWER | `bank_code`, `status`, `limit`, `offset` | List Runs (deprecated) |
| GET | `/api/v1/runs/{run_id}/results` | VIEWER | `status`, `match_type`, `limit`, `offset` | Run Results (deprecated) |

## upload

| Method | Path | Role | Parameters | Summary |
|---|---|---|---|---|
| POST | `/api/v1/files/upload` | ANALYST | none | Upload File |
| POST | `/api/v1/upload` | ANALYST | none | Upload File (deprecated) |
