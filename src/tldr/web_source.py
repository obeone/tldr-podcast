"""
Web source for TLDR newsletters.

Fetches the daily newsletter page for one or more TLDR topics directly
from ``https://tldr.tech/<topic>/<YYYY-MM-DD>`` and parses its HTML into
a list of :class:`~tldr.models.Article` objects.

When a topic does not publish on the requested date, ``tldr.tech``
redirects to the bare topic page (``/<topic>``).  Such redirects are
detected and the topic is silently skipped.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date

import httpx
from bs4 import BeautifulSoup

from tldr.models import Article
from tldr.user_agent import BROWSER_USER_AGENT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Topic slugs accepted on https://tldr.tech.
SUPPORTED_TOPICS: tuple[str, ...] = (
    "ai",
    "infosec",
    "tech",
    "crypto",
    "founders",
    "dev",
    "it",
    "design",
    "product",
    "devops",
    "marketing",
    "data",
    "fintech",
)

_BASE_URL = "https://tldr.tech"
_DEFAULT_USER_AGENT = BROWSER_USER_AGENT
_DEFAULT_TIMEOUT_SECONDS = 15

#: Default ``(min, max)`` seconds for the randomised pause inserted between
#: successive tldr.tech requests.  Probing all 13 topics back-to-back makes
#: ``tldr.tech`` rate-limit the burst and answer ``404`` for editions that
#: actually exist; a jittered delay spreads the requests out and avoids the
#: false negatives.  Set both bounds to ``0`` (or pass ``delay_range=None``)
#: to restore the old concurrent, no-delay behaviour.
_DEFAULT_CHECK_DELAY_RANGE: tuple[float, float] = (1.0, 5.0)

_SPONSOR_HEADER_PATTERNS = ("sponsor", "together with", "promotion")
_SPONSOR_URL_MARKER = "utm_source=tldrnewsletter&utm_medium=sponsor"
_SPONSOR_TITLE_PREFIX = "(sponsor)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate_topics(topics: list[str]) -> list[str]:
    """
    Normalise and validate a list of TLDR topic slugs.

    Lower-cases each entry and rejects any topic not in
    :data:`SUPPORTED_TOPICS`.  Suggestions are emitted in the error
    message via a simple substring match.

    Parameters
    ----------
    topics : list[str]
        Raw topic strings (case-insensitive).

    Returns
    -------
    list[str]
        Normalised list of valid topic slugs in their original order.

    Raises
    ------
    ValueError
        If at least one entry does not match a supported topic.
    """
    normalised: list[str] = []
    unknown: list[str] = []
    for raw in topics:
        slug = raw.strip().lower()
        if not slug:
            continue
        if slug in SUPPORTED_TOPICS:
            normalised.append(slug)
        else:
            unknown.append(slug)
    if unknown:
        suggestions: list[str] = []
        for bad in unknown:
            close = [t for t in SUPPORTED_TOPICS if bad in t or t in bad]
            if close:
                suggestions.append(f"'{bad}' (did you mean: {', '.join(close)}?)")
            else:
                suggestions.append(f"'{bad}'")
        raise ValueError(
            "Unknown topic(s): "
            + ", ".join(suggestions)
            + f". Supported topics: {', '.join(SUPPORTED_TOPICS)}."
        )
    return normalised


def _build_url(topic: str, target_date: date) -> str:
    """Return the canonical newsletter URL for *topic* on *target_date*."""
    return f"{_BASE_URL}/{topic}/{target_date.isoformat()}"


def _is_sponsor_section(name: str) -> bool:
    """Return True when a section header looks like a sponsor block."""
    lowered = name.lower()
    return any(pat in lowered for pat in _SPONSOR_HEADER_PATTERNS)


def _is_sponsor_article(title: str, url: str) -> bool:
    """Return True when an individual article looks like a sponsor entry."""
    if _SPONSOR_URL_MARKER in url:
        return True
    if title.strip().lower().startswith(_SPONSOR_TITLE_PREFIX):
        return True
    return False


def _strip_read_time(title: str) -> str:
    """
    Remove a trailing ``(N minute read)`` annotation from a title.

    The newsletter HTML embeds reading time inside the ``<h3>`` itself, e.g.
    ``"Some Title (3 minute read)"``.  Stripping it keeps article titles
    consistent with what humans expect to see in the report.
    """
    import re

    return re.sub(
        r"\s*\((?:\d+\s+minute\s+read|github\s+repo|resource|video|tool|book)\)\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()


def _normalise_delay_range(
    delay_range: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """
    Coerce a caller-supplied delay range into a sane ``(lo, hi)`` tuple.

    Negative bounds are clamped to ``0`` and a reversed pair is swapped so
    callers cannot accidentally pass ``(2, 1)``.  When the resulting upper
    bound is ``0`` (or *delay_range* is ``None``) the jitter is considered
    disabled and ``None`` is returned, which signals the concurrent,
    no-delay code path.

    Parameters
    ----------
    delay_range : tuple[float, float] or None
        Requested ``(min, max)`` pause in seconds, or ``None`` to disable.

    Returns
    -------
    tuple[float, float] or None
        A normalised ``(lo, hi)`` with ``0 <= lo <= hi`` and ``hi > 0``, or
        ``None`` when delays are disabled.
    """
    if delay_range is None:
        return None
    lo, hi = (float(delay_range[0]), float(delay_range[1]))
    lo = max(0.0, lo)
    hi = max(0.0, hi)
    if hi < lo:
        lo, hi = hi, lo
    if hi <= 0.0:
        return None
    return (lo, hi)


def _jitter_sleep(delay_range: tuple[float, float]) -> None:
    """
    Sleep a random duration uniformly drawn from *delay_range*.

    Parameters
    ----------
    delay_range : tuple[float, float]
        Already-normalised ``(lo, hi)`` bounds in seconds.
    """
    pause = random.uniform(*delay_range)
    logger.debug("Throttling tldr.tech: sleeping %.2fs before next request…", pause)
    time.sleep(pause)


def _throttle(index: int, delay_range: tuple[float, float] | None) -> None:
    """
    Pause before request *index* to spread successive tldr.tech calls out.

    A no-op for the first request (``index == 0``) and whenever throttling
    is disabled (*delay_range* is ``None``).  Centralising both guards here
    keeps every call site in sync — the sequential probe loop and the
    newsletter fetch loop must apply the exact same "between requests,
    never before the first" rule.

    Parameters
    ----------
    index : int
        Zero-based position of the request about to be made.
    delay_range : tuple[float, float] or None
        Already-normalised ``(lo, hi)`` bounds, or ``None`` when disabled.
    """
    if index > 0 and delay_range is not None:
        _jitter_sleep(delay_range)


def check_availability(
    topics: list[str],
    target_date: date,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = _DEFAULT_USER_AGENT,
    delay_range: tuple[float, float] | None = _DEFAULT_CHECK_DELAY_RANGE,
) -> list[str]:
    """
    Return the subset of *topics* that have a published newsletter on *target_date*.

    Each URL is probed with a ``HEAD`` request (``follow_redirects=False``).
    Any 3xx response means ``tldr.tech`` is about to redirect to the bare
    topic page → no edition for this date.  All other successful responses
    (``2xx``) are treated as available.

    By default the probes run **sequentially** with a randomised pause
    between successive requests (see *delay_range*).  Firing all 13 topics
    concurrently makes ``tldr.tech`` rate-limit the burst and answer
    ``404`` for editions that actually exist; the jittered delay spreads
    the requests out and removes those false negatives.  Pass
    ``delay_range=None`` (or a zero range) to restore the old concurrent,
    no-delay behaviour.

    Parameters
    ----------
    topics : list[str]
        Topic slugs to check (must already be valid).
    target_date : date
        Date of the newsletter edition to check.
    timeout_seconds : int, optional
        HTTP request timeout, by default 15.
    user_agent : str, optional
        ``User-Agent`` header to send, by default a browser-like Chrome UA.
    delay_range : tuple[float, float] or None, optional
        ``(min, max)`` seconds for the randomised pause inserted *between*
        successive probes (never before the first or after the last).
        Defaults to :data:`_DEFAULT_CHECK_DELAY_RANGE`.  ``None`` or a zero
        range disables the delay and probes every topic concurrently.

    Returns
    -------
    list[str]
        Topics in the same order as *topics* whose edition exists.
    """
    headers = {"User-Agent": user_agent}
    norm_delay = _normalise_delay_range(delay_range)

    def _probe(topic: str) -> tuple[str, bool]:
        url = _build_url(topic, target_date)
        try:
            with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
                response = client.head(url, follow_redirects=False)
        except httpx.HTTPError as exc:
            logger.debug("Availability probe failed for %s: %s", url, exc)
            return topic, False
        if response.is_redirect or response.status_code >= 400:
            return topic, False
        return topic, True

    if norm_delay is None:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(topics) or 1
        ) as pool:
            results = dict(pool.map(_probe, topics))
    else:
        results: dict[str, bool] = {}
        for index, topic in enumerate(topics):
            _throttle(index, norm_delay)
            probed_topic, available = _probe(topic)
            results[probed_topic] = available

    return [t for t in topics if results.get(t)]


def _fetch_page(
    url: str,
    *,
    timeout_seconds: int,
    user_agent: str,
) -> str | None:
    """
    Fetch a TLDR newsletter page, skipping silently on redirect or HTTP error.

    The TLDR website returns a redirect to the bare topic page
    (``/<topic>``) when the requested date does not exist.  We compare the
    final response URL with the requested URL and treat any mismatch as
    "no newsletter for this date".

    Parameters
    ----------
    url : str
        Newsletter URL to fetch.
    timeout_seconds : int
        HTTP request timeout in seconds.
    user_agent : str
        Value of the ``User-Agent`` request header.

    Returns
    -------
    str or None
        The HTML body when the page exists, ``None`` when the topic was
        silently redirected or the request failed.
    """
    headers = {"User-Agent": user_agent}
    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers) as client:
            response = client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching %s: %s — skipping.", url, exc)
        return None

    if response.status_code >= 400:
        logger.warning(
            "Got HTTP %s for %s — skipping topic.", response.status_code, url
        )
        return None

    final_url = str(response.url).rstrip("/")
    requested = url.rstrip("/")
    if final_url != requested:
        logger.warning(
            "Newsletter URL %s redirected to %s — no edition for this date, skipping.",
            requested,
            final_url,
        )
        return None

    return response.text


def _parse_html(html: str) -> list[Article]:
    """
    Parse a TLDR newsletter HTML page into a list of :class:`Article` objects.

    The page is structured as a sequence of ``<section>`` blocks, each
    introduced by a ``<header>`` containing an
    ``<h3 class="text-center font-bold">`` with the section name.  Inside
    each section, articles are ``<article class="mt-3">`` elements with an
    ``<a><h3>TITLE (N minute read)</h3></a>`` followed by a
    ``<div class="newsletter-html">SUMMARY</div>``.

    Parameters
    ----------
    html : str
        Raw HTML body of a topic page.

    Returns
    -------
    list[Article]
        Articles in document order, sponsor entries excluded.
    """
    soup = BeautifulSoup(html, "lxml")
    articles: list[Article] = []
    current_section = ""

    # Walk every relevant tag once in document order so section context is
    # carried forward correctly across sibling boundaries.
    for tag in soup.find_all(["h3", "article"]):
        if tag.name == "h3":
            classes = tag.get("class") or []
            if "font-bold" in classes and "text-center" in classes:
                current_section = tag.get_text(strip=True)
            continue

        # tag.name == "article"
        classes = tag.get("class") or []
        if "mt-3" not in classes:
            continue

        if _is_sponsor_section(current_section):
            logger.debug("Skipping sponsor section: %s", current_section)
            continue

        link = tag.find("a", href=True)
        heading = tag.find("h3")
        summary_div = tag.find("div", class_="newsletter-html")
        if link is None or heading is None:
            continue

        url = link["href"].strip()
        raw_title = heading.get_text(strip=True)
        title = _strip_read_time(raw_title)
        summary = summary_div.get_text(strip=True) if summary_div else ""

        if not title or not url:
            continue

        if _is_sponsor_article(title, url):
            logger.debug("Skipping sponsor article: %s", title[:60])
            continue

        articles.append(
            Article(
                title=title,
                summary=summary,
                url=url,
                section=current_section,
            )
        )

    logger.info("Parsed %d non-sponsor article(s) from page.", len(articles))
    return articles


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_newsletters(
    topics: list[str],
    target_date: date,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    user_agent: str = _DEFAULT_USER_AGENT,
    delay_range: tuple[float, float] | None = _DEFAULT_CHECK_DELAY_RANGE,
) -> list[Article]:
    """
    Fetch TLDR newsletters for each topic on *target_date* and merge them.

    Topics that do not publish on the given date (``tldr.tech`` redirects
    to the bare topic page) are skipped silently with a warning log.
    Articles that appear in multiple newsletters are deduplicated by URL,
    keeping the first occurrence.  A randomised pause is inserted between
    successive topic requests (see *delay_range*) so the sequential fetch
    does not hammer ``tldr.tech`` right after the availability probe.

    Parameters
    ----------
    topics : list[str]
        Topic slugs to fetch.  Must be members of :data:`SUPPORTED_TOPICS`;
        callers should validate via :func:`validate_topics` first.
    target_date : date
        Date of the newsletter edition to fetch.
    timeout_seconds : int, optional
        HTTP request timeout, by default 15.
    user_agent : str, optional
        ``User-Agent`` header to send, by default a browser-like Chrome UA.
    delay_range : tuple[float, float] or None, optional
        ``(min, max)`` seconds for the randomised pause inserted *between*
        successive topic requests (never before the first or after the
        last).  Defaults to :data:`_DEFAULT_CHECK_DELAY_RANGE`.  ``None``
        or a zero range disables the delay.

    Returns
    -------
    list[Article]
        Deduplicated articles across all successfully fetched topics.
        Empty when no topic has an edition for *target_date*.
    """
    seen_urls: set[str] = set()
    merged: list[Article] = []
    norm_delay = _normalise_delay_range(delay_range)

    for index, topic in enumerate(topics):
        _throttle(index, norm_delay)
        url = _build_url(topic, target_date)
        logger.info("Fetching TLDR %s for %s…", topic, target_date.isoformat())
        html = _fetch_page(
            url, timeout_seconds=timeout_seconds, user_agent=user_agent
        )
        if html is None:
            continue

        for article in _parse_html(html):
            if article.url in seen_urls:
                logger.debug(
                    "Skipping duplicate article across topics: %s", article.url
                )
                continue
            seen_urls.add(article.url)
            merged.append(article)

    logger.info(
        "Fetched %d unique article(s) from %d topic(s).", len(merged), len(topics)
    )
    return merged
