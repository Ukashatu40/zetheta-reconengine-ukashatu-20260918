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

## Status

Work package 1 of 7 in progress. This README is a skeleton and is completed in
work package 7. Current state is tracked in
[docs/REQUIREMENTS_MATRIX.md](docs/REQUIREMENTS_MATRIX.md).

## Quick start

Not yet available. Docker Compose lands in WP1 Increment 2.

## Documentation

| Document                                    | Contents                                          |
| ------------------------------------------- | ------------------------------------------------- |
| `docs/ARCHITECTURE.md`                      | System design and rationale                       |
| `docs/DATABASE.md`                          | Schema, indexes, partitioning, money handling     |
| `docs/MATCHING_ENGINE.md`                   | Matching hierarchy, blocking, confidence scoring  |
| `docs/EXCEPTIONS.md`                        | 18-category taxonomy, SLAs, escalation            |
| `docs/AUDIT.md`                             | Hash chain, canonical serialisation, verification |
| `docs/SECURITY.md`                          | RBAC, PAN masking, upload controls                |
| `docs/PERFORMANCE.md`                       | Targets vs measured results                       |
| `docs/TESTING.md`                           | Test strategy and golden dataset                  |
| `docs/API.md`                               | Endpoint reference                                |
| `docs/OPERATIONS.md`                        | Running, recovery mode, troubleshooting           |
| `docs/DECISIONS.md`                         | ADR index                                         |
| `docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md` | Specification errors identified and corrected     |
| `docs/REQUIREMENTS_MATRIX.md`               | Requirement traceability                          |
| `docs/SIMULATION.md`                        | Gamified simulation design (Part B)               |
