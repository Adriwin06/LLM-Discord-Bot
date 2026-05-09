# LLM-Discord-Bot

An LLM-powered Discord bot built with Python, discord.py, and LiteLLM. It is designed to be modular and configurable, with a full context pipeline (summaries, profiles, history, media processing) and a dual-model decision system for low-latency reply/reaction routing.

## Table of contents

- Features at a glance
- How the bot decides to reply
- How context is built for every message
- Attachment and embed processing by model capability
- Local tools and web search
- Commands
- Configuration
- Data storage and backups
- Setup and run
- Troubleshooting
- Project structure

## Features at a glance

- Universal LLM support via LiteLLM (OpenAI, Gemini, Anthropic, Mistral, Ollama, and more).
- Dual-model system:
  - Main model for full responses.
  - Decision model for fast reply/react/ignore decisions.
- Rich context:
  - Channel summaries that evolve over time.
  - User profiles with AI summaries plus manual notes.
  - Recent message history and reply chain context.
  - Server emoji map for safe custom emoji usage.
- Media understanding pipeline:
  - Images (including animated GIFs), audio, video, PDFs, Office documents, text files.
  - OCR and local speech-to-text fallbacks for text-only models.
- Safe mention resolver for user and role pings.
- Optional local tools (message search, channel summaries, user info, web search, web fetch).
- Slash-command administration for summaries, profiles, models, media config, and more.
- Automatic backups and rotating logs.
- Async-first implementation for scale and responsiveness.

## How the bot decides to reply

The reply flow is designed to be fast, low-noise, and safe:

1. Message arrives (`on_message`).
2. Counters update (messages since summary and profile update).
3. Summary or profile tasks may be scheduled.
4. Blacklist checks and command bypasses run early.
5. If the decision model is enabled:
    - The decision model receives context tailored to its capabilities.
    - It returns one of: reply, react, gif, or none.
6. If decision model is disabled:
    - The bot only replies to direct interactions (reply or mention).
7. If the bot is not part of the conversation and the channel is busy:
    - It skips decision to avoid interrupting an active human thread.
8. If reply is chosen, the main model builds a full context and generates the response.

Debounce and typing behavior:

- Same-user message chains are treated as one combined input.
- The bot waits briefly for the user to finish typing (configurable).
- Optional long-typing state allows the decision model to choose a light joke or GIF.

## How context is built for every message

Context is assembled in a stable order to avoid prompt conflicts. Every model gets a context tailored to its capabilities, but the structure is consistent.

Context pipeline order:

1. System prompts
    - Capabilities prompt (what the bot can do and what tools exist).
    - Behavior prompt (server or channel override).
2. Bot identity (optional)
    - Bot name and ID as a system message.
3. Server emojis (optional)
    - Available emojis with safe formats for message or reaction use.
4. Channel summaries (optional)
    - Current channel summary.
    - Summaries for explicitly mentioned channels, if accessible.
5. User profile (optional)
    - Manual note and AI summary for the author of the current message.
6. Conversation history (optional)
    - Recent messages in the channel.
    - Reply chain history, if enabled.
7. Reply-chain media context (conditional)
    - If the current message references media in the reply chain and does not include new media.
8. Current message
    - Added last, formatted with any processed attachments or embeds.

Important behaviors:

- Conversation history is injected as system text, not user turns, to prevent the model from answering old messages.
- The current message is always the only live user turn.
- Both decision and main models get the same logical context, but media is processed based on each model's capabilities.

### Context controls

Admins can inspect context directly using the `/context show` command with flags to include or exclude parts of the context.

## Attachment and embed processing by model capability

Every attachment is downloaded safely, checked against size limits, and processed based on both file type and model capability. The decision model and main model each get their own processed media representation.

### Common safety and limits

- Only http/https URLs are allowed.
- Local or private network URLs are blocked.
- Content is downloaded with a bounded read limit based on file type.
- Oversized files are rejected early with a descriptive fallback message.

### Images (including GIFs)

If the model supports vision:

- Static images are sent as `image_url` content parts.
- For Ollama-style models, images are re-encoded to validated base64 and sent inline.
- Optional OCR text can be included alongside the image when enabled.

If the model does not support vision:

- OCR is used (when available) and injected as text.
- If OCR is disabled or unavailable, a descriptive fallback line is used.

Animated GIFs:

- Frames are sampled evenly across the full GIF and attached as multiple images for vision models.
- If GIF frame extraction is disabled or fails, the GIF is treated as a static image.

### Audio

- If `send_direct_if_supported` is enabled and the model supports audio, the audio URL is passed directly.
- Otherwise, audio is transcribed locally using faster-whisper or openai-whisper.
- Duration limits are enforced, and transcripts are truncated by configured max chars.

### Video

- Duration limits are enforced using ffprobe/ffmpeg.
- Audio is extracted and transcribed locally.
- Frames are extracted at intervals with size and count limits.
- If the model supports vision, frames are attached as images.
- If the model is text-only, OCR is applied to frames (when enabled) and included as text.

### PDFs

- If the model supports PDFs, the PDF is passed directly.
- Otherwise text is extracted using pdfplumber (preferred) or PyPDF2.
- Optional page markers are included when preserve formatting is enabled.

### Office documents

Supported types: DOCX, XLSX, PPTX.

- DOCX: paragraphs are extracted; headings are preserved when configured.
- XLSX: sheet names and rows are exported to text.
- PPTX: slide text is extracted (requires python-pptx).

### Text files

- Full content is included as text if the extension is supported and size limits allow.
- Supported extensions are configurable.

### Other files

- Metadata-only fallback (name, size, type, URL) when enabled.
- Otherwise the attachment is omitted with a brief note.

### Embeds

- Embed media (Tenor/Giphy/GIF URLs) is routed through the same attachment pipeline.
- Non-media embeds are summarized by title/description/URL.

## Local tools and web search

If tools are enabled and no visual content is attached, the main model can call local, read-only tools for additional context:

- Channel discovery and resolution
- Stored channel summaries
- Message search and retrieval
- User profile lookup (including stored AI summaries)
- Web search and web page extraction

The tool loop is capped by `TOOL_MAX_ROUNDS` and avoids duplicate calls. Preloaded channel summaries prevent redundant tool calls.

## Commands

### Core administration

- `/llm settings` - Set main model, behavior prompt, summary triggers.
- `/llm decision` - Toggle or inspect the decision model (server or channel).
- `/llm blacklist` - Block or unblock LLM output for a channel.
- `/channel override` - Per-channel model and prompt overrides.
- `/context reset` - Clear channel or guild summary context.
- `/context show` - Inspect the full LLM context.
- `/backup` - Run a manual backup.
- `/reload` - Reload cogs, config, prompts, or all.
- `/restart` - Restart the bot process.

### Summaries and profiles

- `/summary view` - Show a channel summary.
- `/summary update` - Force a summary update now.
- `/summary clear` - Reset summary and counters.
- `/summary settings` - Per-channel summary triggers.
- `/note add` - Add a manual note to a user profile.
- `/note view` - View manual note and AI summary.
- `/note refresh ai` - Force an AI profile refresh.
- `/user profile` - Profile stats and profile update settings.

### Media configuration

- `/media config set` - Edit media processing settings by type.
- `/media config view` - View current media settings.

### Fun and utility

- `/fun insult` - Generate a personalized insult.
- `/fun compliment` - Generate a personalized compliment.
- `/fun reverse` - Reverse trash talk.
- `/mock message` - Mock the last message from a user.
- `/mock avatar` - Roast a profile picture when vision is supported.
- `/say` - Send a message as the bot.
- `/retry` - Delete and regenerate the last bot reply in a channel.

### Leveling system

- `/level profile` - View user level profile.
- `/level leaderboard` - Server XP leaderboard.
- `/level settings` - Configure XP, cooldown, and notifications.
- `/level roles` - Add or remove level roles.
- `/level prestige` - Configure prestige levels.
- `/level prestige requirement` - Set prestige level requirement.
- `/level voice xp` - Configure voice XP settings.

## Configuration

### Environment variables

Core:

- `DISCORD_TOKEN` - Bot token.
- `BOT_ACTIVITY_TYPE` - watching, playing, listening, streaming.
- `BOT_ACTIVITY_TEXT` - Activity text.

Models and rate limits:

- `MAIN_LLM_MODEL` - Main response model.
- `DECISION_LLM_MODEL` - Decision model.
- `DECISION_LLM_ENABLED` - Enable ambient decision model.
- `MAIN_LLM_RATE_LIMIT_ENABLED`, `MAIN_LLM_RATE_LIMIT_SECONDS`
- `DECISION_LLM_RATE_LIMIT_ENABLED`, `DECISION_LLM_RATE_LIMIT_SECONDS`

Reply chain and typing behavior:

- `REPLY_CHAIN_DEBOUNCE_ENABLED`, `REPLY_CHAIN_DEBOUNCE_SECONDS`
- `REPLY_CHAIN_WAIT_FOR_TYPING`
- `REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS`
- `REPLY_CHAIN_LONG_TYPING_SECONDS`
- `TYPING_ACTIVE_SECONDS`
- `GIFS_ENABLED`

Tools and web search:

- `TOOLS_ENABLED`, `TOOL_MAX_ROUNDS`
- `WEB_SEARCH_ENABLED`, `WEB_SEARCH_AUTO_ENABLED`
- `WEB_SEARCH_CONTEXT_SIZE`
- `WEB_FETCH_MAX_CHARS`

Summaries and profiles:

- `DEFAULT_SUMMARIZE_EVERY_MESSAGES`
- `INITIAL_SUMMARY_MESSAGES`
- `DEFAULT_SUMMARIZE_EVERY_HOURS`
- `DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES`
- `DEFAULT_PROFILE_UPDATE_EVERY_HOURS`

Media defaults:

- `DEFAULT_MEDIA_IMAGES_ENABLED`
- `DEFAULT_MEDIA_AUDIO_ENABLED`
- `DEFAULT_MEDIA_VIDEO_ENABLED`
- `DEFAULT_MEDIA_PDF_ENABLED`
- `DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED`
- `DEFAULT_MEDIA_TEXT_FILES_ENABLED`
- `DEFAULT_MEDIA_OTHER_FILES_ENABLED`
- `DEFAULT_GIF_MAX_FRAMES`
- `DEFAULT_GIF_FRAME_QUALITY`
- `DEFAULT_VIDEO_MAX_SIZE_MB`
- `DEFAULT_MEDIA_PROBE_TIMEOUT_SECONDS`
- `DEFAULT_VIDEO_FRAME_TIMEOUT_SECONDS`
- `DEFAULT_VIDEO_MAX_FRAMES`
- `DEFAULT_VIDEO_FRAME_QUALITY`

Local speech-to-text:

- `LOCAL_STT_ENGINE` (faster-whisper or openai-whisper)
- `LOCAL_STT_MODEL`
- `LOCAL_STT_DEVICE`
- `LOCAL_STT_COMPUTE_TYPE`
- `LOCAL_STT_BEAM_SIZE`
- `LOCAL_STT_VAD_FILTER`
- `LOCAL_STT_LANGUAGE`

Backups and logging:

- `BACKUP_INTERVAL_HOURS`
- `LOG_DIR`, `LOG_FILE`
- `LOG_LEVEL`, `LOG_CONSOLE_LEVEL`, `LOG_FILE_LEVEL`
- `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`

Provider keys:

- LiteLLM reads provider keys from standard env vars (for example `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`).

### Guild and channel settings

Settings are stored in `data/settings.json`. Key fields include:

- `model` - Main model override.
- `behavior_prompt` - Server behavior prompt override.
- `decision_llm_enabled` - Toggle decision model per server or channel.
- `summarize_every_messages`, `summarize_every_hours`.
- `initial_summarize_messages`.
- `profile_update_every_messages`, `profile_update_every_hours`.
- `llm_blacklisted_channels` - Channels that block LLM output.
- `media` - Per-type media overrides.
- `context.history_messages`, `context.reply_chain_limit`.
- `context.mentioned_channel_summary_limit`, `context.mentioned_channel_summary_max_chars`.
- `channel_overrides` - Per-channel overrides for any of the above.

### Prompt files

- `prompts/BEHAVIOR_PROMPT.md` - Personality and style.
- `prompts/CAPABILITIES_PROMPT.md` - Allowed actions and tools.

## Data storage and backups

- `data/settings.json` stores server and channel configuration.
- `data/data.json` stores dynamic state:
  - Channel summaries and summary metadata.
  - User profiles, manual notes, and update counters.
- `data/levels.json` stores XP and leveling data.
- `.backup/` stores timestamped backups for `settings.json` and `data.json`.

## Setup and run

### 1) Clone and create a venv

```bash
git clone https://github.com/your-username/LLM-Discord-Bot.git
cd LLM-Discord-Bot
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

macOS/Linux:

```bash
source venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure `.env`

Create a `.env` file and add at least:

- `DISCORD_TOKEN`
- `MAIN_LLM_MODEL`
- `DECISION_LLM_MODEL`
- The provider key for your model (for example `OPENAI_API_KEY`)

### 4) Run the bot

```bash
python main.py
```

## Troubleshooting

- Media processing fails:
  - Ensure ffmpeg/ffprobe are available in PATH for video processing.
  - Install `pytesseract` and the Tesseract binary for OCR.
  - Use `faster-whisper` or `openai-whisper` for local transcription.
- The bot does not reply:
  - Check whether the channel is LLM-blacklisted.
  - Verify decision model is enabled or that the bot was mentioned.
  - Inspect `/context show` to confirm context assembly.
- Model does not see images:
  - Confirm the model supports vision.
  - For text-only models, check OCR settings.

## Project structure

- `main.py` - Bot entry point and lifecycle management.
- `bot/config.py` - Environment configuration.
- `bot/store.py` - Thread-safe JSON storage.
- `bot/llm_provider.py` - LiteLLM integration and capability checks.
- `bot/context_manager.py` - Context assembly and media processing.
- `bot/discord_tools.py` - Local tool implementations.
- `bot/cogs/` - Command and event cogs.
- `data/` - Persistent settings and state.
- `prompts/` - Behavior and capability prompts.

## Contributing

Issues and pull requests are welcome. Please describe the behavior, the expected behavior, and any logs or screenshots that help reproduce the issue.
