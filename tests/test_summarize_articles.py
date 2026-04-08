"""Tests for the summarize_articles() per-article summarization."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from tldr.llm_summarizer import summarize_articles


@dataclass
class FakeArticle:
    """Minimal article stub for testing."""

    title: str
    url: str
    summary: str
    full_text: str = ""


GEMINI_CFG_WITH_SUMMARY = {
    "api_key": "test-api-key",
    "text_model": "gemini-3-flash-preview",
    "summary_model": "gemini-2.0-flash-lite",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

GEMINI_CFG_NO_SUMMARY = {
    "api_key": "test-api-key",
    "text_model": "gemini-2.0-flash",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

GEMINI_CFG_SAME_MODEL = {
    "api_key": "test-api-key",
    "text_model": "gemini-2.0-flash",
    "summary_model": "gemini-2.0-flash",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

SAMPLE_ARTICLES = [
    FakeArticle(
        title="Rust 2.0 released",
        url="https://example.com/rust",
        summary="Rust 2.0 is out.",
        full_text="Rust 2.0 has been released with major improvements to the borrow checker.",
    ),
    FakeArticle(
        title="Python adds pattern matching",
        url="https://example.com/python",
        summary="Python gets match.",
        full_text="Python 3.14 introduces advanced pattern matching syntax.",
    ),
]

# Per-article mode: the LLM returns plain summary text (no [N] prefix).
SUMMARY_RESPONSE = "Concise LLM-generated summary of the article."


def _mock_genai_response(text):
    mock_response = MagicMock()
    mock_response.text = text
    mock_response.usage_metadata = MagicMock()
    mock_response.usage_metadata.prompt_token_count = 500
    mock_response.usage_metadata.candidates_token_count = 100

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_client = MagicMock()
    mock_client.models = mock_model

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client

    return mock_genai


class TestSummarizeArticles:
    """Tests for summarize_articles() (per-article mode)."""

    def test_replaces_full_text_with_summaries(self):
        """Each article's full_text is replaced with the LLM-generated summary."""
        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(SAMPLE_ARTICLES, GEMINI_CFG_WITH_SUMMARY)

        for article in result:
            assert article.full_text == SUMMARY_RESPONSE

    def test_uses_summary_model_not_text_model(self):
        """The API must be called with the summary_model, not text_model."""
        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            summarize_articles(SAMPLE_ARTICLES, GEMINI_CFG_WITH_SUMMARY)

        call_kwargs = mock_genai.Client.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.0-flash-lite"

    def test_skips_when_no_summary_model(self):
        """When summary_model is absent, articles are returned unchanged."""
        articles = [
            FakeArticle(title="Test", url="http://x", summary="s", full_text="original"),
        ]
        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(articles, GEMINI_CFG_NO_SUMMARY)

        assert result[0].full_text == "original"
        mock_genai.Client.return_value.models.generate_content.assert_not_called()

    def test_skips_when_same_model(self):
        """When summary_model equals text_model, no summarization happens."""
        articles = [
            FakeArticle(title="Test", url="http://x", summary="s", full_text="original"),
        ]
        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(articles, GEMINI_CFG_SAME_MODEL)

        assert result[0].full_text == "original"
        mock_genai.Client.return_value.models.generate_content.assert_not_called()

    def test_records_token_usage_per_article(self):
        """Token usage is recorded once per article (one call each)."""
        from tldr.token_tracker import TokenTracker

        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)
        tracker = TokenTracker()

        with patch("tldr.llm_summarizer.genai", mock_genai):
            summarize_articles(
                SAMPLE_ARTICLES, GEMINI_CFG_WITH_SUMMARY, token_tracker=tracker,
            )

        assert tracker._usage["gemini-2.0-flash-lite"].calls == len(SAMPLE_ARTICLES)

    def test_makes_one_api_call_per_article(self):
        """Each article triggers exactly one API call."""
        mock_genai = _mock_genai_response(SUMMARY_RESPONSE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            summarize_articles(SAMPLE_ARTICLES, GEMINI_CFG_WITH_SUMMARY)

        assert mock_genai.Client.return_value.models.generate_content.call_count == len(SAMPLE_ARTICLES)

    def test_keeps_original_on_empty_response(self):
        """If the LLM returns empty text, articles keep their original full_text."""
        articles = [
            FakeArticle(title="Test", url="http://x", summary="s", full_text="original"),
        ]
        mock_genai = _mock_genai_response("")

        with patch("tldr.llm_summarizer.genai", mock_genai):
            result = summarize_articles(articles, GEMINI_CFG_WITH_SUMMARY)

        assert result[0].full_text == "original"
