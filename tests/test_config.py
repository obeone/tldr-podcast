"""Tests for src/tldr/config.py."""

import textwrap
from pathlib import Path

import pytest

from tldr.config import ConfigError, load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp YAML file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnvVarResolution:
    """``*_env`` keys must be replaced by their resolved environment value."""

    def test_top_level_env_key_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_SECRET", "top_secret_value")
        cfg_file = write_yaml(
            tmp_path,
            """
            service:
              api_key_env: MY_SECRET
              host: example.com
            """,
        )
        cfg = load_config(cfg_file)
        assert cfg["service"]["api_key"] == "top_secret_value"
        assert "api_key_env" not in cfg["service"]

    def test_nested_env_key_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_SECRET", "hunter2")
        cfg_file = write_yaml(
            tmp_path,
            """
            backend:
              host: example.com
              token_env: MY_SECRET
            """,
        )
        cfg = load_config(cfg_file)
        assert cfg["backend"]["token"] == "hunter2"
        assert "token_env" not in cfg["backend"]

    def test_multiple_env_keys_resolved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KEY_A", "value_a")
        monkeypatch.setenv("KEY_B", "value_b")
        cfg_file = write_yaml(
            tmp_path,
            """
            section:
              first_env: KEY_A
              second_env: KEY_B
            """,
        )
        cfg = load_config(cfg_file)
        assert cfg["section"]["first"] == "value_a"
        assert cfg["section"]["second"] == "value_b"

    def test_plain_keys_unchanged(self, tmp_path: Path) -> None:
        cfg_file = write_yaml(tmp_path, "key: value\n")
        cfg = load_config(cfg_file)
        assert cfg["key"] == "value"


class TestMissingEnvVar:
    """A missing environment variable must raise :exc:`ConfigError`."""

    def test_raises_config_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        cfg_file = write_yaml(tmp_path, "token_env: NONEXISTENT_VAR\n")
        with pytest.raises(ConfigError) as exc_info:
            load_config(cfg_file)
        assert "NONEXISTENT_VAR" in str(exc_info.value)

    def test_error_message_contains_var_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
        cfg_file = write_yaml(tmp_path, "secret_key_env: SECRET_KEY\n")
        with pytest.raises(ConfigError, match="SECRET_KEY"):
            load_config(cfg_file)


class TestMissingFile:
    """A non-existent config file must raise :exc:`ConfigError`."""

    def test_raises_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(ConfigError):
            load_config(missing)

    def test_error_mentions_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yaml"
        with pytest.raises(ConfigError, match="nope.yaml"):
            load_config(missing)
