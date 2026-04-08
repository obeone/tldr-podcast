# tldr-podcast

![Python](https://img.shields.io/badge/Python-3.13+-blue?logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Flash%20%7C%20TTS-4285F4?logo=google&logoColor=white)
![ffmpeg](https://img.shields.io/badge/ffmpeg-required-green?logo=ffmpeg&logoColor=white)
![Tests](https://img.shields.io/badge/tests-156%20passed-brightgreen)

Converts TLDR newsletters into a two-voice podcast MP3 using Gemini AI.

---

## 🏗️ Architecture

```mermaid
flowchart TB
    IN[📧 IMAP / .eml file] --> EP[Email Parser<br>sponsor filter]
    EP --> WS[Web Scraper<br>trafilatura]

    WS --> SUM[Pre-Summarizer<br>cheap model · optional]
    WS --> LE[Link Extractor<br>repos · models · papers]

    SUM --> LLM[Script Writer<br>Gemini Flash]

    LLM --> DC[Dialogue chunks<br>≤ 3 800 bytes]

    DC --> TTS[TTS Generator<br>Gemini multi-speaker]
    DC --> RPT[📊 Report Generator]
    LE --> RPT

    TTS --> AE[Audio Exporter<br>pydub + ffmpeg]

    AE --> MP3[🎙️ .mp3]
    RPT --> OUT[📂 articles · script · links]
```

---

## ✨ Features

| Feature | Details |
| --- | --- |
| 📬 IMAP fetch | Retrieves unread TLDR emails via SSL, moves processed emails to a configurable folder |
| 📅 Date filter | Deduplicates emails per day with `--date YYYY-MM-DD` |
| 🚫 Sponsor filter | Strips `TOGETHER WITH`, `SPONSOR`, ads |
| 🌐 Article scraping | Full text via trafilatura, fallback to summary |
| 🔗 Link extraction | Categorises URLs into repos, models, papers, sources |
| 🤖 AI curation | Gemini Flash selects 8-12 interesting stories (configurable) |
| 📝 Pre-summarization | Optional cheap model summarizes articles before script writing |
| ⚡ Service tiers | Support for `flex` (cheaper) and `priority` (faster) API tiers |
| 🎙️ Two-voice TTS | Configurable speaker names, voices, and personalities |
| 🌍 Multi-language | Dialogue and TTS language configurable (`language` key) |
| 🎵 MP3 / WAV export | pydub + ffmpeg, auto-creates output directories |
| 📊 Report generation | `--report` creates a timestamped folder with articles, script, and links |
| 💰 Token tracking | Real-time token usage and cost display on progress bars, tier-aware pricing |
| 🔍 Dry-run mode | Print dialogue without calling TTS |
| 📂 Local .eml mode | Test with a saved email, no IMAP required |
| 🔄 Retry logic | Automatic retry with backoff for transient API failures |
| 🛠️ Config wizard | Interactive `config init` to bootstrap your configuration |

---

## 📦 Installation

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- ffmpeg

```bash
# Ubuntu / Debian
sudo apt-get install -y ffmpeg

# macOS
brew install ffmpeg
```

### Install with uv

```bash
git clone <repo-url> tldr-podcast
cd tldr-podcast

# Install as a CLI tool
uv tool install .

# Or for development (editable install)
uv sync && uv pip install -e .
```

---

## ⚙️ Configuration

Bootstrap with the interactive wizard:

```bash
tldr-podcast config init
```

Or copy the example and fill in your values manually:

```bash
cp config.example.yaml config.yaml
```

```yaml
imap:
  host: imap.gmail.com
  port: 993
  username: your@email.com
  password_env: IMAP_PASSWORD   # name of env var holding the password
  folder: INBOX
  seen_folder: TLDR/Seen        # folder for processed emails

gemini:
  api_key_env: GEMINI_API_KEY   # name of env var holding the API key
  text_model: gemini-2.0-flash
  # summary_model: gemini-2.0-flash-lite  # optional cheap pre-summarizer
  tts_model: gemini-2.5-flash-preview-tts
  # service_tier: flex          # optional: flex | priority (omit for standard)
  language: French              # dialogue and TTS language
  speaker1:
    name: Alex
    voice: Puck
    personality: "enthusiastic, curious, quick to get excited about tech innovations"
  speaker2:
    name: Jordan
    voice: Charon
    personality: "analytical, mildly skeptical, adds nuance and historical context"
  dialogue:
    min_articles: 8
    max_articles: 12
    target_word_count: 1200
  tts_style:
    pace: "slow and deliberate"
    scene: "Two friends co-hosting a casual French tech podcast in a cozy studio"
    temperature: 1.2             # expressiveness (0.0–2.0)

scraping:
  max_articles: 15
  timeout_seconds: 10

output:
  directory: output
  format: mp3                    # mp3 or wav

pricing:                         # USD per 1M tokens (for cost tracking)
  # Flat format (no tiers):
  gemini-2.0-flash:
    input_per_1m: 0.10
    output_per_1m: 0.40
  # Tier-aware format:
  gemini-2.5-flash:
    standard:
      input_per_1m: 0.30
      output_per_1m: 2.50
    flex:
      input_per_1m: 0.15
      output_per_1m: 1.25
    priority:
      input_per_1m: 0.54
      output_per_1m: 4.50
  gemini-2.5-flash-preview-tts:
    input_per_1m: 0.50
    output_per_1m: 10.00
```

Keys ending in `_env` reference environment variable **names** — the actual
secrets are never stored in the file.

### Environment Variables

| Variable | Description |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key (Google AI Studio) |
| `IMAP_PASSWORD` | IMAP account password |

Export them or add to a `.env` file:

```bash
export GEMINI_API_KEY="your-key"
export IMAP_PASSWORD="your-password"
```

---

## 🚀 Usage

### CLI Commands

| Command | Description |
| --- | --- |
| `tldr-podcast run --config config.yaml` | Fetch unread emails via IMAP and generate podcast |
| `tldr-podcast run --config config.yaml --eml file.eml` | Use a local `.eml` file |
| `tldr-podcast run --config config.yaml --date 2026-03-15` | Target a specific date |
| `tldr-podcast run --config config.yaml --eml file.eml --dry-run` | Print dialogue only, no TTS |
| `tldr-podcast run --config config.yaml --report` | Generate report folder alongside podcast |
| `tldr-podcast run --config config.yaml --no-progress` | Disable rich progress bar |
| `tldr-podcast run --config config.yaml --verbose` | Enable DEBUG logging |
| `tldr-podcast config init` | Interactive configuration wizard |
| `tldr-podcast config show` | Display the current configuration |
| `tldr-podcast config show --resolve` | Display config with resolved env vars (masked) |

### Example — dry-run on a saved email

```bash
tldr-podcast run \
  --config config.yaml \
  --eml "mails/Gemini 3.1 Pro.eml" \
  --dry-run
```

### Example — full pipeline with report

```bash
tldr-podcast run --config config.yaml --eml "mails/newsletter.eml" --report
# → output/tldr_2026-02-22_1430.mp3
# → output/tldr_2026-02-22_1430/articles.md
# → output/tldr_2026-02-22_1430/script.md
# → output/tldr_2026-02-22_1430/summary.md
```

---

## 🧪 Tests

```bash
uv run pytest tests/ -v
```

156 unit tests covering every module. All external APIs are mocked.

---

## 🗂️ Project Structure

```text
tldr-podcast/
├── config.example.yaml        # Documented configuration template
├── pyproject.toml
├── src/tldr/
│   ├── cli.py                 # Click CLI entry point (group with subcommands)
│   ├── config.py              # YAML loader with *_env resolution
│   ├── imap_client.py         # IMAP SSL client
│   ├── email_parser.py        # MIME parser → Article dataclass list
│   ├── web_scraper.py         # trafilatura scraper with fallback
│   ├── link_extractor.py      # URL extraction and categorisation
│   ├── llm_summarizer.py      # Gemini Flash dialogue + chunking
│   ├── tts_generator.py       # Gemini multi-speaker TTS
│   ├── audio_exporter.py      # PCM → MP3/WAV via pydub
│   ├── report_generator.py    # Timestamped report folder output
│   ├── token_tracker.py       # Token usage and cost tracking
│   └── retry.py               # Retry with exponential backoff
├── tests/                     # 156 pytest unit tests (13 files)
└── mails/                     # Sample .eml files for testing
```

---

## 📝 Notes

- Gemini TTS outputs raw PCM (24 kHz, mono, 16-bit). ffmpeg is required
  for MP3 encoding.
- The TTS API has a ~4 000-byte text limit per call. The summarizer
  automatically splits dialogue at speaker-turn boundaries.
- Sponsor sections (`TOGETHER WITH`, `SPONSOR`, etc.) are filtered out
  before article selection.
- Token costs are tracked live and displayed at the end of each run.
- Pricing supports both flat and tier-aware formats — the active tier is
  selected by `gemini.service_tier` in your config.

---

*Author: Grégoire Compagnon — <obeone@obeone.org>*
