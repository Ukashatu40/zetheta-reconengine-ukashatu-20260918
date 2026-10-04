# src/recon/paths.py
"""Locates the config directory.

Resolution order:
  1. RECON_CONFIG_DIR, if set. It must be valid; an invalid value is an
     error, never silently ignored.
  2. ./config relative to the working directory (the Docker image's WORKDIR
     is /app, which contains config/).
  3. a config/ directory in any parent of this file (source checkouts,
     including editable installs).

Never derive config paths from the package location with parents[N]: an
installed package lives in site-packages, not next to config/ (IB-10).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "RECON_CONFIG_DIR"
_MARKER = Path("banks") / "_schema.json"


class ConfigDirNotFoundError(FileNotFoundError):
    """No usable config directory was found."""


def _is_config_dir(path: Path) -> bool:
    return (path / _MARKER).is_file()


def _search_candidates() -> list[Path]:
    candidates = [Path.cwd() / "config"]
    candidates.extend(parent / "config" for parent in Path(__file__).resolve().parents)
    return candidates


def config_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        path = Path(override)
        if not _is_config_dir(path):
            raise ConfigDirNotFoundError(f"{ENV_VAR}={override!r} does not contain {_MARKER}")
        return path
    candidates = _search_candidates()
    for candidate in candidates:
        if _is_config_dir(candidate):
            return candidate
    tried = ", ".join(str(c) for c in candidates)
    raise ConfigDirNotFoundError(
        f"no config directory found (looked for {_MARKER} under: {tried}); set {ENV_VAR}"
    )
