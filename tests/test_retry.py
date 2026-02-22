"""
Tests for the retry module and its integration with llm_summarizer / tts_generator.

Verifies that transient ServerError failures are retried transparently and that
non-retryable errors are re-raised immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, call, patch

import pytest
from google.genai import errors as genai_errors
from tenacity import RetryError

from tldr.llm_summarizer import generate_dialogue
from tldr.tts_generator import generate_audio_chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server_error(code: int = 503) -> genai_errors.ServerError:
    """Build a ServerError with the given HTTP status code."""
    return genai_errors.ServerError(code, {"error": {"code": code, "status": "UNAVAILABLE", "message": "overloaded"}})


def _make_client_error(code: int = 400) -> genai_errors.ClientError:
    """Build a ClientError with the given HTTP status code."""
    return genai_errors.ClientError(code, {"error": {"code": code, "status": "BAD_REQUEST", "message": "bad"}})


@dataclass
class FakeArticle:
    """Minimal article stub."""

    title: str = "Test Article"
    url: str = "https://example.com"
    summary: str = "Summary text"
    full_text: str = "Full text content."


@dataclass
class FakeChunk:
    """Minimal dialogue-chunk stub."""

    text: str = "Alex: Hello.\nJordan: Hi!"
    index: int = 0


GEMINI_CFG = {
    "api_key": "test-key",
    "text_model": "gemini-2.0-flash",
    "tts_model": "gemini-2.5-flash-preview-tts",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

DIALOGUE_RESPONSE = "Alex: Hey!\nJordan: Hello!"


# ---------------------------------------------------------------------------
# llm_summarizer retry tests
# ---------------------------------------------------------------------------


class TestLLMSummarizerRetry:
    """Retry behaviour for generate_dialogue."""

    @patch("tldr.llm_summarizer.genai.Client")
    def test_retries_on_server_error_then_succeeds(self, mock_client_cls: MagicMock) -> None:
        """A single 503 should be retried and the successful response returned."""
        mock_response = MagicMock()
        mock_response.text = DIALOGUE_RESPONSE

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = [
            _make_server_error(503),
            mock_response,
        ]
        mock_client_cls.return_value.models = mock_model

        chunks = generate_dialogue([FakeArticle()], GEMINI_CFG, "Alex", "Jordan")

        assert len(chunks) > 0
        assert mock_model.generate_content.call_count == 2

    @patch("tldr.llm_summarizer.genai.Client")
    def test_gives_up_after_max_attempts(self, mock_client_cls: MagicMock) -> None:
        """Persistent 503s should exhaust retries and raise the ServerError."""
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = _make_server_error(503)
        mock_client_cls.return_value.models = mock_model

        with pytest.raises(genai_errors.ServerError):
            generate_dialogue([FakeArticle()], GEMINI_CFG, "Alex", "Jordan")

        assert mock_model.generate_content.call_count == 5  # _MAX_ATTEMPTS

    @patch("tldr.llm_summarizer.genai.Client")
    def test_does_not_retry_client_error(self, mock_client_cls: MagicMock) -> None:
        """A 4xx ClientError must NOT be retried — fail fast."""
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = _make_client_error(400)
        mock_client_cls.return_value.models = mock_model

        with pytest.raises(genai_errors.ClientError):
            generate_dialogue([FakeArticle()], GEMINI_CFG, "Alex", "Jordan")

        assert mock_model.generate_content.call_count == 1


# ---------------------------------------------------------------------------
# tts_generator retry tests
# ---------------------------------------------------------------------------


class TestTTSGeneratorRetry:
    """Retry behaviour for generate_audio_chunks."""

    def _make_tts_response(self, data: bytes) -> MagicMock:
        """Build a mock TTS response containing *data* as inline PCM bytes."""
        part = MagicMock()
        part.inline_data.data = data
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        response = MagicMock()
        response.candidates = [candidate]
        return response

    @patch("tldr.tts_generator.genai.Client")
    def test_retries_on_server_error_then_succeeds(self, mock_client_cls: MagicMock) -> None:
        """A single 503 during TTS should be retried and audio returned."""
        pcm = b"\x00\x01" * 100
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = [
            _make_server_error(503),
            self._make_tts_response(pcm),
        ]
        mock_client_cls.return_value.models = mock_model

        result = generate_audio_chunks([FakeChunk()], GEMINI_CFG)

        assert result == [pcm]
        assert mock_model.generate_content.call_count == 2

    @patch("tldr.tts_generator.genai.Client")
    def test_gives_up_after_max_attempts(self, mock_client_cls: MagicMock) -> None:
        """Persistent 503s on TTS should exhaust retries and raise ServerError."""
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = _make_server_error(503)
        mock_client_cls.return_value.models = mock_model

        with pytest.raises(genai_errors.ServerError):
            generate_audio_chunks([FakeChunk()], GEMINI_CFG)

        assert mock_model.generate_content.call_count == 5

    @patch("tldr.tts_generator.genai.Client")
    def test_does_not_retry_client_error(self, mock_client_cls: MagicMock) -> None:
        """A 4xx ClientError during TTS must NOT be retried."""
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = _make_client_error(400)
        mock_client_cls.return_value.models = mock_model

        with pytest.raises(genai_errors.ClientError):
            generate_audio_chunks([FakeChunk()], GEMINI_CFG)

        assert mock_model.generate_content.call_count == 1

    @patch("tldr.tts_generator.genai.Client")
    def test_retries_per_chunk_independently(self, mock_client_cls: MagicMock) -> None:
        """Each chunk gets its own retry budget — a 503 on chunk 1 does not burn chunk 2's retries."""
        pcm1 = b"\xAA" * 50
        pcm2 = b"\xBB" * 50
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = [
            _make_server_error(503),  # chunk 0, attempt 1 — fails
            self._make_tts_response(pcm1),  # chunk 0, attempt 2 — succeeds
            self._make_tts_response(pcm2),  # chunk 1, attempt 1 — succeeds
        ]
        mock_client_cls.return_value.models = mock_model

        chunks = [FakeChunk(text="Alex: One.", index=0), FakeChunk(text="Jordan: Two.", index=1)]
        result = generate_audio_chunks(chunks, GEMINI_CFG)

        assert result == [pcm1, pcm2]
        assert mock_model.generate_content.call_count == 3
