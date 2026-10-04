# tests/unit/runs/test_run_settings.py
from __future__ import annotations

from dataclasses import replace

from recon.config.registry import default_bank_configs
from recon.runs.settings import default_settings


def test_unknown_bank_falls_back_to_the_default_blocking_window() -> None:
    settings = default_settings()
    assert settings.blocking_for("ZZZZ") is settings.blocking
    assert settings.stale_after_days("ZZZZ") is None


def test_bank_window_overrides_blocking_and_stale_days() -> None:
    hdfc = default_bank_configs()["HDFC"].model_copy(update={"reconciliation_window_days": 5})
    settings = replace(default_settings(), banks={"HDFC": hdfc})
    assert settings.blocking_for("HDFC").date_window_days == 5
    assert settings.stale_after_days("HDFC") == 5


def test_a_zero_day_window_still_gives_a_valid_blocking_window() -> None:
    hdfc = default_bank_configs()["HDFC"].model_copy(update={"reconciliation_window_days": 0})
    settings = replace(default_settings(), banks={"HDFC": hdfc})
    assert settings.blocking_for("HDFC").date_window_days == 1
    assert settings.stale_after_days("HDFC") == 0
