"""
TLDR Newsletter → Podcast CLI.

Usage
-----
    python main.py --config config.yaml --eml path/to/newsletter.eml --dry-run
    python main.py --config config.yaml  # fetches unread emails via IMAP

Options
-------
--config    Path to the YAML configuration file (required).
--eml       Path to a local .eml file (skips IMAP fetch when provided).
--dry-run   Print the generated dialogue to stdout instead of calling TTS.
--verbose   Enable DEBUG-level logging.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import click
import coloredlogs
from dotenv import load_dotenv
from email import message_from_bytes
from email.utils import parsedate_to_datetime

from tldr.audio_exporter import export_audio
from tldr.config import ConfigError, load_config
from tldr.email_parser import ParseError, parse_emails
from tldr.imap_client import IMAPError, fetch_unread_emails, move_emails_to_folder
from tldr.llm_summarizer import generate_dialogue
from tldr.tts_generator import generate_audio_chunks
from tldr.web_scraper import scrape_articles

logger = logging.getLogger(__name__)


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


@click.command()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the YAML configuration file.",
)
@click.option(
    "--eml",
    "eml_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Path to a local .eml file (skips IMAP fetch).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print generated dialogue to stdout instead of synthesising audio.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable DEBUG-level logging.",
)
def main(
    config_path: str,
    eml_path: str | None,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Convert today's TLDR newsletter emails into a single two-voice podcast MP3."""
    load_dotenv()
    _setup_logging(verbose)

    # ------------------------------------------------------------------
    # 1. Load configuration
    # ------------------------------------------------------------------
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"[ERROR] Configuration error: {exc}", err=True)
        sys.exit(1)

    imap_cfg = cfg.get("imap", {})
    gemini_cfg = cfg.get("gemini", {})
    scraping_cfg = cfg.get("scraping", {})
    output_cfg = cfg.get("output", {})

    max_articles: int = scraping_cfg.get("max_articles", 15)
    scrape_timeout: int = scraping_cfg.get("timeout_seconds", 10)
    output_dir: str = output_cfg.get("directory", "output")
    output_fmt: str = output_cfg.get("format", "mp3")

    speaker1_name: str = gemini_cfg.get("speaker1", {}).get("name", "Alex")
    speaker2_name: str = gemini_cfg.get("speaker2", {}).get("name", "Jordan")

    seen_folder: str = imap_cfg.get("seen_folder", "TLDR/Seen")

    # ------------------------------------------------------------------
    # 2. Fetch email(s)
    # ------------------------------------------------------------------
    # imap_message_ids holds the server-side IDs needed to move messages later.
    # It remains empty when a local .eml file is used (no IMAP involved).
    imap_message_ids: list[int] = []
    email_data: list[tuple[int, bytes]] = []

    if eml_path:
        logger.info("Reading local .eml file: %s", eml_path)
        # Assign a placeholder ID of 0 — it is never used for moving.
        email_data = [(0, Path(eml_path).read_bytes())]
    else:
        logger.info("Fetching unread emails via IMAP…")
        try:
            email_data = fetch_unread_emails(imap_cfg, target_date=date.today())
        except IMAPError as exc:
            click.echo(f"[ERROR] IMAP error: {exc}", err=True)
            sys.exit(1)

        if not email_data:
            click.echo("No unread TLDR emails found for today. Nothing to do.")
            sys.exit(0)

        imap_message_ids = [msg_id for msg_id, _ in email_data]

    # ------------------------------------------------------------------
    # 3. Parse all emails → merge → deduplicate
    # ------------------------------------------------------------------
    email_data = _sort_emails_by_date(email_data)
    all_articles: list = []

    for i, (_, raw) in enumerate(email_data, start=1):
        logger.info("Parsing email %d/%d…", i, len(raw_emails))
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
    logger.info("%d unique article(s) ready for processing.", len(all_articles))

    # ------------------------------------------------------------------
    # 4. Scrape + generate dialogue
    # ------------------------------------------------------------------
    logger.info("Scraping full text…")
    scrape_articles(all_articles, timeout=scrape_timeout, max_articles=max_articles)

    logger.info("Generating dialogue via Gemini…")
    chunks = generate_dialogue(all_articles, gemini_cfg, speaker1_name, speaker2_name)

    if dry_run:
        click.echo("\n=== Daily Dialogue Preview ===\n")
        for chunk in chunks:
            click.echo(chunk.text)
            click.echo()
        sys.exit(0)

    # ------------------------------------------------------------------
    # 5. TTS → audio export
    # ------------------------------------------------------------------
    logger.info("Generating TTS audio for %d chunk(s)…", len(chunks))
    pcm_chunks = generate_audio_chunks(chunks, gemini_cfg)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"tldr_{timestamp}.{output_fmt}"
    out_path = Path(output_dir) / filename

    logger.info("Exporting audio to %s…", out_path)
    saved = export_audio(pcm_chunks, out_path, fmt=output_fmt)
    click.echo(f"Podcast saved to: {saved}")

    # ------------------------------------------------------------------
    # 6. Move processed emails to the "seen" folder
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


if __name__ == "__main__":
    main()
