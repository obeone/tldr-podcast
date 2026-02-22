"""
Tests for the llm_summarizer module.

Verifies dialogue generation and chunking behaviour with a mocked Gemini
client.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from tldr.llm_summarizer import DialogueChunk, generate_dialogue


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


GEMINI_CFG = {
    "api_key": "test-api-key",
    "text_model": "gemini-2.0-flash",
    "tts_model": "gemini-2.5-flash-preview-tts",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

SAMPLE_ARTICLES = [
    FakeArticle(
        title="Rust hits 1.0 stability milestone",
        url="https://example.com/rust",
        summary="Rust language announces major stability improvements.",
        full_text="Rust language announces major stability improvements in version 1.0.",
    ),
    FakeArticle(
        title="Python 4.0 preview released",
        url="https://example.com/python",
        summary="Python releases a preview of the upcoming 4.0 version.",
        full_text="Python releases a preview of the upcoming 4.0 version with many new features.",
    ),
]

SHORT_DIALOGUE = """\
Alex: Hey Jordan, ready to dive into today's TLDR?
Jordan: Absolutely! What caught your eye?
Alex: There's a fascinating piece about Rust hitting stability milestones.
Jordan: Oh interesting! Tell me more.
Alex: The language team says performance improved by 40 percent.
Jordan: That's huge for systems programming.
"""


def _make_long_dialogue(speaker1: str = "Alex", speaker2: str = "Jordan", turns: int = 60) -> str:
    """
    Build a dialogue string whose total UTF-8 byte size exceeds 4000 bytes.

    Each turn is ~80 bytes, so 60 turns ≈ 4800 bytes.

    Parameters
    ----------
    speaker1 : str
        Name of the first speaker.
    speaker2 : str
        Name of the second speaker.
    turns : int
        Total number of speaker turns to generate.

    Returns
    -------
    str
        Multi-line dialogue string.
    """
    lines: list[str] = []
    for i in range(turns):
        speaker = speaker1 if i % 2 == 0 else speaker2
        # ~80 chars per line -> ~80 bytes (ASCII)
        lines.append(f"{speaker}: This is turn number {i:03d}, discussing a very interesting topic here.")
    return "\n".join(lines)


def _mock_genai_response(text: str):
    """
    Build a mock genai module whose Client.models.generate_content returns text.

    Parameters
    ----------
    text : str
        The dialogue text the mock should return.

    Returns
    -------
    MagicMock
        A mock that mimics the genai module interface.
    """
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
# Tests
# ---------------------------------------------------------------------------


class TestGenerateDialogue:
    """Unit tests for generate_dialogue()."""

    def test_returns_non_empty_list_of_chunks(self):
        """generate_dialogue returns at least one DialogueChunk on success."""
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            chunks = generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert all(isinstance(c, DialogueChunk) for c in chunks)

    def test_chunks_contain_text(self):
        """Every returned DialogueChunk has non-empty text."""
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            chunks = generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        for chunk in chunks:
            assert chunk.text.strip() != ""

    def test_chunks_have_sequential_indices(self):
        """DialogueChunk objects are indexed sequentially from 0."""
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            chunks = generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        for expected_index, chunk in enumerate(chunks):
            assert chunk.index == expected_index

    def test_all_chunks_within_byte_limit(self):
        """All chunks must be ≤ 3800 UTF-8 bytes when dialogue exceeds 4000 bytes."""
        long_dialogue = _make_long_dialogue(turns=60)
        # Verify the mock dialogue is actually > 4000 bytes
        assert len(long_dialogue.encode("utf-8")) > 4000, (
            "Test prerequisite: mock dialogue must exceed 4000 UTF-8 bytes"
        )

        mock_genai = _mock_genai_response(long_dialogue)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            chunks = generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        assert len(chunks) > 1, "Long dialogue should produce multiple chunks"

        for chunk in chunks:
            byte_size = len(chunk.text.encode("utf-8"))
            assert byte_size <= 3800, (
                f"Chunk {chunk.index} exceeds 3800 bytes: {byte_size} bytes\n"
                f"Chunk text:\n{chunk.text}"
            )

    def test_genai_client_called_with_correct_model(self):
        """Gemini client is called with the model specified in gemini_cfg."""
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        mock_genai.Client.assert_called_once_with(api_key="test-api-key")
        mock_client = mock_genai.Client.return_value
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs.get("model") == "gemini-2.0-flash" or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == "gemini-2.0-flash"
        )
