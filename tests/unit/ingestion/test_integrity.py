# tests/unit/ingestion/test_integrity.py
"""Tests for recon.ingestion.integrity.

test_identical_content_different_filenames_same_hash is the direct proof
behind B4.4's duplicate-settlement scenario: detection must key on content,
never on filename or ingestion timestamp.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from recon.ingestion.integrity import compute_sha256, file_size_bytes


def test_sha256_matches_known_value(tmp_path: Path) -> None:
    content = b"hello world\n"
    path = tmp_path / "sample.txt"
    path.write_bytes(content)

    assert compute_sha256(path) == hashlib.sha256(content).hexdigest()


def test_identical_content_different_filenames_same_hash(tmp_path: Path) -> None:
    content = b"HDFC,REF001,1000.00\n"
    file_a = tmp_path / "settlement_original.csv"
    file_b = tmp_path / "settlement_resubmitted.csv"
    file_a.write_bytes(content)
    file_b.write_bytes(content)

    assert compute_sha256(file_a) == compute_sha256(file_b)


def test_different_content_produces_different_hash(tmp_path: Path) -> None:
    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_bytes(b"content A")
    file_b.write_bytes(b"content B")

    assert compute_sha256(file_a) != compute_sha256(file_b)


def test_file_size_bytes_matches_actual_size(tmp_path: Path) -> None:
    path = tmp_path / "sized.csv"
    path.write_bytes(b"x" * 12345)

    assert file_size_bytes(path) == 12345
