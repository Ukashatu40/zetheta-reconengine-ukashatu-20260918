# Exception management

After every run, `ExceptionClassifier.classify_run` examines the unmatched leftovers, the
pending-review pairs, the matched pairs and the groups, and raises one exception per finding
(`excmgmt/classifier.py`, `pair_analysis.py`). All 19 categories are reachable and the golden
dataset plants each one (`tests/golden/`). Configuration: `config/exceptions/taxonomy.yaml`.

## Taxonomy

18 categories come from the PDF; `SETTLEMENT_DELAY` is the 19th, suggested by A7.1 (DD-07).

| Category | Severity | Default tier | SLA (min) | Auto-resolvable |
|---|---|---|---|---|
| MISSING_INTERNAL | HIGH | 2 | 240 | never |
| MISSING_EXTERNAL | HIGH | 2 | 240 | never |
| AMOUNT_MISMATCH | MEDIUM | 2 | 120 | within 1 minor unit |
| DUPLICATE_INTERNAL | HIGH | 2 | 60 | always |
| DUPLICATE_EXTERNAL | HIGH | 2 | 120 | never |
| DATE_MISMATCH | LOW | 2 | 480 | within 1 day |
| CURRENCY_MISMATCH | MEDIUM | 2 | 240 | never |
| DIRECTION_REVERSAL | CRITICAL | 4 | 30 | never |
| PARTIAL_MATCH | LOW | 2 | 240 | always |
| NETTED_SETTLEMENT | LOW | 2 | 480 | always |
| FEE_DEDUCTION | LOW | 2 | 120 | always |
| FX_VARIANCE | MEDIUM | 2 | 240 | never (Tier 4 beyond 3%) |
| STALE_TRANSACTION | MEDIUM | 2 | 1440 | never |
| FORMAT_ERROR | HIGH | 2 | 60 | never (Tier 4 when systemic) |
| REFERENCE_TRUNCATED | LOW | 2 | 120 | above 0.85 confidence |
| TIMEZONE_OFFSET | LOW | 2 | 60 | always |
| REVERSAL_PENDING | MEDIUM | 2 | 1440 | never |
| REGULATORY_HOLD | CRITICAL | 4 | 2880 | never |
| SETTLEMENT_DELAY | MEDIUM | 2 | 1440 | never |

The SLAs of DIRECTION_REVERSAL (AE-03), STALE_TRANSACTION (AE-21) and SETTLEMENT_DELAY are
engineering decisions where the PDF is silent or inconsistent; each is recorded in the
corrections document. Severity is a property of the category, not derived from SLA (AE-07).

## Routing

`excmgmt/routing.py`, in this precedence:

1. Category forced to Tier 4 (DIRECTION_REVERSAL, REGULATORY_HOLD), and the systemic cases:
   more than 50 exceptions per bank per batch, FX variance beyond 3%.
2. Tier 3 when the **individual** INR-equivalent value is strictly above Rs 10 lakh; this overrides
   auto-resolution, so a large exception is never auto-resolved.
3. Tier 1 when the category is rule-eligible and its condition holds.
4. Tier 3 when the bank's **cumulative** value for the day is strictly above Rs 1 crore; it routes
   human-handled exceptions to a senior manager but does not take a Rs 6 fee adjustment out of
   automation (IB-14).
5. Otherwise the category default.

Values in other currencies are converted to INR at the rate for the booking date (`fx/`). When no
rate exists the value rule is skipped and the rationale says so.

## Auto-resolution (Tier 1)

`excmgmt/autoresolve/resolver.py` performs the PDF's actions: void the later copy of a duplicate
(status `VOIDED`, original linked), confirm a partial or netted settlement, post a fee adjustment
to `ledger_adjustments`, re-match across the timezone or a one-day date gap, re-match a truncated
reference on its prefix, accept an amount difference within one minor unit. Nothing is deleted:
a voided duplicate keeps its row with status `VOIDED`. Each outcome is audited and creates an
`exception_events` row. When an action cannot be carried out safely (a claim conflict, a
duplicate that is already matched) the exception is not forced through: it moves to Tier 2 with
the reason recorded. A pair a human already rejected is never auto-resolved.

## Lifecycle and SLA

`OPEN`, `IN_REVIEW`, `ESCALATED`, `RESOLVED`, `WRITTEN_OFF`, `REOPENED`;
`POST /exceptions/{id}/reopen` reopens a closed one. Every transition writes an event and an audit
entry. `POST /exceptions/sla-scan` (also run by the scheduler of your choice) finds breaches and
escalates them one tier, queueing them for the next tier (`escalation.py`, `queues.py`).
Resolving tiers 3 and 4 and every write-off needs ADMIN (DD-14); `PUT /exceptions/{id}/override`
is ADMIN only: it moves an open exception to any tier (A9.2), requires a reason, and records an
`OVERRIDDEN` event and an `OVERRIDE` audit entry.
