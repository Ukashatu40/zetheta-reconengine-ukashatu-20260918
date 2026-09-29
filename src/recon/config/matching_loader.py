# src/recon/config/matching_loader.py
"""Loader for matching configuration, mirroring
recon.config.loader.load_bank_config's two-layer validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError

from recon.config.matching_models import MatchingConfig

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "matching" / "_schema.json"


class MatchingConfigError(ValueError):
    """Raised when a matching config file fails schema or model validation."""


def _load_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def load_matching_config(path: Path) -> MatchingConfig:
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise MatchingConfigError(f"{path}: expected a YAML mapping at the top level")

    schema = _load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        details = "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors)
        raise MatchingConfigError(f"{path}: schema validation failed: {details}")

    try:
        return MatchingConfig.model_validate(data)
    except PydanticValidationError as exc:
        raise MatchingConfigError(f"{path}: {exc}") from exc
