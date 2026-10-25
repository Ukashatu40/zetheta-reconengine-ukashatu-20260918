# Operations

## Run it

```bash
cp .env.example .env            # replace each CHANGE_ME
docker compose up -d --build    # postgres, redis, api on :8000
docker compose exec api alembic upgrade head
docker compose exec api python -m recon.cli.create_api_key --name admin --role ADMIN
docker compose exec api python -m recon.cli.seed            # optional demo data
curl localhost:8000/health/ready
```

`make up`, `make migrate`, `make seed SCALE=5`, `make api-key NAME=x ROLE=ANALYST` wrap these.
Interactive API docs: `http://localhost:8000/docs`.

## Day to day

| Task | How |
|---|---|
| Upload a bank statement or ledger | `POST /api/v1/files/upload` (`file`, `bank_code`, `format_type`, `source`, optional `auto_reconcile`) |
| Reconcile one bank | `POST /api/v1/reconciliation/run` with `Idempotency-Key`; body `bank_code`, optional `date_from`, `date_to`, `mode` (`FAST` or `FULL`) |
| Catch up after downtime | `POST /api/v1/reconciliation/backlog`: newest business day first, banks in parallel, a FAST pass first, then queued FULL runs in the background; `GET /api/v1/reports/recovery` for the report (B4.3) |
| Review fuzzy matches | `GET /api/v1/matches?status=PENDING_REVIEW`, then `PUT .../confirm` or `.../reject` |
| Work exceptions | `GET /api/v1/exceptions`, `PUT .../assign`, `PUT .../resolve`; SLA breaches: `POST /api/v1/exceptions/sla-scan` on a schedule |
| Missing-file alerts | `POST /api/v1/anomalies/scan` on a schedule (the absence rule needs to be asked) |
| Add or change a bank | edit `config/banks/<bank>.vN.yaml`, `python -m recon.cli.validate_bank_config <file> --sample <statement>`, then `POST /api/v1/admin/banks/reload` (or wait for the file watcher); no restart |
| Load exchange rates | `POST /api/v1/fx-rates/upload` (CSV); outliers are flagged, not silently used |
| Check integrity | `GET /api/v1/audit/verify`, `POST /api/v1/files/verify`, `GET /api/v1/reports/audit-compliance` |

Schedulers are not built in: run the two scans and an audit-anchor export from cron or your
orchestrator.

## Failure handling

- **File rejected as a format change** (422, alert `FORMAT_CHANGE_DETECTED`): the bank changed its
  layout. Create the new bank config version, validate it against the new sample, reload, re-upload.
- **Run FAILED**: the run's transaction rolled back; `error_summary` says why. Re-submit with a new
  idempotency key. A run stuck RUNNING for more than 30 minutes is reaped as FAILED by the next
  request for that bank.
- **Redis down**: the dashboard is computed uncached and rate limiting is off; `/health/ready`
  reports it. Nothing else depends on Redis.
- **Audit verification fails**: the response names the chain, the first bad sequence number and
  the kind (hash mismatch, broken link, gap, or truncation against an anchor). Treat as an incident.

## Maintenance

- **Partitions.** Monthly partitions exist through December 2026; rows beyond land in the default
  partition and still work, only slower to prune. Add the next year's partitions in a migration
  before the year starts.
- **Backups.** Standard PostgreSQL practice; the audit schema is in the same database, so a restore
  rewinds it as well. Compare against stored anchors after any restore.
- **Retention.** Not automated (`SECURITY.md`).

## Observability

Standard Python logging to stdout (levels from uvicorn; no PAN or secrets are logged, and
upload failures log the exception type only). `/health/live` and `/health/ready` (database and
Redis). No JSON log formatter, metrics endpoint or tracing is built.
