"""
Tests for the link extractor module (src/tldr/link_extractor.py).

Covers URL categorisation, text-based link extraction, and the main
``extract_links`` function that aggregates links from parsed articles.
"""

from __future__ import annotations

import pytest

from tldr.models import Article
from tldr.link_extractor import (
    CategorisedLink,
    LinkReport,
    categorise_url,
    extract_links,
    extract_links_from_text,
)


# ---------------------------------------------------------------------------
# Tests: categorise_url
# ---------------------------------------------------------------------------


class TestCategoriseUrl:
    """categorise_url() should assign the correct category to known URL patterns."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/user/repo", "repo"),
            ("https://github.com/org/my-project/tree/main", "repo"),
            ("https://gitlab.com/org/project", "repo"),
            ("https://bitbucket.org/team/repo", "repo"),
        ],
    )
    def test_repo_urls(self, url: str, expected: str) -> None:
        """Repository hosting URLs are categorised as 'repo'."""
        assert categorise_url(url) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://huggingface.co/meta-llama/Llama-3", "model"),
            ("https://hf.co/openai/whisper-large-v3", "model"),
        ],
    )
    def test_model_urls(self, url: str, expected: str) -> None:
        """Hugging Face model URLs are categorised as 'model'."""
        assert categorise_url(url) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://arxiv.org/abs/2401.12345", "paper"),
            ("https://arxiv.org/pdf/2401.12345", "paper"),
            ("https://papers.ssrn.com/sol3/papers.cfm?id=123", "paper"),
            ("https://openreview.net/forum?id=abc", "paper"),
            ("https://aclanthology.org/2024.acl-long.1/", "paper"),
            ("https://dl.acm.org/doi/10.1145/12345", "paper"),
        ],
    )
    def test_paper_urls(self, url: str, expected: str) -> None:
        """Academic paper URLs are categorised as 'paper'."""
        assert categorise_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/blog/post",
            "https://techcrunch.com/2024/01/01/news",
            "https://www.google.com",
        ],
    )
    def test_other_urls(self, url: str) -> None:
        """Generic URLs that match no specific pattern are categorised as 'other'."""
        assert categorise_url(url) == "other"


# ---------------------------------------------------------------------------
# Tests: extract_links_from_text
# ---------------------------------------------------------------------------


class TestExtractLinksFromText:
    """extract_links_from_text() should find all HTTP(S) URLs in free text."""

    def test_extracts_single_url(self) -> None:
        """A single URL embedded in a sentence is extracted."""
        text = "Check out https://example.com for details."
        result = extract_links_from_text(text)
        assert result == ["https://example.com"]

    def test_extracts_multiple_urls(self) -> None:
        """Multiple URLs in a block are all extracted."""
        text = (
            "See https://github.com/user/repo and "
            "also https://arxiv.org/abs/1234.5678 for the paper."
        )
        result = extract_links_from_text(text)
        assert len(result) == 2
        assert "https://github.com/user/repo" in result
        assert "https://arxiv.org/abs/1234.5678" in result

    def test_deduplicates_urls(self) -> None:
        """Duplicate URLs in the same text block are returned once."""
        text = "Visit https://example.com and https://example.com again."
        result = extract_links_from_text(text)
        assert result == ["https://example.com"]

    def test_strips_trailing_punctuation(self) -> None:
        """Trailing sentence punctuation is not included in the URL."""
        text = "Read https://example.com/path."
        result = extract_links_from_text(text)
        assert result == ["https://example.com/path"]

    def test_empty_text_returns_empty_list(self) -> None:
        """An empty string yields no URLs."""
        assert extract_links_from_text("") == []

    def test_no_urls_returns_empty_list(self) -> None:
        """Text with no URLs yields an empty list."""
        assert extract_links_from_text("No links here, just text.") == []


# ---------------------------------------------------------------------------
# Tests: LinkReport dataclass
# ---------------------------------------------------------------------------


class TestLinkReport:
    """LinkReport.total should reflect the sum across all categories."""

    def test_empty_report_has_zero_total(self) -> None:
        """A freshly created report has zero total."""
        report = LinkReport()
        assert report.total == 0

    def test_total_sums_all_categories(self) -> None:
        """Total reflects links in every category."""
        report = LinkReport(
            repos=[CategorisedLink(url="u", label="l", category="repo")],
            models=[CategorisedLink(url="u2", label="l", category="model")],
            papers=[],
            sources=[
                CategorisedLink(url="u3", label="l", category="source"),
                CategorisedLink(url="u4", label="l", category="source"),
            ],
            other=[],
        )
        assert report.total == 4


# ---------------------------------------------------------------------------
# Tests: extract_links (integration)
# ---------------------------------------------------------------------------


def _make_article(
    title: str = "Test Article",
    summary: str = "A summary.",
    url: str = "https://example.com/post",
    section: str = "AI",
    full_text: str = "",
) -> Article:
    """Build an Article with sensible defaults for testing."""
    return Article(
        title=title,
        summary=summary,
        url=url,
        section=section,
        full_text=full_text,
    )


class TestExtractLinks:
    """extract_links() should aggregate and categorise links from articles."""

    def test_primary_url_becomes_source(self) -> None:
        """Each article's primary URL is added as a 'source' link."""
        articles = [_make_article(url="https://blog.example.com/post")]
        report = extract_links(articles)
        assert len(report.sources) == 1
        assert report.sources[0].url == "https://blog.example.com/post"
        assert report.sources[0].category == "source"

    def test_github_url_in_body_becomes_repo(self) -> None:
        """A GitHub URL found in full_text is categorised as 'repo'."""
        articles = [
            _make_article(
                full_text="The code is at https://github.com/org/project — check it out."
            )
        ]
        report = extract_links(articles)
        assert len(report.repos) == 1
        assert "github.com/org/project" in report.repos[0].url

    def test_arxiv_url_in_body_becomes_paper(self) -> None:
        """An arXiv URL found in full_text is categorised as 'paper'."""
        articles = [
            _make_article(
                full_text="Read the paper at https://arxiv.org/abs/2401.99999 for details."
            )
        ]
        report = extract_links(articles)
        assert len(report.papers) == 1

    def test_hf_url_in_body_becomes_model(self) -> None:
        """A Hugging Face URL found in full_text is categorised as 'model'."""
        articles = [
            _make_article(
                full_text="Model card: https://huggingface.co/meta/llama-4"
            )
        ]
        report = extract_links(articles)
        assert len(report.models) == 1

    def test_deduplicates_across_articles(self) -> None:
        """The same URL appearing in multiple articles is reported once."""
        articles = [
            _make_article(title="A", url="https://same.com/post"),
            _make_article(title="B", url="https://same.com/post"),
        ]
        report = extract_links(articles)
        assert len(report.sources) == 1

    def test_falls_back_to_summary_when_no_full_text(self) -> None:
        """When full_text is empty, URLs are extracted from the summary."""
        articles = [
            _make_article(
                summary="See https://github.com/x/y for details.",
                full_text="",
            )
        ]
        report = extract_links(articles)
        assert len(report.repos) == 1

    def test_empty_articles_list(self) -> None:
        """An empty article list produces an empty report."""
        report = extract_links([])
        assert report.total == 0

    def test_label_uses_article_title(self) -> None:
        """Extracted links use the article title as their label."""
        articles = [_make_article(title="My Great Article")]
        report = extract_links(articles)
        assert report.sources[0].label == "My Great Article"

    def test_mixed_categories(self) -> None:
        """An article with multiple link types populates multiple categories."""
        articles = [
            _make_article(
                url="https://techcrunch.com/article",
                full_text=(
                    "Code: https://github.com/org/repo "
                    "Paper: https://arxiv.org/abs/2401.00001 "
                    "Model: https://huggingface.co/org/model "
                    "Other: https://docs.example.com/guide"
                ),
            )
        ]
        report = extract_links(articles)
        assert len(report.sources) == 1
        assert len(report.repos) == 1
        assert len(report.papers) == 1
        assert len(report.models) == 1
        assert len(report.other) == 1
        assert report.total == 5
