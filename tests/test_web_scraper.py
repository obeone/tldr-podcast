"""
Tests for the web_scraper module.

Verifies article scraping behaviour with mocked trafilatura calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import create_autospec, patch

import trafilatura

from tldr.web_scraper import scrape_article, scrape_articles

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


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

    def test_returns_text_on_success(self) -> None:
        """scrape_article returns extracted text when trafilatura succeeds."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>") as mock_fetch,
            patch("tldr.web_scraper.trafilatura.extract", return_value="Article body text") as mock_extract,
        ):
            result = scrape_article("https://example.com/article")

        assert result == "Article body text"
        args, kwargs = mock_fetch.call_args
        assert args == ("https://example.com/article",)
        assert kwargs["no_ssl"] is True
        assert "headers" not in kwargs
        assert kwargs["config"].get("DEFAULT", "USER_AGENTS") == _BROWSER_USER_AGENT
        mock_extract.assert_called_once_with("<html>")

    def test_fetch_url_uses_browser_user_agent_by_default(self) -> None:
        """scrape_article sends a browser-like User-Agent when downloading."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>") as mock_fetch,
            patch("tldr.web_scraper.trafilatura.extract", return_value="Article body text"),
        ):
            scrape_article("https://example.com/article")

        args, kwargs = mock_fetch.call_args
        assert args == ("https://example.com/article",)
        assert kwargs["no_ssl"] is True
        assert "headers" not in kwargs
        assert kwargs["config"].get("DEFAULT", "USER_AGENTS") == _BROWSER_USER_AGENT

    def test_returns_none_when_fetch_url_returns_none(self) -> None:
        """scrape_article returns None when trafilatura.fetch_url returns None."""
        with patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None):
            result = scrape_article("https://example.com/article")

        assert result is None

    def test_returns_none_when_fetch_url_raises(self) -> None:
        """scrape_article returns None (does not raise) when fetch_url throws."""
        with patch(
            "tldr.web_scraper.trafilatura.fetch_url",
            side_effect=RuntimeError("network error"),
        ):
            result = scrape_article("https://example.com/article")

        assert result is None

    def test_returns_none_when_extract_returns_none(self) -> None:
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

    def test_sets_full_text_on_success(self) -> None:
        """scrape_articles populates full_text from scraped content."""
        articles = [FakeArticle(url="https://a.com", summary="Summary A")]

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value="Full text A"),
        ):
            scrape_articles(articles)

        assert articles[0].full_text == "Full text A"

    def test_falls_back_to_summary_on_failure(self) -> None:
        """scrape_articles falls back to article.summary when scraping fails."""
        articles = [FakeArticle(url="https://a.com", summary="Fallback summary")]

        with patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None):
            scrape_articles(articles)

        assert articles[0].full_text == "Fallback summary"

    def test_passes_custom_user_agent_to_article_scraper(self) -> None:
        """scrape_articles forwards the configured UA to each article fetch."""
        articles = [FakeArticle(url="https://a.com", summary="Summary A")]

        with patch("tldr.web_scraper.scrape_article", return_value="Full text") as mock_scrape:
            scrape_articles(articles, user_agent="custom-client/7.0")

        mock_scrape.assert_called_once_with("https://a.com", 10, "custom-client/7.0")
        assert articles[0].full_text == "Full text"

    def test_respects_max_articles_limit(self) -> None:
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


class TestScrapeArticleTrafilaturaApi:
    """Regression tests pinning the call to the real trafilatura API.

    These would fail against the old code that passed an unsupported
    ``headers=`` kwarg to ``trafilatura.fetch_url``.
    """

    def test_fetch_url_called_with_supported_signature(self) -> None:
        """scrape_article must only pass kwargs the installed
        trafilatura.fetch_url actually accepts (autospec enforces the
        real signature, so a stray ``headers`` kwarg raises TypeError)."""
        autospec_fetch = create_autospec(trafilatura.fetch_url, return_value="<html>")
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", autospec_fetch),
            patch("tldr.web_scraper.trafilatura.extract", return_value="Body"),
        ):
            result = scrape_article("https://example.com/a")

        assert result == "Body"
        _, kwargs = autospec_fetch.call_args
        assert "headers" not in kwargs

    def test_user_agent_and_timeout_passed_via_config(self) -> None:
        """The configured UA and timeout reach trafilatura through the
        config object (USER_AGENTS / DOWNLOAD_TIMEOUT)."""
        captured: dict[str, str] = {}

        def fake_fetch(url, no_ssl=False, config=None, options=None):
            captured["ua"] = config.get("DEFAULT", "USER_AGENTS")
            captured["timeout"] = config.get("DEFAULT", "DOWNLOAD_TIMEOUT")
            return "<html>"

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", side_effect=fake_fetch),
            patch("tldr.web_scraper.trafilatura.extract", return_value="Body"),
        ):
            scrape_article("https://example.com/a", timeout=42, user_agent="custom/9")

        assert captured["ua"] == "custom/9"
        assert captured["timeout"] == "42"
