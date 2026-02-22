# Daily Email Filtering and Deduplication — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fetch only today's TLDR emails via IMAP, merge their articles into one
deduplicated list, and produce a single daily podcast.

**Architecture:** Add a `target_date` parameter to `fetch_unread_emails` that
translates to IMAP `SENTSINCE`/`BEFORE` criteria. In `main.py`, replace the
per-email loop with a merge → dedup → single pipeline that normalises article
titles (lowercased, collapsed whitespace) as deduplication keys.

**Tech Stack:** Python 3.13, imapclient, standard `email` module, pytest + unittest.mock.

---

### Task 1: Add `target_date` filtering to `imap_client.py`

**Files:**
- Modify: `src/tldr/imap_client.py`
- Test: `tests/test_imap_client.py`

**Step 1: Write the failing tests**

Add to `tests/test_imap_client.py`:

```python
from datetime import date
from unittest.mock import call

class TestFetchUnreadEmailsDateFilter:
    """Tests for the target_date filtering behaviour."""

    def test_uses_sentsince_and_before_when_target_date_given(self):
        """fetch_unread_emails passes SENTSINCE/BEFORE criteria when target_date is set."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        target = date(2026, 2, 20)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            fetch_unread_emails(SAMPLE_CFG, target_date=target)

        mock_client.search.assert_called_once_with(
            ["UNSEEN", "SENTSINCE", "20-Feb-2026", "BEFORE", "21-Feb-2026"]
        )

    def test_defaults_to_today_when_no_target_date(self):
        """fetch_unread_emails defaults target_date to date.today()."""
        raw_map = {1: {b"RFC822": RAW_EMAIL_1}}
        mock_client = _make_mock_client([1], raw_map)
        today = date(2026, 2, 22)
        tomorrow = date(2026, 2, 23)

        with patch("tldr.imap_client.IMAPClient", return_value=mock_client):
            with patch("tldr.imap_client.date") as mock_date:
                mock_date.today.return_value = today
                mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
                fetch_unread_emails(SAMPLE_CFG)

        mock_client.search.assert_called_once_with(
            ["UNSEEN", "SENTSINCE", "22-Feb-2026", "BEFORE", "23-Feb-2026"]
        )
```

**Step 2: Run tests to verify they fail**

```bash
cd .worktrees/feat/daily-filter-dedup
uv run pytest tests/test_imap_client.py::TestFetchUnreadEmailsDateFilter -v
```

Expected: FAIL — `fetch_unread_emails` does not accept `target_date` yet.

**Step 3: Implement the change**

Replace the imports block and `fetch_unread_emails` signature in
`src/tldr/imap_client.py`:

```python
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import coloredlogs
from imapclient import IMAPClient
```

Replace the `fetch_unread_emails` signature and the search call:

```python
def fetch_unread_emails(
    imap_cfg: dict[str, Any],
    target_date: date | None = None,
) -> list[bytes]:
    """
    Connect to an IMAP server and fetch the raw bytes of all unread messages
    sent on *target_date* (defaults to today).

    ...existing docstring body...

    Parameters
    ----------
    imap_cfg : dict[str, Any]
        ...existing...
    target_date : date or None, optional
        The calendar day to filter by (send date). Defaults to ``date.today()``.
        Uses IMAP ``SENTSINCE``/``BEFORE`` criteria (RFC 3501 date format).

    ...rest of docstring unchanged...
    """
    if target_date is None:
        target_date = date.today()

    since_str = target_date.strftime("%d-%b-%Y")
    before_str = (target_date + timedelta(days=1)).strftime("%d-%b-%Y")

    # ... existing connection boilerplate ...

    message_ids = client.search(
        ["UNSEEN", "SENTSINCE", since_str, "BEFORE", before_str]
    )
```

> The rest of the function body (fetch, build result list) is unchanged.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_imap_client.py -v
```

Expected: all tests PASS including the two new ones.

**Step 5: Commit**

```bash
git add src/tldr/imap_client.py tests/test_imap_client.py
git commit --no-gpg-sign -m "feat(imap): filter emails by send date using SENTSINCE/BEFORE"
```

---

### Task 2: Add `_dedup_articles` helper and its tests

**Files:**
- Modify: `main.py`
- No separate module needed — the helper lives in `main.py`

> Write and test the helper in isolation before wiring it into the pipeline.

**Step 1: Write the failing test**

Create `tests/test_main_dedup.py`:

```python
"""Tests for the _dedup_articles helper in main.py."""
from __future__ import annotations

import sys
import importlib
import pytest

# Import the private helper directly from main module
import main as main_module
from tldr.email_parser import Article


def _make_article(title: str, url: str = "https://example.com") -> Article:
    return Article(title=title, summary="s", url=url, section="SEC", full_text="")


class TestDedupArticles:
    """Tests for _dedup_articles."""

    def test_keeps_all_articles_when_no_duplicates(self):
        articles = [
            _make_article("FIRST ARTICLE"),
            _make_article("SECOND ARTICLE"),
        ]
        result = main_module._dedup_articles(articles)
        assert len(result) == 2

    def test_removes_exact_duplicate_title(self):
        articles = [
            _make_article("SAME TITLE", "https://a.com"),
            _make_article("SAME TITLE", "https://b.com"),
        ]
        result = main_module._dedup_articles(articles)
        assert len(result) == 1
        assert result[0].url == "https://a.com"  # first wins

    def test_dedup_is_case_insensitive(self):
        articles = [
            _make_article("How Will Openai Compete?"),
            _make_article("HOW WILL OPENAI COMPETE?"),
        ]
        result = main_module._dedup_articles(articles)
        assert len(result) == 1

    def test_dedup_ignores_extra_whitespace(self):
        articles = [
            _make_article("TITLE  WITH  SPACES"),
            _make_article("TITLE WITH SPACES"),
        ]
        result = main_module._dedup_articles(articles)
        assert len(result) == 1

    def test_preserves_order_of_first_occurrences(self):
        articles = [
            _make_article("ALPHA"),
            _make_article("BETA"),
            _make_article("ALPHA"),  # dup of first
            _make_article("GAMMA"),
        ]
        result = main_module._dedup_articles(articles)
        assert [a.title for a in result] == ["ALPHA", "BETA", "GAMMA"]

    def test_empty_list_returns_empty(self):
        assert main_module._dedup_articles([]) == []
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_main_dedup.py -v
```

Expected: FAIL — `_dedup_articles` does not exist in `main.py` yet.

**Step 3: Implement `_dedup_articles` in `main.py`**

Add after the imports, before `_setup_logging`:

```python
import re as _re

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
        key = _re.sub(r"\s+", " ", article.title.lower().strip())
        if key not in seen:
            seen.add(key)
            result.append(article)
    return result
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_main_dedup.py -v
```

Expected: all 6 tests PASS.

**Step 5: Commit**

```bash
git add main.py tests/test_main_dedup.py
git commit --no-gpg-sign -m "feat(main): add _dedup_articles helper with title normalisation"
```

---

### Task 3: Rework `main.py` pipeline — merge + dedup + single podcast

**Files:**
- Modify: `main.py`

**Step 1: Understand what changes**

Current flow (inside `main()`):
```
for raw in raw_emails:
    articles = parse_emails(raw)
    scrape → dialogue → TTS → export  # one MP3 per email
```

New flow:
```
sort raw_emails by Date: header (ascending)
all_articles = []
for raw in raw_emails:
    all_articles.extend(parse_emails(raw))
all_articles = _dedup_articles(all_articles)
scrape → dialogue → TTS → export      # one MP3 for the day
```

**Step 2: Add a helper to sort raw emails by date header**

Add `_sort_emails_by_date` in `main.py` (next to `_dedup_articles`):

```python
from email import message_from_bytes
from email.utils import parsedate_to_datetime

def _sort_emails_by_date(raw_emails: list[bytes]) -> list[bytes]:
    """
    Return *raw_emails* sorted ascending by their ``Date:`` header.

    Emails whose ``Date:`` header cannot be parsed are placed last.

    Parameters
    ----------
    raw_emails : list[bytes]
        Raw RFC 822 email bytes.

    Returns
    -------
    list[bytes]
        Sorted copy (ascending by send date).
    """
    def _key(raw: bytes):
        try:
            msg = message_from_bytes(raw)
            return parsedate_to_datetime(msg["Date"])
        except Exception:
            return datetime.max.replace(tzinfo=None)

    return sorted(raw_emails, key=_key)
```

> `datetime` is already imported in `main.py`.

**Step 3: Replace the per-email loop**

Find this block in `main()` (around line 130):

```python
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
```

Replace it with:

```python
    # ------------------------------------------------------------------
    # 3. Parse all emails → merge → deduplicate
    # ------------------------------------------------------------------
    raw_emails = _sort_emails_by_date(raw_emails)
    all_articles: list = []

    for i, raw in enumerate(raw_emails, start=1):
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
```

**Step 4: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all existing tests PASS (no regressions). `test_main_dedup.py` also PASS.

**Step 5: Smoke-test with the example emails (dry-run)**

```bash
# Multiple .eml files to simulate multi-email day
uv run python main.py --config config.yaml \
  --eml "mails/Gemini 3.1 Pro 🤖, OpenAI's strategic issues 💡, building AI eng culture 👨‍💻.eml" \
  --dry-run 2>/dev/null | head -30
```

> Note: `--eml` only supports a single file. The multi-email path is only
> exercised via IMAP. Verify visually that the dialogue output looks correct.

**Step 6: Commit**

```bash
git add main.py
git commit --no-gpg-sign -m "feat(main): merge daily emails into single deduplicated podcast"
```

---

### Task 4: Update `main.py` IMAP call to pass `target_date`

**Files:**
- Modify: `main.py`

**Step 1: Update the IMAP fetch call**

Find in `main()`:

```python
        raw_emails = fetch_unread_emails(imap_cfg)
```

Replace with:

```python
        from datetime import date as _date
        raw_emails = fetch_unread_emails(imap_cfg, target_date=_date.today())
```

> The import at the top of `main.py` already imports `datetime`; add
> `from datetime import date` to the top-level imports instead of inline.

**Step 2: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS.

**Step 3: Commit**

```bash
git add main.py
git commit --no-gpg-sign -m "feat(main): pass today's date to IMAP fetch for daily filtering"
```

---

### Task 5: Update docstrings and log messages

**Files:**
- Modify: `src/tldr/imap_client.py` (docstring update — `target_date` already added in Task 1)
- Modify: `main.py` (update CLI docstring / help text)

**Step 1: Update CLI docstring in `main()`**

Change:

```python
    """Convert a TLDR newsletter email into a two-voice podcast MP3."""
```

To:

```python
    """Convert today's TLDR newsletter emails into a single two-voice podcast MP3."""
```

**Step 2: Update the "no emails" log message in `main()`**

Find:

```python
        click.echo("No unread TLDR emails found. Nothing to do.")
```

Replace with:

```python
        click.echo("No unread TLDR emails found for today. Nothing to do.")
```

**Step 3: Run full test suite one final time**

```bash
uv run pytest tests/ -v
```

Expected: all tests PASS.

**Step 4: Commit**

```bash
git add main.py src/tldr/imap_client.py
git commit --no-gpg-sign -m "docs: update docstrings and messages for daily filtering behaviour"
```
