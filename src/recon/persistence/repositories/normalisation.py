# src/recon/persistence/repositories/normalisation.py
"""Repository for normalised_transactions and the raw-side reads
NormalisationService needs to reconstruct ParsedRow objects from
persisted RawTransaction rows."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from recon.domain.canonical import CanonicalTransaction
from recon.persistence.models import NormalisedTransaction, RawTransaction


class NormalisationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_parsed_raw_transactions(self, ingestion_file_id: uuid.UUID) -> list[RawTransaction]:
        """Only PARSED rows — QUARANTINED rows are excluded here rather
        than filtered later, since NormalisationPipeline would reject
        them anyway (see this increment's module-level scope note on
        quarantined rows)."""
        return (
            self._session.query(RawTransaction)
            .filter(
                RawTransaction.ingestion_file_id == ingestion_file_id,
                RawTransaction.parse_status == "PARSED",
            )
            .order_by(RawTransaction.source_line_no)
            .all()
        )

    def save(
        self, canonical: CanonicalTransaction, raw_transaction_id: uuid.UUID
    ) -> NormalisedTransaction:
        row = NormalisedTransaction(
            id=uuid.UUID(canonical.id),
            txn_date=canonical.txn_date,
            raw_transaction_id=raw_transaction_id,
            ingestion_file_id=uuid.UUID(canonical.ingestion_file_id),
            source=canonical.source.value,
            bank_code=canonical.bank_code,
            format_type=canonical.format_type.value,
            source_line_no=canonical.source_line_no,
            config_version=canonical.config_version,
            txn_id=canonical.txn_id,
            bank_ref=canonical.bank_ref,
            amount=canonical.amount,
            amount_minor=canonical.amount_minor,
            currency=canonical.currency,
            currency_exponent=canonical.currency_exponent_value,
            currency_source=canonical.currency_source.value,
            original_currency=canonical.original_currency,
            direction=canonical.direction.value,
            is_reversal=canonical.is_reversal,
            reverses_reference=canonical.reverses_reference,
            counterparty_name=canonical.counterparty_name,
            counterparty_name_normalised=canonical.counterparty_name_normalised,
            counterparty_account=canonical.counterparty_account,
            counterparty_account_masked=canonical.counterparty_account_masked,
            narration=canonical.narration,
            narration_normalised=canonical.narration_normalised,
            settlement_date=canonical.settlement_date,
            original_amount_text=canonical.original_amount_text,
            txn_timestamp_utc=canonical.txn_timestamp_utc,
            txn_timestamp_original=canonical.txn_timestamp_original,
            source_timezone=canonical.source_timezone,
            original_reference_text=canonical.original_reference_text,
            normalised_reference=canonical.normalised_reference,
            match_status=canonical.match_status,
        )
        self._session.add(row)
        return row
