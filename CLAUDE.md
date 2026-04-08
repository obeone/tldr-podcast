# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`tldr-podcast` converts TLDR newsletter emails into two-voice podcast MP3 files using Google Gemini for both dialogue generation and TTS synthesis.

## Commands

```bash
# Install as a tool
uv tool install .

# Run the CLI (local .eml file, dry-run)
tldr-podcast run --eml "mails/newsletter.eml" --dry-run

# Run the full pipeline (IMAP fetch)
tldr-podcast run

# Dev mode (editable install)
uv sync && uv pip install -e .

# Run tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_email_parser.py -v

# Run a single test by name
uv run pytest tests/test_email_parser.py::test_parse_emails_basic -v

# Run with coverage
uv run pytest tests/ --cov=src/tldr
```

## Architecture

Pipeline (left to right):

```
IMAP / .eml → email_parser → web_scraper → llm_summarizer → tts_generator → audio_exporter → MP3
```

**`src/tldr/` modules:**

- `config.py` — Loads `config.yaml` via `load_config()`. Keys ending in `_env` are replaced at runtime by the named environment variable value (e.g. `api_key_env: GEMINI_API_KEY` becomes `api_key: <value of $GEMINI_API_KEY>`).
- `imap_client.py` — Fetches unread emails over IMAP SSL; returns `list[bytes]`.
- `email_parser.py` — Parses raw MIME bytes into `Article` dataclasses. Strips sponsor sections (`TOGETHER WITH`, `SPONSOR`, etc.) using regex. Resolves article URLs from the "Links:" footer block.
- `web_scraper.py` — Fetches full article text via trafilatura; falls back to the email summary if scraping fails. Populates `Article.full_text`.
- `llm_summarizer.py` — Sends articles to Gemini Flash; receives a two-host dialogue script. Splits output into `DialogueChunk` objects bounded to ≤3800 UTF-8 bytes each (Gemini TTS API limit is ~4000 bytes).
- `tts_generator.py` — Calls Gemini multi-speaker TTS for each `DialogueChunk`; returns raw PCM bytes (24 kHz, mono, 16-bit LE).
- `audio_exporter.py` — Concatenates PCM chunks and encodes to MP3 or WAV via pydub + ffmpeg.

**`cli.py`** — Click CLI entry point (group with `run` and `config` subcommands); orchestrates the full pipeline. Installed as the `tldr-podcast` command via `[project.scripts]`.

## Key Data Types

- `Article` (dataclass in `email_parser.py`): `title`, `summary`, `url`, `section`, `full_text`
- `DialogueChunk` (dataclass in `llm_summarizer.py`): `text`, `index`

## Configuration

Default config path is `~/.config/tldr/config.yaml`. Run `tldr-podcast config init` to create it interactively. Secrets are never stored directly — use `_env`-suffixed keys that reference environment variable names. Required env vars: `GEMINI_API_KEY`, `IMAP_PASSWORD`.

## Testing

All tests use mocks for external APIs (Gemini, IMAP, HTTP). Sample `.eml` files in `mails/` are used by integration-style tests for the parser. 156 unit tests across 13 files.

## Dependencies

Requires `ffmpeg` installed on the system for MP3 encoding (not a Python package).
