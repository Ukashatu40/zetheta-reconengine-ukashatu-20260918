# src/recon/config/store.py
"""Versioned record of bank configurations (the bank_configurations table).

The YAML files stay the source of truth. This records, keyed by content hash,
which configuration was live and when, and who loaded it, so a reconciliation
can be tied to the exact configuration that produced it. Every change is also
written to the audit chain (A4.3 CONFIG_CHANGE).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from recon.audit.chains import CONFIG_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.registry import BankConfigRegistry
from recon.domain.enums import AuditActionType
from recon.persistence.models import BankConfiguration


@dataclass(frozen=True, slots=True)
class ConfigChange:
    bank_code: str
    kind: str  # ADDED, UPDATED or REACTIVATED
    from_version: str | None
    to_version: str
    to_sha256: str


def sync_bank_configurations(
    session: Session, registry: BankConfigRegistry, *, loaded_by: str, now: datetime
) -> list[ConfigChange]:
    """Brings the table in line with what is live. A configuration already recorded
    as active changes nothing, so calling this on every run is cheap."""
    changes: list[ConfigChange] = []
    audit = AuditLogger(session)
    for bank_code, item in sorted(registry.loaded().items()):
        active = session.scalars(
            select(BankConfiguration).where(
                BankConfiguration.bank_code == bank_code, BankConfiguration.is_active.is_(True)
            )
        ).first()
        if active is not None and active.content_sha256 == item.sha256:
            continue
        previous_version = active.config_version if active else None
        if active is not None:
            session.execute(
                update(BankConfiguration)
                .where(BankConfiguration.id == active.id)
                .values(is_active=False)
            )
            session.flush()
        existing = session.scalars(
            select(BankConfiguration).where(
                BankConfiguration.bank_code == bank_code,
                BankConfiguration.content_sha256 == item.sha256,
            )
        ).first()
        if existing is not None:
            existing.is_active = True
            existing.loaded_by = loaded_by
            existing.loaded_at = now
            kind = "REACTIVATED"
        else:
            session.add(
                BankConfiguration(
                    bank_code=bank_code,
                    config_version=item.config.config_version,
                    content_sha256=item.sha256,
                    config_json=item.config.model_dump(mode="json"),
                    source_path=item.path.name,
                    is_active=True,
                    loaded_by=loaded_by,
                    loaded_at=now,
                )
            )
            kind = "UPDATED" if active is not None else "ADDED"
        session.flush()
        change = ConfigChange(
            bank_code, kind, previous_version, item.config.config_version, item.sha256
        )
        changes.append(change)
        audit.append(
            chain_id=CONFIG_CHAIN,
            actor_type="SYSTEM" if loaded_by.startswith("system:") else "USER",
            actor_id=loaded_by,
            action_type=AuditActionType.CONFIG_CHANGE,
            occurred_at=now,
            affected_records={"bank_code": bank_code},
            before_state={"config_version": previous_version},
            after_state={
                "config_version": item.config.config_version,
                "content_sha256": item.sha256,
                "change": kind,
            },
            rationale=f"{bank_code} configuration {kind.lower()}",
        )
    return changes
