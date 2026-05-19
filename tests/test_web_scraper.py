"""
Tests for the web_scraper module.

Verifies article scraping behaviour with mocked trafilatura calls.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from unittest.mock import MagicMock, create_autospec, patch

import pytest
import trafilatura

from tldr.web_scraper import (
    _cloak_available,
    _resolve_use_cloak,
    _scrape_with_cloak,
    scrape_article,
    scrape_articles,
)

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

    def test_cloak_called_when_fetch_url_returns_none_and_use_cloak_true(self) -> None:
        """scrape_article calls _scrape_with_cloak when fetch_url fails and use_cloak=True."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None),
            patch("tldr.web_scraper._scrape_with_cloak", return_value="cloaked body") as mock_cloak,
        ):
            result = scrape_article("https://example.com/article", use_cloak=True)

        assert result == "cloaked body"
        mock_cloak.assert_called_once_with("https://example.com/article", 10)

    def test_cloak_called_when_extract_returns_none_and_use_cloak_true(self) -> None:
        """scrape_article calls _scrape_with_cloak when extract fails and use_cloak=True."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value=None),
            patch("tldr.web_scraper._scrape_with_cloak", return_value="cloaked body") as mock_cloak,
        ):
            result = scrape_article("https://example.com/article", use_cloak=True)

        assert result == "cloaked body"
        mock_cloak.assert_called_once()

    def test_cloak_called_when_exception_and_use_cloak_true(self) -> None:
        """scrape_article calls _scrape_with_cloak when trafilatura raises and use_cloak=True."""
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", side_effect=RuntimeError("boom")),
            patch("tldr.web_scraper._scrape_with_cloak", return_value="cloaked body") as mock_cloak,
        ):
            result = scrape_article("https://example.com/article", use_cloak=True)

        assert result == "cloaked body"
        mock_cloak.assert_called_once()

    def test_cloak_not_called_on_success(self) -> None:
        """scrape_article does NOT call _scrape_with_cloak when trafilatura succeeds."""
        mock_cloak = MagicMock(return_value="cloaked body")
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value="<html>"),
            patch("tldr.web_scraper.trafilatura.extract", return_value="real body"),
            patch("tldr.web_scraper._scrape_with_cloak", mock_cloak),
        ):
            result = scrape_article("https://example.com/article", use_cloak=True)

        assert result == "real body"
        mock_cloak.assert_not_called()

    def test_cloak_not_called_when_use_cloak_false_and_fetch_fails(self) -> None:
        """scrape_article does NOT call _scrape_with_cloak when use_cloak=False."""
        mock_cloak = MagicMock(return_value="cloaked body")
        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None),
            patch("tldr.web_scraper._scrape_with_cloak", mock_cloak),
        ):
            result = scrape_article("https://example.com/article", use_cloak=False)

        assert result is None
        mock_cloak.assert_not_called()


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
            patch("tldr.web_scraper._cloak_available", return_value=False),
        ):
            scrape_articles(articles)

        assert articles[0].full_text == "Full text A"

    def test_falls_back_to_summary_on_failure(self) -> None:
        """scrape_articles falls back to article.summary when scraping fails."""
        articles = [FakeArticle(url="https://a.com", summary="Fallback summary")]

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None),
            patch("tldr.web_scraper._cloak_available", return_value=False),
        ):
            scrape_articles(articles)

        assert articles[0].full_text == "Fallback summary"

    def test_passes_custom_user_agent_to_article_scraper(self) -> None:
        """scrape_articles forwards the configured UA to each article fetch."""
        articles = [FakeArticle(url="https://a.com", summary="Summary A")]

        with (
            patch("tldr.web_scraper.scrape_article", return_value="Full text") as mock_scrape,
            patch("tldr.web_scraper._cloak_available", return_value=False),
        ):
            scrape_articles(articles, user_agent="custom-client/7.0")

        mock_scrape.assert_called_once_with("https://a.com", 10, "custom-client/7.0", False)
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
            patch("tldr.web_scraper._cloak_available", return_value=False),
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

    def test_cloak_fallback_on_populates_full_text_from_cloak(self) -> None:
        """scrape_articles uses cloak text (not summary) when cloak_fallback=on and cloak succeeds."""
        articles = [FakeArticle(url="https://a.com", summary="Newsletter summary")]

        with (
            patch("tldr.web_scraper.trafilatura.fetch_url", return_value=None),
            patch("tldr.web_scraper._cloak_available", return_value=True),
            patch("tldr.web_scraper._scrape_with_cloak", return_value="cloaked full text") as mock_cloak,
        ):
            scrape_articles(articles, cloak_fallback="on")

        mock_cloak.assert_called_once()
        assert articles[0].full_text == "cloaked full text"


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


# ---------------------------------------------------------------------------
# _resolve_use_cloak tests
# ---------------------------------------------------------------------------


class TestResolveUseCloak:
    """Unit tests for _resolve_use_cloak()."""

    def test_off_returns_false(self) -> None:
        """cloak_fallback='off' always returns False."""
        assert _resolve_use_cloak("off") is False

    def test_off_case_insensitive(self) -> None:
        """cloak_fallback='OFF' is treated the same as 'off'."""
        assert _resolve_use_cloak("OFF") is False

    def test_auto_with_cloak_available_returns_true(self) -> None:
        """cloak_fallback='auto' returns True when cloakbrowser is importable."""
        with patch("tldr.web_scraper._cloak_available", return_value=True):
            assert _resolve_use_cloak("auto") is True

    def test_auto_with_cloak_unavailable_returns_false(self) -> None:
        """cloak_fallback='auto' returns False when cloakbrowser is not installed."""
        with patch("tldr.web_scraper._cloak_available", return_value=False):
            assert _resolve_use_cloak("auto") is False

    def test_on_with_cloak_available_returns_true(self) -> None:
        """cloak_fallback='on' returns True when cloakbrowser is installed."""
        with patch("tldr.web_scraper._cloak_available", return_value=True):
            assert _resolve_use_cloak("on") is True

    def test_on_with_cloak_unavailable_returns_false_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """cloak_fallback='on' returns False and emits a warning when not installed."""
        import logging

        with patch("tldr.web_scraper._cloak_available", return_value=False):
            with caplog.at_level(logging.WARNING, logger="tldr.web_scraper"):
                result = _resolve_use_cloak("on")

        assert result is False
        assert any("cloak_fallback=on" in r.message for r in caplog.records)

    def test_unknown_value_treated_as_auto(self) -> None:
        """Unrecognised values are treated as 'auto'."""
        with patch("tldr.web_scraper._cloak_available", return_value=True):
            assert _resolve_use_cloak("maybe") is True

    def test_empty_string_treated_as_auto(self) -> None:
        """Empty string is treated as 'auto'."""
        with patch("tldr.web_scraper._cloak_available", return_value=False):
            assert _resolve_use_cloak("") is False


# ---------------------------------------------------------------------------
# _scrape_with_cloak tests
# ---------------------------------------------------------------------------


class TestScrapeWithCloak:
    """Unit tests for _scrape_with_cloak()."""

    def _make_fake_cloakbrowser(self, html: str = "<html>body</html>") -> types.ModuleType:
        """Build a fake cloakbrowser module with cooperative mock objects."""
        fake_page = MagicMock()
        fake_page.goto = MagicMock()
        fake_page.content = MagicMock(return_value=html)

        fake_browser = MagicMock()
        fake_browser.new_page = MagicMock(return_value=fake_page)
        fake_browser.close = MagicMock()

        fake_mod = types.ModuleType("cloakbrowser")
        fake_mod.launch = MagicMock(return_value=fake_browser)

        return fake_mod, fake_browser, fake_page

    def test_happy_path_returns_extracted_text(self) -> None:
        """_scrape_with_cloak returns extracted text on success."""
        fake_mod, fake_browser, fake_page = self._make_fake_cloakbrowser("<html>body</html>")

        sys.modules["cloakbrowser"] = fake_mod
        try:
            with patch("tldr.web_scraper.trafilatura.extract", return_value="extracted"):
                result = _scrape_with_cloak("https://example.com/a", timeout=5)
        finally:
            del sys.modules["cloakbrowser"]

        assert result == "extracted"
        fake_browser.close.assert_called_once()

    def test_happy_path_passes_timeout_in_ms(self) -> None:
        """_scrape_with_cloak converts timeout seconds to ms for page.goto."""
        fake_mod, fake_browser, fake_page = self._make_fake_cloakbrowser()

        sys.modules["cloakbrowser"] = fake_mod
        try:
            with patch("tldr.web_scraper.trafilatura.extract", return_value="text"):
                _scrape_with_cloak("https://example.com/a", timeout=7)
        finally:
            del sys.modules["cloakbrowser"]

        fake_page.goto.assert_called_once_with("https://example.com/a", timeout=7000)

    def test_browser_close_called_on_success(self) -> None:
        """_scrape_with_cloak always closes the browser after a successful scrape."""
        fake_mod, fake_browser, _ = self._make_fake_cloakbrowser()

        sys.modules["cloakbrowser"] = fake_mod
        try:
            with patch("tldr.web_scraper.trafilatura.extract", return_value="text"):
                _scrape_with_cloak("https://example.com/a")
        finally:
            del sys.modules["cloakbrowser"]

        fake_browser.close.assert_called_once()

    def test_launch_raises_returns_none(self) -> None:
        """_scrape_with_cloak returns None when cloakbrowser.launch raises."""
        fake_mod = types.ModuleType("cloakbrowser")
        fake_mod.launch = MagicMock(side_effect=RuntimeError("chromium crashed"))

        sys.modules["cloakbrowser"] = fake_mod
        try:
            result = _scrape_with_cloak("https://example.com/a")
        finally:
            del sys.modules["cloakbrowser"]

        assert result is None

    def test_import_error_returns_none(self) -> None:
        """_scrape_with_cloak returns None when cloakbrowser is not importable."""
        # Force ImportError by setting sys.modules entry to None
        sys.modules["cloakbrowser"] = None  # type: ignore[assignment]
        try:
            result = _scrape_with_cloak("https://example.com/a")
        finally:
            del sys.modules["cloakbrowser"]

        assert result is None

    def test_extraction_returns_nothing_returns_none(self) -> None:
        """_scrape_with_cloak returns None when trafilatura.extract returns empty."""
        fake_mod, fake_browser, _ = self._make_fake_cloakbrowser()

        sys.modules["cloakbrowser"] = fake_mod
        try:
            with patch("tldr.web_scraper.trafilatura.extract", return_value=None):
                result = _scrape_with_cloak("https://example.com/a")
        finally:
            del sys.modules["cloakbrowser"]

        assert result is None
        fake_browser.close.assert_called_once()

    def test_goto_raises_returns_none_and_closes_browser(self) -> None:
        """_scrape_with_cloak returns None when page.goto raises, and closes browser."""
        fake_page = MagicMock()
        fake_page.goto = MagicMock(side_effect=RuntimeError("timeout"))

        fake_browser = MagicMock()
        fake_browser.new_page = MagicMock(return_value=fake_page)
        fake_browser.close = MagicMock()

        fake_mod = types.ModuleType("cloakbrowser")
        fake_mod.launch = MagicMock(return_value=fake_browser)

        sys.modules["cloakbrowser"] = fake_mod
        try:
            result = _scrape_with_cloak("https://example.com/a")
        finally:
            del sys.modules["cloakbrowser"]

        assert result is None
        fake_browser.close.assert_called_once()
