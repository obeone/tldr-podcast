"""
LLM-based dialogue generator for the TLDR Podcast tool.

Uses Google Gemini Flash to turn a list of newsletter articles into a
conversational two-host podcast dialogue, then splits the output into
byte-size-bounded chunks ready for TTS processing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google import genai

from tldr.retry import gemini_retry

logger = logging.getLogger(__name__)

# Maximum UTF-8 byte size for a single dialogue chunk sent to TTS.
# Set to 3000 to leave ~600-800 bytes of headroom for the TTS preamble
# that tts_generator.py prepends before sending to the Gemini TTS API.
_MAX_CHUNK_BYTES = 3000

_SYSTEM_PROMPT_TEMPLATE = """\
You are a podcast script writer. Your job is to create an engaging, \
conversational podcast dialogue between two hosts: {speaker1} and {speaker2}.

Host personalities:
- {speaker1}: {speaker1_personality}
- {speaker2}: {speaker2_personality}

Instructions:
- Review all the articles provided below.
- Select the 5 to 8 most interesting or significant articles.
- Discuss them in a natural, conversational style between {speaker1} and {speaker2}.
- Cover each selected article briefly: what it is about and why it matters.
- Keep the tone informative but lively — like two curious friends catching up on tech news.
- The entire dialogue MUST be written in French.
- Reflect each host's personality in their speaking style and reactions.
- Add inline emotional cues in parentheses within the dialogue text to guide \
delivery (e.g., "(avec enthousiasme)", "(sceptique)", "(en accélérant)", \
"(surpris)"). Vary them naturally according to the content.
- Use shorter sentences for excitement, longer ones for analysis.

STRICT OUTPUT FORMAT:
- Each line must follow exactly this pattern: SpeakerName: dialogue text
- Alternate between {speaker1} and {speaker2}.
- Emotional cues go INSIDE the dialogue text, never in the SpeakerName prefix.
- Do NOT add blank lines between turns.
- Do NOT add any introduction or conclusion outside of the dialogue format.

Example output format:
{speaker1}: (avec enthousiasme) Incroyable, {speaker2} ! Google vient d'annoncer quelque chose qui change tout !
{speaker2}: Mouais... (sceptique) on a déjà entendu ça. Qu'est-ce qui est vraiment nouveau cette fois ?
{speaker1}: Eh bien, (en accélérant) ils ont réussi à réduire la latence de moitié !
{speaker2}: (impressionné malgré lui) D'accord, ça c'est concret. Mais quelles sont les implications ?

Articles:
{articles}
"""


def _build_prompt(
    articles: list,
    speaker1_name: str,
    speaker2_name: str,
    speaker1_personality: str = "",
    speaker2_personality: str = "",
) -> str:
    """
    Build the full LLM prompt from the article list, speaker names, and personalities.

    Parameters
    ----------
    articles : list
        A list of article-like objects with ``title``, ``url``, and
        ``full_text`` (or ``summary``) attributes.
    speaker1_name : str
        Display name of the first podcast host.
    speaker2_name : str
        Display name of the second podcast host.
    speaker1_personality : str, optional
        Short description of the first host's personality and speaking style.
    speaker2_personality : str, optional
        Short description of the second host's personality and speaking style.

    Returns
    -------
    str
        The formatted prompt string ready to send to Gemini.
    """
    article_blocks: list[str] = []
    for i, article in enumerate(articles, start=1):
        text = getattr(article, "full_text", "") or getattr(article, "summary", "")
        title = getattr(article, "title", f"Article {i}")
        url = getattr(article, "url", "")
        block = f"[{i}] {title}\nURL: {url}\n{text}"
        article_blocks.append(block)

    articles_text = "\n\n".join(article_blocks)

    return _SYSTEM_PROMPT_TEMPLATE.format(
        speaker1=speaker1_name,
        speaker2=speaker2_name,
        speaker1_personality=speaker1_personality or "enthusiastic and curious",
        speaker2_personality=speaker2_personality or "analytical and thoughtful",
        articles=articles_text,
    )


def _split_dialogue_into_chunks(
    dialogue_text: str,
    speaker1_name: str,
    speaker2_name: str,
    max_bytes: int = _MAX_CHUNK_BYTES,
) -> list[DialogueChunk]:
    """
    Split a raw dialogue string into byte-bounded chunks.

    Chunks are split only at speaker-turn boundaries (lines beginning with
    a speaker name followed by a colon).  A turn is never split mid-line.

    Parameters
    ----------
    dialogue_text : str
        The raw multi-line dialogue produced by the LLM.
    speaker1_name : str
        Name of the first speaker, used to detect turn boundaries.
    speaker2_name : str
        Name of the second speaker, used to detect turn boundaries.
    max_bytes : int, optional
        Maximum UTF-8 byte size per chunk, by default 3000.

    Returns
    -------
    list[DialogueChunk]
        Ordered list of dialogue chunks, each within the byte limit.
    """
    speaker_prefixes = (
        f"{speaker1_name}:",
        f"{speaker2_name}:",
    )

    # Collect turn lines (lines that start a new speaker turn) and their
    # accumulated content (a turn may theoretically span multiple lines,
    # though the prompt requests one line per turn).
    turns: list[str] = []
    current_lines: list[str] = []

    for line in dialogue_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_new_turn = any(stripped.startswith(prefix) for prefix in speaker_prefixes)
        if is_new_turn and current_lines:
            turns.append("\n".join(current_lines))
            current_lines = [stripped]
        else:
            current_lines.append(stripped)

    if current_lines:
        turns.append("\n".join(current_lines))

    logger.debug("Dialogue split into %d speaker turns.", len(turns))

    # Pack turns into byte-bounded chunks
    chunks: list[DialogueChunk] = []
    current_chunk_lines: list[str] = []
    current_size = 0

    for turn in turns:
        turn_bytes = len(turn.encode("utf-8")) + 1  # +1 for newline separator

        if current_chunk_lines and current_size + turn_bytes > max_bytes:
            # Flush current chunk
            chunk_text = "\n".join(current_chunk_lines)
            chunks.append(DialogueChunk(text=chunk_text, index=len(chunks)))
            logger.debug(
                "Chunk %d created: %d UTF-8 bytes.",
                len(chunks) - 1,
                len(chunk_text.encode("utf-8")),
            )
            current_chunk_lines = [turn]
            current_size = turn_bytes
        else:
            current_chunk_lines.append(turn)
            current_size += turn_bytes

    if current_chunk_lines:
        chunk_text = "\n".join(current_chunk_lines)
        chunks.append(DialogueChunk(text=chunk_text, index=len(chunks)))
        logger.debug(
            "Chunk %d created: %d UTF-8 bytes.",
            len(chunks) - 1,
            len(chunk_text.encode("utf-8")),
        )

    logger.info("Dialogue split into %d chunk(s).", len(chunks))
    return chunks


@dataclass
class DialogueChunk:
    """
    A byte-size-bounded segment of podcast dialogue.

    Attributes
    ----------
    text : str
        The raw dialogue text for this chunk.
    index : int
        Zero-based position of this chunk in the full dialogue sequence.
    """

    text: str
    index: int


def generate_dialogue(
    articles: list,
    gemini_cfg: dict,
    speaker1_name: str,
    speaker2_name: str,
) -> list[DialogueChunk]:
    """
    Generate a two-host podcast dialogue from a list of articles.

    Sends all articles to Gemini Flash and parses the resulting dialogue
    into byte-bounded :class:`DialogueChunk` objects suitable for TTS.

    Parameters
    ----------
    articles : list
        A list of article-like objects with ``title``, ``url``, and
        ``full_text`` / ``summary`` attributes.
    gemini_cfg : dict
        Resolved Gemini configuration section from the YAML config, containing
        at minimum ``api_key`` and ``text_model`` keys.  Optional keys:
        ``speaker1.personality`` and ``speaker2.personality`` strings used
        to personalise each host's speaking style.
    speaker1_name : str
        Display name of the first podcast host (used in the prompt and as a
        speaker-turn boundary marker).
    speaker2_name : str
        Display name of the second podcast host.

    Returns
    -------
    list[DialogueChunk]
        Ordered list of dialogue chunks ready for TTS processing.

    Raises
    ------
    RuntimeError
        If the Gemini API returns an empty or missing response text.

    Examples
    --------
    >>> chunks = generate_dialogue(articles, gemini_cfg, "Alex", "Jordan")
    >>> len(chunks) > 0
    True
    """
    speaker1_personality = gemini_cfg.get("speaker1", {}).get("personality", "")
    speaker2_personality = gemini_cfg.get("speaker2", {}).get("personality", "")

    prompt = _build_prompt(
        articles,
        speaker1_name,
        speaker2_name,
        speaker1_personality=speaker1_personality,
        speaker2_personality=speaker2_personality,
    )

    logger.info(
        "Sending %d article(s) to Gemini model '%s'.",
        len(articles),
        gemini_cfg["text_model"],
    )
    logger.debug("Prompt length: %d chars.", len(prompt))

    client = genai.Client(api_key=gemini_cfg["api_key"])

    @gemini_retry
    def _call_api() -> str:
        response = client.models.generate_content(
            model=gemini_cfg["text_model"],
            contents=prompt,
        )
        return response.text

    dialogue_text = _call_api()

    if not dialogue_text:
        raise RuntimeError("Gemini returned an empty dialogue response.")

    logger.info("Received dialogue of %d chars from Gemini.", len(dialogue_text))

    return _split_dialogue_into_chunks(dialogue_text, speaker1_name, speaker2_name)
