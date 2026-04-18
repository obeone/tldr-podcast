"""Tests for the config migration system."""

from __future__ import annotations

from pathlib import Path

import yaml

from tldr.config_migrations import (
    CURRENT_CONFIG_VERSION,
    upgrade_config_if_needed,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class TestUpgradeConfigIfNeeded:
    """Unit tests for upgrade_config_if_needed()."""

    def test_v1_config_upgrades_to_current(self, tmp_path: Path) -> None:
        """A v1 config (no config_version key) is upgraded and written back."""
        cfg_path = tmp_path / "config.yaml"
        raw = {"gemini": {"tts_model": "gemini-2.5-flash-preview-tts"}}
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["gemini"]["tts_style"]["audio_tags"] == "auto"

        on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert on_disk["config_version"] == CURRENT_CONFIG_VERSION
        assert on_disk["gemini"]["tts_style"]["audio_tags"] == "auto"

    def test_v1_upgrade_creates_backup(self, tmp_path: Path) -> None:
        """The original file is copied to <name>.v1.bak before rewriting."""
        cfg_path = tmp_path / "config.yaml"
        raw = {"gemini": {"tts_model": "gemini-2.5-flash-preview-tts"}}
        _write_yaml(cfg_path, raw)
        original_text = cfg_path.read_text(encoding="utf-8")

        upgrade_config_if_needed(dict(raw), cfg_path)

        backup = cfg_path.with_suffix(cfg_path.suffix + ".v1.bak")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original_text

    def test_current_version_is_not_rewritten(self, tmp_path: Path) -> None:
        """A config already at CURRENT_CONFIG_VERSION is left untouched on disk."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": CURRENT_CONFIG_VERSION,
            "gemini": {
                "tts_model": "gemini-2.5-flash-preview-tts",
                "tts_style": {"audio_tags": "off"},
            },
        }
        _write_yaml(cfg_path, raw)
        original_mtime = cfg_path.stat().st_mtime_ns

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["gemini"]["tts_style"]["audio_tags"] == "off"
        assert cfg_path.stat().st_mtime_ns == original_mtime
        backup = cfg_path.with_suffix(cfg_path.suffix + ".v1.bak")
        assert not backup.exists()

    def test_in_memory_upgrade_when_path_is_none(self) -> None:
        """Upgrade works without a backing file; no IO is attempted."""
        raw = {"gemini": {"tts_model": "gemini-3.1-flash-preview-tts"}}
        upgraded = upgrade_config_if_needed(raw, None)
        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["gemini"]["tts_style"]["audio_tags"] == "auto"

    def test_future_version_is_left_untouched(self, tmp_path: Path) -> None:
        """A config from a newer build is accepted as-is (no downgrade)."""
        cfg_path = tmp_path / "config.yaml"
        raw = {"config_version": CURRENT_CONFIG_VERSION + 5, "gemini": {}}
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)
        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION + 5
