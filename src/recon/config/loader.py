# src/recon/config/loader.py
"""Bank configuration loader: reads a YAML file, validates it against the
JSON Schema (R45), and returns a typed, immutable BankConfig.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError

from recon.config.models import BankConfig

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "banks" / "_schema.json"


class BankConfigError(ValueError):
    """Raised when a bank config file fails schema or model validation."""


def _load_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def load_bank_config(path: Path) -> BankConfig:
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise BankConfigError(f"{path}: expected a YAML mapping at the top level")

    schema = _load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)
        raise BankConfigError(f"{path}: schema validation failed: {details}")

    try:
        return BankConfig.model_validate(data)
    except PydanticValidationError as exc:
        raise BankConfigError(f"{path}: {exc}") from exc
