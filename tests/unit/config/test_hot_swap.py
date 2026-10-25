"""Hot-swappable bank configuration (B4.1): reload without a restart, keep the last
good configuration when an edit is invalid."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from recon.config.registry import (
    BankConfigRegistry,
    DuplicateBankCodeError,
    _load_directory,
)
from recon.paths import config_dir


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def banks(tmp_path: Path) -> Path:
    directory = tmp_path / "banks"
    directory.mkdir()
    for name in ("hdfc.v1.yaml", "icici.v1.yaml"):
        shutil.copy(config_dir() / "banks" / name, directory / name)
    return directory


def _registry(directory: Path, clock: _Clock, interval: float = 2.0) -> BankConfigRegistry:
    return BankConfigRegistry(directory, check_interval_seconds=interval, clock=clock)


def _rewrite(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))
    # A same-size edit within one filesystem tick would keep the signature; bump the mtime.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_the_registry_loads_every_file_once_at_start(banks: Path) -> None:
    registry = _registry(banks, _Clock())
    assert set(registry.current()) == {"HDFC", "ICICI"}
    assert registry.last_error is None


def test_a_changed_file_is_picked_up_after_the_check_interval_without_a_restart(
    banks: Path,
) -> None:
    clock = _Clock()
    registry = _registry(banks, clock)
    assert registry.current()["HDFC"].reconciliation_window_days == 1

    _rewrite(
        banks / "hdfc.v1.yaml", "reconciliation_window_days: 1", "reconciliation_window_days: 3"
    )

    assert (
        registry.current()["HDFC"].reconciliation_window_days == 1
    )  # inside the interval: not rechecked
    clock.now += 2.5
    assert registry.current()["HDFC"].reconciliation_window_days == 3


def test_reload_reports_what_changed(banks: Path) -> None:
    registry = _registry(banks, _Clock())
    _rewrite(
        banks / "icici.v1.yaml", "reconciliation_window_days: 1", "reconciliation_window_days: 2"
    )
    result = registry.reload()
    assert (result.changed, result.updated, result.added, result.removed) == (
        True,
        ["ICICI"],
        [],
        [],
    )
    assert registry.reload().changed is False  # nothing further to do


def test_a_new_file_is_added_and_a_deleted_one_removed(banks: Path) -> None:
    registry = _registry(banks, _Clock())
    shutil.copy(config_dir() / "banks" / "axis.v1.yaml", banks / "axis.v1.yaml")
    (banks / "icici.v1.yaml").unlink()
    result = registry.reload()
    assert (result.added, result.removed) == (["AXIS"], ["ICICI"])
    assert set(registry.current()) == {"HDFC", "AXIS"}


def test_an_invalid_edit_keeps_the_previous_configuration_live(banks: Path) -> None:
    clock = _Clock()
    registry = _registry(banks, clock)
    _rewrite(banks / "hdfc.v1.yaml", "timezone: Asia/Kolkata", "timezone: Not/AZone")

    result = registry.reload()

    assert result.changed is False and result.error is not None
    assert registry.current()["HDFC"].timezone == "Asia/Kolkata"  # the last good one
    assert registry.last_error is not None and "Not/AZone" in registry.last_error


def test_a_bad_file_is_not_reparsed_on_every_request_and_recovers_when_fixed(banks: Path) -> None:
    clock = _Clock()
    registry = _registry(banks, clock)
    _rewrite(banks / "hdfc.v1.yaml", "timezone: Asia/Kolkata", "timezone: Not/AZone")
    clock.now += 3
    registry.current()
    first_error = registry.last_error
    clock.now += 3
    registry.current()  # same bad content: no new parse, error unchanged
    assert registry.last_error == first_error

    _rewrite(banks / "hdfc.v1.yaml", "timezone: Not/AZone", "timezone: Asia/Dubai")
    clock.now += 3
    assert registry.current()["HDFC"].timezone == "Asia/Dubai"
    assert registry.last_error is None


def test_force_reload_rereads_even_when_nothing_changed(banks: Path) -> None:
    registry = _registry(banks, _Clock())
    assert registry.reload(force=True).changed is False  # same content: nothing to report


def test_two_files_for_one_bank_are_rejected(banks: Path) -> None:
    shutil.copy(banks / "hdfc.v1.yaml", banks / "hdfc.v2.yaml")
    with pytest.raises(DuplicateBankCodeError):
        _load_directory(banks)


def test_loaded_exposes_a_content_hash_per_bank(banks: Path) -> None:
    registry = _registry(banks, _Clock())
    before = registry.loaded()["HDFC"].sha256
    _rewrite(
        banks / "hdfc.v1.yaml", "reconciliation_window_days: 1", "reconciliation_window_days: 4"
    )
    registry.reload()
    assert registry.loaded()["HDFC"].sha256 != before and len(before) == 64
