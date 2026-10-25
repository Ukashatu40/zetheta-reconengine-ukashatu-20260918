# src/recon/api/v1/admin.py
"""Administration (A9.2): bank configuration (hot-swap, validation) and users.

ADMIN can reload bank configuration, create and deactivate users and issue or
revoke their API keys. Reading the live configuration needs only VIEWER, and any
ANALYST can validate a candidate configuration against a sample file, so the
operations team can check a bank's new layout before an admin deploys it.
Every change is written to the audit chain.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from recon.api.auth import Principal, require_admin, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.schemas.responses import (
    ApiKeyIssuedOut,
    BankConfigOut,
    BankConfigsOut,
    ConfigVersionOut,
    CreateUserRequest,
    IssueKeyRequest,
    Page,
    ReloadOut,
    SampleReportOut,
    UserOut,
    ValidationReportOut,
)
from recon.audit.chains import USERS_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.registry import get_registry
from recon.config.store import sync_bank_configurations
from recon.config.validation import validate_candidate
from recon.domain.enums import AuditActionType
from recon.persistence.models import ApiKey, BankConfiguration, User
from recon.security.api_keys import generate_api_key, hash_api_key

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_MAX_CONFIG_BYTES = 256 * 1024
_MAX_SAMPLE_BYTES = 5 * 1024 * 1024


# -- bank configuration ----------------------------------------------------------------


@router.get("/banks", response_model=BankConfigsOut, dependencies=[Depends(require_viewer)])
def list_bank_configs(session: SessionDep) -> BankConfigsOut:
    registry = get_registry()
    history = {
        row.bank_code: row
        for row in session.scalars(
            select(BankConfiguration).where(BankConfiguration.is_active.is_(True))
        )
    }
    banks = []
    for code, item in sorted(registry.loaded().items()):
        recorded = history.get(code)
        banks.append(
            BankConfigOut(
                bank_code=code,
                bank_name=item.config.bank_name,
                config_version=item.config.config_version,
                content_sha256=item.sha256,
                supported_formats=item.config.supported_formats,
                timezone=item.config.timezone,
                settlement_cycle=item.config.settlement_cycle,
                reconciliation_window_days=item.config.reconciliation_window_days,
                recorded_in_database=recorded is not None
                and recorded.content_sha256 == item.sha256,
            )
        )
    versions = session.scalar(select(func.count()).select_from(BankConfiguration)) or 0
    return BankConfigsOut(
        banks=banks,
        loaded_at=registry.loaded_at,
        last_error=registry.last_error,
        recorded_versions=versions,
    )


@router.get(
    "/banks/{bank_code}/versions",
    response_model=list[ConfigVersionOut],
    dependencies=[Depends(require_viewer)],
)
def bank_config_versions(bank_code: str, session: SessionDep) -> list[ConfigVersionOut]:
    rows = session.scalars(
        select(BankConfiguration)
        .where(BankConfiguration.bank_code == bank_code)
        .order_by(BankConfiguration.loaded_at.desc(), BankConfiguration.is_active.desc())
    ).all()
    return [ConfigVersionOut.model_validate(r) for r in rows]


@router.post("/banks/reload", response_model=ReloadOut)
def reload_bank_configs(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> ReloadOut:
    """Re-reads config/banks now (a changed file is also picked up automatically within
    a couple of seconds). An invalid file leaves the previous configuration live."""
    registry = get_registry()
    result = registry.reload()
    changes = sync_bank_configurations(
        session, registry, loaded_by=f"api-key:{principal.name}", now=now
    )
    session.commit()
    return ReloadOut(
        changed=result.changed,
        added=result.added,
        updated=result.updated,
        removed=result.removed,
        error=result.error,
        recorded=[f"{c.bank_code}:{c.to_version}:{c.kind}" for c in changes],
    )


@router.post(
    "/banks/validate",
    response_model=ValidationReportOut,
    dependencies=[Depends(require_analyst)],
)
def validate_bank_config(
    config: Annotated[UploadFile, File(description="candidate bank configuration (YAML)")],
    sample: Annotated[
        UploadFile | None, File(description="a sample of the bank's CSV file")
    ] = None,
) -> ValidationReportOut:
    """Checks a candidate configuration, and a sample file against it. Stores nothing."""
    raw_config = config.file.read(_MAX_CONFIG_BYTES + 1)
    if len(raw_config) > _MAX_CONFIG_BYTES:
        raise HTTPException(status_code=413, detail="configuration file is too large")
    sample_text: str | None = None
    if sample is not None:
        raw_sample = sample.file.read(_MAX_SAMPLE_BYTES + 1)
        if len(raw_sample) > _MAX_SAMPLE_BYTES:
            raise HTTPException(status_code=413, detail="sample file is too large")
        try:
            sample_text = raw_sample.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="the sample is not valid UTF-8") from exc
    try:
        config_text = raw_config.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="the configuration is not valid UTF-8") from exc

    report = validate_candidate(config_text, config.filename or "candidate.yaml", sample_text)
    sample_out = None
    if report.sample is not None:
        s = report.sample
        sample_out = SampleReportOut(
            header=s.header,
            missing_columns=s.change.missing,
            new_columns=s.change.unexpected,
            reordered=s.change.reordered,
            rows_checked=s.rows_checked,
            rows_valid=s.rows_valid,
            problems=s.errors,
            preview=s.preview,
        )
    return ValidationReportOut(
        ok=report.ok,
        bank_code=report.bank_code,
        config_version=report.config_version,
        config_errors=report.config_errors,
        warnings=report.warnings,
        sample=sample_out,
    )


# -- users ----------------------------------------------------------------------------------


def _audit_user(
    session: SessionDep,
    principal: Principal,
    now: NowDep,
    *,
    rationale: str,
    affected: dict[str, str],
    before: dict[str, object] | None,
    after: dict[str, object],
) -> None:
    AuditLogger(session).append(
        chain_id=USERS_CHAIN,
        actor_type="USER",
        actor_id=f"api-key:{principal.name}",
        action_type=AuditActionType.CONFIG_CHANGE,
        occurred_at=now,
        affected_records=affected,
        before_state=before,
        after_state=after,
        rationale=rationale,
    )


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: CreateUserRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> UserOut:
    user = User(username=body.username, email=body.email, role=body.role, created_at=now)
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="that username already exists") from exc
    _audit_user(
        session,
        principal,
        now,
        rationale=f"user {body.username} created",
        affected={"user_id": str(user.id)},
        before=None,
        after={"username": body.username, "role": body.role, "is_active": True},
    )
    result = UserOut.model_validate(user)
    session.commit()
    return result


@router.get("/users", response_model=Page[UserOut], dependencies=[Depends(require_admin)])
def list_users(session: SessionDep, limit: int = 100, offset: int = 0) -> Page[UserOut]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total = session.scalar(select(func.count()).select_from(User)) or 0
    rows = session.scalars(select(User).order_by(User.username).limit(limit).offset(offset)).all()
    return Page(
        items=[UserOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.post("/users/{user_id}/api-keys", response_model=ApiKeyIssuedOut, status_code=201)
def issue_api_key(
    user_id: uuid.UUID,
    body: IssueKeyRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> ApiKeyIssuedOut:
    """Issues a key with the user's role. The key is shown once; only its hash is kept."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="the user is deactivated")
    raw = generate_api_key()
    name = f"{user.username}:{body.label}"
    key = ApiKey(key_hash=hash_api_key(raw), name=name, role=user.role, user_id=user.id)
    session.add(key)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="that key label is already in use") from exc
    _audit_user(
        session,
        principal,
        now,
        rationale=f"API key {name} issued",
        affected={"user_id": str(user.id), "api_key_id": str(key.id)},
        before=None,
        after={"name": name, "role": user.role},
    )
    result = ApiKeyIssuedOut(id=key.id, name=name, role=user.role, api_key=raw)
    session.commit()
    return result


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: uuid.UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> UserOut:
    """Deactivates the user and revokes every key issued to them."""
    user = session.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_active = False
    revoked = len(
        session.scalars(
            update(ApiKey)
            .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(ApiKey.id)
        ).all()
    )
    _audit_user(
        session,
        principal,
        now,
        rationale=f"user {user.username} deactivated",
        affected={"user_id": str(user.id)},
        before={"is_active": True},
        after={"is_active": False, "keys_revoked": revoked},
    )
    session.flush()
    result = UserOut.model_validate(user)
    session.commit()
    return result
