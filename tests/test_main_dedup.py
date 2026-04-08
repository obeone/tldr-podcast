"""Tests for the _dedup_articles helper in cli.py."""
from __future__ import annotations

from tldr.cli import _dedup_articles
from tldr.email_parser import Article


def _make_article(title: str, url: str = "https://example.com") -> Article:
    """Create a minimal Article for testing purposes."""
    return Article(title=title, summary="s", url=url, section="SEC", full_text="")


class TestDedupArticles:
    """Tests for _dedup_articles."""

    def test_keeps_all_articles_when_no_duplicates(self):
        articles = [
            _make_article("FIRST ARTICLE"),
            _make_article("SECOND ARTICLE"),
        ]
        result = _dedup_articles(articles)
        assert len(result) == 2

    def test_removes_exact_duplicate_title(self):
        articles = [
            _make_article("SAME TITLE", "https://a.com"),
            _make_article("SAME TITLE", "https://b.com"),
        ]
        result = _dedup_articles(articles)
        assert len(result) == 1
        assert result[0].url == "https://a.com"  # first wins

    def test_dedup_is_case_insensitive(self):
        articles = [
            _make_article("How Will Openai Compete?"),
            _make_article("HOW WILL OPENAI COMPETE?"),
        ]
        result = _dedup_articles(articles)
        assert len(result) == 1

    def test_dedup_ignores_extra_whitespace(self):
        articles = [
            _make_article("TITLE  WITH  SPACES"),
            _make_article("TITLE WITH SPACES"),
        ]
        result = _dedup_articles(articles)
        assert len(result) == 1

    def test_preserves_order_of_first_occurrences(self):
        articles = [
            _make_article("ALPHA"),
            _make_article("BETA"),
            _make_article("ALPHA"),  # dup of first
            _make_article("GAMMA"),
        ]
        result = _dedup_articles(articles)
        assert [a.title for a in result] == ["ALPHA", "BETA", "GAMMA"]

    def test_empty_list_returns_empty(self):
        assert _dedup_articles([]) == []
