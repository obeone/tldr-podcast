# Design: Daily email filtering and article deduplication

Date: 2026-02-22

## Problem

The IMAP client currently fetches **all unread emails** with no date constraint.
When multiple TLDR newsletter editions arrive on the same day (e.g. TLDR main,
TLDR AI, TLDR DevOps), the same article can appear in more than one email and
end up repeated in the generated podcast.

## Goals

- Fetch only emails sent **today** (system date at execution time).
- Merge articles from all daily emails into a single deduplicated list.
- Generate **one podcast per day** from that merged list.

## Approach

### 1. IMAP-side date filtering (`imap_client.py`)

`fetch_unread_emails` gains an optional `target_date: date | None = None`
parameter. When `None`, defaults to `date.today()`.

The IMAP search criteria become:

```python
["UNSEEN", "SENTSINCE", since_str, "BEFORE", before_str]
```

where both strings use the RFC 3501 date format `%d-%b-%Y`
(e.g. `"22-Feb-2026"`), and `before_str` is `target_date + timedelta(days=1)`.

This filters on the `Date:` header of the message (send date), which is
reliable for TLDR newsletters.

### 2. Article deduplication (`main.py`)

After parsing all emails, articles from every email are concatenated into a
single flat list sorted by email send date (ascending, deterministic).
A deduplication pass removes articles whose title has already been seen,
using a normalised key:

```python
key = re.sub(r"\s+", " ", article.title.lower().strip())
```

First occurrence wins; later duplicates are dropped.

### 3. Single daily podcast pipeline (`main.py`)

The current per-email loop is replaced by:

1. Parse all raw emails → sort by `Date:` header → collect all articles.
2. Deduplicate the merged article list.
3. Scrape full text for deduplicated articles.
4. Generate dialogue (one Gemini call for the full day).
5. TTS → export one MP3, timestamped with today's date.

## Files changed

| File | Change |
|---|---|
| `src/tldr/imap_client.py` | Add `target_date` parameter; build SENTSINCE/BEFORE search criteria |
| `main.py` | Replace per-email loop with merge → dedup → single pipeline |

## Out of scope

- CLI `--date` flag for historical replay (can be added later trivially).
- Deduplication across different days.
