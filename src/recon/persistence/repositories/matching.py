# src/recon/persistence/repositories/matching.py
from sqlalchemy import exists
from sqlalchemy.orm import Session

from recon.persistence.models import MatchClaim, NormalisedTransaction


class MatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_unmatched(self, bank_code: str, source: str) -> list[NormalisedTransaction]:
        """Transactions still available to a matching level: UNMATCHED and
        holding no ACTIVE claim. A PENDING_REVIEW pair keeps both claims
        ACTIVE while a human decides, so those rows are excluded here
        (IB-07). Releasing a claim returns the transaction to the pool."""
        actively_claimed = exists().where(
            MatchClaim.normalised_transaction_id == NormalisedTransaction.id,
            MatchClaim.status == "ACTIVE",
        )
        return (
            self._session.query(NormalisedTransaction)
            .filter(
                NormalisedTransaction.bank_code == bank_code,
                NormalisedTransaction.source == source,
                NormalisedTransaction.match_status == "UNMATCHED",
                ~actively_claimed,
            )
            .order_by(NormalisedTransaction.txn_date, NormalisedTransaction.id)
            .all()
        )
