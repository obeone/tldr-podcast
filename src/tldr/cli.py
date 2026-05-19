"""
TLDR Newsletter → Podcast CLI.

Commands
--------
    tldr-podcast --version                   Print the installed version and exit.
    tldr-podcast run [OPTIONS]               Run the full pipeline.
    tldr-podcast config init                 Interactive configuration wizard.
    tldr-podcast config show                 Display the current configuration file.
    tldr-podcast completions SHELL           Print shell completion script (bash/zsh/fish).

Run options
-----------
--config          Path to the YAML configuration file (default: $XDG_CONFIG_HOME/tldr/config.yaml).
--topics          Comma-separated topic slugs to fetch, e.g. ``ai,devops``.
--date            Date to process in YYYY-MM-DD format (default: today).
--output-dir      Directory where the podcast file is written (overrides config).
--no-interactive  Skip the topic-selection checkbox prompt.
--dry-run         Print the generated dialogue to stdout instead of calling TTS.
--no-audio        Generate the script and report but skip TTS and audio export.
--no-progress     Disable the rich progress bar.
--verbose         Enable DEBUG-level logging.
--no-report       Disable the report folder generated alongside the podcast.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import click
import coloredlogs
import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax

from tldr.audio_exporter import export_audio
from tldr.config import ConfigError, load_config
from tldr.link_extractor import extract_links
from tldr.llm_summarizer import generate_dialogue, rank_articles_by_interest
from tldr.models import Article
from tldr.report_generator import generate_report
from tldr.token_tracker import TokenTracker
from tldr.tts_generator import generate_audio_chunks
from tldr.user_agent import BROWSER_USER_AGENT
from tldr.web_scraper import scrape_articles
from tldr.web_source import (
    SUPPORTED_TOPICS,
    check_availability,
    fetch_newsletters,
    validate_topics,
)

logger = logging.getLogger(__name__)

console = Console(stderr=False)


def _xdg_config_home() -> Path:
    """Return ``$XDG_CONFIG_HOME`` or its default (``~/.config``)."""
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value else Path.home() / ".config"


_DEFAULT_CONFIG = _xdg_config_home() / "tldr" / "config.yaml"

_GEMINI_VOICES = [
    "Puck", "Charon", "Kore", "Fenrir", "Aoede",
    "Leda", "Orus", "Zephyr",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_ffmpeg() -> None:
    """
    Verify that ffmpeg is available in PATH and abort with a helpful message if not.

    Raises
    ------
    SystemExit
        Exits with code 1 if ffmpeg cannot be found.
    """
    if shutil.which("ffmpeg") is None:
        click.echo(
            "[ERROR] ffmpeg not found in PATH. Install it to enable audio export.\n"
            "  macOS:         brew install ffmpeg\n"
            "  Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  Windows:       https://ffmpeg.org/download.html",
            err=True,
        )
        sys.exit(1)


def _dedup_articles(articles: list) -> list:
    """
    Return a copy of *articles* with duplicates removed.

    Two articles are considered identical when their titles match after
    lowercasing and collapsing runs of whitespace to a single space.
    The first occurrence is kept; subsequent duplicates are dropped.

    Parameters
    ----------
    articles : list[Article]
        Ordered list of articles, possibly containing duplicates.

    Returns
    -------
    list[Article]
        Deduplicated list preserving the original order of first occurrences.
    """
    seen: set[str] = set()
    result = []
    for article in articles:
        key = re.sub(r"\s+", " ", article.title.lower().strip())
        if key not in seen:
            seen.add(key)
            result.append(article)
    return result


def _setup_logging(verbose: bool) -> None:
    """Configure coloredlogs for the root logger."""
    level = "DEBUG" if verbose else "INFO"
    coloredlogs.install(
        level=level,
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _make_progress(disable: bool) -> Progress:
    """
    Build a rich Progress instance with a consistent column layout.

    Parameters
    ----------
    disable : bool
        When ``True``, the progress bar renders nothing (all output is
        suppressed).

    Returns
    -------
    rich.progress.Progress
        Configured progress instance.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=disable,
    )


def _resolve_config_path(config_path: str | None) -> str:
    """
    Return the effective config file path, falling back to the XDG default.

    Parameters
    ----------
    config_path : str | None
        Path supplied via ``--config``, or ``None`` to use the default.

    Returns
    -------
    str
        Resolved path to an existing config file.

    Raises
    ------
    SystemExit
        Exits with code 1 if no config file can be found.
    """
    if config_path is not None:
        return config_path
    if _DEFAULT_CONFIG.exists():
        return str(_DEFAULT_CONFIG)
    click.echo(
        f"[ERROR] No --config provided and no default config found at {_DEFAULT_CONFIG}.\n"
        f"  Run `tldr-podcast config init` to create one.",
        err=True,
    )
    sys.exit(1)


def _select_topics_interactive(
    default_topics: list[str],
    target_date: date,
    *,
    web_timeout: int,
    web_user_agent: str,
    check_delay_range: tuple[float, float] | None,
) -> list[str]:
    """
    Display an interactive checkbox restricted to topics published on *target_date*.

    Availability is probed with HEAD requests before the prompt so users
    only pick from editions that actually exist for the target date.  The
    probes are throttled with a randomised inter-request delay
    (*check_delay_range*) to avoid ``tldr.tech`` rate-limiting the burst
    and returning false ``404``s.  Falls back to returning *default_topics*
    when ``questionary`` is not available or when stdin is not a TTY.

    Parameters
    ----------
    default_topics : list[str]
        Topic slugs pre-checked in the selector (intersected with availability).
    target_date : date
        Date of the newsletter edition to check.
    web_timeout : int
        HTTP request timeout (seconds) for the availability probe.
    web_user_agent : str
        ``User-Agent`` header sent with the probe.
    check_delay_range : tuple[float, float] or None
        ``(min, max)`` seconds for the randomised pause between successive
        availability probes.  ``None`` or a zero range probes concurrently.

    Returns
    -------
    list[str]
        Selected topic slugs (at least one entry).
    """
    if not sys.stdin.isatty():
        logger.debug("stdin is not a TTY — using default topics.")
        return default_topics or list(SUPPORTED_TOPICS[:3])

    try:
        import questionary  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("questionary not available; using default topics.")
        return default_topics or list(SUPPORTED_TOPICS[:3])

    click.echo(f"Checking TLDR editions published on {target_date.isoformat()}…")
    available = check_availability(
        list(SUPPORTED_TOPICS),
        target_date,
        timeout_seconds=web_timeout,
        user_agent=web_user_agent,
        delay_range=check_delay_range,
    )

    if not available:
        click.echo(
            f"[ERROR] No TLDR edition published on {target_date.isoformat()}. "
            "Try another date with --date YYYY-MM-DD.",
            err=True,
        )
        sys.exit(1)

    choices = [
        questionary.Choice(title=t, checked=(t in default_topics))
        for t in available
    ]
    selected: list[str] | None = questionary.checkbox(
        f"Select topics published on {target_date.isoformat()} "
        f"(space to toggle, enter to confirm):",
        choices=choices,
    ).ask()

    if not selected:
        click.echo("[ERROR] No topics selected. Aborting.", err=True)
        sys.exit(1)

    return selected


def _build_output_stem(topics: list[str], target_date: date) -> str:
    """
    Build the filename stem for a podcast episode.

    Topics are sorted alphabetically and joined with hyphens, followed by
    the ISO date.  For example topics ``["devops", "ai"]`` on 2026-04-17
    produce ``"ai-devops-2026-04-17"``.

    Parameters
    ----------
    topics : list[str]
        Topic slugs used for this run.
    target_date : date
        Date of the newsletter edition.

    Returns
    -------
    str
        Filename stem without extension, e.g. ``"ai-devops-2026-04-17"``.
    """
    sorted_topics = sorted(topics)
    return "-".join(sorted_topics) + "-" + target_date.isoformat()


# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(
    None,
    "--version",
    package_name="tldr-podcast",
    prog_name="tldr-podcast",
)
def cli() -> None:
    """TLDR Newsletter → Podcast: convert newsletters into two-voice MP3 podcasts."""


# ---------------------------------------------------------------------------
# `run` command
# ---------------------------------------------------------------------------

@cli.command("run")
@click.option(
    "-c", "--config",
    "config_path",
    required=False,
    default=None,
    type=click.Path(dir_okay=False),
    help=f"Path to the YAML configuration file. Defaults to {_DEFAULT_CONFIG}.",
)
@click.option(
    "-t", "--topics",
    "topics_str",
    default=None,
    help=(
        "Comma-separated list of TLDR topic slugs to fetch, e.g. 'ai,devops'. "
        f"Supported: {', '.join(SUPPORTED_TOPICS)}."
    ),
)
@click.option(
    "-d", "--date",
    "target_date_str",
    default=None,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Date to process (YYYY-MM-DD). Default: today.",
)
@click.option(
    "-o", "--output-dir",
    "output_dir_override",
    default=None,
    type=click.Path(file_okay=False),
    help="Directory where the podcast file is written. Overrides config output.dir.",
)
@click.option(
    "--no-interactive",
    "no_interactive",
    is_flag=True,
    default=False,
    help="Skip the topic-selection prompt; use --topics or config default_topics.",
)
@click.option(
    "-n", "--dry-run",
    is_flag=True,
    default=False,
    help="Print generated dialogue to stdout instead of synthesising audio.",
)
@click.option(
    "-A", "--no-audio",
    "no_audio",
    is_flag=True,
    default=False,
    help="Generate the dialogue script and report, but skip TTS synthesis and audio export.",
)
@click.option(
    "--no-progress",
    "no_progress",
    is_flag=True,
    default=False,
    help="Disable the rich progress bar.",
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    default=False,
    help="Enable DEBUG-level logging.",
)
@click.option(
    "-r/-R", "--report/--no-report",
    default=True,
    help="Generate a report folder (articles, script, links, overview) alongside the podcast.",
)
def run(
    config_path: str | None,
    topics_str: str | None,
    target_date_str: datetime | None,
    output_dir_override: str | None,
    no_interactive: bool,
    dry_run: bool,
    no_audio: bool,
    no_progress: bool,
    verbose: bool,
    report: bool,
) -> None:
    """Fetch TLDR newsletters from tldr.tech and generate a two-voice podcast MP3."""
    load_dotenv()
    _setup_logging(verbose)

    target_date: date = target_date_str.date() if target_date_str else date.today()

    # ------------------------------------------------------------------
    # 0. Preflight checks
    # ------------------------------------------------------------------
    if not dry_run and not no_audio:
        _check_ffmpeg()

    # ------------------------------------------------------------------
    # 1. Load configuration
    # ------------------------------------------------------------------
    config_path = _resolve_config_path(config_path)
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"[ERROR] Configuration error: {exc}", err=True)
        sys.exit(1)

    web_cfg = cfg.get("web", {})
    gemini_cfg = cfg.get("gemini", {})
    scraping_cfg = cfg.get("scraping", {})
    output_cfg = cfg.get("output", {})
    pricing_cfg: dict = cfg.get("pricing", {})

    max_articles: int = scraping_cfg.get("max_articles", 15)
    scrape_timeout: int = scraping_cfg.get("timeout_seconds", 10)
    cloak_fallback: str = scraping_cfg.get("cloak_fallback", "auto")

    # Output dir: CLI override > config output.dir > cwd
    output_dir: str = (
        output_dir_override
        or output_cfg.get("dir")
        or output_cfg.get("directory")
        or "."
    )
    output_fmt: str = output_cfg.get("format", "mp3")

    speaker1_name: str = gemini_cfg.get("speaker1", {}).get("name", "Alex")
    speaker2_name: str = gemini_cfg.get("speaker2", {}).get("name", "Jordan")

    web_timeout: int = web_cfg.get("timeout_seconds", 15)
    web_user_agent: str = web_cfg.get("user_agent", BROWSER_USER_AGENT)
    default_topics: list[str] = web_cfg.get("default_topics", ["ai", "infosec", "devops"])

    # Randomised pause between successive tldr.tech requests.  Probing all
    # topics back-to-back makes tldr.tech rate-limit the burst and answer
    # 404 for editions that exist; the jitter spreads requests out.  Set
    # both bounds to 0 to disable (restores concurrent, no-delay probing).
    check_delay_min: float = float(web_cfg.get("check_delay_min", 1.0))
    check_delay_max: float = float(web_cfg.get("check_delay_max", 5.0))
    check_delay_range: tuple[float, float] = (check_delay_min, check_delay_max)

    service_tier: str | None = gemini_cfg.get("service_tier") or None
    if service_tier:
        logger.info("Gemini service tier: %s", service_tier)

    tracker = TokenTracker(pricing=pricing_cfg, service_tier=service_tier)

    # ------------------------------------------------------------------
    # 2. Resolve topics
    # ------------------------------------------------------------------
    if topics_str:
        raw_topics = [t.strip() for t in topics_str.split(",") if t.strip()]
        try:
            topics = validate_topics(raw_topics)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}", err=True)
            sys.exit(1)
    elif no_interactive:
        try:
            topics = validate_topics(default_topics)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}", err=True)
            sys.exit(1)
    else:
        topics = _select_topics_interactive(
            default_topics,
            target_date,
            web_timeout=web_timeout,
            web_user_agent=web_user_agent,
            check_delay_range=check_delay_range,
        )

    logger.info("Topics: %s  |  Date: %s", ", ".join(topics), target_date.isoformat())

    # ------------------------------------------------------------------
    # 3. Fetch newsletters from tldr.tech
    # ------------------------------------------------------------------
    logger.info("Fetching TLDR newsletters from tldr.tech…")
    all_articles: list[Article] = fetch_newsletters(
        topics,
        target_date,
        timeout_seconds=web_timeout,
        user_agent=web_user_agent,
        delay_range=check_delay_range,
    )

    if not all_articles:
        click.echo(
            f"No articles found for topics {topics} on {target_date}. "
            "All topics may have been redirected (no edition for this date)."
        )
        sys.exit(1)

    before_dedup = len(all_articles)
    all_articles = _dedup_articles(all_articles)
    removed = before_dedup - len(all_articles)
    if removed:
        logger.info("Deduplication removed %d duplicate article(s).", removed)

    logger.info("%d unique article(s) ready for interest ranking.", len(all_articles))

    # ------------------------------------------------------------------
    # 4. Rank → Scrape → Dialogue → TTS
    # ------------------------------------------------------------------
    with _make_progress(disable=no_progress) as progress:

        # 4a. Interest ranking
        rank_task = progress.add_task(
            f"[cyan]Ranking[/cyan] {len(all_articles)} article(s) by interest…",
            total=1,
        )
        all_articles = rank_articles_by_interest(
            all_articles,
            gemini_cfg,
            token_tracker=tracker,
            progress=progress,
            task_id=rank_task,
        )

        all_articles = all_articles[:max_articles]
        progress.update(
            rank_task,
            description=(
                f"[cyan]Ranked[/cyan]: kept top {len(all_articles)} — {tracker.live_line()}"
            ),
        )

        # 4b. Scraping
        scrape_task = progress.add_task(
            f"[cyan]Scraping[/cyan] {len(all_articles)} article(s)…",
            total=len(all_articles),
        )
        scrape_articles(
            all_articles,
            timeout=scrape_timeout,
            max_articles=max_articles,
            user_agent=web_user_agent,
            cloak_fallback=cloak_fallback,
            progress=progress,
            task_id=scrape_task,
        )

        # 4c. Link extraction (for report and dry-run display)
        link_report = None
        if report:
            link_report = extract_links(all_articles)
            logger.info(
                "Link report: %d link(s) extracted (%d source, %d repo, "
                "%d model, %d paper, %d other).",
                link_report.total,
                len(link_report.sources),
                len(link_report.repos),
                len(link_report.models),
                len(link_report.papers),
                len(link_report.other),
            )

        # 4d. Dialogue generation
        llm_task = progress.add_task("[cyan]Generating dialogue…[/cyan]", total=1)
        chunks = generate_dialogue(
            all_articles,
            gemini_cfg,
            speaker1_name,
            speaker2_name,
            token_tracker=tracker,
            progress=progress,
            task_id=llm_task,
        )
        progress.update(
            llm_task,
            description=f"[cyan]Dialogue[/cyan]: {len(chunks)} chunk(s) — {tracker.live_line()}",
        )

        if dry_run:
            progress.stop()
            click.echo("\n=== Daily Dialogue Preview ===\n")
            for chunk in chunks:
                click.echo(chunk.text)
                click.echo()
            if link_report is not None:
                click.echo("=== Link Report ===\n")
                for heading, items in (
                    ("Source Articles", link_report.sources),
                    ("Repositories", link_report.repos),
                    ("Models", link_report.models),
                    ("Papers", link_report.papers),
                    ("Other Links", link_report.other),
                ):
                    if items:
                        click.echo(f"## {heading}")
                        for lnk in items:
                            click.echo(f"  - [{lnk.label}] {lnk.url}")
                        click.echo()
            sys.exit(0)

        # 4e. TTS synthesis (skipped when --no-audio)
        if not no_audio:
            tts_task = progress.add_task(
                f"[cyan]TTS synthesis[/cyan] ({len(chunks)} chunk(s))…",
                total=len(chunks),
            )
            pcm_chunks = generate_audio_chunks(
                chunks,
                gemini_cfg,
                token_tracker=tracker,
                progress=progress,
                task_id=tts_task,
            )
            progress.console.print(
                f"  [dim]TTS done — {tracker.live_line()}[/dim]"
            )

    # ------------------------------------------------------------------
    # 5. Audio export (skipped when --no-audio)
    # ------------------------------------------------------------------
    stem = _build_output_stem(topics, target_date)
    saved: Path | None = None
    if not no_audio:
        filename = f"{stem}.{output_fmt}"
        out_path = Path(output_dir) / filename

        logger.info("Exporting audio to %s…", out_path)
        saved = export_audio(pcm_chunks, out_path, fmt=output_fmt)
        click.echo(f"Podcast saved to: {saved}")
    else:
        click.echo("Skipping TTS synthesis and audio export (--no-audio).")

    # ------------------------------------------------------------------
    # 6. Report folder
    # ------------------------------------------------------------------
    if link_report is not None:
        report_dir = generate_report(
            articles=all_articles,
            chunks=chunks,
            link_report=link_report,
            output_dir=output_dir,
            timestamp=stem,
            audio_path=saved,
            token_summary=tracker.summary(),
            topics=topics,
            target_date=target_date,
        )
        click.echo(f"Report folder saved to: {report_dir}")

    # ------------------------------------------------------------------
    # 7. Token / cost summary
    # ------------------------------------------------------------------
    click.echo()
    click.echo(tracker.summary())


# ---------------------------------------------------------------------------
# `config` subgroup
# ---------------------------------------------------------------------------

@cli.group("config", context_settings=CONTEXT_SETTINGS)
def config_group() -> None:
    """Manage the tldr-podcast configuration file."""


def _load_raw_config() -> dict:
    """
    Load the raw YAML config without env-var resolution, or return an empty dict.

    Returns
    -------
    dict
        Parsed YAML content, or ``{}`` if the file does not exist.
    """
    if _DEFAULT_CONFIG.exists():
        raw = yaml.safe_load(_DEFAULT_CONFIG.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    return {}


def _prompt(label: str, default: str, **kwargs) -> str:
    """Thin wrapper around ``click.prompt`` that shows the default inline."""
    return click.prompt(label, default=default, **kwargs)


@config_group.command("init")
@click.option(
    "--output",
    "output_path",
    default=str(_DEFAULT_CONFIG),
    show_default=True,
    help="Where to write the config file.",
)
def config_init(output_path: str) -> None:
    """Interactively create or update the configuration file."""
    existing = _load_raw_config()

    def _get(section: str, key: str, fallback: str = "") -> str:
        return str(existing.get(section, {}).get(key, fallback))

    click.echo(f"\nConfiguring tldr-podcast → {output_path}\n")
    click.echo("Press Enter to keep the current value shown in brackets.\n")

    # ── Web source ────────────────────────────────────────────────────────
    click.echo("── Web source ────────────────────────────────────────────────")
    existing_default_topics = existing.get("web", {}).get(
        "default_topics", ["ai", "infosec", "devops"]
    )
    default_topics_str = _prompt(
        f"Default topics (comma-separated, supported: {', '.join(SUPPORTED_TOPICS)})",
        ",".join(existing_default_topics) if isinstance(existing_default_topics, list) else str(existing_default_topics),
    )
    web_user_agent = _prompt(
        "User-Agent header",
        _get("web", "user_agent", BROWSER_USER_AGENT),
    )
    web_timeout = _prompt(
        "HTTP timeout (seconds)",
        _get("web", "timeout_seconds", "15"),
    )
    web_check_delay_min = _prompt(
        "Min delay between tldr.tech checks (seconds, 0 to disable)",
        _get("web", "check_delay_min", "1.0"),
    )
    web_check_delay_max = _prompt(
        "Max delay between tldr.tech checks (seconds, 0 to disable)",
        _get("web", "check_delay_max", "5.0"),
    )

    # ── Gemini ────────────────────────────────────────────────────────────
    click.echo("\n── Gemini ────────────────────────────────────────────────────")
    gemini_key_env  = _prompt(
        "Env var for Gemini API key",
        _get("gemini", "api_key_env", "GEMINI_API_KEY"),
    )
    gemini_text_model = _prompt("Text model",  _get("gemini", "text_model", "gemini-2.0-flash"))
    gemini_tts_model  = _prompt("TTS model",   _get("gemini", "tts_model", "gemini-2.5-flash-preview-tts"))
    gemini_language   = _prompt("Podcast language", _get("gemini", "language", "French"))
    gemini_tier       = _prompt(
        "Service tier (standard/flex/priority, leave empty for default)",
        _get("gemini", "service_tier", ""),
    )

    # ── Speakers ──────────────────────────────────────────────────────────
    click.echo("\n── Speaker 1 ─────────────────────────────────────────────────")
    sp1 = existing.get("gemini", {}).get("speaker1", {}) if isinstance(existing.get("gemini", {}).get("speaker1"), dict) else {}
    sp1_name  = _prompt("Name",        sp1.get("name", "Alex"))
    sp1_voice = _prompt(f"Voice ({', '.join(_GEMINI_VOICES)})", sp1.get("voice", "Puck"))
    sp1_personality = _prompt(
        "Personality",
        sp1.get("personality", "enthusiastic, curious, quick to get excited about tech innovations"),
    )

    click.echo("\n── Speaker 2 ─────────────────────────────────────────────────")
    sp2 = existing.get("gemini", {}).get("speaker2", {}) if isinstance(existing.get("gemini", {}).get("speaker2"), dict) else {}
    sp2_name  = _prompt("Name",        sp2.get("name", "Jordan"))
    sp2_voice = _prompt(f"Voice ({', '.join(_GEMINI_VOICES)})", sp2.get("voice", "Charon"))
    sp2_personality = _prompt(
        "Personality",
        sp2.get("personality", "analytical, mildly skeptical, adds nuance and historical context"),
    )

    # ── Output ────────────────────────────────────────────────────────────
    click.echo("\n── Output ────────────────────────────────────────────────────")
    output_dir = _prompt(
        "Output directory",
        existing.get("output", {}).get("dir") or existing.get("output", {}).get("directory") or ".",
    )
    output_fmt = _prompt("Format (mp3/wav)",  _get("output", "format", "mp3"))

    # ── Build config dict ─────────────────────────────────────────────────
    parsed_default_topics = [t.strip() for t in default_topics_str.split(",") if t.strip()]

    cfg: dict = {
        "web": {
            "default_topics": parsed_default_topics,
            "user_agent": web_user_agent,
            "timeout_seconds": int(web_timeout),
            "check_delay_min": float(web_check_delay_min),
            "check_delay_max": float(web_check_delay_max),
        },
        "gemini": {
            "api_key_env": gemini_key_env,
            "text_model": gemini_text_model,
            "tts_model": gemini_tts_model,
            "language": gemini_language,
            "speaker1": {
                "name": sp1_name,
                "voice": sp1_voice,
                "personality": sp1_personality,
            },
            "speaker2": {
                "name": sp2_name,
                "voice": sp2_voice,
                "personality": sp2_personality,
            },
        },
        "scraping": existing.get("scraping", {"max_articles": 15, "timeout_seconds": 10, "cloak_fallback": "auto"}),
        "output": {
            "dir": output_dir,
            "format": output_fmt,
        },
        "pricing": existing.get("pricing", {}),
    }
    if gemini_tier.strip():
        cfg["gemini"]["service_tier"] = gemini_tier.strip()

    # Preserve pricing block from example config if empty
    if not cfg["pricing"]:
        example = Path(__file__).parent.parent.parent / "config.example.yaml"
        if example.exists():
            example_raw = yaml.safe_load(example.read_text(encoding="utf-8"))
            cfg["pricing"] = example_raw.get("pricing", {})

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    click.echo(f"\nConfiguration written to {dest}")
    click.echo(
        f"\nMake sure this environment variable is set before running:\n"
        f"  export {gemini_key_env}=<your Gemini API key>"
    )


@config_group.command("show")
@click.option(
    "--resolve",
    is_flag=True,
    default=False,
    help="Show resolved values (secrets masked).",
)
def config_show(resolve: bool) -> None:
    """Display the current configuration file."""
    if not _DEFAULT_CONFIG.exists():
        click.echo(
            f"No config file found at {_DEFAULT_CONFIG}.\n"
            "Run `tldr-podcast config init` to create one.",
            err=True,
        )
        sys.exit(1)

    if resolve:
        try:
            cfg = load_config(_DEFAULT_CONFIG)
        except ConfigError as exc:
            click.echo(f"[ERROR] {exc}", err=True)
            sys.exit(1)

        def _mask(data):
            if isinstance(data, dict):
                return {k: ("***" if any(s in k for s in ("key", "password", "token", "secret")) else _mask(v)) for k, v in data.items()}
            if isinstance(data, list):
                return [_mask(i) for i in data]
            return data

        masked = _mask(cfg)
        text = yaml.dump(masked, allow_unicode=True, sort_keys=False, default_flow_style=False)
        console.print(Syntax(text, "yaml", theme="monokai"))
    else:
        text = _DEFAULT_CONFIG.read_text(encoding="utf-8")
        console.print(Syntax(text, "yaml", theme="monokai"))
        click.echo(f"\n{_DEFAULT_CONFIG}")


# ---------------------------------------------------------------------------
# `completions` command
# ---------------------------------------------------------------------------

_COMPLETION_SHELLS = ("bash", "zsh", "fish")


@cli.command("completions")
@click.argument("shell", type=click.Choice(_COMPLETION_SHELLS, case_sensitive=False))
def completions(shell: str) -> None:
    """Print the shell completion script for SHELL (bash, zsh, fish).

    Write the output to the appropriate file and source it from your shell
    profile.  Do not pipe directly into eval.

    \b
    Bash (~/.bashrc):
      mkdir -p ~/.local/share/bash-completion/completions
      tldr-podcast completions bash > ~/.local/share/bash-completion/completions/tldr-podcast
      # bash-completion picks it up automatically on next shell start

    \b
    Zsh (~/.zshrc):
      mkdir -p ~/.zsh/completions
      tldr-podcast completions zsh > ~/.zsh/completions/_tldr-podcast
      # ensure ~/.zshrc contains:
      #   fpath=(~/.zsh/completions $fpath)
      #   autoload -Uz compinit && compinit

    \b
    Fish:
      tldr-podcast completions fish > ~/.config/fish/completions/tldr-podcast.fish
      # fish picks it up automatically on next shell start
    """
    from click.shell_completion import BashComplete, FishComplete, ZshComplete

    cls = {"bash": BashComplete, "zsh": ZshComplete, "fish": FishComplete}[shell.lower()]
    comp = cls(cli, {}, "tldr-podcast", "_TLDR_PODCAST_COMPLETE")
    click.echo(comp.source())


if __name__ == "__main__":
    cli()
