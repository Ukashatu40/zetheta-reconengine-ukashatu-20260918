# src/recon/excmgmt/queues.py
"""Work queues the four escalation tiers feed (A4.2). Tier 1 is automatic, so
it has no queue; an exception that cannot be auto-resolved moves to Tier 2."""

from __future__ import annotations

QUEUE_PREFIX = "queue:"
QUEUE_BY_TIER: dict[int, str] = {
    2: "queue:analyst",
    3: "queue:senior-ops",
    4: "queue:compliance",
}


def queue_for_tier(tier: int) -> str | None:
    return QUEUE_BY_TIER.get(tier)


def is_queue(assignee: str | None) -> bool:
    """True for a tier queue (re-routed when the tier changes), False for a named person."""
    return assignee is None or assignee.startswith(QUEUE_PREFIX)
