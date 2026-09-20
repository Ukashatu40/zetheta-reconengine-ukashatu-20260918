# CONFIDENTIAL

**Strictly Private and Confidential - Not for Circulation.**

This repository and everything in it is the property of Zetheta Algorithms
Private Limited ("Zetheta"). It was produced as an assessment deliverable and
is transferred to Zetheta in full on completion.

## Handling rules observed in this repository

- The repository is **private** and was created private from the first commit.
- No credentials, API keys, tokens or connection strings are committed. All
  configuration is supplied through environment variables; `.env.example`
  contains placeholder values only.
- **No production or personally identifiable data is used.** All transaction
  data in `data/` and `tests/fixtures/` is synthetic and generated from a fixed
  seed, in line with the Digital Personal Data Protection Act, 2023.
- Card primary account numbers (PANs) are masked to the last four digits at the
  ingestion boundary, before any persistence or logging. See `docs/SECURITY.md`.
- Third-party material is limited to the open-source dependencies declared in
  `pyproject.toml` and `frontend/package.json`.

## Attribution

Project specification, sample data design and assessment framework:
Zetheta Algorithms Private Limited.
Implementation: Ukashatu Abdullahi.
