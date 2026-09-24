# src/recon/ingestion/integrity.py
"""File integrity: SHA-256 hashing and size, for duplicate-file detection
(R22, B4.4) independent of filename or ingestion timestamp.

Not yet wired to a repository — this increment provides the utility;
Increment 2 wires it into the ingestion_files insert path, where
content_sha256's unique constraint (see WP1's core-persistence migration)
is the database-level backstop this function's output is checked against.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB — streamed, never loads the whole file into memory


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def file_size_bytes(path: Path) -> int:
    return path.stat().st_size
