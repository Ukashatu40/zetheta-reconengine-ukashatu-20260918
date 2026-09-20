"""Verifies the package is importable and the toolchain is wired correctly.

Replaced by genuine domain tests in WP1 Increment 3.
"""

from __future__ import annotations

import importlib


def test_package_imports() -> None:
    module = importlib.import_module("recon")
    assert module.__name__ == "recon"
