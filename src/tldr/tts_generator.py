"""
Text-to-Speech generator using Gemini's multi-speaker TTS API.

Each :class:`~tldr.llm_summarizer.DialogueChunk` is converted into raw PCM
audio bytes (24 kHz, mono, 16-bit signed little-endian) by calling the
configured Gemini TTS model with a :class:`~google.genai.types.MultiSpeakerVoiceConfig`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import coloredlogs
from google import genai
from google.genai import types

from tldr.retry import gemini_retry

if TYPE_CHECKING:
    from tldr.llm_summarizer import DialogueChunk

logger = logging.getLogger(__name__)
coloredlogs.install(level="DEBUG", logger=logger)


def generate_audio_chunks(
    chunks: list[DialogueChunk],
    gemini_cfg: dict,
) -> list[bytes]:
    """
    Convert a list of dialogue chunks into raw PCM audio bytes using Gemini TTS.

    Each chunk is sent as a separate TTS request so that the 4 000-byte text
    limit is respected.  The resulting audio data for each chunk is returned in
    the same order as the input.

    Parameters
    ----------
    chunks : list[DialogueChunk]
        Ordered dialogue chunks produced by :func:`~tldr.llm_summarizer.generate_dialogue`.
    gemini_cfg : dict
        Resolved Gemini configuration with keys:
        ``api_key``, ``tts_model``, ``speaker1`` ({``name``, ``voice``}),
        ``speaker2`` ({``name``, ``voice``}).

    Returns
    -------
    list[bytes]
        One PCM bytes object per input chunk, in the same order.
        Each bytes object is raw 24 kHz / mono / 16-bit signed little-endian audio.

    Raises
    ------
    RuntimeError
        If the Gemini API returns an unexpected response structure for a chunk.

    Examples
    --------
    >>> audio_chunks = generate_audio_chunks(dialogue_chunks, gemini_cfg)
    >>> len(audio_chunks) == len(dialogue_chunks)
    True
    """
    client = genai.Client(api_key=gemini_cfg["api_key"])

    speaker1_cfg = gemini_cfg["speaker1"]
    speaker2_cfg = gemini_cfg["speaker2"]

    voice_config = types.MultiSpeakerVoiceConfig(
        speaker_voice_configs=[
            types.SpeakerVoiceConfig(
                speaker=speaker1_cfg["name"],
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=speaker1_cfg["voice"],
                    )
                ),
            ),
            types.SpeakerVoiceConfig(
                speaker=speaker2_cfg["name"],
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=speaker2_cfg["voice"],
                    )
                ),
            ),
        ]
    )

    audio_chunks: list[bytes] = []

    for chunk in chunks:
        logger.debug(
            "Generating TTS audio for chunk %d (%d bytes of text)",
            chunk.index,
            len(chunk.text.encode("utf-8")),
        )

        @gemini_retry
        def _call_tts() -> bytes:
            response = client.models.generate_content(
                model=gemini_cfg["tts_model"],
                contents=chunk.text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        multi_speaker_voice_config=voice_config,
                    ),
                ),
            )
            try:
                return response.candidates[0].content.parts[0].inline_data.data
            except (IndexError, AttributeError) as exc:
                raise RuntimeError(
                    f"Unexpected TTS response structure for chunk {chunk.index}: {exc}"
                ) from exc

        pcm_bytes: bytes = _call_tts()

        logger.info(
            "Chunk %d: received %d bytes of PCM audio",
            chunk.index,
            len(pcm_bytes),
        )
        audio_chunks.append(pcm_bytes)

    return audio_chunks
