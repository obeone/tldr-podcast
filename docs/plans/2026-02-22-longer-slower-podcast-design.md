# Design: Longer Podcast Duration and Slower Speaking Pace

**Date:** 2026-02-22
**Status:** Approved

## Problem Statement

The generated TLDR podcast currently runs ~2 minutes 40 seconds. The target duration is
8 minutes. Additionally, the speaking pace is too fast for comfortable comprehension.

These are two independent problems with independent solutions:

1. **Too short**: The LLM generates too little content — few articles, shallow coverage.
2. **Too fast**: The TTS preamble does not instruct slower delivery.

## Goals

- Reach ~8 minutes of podcast audio per daily episode.
- Improve comprehension comfort by slowing the speaking pace.
- Expose key tuning parameters in `config.yaml` without requiring code changes.

## Non-Goals

- Audio post-processing (ffmpeg time-stretching) — previously attempted and discarded.

## Design

### 1. New config keys (`config.example.yaml`)

Three new optional keys under the `gemini` section:

```yaml
gemini:
  dialogue:
    min_articles: 8        # minimum number of articles to cover (default: 8)
    max_articles: 12       # maximum number of articles to cover (default: 12)
    target_word_count: 1200  # minimum word count for the full dialogue (default: 1200)
  tts_style:
    pace: "slow and deliberate"  # natural-language pace instruction for TTS preamble
    scene: "..."
    temperature: 1.2
```

All three keys are optional with sensible defaults so existing `config.yaml` files
remain valid without modification.

### 2. Longer content (`llm_summarizer.py`)

**`_build_prompt()` changes:**

- Read `min_articles`, `max_articles`, and `target_word_count` from `gemini_cfg`.
- Inject them into `_SYSTEM_PROMPT_TEMPLATE`:
  - Article selection: `"Select the {min_articles} to {max_articles} most interesting"`
  - Coverage depth: replace "briefly" with "in depth — explain what it is, why it
    matters, and explore implications. Aim for 3 to 5 exchanges per article."
  - Word count: `"The total dialogue must be at least {target_word_count} words."`
  - Pacing cues: instruct the LLM to include `(posément)`, `(en pesant ses mots)`,
    `(après une courte pause)` where appropriate.

**`generate_dialogue()` API call:**

- Add `max_output_tokens=8192` to prevent silent truncation of long dialogues.

### 3. Slower speaking pace (`tts_generator.py`)

**`_build_tts_prompt()` change:**

- Read `pace` from `gemini_cfg.get("tts_style", {}).get("pace", "natural")`.
- Replace the hardcoded `"Natural conversational pace"` in the Director's notes with
  the configured value, wrapped as:
  `"{pace} conversational pace — speak clearly, allow a natural beat between sentences
  so the listener can absorb each idea."`

### 4. `_build_prompt()` signature update

`_build_prompt()` gains two optional parameters: `min_articles` and `max_articles`
(and `target_word_count`), read from `gemini_cfg` in `generate_dialogue()` and passed
through.

## Files Changed

| File | Change |
|---|---|
| `config.example.yaml` | Add `dialogue.min_articles`, `dialogue.max_articles`, `dialogue.target_word_count`, `tts_style.pace` |
| `src/tldr/llm_summarizer.py` | Prompt template: depth, word count, pacing cues; read new config keys |
| `src/tldr/llm_summarizer.py` | API call: add `max_output_tokens=8192` |
| `src/tldr/tts_generator.py` | Preamble: use configured pace instruction |

## Testing

- All existing unit tests must continue to pass.
- Manual verification: generate a podcast and confirm audio duration is near 8 minutes
  with a noticeably more deliberate pace.
