"""
Report generator for TLDR podcast runs.

Creates a timestamped output folder for each generation containing:

- ``overview.md`` — generation metadata: date, email count, article count,
  sections covered, audio path, and token/cost summary.
- ``articles.md`` — selected articles with title, section, URL, summary,
  and full text when available.
- ``script.md`` — the full two-host dialogue script.
- ``summary.md`` — synthetic reference sheet with categorised links to all
  sources, repositories, models, and papers mentioned in the articles.

The heavy lifting for link categorisation is delegated to
:mod:`tldr.link_extractor`.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tldr.email_parser import Article
    from tldr.link_extractor import LinkReport
    from tldr.llm_summarizer import DialogueChunk

logger = logging.getLogger(__name__)


def _render_overview(
    articles: list[Article],
    chunks: list[DialogueChunk],
    link_report: LinkReport,
    audio_path: Path | str | None = None,
    token_summary: str | None = None,
    email_count: int = 0,
    target_date: date | None = None,
) -> str:
    """
    Render a generation overview as Markdown.

    Parameters
    ----------
    articles : list[Article]
        Parsed articles included in the podcast.
    chunks : list[DialogueChunk]
        Dialogue chunks produced by the LLM.
    link_report : LinkReport
        Categorised links extracted from the articles.
    audio_path : Path or str or None
        Path to the generated audio file.
    token_summary : str or None
        Human-readable token/cost summary from :class:`~tldr.token_tracker.TokenTracker`.
    email_count : int
        Number of source emails processed.
    target_date : date or None
        Date the podcast covers.

    Returns
    -------
    str
        Markdown content for ``overview.md``.
    """
    lines: list[str] = ["# Podcast Generation Overview\n"]

    # Metadata table
    sections = sorted({a.section for a in articles if a.section})
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    if target_date:
        lines.append(f"| Date | {target_date.isoformat()} |")
    lines.append(f"| Emails processed | {email_count} |")
    lines.append(f"| Articles selected | {len(articles)} |")
    lines.append(f"| Sections | {', '.join(sections) if sections else 'N/A'} |")
    lines.append(f"| Dialogue chunks | {len(chunks)} |")
    lines.append(f"| Links extracted | {link_report.total} |")
    if audio_path:
        lines.append(f"| Audio file | `{audio_path}` |")
    lines.append("")

    # Link breakdown
    if link_report.total > 0:
        lines.append("## Link Breakdown\n")
        lines.append("| Category | Count |")
        lines.append("|---|---|")
        for label, items in (
            ("Source articles", link_report.sources),
            ("Repositories", link_report.repos),
            ("Models", link_report.models),
            ("Papers", link_report.papers),
            ("Other", link_report.other),
        ):
            if items:
                lines.append(f"| {label} | {len(items)} |")
        lines.append("")

    # Token / cost summary
    if token_summary:
        lines.append("## Token Usage & Cost\n")
        lines.append("```")
        lines.append(token_summary)
        lines.append("```\n")

    return "\n".join(lines) + "\n"


def _render_articles(articles: list[Article]) -> str:
    """
    Render the list of selected articles as Markdown.

    Parameters
    ----------
    articles : list[Article]
        Parsed articles included in the podcast.

    Returns
    -------
    str
        Markdown content for ``articles.md``.
    """
    lines: list[str] = ["# Selected Articles\n"]
    current_section = ""

    for article in articles:
        if article.section and article.section != current_section:
            current_section = article.section
            lines.append(f"\n## {current_section}\n")

        lines.append(f"### {article.title}\n")
        if article.url:
            lines.append(f"**Link:** <{article.url}>\n")
        if article.summary:
            lines.append(f"**Summary:** {article.summary}\n")
        if article.full_text and article.full_text != article.summary:
            lines.append("<details>\n<summary>Full text</summary>\n")
            lines.append(f"{article.full_text}\n")
            lines.append("</details>\n")

    return "\n".join(lines) + "\n"


def _render_script(chunks: list[DialogueChunk]) -> str:
    """
    Render the dialogue script as Markdown.

    Parameters
    ----------
    chunks : list[DialogueChunk]
        Dialogue chunks produced by the LLM summariser.

    Returns
    -------
    str
        Markdown content for ``script.md``.
    """
    lines: list[str] = ["# Podcast Script\n"]
    for chunk in chunks:
        lines.append(chunk.text)
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_summary(link_report: LinkReport) -> str:
    """
    Render the synthetic reference sheet as Markdown.

    Parameters
    ----------
    link_report : LinkReport
        Categorised links extracted from the articles.

    Returns
    -------
    str
        Markdown content for ``summary.md``.
    """
    lines: list[str] = ["# Synthetic Summary — Sources & References\n"]

    sections = [
        ("Source Articles", link_report.sources),
        ("GitHub / GitLab Repositories", link_report.repos),
        ("Hugging Face Models", link_report.models),
        ("Academic Papers", link_report.papers),
        ("Other Links", link_report.other),
    ]

    for heading, items in sections:
        if not items:
            continue
        lines.append(f"\n## {heading}\n")
        for link in items:
            lines.append(f"- [{link.label}]({link.url})")

    if link_report.total == 0:
        lines.append("\n*No external links were found in the articles.*\n")

    return "\n".join(lines) + "\n"


def generate_report(
    articles: list[Article],
    chunks: list[DialogueChunk],
    link_report: LinkReport,
    output_dir: str | Path,
    timestamp: str,
    audio_path: Path | str | None = None,
    token_summary: str | None = None,
    email_count: int = 0,
    target_date: date | None = None,
) -> Path:
    """
    Generate a complete report folder for one podcast generation.

    Creates ``<output_dir>/tldr_<timestamp>/`` containing four Markdown
    files: ``overview.md``, ``articles.md``, ``script.md``, and
    ``summary.md``.

    Parameters
    ----------
    articles : list[Article]
        The articles selected for this podcast episode.
    chunks : list[DialogueChunk]
        The dialogue chunks produced by the LLM.
    link_report : LinkReport
        Categorised links extracted from the articles.
    output_dir : str | Path
        Parent directory for output (e.g. ``"output"``).
    timestamp : str
        Timestamp string used to name the report folder
        (e.g. ``"2026-04-03_1430"``).
    audio_path : Path or str or None
        Path to the generated audio file (shown in the overview).
    token_summary : str or None
        Human-readable token/cost summary from the token tracker.
    email_count : int
        Number of source emails processed.
    target_date : date or None
        Date the podcast covers.

    Returns
    -------
    Path
        Path to the created report folder.

    Raises
    ------
    OSError
        If the report directory cannot be created or files cannot be written.

    Examples
    --------
    >>> from pathlib import Path
    >>> report_dir = generate_report(articles, chunks, links, "output", "2026-04-03_1430")
    >>> (report_dir / "overview.md").exists()
    True
    """
    report_dir = Path(output_dir) / f"tldr_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    overview_path = report_dir / "overview.md"
    overview_path.write_text(
        _render_overview(
            articles, chunks, link_report,
            audio_path=audio_path,
            token_summary=token_summary,
            email_count=email_count,
            target_date=target_date,
        ),
        encoding="utf-8",
    )
    logger.info("Written %s", overview_path)

    articles_path = report_dir / "articles.md"
    articles_path.write_text(_render_articles(articles), encoding="utf-8")
    logger.info("Written %s", articles_path)

    script_path = report_dir / "script.md"
    script_path.write_text(_render_script(chunks), encoding="utf-8")
    logger.info("Written %s", script_path)

    summary_path = report_dir / "summary.md"
    summary_path.write_text(_render_summary(link_report), encoding="utf-8")
    logger.info("Written %s", summary_path)

    logger.info("Report folder created: %s", report_dir)
    return report_dir
