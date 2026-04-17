"""
Tests for the report generator module (src/tldr/report_generator.py).

Covers rendering of articles, script, summary, and overview files, as well as
the top-level ``generate_report`` function that creates the output folder.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tldr.models import Article
from tldr.link_extractor import CategorisedLink, LinkReport
from tldr.llm_summarizer import DialogueChunk
from tldr.report_generator import (
    _render_articles,
    _render_overview,
    _render_script,
    _render_summary,
    generate_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_article(
    title: str = "Test Article",
    summary: str = "A short summary.",
    url: str = "https://example.com/post",
    section: str = "AI & ML",
    full_text: str = "",
) -> Article:
    return Article(
        title=title, summary=summary, url=url, section=section, full_text=full_text
    )


def _make_chunk(text: str = "Alex: Hello!\nJordan: Hi there!", index: int = 0) -> DialogueChunk:
    return DialogueChunk(text=text, index=index)


def _make_link_report() -> LinkReport:
    return LinkReport(
        sources=[CategorisedLink(url="https://example.com/post", label="Test Article", category="source")],
        repos=[CategorisedLink(url="https://github.com/org/repo", label="Test Article", category="repo")],
        models=[CategorisedLink(url="https://huggingface.co/org/model", label="Test Article", category="model")],
        papers=[CategorisedLink(url="https://arxiv.org/abs/2401.00001", label="Test Article", category="paper")],
        other=[],
    )


# ---------------------------------------------------------------------------
# Tests: _render_articles
# ---------------------------------------------------------------------------


class TestRenderArticles:
    """_render_articles() should produce valid Markdown with sections and articles."""

    def test_contains_article_title(self) -> None:
        """The article title appears as a heading."""
        md = _render_articles([_make_article(title="My Great Article")])
        assert "### My Great Article" in md

    def test_contains_article_url(self) -> None:
        """The article URL appears as a link."""
        md = _render_articles([_make_article(url="https://example.com/a")])
        assert "https://example.com/a" in md

    def test_contains_section_heading(self) -> None:
        """The section name appears as an H2."""
        md = _render_articles([_make_article(section="BIG TECH")])
        assert "## BIG TECH" in md

    def test_contains_summary(self) -> None:
        """The summary text is included with a bold label."""
        md = _render_articles([_make_article(summary="This is important.")])
        assert "**Summary:** This is important." in md

    def test_contains_full_text_in_details(self) -> None:
        """Full text appears in a collapsible details block."""
        md = _render_articles([_make_article(summary="Short.", full_text="Long detailed text here.")])
        assert "<details>" in md
        assert "Long detailed text here." in md

    def test_no_details_when_full_text_matches_summary(self) -> None:
        """No details block when full_text equals the summary."""
        md = _render_articles([_make_article(summary="Same.", full_text="Same.")])
        assert "<details>" not in md

    def test_no_details_when_full_text_empty(self) -> None:
        """No details block when full_text is empty."""
        md = _render_articles([_make_article(summary="Summary only.", full_text="")])
        assert "<details>" not in md

    def test_empty_articles_returns_header_only(self) -> None:
        """An empty list produces just the top-level heading."""
        md = _render_articles([])
        assert "# Selected Articles" in md

    def test_groups_by_section(self) -> None:
        """Articles in different sections get separate section headings."""
        articles = [
            _make_article(title="A", section="AI"),
            _make_article(title="B", section="DEVOPS"),
        ]
        md = _render_articles(articles)
        assert "## AI" in md
        assert "## DEVOPS" in md


# ---------------------------------------------------------------------------
# Tests: _render_script
# ---------------------------------------------------------------------------


class TestRenderScript:
    """_render_script() should produce Markdown with dialogue text."""

    def test_contains_chunk_text(self) -> None:
        """Each chunk's text appears in the output."""
        chunks = [_make_chunk("Alex: First chunk"), _make_chunk("Jordan: Second chunk")]
        md = _render_script(chunks)
        assert "Alex: First chunk" in md
        assert "Jordan: Second chunk" in md

    def test_has_header(self) -> None:
        """The script starts with a top-level heading."""
        md = _render_script([_make_chunk()])
        assert "# Podcast Script" in md

    def test_empty_chunks(self) -> None:
        """An empty chunk list produces just the heading."""
        md = _render_script([])
        assert "# Podcast Script" in md


# ---------------------------------------------------------------------------
# Tests: _render_summary
# ---------------------------------------------------------------------------


class TestRenderSummary:
    """_render_summary() should produce categorised Markdown link sections."""

    def test_contains_repo_section(self) -> None:
        """Repositories appear under their heading."""
        md = _render_summary(_make_link_report())
        assert "GitHub / GitLab Repositories" in md

    def test_contains_model_section(self) -> None:
        """Models appear under their heading."""
        md = _render_summary(_make_link_report())
        assert "Hugging Face Models" in md

    def test_contains_paper_section(self) -> None:
        """Papers appear under their heading."""
        md = _render_summary(_make_link_report())
        assert "Academic Papers" in md

    def test_empty_report_shows_message(self) -> None:
        """An empty report shows a 'no links found' message."""
        md = _render_summary(LinkReport())
        assert "No external links" in md

    def test_links_are_markdown_formatted(self) -> None:
        """Links use Markdown link syntax."""
        md = _render_summary(_make_link_report())
        assert "[Test Article](https://github.com/org/repo)" in md

    def test_skips_empty_categories_without_articles(self) -> None:
        """Categories with no items are not rendered (no articles context)."""
        report = LinkReport(
            sources=[CategorisedLink(url="https://x.com", label="X", category="source")],
        )
        md = _render_summary(report)
        assert "GitHub" not in md
        assert "Hugging Face" not in md

    def test_with_articles_shows_summary(self) -> None:
        """When articles are passed, the summary text appears."""
        articles = [_make_article(title="Test Article", summary="This is important.")]
        md = _render_summary(_make_link_report(), articles=articles)
        assert "This is important." in md

    def test_with_articles_shows_interest_score(self) -> None:
        """When articles have an interest score, it appears in the summary."""
        article = _make_article(title="Test Article")
        article.interest_score = 8.0
        md = _render_summary(_make_link_report(), articles=[article])
        assert "8/10" in md

    def test_with_articles_groups_secondary_links(self) -> None:
        """Secondary links (repos, papers) appear under their source article."""
        articles = [_make_article(title="Test Article")]
        md = _render_summary(_make_link_report(), articles=articles)
        assert "Repository:" in md
        assert "github.com/org/repo" in md

    def test_with_articles_shows_article_heading_as_link(self) -> None:
        """Each source article title is rendered as a Markdown link."""
        articles = [_make_article(title="Test Article")]
        md = _render_summary(_make_link_report(), articles=articles)
        assert "[Test Article](https://example.com/post)" in md

    def test_no_score_shown_when_zero(self) -> None:
        """Interest score line is omitted when score is 0."""
        articles = [_make_article(title="Test Article")]
        md = _render_summary(_make_link_report(), articles=articles)
        assert "Interest score" not in md


# ---------------------------------------------------------------------------
# Tests: _render_overview
# ---------------------------------------------------------------------------


class TestRenderOverview:
    """_render_overview() should produce a Markdown overview with metadata."""

    def test_contains_date(self) -> None:
        """The target date appears in the table."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
            target_date=date(2026, 4, 3),
        )
        assert "2026-04-03" in md

    def test_contains_article_count(self) -> None:
        """The article count is shown."""
        md = _render_overview(
            [_make_article(), _make_article(title="Second")],
            [_make_chunk()], _make_link_report(),
        )
        assert "| Articles selected | 2 |" in md

    def test_contains_topics(self) -> None:
        """The list of fetched TLDR topics is shown."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
            topics=["ai", "devops"],
        )
        assert "| Topics | ai, devops |" in md

    def test_contains_audio_path(self) -> None:
        """The audio file path is shown when provided."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
            audio_path="output/tldr_2026.mp3",
        )
        assert "output/tldr_2026.mp3" in md

    def test_contains_token_summary(self) -> None:
        """The token summary block is included."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
            token_summary="Token usage: 1,000 in + 500 out",
        )
        assert "Token usage: 1,000 in + 500 out" in md

    def test_contains_sections(self) -> None:
        """Article sections are listed."""
        articles = [
            _make_article(section="AI & ML"),
            _make_article(title="B", section="DEVOPS"),
        ]
        md = _render_overview(articles, [_make_chunk()], _make_link_report())
        assert "AI & ML" in md
        assert "DEVOPS" in md

    def test_contains_link_breakdown(self) -> None:
        """Link category counts are shown."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
        )
        assert "## Link Breakdown" in md
        assert "Source articles" in md

    def test_no_link_breakdown_when_empty(self) -> None:
        """No link breakdown section for empty reports."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], LinkReport(),
        )
        assert "## Link Breakdown" not in md

    def test_no_token_section_when_none(self) -> None:
        """No token section when summary is not provided."""
        md = _render_overview(
            [_make_article()], [_make_chunk()], _make_link_report(),
        )
        assert "## Token Usage" not in md


# ---------------------------------------------------------------------------
# Tests: generate_report (integration)
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """generate_report() should create the folder with all three files."""

    def test_creates_folder_and_files(self, tmp_path: Path) -> None:
        """The report folder contains overview.md, articles.md, script.md, and summary.md."""
        articles = [_make_article()]
        chunks = [_make_chunk()]
        link_report = _make_link_report()

        result = generate_report(articles, chunks, link_report, tmp_path, "2026-04-03_1430")

        assert result == tmp_path / "tldr_2026-04-03_1430"
        assert (result / "overview.md").is_file()
        assert (result / "articles.md").is_file()
        assert (result / "script.md").is_file()
        assert (result / "summary.md").is_file()

    def test_articles_file_content(self, tmp_path: Path) -> None:
        """articles.md contains the article title and URL."""
        articles = [_make_article(title="Cool Article", url="https://cool.com")]
        result = generate_report(articles, [_make_chunk()], _make_link_report(), tmp_path, "t1")

        content = (result / "articles.md").read_text(encoding="utf-8")
        assert "Cool Article" in content
        assert "https://cool.com" in content

    def test_script_file_content(self, tmp_path: Path) -> None:
        """script.md contains the dialogue text."""
        chunks = [_make_chunk("Alex: Testing the script output")]
        result = generate_report([_make_article()], chunks, _make_link_report(), tmp_path, "t2")

        content = (result / "script.md").read_text(encoding="utf-8")
        assert "Testing the script output" in content

    def test_summary_file_content(self, tmp_path: Path) -> None:
        """summary.md contains categorised links."""
        result = generate_report([_make_article()], [_make_chunk()], _make_link_report(), tmp_path, "t3")

        content = (result / "summary.md").read_text(encoding="utf-8")
        assert "github.com/org/repo" in content
        assert "arxiv.org" in content

    def test_idempotent_on_existing_folder(self, tmp_path: Path) -> None:
        """Calling twice with the same timestamp overwrites without error."""
        args = ([_make_article()], [_make_chunk()], _make_link_report(), tmp_path, "dup")
        generate_report(*args)
        result = generate_report(*args)
        assert (result / "articles.md").is_file()
