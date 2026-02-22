"""Tests for src/tldr/audio_exporter.py."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from pydub import AudioSegment

from tldr.audio_exporter import export_audio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RATE = 24_000
_SAMPLE_WIDTH = 2  # 16-bit
_CHANNELS = 1


def _make_pcm(duration_ms: int = 100) -> bytes:
    """Generate silent raw PCM bytes for the given duration."""
    n_frames = int(_SAMPLE_RATE * duration_ms / 1000)
    # 16-bit silence = 0x0000 repeated
    return struct.pack(f"<{n_frames}h", *([0] * n_frames))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportAudio:
    """export_audio must write valid audio files in various formats."""

    def test_creates_mp3_file_of_nonzero_size(self, tmp_path: Path) -> None:
        pcm = _make_pcm(200)
        out = tmp_path / "episode.mp3"
        result = export_audio([pcm], out, fmt="mp3")
        assert result.exists()
        assert result.stat().st_size > 0

    def test_creates_wav_file(self, tmp_path: Path) -> None:
        pcm = _make_pcm(200)
        out = tmp_path / "episode.wav"
        result = export_audio([pcm], out, fmt="wav")
        assert result.exists()
        assert result.stat().st_size > 0

    def test_creates_parent_directories_automatically(self, tmp_path: Path) -> None:
        pcm = _make_pcm(100)
        out = tmp_path / "deep" / "nested" / "dir" / "episode.mp3"
        result = export_audio([pcm], out, fmt="mp3")
        assert result.exists()
        assert (tmp_path / "deep" / "nested" / "dir").is_dir()

    def test_multiple_chunks_are_concatenated(self, tmp_path: Path) -> None:
        pcm1 = _make_pcm(100)
        pcm2 = _make_pcm(200)
        out = tmp_path / "combined.wav"
        result = export_audio([pcm1, pcm2], out, fmt="wav")
        # Combined duration should be ~300 ms
        seg = AudioSegment.from_wav(str(result))
        assert seg.duration_seconds > 0.25

    def test_raises_value_error_when_chunks_empty(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.mp3"
        with pytest.raises(ValueError, match="empty"):
            export_audio([], out)

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        pcm = _make_pcm(50)
        out = tmp_path / "ep.wav"
        result = export_audio([pcm], out, fmt="wav")
        assert result.is_absolute()
