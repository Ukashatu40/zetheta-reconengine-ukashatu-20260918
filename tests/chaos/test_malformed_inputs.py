"""Chaos: damaged, hostile and nonsensical input files (A8.1).

The contract is that no input crashes the pipeline with an unhandled error or corrupts
what is already stored: a file either ingests what is readable and reports the rest, or
is refused with a clear exception. Nothing in between."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recon.ingestion.format_check import FormatChangeDetectedError
from recon.ingestion.pipeline import IngestionPipeline
from recon.persistence.models import NormalisedTransaction, RawTransaction
from recon.runs.settings import default_settings
from recon.samples.generator import DEFAULT_SEED, build_sample_set

pytestmark = pytest.mark.chaos

_NOW = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)
_SAMPLE = build_sample_set(DEFAULT_SEED, 1)


def _first(fmt: str, source: str = "EXTERNAL") -> tuple[str, str]:
    """(bank, content) of the first generated file in this format."""
    f = next(f for f in _SAMPLE.files if f.format_type == fmt and f.source == source)
    return f.bank, f.content


def _ingest(session: Session, tmp_path: Path, data: bytes, *, bank: str, fmt: str) -> int:
    path = tmp_path / "damaged.bin"
    path.write_bytes(data)
    settings = default_settings()
    outcome = IngestionPipeline(session, settings, "chaos").process(
        path=path,
        display_name="damaged",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        config=settings.banks[bank],
        format_type=fmt,
        source="EXTERNAL",
        now=_NOW,
    )
    return outcome.result.parsed_count


def _stored(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(RawTransaction)) or 0


def test_a_csv_cut_off_mid_row_keeps_the_readable_rows_and_flags_the_broken_one(
    db_session: Session, tmp_path: Path
) -> None:
    bank, text = _first("CSV")
    cut = text[: len(text) // 2]  # lands inside a row
    whole_rows = cut.count("\n") - 1  # minus the header
    parsed = _ingest(db_session, tmp_path, cut.encode(), bank=bank, fmt="CSV")
    assert whole_rows <= parsed <= whole_rows + 1
    assert _stored(db_session) >= whole_rows


def test_invalid_utf8_bytes_are_a_typed_refusal_not_a_crash(
    db_session: Session, tmp_path: Path
) -> None:
    """A ValueError subclass, which the upload endpoint maps to a 422 (see test_upload_chaos)."""
    bank, text = _first("CSV")
    data = text.encode()
    damaged = data[:200] + b"\xff\xfe\xfa\x80" + data[200:]
    with pytest.raises(UnicodeDecodeError):
        _ingest(db_session, tmp_path, damaged, bank=bank, fmt="CSV")


@pytest.mark.parametrize("payload", [b"", b"\n\n\n", b"\x00" * 512, bytes(range(256)) * 8])
def test_empty_or_binary_input_is_handled_not_fatal(
    db_session: Session, tmp_path: Path, payload: bytes
) -> None:
    bank, _ = _first("CSV")
    try:
        parsed = _ingest(db_session, tmp_path, payload, bank=bank, fmt="CSV")
    except ValueError:
        return  # a clear, typed refusal is acceptable
    assert parsed == 0


def test_a_header_only_csv_ingests_zero_records(db_session: Session, tmp_path: Path) -> None:
    bank, text = _first("CSV")
    header = text.splitlines()[0] + "\n"
    assert _ingest(db_session, tmp_path, header.encode(), bank=bank, fmt="CSV") == 0


def test_a_missing_required_column_is_blocked_and_stores_nothing(
    db_session: Session, tmp_path: Path
) -> None:
    bank, text = _first("CSV")
    lines = text.splitlines()
    lines[0] = ",".join(lines[0].split(",")[1:])  # drop the first mapped column
    before = _stored(db_session)
    with pytest.raises(FormatChangeDetectedError):
        _ingest(db_session, tmp_path, ("\n".join(lines) + "\n").encode(), bank=bank, fmt="CSV")
    assert _stored(db_session) == before  # nothing half-ingested


def test_an_mt940_cut_off_inside_a_statement_line_is_handled(
    db_session: Session, tmp_path: Path
) -> None:
    bank, text = _first("MT940")
    parsed = _ingest(
        db_session, tmp_path, text[: len(text) * 2 // 3].encode(), bank=bank, fmt="MT940"
    )
    assert parsed >= 0


def test_malformed_camt_xml_is_refused_cleanly(db_session: Session, tmp_path: Path) -> None:
    bank, xml = _first("CAMT053")
    broken = xml[: len(xml) // 2]
    with pytest.raises(Exception):  # noqa: B017 - any typed parser error
        _ingest(db_session, tmp_path, broken.encode(), bank=bank, fmt="CAMT053")


def test_an_xml_external_entity_is_never_resolved(db_session: Session, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    bank, xml = _first("CAMT053")
    hostile = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://{secret}">]>'
        + xml.split("?>", 1)[-1].replace("<Ntry>", "<Ntry><AddtlNtryInf>&x;</AddtlNtryInf>", 1)
    )
    try:
        _ingest(db_session, tmp_path, hostile.encode(), bank=bank, fmt="CAMT053")
    except Exception:
        return
    leaked = db_session.scalar(
        select(func.count())
        .select_from(NormalisedTransaction)
        .where(NormalisedTransaction.narration.contains("TOP-SECRET-CONTENT"))
    )
    assert leaked == 0


def test_a_billion_laughs_document_is_not_expanded(db_session: Session, tmp_path: Path) -> None:
    bank, _ = _first("CAMT053")
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "AAAAAAAAAA">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        '<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]><Document>&d;</Document>'
    )
    with pytest.raises(Exception):  # noqa: B017
        _ingest(db_session, tmp_path, bomb.encode(), bank=bank, fmt="CAMT053")
