# src/recon/ingestion/upload.py
"""Upload handling: size-capped streaming, extension allow-list, name hygiene.
The client's filename is never used as a path."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

ALLOWED_EXTENSIONS: dict[str, frozenset[str]] = {
    "CSV": frozenset({".csv"}),
    "MT940": frozenset({".sta", ".mt940", ".txt"}),
    "CAMT053": frozenset({".xml"}),
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_MAX_NAME_LENGTH = 200
_FORBIDDEN_NAME_CHARS = frozenset('<>:"|?*')


class UploadRejectedError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SavedUpload:
    path: Path
    size_bytes: int
    sha256: str


def safe_display_name(raw: str | None) -> str:
    """Basename only (both separators), printable characters, bounded length."""
    base = PurePosixPath((raw or "").replace("\\", "/")).name
    cleaned = "".join(c for c in base if c.isprintable() and c not in _FORBIDDEN_NAME_CHARS)
    return cleaned.strip(" .")[:_MAX_NAME_LENGTH] or "upload"


def extension_of(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def save_stream(source: BinaryIO, directory: Path, extension: str, max_bytes: int) -> SavedUpload:
    """Streams source to directory/<uuid><extension>, hashing as it goes.
    Raises UploadRejectedError (413 over the cap, 422 empty) and leaves no
    partial file behind."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}{extension}"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := source.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadRejectedError(413, f"file exceeds the {max_bytes} byte limit")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise UploadRejectedError(422, "file is empty")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return SavedUpload(path, size, digest.hexdigest())
