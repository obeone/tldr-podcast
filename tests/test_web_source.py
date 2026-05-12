"""
Tests for the web source module (src/tldr/web_source.py).

Covers happy-path parsing, redirect-skip behaviour, cross-topic
deduplication, sponsor filtering, and topic validation.  All HTTP
requests are intercepted with unittest.mock so no real network access
occurs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from tldr.web_source import (
    _DEFAULT_USER_AGENT,
    SUPPORTED_TOPICS,
    _build_url,
    _is_sponsor_article,
    _is_sponsor_section,
    _parse_html,
    _strip_read_time,
    check_availability,
    fetch_newsletters,
    validate_topics,
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Fixture HTML path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tldr-infosec-2026-04-06.html"


# ---------------------------------------------------------------------------
# Minimal inline HTML pages for unit tests
# ---------------------------------------------------------------------------

_MINIMAL_HTML = """\
<!DOCTYPE html>
<html>
<body>
<section>
<header>
  <h3 class="text-center font-bold">Attacks &amp; Vulnerabilities</h3>
</header>
<article class="mt-3">
  <a class="font-bold" href="https://example.com/article1" target="_blank">
    <h3>A Critical Flaw in OpenSSL (3 minute read)</h3>
  </a>
  <div class="newsletter-html">Researchers found a critical flaw in OpenSSL 3.x.</div>
</article>
<article class="mt-3">
  <a class="font-bold" href="https://example.com/article2" target="_blank">
    <h3>New Ransomware Targets Healthcare (2 minute read)</h3>
  </a>
  <div class="newsletter-html">A new ransomware strain is targeting hospitals.</div>
</article>
</section>
<section>
<header>
  <h3 class="text-center font-bold">Strategies &amp; Tactics</h3>
</header>
<article class="mt-3">
  <a class="font-bold" href="https://example.com/article3" target="_blank">
    <h3>Zero-Trust Architecture Guide (5 minute read)</h3>
  </a>
  <div class="newsletter-html">A comprehensive guide to implementing zero-trust.</div>
</article>
</section>
</body>
</html>
"""

_SPONSOR_HTML = """\
<!DOCTYPE html>
<html>
<body>
<section>
<header>
  <h3 class="text-center font-bold">TOGETHER WITH Acme Corp</h3>
</header>
<article class="mt-3">
  <a href="https://sponsor.example.com/promo" target="_blank">
    <h3>Check out our product (1 minute read)</h3>
  </a>
  <div class="newsletter-html">Buy our stuff.</div>
</article>
</section>
<section>
<header>
  <h3 class="text-center font-bold">Launches &amp; Tools</h3>
</header>
<article class="mt-3">
  <a href="https://example.com/tool1?utm_source=tldrnewsletter&utm_medium=sponsor" target="_blank">
    <h3>Great Tool (2 minute read)</h3>
  </a>
  <div class="newsletter-html">A sponsored tool article.</div>
</article>
<article class="mt-3">
  <a href="https://example.com/real-tool" target="_blank">
    <h3>(Sponsor) Another promoted tool (2 minute read)</h3>
  </a>
  <div class="newsletter-html">Another sponsored entry.</div>
</article>
<article class="mt-3">
  <a href="https://example.com/legit" target="_blank">
    <h3>Legitimate Open-Source Tool (4 minute read)</h3>
  </a>
  <div class="newsletter-html">A real non-sponsored tool.</div>
</article>
</section>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestStripReadTime:
    """_strip_read_time() removes trailing read-time annotation."""

    def test_strips_minute_read(self) -> None:
        assert _strip_read_time("Some Article (3 minute read)") == "Some Article"

    def test_strips_github_repo(self) -> None:
        assert _strip_read_time("My Repo (github repo)") == "My Repo"

    def test_case_insensitive(self) -> None:
        assert _strip_read_time("Title (5 MINUTE READ)") == "Title"

    def test_no_annotation_unchanged(self) -> None:
        assert _strip_read_time("Plain Title") == "Plain Title"

    def test_strips_resource(self) -> None:
        assert _strip_read_time("Some Resource (resource)") == "Some Resource"


class TestIsSponsorSection:
    """_is_sponsor_section() detects sponsor header names."""

    def test_together_with(self) -> None:
        assert _is_sponsor_section("TOGETHER WITH Acme") is True

    def test_sponsor_header(self) -> None:
        assert _is_sponsor_section("Sponsor content") is True

    def test_promotion_header(self) -> None:
        assert _is_sponsor_section("Promotion Zone") is True

    def test_normal_section(self) -> None:
        assert _is_sponsor_section("Attacks & Vulnerabilities") is False

    def test_launches_and_tools(self) -> None:
        assert _is_sponsor_section("Launches & Tools") is False


class TestIsSponsorArticle:
    """_is_sponsor_article() detects sponsored article entries."""

    def test_sponsor_utm_in_url(self) -> None:
        url = "https://example.com/tool?utm_source=tldrnewsletter&utm_medium=sponsor"
        assert _is_sponsor_article("Normal Title", url) is True

    def test_sponsor_title_prefix(self) -> None:
        assert _is_sponsor_article("(Sponsor) Great product", "https://ok.com") is True

    def test_sponsor_title_case_insensitive(self) -> None:
        assert _is_sponsor_article("(SPONSOR) Thing", "https://ok.com") is True

    def test_clean_article(self) -> None:
        assert _is_sponsor_article("Normal Article", "https://example.com/post") is False


class TestBuildUrl:
    """_build_url() produces the correct TLDR newsletter URL."""

    def test_correct_url_format(self) -> None:
        url = _build_url("infosec", date(2026, 4, 6))
        assert url == "https://tldr.tech/infosec/2026-04-06"

    def test_ai_topic(self) -> None:
        url = _build_url("ai", date(2026, 1, 15))
        assert url == "https://tldr.tech/ai/2026-01-15"


class TestValidateTopics:
    """validate_topics() normalises and rejects unknown slugs."""

    def test_valid_topics_returned(self) -> None:
        result = validate_topics(["ai", "devops"])
        assert result == ["ai", "devops"]

    def test_case_insensitive(self) -> None:
        result = validate_topics(["AI", "DevOps"])
        assert result == ["ai", "devops"]

    def test_unknown_topic_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown topic"):
            validate_topics(["ai", "notarealtopic"])

    def test_suggestion_in_error(self) -> None:
        with pytest.raises(ValueError, match="infosec"):
            validate_topics(["infosecurity"])

    def test_empty_strings_skipped(self) -> None:
        result = validate_topics(["ai", "", "  "])
        assert result == ["ai"]

    def test_all_supported_topics_valid(self) -> None:
        result = validate_topics(list(SUPPORTED_TOPICS))
        assert result == list(SUPPORTED_TOPICS)


# ---------------------------------------------------------------------------
# Unit tests: HTML parser
# ---------------------------------------------------------------------------


class TestParseHtml:
    """_parse_html() extracts Article objects from TLDR newsletter HTML."""

    def test_happy_path_article_count(self) -> None:
        """All three non-sponsor articles are parsed from the minimal HTML."""
        articles = _parse_html(_MINIMAL_HTML)
        assert len(articles) == 3

    def test_title_stripped_of_read_time(self) -> None:
        """Read-time annotation is removed from titles."""
        articles = _parse_html(_MINIMAL_HTML)
        for a in articles:
            assert "minute read" not in a.title.lower()

    def test_url_extracted(self) -> None:
        """Article URLs are set correctly."""
        articles = _parse_html(_MINIMAL_HTML)
        urls = {a.url for a in articles}
        assert "https://example.com/article1" in urls
        assert "https://example.com/article2" in urls
        assert "https://example.com/article3" in urls

    def test_section_assigned(self) -> None:
        """Articles carry the section name from the preceding header."""
        articles = _parse_html(_MINIMAL_HTML)
        sections = {a.section for a in articles}
        assert "Attacks & Vulnerabilities" in sections
        assert "Strategies & Tactics" in sections

    def test_summary_extracted(self) -> None:
        """Summary text from newsletter-html div is populated."""
        articles = _parse_html(_MINIMAL_HTML)
        article = next(a for a in articles if "OpenSSL" in a.title)
        assert "OpenSSL" in article.summary

    def test_sponsor_section_filtered(self) -> None:
        """Articles inside a sponsor section are excluded."""
        articles = _parse_html(_SPONSOR_HTML)
        urls = [a.url for a in articles]
        assert not any("sponsor.example.com" in u for u in urls)

    def test_sponsor_utm_url_filtered(self) -> None:
        """Articles with a sponsor UTM URL parameter are excluded."""
        articles = _parse_html(_SPONSOR_HTML)
        urls = [a.url for a in articles]
        assert not any("utm_medium=sponsor" in u for u in urls)

    def test_sponsor_title_prefix_filtered(self) -> None:
        """Articles with (Sponsor) in the title are excluded."""
        articles = _parse_html(_SPONSOR_HTML)
        titles = [a.title.lower() for a in articles]
        assert not any(t.startswith("(sponsor)") for t in titles)

    def test_legitimate_article_kept(self) -> None:
        """Non-sponsored articles in sections with sponsors are kept."""
        articles = _parse_html(_SPONSOR_HTML)
        titles = [a.title for a in articles]
        assert any("Legitimate" in t for t in titles)

    def test_full_text_starts_empty(self) -> None:
        """Parsed articles have empty full_text (filled later by web_scraper)."""
        articles = _parse_html(_MINIMAL_HTML)
        assert all(a.full_text == "" for a in articles)


# ---------------------------------------------------------------------------
# Integration tests: fetch_newsletters (HTTP mocked via _fetch_page)
# ---------------------------------------------------------------------------


class TestFetchNewsletters:
    """fetch_newsletters() fetches, parses, and deduplicates across topics."""

    def test_happy_path_returns_articles(self) -> None:
        """Articles are returned when the page exists."""
        with patch("tldr.web_source._fetch_page", return_value=_MINIMAL_HTML):
            articles = fetch_newsletters(["infosec"], date(2026, 4, 6))
        assert len(articles) == 3

    def test_redirect_returns_empty(self) -> None:
        """When _fetch_page returns None (redirect), the topic is skipped."""
        with patch("tldr.web_source._fetch_page", return_value=None):
            articles = fetch_newsletters(["infosec"], date(2026, 4, 6))
        assert articles == []

    def test_dedup_across_topics(self) -> None:
        """Same URLs from two topics are deduplicated to one entry each."""
        with patch("tldr.web_source._fetch_page", return_value=_MINIMAL_HTML):
            articles = fetch_newsletters(["infosec", "tech"], date(2026, 4, 6))
        # 3 unique articles, not 6
        assert len(articles) == 3

    def test_multi_topic_merges_articles(self) -> None:
        """Articles from different topics with distinct URLs are all included."""
        html_a = """\
<html><body>
<section>
<header><h3 class="text-center font-bold">Section A</h3></header>
<article class="mt-3">
  <a href="https://example.com/a1"><h3>Article A1 (1 minute read)</h3></a>
  <div class="newsletter-html">Summary A1</div>
</article>
</section>
</body></html>"""
        html_b = """\
<html><body>
<section>
<header><h3 class="text-center font-bold">Section B</h3></header>
<article class="mt-3">
  <a href="https://example.com/b1"><h3>Article B1 (2 minute read)</h3></a>
  <div class="newsletter-html">Summary B1</div>
</article>
</section>
</body></html>"""

        def _fake_fetch(url, *, timeout_seconds, user_agent):
            return html_a if "infosec" in url else html_b

        with patch("tldr.web_source._fetch_page", side_effect=_fake_fetch):
            articles = fetch_newsletters(["infosec", "devops"], date(2026, 4, 6))

        assert len(articles) == 2
        urls = {a.url for a in articles}
        assert "https://example.com/a1" in urls
        assert "https://example.com/b1" in urls

    def test_sponsor_filtering_in_fetch(self) -> None:
        """Sponsor articles are excluded even in the integrated fetch path."""
        with patch("tldr.web_source._fetch_page", return_value=_SPONSOR_HTML):
            articles = fetch_newsletters(["infosec"], date(2026, 4, 6))
        titles = [a.title.lower() for a in articles]
        assert not any(t.startswith("(sponsor)") for t in titles)
        assert any("Legitimate" in a.title for a in articles)

    def test_all_topics_redirect_returns_empty(self) -> None:
        """When every topic redirects, an empty list is returned."""
        with patch("tldr.web_source._fetch_page", return_value=None):
            articles = fetch_newsletters(["ai", "devops", "infosec"], date(2026, 4, 6))
        assert articles == []

    def test_partial_redirect_keeps_working_topic(self) -> None:
        """If one topic redirects and one succeeds, articles from the good topic are kept."""
        def _fake_fetch(url, *, timeout_seconds, user_agent):
            return None if "infosec" in url else _MINIMAL_HTML

        with patch("tldr.web_source._fetch_page", side_effect=_fake_fetch):
            articles = fetch_newsletters(["infosec", "devops"], date(2026, 4, 6))

        assert len(articles) == 3


# ---------------------------------------------------------------------------
# Fixture-based tests (real saved HTML)
# ---------------------------------------------------------------------------


class TestRealFixture:
    """Parse the saved tldr-infosec-2026-04-06.html fixture."""

    @pytest.mark.skipif(
        not _FIXTURE_PATH.exists(),
        reason="Fixture file not found.",
    )
    def test_fixture_parses_articles(self) -> None:
        """The real fixture yields a non-empty list of articles."""
        html = _FIXTURE_PATH.read_text(encoding="utf-8")
        articles = _parse_html(html)
        assert len(articles) > 0

    @pytest.mark.skipif(
        not _FIXTURE_PATH.exists(),
        reason="Fixture file not found.",
    )
    def test_fixture_no_sponsor_articles(self) -> None:
        """No sponsor entries appear in the parsed fixture articles."""
        html = _FIXTURE_PATH.read_text(encoding="utf-8")
        articles = _parse_html(html)
        for a in articles:
            assert "utm_medium=sponsor" not in a.url
            assert not a.title.lower().startswith("(sponsor)")

    @pytest.mark.skipif(
        not _FIXTURE_PATH.exists(),
        reason="Fixture file not found.",
    )
    def test_fixture_articles_have_sections(self) -> None:
        """Every article from the fixture has a non-empty section."""
        html = _FIXTURE_PATH.read_text(encoding="utf-8")
        articles = _parse_html(html)
        for a in articles:
            assert a.section, f"Article '{a.title}' has no section"

    @pytest.mark.skipif(
        not _FIXTURE_PATH.exists(),
        reason="Fixture file not found.",
    )
    def test_fixture_articles_have_titles_and_urls(self) -> None:
        """Every article has a non-empty title and URL."""
        html = _FIXTURE_PATH.read_text(encoding="utf-8")
        articles = _parse_html(html)
        for a in articles:
            assert a.title, "Empty title found"
            assert a.url.startswith("http"), f"Invalid URL: {a.url!r}"


class TestCheckAvailability:
    """check_availability() filters out redirected topic URLs in parallel."""

    def test_default_user_agent_matches_browser(self) -> None:
        """The default web-source User-Agent is a browser-like Chrome UA."""
        assert _DEFAULT_USER_AGENT == _BROWSER_USER_AGENT

    def _make_head_response(self, url: str, status_code: int) -> object:
        from unittest.mock import MagicMock

        request = MagicMock()
        response = MagicMock()
        response.status_code = status_code
        response.is_redirect = 300 <= status_code < 400
        response.request = request
        return response

    def test_only_available_topics_returned(self) -> None:
        """A 200 is available; a 3xx (redirect) is not."""
        from unittest.mock import patch

        def _fake_head(self_, url, follow_redirects=False):
            # Pretend 'ai' and 'devops' publish, the rest redirect.
            if url.endswith("/ai/2026-04-17") or url.endswith("/devops/2026-04-17"):
                return TestCheckAvailability()._make_head_response(url, 200)
            return TestCheckAvailability()._make_head_response(url, 307)

        with patch("httpx.Client.head", new=_fake_head):
            result = check_availability(
                ["ai", "infosec", "devops", "crypto"],
                date(2026, 4, 17),
            )
        assert result == ["ai", "devops"]

    def test_preserves_input_order(self) -> None:
        """Returned topics keep the order of the input list."""
        from unittest.mock import patch

        def _fake_head(self_, url, follow_redirects=False):
            return TestCheckAvailability()._make_head_response(url, 200)

        with patch("httpx.Client.head", new=_fake_head):
            result = check_availability(
                ["devops", "ai", "infosec"],
                date(2026, 4, 17),
            )
        assert result == ["devops", "ai", "infosec"]

    def test_http_error_treated_as_unavailable(self) -> None:
        """Network failures during probing are silently marked unavailable."""
        import httpx
        from unittest.mock import patch

        def _fake_head(self_, url, follow_redirects=False):
            raise httpx.ConnectError("boom")

        with patch("httpx.Client.head", new=_fake_head):
            result = check_availability(["ai", "devops"], date(2026, 4, 17))
        assert result == []
