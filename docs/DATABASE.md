# Database

PostgreSQL 15. Migrations are hand-written Alembic files in `migrations/versions/` (autogenerate
is not used, so a model change needs its migration written by hand). A fresh
`alembic upgrade head` builds the whole schema (verified on an empty database; CI does the same).

## Tables

| Table | Purpose |
|---|---|
| `ingestion_files` | One row per uploaded file: bank, source, format, SHA-256, size, status, counts, stored path, integrity status |
| `raw_transactions` | Parsed rows exactly as read (PAN-masked), partitioned monthly by `ingested_on` |
| `normalised_transactions` | The canonical model, partitioned monthly by `txn_date` |
| `match_results` | One row per matched or proposed pair; `match_group_id` links the members of a split or netted group |
| `match_claims` | Exclusivity ledger: at most one ACTIVE claim per transaction |
| `reconciliation_runs` | Run state, mode, date range, metrics, idempotency key |
| `exceptions`, `exception_events` | Exceptions and their history |
| `ledger_adjustments` | Fee adjustments created by auto-resolution |
| `alerts`, `counterparty_stats` | Anomaly alerts (deduplicated) and rolling per-counterparty statistics |
| `fx_rates` | Daily exchange rates by source |
| `statement_balances` | Opening/closing balances read from MT940 and CAMT statements |
| `bank_configurations` | Versioned snapshots of each bank configuration (hash, content, who changed it) |
| `users`, `api_keys` | Identities and key hashes |
| `audit.audit_log` | The hash-chained audit trail (separate schema) |

The monthly partitions run to December 2026 with a default partition behind them; creating the
next year's partitions is a manual migration step (`OPERATIONS.md`).

## Money

Never a float. The domain uses `Decimal` and integer minor units scaled by the currency's ISO 4217
exponent (2 for INR, USD, GBP; 0 for JPY; 3 for KWD), because the PDF's `DECIMAL(18,4)` ignores
per-currency precision (AE-17). Amounts are stored as the 4-decimal value and as `amount_minor`;
all comparison, summation and subset-sum matching use `amount_minor`. Rounding is explicit
(half-even by default), never the ambient `decimal` context. A negative amount is rejected: the
sign lives in `direction` (DD-01), with `is_reversal` for reversal entries (AE-12).

## Constraints and indexes worth knowing

- Check constraints are named `ck_<table>_<name>` through the metadata naming convention. Migrations
  must pass the bare name; passing a prefixed one produced doubled, hash-truncated names three times
  (IB-02). Verify names with `\d <table>` after writing a migration.
- `normalised_transactions`: primary key `(id, txn_date)` (the partition key must be part of any
  primary key); the literal A6.2 index on `(txn_id, amount, currency, txn_date)`; and the partial
  index `ix_normalised_transactions_unmatched_pool` on `(bank_code, currency, direction, txn_date,
  amount_minor)` where `match_status = 'UNMATCHED'`, which is what matching actually uses.
- `match_claims`: partial unique index on the transaction where the claim is ACTIVE.
- `match_results`: partial index on rejected `(internal, external)` pairs for the "never propose
  again" check.
- `audit.audit_log`: unique `(chain_id, sequence_no)`; mutation trigger and revoked privileges
  (`AUDIT.md`).

## Time

All instants are stored as UTC. A bank's local statement date is kept as `txn_date` (the date the
bank printed), and the UTC instant as `txn_timestamp_utc`. A date-only value in a positive-offset
zone is midnight local, which is the previous UTC day (AE-33, IB-01); a value that carries a time
of day keeps it (IB-12).
