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
import sys
from datetime import datetime
from pathlib import Path

import click
import coloredlogs
from dotenv import load_dotenv

from tldr.audio_exporter import export_audio
from tldr.config import ConfigError, load_config
from tldr.email_parser import ParseError, parse_emails
from tldr.imap_client import IMAPError, fetch_unread_emails
from tldr.llm_summarizer import generate_dialogue
from tldr.tts_generator import generate_audio_chunks
from tldr.web_scraper import scrape_articles

logger = logging.getLogger(__name__)


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
    """Convert a TLDR newsletter email into a two-voice podcast MP3."""
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

    # ------------------------------------------------------------------
    # 2. Fetch email(s)
    # ------------------------------------------------------------------
    raw_emails: list[bytes] = []

    if eml_path:
        logger.info("Reading local .eml file: %s", eml_path)
        raw_emails = [Path(eml_path).read_bytes()]
    else:
        logger.info("Fetching unread emails via IMAP…")
        try:
            raw_emails = fetch_unread_emails(imap_cfg)
        except IMAPError as exc:
            click.echo(f"[ERROR] IMAP error: {exc}", err=True)
            sys.exit(1)

        if not raw_emails:
            click.echo("No unread TLDR emails found. Nothing to do.")
            sys.exit(0)

    # ------------------------------------------------------------------
    # 3. Parse + scrape + generate dialogue for each email
    # ------------------------------------------------------------------
    for i, raw in enumerate(raw_emails, start=1):
        logger.info("Processing email %d/%d…", i, len(raw_emails))

        try:
            articles = parse_emails(raw)
        except ParseError as exc:
            click.echo(f"[ERROR] Failed to parse email {i}: {exc}", err=True)
            continue

        if not articles:
            logger.warning("No articles extracted from email %d — skipping.", i)
            continue

        logger.info("%d articles extracted. Scraping full text…", len(articles))
        scrape_articles(articles, timeout=scrape_timeout, max_articles=max_articles)

        logger.info("Generating dialogue via Gemini…")
        chunks = generate_dialogue(articles, gemini_cfg, speaker1_name, speaker2_name)

        if dry_run:
            click.echo(f"\n=== Email {i}: Dialogue Preview ===\n")
            for chunk in chunks:
                click.echo(chunk.text)
                click.echo()
            continue

        # --------------------------------------------------------------
        # 4. TTS → audio export
        # --------------------------------------------------------------
        logger.info("Generating TTS audio for %d chunk(s)…", len(chunks))
        pcm_chunks = generate_audio_chunks(chunks, gemini_cfg)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename = f"tldr_{timestamp}.{output_fmt}"
        out_path = Path(output_dir) / filename

        logger.info("Exporting audio to %s…", out_path)
        saved = export_audio(pcm_chunks, out_path, fmt=output_fmt)
        click.echo(f"Podcast saved to: {saved}")


if __name__ == "__main__":
    main()
