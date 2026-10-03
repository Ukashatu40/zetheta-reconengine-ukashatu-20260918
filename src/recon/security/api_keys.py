# src/recon/security/api_keys.py
"""API key generation and hashing.

SHA-256 is appropriate here because keys are 256-bit random values. Slow
password hashes (bcrypt, argon2) exist to protect low-entropy human
passwords and would add cost per request without adding protection.
"""

from __future__ import annotations

import hashlib
import secrets

_PREFIX = "rk_"


def generate_api_key() -> str:
    return _PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
