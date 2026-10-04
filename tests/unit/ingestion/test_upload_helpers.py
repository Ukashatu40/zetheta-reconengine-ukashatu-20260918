# tests/unit/ingestion/test_upload_helpers.py
from __future__ import annotations

import io
from pathlib import Path

import pytest

from recon.ingestion.upload import (
    UploadRejectedError,
    extension_of,
    safe_display_name,
    save_stream,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("statement.csv", "statement.csv"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\stmt.csv", "stmt.csv"),
        ("a<b>c|d.csv", "abcd.csv"),
        ("bell\x07name.csv", "bellname.csv"),
        ("", "upload"),
        (None, "upload"),
        ("...", "upload"),
        ("é-statement.csv", "é-statement.csv"),
        ("x" * 500 + ".csv", ("x" * 500 + ".csv")[:200]),
    ],
)
def test_safe_display_name(raw: str | None, expected: str) -> None:
    assert safe_display_name(raw) == expected


@pytest.mark.parametrize(
    ("name", "ext"),
    [
        ("a.CSV", ".csv"),
        ("a.tar.gz", ".gz"),
        ("noext", ""),
        ("dir/a.xml", ".xml"),
        ("a\\b.STA", ".sta"),
    ],
)
def test_extension_of(name: str, ext: str) -> None:
    assert extension_of(name) == ext


def test_save_stream_writes_hashes_and_names_the_file_itself(tmp_path: Path) -> None:
    saved = save_stream(io.BytesIO(b"hello"), tmp_path, ".csv", 100)
    assert saved.size_bytes == 5
    assert saved.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert saved.path.parent == tmp_path and saved.path.suffix == ".csv"
    assert saved.path.read_bytes() == b"hello"


def test_save_stream_over_the_limit_is_413_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError) as caught:
        save_stream(io.BytesIO(b"x" * 11), tmp_path, ".csv", 10)
    assert caught.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_save_stream_exactly_at_the_limit_is_accepted(tmp_path: Path) -> None:
    assert save_stream(io.BytesIO(b"x" * 10), tmp_path, ".csv", 10).size_bytes == 10


def test_save_stream_empty_is_422_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError) as caught:
        save_stream(io.BytesIO(b""), tmp_path, ".csv", 10)
    assert caught.value.status_code == 422
    assert list(tmp_path.iterdir()) == []
