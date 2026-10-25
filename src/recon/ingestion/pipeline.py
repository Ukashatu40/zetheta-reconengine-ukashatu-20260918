# src/recon/ingestion/pipeline.py
"""IngestionPipeline: everything that happens to an accepted file, in one place so
the upload endpoint, the seed script and the CLI behave identically.

    ingest (parse, mask card numbers, store raw rows, statement balances)
      -> normalise -> classify FORMAT_ERROR -> anomaly scan -> ingestion alerts
      -> settlement-uniqueness check -> audit (INGEST and NORMALISE)

The caller owns the transaction: nothing here commits, so one failure rolls the
whole file back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from recon.anomaly.service import AnomalyService
from recon.audit.chains import INGESTION_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.models import BankConfig
from recon.domain.enums import AuditActionType, SourceFormat, TransactionSource
from recon.excmgmt.classifier import ClassifierOutcome, ExceptionClassifier
from recon.ingestion.normalisation_service import NormalisationResult, NormalisationService
from recon.ingestion.service import IngestionResult, IngestionService
from recon.persistence.models import IngestionFile
from recon.runs.settings import RunSettings


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    file_row: IngestionFile
    result: IngestionResult
    normalised: NormalisationResult
    classified: ClassifierOutcome
    alerts_raised: int


class IngestionPipeline:
    def __init__(self, session: Session, settings: RunSettings, actor: str) -> None:
        self._session = session
        self._settings = settings
        self._actor = actor

    def process(
        self,
        *,
        path: Path,
        display_name: str,
        sha256: str,
        size_bytes: int,
        config: BankConfig,
        format_type: str,
        source: str,
        now: datetime,
    ) -> UploadOutcome:
        session = self._session
        result = IngestionService(session, self._actor).ingest_file(path, config, format_type)
        file_row = session.get(IngestionFile, uuid.UUID(result.ingestion_file_id))
        if file_row is None:
            raise ValueError("ingestion file row is missing after ingestion")
        file_row.original_filename = display_name
        file_row.source = source
        file_row.stored_path = str(path)

        normalised = NormalisationService(session).normalise_ingestion_file(
            result.ingestion_file_id, config, SourceFormat(format_type), TransactionSource(source)
        )
        audit = AuditLogger(session)
        classified = ExceptionClassifier(
            session, self._settings.taxonomy, audit
        ).classify_ingestion_file(file_row.id, now)

        anomalies = AnomalyService(session, self._settings.anomaly, self._settings.banks)
        alerts = anomalies.scan_ingestion_file(file_row.id, now).alerts_raised
        alerts += self._ingestion_alerts(anomalies, file_row, result, now)
        if source == "EXTERNAL":
            alerts += sum(anomalies.check_settlement_uniqueness(file_row.id, now).values())

        actor_id = self._actor
        audit.append(
            chain_id=INGESTION_CHAIN,
            actor_type="USER",
            actor_id=actor_id,
            action_type=AuditActionType.INGEST,
            occurred_at=now,
            affected_records={"ingestion_file_id": result.ingestion_file_id},
            after_state={
                "bank_code": config.bank_code,
                "format_type": format_type,
                "source": source,
                "size_bytes": size_bytes,
                "content_sha256": sha256,
                "record_count": result.record_count,
                "parsed_count": result.parsed_count,
                "error_count": result.error_count,
                "pan_masked_count": result.pan_masked_count,
                "alerts_raised": alerts,
            },
            rationale="file uploaded and ingested",
        )
        audit.append(
            chain_id=INGESTION_CHAIN,
            actor_type="USER",
            actor_id=actor_id,
            action_type=AuditActionType.NORMALISE,
            occurred_at=now,
            affected_records={"ingestion_file_id": result.ingestion_file_id},
            after_state={
                "normalised_count": normalised.normalised_count,
                "failed_count": normalised.failed_count,
                "config_version": config.config_version,
            },
            rationale="parsed rows normalised to the canonical model",
        )
        return UploadOutcome(file_row, result, normalised, classified, alerts)

    @staticmethod
    def _ingestion_alerts(
        anomalies: AnomalyService, file_row: IngestionFile, result: IngestionResult, now: datetime
    ) -> int:
        """Alerts the ingestion itself surfaced: format drift, card numbers, a
        statement that does not balance."""
        raised: dict[str, int] = {}
        if result.format_change is not None:
            raised |= anomalies.format_change_alert(
                bank_code=file_row.bank_code,
                filename=file_row.original_filename,
                change=result.format_change,
                blocked=False,
                now=now,
            )
        if result.pan_masked_count:
            raised |= anomalies.pan_exposure_alert(
                ingestion_file_id=file_row.id,
                bank_code=file_row.bank_code,
                filename=file_row.original_filename,
                count=result.pan_masked_count,
                now=now,
            )
        if result.balance_check is not None:
            raised |= anomalies.balance_alert(
                ingestion_file_id=file_row.id,
                bank_code=file_row.bank_code,
                filename=file_row.original_filename,
                check=result.balance_check,
                now=now,
            )
        return sum(raised.values())
