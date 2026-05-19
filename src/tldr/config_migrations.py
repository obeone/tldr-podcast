"""
Schema migrations for the ``tldr-podcast`` configuration file.

The config file carries a top-level ``config_version`` integer.  When
:func:`tldr.config.load_config` loads a file whose version is lower than
:data:`CURRENT_CONFIG_VERSION`, every registered migration from the stored
version up to the current one is applied in order, the original file is
backed up to ``<path>.v<old>.bak``, and the upgraded config is written back
to disk.  A config with no ``config_version`` key is treated as version 1.

Adding a new migration
----------------------
1. Increment :data:`CURRENT_CONFIG_VERSION`.
2. Append a ``(from_version, migrate_fn)`` tuple to :data:`MIGRATIONS`, where
   ``migrate_fn`` takes a raw config dict and returns the migrated dict for
   ``from_version + 1``.  Do not mutate the input; return a new dict or a
   shallow-copied one.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

from tldr.user_agent import BROWSER_USER_AGENT

logger = logging.getLogger(__name__)

# Current schema version.  Bump this whenever you add a migration below.
CURRENT_CONFIG_VERSION: int = 5

_OLD_DEFAULT_USER_AGENT = "tldr-podcast/1.0"

def _migrate_1_to_2(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Add ``gemini.tts_style.audio_tags: auto`` for Gemini 3.x Flash TTS support.

    The flag controls whether the dialogue LLM is instructed to sprinkle
    English bracketed audio tags inline.  ``"auto"`` keeps backward-compatible
    behaviour: tags are enabled only when ``tts_model`` starts with
    ``"gemini-3"``.

    Parameters
    ----------
    raw : dict
        The raw config dict at schema version 1.

    Returns
    -------
    dict
        Config dict upgraded to schema version 2.
    """
    gemini = raw.setdefault("gemini", {})
    tts_style = gemini.setdefault("tts_style", {})
    tts_style.setdefault("audio_tags", "auto")
    return raw


def _migrate_2_to_3(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Replace the old default bot-like User-Agent with a browser-like UA.

    The migration updates only configurations that either omit
    ``web.user_agent`` or still use the previous default value. Custom
    user-provided values are preserved.

    Parameters
    ----------
    raw : dict
        The raw config dict at schema version 2.

    Returns
    -------
    dict
        Config dict upgraded to schema version 3.
    """
    web = raw.setdefault("web", {})
    if web.get("user_agent", _OLD_DEFAULT_USER_AGENT) == _OLD_DEFAULT_USER_AGENT:
        web["user_agent"] = BROWSER_USER_AGENT
    return raw


def _migrate_3_to_4(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Add ``scraping.cloak_fallback: auto`` for CloakBrowser stealth-browser support.

    The flag controls whether the CloakBrowser stealth-Chromium fallback is
    used when trafilatura fails to fetch or extract an article.  ``"auto"``
    keeps backward-compatible behaviour: the fallback is enabled only when
    the ``cloakbrowser`` package is importable.

    Parameters
    ----------
    raw : dict
        The raw config dict at schema version 3.

    Returns
    -------
    dict
        Config dict upgraded to schema version 4.
    """
    raw.setdefault("scraping", {}).setdefault("cloak_fallback", "auto")
    return raw


def _migrate_4_to_5(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Add ``web.check_delay_min`` / ``web.check_delay_max`` for request throttling.

    Probing every tldr.tech topic back-to-back makes the site rate-limit
    the burst and answer ``404`` for editions that actually exist.  These
    keys bound the randomised pause (seconds) inserted between successive
    requests.  The defaults (``1.0`` / ``3.0``) enable throttling; set both
    to ``0`` to restore the old concurrent, no-delay behaviour.  Existing
    user-set values are preserved via ``setdefault`` semantics.

    Parameters
    ----------
    raw : dict
        The raw config dict at schema version 4.

    Returns
    -------
    dict
        Config dict upgraded to schema version 5.
    """
    web = raw.setdefault("web", {})
    web.setdefault("check_delay_min", 1.0)
    web.setdefault("check_delay_max", 3.0)
    return raw


MIGRATIONS: list[tuple[int, Callable[[dict[str, Any]], dict[str, Any]]]] = [
    (1, _migrate_1_to_2),
    (2, _migrate_2_to_3),
    (3, _migrate_3_to_4),
    (4, _migrate_4_to_5),
]

# Enforce a contiguous migration chain from v1 to CURRENT_CONFIG_VERSION.
# A gap would silently leave a config partially upgraded; fail loudly instead.
_expected = list(range(1, CURRENT_CONFIG_VERSION))
_registered = [from_v for from_v, _ in MIGRATIONS]
assert _registered == _expected, (
    f"MIGRATIONS must cover versions {_expected} contiguously, "
    f"got {_registered}"
)


def _dump_yaml(raw: dict[str, Any]) -> str:
    """Render *raw* as a YAML string suitable for on-disk storage."""
    return yaml.dump(
        raw,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def upgrade_config_if_needed(
    raw: dict[str, Any],
    path: Path | None,
) -> dict[str, Any]:
    """
    Upgrade a raw config dict to :data:`CURRENT_CONFIG_VERSION` if outdated.

    When migrations are applied and *path* is provided, the original file is
    copied to ``<path>.v<old>.bak`` before the upgraded YAML is written back.
    Comments in the original file are not preserved — users relying on inline
    comments should edit the freshly-written file and read the upgrade notice
    in the logs.

    Parameters
    ----------
    raw : dict
        The raw config dict as parsed from YAML.
    path : Path or None
        Location of the config file on disk.  When ``None`` (tests, or a
        config that did not originate from a file), migrations are applied
        in-memory only and no backup is written.

    Returns
    -------
    dict
        The upgraded config dict.  The same object is returned unchanged when
        no migration was needed.
    """
    stored_version = int(raw.get("config_version", 1))

    if stored_version > CURRENT_CONFIG_VERSION:
        logger.warning(
            "Config file reports version %d but this build only understands "
            "up to version %d. Proceeding as-is; consider upgrading tldr-podcast.",
            stored_version,
            CURRENT_CONFIG_VERSION,
        )
        return raw

    if stored_version == CURRENT_CONFIG_VERSION:
        return raw

    logger.info(
        "Upgrading config schema from v%d to v%d…",
        stored_version,
        CURRENT_CONFIG_VERSION,
    )

    upgraded = raw
    for from_version, migrate in MIGRATIONS:
        if from_version < stored_version:
            continue
        upgraded = migrate(upgraded)

    upgraded["config_version"] = CURRENT_CONFIG_VERSION

    if path is not None:
        backup = path.with_suffix(path.suffix + f".v{stored_version}.bak")
        try:
            shutil.copy2(path, backup)
            # Restrict backup to owner read/write — the file may sit next to
            # user-sensitive config even though we only ever back up the raw
            # on-disk form (env-var references, not resolved secrets).
            try:
                backup.chmod(0o600)
            except OSError:
                pass
            logger.info("Saved pre-upgrade backup to %s", backup)
        except OSError as exc:
            logger.warning("Could not write backup %s: %s", backup, exc)

        try:
            path.write_text(_dump_yaml(upgraded), encoding="utf-8")
            logger.info("Wrote upgraded config to %s", path)
        except OSError as exc:
            logger.warning(
                "Could not persist upgraded config to %s: %s — continuing with "
                "in-memory upgrade only.",
                path,
                exc,
            )

    return upgraded
