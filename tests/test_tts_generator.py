"""Tests for src/tldr/tts_generator.py."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch


from tldr.tts_generator import generate_audio_chunks


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeChunk:
    """Minimal stand-in for DialogueChunk used in tests."""

    text: str
    index: int


FAKE_CFG = {
    "api_key": "test-api-key",
    "tts_model": "gemini-2.5-flash-preview-tts",
    "speaker1": {"name": "Alex", "voice": "Puck"},
    "speaker2": {"name": "Jordan", "voice": "Charon"},
}

FAKE_PCM_1 = b"\x00\x01\x02\x03" * 100
FAKE_PCM_2 = b"\x10\x11\x12\x13" * 200


def _make_response(pcm: bytes) -> MagicMock:
    """Build a mock Gemini response containing raw PCM bytes."""
    inline_data = MagicMock()
    inline_data.data = pcm

    part = MagicMock()
    part.inline_data = inline_data

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGenerateAudioChunks:
    """generate_audio_chunks must call TTS once per chunk and return bytes."""

    def test_returns_list_of_bytes_one_per_chunk(self) -> None:
        chunks = [
            FakeChunk(text="Alex: Hello!\nJordan: Hi there!", index=0),
            FakeChunk(text="Alex: Let's talk tech.\nJordan: Sure!", index=1),
        ]

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _make_response(FAKE_PCM_1),
            _make_response(FAKE_PCM_2),
        ]

        with patch("tldr.tts_generator.genai.Client", return_value=mock_client):
            result = generate_audio_chunks(chunks, FAKE_CFG)

        assert len(result) == 2
        assert isinstance(result[0], bytes)
        assert isinstance(result[1], bytes)

    def test_each_bytes_object_matches_mocked_audio_data(self) -> None:
        chunks = [
            FakeChunk(text="Alex: First chunk.", index=0),
            FakeChunk(text="Jordan: Second chunk.", index=1),
        ]

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _make_response(FAKE_PCM_1),
            _make_response(FAKE_PCM_2),
        ]

        with patch("tldr.tts_generator.genai.Client", return_value=mock_client):
            result = generate_audio_chunks(chunks, FAKE_CFG)

        assert result[0] == FAKE_PCM_1
        assert result[1] == FAKE_PCM_2

    def test_empty_chunks_returns_empty_list(self) -> None:
        mock_client = MagicMock()
        with patch("tldr.tts_generator.genai.Client", return_value=mock_client):
            result = generate_audio_chunks([], FAKE_CFG)

        assert result == []
        mock_client.models.generate_content.assert_not_called()

    def test_single_chunk_calls_api_once(self) -> None:
        chunks = [FakeChunk(text="Alex: Single.", index=0)]

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_response(FAKE_PCM_1)

        with patch("tldr.tts_generator.genai.Client", return_value=mock_client):
            result = generate_audio_chunks(chunks, FAKE_CFG)

        mock_client.models.generate_content.assert_called_once()
        assert result == [FAKE_PCM_1]

    def test_uses_configured_tts_model(self) -> None:
        chunks = [FakeChunk(text="Alex: Test.", index=0)]
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _make_response(FAKE_PCM_1)

        with patch("tldr.tts_generator.genai.Client", return_value=mock_client):
            generate_audio_chunks(chunks, FAKE_CFG)

        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == FAKE_CFG["tts_model"]


class TestBuildTtsPromptPace:
    """Tests for _build_tts_prompt pace configuration."""

    def test_configured_pace_appears_in_preamble(self):
        """When tts_style.pace is set, it must appear in the preamble."""
        from tldr.tts_generator import _build_tts_prompt

        cfg = {
            **FAKE_CFG,
            "tts_style": {"pace": "slow and deliberate"},
        }
        result = _build_tts_prompt("Alex: Hello!", cfg)
        assert "slow and deliberate" in result

    def test_default_pace_used_when_not_configured(self):
        """When tts_style.pace is absent, a sensible default pace is still present."""
        from tldr.tts_generator import _build_tts_prompt

        result = _build_tts_prompt("Alex: Hello!", FAKE_CFG)
        # Must still contain a pace instruction of some kind
        assert "pace" in result.lower()

    def test_preamble_precedes_dialogue(self):
        """The dialogue text must appear after the preamble in the output."""
        from tldr.tts_generator import _build_tts_prompt

        cfg = {**FAKE_CFG, "tts_style": {"pace": "slow and deliberate"}}
        dialogue = "Alex: Bonjour tout le monde!"
        result = _build_tts_prompt(dialogue, cfg)
        assert result.index("slow and deliberate") < result.index(dialogue)
