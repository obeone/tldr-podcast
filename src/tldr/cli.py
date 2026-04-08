"""
TLDR Newsletter → Podcast CLI.

Commands
--------
    tldr-podcast run [OPTIONS]      Run the full pipeline.
    tldr-podcast config init        Interactive configuration wizard.
    tldr-podcast config show        Display the current configuration file.

Run options
-----------
--config       Path to the YAML configuration file (default: ~/.config/tldr/config.yaml).
--eml          Path to a local .eml file (skips IMAP fetch when provided).
--date         Date to process in YYYY-MM-DD format (default: today).
--dry-run      Print the generated dialogue to stdout instead of calling TTS.
--no-progress  Disable the rich progress bar.
--verbose      Enable DEBUG-level logging.
--no-report    Disable the report folder generated alongside the podcast.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import coloredlogs
import yaml
from dotenv import load_dotenv
from email import message_from_bytes
from email.utils import parsedate_to_datetime
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
from tldr.email_parser import ParseError, parse_emails
from tldr.imap_client import IMAPError, fetch_unread_emails, move_emails_to_folder
from tldr.link_extractor import extract_links
from tldr.llm_summarizer import generate_dialogue, rank_articles_by_interest, summarize_articles
from tldr.report_generator import generate_report
from tldr.token_tracker import TokenTracker
from tldr.tts_generator import generate_audio_chunks
from tldr.web_scraper import scrape_articles

logger = logging.getLogger(__name__)

console = Console(stderr=False)

_DEFAULT_CONFIG = Path.home() / ".config" / "tldr" / "config.yaml"

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


def _sort_emails_by_date(
    email_data: list[tuple[int, bytes]],
) -> list[tuple[int, bytes]]:
    """
    Return *email_data* sorted ascending by the ``Date:`` header of each message.

    Entries whose ``Date:`` header cannot be parsed are placed last.

    Parameters
    ----------
    email_data : list[tuple[int, bytes]]
        List of ``(message_id, raw_bytes)`` pairs as returned by
        :func:`~tldr.imap_client.fetch_unread_emails`.

    Returns
    -------
    list[tuple[int, bytes]]
        Sorted copy (ascending by send date).
    """
    def _key(item: tuple[int, bytes]):
        _, raw = item
        try:
            msg = message_from_bytes(raw)
            return parsedate_to_datetime(msg["Date"])
        except Exception:
            return datetime.max.replace(tzinfo=timezone.utc)

    return sorted(email_data, key=_key)


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


# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
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
    "-e", "--eml",
    "eml_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to a local .eml file (skips IMAP fetch).",
)
@click.option(
    "-d", "--date",
    "target_date_str",
    default=None,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Date to process (YYYY-MM-DD). Ignored when --eml is used. Default: today.",
)
@click.option(
    "-n", "--dry-run",
    is_flag=True,
    default=False,
    help="Print generated dialogue to stdout instead of synthesising audio.",
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
    eml_path: str | None,
    target_date_str: datetime | None,
    dry_run: bool,
    no_progress: bool,
    verbose: bool,
    report: bool,
) -> None:
    """Convert TLDR newsletter emails into a single two-voice podcast MP3."""
    load_dotenv()
    _setup_logging(verbose)

    target_date: date = target_date_str.date() if target_date_str else date.today()

    # ------------------------------------------------------------------
    # 0. Preflight checks
    # ------------------------------------------------------------------
    if not dry_run:
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

    imap_cfg = cfg.get("imap", {})
    gemini_cfg = cfg.get("gemini", {})
    scraping_cfg = cfg.get("scraping", {})
    output_cfg = cfg.get("output", {})
    pricing_cfg: dict = cfg.get("pricing", {})

    max_articles: int = scraping_cfg.get("max_articles", 15)
    scrape_timeout: int = scraping_cfg.get("timeout_seconds", 10)
    output_dir: str = output_cfg.get("directory", "output")
    output_fmt: str = output_cfg.get("format", "mp3")

    speaker1_name: str = gemini_cfg.get("speaker1", {}).get("name", "Alex")
    speaker2_name: str = gemini_cfg.get("speaker2", {}).get("name", "Jordan")

    seen_folder: str = imap_cfg.get("seen_folder", "TLDR/Seen")

    service_tier: str | None = gemini_cfg.get("service_tier") or None
    if service_tier:
        logger.info("Gemini service tier: %s", service_tier)

    tracker = TokenTracker(pricing=pricing_cfg, service_tier=service_tier)

    # ------------------------------------------------------------------
    # 2. Fetch email(s)
    # ------------------------------------------------------------------
    imap_message_ids: list[int] = []
    email_data: list[tuple[int, bytes]] = []

    if eml_path:
        logger.info("Reading local .eml file: %s", eml_path)
        email_data = [(0, Path(eml_path).read_bytes())]
    else:
        logger.info("Fetching unread emails for %s via IMAP…", target_date)
        try:
            email_data = fetch_unread_emails(imap_cfg, target_date=target_date)
        except IMAPError as exc:
            click.echo(f"[ERROR] IMAP error: {exc}", err=True)
            sys.exit(1)

        if not email_data:
            click.echo(
                f"No unread TLDR emails found for {target_date}. Nothing to do."
            )
            sys.exit(0)

        imap_message_ids = [msg_id for msg_id, _ in email_data]

    # ------------------------------------------------------------------
    # 3. Parse all emails → merge → deduplicate
    # ------------------------------------------------------------------
    email_data = _sort_emails_by_date(email_data)
    all_articles: list = []

    for i, (_, raw) in enumerate(email_data, start=1):
        logger.info("Parsing email %d/%d…", i, len(email_data))
        try:
            articles = parse_emails(raw)
        except ParseError as exc:
            click.echo(f"[ERROR] Failed to parse email {i}: {exc}", err=True)
            continue
        logger.info("Email %d: %d article(s) extracted.", i, len(articles))
        all_articles.extend(articles)

    if not all_articles:
        click.echo("No articles extracted from today's emails. Nothing to do.")
        sys.exit(0)

    before_dedup = len(all_articles)
    all_articles = _dedup_articles(all_articles)
    removed = before_dedup - len(all_articles)
    if removed:
        logger.info("Deduplication removed %d duplicate article(s).", removed)

    logger.info("%d unique article(s) ready for interest ranking.", len(all_articles))

    # ------------------------------------------------------------------
    # 4. Rank → Scrape → Summarize → Dialogue → TTS
    # ------------------------------------------------------------------
    with _make_progress(disable=no_progress) as progress:

        # 4a. Interest ranking (sorts by apparent interest)
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

        # Keep only the top max_articles most interesting.
        all_articles = all_articles[:max_articles]
        progress.update(
            rank_task,
            description=(
                f"[cyan]Ranked[/cyan]: kept top {len(all_articles)} — {tracker.live_line()}"
            ),
        )

        # 4b. Scraping (only the interesting articles)
        scrape_task = progress.add_task(
            f"[cyan]Scraping[/cyan] {len(all_articles)} article(s)…",
            total=len(all_articles),
        )
        scrape_articles(
            all_articles,
            timeout=scrape_timeout,
            max_articles=max_articles,
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

        # 4d. Per-article summarization (optional, when summary_model is configured)
        if gemini_cfg.get("summary_model") and gemini_cfg["summary_model"] != gemini_cfg.get("text_model"):
            summary_task = progress.add_task(
                f"[cyan]Summarizing[/cyan] {len(all_articles)} article(s)…",
                total=len(all_articles),
            )
            all_articles = summarize_articles(
                all_articles,
                gemini_cfg,
                token_tracker=tracker,
                progress=progress,
                task_id=summary_task,
            )
            progress.update(
                summary_task,
                description=f"[cyan]Summarized[/cyan] — {tracker.live_line()}",
            )

        # 4e. Dialogue generation
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

        # 4f. TTS synthesis
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
    # 5. Audio export
    # ------------------------------------------------------------------
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"tldr_{timestamp}.{output_fmt}"
    out_path = Path(output_dir) / filename

    logger.info("Exporting audio to %s…", out_path)
    saved = export_audio(pcm_chunks, out_path, fmt=output_fmt)
    click.echo(f"Podcast saved to: {saved}")

    # ------------------------------------------------------------------
    # 6. Report folder (enabled by default, disable with --no-report)
    # ------------------------------------------------------------------
    if link_report is not None:
        report_dir = generate_report(
            articles=all_articles,
            chunks=chunks,
            link_report=link_report,
            output_dir=output_dir,
            timestamp=timestamp,
            audio_path=saved,
            token_summary=tracker.summary(),
            email_count=len(email_data),
            target_date=target_date,
        )
        click.echo(f"Report folder saved to: {report_dir}")

    # ------------------------------------------------------------------
    # 7. Token / cost summary
    # ------------------------------------------------------------------
    click.echo()
    click.echo(tracker.summary())

    # ------------------------------------------------------------------
    # 8. Move processed emails to the "seen" folder
    # ------------------------------------------------------------------
    if imap_message_ids:
        logger.info(
            "Moving %d processed email(s) to %s…",
            len(imap_message_ids),
            seen_folder,
        )
        try:
            move_emails_to_folder(imap_cfg, imap_message_ids, seen_folder)
            logger.info("Emails moved to %s.", seen_folder)
        except IMAPError as exc:
            logger.warning(
                "Could not move emails to %s: %s — continuing anyway.",
                seen_folder,
                exc,
            )


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

    # ── IMAP ─────────────────────────────────────────────────────────────
    click.echo("── IMAP ──────────────────────────────────────────────────────")
    imap_host     = _prompt("IMAP host",                _get("imap", "host", "imap.gmail.com"))
    imap_port     = _prompt("IMAP port",                _get("imap", "port", "993"))
    imap_username = _prompt("IMAP username (email)",    _get("imap", "username"))
    imap_pass_env = _prompt(
        "Env var for IMAP password",
        _get("imap", "password_env", "IMAP_PASSWORD"),
    )
    imap_folder      = _prompt("IMAP folder to watch",  _get("imap", "folder", "INBOX"))
    imap_seen_folder = _prompt("Folder for processed emails", _get("imap", "seen_folder", "TLDR/Seen"))

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
    sp1_name  = _prompt("Name",        _get("gemini", "speaker1", {}).get("name", "Alex") if isinstance(_get("gemini", "speaker1", {}), dict) else "Alex")
    sp1_voice = _prompt(f"Voice ({', '.join(_GEMINI_VOICES)})", _get("gemini", "speaker1", {}).get("voice", "Puck") if isinstance(_get("gemini", "speaker1", {}), dict) else "Puck")
    sp1_personality = _prompt(
        "Personality",
        existing.get("gemini", {}).get("speaker1", {}).get("personality", "enthusiastic, curious, quick to get excited about tech innovations") if isinstance(existing.get("gemini", {}).get("speaker1"), dict) else "enthusiastic, curious, quick to get excited about tech innovations",
    )

    click.echo("\n── Speaker 2 ─────────────────────────────────────────────────")
    sp2_name  = _prompt("Name",        existing.get("gemini", {}).get("speaker2", {}).get("name", "Jordan") if isinstance(existing.get("gemini", {}).get("speaker2"), dict) else "Jordan")
    sp2_voice = _prompt(f"Voice ({', '.join(_GEMINI_VOICES)})", existing.get("gemini", {}).get("speaker2", {}).get("voice", "Charon") if isinstance(existing.get("gemini", {}).get("speaker2"), dict) else "Charon")
    sp2_personality = _prompt(
        "Personality",
        existing.get("gemini", {}).get("speaker2", {}).get("personality", "analytical, mildly skeptical, adds nuance and historical context") if isinstance(existing.get("gemini", {}).get("speaker2"), dict) else "analytical, mildly skeptical, adds nuance and historical context",
    )

    # ── Output ────────────────────────────────────────────────────────────
    click.echo("\n── Output ────────────────────────────────────────────────────")
    output_dir = _prompt("Output directory", _get("output", "directory", "output"))
    output_fmt = _prompt("Format (mp3/wav)",  _get("output", "format", "mp3"))

    # ── Build config dict ─────────────────────────────────────────────────
    cfg: dict = {
        "imap": {
            "host": imap_host,
            "port": int(imap_port),
            "username": imap_username,
            "password_env": imap_pass_env,
            "folder": imap_folder,
            "seen_folder": imap_seen_folder,
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
        "scraping": existing.get("scraping", {"max_articles": 15, "timeout_seconds": 10}),
        "output": {
            "directory": output_dir,
            "format": output_fmt,
        },
        "pricing": existing.get("pricing", {}),
    }
    if gemini_tier.strip():
        cfg["gemini"]["service_tier"] = gemini_tier.strip()

    # Preserve pricing block from existing config or example if empty
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

    # Remind the user to export the env vars
    click.echo(
        f"\nMake sure these environment variables are set before running:\n"
        f"  export {imap_pass_env}=<your IMAP password>\n"
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


if __name__ == "__main__":
    cli()
