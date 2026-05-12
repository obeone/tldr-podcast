# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/tldr/`. The main entry point is
`src/tldr/cli.py`, with focused modules for config loading, scraping,
summarization, TTS, audio export, reporting, and retry logic. Tests live in
`tests/`, with fixture HTML in `tests/fixtures/`. Project-level files include
`pyproject.toml`, `uv.lock`, `README.md`, and `config.example.yaml`.

## Build, Test, and Development Commands

Use `uv` for all local development.

- `uv sync` installs runtime and dev dependencies in the project environment.
- `uv run pytest` runs the full test suite.
- `uv run pytest tests/test_config.py -v` runs one test module verbosely.
- `uv run tldr-podcast --version` checks the installed CLI entry point.
- `uv run tldr-podcast run -t ai --no-interactive -n` exercises the flow
  without generating audio.

`ffmpeg` is required for audio export and should be installed separately.

## Coding Style & Naming Conventions

Target Python 3.13+ and keep type hints on all public signatures. Follow the
existing module split: one responsibility per file, small focused functions,
and descriptive snake_case names for modules, functions, and variables.
Classes use PascalCase. Keep imports tidy and prefer explicit dependencies over
shared globals. Use `coloredlogs` for new logging paths.

## Testing Guidelines

Tests use `pytest` with files named `test_<area>.py`. Add a regression test for
every bug fix and cover the main happy path plus failure modes for new
features. Prefer realistic integration-style tests when behavior spans scraping,
LLM orchestration, or reporting. Mock external APIs and network calls; the
suite already relies on captured HTML fixtures instead of live services.

## Commit & Pull Request Guidelines

Recent history follows Conventional Commits such as
`feat(cli): add --version flag` and `chore(deps): sync uv.lock to 1.5.0`.
Keep commits atomic and scoped to one logical change. Commit lockfile updates
with the dependency change that requires them. PRs should state the user-facing
change, note config or dependency impacts, and include command output for tests
run locally. Every PR must include an appropriate project version bump.

## Repository Hosting & Forgejo Tools

This repository is hosted on Forgejo. Use the `fj` skill for Forgejo
operations such as pull requests, issues, releases, tags, and workflows. Do
not use GitHub tooling for this repository.

## Configuration & Security Tips

Do not commit secrets. Runtime credentials such as `GEMINI_API_KEY` should come
from environment variables, while configuration stays in YAML files based on
`config.example.yaml`.
