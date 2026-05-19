"""
Web scraper module for fetching full article text from URLs.

Uses trafilatura to fetch and extract the main content from web pages.
Articles that fail to scrape fall back to their email summary text.
Scraping is performed in parallel using a thread pool to reduce total
wall-clock time.  When trafilatura fails, an optional CloakBrowser
stealth-Chromium fallback can re-render the page to bypass bot detection.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Protocol

import trafilatura
from trafilatura.settings import use_config

from tldr.user_agent import BROWSER_USER_AGENT

if TYPE_CHECKING:
    from rich.progress import Progress

logger = logging.getLogger(__name__)

# Launching many stealth Chromium instances in parallel exhausts memory.
# trafilatura stays at up to 10 workers; only the (subset) failures that
# reach the browser fallback are bounded here to 2 concurrent instances.
_CLOAK_MAX_CONCURRENCY = 2
_CLOAK_SEMAPHORE = threading.BoundedSemaphore(_CLOAK_MAX_CONCURRENCY)

# Maximum seconds to wait for a Cloudflare challenge to auto-resolve.
# This is a bounded budget separate from the per-article HTTP timeout.
_CLOAK_CHALLENGE_WAIT_S = 35

# HTML substrings that indicate the page is still showing a Cloudflare
# challenge interstitial rather than the real article content.
_CLOAK_CHALLENGE_MARKERS = ("challenge-platform", "/cdn-cgi/chl", "turnstile", "just a moment")


class ArticleLike(Protocol):
    """
    Minimal protocol for objects that can be scraped.

    Attributes
    ----------
    url : str
        The URL to fetch full text from.
    summary : str
        Fallback text if scraping fails.
    full_text : str
        Destination field for the scraped content.
    """

    url: str
    summary: str
    full_text: str


def _cloak_available() -> bool:
    """
    Check whether the ``cloakbrowser`` package is importable.

    Returns
    -------
    bool
        ``True`` if ``cloakbrowser`` is installed and importable,
        ``False`` otherwise.
    """
    return importlib.util.find_spec("cloakbrowser") is not None


def _resolve_use_cloak(cloak_fallback: str) -> bool:
    """
    Resolve the ``cloak_fallback`` config value to a boolean.

    Parameters
    ----------
    cloak_fallback : str
        One of ``"auto"``, ``"on"``, or ``"off"`` (case-insensitive).
        Any unrecognised value is treated as ``"auto"``.

    Returns
    -------
    bool
        ``True`` when the CloakBrowser fallback should be used,
        ``False`` otherwise.
    """
    mode = (cloak_fallback or "auto").strip().lower()

    if mode == "off":
        return False

    available = _cloak_available()

    if mode == "on":
        if not available:
            logger.warning(
                "cloak_fallback=on but the 'cloakbrowser' package is not installed; "
                "install the optional extra with: pip install \"tldr-podcast[cloak]\". "
                "Falling back to newsletter summaries on scrape failure."
            )
            return False
        return True

    # treat anything else (including "auto") as auto
    if not available:
        logger.debug(
            "cloak_fallback=auto: 'cloakbrowser' not installed; browser fallback disabled."
        )
    return available


def _cloak_safe_content(page: Any) -> str:
    """
    Retrieve the current HTML content of a CloakBrowser page, with retries.

    Cloudflare reloads the page during challenge resolution, which can cause
    Playwright to raise "Page.content: Unable to retrieve content because the
    page is navigating".  This helper retries up to 12 times, sleeping 0.5 s
    between attempts, and returns ``""`` if it never succeeds.  Never raises.

    Parameters
    ----------
    page : Any
        A CloakBrowser / Playwright page object exposing a ``content()``
        method.

    Returns
    -------
    str
        The page's current HTML source, or ``""`` if all attempts fail.
    """
    for _ in range(12):
        try:
            return page.content()
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return ""


def _scrape_with_cloak(url: str, timeout: int = 10) -> str | None:
    """
    Attempt to scrape a URL using the CloakBrowser stealth browser.

    Acquires a concurrency semaphore before launching Chromium to prevent
    memory exhaustion.  Waits for any Cloudflare challenge interstitial to
    resolve before reading the page content.  Never raises; returns ``None``
    on any failure.

    Note: CloakBrowser manages its own stealth fingerprint, so no
    custom User-Agent is passed — overriding it would defeat the stealth.

    Parameters
    ----------
    url : str
        The URL to render and extract text from.
    timeout : int, optional
        Navigation timeout in seconds, by default 10.  The goto call uses
        ``max(timeout, 30)`` seconds to allow for Cloudflare JS execution.

    Returns
    -------
    str | None
        The extracted article text, or ``None`` if rendering or
        extraction failed.
    """
    with _CLOAK_SEMAPHORE:
        browser = None
        try:
            try:
                import cloakbrowser  # noqa: PLC0415
            except ImportError:
                logger.debug("CloakBrowser not importable; skipping browser fallback.")
                return None

            logger.info("Trying CloakBrowser browser fallback for: %s", url)
            # humanize=True enables human-like interaction timing, which is
            # required for Cloudflare Turnstile challenge resolution.
            browser = cloakbrowser.launch(headless=True, humanize=True)
            page = browser.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=max(timeout, 30) * 1000)
            except Exception as goto_exc:  # noqa: BLE001
                logger.warning(
                    "CloakBrowser goto failed for %s: %s", url, goto_exc
                )
                return None

            # Wait for any Cloudflare challenge to resolve before reading content.
            deadline = time.time() + _CLOAK_CHALLENGE_WAIT_S
            html = ""
            while time.time() < deadline:
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:  # noqa: BLE001
                    pass
                html = _cloak_safe_content(page)
                low = html.lower()
                if html and not any(m in low for m in _CLOAK_CHALLENGE_MARKERS):
                    break
                time.sleep(1.5)

            # One final read to grab the freshest content after the loop.
            html = _cloak_safe_content(page)

            text = trafilatura.extract(html, favor_recall=True)
            if not text:
                logger.warning(
                    "CloakBrowser fallback: extraction returned nothing for %s", url
                )
                return None

            logger.info(
                "CloakBrowser fallback scraped %d chars from: %s", len(text), url
            )
            return text

        except Exception as exc:  # noqa: BLE001
            logger.warning("CloakBrowser fallback failed for %s: %s", url, exc)
            return None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass


def scrape_article(
    url: str,
    timeout: int = 10,
    user_agent: str = BROWSER_USER_AGENT,
    use_cloak: bool = False,
) -> str | None:
    """
    Fetch and extract the main text content from a URL.

    Uses trafilatura to download the page and extract the article body.
    When trafilatura fails and ``use_cloak`` is ``True``, falls back to
    :func:`_scrape_with_cloak` which renders the page via a stealth
    Chromium browser.  Never raises; returns ``None`` on any failure.

    Parameters
    ----------
    url : str
        The URL of the article to scrape.
    timeout : int, optional
        HTTP request timeout in seconds, by default 10.
    user_agent : str, optional
        ``User-Agent`` header to send with the article request, by default
        a browser-like Chrome UA.
    use_cloak : bool, optional
        When ``True`` and trafilatura fails, attempt a CloakBrowser
        stealth-browser fallback, by default ``False``.

    Returns
    -------
    str | None
        The extracted article text, or ``None`` if fetching or extraction
        failed (and the cloak fallback also failed or was not enabled).

    Examples
    --------
    >>> text = scrape_article("https://example.com/article")
    >>> text is not None
    True
    """
    try:
        logger.debug("Fetching URL: %s", url)
        cfg = use_config()
        cfg.set("DEFAULT", "USER_AGENTS", user_agent)
        cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(timeout))
        downloaded = trafilatura.fetch_url(url, no_ssl=True, config=cfg)
        if downloaded is None:
            logger.warning("trafilatura.fetch_url returned None for URL: %s", url)
            if use_cloak:
                return _scrape_with_cloak(url, timeout)
            return None

        text = trafilatura.extract(downloaded)
        if text is None:
            logger.warning("trafilatura.extract returned None for URL: %s", url)
            if use_cloak:
                return _scrape_with_cloak(url, timeout)
            return None

        logger.info("Successfully scraped %d chars from: %s", len(text), url)
        return text

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to scrape URL %s: %s", url, exc)
        if use_cloak:
            return _scrape_with_cloak(url, timeout)
        return None


def scrape_articles(
    articles: list,
    timeout: int = 10,
    max_articles: int = 15,
    user_agent: str = BROWSER_USER_AGENT,
    cloak_fallback: str = "auto",
    progress: Progress | None = None,
    task_id: Any = None,
) -> None:
    """
    Scrape full text for a list of articles in-place, using a thread pool.

    Up to ``max_articles`` articles are scraped concurrently (at most 10
    workers).  If scraping fails or returns nothing, ``article.full_text``
    is set to the original ``article.summary`` as a fallback.

    When trafilatura fails and the CloakBrowser fallback is enabled (via
    ``cloak_fallback``), up to :data:`_CLOAK_MAX_CONCURRENCY` concurrent
    stealth-browser sessions are launched to re-render the page.

    Parameters
    ----------
    articles : list
        A list of article-like objects exposing ``url``, ``summary``, and
        ``full_text`` attributes.
    timeout : int, optional
        HTTP request timeout in seconds passed to :func:`scrape_article`,
        by default 10.
    max_articles : int, optional
        Maximum number of articles to attempt scraping, by default 15.
    user_agent : str, optional
        ``User-Agent`` header to send with article requests, by default a
        browser-like Chrome UA.
    cloak_fallback : str, optional
        Controls the CloakBrowser stealth-browser fallback.  One of:

        - ``"auto"`` (default): use it when the ``cloakbrowser`` package
          is importable.
        - ``"on"``: require it; warns and degrades gracefully if not
          installed.
        - ``"off"``: never use the browser fallback.
    progress : rich.progress.Progress or None, optional
        A rich :class:`~rich.progress.Progress` instance.  When provided,
        ``task_id`` must also be supplied and will be advanced once per
        completed article.
    task_id : Any, optional
        Task identifier returned by ``progress.add_task()``.

    Returns
    -------
    None
        Articles are mutated in-place; nothing is returned.
    """
    to_scrape = articles[:max_articles]
    total = len(to_scrape)

    if not to_scrape:
        logger.info("No articles to scrape.")
        return

    use_cloak = _resolve_use_cloak(cloak_fallback)
    if use_cloak:
        logger.info("CloakBrowser browser fallback is ENABLED for failed scrapes.")

    max_workers = min(10, total)
    logger.info("Scraping %d article(s) with up to %d worker(s)…", total, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_article = {
            executor.submit(scrape_article, article.url, timeout, user_agent, use_cloak): article
            for article in to_scrape
        }
        for future in as_completed(future_to_article):
            article = future_to_article[future]
            scraped = future.result()  # never raises — scrape_article handles all exceptions
            article.full_text = scraped if scraped else article.summary

            if scraped:
                logger.debug("Scraped successfully: %s", article.url)
            else:
                logger.warning("Scraping failed; using email summary: %s", article.url)

            if progress is not None and task_id is not None:
                progress.advance(task_id)

    logger.info("Scraping complete (%d article(s) processed).", total)
