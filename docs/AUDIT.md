# Audit trail

`audit.audit_log` is the system's evidence. Every state change (ingestion, normalisation, match,
exception transition, run, configuration, user and key management, alert) appends one entry to a
named chain (`audit/chains.py`: `exceptions`, `ingestion`, `matching`, `runs`, `config`, `alerts`,
`users`).

## Structure

Each entry holds `chain_id`, `sequence_no`, `occurred_at`, `actor_type`, `actor_id`,
`action_type`, `affected_records`, `before_state`, `after_state`, `rationale`, `prev_hash`,
`current_hash`. `current_hash` is SHA-256 over the canonical bytes of all fields **including**
`prev_hash`, which is what turns per-row hashes into a chain (AE-18).

## Canonical serialisation (version 1, `audit/canonical.py`)

One JSON object, keys sorted, no whitespace, UTF-8, non-ASCII kept; `occurred_at` as UTC with six
microsecond digits and a trailing `Z`; floats rejected anywhere (their text form is not stable
through JSONB); naive datetimes and non-string keys rejected; `recorded_at` excluded because the
database sets it after the hash is known.

## Append-only

The migration creates the `audit` schema and a role, `recon_audit`, with INSERT and SELECT only;
UPDATE and DELETE are revoked for it **and for the application role**, and a trigger
(`audit_log_forbid_mutation`) rejects any update or delete as defence in depth. The application writes through the audit logger only, in the same transaction as the change it
records (so an entry cannot exist for a change that rolled back, nor the reverse); it connects as
the application role. A `get_audit_engine()` for the `recon_audit` role exists but is not used at
runtime. Writers to one chain are
serialised so sequence numbers stay contiguous (DD-12).
**Honest limit (AE-19):** this is one PostgreSQL instance. "Stored separately from the
operational database" is met by a separate schema and role, not a separate server; a superuser
can still bypass it. The exported anchors below are what close that gap.

## Verification

- `GET /api/v1/audit/verify`: walks every chain, checking contiguous sequence numbers, stored
  `prev_hash` equal to the previous `current_hash`, and the recomputed hash equal to the stored
  one; reports the first failure with its kind and sequence number.
- Deleting the **newest** entries leaves a valid shorter chain, which a walk cannot detect. So
  `GET /api/v1/audit/anchors` returns the current head of every chain (`chain:sequence:hash`) to be
  stored outside the database, and `verify?expected=chain:seq:hash` checks them later (DD-12).
- `GET /api/v1/reports/audit-compliance` combines the verification result with coverage figures
  and a PASS/FAIL verdict.
- `GET /api/v1/audit/export` streams the trail as CSV (at most 50,000 rows per call; filter by
  chain, action, actor and dates).

## What is recorded

Ingestion records the file hash and counts; normalisation the failure counts; each run its state
changes and a SHA-256 digest of the match summary; each match, review decision, manual match and
unmatch the before/after state; each exception transition the actor and rationale; each bank
configuration change (`CONFIG_CHANGE`) the new version. The actor is `api-key:<name>` or the user bound to the key.
