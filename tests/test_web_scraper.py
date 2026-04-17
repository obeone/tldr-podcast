"""
Tests for the web_scraper module.

Verifies article scraping behaviour with mocked trafilatura calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch


from tldr.web_scraper import scrape_article, scrape_articles


# ---------------------------------------------------------------------------
# Minimal stub that satisfies the ArticleLike protocol
# ---------------------------------------------------------------------------


@dataclass
class FakeArticle:
    """Lightweight stand-in for a real Article object."""

    url: str
    summary: str
    full_text: str = ""


# ---------------------------------------------------------------------------
# scrape_article tests
# ---------------------------------------------------------------------------


class TestScrapeArticle:
    """Unit tests for scrape_article()."""

    def test_returns_text_on_success(self):
        """scrape_article returns extracted text when trafilatura succeeds."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>") as mock_fetch,
            patch("tldr.web_scraper.trafilatura.extract", return_value="Article body text") as mock_extract,
        ):
            result = scrape_article("https://example.com/article")

        assert result == "Article body text"
        mock_fetch.assert_called_once_with("https://example.com/article", no_ssl=True)
        mock_extract.assert_called_once_with("<html>")

    def test_returns_none_when_fetch_url_returns_none(self):
        """scrape_article returns None when trafilatura.fetch_url returns None."""
        with patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None):
            result = scrape_article("https://example.com/article")

        assert result is None

    def test_returns_none_when_fetch_url_raises(self):
        """scrape_article returns None (does not raise) when fetch_url throws."""
        with patch(
            "tldr.web_scraper.trafilatura.fetch_url",
            side_effect=RuntimeError("network error"),
        ):
            result = scrape_article("https://example.com/article")

        assert result is None

    def test_returns_none_when_extract_returns_none(self):
        """scrape_article returns None when trafilatura.extract returns None."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value=None),
        ):
            result = scrape_article("https://example.com/article")

        assert result is None


# ---------------------------------------------------------------------------
# scrape_articles tests
# ---------------------------------------------------------------------------


class TestScrapeArticles:
    """Unit tests for scrape_articles()."""

    def test_sets_full_text_on_success(self):
        """scrape_articles populates full_text from scraped content."""
        articles = [FakeArticle(url="https://a.com", summary="Summary A")]

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value="Full text A"),
        ):
            scrape_articles(articles)

        assert articles[0].full_text == "Full text A"

    def test_falls_back_to_summary_on_failure(self):
        """scrape_articles falls back to article.summary when scraping fails."""
        articles = [FakeArticle(url="https://a.com", summary="Fallback summary")]

        with patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None):
            scrape_articles(articles)

        assert articles[0].full_text == "Fallback summary"

    def test_respects_max_articles_limit(self):
        """scrape_articles processes at most max_articles items."""
        articles = [
            FakeArticle(url=f"https://example.com/{i}", summary=f"Summary {i}")
            for i in range(5)
        ]

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value="Text") as mock_extract,
        ):
            scrape_articles(articles, max_articles=2)

        # Only the first 2 articles should have been scraped
        assert mock_extract.call_count == 2
        assert articles[0].full_text == "Text"
        assert articles[1].full_text == "Text"
        # Articles beyond the limit remain untouched
        assert articles[2].full_text == ""
        assert articles[3].full_text == ""
        assert articles[4].full_text == ""
