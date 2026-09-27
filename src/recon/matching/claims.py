# src/recon/matching/claims.py
"""ClaimsService: the only code path that writes to match_claims.

Every matching strategy (exact, fuzzy, rule-based — all WP4 increments
after this one) claims transactions through this service, never through
a raw session.add(MatchClaim(...)). Centralising it here means the
exclusivity guarantee's failure mode (IntegrityError on a duplicate
active claim) has exactly one place it can be raised from and exactly
one place a caller needs to catch it, rather than every matching
strategy needing its own try/except around raw ORM code.

Two-phase claim/release, not a single atomic "claim-and-match": a
strategy claims candidate transactions BEFORE it has fully decided they
match (so a claim can exist without match_result_id populated yet), then
either finalises the claim by recording a match_result_id, or releases
it if the candidate turns out not to be a real match after all (e.g. a
fuzzy candidate that failed a hard constraint check). This module
supports both paths; deciding which path a given comparison takes is the
matching strategy's job, not this service's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.persistence.models import MatchClaim


class ClaimConflictError(ValueError):
    """Raised when a normalised transaction already has an ACTIVE claim.
    This is the expected, correct outcome of the exclusivity guarantee
    working — not a bug to route around. A caller catching this should
    treat the candidate as unavailable and move on to the next one, never
    retry the same claim."""

    def __init__(self, normalised_transaction_id: uuid.UUID) -> None:
        self.normalised_transaction_id = normalised_transaction_id
        super().__init__(
            f"normalised_transaction_id={normalised_transaction_id} already has " "an active claim"
        )


class ClaimsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(
        self,
        normalised_transaction_id: uuid.UUID,
        role: str,
        *,
        match_result_id: uuid.UUID | None = None,
    ) -> MatchClaim:
        """Attempts to create an ACTIVE claim. Raises ClaimConflictError
        if one already exists for this transaction — the database's
        unique partial index is the actual source of truth; this method
        translates the resulting IntegrityError into a typed exception a
        caller can catch specifically, rather than a generic
        IntegrityError that could mean several different things.

        Flushes immediately rather than waiting for the caller's own
        commit, so the conflict (if any) surfaces at the call site, not
        at some later, harder-to-diagnose flush."""
        claim = MatchClaim(
            normalised_transaction_id=normalised_transaction_id,
            match_result_id=match_result_id,
            role=role,
            status="ACTIVE",
        )
        self._session.add(claim)
        try:
            self._session.flush()
        except IntegrityError as exc:
            self._session.rollback()
            raise ClaimConflictError(normalised_transaction_id) from exc
        return claim

    def finalise(self, claim: MatchClaim, match_result_id: uuid.UUID) -> None:
        """Records the match_result_id a previously-created claim belongs
        to, once the matching strategy has confirmed the candidate is a
        genuine match (not just a candidate under consideration)."""
        claim.match_result_id = match_result_id
        self._session.flush()

    def release(self, claim: MatchClaim) -> None:
        """Releases a claim that turned out not to be a real match —
        e.g. a fuzzy candidate that failed a hard constraint after being
        provisionally claimed. The row is never deleted; its status
        transitions to RELEASED, and released_at is stamped, preserving
        the full history of what was considered and rejected."""
        claim.status = "RELEASED"
        claim.released_at = datetime.now(UTC)
        self._session.flush()

    def is_claimed(self, normalised_transaction_id: uuid.UUID) -> bool:
        """Read-only check for whether a transaction currently has an
        ACTIVE claim. Useful for candidate-generation code that wants to
        skip already-claimed rows before attempting a claim at all —
        but note this is inherently a check-then-act read: the
        authoritative guarantee is always the unique partial index, not
        this method. A caller relying solely on is_claimed() to avoid
        calling claim() is optimising away unnecessary IntegrityErrors,
        not replacing the database-level guarantee."""
        return (
            self._session.query(MatchClaim)
            .filter(
                MatchClaim.normalised_transaction_id == normalised_transaction_id,
                MatchClaim.status == "ACTIVE",
            )
            .first()
            is not None
        )
