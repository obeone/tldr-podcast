"""Tests for the config migration system."""

from __future__ import annotations

from pathlib import Path

import yaml

from tldr.config_migrations import (
    CURRENT_CONFIG_VERSION,
    upgrade_config_if_needed,
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
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
        raw = {
            "web": {"user_agent": "tldr-podcast/1.0"},
            "gemini": {"tts_model": "gemini-2.5-flash-preview-tts"},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["user_agent"] == _BROWSER_USER_AGENT
        assert upgraded["gemini"]["tts_style"]["audio_tags"] == "auto"

        on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert on_disk["config_version"] == CURRENT_CONFIG_VERSION
        assert on_disk["web"]["user_agent"] == _BROWSER_USER_AGENT
        assert on_disk["gemini"]["tts_style"]["audio_tags"] == "auto"

    def test_current_v2_config_with_old_default_user_agent_is_upgraded(
        self,
        tmp_path: Path,
    ) -> None:
        """A v2 config using the old default UA is migrated to the browser UA."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 2,
            "web": {"user_agent": "tldr-podcast/1.0"},
            "gemini": {"tts_style": {"audio_tags": "auto"}},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["user_agent"] == _BROWSER_USER_AGENT

    def test_custom_user_agent_is_preserved_during_upgrade(
        self,
        tmp_path: Path,
    ) -> None:
        """A user-provided UA is not overwritten by the migration."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 2,
            "web": {"user_agent": "custom-client/7.0"},
            "gemini": {"tts_style": {"audio_tags": "auto"}},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["user_agent"] == "custom-client/7.0"

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

    def test_v3_config_gets_cloak_fallback_auto(self, tmp_path: Path) -> None:
        """A v3 config is upgraded with scraping.cloak_fallback='auto'."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 3,
            "web": {"user_agent": _BROWSER_USER_AGENT},
            "gemini": {"tts_style": {"audio_tags": "auto"}},
            "scraping": {"max_articles": 15, "timeout_seconds": 10},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["scraping"]["cloak_fallback"] == "auto"

    def test_v3_config_preserves_user_set_cloak_fallback(self, tmp_path: Path) -> None:
        """A v3 config with a user-set cloak_fallback value is preserved (setdefault semantics)."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 3,
            "web": {"user_agent": _BROWSER_USER_AGENT},
            "gemini": {"tts_style": {"audio_tags": "auto"}},
            "scraping": {"max_articles": 15, "timeout_seconds": 10, "cloak_fallback": "off"},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["scraping"]["cloak_fallback"] == "off"

    def test_v1_upgrade_applies_all_migrations_including_v5(self, tmp_path: Path) -> None:
        """A full v1→v5 chain produces all expected keys."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "web": {"user_agent": "tldr-podcast/1.0"},
            "gemini": {"tts_model": "gemini-2.5-flash-preview-tts"},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["user_agent"] == _BROWSER_USER_AGENT
        assert upgraded["gemini"]["tts_style"]["audio_tags"] == "auto"
        assert upgraded["scraping"]["cloak_fallback"] == "auto"
        assert upgraded["web"]["check_delay_min"] == 0.5
        assert upgraded["web"]["check_delay_max"] == 2.0

    def test_v4_config_gets_check_delay_defaults(self, tmp_path: Path) -> None:
        """A v4 config is upgraded with web.check_delay_min/max defaults."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 4,
            "web": {"user_agent": _BROWSER_USER_AGENT},
            "gemini": {"tts_style": {"audio_tags": "auto"}},
            "scraping": {"cloak_fallback": "auto"},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["check_delay_min"] == 0.5
        assert upgraded["web"]["check_delay_max"] == 2.0

    def test_v4_config_preserves_user_set_check_delay(self, tmp_path: Path) -> None:
        """User-set check_delay bounds survive the v4→v5 migration."""
        cfg_path = tmp_path / "config.yaml"
        raw = {
            "config_version": 4,
            "web": {
                "user_agent": _BROWSER_USER_AGENT,
                "check_delay_min": 0,
                "check_delay_max": 0,
            },
            "gemini": {"tts_style": {"audio_tags": "auto"}},
            "scraping": {"cloak_fallback": "auto"},
        }
        _write_yaml(cfg_path, raw)

        upgraded = upgrade_config_if_needed(dict(raw), cfg_path)

        assert upgraded["config_version"] == CURRENT_CONFIG_VERSION
        assert upgraded["web"]["check_delay_min"] == 0
        assert upgraded["web"]["check_delay_max"] == 0
