"""
Tests for the email parser module (src/tldr/email_parser.py).

Uses real .eml files from the mails/ directory to validate parsing behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tldr.email_parser import Article, ParseError, parse_emails

# ---------------------------------------------------------------------------
# Helpers / paths
# ---------------------------------------------------------------------------

_MAILS_DIR = Path(__file__).parent.parent / "mails"

_EML_FILES = list(_MAILS_DIR.glob("*.eml"))


def _load_eml(name_fragment: str) -> bytes:
    """
    Load the first .eml file whose name contains *name_fragment*.

    Parameters
    ----------
    name_fragment : str
        A substring that uniquely identifies the desired .eml file.

    Returns
    -------
    bytes
        The raw bytes of the matching file.

    Raises
    ------
    FileNotFoundError
        If no file matching *name_fragment* is found.
    """
    for f in _EML_FILES:
        if name_fragment in f.name:
            return f.read_bytes()
    raise FileNotFoundError(
        f"No .eml file containing '{name_fragment}' found in {_MAILS_DIR}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gemini_openai_articles() -> list[Article]:
    """Articles from the 'OpenAI strategic issues' TLDR newsletter."""
    raw = _load_eml("OpenAI")
    return parse_emails(raw)


@pytest.fixture(scope="module")
def gemini_ai_articles() -> list[Article]:
    """Articles from the 'agent sandboxing' TLDR AI newsletter."""
    raw = _load_eml("agent sandboxing")
    return parse_emails(raw)


@pytest.fixture(scope="module")
def prometheus_articles() -> list[Article]:
    """Articles from the 'Modernizing Prometheus' TLDR DevOps newsletter."""
    raw = _load_eml("Modernizing")
    return parse_emails(raw)


# ---------------------------------------------------------------------------
# Tests: non-empty list of Articles per email
# ---------------------------------------------------------------------------


class TestParseEmailsReturnsArticles:
    """parse_emails() must return a non-empty list of Articles for each file."""

    def test_gemini_openai_non_empty(self, gemini_openai_articles: list[Article]) -> None:
        """TLDR newsletter with OpenAI / Gemini content yields articles."""
        assert len(gemini_openai_articles) > 0

    def test_gemini_ai_non_empty(self, gemini_ai_articles: list[Article]) -> None:
        """TLDR AI newsletter yields articles."""
        assert len(gemini_ai_articles) > 0

    def test_prometheus_non_empty(self, prometheus_articles: list[Article]) -> None:
        """TLDR DevOps newsletter yields articles."""
        assert len(prometheus_articles) > 0


# ---------------------------------------------------------------------------
# Tests: every Article has non-empty title, summary, and valid URL
# ---------------------------------------------------------------------------


class TestArticleFields:
    """Each Article must have a non-empty title, summary, and HTTP URL."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "gemini_openai_articles",
            "gemini_ai_articles",
            "prometheus_articles",
        ],
    )
    def test_all_titles_non_empty(self, request: pytest.FixtureRequest, fixture_name: str) -> None:
        """Every article has a non-empty title."""
        articles: list[Article] = request.getfixturevalue(fixture_name)
        for article in articles:
            assert article.title, f"Empty title found: {article}"

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "gemini_openai_articles",
            "gemini_ai_articles",
            "prometheus_articles",
        ],
    )
    def test_all_summaries_non_empty(
        self, request: pytest.FixtureRequest, fixture_name: str
    ) -> None:
        """Every article has a non-empty summary."""
        articles: list[Article] = request.getfixturevalue(fixture_name)
        for article in articles:
            assert article.summary, f"Empty summary for article '{article.title}'"

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "gemini_openai_articles",
            "gemini_ai_articles",
            "prometheus_articles",
        ],
    )
    def test_all_urls_valid(self, request: pytest.FixtureRequest, fixture_name: str) -> None:
        """Every article URL starts with 'http'."""
        articles: list[Article] = request.getfixturevalue(fixture_name)
        for article in articles:
            assert article.url.startswith("http"), (
                f"Invalid URL '{article.url}' for article '{article.title}'"
            )


# ---------------------------------------------------------------------------
# Tests: no sponsor articles in output
# ---------------------------------------------------------------------------


_SPONSOR_KEYWORDS = ("sponsor", "together with", "advertisement", "presented by")


def _is_sponsor_text(text: str) -> bool:
    """Return True if *text* contains any sponsor keyword (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in _SPONSOR_KEYWORDS)


class TestNoSponsorArticles:
    """Sponsor articles must be filtered out of the returned list."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "gemini_openai_articles",
            "gemini_ai_articles",
            "prometheus_articles",
        ],
    )
    def test_no_sponsor_in_titles(
        self, request: pytest.FixtureRequest, fixture_name: str
    ) -> None:
        """No article title should contain sponsor keywords."""
        articles: list[Article] = request.getfixturevalue(fixture_name)
        for article in articles:
            assert not _is_sponsor_text(article.title), (
                f"Sponsor article leaked through: '{article.title}'"
            )

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "gemini_openai_articles",
            "gemini_ai_articles",
            "prometheus_articles",
        ],
    )
    def test_no_workos_sponsor(
        self, request: pytest.FixtureRequest, fixture_name: str
    ) -> None:
        """Known WorkOS / Mabl sponsor articles must not appear in the output."""
        articles: list[Article] = request.getfixturevalue(fixture_name)
        titles = [a.title for a in articles]
        for title in titles:
            assert "WORKOS" not in title.upper()
            assert "MABL" not in title.upper()


# ---------------------------------------------------------------------------
# Tests: ParseError on invalid input
# ---------------------------------------------------------------------------


class TestParseError:
    """parse_emails() must raise ParseError for invalid input."""

    def test_raises_on_empty_bytes(self) -> None:
        """Empty bytes input raises ParseError."""
        with pytest.raises(ParseError):
            parse_emails(b"")

    def test_valid_email_with_no_plain_text_raises(self) -> None:
        """An email that has no text/plain part raises ParseError."""
        # Minimal MIME email with only an HTML part.
        raw = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<html><body>Hello</body></html>\r\n"
        )
        with pytest.raises(ParseError, match="text/plain"):
            parse_emails(raw)

    def test_returns_list_for_plain_text_only_email(self) -> None:
        """A plain-text email with no articles returns an empty list (no crash)."""
        raw = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"Just a regular email with no TLDR articles.\r\n"
        )
        result = parse_emails(raw)
        assert isinstance(result, list)
