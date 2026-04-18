# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`tldr-podcast` fetches TLDR newsletters directly from `tldr.tech` and converts them into two-voice podcast MP3 files using Google Gemini for both dialogue generation and TTS synthesis.

## Commands

```bash
# Install as a tool
uv tool install .

# Run the full pipeline with an interactive topic picker (default: today)
tldr-podcast run

# Pick topics explicitly, skip the prompt
tldr-podcast run --topics ai,devops --no-interactive

# Target a specific date
tldr-podcast run -d 2026-04-06

# Dry-run (no TTS, prints the dialogue script)
tldr-podcast run -t infosec --no-interactive --dry-run

# Dev mode (editable install)
uv sync && uv pip install -e .

# Run tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_web_source.py -v

# Run a single test by name
uv run pytest tests/test_web_source.py::TestFetchNewsletters::test_happy_path_parses_fixture -v

# Run with coverage
uv run pytest tests/ --cov=src/tldr

# Generate shell completions (bash/zsh/fish) — write to file, do not eval
mkdir -p ~/.local/share/bash-completion/completions
tldr-podcast completions bash > ~/.local/share/bash-completion/completions/tldr-podcast

mkdir -p ~/.zsh/completions
tldr-podcast completions zsh > ~/.zsh/completions/_tldr-podcast
# add to ~/.zshrc: fpath=(~/.zsh/completions $fpath); autoload -Uz compinit && compinit

tldr-podcast completions fish > ~/.config/fish/completions/tldr-podcast.fish
```

## Architecture

Pipeline (left to right):

```
web_source (topics, date) → interest_ranking → web_scraper → llm_dialogue → tts_generator → audio_exporter → MP3
```

**`src/tldr/` modules:**

- `config.py` — Loads `config.yaml` via `load_config()`. Keys ending in `_env` are replaced at runtime by the named environment variable value (e.g. `api_key_env: GEMINI_API_KEY` becomes `api_key: <value of $GEMINI_API_KEY>`).
- `models.py` — Shared dataclasses (`Article`) consumed by every pipeline stage.
- `web_source.py` — Fetches `https://tldr.tech/<topic>/<YYYY-MM-DD>` for each requested topic, parses the HTML with BeautifulSoup, skips redirected URLs silently (no edition for that date), dedupes articles across topics, and filters sponsor/promo sections. Returns `list[Article]`.
- `web_scraper.py` — Fetches full article text via trafilatura; falls back to the newsletter summary if scraping fails. Populates `Article.full_text`.
- `llm_summarizer.py` — Two-stage LLM module: (1) `rank_articles_by_interest()` scores articles 1–10 by title+summary to select the most interesting before scraping; (2) `generate_dialogue()` produces a two-host dialogue script from full article text, split into `DialogueChunk` objects bounded to ≤3000 UTF-8 bytes each.
- `tts_generator.py` — Calls Gemini multi-speaker TTS for each `DialogueChunk`; returns raw PCM bytes (24 kHz, mono, 16-bit LE).
- `audio_exporter.py` — Concatenates PCM chunks and encodes to MP3 or WAV via pydub + ffmpeg.
- `report_generator.py` — Writes `overview.md`, `articles.md`, `script.md`, `summary.md` in a folder named after the podcast stem.
- `link_extractor.py`, `token_tracker.py`, `retry.py` — Supporting helpers.

**`cli.py`** — Click CLI entry point (group with `run`, `config`, and `completions` subcommands); orchestrates the full pipeline. Installed as the `tldr-podcast` command via `[project.scripts]`. All commands support `-h` for help. Short flags: `-c` config, `-t` topics, `-d` date, `-o` output-dir, `-n` dry-run, `-v` verbose, `-r`/`-R` report/no-report. Report generation is enabled by default.

Topic selection precedence: `--topics ai,devops` CLI arg > interactive `questionary.checkbox()` prompt (pre-checked from `web.default_topics`) > config `web.default_topics` (when `--no-interactive` is set).

Supported topic slugs: `ai`, `infosec`, `tech`, `crypto`, `founders`, `dev`, `it`, `design`, `product`, `devops`, `marketing`, `data`, `fintech`.

Output filename is `<topic1>-<topic2>-…-<YYYY-MM-DD>.<fmt>` (topics sorted alphabetically); the report folder uses the same stem. Output directory defaults to the current working directory, overridable by `--output-dir` and `output.dir` in the config.

## Key Data Types

- `Article` (dataclass in `models.py`): `title`, `summary`, `url`, `section`, `full_text`, `interest_score`
- `DialogueChunk` (dataclass in `llm_summarizer.py`): `text`, `index`

## Configuration

Default config path is `$XDG_CONFIG_HOME/tldr/config.yaml` (falls back to `~/.config/tldr/config.yaml`). Run `tldr-podcast config init` to create it interactively. Secrets are never stored directly — use `_env`-suffixed keys that reference environment variable names. Required env var: `GEMINI_API_KEY`.

## Testing

All tests use mocks for external APIs (Gemini, HTTP). The TLDR HTML fixture under `tests/fixtures/` is a real captured page used by `test_web_source.py`. See `uv run pytest tests/ -q` for the current count.

## Versioning

Every feature, fix, or behavioural change **must** bump the version in `pyproject.toml` before committing. Follow [SemVer](https://semver.org/):

- **patch** (`1.0.0` → `1.0.1`): bug fixes, minor tweaks
- **minor** (`1.0.0` → `1.1.0`): new features, prompt changes, new CLI options
- **major** (`1.0.0` → `2.0.0`): breaking changes (config format, CLI interface, output format)

## Dependencies

Requires `ffmpeg` installed on the system for MP3 encoding (not a Python package).
