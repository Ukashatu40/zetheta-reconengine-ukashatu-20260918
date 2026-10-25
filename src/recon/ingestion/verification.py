# src/recon/ingestion/verification.py
"""Re-verification of stored statement files (Case Study C4, "The Phantom
Settlement").

The SHA-256 taken at upload is stored with the file. Re-hashing the bytes that are
now on disk and comparing proves the file is the one that was ingested: a file
edited afterwards, or one that has gone missing, is flagged and raised as a
FILE_TAMPERED alert, and every check is written to the audit chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.anomaly.service import AlertDraft, AnomalyService
from recon.audit.chains import INGESTION_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.ingestion.integrity import compute_sha256
from recon.persistence.models import IngestionFile


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ingestion_file_id: str
    status: str  # OK, TAMPERED, MISSING
    expected_sha256: str
    actual_sha256: str | None


@dataclass(frozen=True, slots=True)
class VerifyAllOutcome:
    checked: int
    ok: int
    tampered: int
    missing: int


class FileVerifier:
    def __init__(self, session: Session, anomalies: AnomalyService, actor: str) -> None:
        self._session = session
        self._anomalies = anomalies
        self._actor = actor

    def verify(self, ingestion_file: IngestionFile, now: datetime) -> VerificationResult:
        if ingestion_file.stored_path is None:
            raise ValueError("this file's bytes were not kept, so it cannot be re-verified")
        path = Path(ingestion_file.stored_path)
        actual: str | None = compute_sha256(path) if path.is_file() else None
        if actual is None:
            status = "MISSING"
        elif actual == ingestion_file.content_sha256:
            status = "OK"
        else:
            status = "TAMPERED"

        previous = ingestion_file.integrity_status
        ingestion_file.integrity_status = status
        ingestion_file.integrity_checked_at = now
        if status != "OK":
            self._anomalies.raise_alerts(
                [
                    AlertDraft(
                        alert_type="FILE_TAMPERED",
                        severity="CRITICAL",
                        subject=ingestion_file.original_filename,
                        message=(
                            f"{ingestion_file.original_filename} "
                            + (
                                "can no longer be found on disk."
                                if status == "MISSING"
                                else "no longer matches the SHA-256 recorded at ingestion."
                            )
                        ),
                        details={
                            "ingestion_file_id": str(ingestion_file.id),
                            "status": status,
                            "expected_sha256": ingestion_file.content_sha256,
                            "actual_sha256": actual,
                        },
                        dedupe_key=f"tamper:{ingestion_file.id}:{actual or 'missing'}",
                        bank_code=ingestion_file.bank_code,
                        route_to="compliance",
                    )
                ],
                now,
            )
        AuditLogger(self._session).append(
            chain_id=INGESTION_CHAIN,
            actor_type="SYSTEM" if self._actor.startswith("system:") else "USER",
            actor_id=self._actor,
            action_type=AuditActionType.FILE_VERIFY,
            occurred_at=now,
            affected_records={"ingestion_file_id": str(ingestion_file.id)},
            before_state={"integrity_status": previous},
            after_state={
                "integrity_status": status,
                "expected_sha256": ingestion_file.content_sha256,
            },
            rationale="stored file re-hashed against the SHA-256 taken at ingestion",
        )
        self._session.flush()
        return VerificationResult(
            str(ingestion_file.id), status, ingestion_file.content_sha256, actual
        )

    def verify_all(self, now: datetime) -> VerifyAllOutcome:
        counts = {"OK": 0, "TAMPERED": 0, "MISSING": 0}
        files = self._session.scalars(
            select(IngestionFile)
            .where(IngestionFile.stored_path.is_not(None))
            .order_by(IngestionFile.ingested_at, IngestionFile.id)
        ).all()
        for ingestion_file in files:
            counts[self.verify(ingestion_file, now).status] += 1
        return VerifyAllOutcome(len(files), counts["OK"], counts["TAMPERED"], counts["MISSING"])
