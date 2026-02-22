# Longer & Slower Podcast Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Generate ~8-minute podcasts (up from ~2:40) with a slower, more deliberate speaking pace, driven by config parameters.

**Architecture:** Three independent changes — new config keys in `config.example.yaml`, prompt engineering in `llm_summarizer.py` (more articles, word count target, pacing cues, `max_output_tokens`), and TTS preamble update in `tts_generator.py` (configurable pace string).

**Tech Stack:** Python, Google Gemini SDK (`google-genai`), pytest, PyYAML

---

### Task 1: Add new config keys to `config.example.yaml`

No tests required — this is purely documentation/config.

**Files:**
- Modify: `config.example.yaml`

**Step 1: Add the `dialogue` section under `gemini`**

In `config.example.yaml`, under the `gemini:` block, add after `tts_model`:

```yaml
  dialogue:
    min_articles: 8      # minimum articles to cover (default: 8)
    max_articles: 12     # maximum articles to cover (default: 12)
    target_word_count: 1200  # minimum word count for the full dialogue
```

**Step 2: Add `pace` to `tts_style`**

In the existing `tts_style:` block, add:

```yaml
  tts_style:
    pace: "slow and deliberate"   # natural-language pace for TTS preamble
    scene: "Two friends co-hosting a casual French tech podcast in a cozy studio"
    temperature: 1.2
```

**Step 3: Commit**

```bash
git add config.example.yaml
git commit -m "feat(config): add dialogue length and tts pace configuration keys"
```

---

### Task 2: Update `_build_prompt()` and prompt template in `llm_summarizer.py`

**Files:**
- Modify: `src/tldr/llm_summarizer.py`
- Test: `tests/test_llm_summarizer.py`

**Step 1: Write the failing tests**

Add to `tests/test_llm_summarizer.py`:

```python
class TestBuildPromptConfig:
    """Tests that generate_dialogue injects config-driven parameters into the prompt."""

    def test_prompt_includes_configured_article_range(self):
        """Prompt must reference the min/max article counts from config."""
        cfg = {
            **GEMINI_CFG,
            "dialogue": {"min_articles": 8, "max_articles": 12, "target_word_count": 1200},
        }
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            generate_dialogue(SAMPLE_ARTICLES, cfg, "Alex", "Jordan")

        call_args = mock_genai.Client.return_value.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[1]
        assert "8" in prompt
        assert "12" in prompt

    def test_prompt_includes_target_word_count(self):
        """Prompt must include the target_word_count value."""
        cfg = {
            **GEMINI_CFG,
            "dialogue": {"min_articles": 8, "max_articles": 12, "target_word_count": 1200},
        }
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            generate_dialogue(SAMPLE_ARTICLES, cfg, "Alex", "Jordan")

        call_args = mock_genai.Client.return_value.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[1]
        assert "1200" in prompt or "1 200" in prompt

    def test_prompt_uses_defaults_when_dialogue_key_absent(self):
        """When 'dialogue' key is absent from config, prompt uses sensible defaults."""
        mock_genai = _mock_genai_response(SHORT_DIALOGUE)

        with patch("tldr.llm_summarizer.genai", mock_genai):
            # GEMINI_CFG has no 'dialogue' key — must not raise
            chunks = generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

        assert len(chunks) > 0
```

**Step 2: Run to confirm failure**

```bash
cd /Users/obeone/tmp/claude_tests/tldr/.worktrees/feat/longer-slower-podcast
uv run pytest tests/test_llm_summarizer.py::TestBuildPromptConfig -v
```

Expected: FAIL — the prompt does not yet include article count or word count.

**Step 3: Update `_SYSTEM_PROMPT_TEMPLATE` and `_build_prompt()`**

In `src/tldr/llm_summarizer.py`, replace `_SYSTEM_PROMPT_TEMPLATE` with:

```python
_SYSTEM_PROMPT_TEMPLATE = """\
You are a podcast script writer. Your job is to create an engaging, \
conversational podcast dialogue between two hosts: {speaker1} and {speaker2}.

Host personalities:
- {speaker1}: {speaker1_personality}
- {speaker2}: {speaker2_personality}

Instructions:
- Review all the articles provided below.
- Select the {min_articles} to {max_articles} most interesting or significant articles.
- Discuss each selected article in depth: explain what it is about, why it matters, \
and explore its implications or connections to broader trends. \
Aim for 3 to 5 exchanges per article.
- The entire dialogue MUST be written in French.
- The total dialogue must be at least {target_word_count} words.
- Keep the tone informative but lively — like two curious friends catching up on tech news.
- Reflect each host's personality in their speaking style and reactions.
- Add inline emotional cues in parentheses within the dialogue text to guide \
delivery (e.g., "(avec enthousiasme)", "(sceptique)", "(posément)", \
"(en pesant ses mots)", "(après une courte pause)"). Vary them naturally \
according to the content and pace of the discussion.
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
```

Then update `_build_prompt()` to accept and use the new parameters:

```python
def _build_prompt(
    articles: list,
    speaker1_name: str,
    speaker2_name: str,
    speaker1_personality: str = "",
    speaker2_personality: str = "",
    min_articles: int = 8,
    max_articles: int = 12,
    target_word_count: int = 1200,
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
    min_articles : int, optional
        Minimum number of articles the LLM must cover, by default 8.
    max_articles : int, optional
        Maximum number of articles the LLM may cover, by default 12.
    target_word_count : int, optional
        Minimum total word count for the generated dialogue, by default 1200.

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
        min_articles=min_articles,
        max_articles=max_articles,
        target_word_count=target_word_count,
        articles=articles_text,
    )
```

**Step 4: Update `generate_dialogue()` to read the new config keys and pass them to `_build_prompt()`**

In `generate_dialogue()`, before calling `_build_prompt()`, add:

```python
dialogue_cfg = gemini_cfg.get("dialogue", {})
min_articles = dialogue_cfg.get("min_articles", 8)
max_articles = dialogue_cfg.get("max_articles", 12)
target_word_count = dialogue_cfg.get("target_word_count", 1200)
```

Then update the `_build_prompt()` call:

```python
prompt = _build_prompt(
    articles,
    speaker1_name,
    speaker2_name,
    speaker1_personality=speaker1_personality,
    speaker2_personality=speaker2_personality,
    min_articles=min_articles,
    max_articles=max_articles,
    target_word_count=target_word_count,
)
```

**Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_llm_summarizer.py -v
```

Expected: all tests PASS.

**Step 6: Commit**

```bash
git add src/tldr/llm_summarizer.py tests/test_llm_summarizer.py
git commit -m "feat(llm): add configurable article count and word count target to dialogue prompt"
```

---

### Task 3: Add `max_output_tokens` to the Gemini API call in `llm_summarizer.py`

**Files:**
- Modify: `src/tldr/llm_summarizer.py`
- Test: `tests/test_llm_summarizer.py`

**Step 1: Write the failing test**

Add to `tests/test_llm_summarizer.py` (inside `TestGenerateDialogue` or as a new class):

```python
def test_generate_dialogue_passes_max_output_tokens(self):
    """generate_dialogue must pass max_output_tokens=8192 to the Gemini API."""
    from google.genai import types as genai_types

    mock_genai = _mock_genai_response(SHORT_DIALOGUE)

    with patch("tldr.llm_summarizer.genai", mock_genai):
        generate_dialogue(SAMPLE_ARTICLES, GEMINI_CFG, "Alex", "Jordan")

    call_kwargs = mock_genai.Client.return_value.models.generate_content.call_args.kwargs
    config_obj = call_kwargs.get("config")
    assert config_obj is not None, "generate_content must be called with a config= kwarg"
    assert config_obj.max_output_tokens == 8192
```

**Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_llm_summarizer.py::TestGenerateDialogue::test_generate_dialogue_passes_max_output_tokens -v
```

Expected: FAIL — no `config=` kwarg currently.

**Step 3: Add the import and update the API call**

At the top of `src/tldr/llm_summarizer.py`, add:

```python
from google.genai import types
```

In `generate_dialogue()`, update the `_call_api` inner function:

```python
@gemini_retry
def _call_api() -> str:
    response = client.models.generate_content(
        model=gemini_cfg["text_model"],
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=8192,
        ),
    )
    return response.text
```

**Step 4: Run tests**

```bash
uv run pytest tests/test_llm_summarizer.py -v
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add src/tldr/llm_summarizer.py tests/test_llm_summarizer.py
git commit -m "feat(llm): set max_output_tokens=8192 to support longer dialogue generation"
```

---

### Task 4: Use configured pace in the TTS preamble (`tts_generator.py`)

**Files:**
- Modify: `src/tldr/tts_generator.py`
- Test: `tests/test_tts_generator.py`

**Step 1: Write the failing tests**

Add to `tests/test_tts_generator.py`:

```python
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
```

**Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_tts_generator.py::TestBuildTtsPromptPace -v
```

Expected: FAIL — `_build_tts_prompt` is not exported (private) so the import fails, and the pace is hardcoded.

**Step 3: Update `_build_tts_prompt()` in `tts_generator.py`**

Replace the `preamble` construction in `_build_tts_prompt()`:

```python
pace = gemini_cfg.get("tts_style", {}).get("pace", "natural")

preamble = (
    f"Audio profile: Two hosts of a French tech podcast, speaking in French.\n"
    f"{s1['name']} is {s1_personality}.\n"
    f"{s2['name']} is {s2_personality}.\n"
)
if scene:
    preamble += f"Scene: {scene}\n"
preamble += (
    f"Director's notes: {pace.capitalize()} conversational pace — speak clearly, "
    "allow a natural beat between sentences so the listener can absorb each idea. "
    "Genuine reactions, honour any emotional cues written in parentheses in the dialogue.\n\n"
)
```

Also update the docstring of `_build_tts_prompt()` to document the `tts_style.pace` key.

**Step 4: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: all 57+ tests PASS.

**Step 5: Commit**

```bash
git add src/tldr/tts_generator.py tests/test_tts_generator.py
git commit -m "feat(tts): use configurable pace instruction in TTS preamble"
```

---

### Final verification

```bash
uv run pytest tests/ -v --tb=short
```

All tests must pass before merging.
