"""
Web scraper module for fetching full article text from URLs.

Uses trafilatura to fetch and extract the main content from web pages.
Articles that fail to scrape fall back to their email summary text.
"""

from __future__ import annotations

import logging
from typing import Protocol

import trafilatura

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


def scrape_article(url: str, timeout: int = 10) -> str | None:
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
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
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
) -> None:
    """
    Scrape full text for a list of articles in-place.

    Iterates over up to ``max_articles`` items and calls :func:`scrape_article`
    for each.  If scraping fails or returns nothing, ``article.full_text`` is
    set to the original ``article.summary`` as a fallback.

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

    Returns
    -------
    None
        Articles are mutated in-place; nothing is returned.
    """
    total = min(len(articles), max_articles)
    logger.info("Scraping up to %d article(s)…", total)

    for i, article in enumerate(articles[:max_articles]):
        logger.debug("Scraping article %d/%d: %s", i + 1, total, article.url)
        scraped = scrape_article(article.url, timeout=timeout)
        article.full_text = scraped if scraped else article.summary

        if scraped:
            logger.debug("Article %d scraped successfully.", i + 1)
        else:
            logger.warning(
                "Article %d scraping failed; falling back to summary.", i + 1
            )
