"""
Web scraper module for fetching full article text from URLs.

Uses trafilatura to fetch and extract the main content from web pages.
Articles that fail to scrape fall back to their email summary text.
Scraping is performed in parallel using a thread pool to reduce total
wall-clock time.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Protocol

import trafilatura
from trafilatura.settings import use_config

from tldr.user_agent import BROWSER_USER_AGENT

if TYPE_CHECKING:
    from rich.progress import Progress

logger = logging.getLogger(__name__)


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


def scrape_article(
    url: str,
    timeout: int = 10,
    user_agent: str = BROWSER_USER_AGENT,
) -> str | None:
    """
    Fetch and extract the main text content from a URL.

    Uses trafilatura to download the page and extract the article body.
    Never raises; returns ``None`` on any failure.

    Parameters
    ----------
    url : str
        The URL of the article to scrape.
    timeout : int, optional
        HTTP request timeout in seconds, by default 10.
    user_agent : str, optional
        ``User-Agent`` header to send with the article request, by default
        a browser-like Chrome UA.

    Returns
    -------
    str | None
        The extracted article text, or ``None`` if fetching or extraction
        failed.

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
            return None

        text = trafilatura.extract(downloaded)
        if text is None:
            logger.warning("trafilatura.extract returned None for URL: %s", url)
            return None

        logger.info("Successfully scraped %d chars from: %s", len(text), url)
        return text

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to scrape URL %s: %s", url, exc)
        return None


def scrape_articles(
    articles: list,
    timeout: int = 10,
    max_articles: int = 15,
    user_agent: str = BROWSER_USER_AGENT,
    progress: Progress | None = None,
    task_id: Any = None,
) -> None:
    """
    Scrape full text for a list of articles in-place, using a thread pool.

    Up to ``max_articles`` articles are scraped concurrently (at most 10
    workers).  If scraping fails or returns nothing, ``article.full_text``
    is set to the original ``article.summary`` as a fallback.

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

    max_workers = min(10, total)
    logger.info("Scraping %d article(s) with up to %d worker(s)…", total, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_article = {
            executor.submit(scrape_article, article.url, timeout, user_agent): article
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
