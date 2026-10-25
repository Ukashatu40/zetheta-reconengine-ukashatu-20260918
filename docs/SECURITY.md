# Security

What is implemented, what is the deployer's responsibility, and what is not done. Nothing here
claims a control that the code does not have.

## Authentication and authorisation

- Identity is an API key sent as `X-API-Key`. Keys are 256-bit random values stored only as SHA-256
  hashes (a slow hash adds nothing at that entropy, DD-14); the raw key is shown once at creation.
- Roles (A9.2), hierarchical: `VIEWER` (read), `ANALYST` (resolve tier 1 and 2 exceptions, review
  matches, upload files, start runs), `ADMIN` (tiers 3 and 4, write-offs, overrides, users, bank
  configuration), `SYSTEM` (internal). Every route declares its role dependency and a test fails
  if a route is left unprotected (IB-09). `docs/API.md` lists the role of every endpoint.
- Admins can create users, issue them keys and deactivate them (`/api/v1/admin/users`); the audit
  trail records the user a key belongs to. There are no passwords, sessions or JWTs: this is an API-key
  service (DD-15 is superseded by DD-29; JWT is not implemented).
- Rate limiting: fixed window per key (120 requests a minute, 10 uploads a minute by default),
  429 with `Retry-After`. Counters live in Redis and **fail open**: if Redis is down requests are
  not blocked.

## The web dashboard

`frontend/` is served by an unprivileged nginx that also forwards `/api` and `/health` to the engine,
so the browser uses one origin and the engine needs no CORS settings. The API key is held in memory
(or `sessionStorage` if the user opts in), never in `localStorage`, a cookie or a URL; it is dropped
after 15 minutes without input and on any 401. Responses carry a strict Content-Security-Policy
(no inline script, no third-party origins), `nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer` and a locked-down `Permissions-Policy`. Nothing from the engine is
rendered as HTML (reports are drawn from JSON; a lint rule forbids `dangerouslySetInnerHTML`). A "hide
amounts" switch masks every money figure for screen sharing. TLS is still the deployer's job: put the
dashboard's port 8080 behind a TLS-terminating proxy and add `Strict-Transport-Security` there.

## Input handling

- Uploads are streamed into a UUID-named file with a 50 MB cap and an extension allow-list; the
  client filename is sanitised and kept as metadata only (no path traversal). Identical content
  (SHA-256) is refused with 409 before parsing.
- CAMT.053 XML is parsed with a single hardened lxml parser: no entity resolution, no network, no
  DTD, tree-size guard (`xml_safety.py`); chaos tests feed it an external-entity file and a
  billion-laughs document.
- A file whose columns no longer match its bank's mapping is refused (`FORMAT_CHANGE_DETECTED`),
  never partially ingested.
- Invalid UTF-8 and binary input are a 422, not a 500, and leave no stored file or row.

## Card data (PCI DSS requirement 3)

Card numbers are masked at the ingestion boundary, on every text field of every parsed row,
before anything is persisted or logged: a digit run is a PAN only if it has 13 to 19 digits, a
known network prefix and passes the Luhn check; only the last four digits are kept. A masked file
raises a `PAN_EXPOSURE` alert. This deviates from the PDF, which places masking in normalisation
(too late: raw rows are stored first, AE-26), and masks-and-alerts rather than rejecting the file
(DD-25). No real card data exists in this repository; all sample data is synthetic.

## Audit and integrity

Hash-chained, append-only audit trail with exportable anchors (`AUDIT.md`). Stored files are
re-hashed on demand (`POST /api/v1/files/verify`); a mismatch is `FILE_TAMPERED`.

## Secrets and configuration

No credentials are committed. `.env` is git-ignored; `.env.example` lists only variables the code
reads. The container runs as a non-root user and has a health check.

## Deployer responsibilities (not done by the application)

- **TLS (A9.3, TLS 1.2+).** The service speaks plain HTTP on port 8000 and expects a TLS-terminating
  reverse proxy or load balancer in front of it. Set HSTS and restrict the proxy to TLS 1.2+ there.
- **Encryption at rest (AES-256).** Not implemented in the application. Use encrypted volumes or
  managed PostgreSQL storage encryption for the database, the uploads volume and Redis persistence.
- **Network exposure.** `docker-compose.yml` publishes 5432 and 6379 for development; do not do
  that in production.
- **Audit anchors.** Store `GET /api/v1/audit/anchors` output outside the database on a schedule;
  without it, deletion of the newest entries is undetectable (DD-12).
- **Retention.** The PDF's seven-year retention with hot/warm/cold tiers is not automated. The
  tables are monthly range partitions, so old months can be detached and archived; no job does it.
- **Secrets rotation** and key expiry are manual.

## Known gaps

- No per-user accounts with passwords, MFA or SSO.
- No CSRF or CORS configuration, because there are no cookie sessions and the dashboard is served
  from the same origin as the API. A dashboard hosted on a different origin would need CORS added.
- The rate limiter is keyed on the API key (or client address when absent) and is not a DDoS
  defence.
