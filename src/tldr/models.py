"""
Shared data models for the TLDR Podcast tool.

Holds dataclasses used across multiple pipeline stages so that producer
modules (web source) and consumer modules (scraper, summariser, report)
can depend on a single, neutral location.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Article:
    """
    Represents a single article extracted from a TLDR newsletter page.

    Attributes
    ----------
    title : str
        The article title as it appears in the newsletter.
    summary : str
        The short summary paragraph below the article title.
    url : str
        The resolved URL of the source article.
    section : str
        The theme section the article belongs to (e.g. ``"Attacks & Vulnerabilities"``).
    full_text : str
        Full article body fetched by the scraper (populated later, default ``""``).
    interest_score : float
        LLM-assigned interest score (1–10). Populated by
        :func:`~tldr.llm_summarizer.rank_articles_by_interest`, default ``0.0``.
    """

    title: str
    summary: str
    url: str
    section: str
    full_text: str = field(default="")
    interest_score: float = field(default=0.0)
