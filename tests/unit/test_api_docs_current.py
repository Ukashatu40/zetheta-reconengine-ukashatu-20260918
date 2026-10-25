"""docs/API.md is generated from the OpenAPI schema; it must not be allowed to go stale."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_the_committed_api_reference_matches_the_application() -> None:
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "gen_api_docs.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_dashboards_openapi_snapshot_matches_the_application() -> None:
    result = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "export_openapi.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
