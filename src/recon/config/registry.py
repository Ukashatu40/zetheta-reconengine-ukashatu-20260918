"""Bank configuration registry with hot-swap (B4.1).

Loads every configuration under config/banks, keyed by bank_code, and reloads
them WITHOUT restarting the service when a file changes: current() compares the
directory's file signatures (name, mtime, size) at most every
check_interval_seconds and re-reads on a difference.

A reload is all-or-nothing. If any file is invalid, the previous good
configuration stays live, the error is kept for the operator (status/last_error,
the admin endpoint) and logged; a bad edit can never take reconciliation down.

LiveBankConfigs is a read-only Mapping that always reflects the registry's
current state, so code holding it (RunSettings) sees a swap without being rebuilt.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from recon.config.loader import BankConfigError, parse_bank_config
from recon.config.models import BankConfig
from recon.paths import config_dir

logger = logging.getLogger("recon.config")

DEFAULT_CHECK_INTERVAL_SECONDS = 2.0


class DuplicateBankCodeError(ValueError):
    """Two config files declare the same bank_code."""


@dataclass(frozen=True, slots=True)
class LoadedBankConfig:
    config: BankConfig
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class ReloadResult:
    changed: bool
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    error: str | None = None


def _yaml_files(directory: Path) -> list[Path]:
    return [p for p in sorted(directory.glob("*.yaml")) if not p.name.startswith("_")]


def _signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
    entries = []
    for path in _yaml_files(directory):
        stat = path.stat()
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def _load_directory(directory: Path) -> dict[str, LoadedBankConfig]:
    loaded: dict[str, LoadedBankConfig] = {}
    for path in _yaml_files(directory):
        raw = path.read_bytes()
        config = parse_bank_config(raw.decode("utf-8"), str(path))
        if config.bank_code in loaded:
            raise DuplicateBankCodeError(f"{config.bank_code} is declared in more than one file")
        loaded[config.bank_code] = LoadedBankConfig(config, path, hashlib.sha256(raw).hexdigest())
    return loaded


def load_bank_configs(directory: Path | None = None) -> dict[str, BankConfig]:
    directory = directory if directory is not None else config_dir() / "banks"
    return {code: item.config for code, item in _load_directory(directory).items()}


class BankConfigRegistry:
    def __init__(
        self,
        directory: Path | None = None,
        check_interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._directory = directory if directory is not None else config_dir() / "banks"
        self._interval = check_interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._loaded = _load_directory(self._directory)
        self._signature = _signature(self._directory)
        self._checked_at = clock()
        self.loaded_at = datetime.now(UTC)
        self.last_error: str | None = None

    def current(self) -> dict[str, BankConfig]:
        self._maybe_reload()
        return {code: item.config for code, item in self._loaded.items()}

    def loaded(self) -> dict[str, LoadedBankConfig]:
        self._maybe_reload()
        return dict(self._loaded)

    def reload(self, *, force: bool = False) -> ReloadResult:
        """Re-reads the directory now. Without force, nothing happens unless a file changed."""
        with self._lock:
            return self._reload_locked(force=force)

    def _maybe_reload(self) -> None:
        if self._clock() - self._checked_at < self._interval:
            return
        with self._lock:
            if self._clock() - self._checked_at < self._interval:
                return  # another thread just checked
            self._reload_locked(force=False)

    def _reload_locked(self, *, force: bool) -> ReloadResult:
        self._checked_at = self._clock()
        try:
            signature = _signature(self._directory)
            if not force and signature == self._signature:
                return ReloadResult(changed=False)
            fresh = _load_directory(self._directory)
        except (BankConfigError, DuplicateBankCodeError, OSError, UnicodeDecodeError) as exc:
            # Keep the last good configuration; remember the signature so a bad file is
            # not re-parsed on every request, only when it changes again.
            self.last_error = str(exc)
            logger.error("bank configuration reload failed; keeping the previous: %s", exc)
            with contextlib.suppress(OSError):
                self._signature = _signature(self._directory)
            return ReloadResult(changed=False, error=str(exc))

        old = self._loaded
        added = sorted(set(fresh) - set(old))
        removed = sorted(set(old) - set(fresh))
        updated = sorted(c for c in set(fresh) & set(old) if fresh[c].sha256 != old[c].sha256)
        self._loaded = fresh
        self._signature = signature
        self.loaded_at = datetime.now(UTC)
        self.last_error = None
        changed = bool(added or removed or updated)
        if changed:
            logger.info("bank configuration hot-swapped: +%s ~%s -%s", added, updated, removed)
        return ReloadResult(changed, added, updated, removed)


class LiveBankConfigs(Mapping[str, BankConfig]):
    """A Mapping of bank_code to BankConfig that always shows the registry's current state."""

    def __getitem__(self, key: str) -> BankConfig:
        return get_registry().current()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_registry().current())

    def __len__(self) -> int:
        return len(get_registry().current())


@lru_cache(maxsize=1)
def get_registry() -> BankConfigRegistry:
    interval = float(os.environ.get("BANK_CONFIG_RELOAD_SECONDS", DEFAULT_CHECK_INTERVAL_SECONDS))
    return BankConfigRegistry(check_interval_seconds=interval)


def default_bank_configs() -> dict[str, BankConfig]:
    """The configurations that are live right now (reloaded if a file changed)."""
    return get_registry().current()
