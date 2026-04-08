"""
Tests for interest-based article ranking and per-article summarization.

Verifies that rank_articles_by_interest filters and sorts articles by
LLM-assigned interest scores, and that summarize_articles now processes
each article individually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from tldr.llm_summarizer import (
    _parse_rankings,
    rank_articles_by_interest,
    summarize_articles,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeArticle:
    """Minimal article stub for testing."""

    title: str
    url: str
    summary: str
    full_text: str = ""
    interest_score: float = field(default=0.0)


GEMINI_CFG = {
    "api_key": "test-api-key",
    "text_model": "gemini-2.0-flash",
    "summary_model": "gemini-2.0-flash-lite",
}

SAMPLE_ARTICLES = [
    FakeArticle(
        title="GOOGLE LAUNCHES QUANTUM CHIP",
        url="https://example.com/quantum",
        summary="Google unveils a new quantum computing chip with 1000 qubits.",
    ),
    FakeArticle(
        title="MINOR CSS FRAMEWORK UPDATE",
        url="https://example.com/css",
        summary="A small CSS framework releases version 2.1.3 with bug fixes.",
    ),
    FakeArticle(
        title="OPENAI RELEASES GPT-5",
        url="https://example.com/gpt5",
        summary="OpenAI announces GPT-5 with major reasoning improvements.",
    ),
    FakeArticle(
        title="NEW RUST LINTER PLUGIN",
        url="https://example.com/rust",
        summary="A community member publishes a new Rust linter plugin.",
    ),
]


RANKING_RESPONSE = """\
[1] 9 — Major breakthrough in quantum computing with practical implications.
[2] 3 — Minor maintenance release with no significant impact.
[3] 9 — Landmark AI model release affecting the entire industry.
[4] 5 — Useful developer tooling but limited audience.
"""


def _mock_genai_response(text: str):
    """Build a mock genai module whose Client.models.generate_content returns text."""
    mock_response = MagicMock()
    mock_response.text = text

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_client_instance = MagicMock()
    mock_client_instance.models = mock_model

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client_instance

    return mock_genai


# ---------------------------------------------------------------------------
# Tests: _parse_rankings
# ---------------------------------------------------------------------------


class TestParseRankings:
    """Unit tests for _parse_rankings()."""

    def test_parses_valid_rankings(self):
        """Correctly extracts scores from well-formatted ranking output."""
        scores = _parse_rankings(RANKING_RESPONSE, 4)
        assert scores == [9.0, 3.0, 9.0, 5.0]

    def test_missing_entries_default_to_zero(self):
        """Missing article indices get a score of 0.0."""
        partial = "[1] 7 — Good article.\n[3] 4 — Meh."
        scores = _parse_rankings(partial, 4)
        assert scores == [7.0, 0.0, 4.0, 0.0]

    def test_handles_empty_text(self):
        """Empty input returns all zeros."""
        scores = _parse_rankings("", 3)
        assert scores == [0.0, 0.0, 0.0]

    def test_ignores_out_of_range_indices(self):
        """Indices beyond expected_count are silently ignored."""
        text = "[1] 8 — OK.\n[99] 10 — Out of range."
        scores = _parse_rankings(text, 2)
        assert scores == [8.0, 0.0]

    def test_handles_decimal_scores(self):
        """Decimal scores like 7.5 are parsed correctly."""
        text = "[1] 7.5 — Good.\n[2] 3.2 — Meh."
        scores = _parse_rankings(text, 2)
        assert scores == [7.5, 3.2]


# ---------------------------------------------------------------------------
# Tests: rank_articles_by_interest
# ---------------------------------------------------------------------------


class TestRankArticlesByInterest:
    """Unit tests for rank_articles_by_interest()."""

    def test_filters_below_threshold(self):
        """Articles below min_score are excluded."""
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = rank_articles_by_interest(
                list(SAMPLE_ARTICLES), GEMINI_CFG, min_score=5.0,
            )

        titles = [a.title for a in result]
        assert "MINOR CSS FRAMEWORK UPDATE" not in titles
        assert len(result) == 3

    def test_sorts_by_descending_score(self):
        """Kept articles are sorted highest score first."""
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = rank_articles_by_interest(
                list(SAMPLE_ARTICLES), GEMINI_CFG, min_score=1.0,
            )

        scores = [a.interest_score for a in result]
        assert scores == sorted(scores, reverse=True)

    def test_populates_interest_score(self):
        """Each article's interest_score is set by the ranking."""
        articles = [FakeArticle(**a.__dict__) for a in SAMPLE_ARTICLES]
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            rank_articles_by_interest(articles, GEMINI_CFG, min_score=0.0)

        assert articles[0].interest_score == 9.0
        assert articles[1].interest_score == 3.0

    def test_keeps_all_when_threshold_zero(self):
        """With min_score=0, all articles are kept."""
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = rank_articles_by_interest(
                list(SAMPLE_ARTICLES), GEMINI_CFG, min_score=0.0,
            )

        assert len(result) == len(SAMPLE_ARTICLES)

    def test_empty_response_keeps_all(self):
        """If the LLM returns empty text, all articles are kept unchanged."""
        mock_genai = _mock_genai_response("")

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = rank_articles_by_interest(
                list(SAMPLE_ARTICLES), GEMINI_CFG, min_score=5.0,
            )

        assert result == list(SAMPLE_ARTICLES)

    def test_empty_input_returns_empty(self):
        """Empty article list returns empty without API call."""
        result = rank_articles_by_interest([], GEMINI_CFG)
        assert result == []

    def test_uses_selection_model_from_config(self):
        """When selection.model is configured, it is used instead of text_model."""
        cfg = {
            **GEMINI_CFG,
            "selection": {"model": "gemini-2.0-flash-lite"},
        }
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            rank_articles_by_interest(list(SAMPLE_ARTICLES), cfg)

        call_kwargs = mock_genai.Client.return_value.models.generate_content.call_args
        assert call_kwargs.kwargs.get("model") == "gemini-2.0-flash-lite"

    def test_prompt_contains_article_titles(self):
        """The ranking prompt includes all article titles."""
        mock_genai = _mock_genai_response(RANKING_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            rank_articles_by_interest(list(SAMPLE_ARTICLES), GEMINI_CFG)

        call_args = mock_genai.Client.return_value.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[1]
        for article in SAMPLE_ARTICLES:
            assert article.title in prompt


# ---------------------------------------------------------------------------
# Tests: summarize_articles (per-article)
# ---------------------------------------------------------------------------


class TestSummarizeArticlesPerArticle:
    """Unit tests for the per-article summarize_articles()."""

    def test_replaces_full_text_with_summary(self):
        """Each article's full_text is replaced by the LLM summary."""
        articles = [
            FakeArticle(
                title="Test Article",
                url="https://example.com",
                summary="Short summary.",
                full_text="Very long article text that should be replaced.",
            ),
        ]

        mock_response = MagicMock()
        mock_response.text = "This is a concise LLM-generated summary."

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        mock_client_instance = MagicMock()
        mock_client_instance.models = mock_model

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(articles, GEMINI_CFG)

        assert result[0].full_text == "This is a concise LLM-generated summary."

    def test_skips_when_no_summary_model(self):
        """When summary_model is absent, articles are returned unchanged."""
        cfg = {"api_key": "test", "text_model": "gemini-2.0-flash"}
        articles = [
            FakeArticle(title="T", url="u", summary="s", full_text="original"),
        ]

        result = summarize_articles(articles, cfg)
        assert result[0].full_text == "original"

    def test_skips_when_summary_model_equals_text_model(self):
        """When summary_model == text_model, no summarization occurs."""
        cfg = {
            "api_key": "test",
            "text_model": "gemini-2.0-flash",
            "summary_model": "gemini-2.0-flash",
        }
        articles = [
            FakeArticle(title="T", url="u", summary="s", full_text="original"),
        ]

        result = summarize_articles(articles, cfg)
        assert result[0].full_text == "original"

    def test_makes_one_call_per_article(self):
        """Each article triggers an individual API call."""
        articles = [
            FakeArticle(title=f"Article {i}", url=f"url{i}", summary=f"s{i}", full_text=f"text{i}")
            for i in range(3)
        ]

        mock_response = MagicMock()
        mock_response.text = "Summary."

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        mock_client_instance = MagicMock()
        mock_client_instance.models = mock_model

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        with patch("tldr.llm_summarizer.genai", mock_genai):
            summarize_articles(articles, GEMINI_CFG)

        assert mock_model.generate_content.call_count == 3

    def test_keeps_original_on_empty_response(self):
        """If the LLM returns empty text for an article, original text is kept."""
        articles = [
            FakeArticle(title="T", url="u", summary="s", full_text="original text"),
        ]

        mock_response = MagicMock()
        mock_response.text = ""

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        mock_client_instance = MagicMock()
        mock_client_instance.models = mock_model

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client_instance

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(articles, GEMINI_CFG)

        assert result[0].full_text == "original text"
