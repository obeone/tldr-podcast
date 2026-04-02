"""
Report generator for TLDR podcast runs.

Creates a timestamped output folder for each generation containing:

- ``articles.md`` — selected articles with title, section, URL, and summary.
- ``script.md`` — the full two-host dialogue script.
- ``summary.md`` — synthetic reference sheet with categorised links to all
  sources, repositories, models, and papers mentioned in the articles.

The heavy lifting for link categorisation is delegated to
:mod:`tldr.link_extractor`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tldr.email_parser import Article
    from tldr.link_extractor import LinkReport
    from tldr.llm_summarizer import DialogueChunk

logger = logging.getLogger(__name__)


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
            lines.append(f"{article.summary}\n")

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
) -> Path:
    """
    Generate a complete report folder for one podcast generation.

    Creates ``<output_dir>/tldr_<timestamp>/`` containing three Markdown
    files: ``articles.md``, ``script.md``, and ``summary.md``.

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
    >>> (report_dir / "articles.md").exists()
    True
    """
    report_dir = Path(output_dir) / f"tldr_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

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
