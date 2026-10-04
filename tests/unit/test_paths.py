# tests/unit/test_paths.py
from __future__ import annotations

from pathlib import Path

import pytest

from recon.paths import ENV_VAR, ConfigDirNotFoundError, config_dir


def _make_config(root: Path) -> Path:
    (root / "banks").mkdir(parents=True)
    (root / "banks" / "_schema.json").write_text("{}")
    return root


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, str(_make_config(tmp_path / "custom")))
    assert config_dir() == tmp_path / "custom"


def test_an_invalid_env_override_raises_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "missing"))
    with pytest.raises(ConfigDirNotFoundError, match="does not contain"):
        config_dir()


def test_working_directory_config_is_used_when_no_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Docker image case: WORKDIR /app contains config/."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    _make_config(tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    assert config_dir().resolve() == (tmp_path / "config").resolve()


def test_a_source_checkout_is_found_from_any_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)  # no config/ here
    assert (config_dir() / "banks" / "_schema.json").is_file()


def test_no_config_anywhere_names_what_was_tried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr("recon.paths._search_candidates", lambda: [tmp_path / "nope"])
    with pytest.raises(ConfigDirNotFoundError, match="no config directory found") as caught:
        config_dir()
    assert str(tmp_path / "nope") in str(caught.value)
