# LLM Discord Bot

Python Discord bot powered by LiteLLM. It can answer messages, react, send GIPHY GIFs, keep channel summaries and user profiles, process attachments, and let the main LLM call read-only Discord/GIPHY/web tools when extra context is needed.

The codebase uses a two-model flow:

- `MAIN_LLM_MODEL` generates final Discord replies and LLM-driven command output.
- `DECISION_LLM_MODEL` decides whether an ordinary message should be ignored, answered, reacted to, or answered with a GIF.

## What This Bot Does

- Replies to direct mentions and replies to the bot.
- Optionally makes ambient decisions on normal messages with the decision model.
- Builds a structured context from prompts, bot identity, server emojis, channel summaries, user profile notes, recent messages, reply chains, and the current message.
- Maintains automatic channel summaries and AI-generated user profile summaries.
- Processes images, GIFs, audio, video, PDFs, Office files, and text files before sending context to the model.
- Exposes read-only local tools to the main model for channel lookup, message search, user profile lookup, web search, and web page extraction.
- Provides slash commands for administration, summaries, profiles, media settings, leveling, fun commands, retries, backups, reloads, and restart.
- Stores runtime state in JSON files and creates timestamped backups.

## Requirements

- Python 3.10 or newer.
- A Discord application and bot token.
- A GIPHY API key if you want GIF replies.
- Discord Developer Portal intents:
  - Message Content Intent
  - Server Members Intent
- A LiteLLM-supported model provider key, for example `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `MISTRAL_API_KEY`.
- Recommended external binaries for media features:
  - `ffmpeg` and `ffprobe` for video/audio probing, frame extraction, and audio extraction.
  - Tesseract OCR binary for image/video-frame OCR when using `pytesseract`.

## Quick Start

```bash
git clone https://github.com/your-username/LLM-Discord-Bot.git
cd LLM-Discord-Bot
python -m venv venv
```

Windows:

```powershell
.\venv\Scripts\Activate.ps1
```

If you are using PowerShell, `venv` is the Windows environment for this repo. The `.venv` folder in the workspace was created on another platform and should not be used from PowerShell.

macOS/Linux:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Use `python -m pip` from the same interpreter you plan to run `main.py` with. That avoids installing packages into a different Python than the one launching the bot.

Create `.env` from `.env.example`, then set at least:

```dotenv
DISCORD_TOKEN="your_discord_bot_token"
MAIN_LLM_MODEL="gemini/gemini-2.5-flash"
DECISION_LLM_MODEL="gemini/gemini-2.5-flash-lite"
GEMINI_API_KEY="your_gemini_key"
```

Run:

```bash
python main.py
```

On Windows PowerShell you can also run `.\run_bot.ps1`, which uses the repo's `venv` explicitly.

## Runtime Flow

For every human server message, `bot/cogs/event_handler.py` performs this flow:

1. Ignore bot messages and duplicate events.
2. Load merged guild/channel settings from `data/settings.json`.
3. Update summary/profile counters and schedule background updates when triggers are reached.
4. Stop early if the channel is LLM-blacklisted.
5. Debounce same-user message chains when enabled.
6. Wait for typing to pause when typing wait is enabled.
7. Bypass the decision model for ordinary direct bot mentions and replies to the bot, except explicit GIF requests.
8. If ambient decisions are enabled, ask `DECISION_LLM_MODEL` for one JSON action:
   - `reply`
   - `react`
   - `gif`
   - `none`
9. If the action is `gif`, search GIPHY with the model's short `gif_query` and send the top result.
10. If the action is `reply`, build context for `MAIN_LLM_MODEL`, optionally allow local tools including `search_giphy_gif`, generate the final message, sanitize it, and send it.

The bot avoids jumping into busy uninvolved conversations: if there are at least 4 recent human messages from at least 2 authors within about 45 seconds and the bot has not been involved recently, ambient decision handling is skipped.

## Context Assembly

`bot/context_manager.py` builds the model context in this order:

1. `prompts/CAPABILITIES_PROMPT.md` plus behavior prompt.
2. Bot identity: bot username and bot user ID.
3. Available server emojis, including safe message/reaction formats.
4. Current channel summary.
5. Stored summaries for explicitly mentioned channels, when visible and available.
6. Manual note and AI summary for the current message author.
7. Recent channel messages and reply chain context.
8. Reply-chain media context, when relevant.
9. Current message or a command-specific custom prompt.

Recent history and summaries are added as system/background context. The current message is the live user turn.

Context defaults:

| Setting path | Default | Meaning |
| --- | ---: | --- |
| `context.history_messages` | `15` | Recent channel messages included in background context. |
| `context.reply_chain_limit` | `5` | Maximum referenced messages followed backward. |
| `context.mentioned_channel_summary_limit` | `5` | Mentioned channel summaries to preload. |
| `context.mentioned_channel_summary_max_chars` | `2500` | Per-mentioned-channel summary limit. |

Admins can inspect the generated context with `/context show`.

## Exact Local LLM Tools

These are the real function tool names exposed by `bot/discord_tools.py`. They are available only to the main reply model when:

- `TOOLS_ENABLED=True`
- the effective guild/channel setting does not disable tools
- the current model request does not already include image parts

The tools are read-only. Discord channel/message tools use the origin user's visible channels and the bot's permissions. GIPHY tools only return GIPHY URLs. Web tools only fetch public HTTP/HTTPS URLs and reject localhost/private network targets.

| Tool name | Required args | Optional args | Purpose |
| --- | --- | --- | --- |
| `list_channels` | none | `messageable_only`, `include_threads`, `limit` | List visible server channels and IDs. |
| `resolve_channel` | `query` | `include_threads`, `limit` | Resolve channel name, mention, or ID. |
| `get_channel_summary` | `channel_id` | `max_chars` | Read the stored summary for a visible channel. |
| `search_messages` | none | `query`, `channel_id`, `author_id`, `include_all_readable_channels`, `after_iso`, `before_iso`, `limit`, `history_limit` | Search recent visible message history. |
| `fetch_message` | `channel_id`, `message_id` | none | Fetch one visible Discord message. |
| `get_recent_messages` | none | `channel_id`, `limit` | Fetch recent visible messages from one channel. |
| `get_user_profile` | none | `user_id`, `query`, `include_activities`, `include_roles`, `include_profile_notes`, `max_roles` | Fetch Discord profile details plus stored notes/summaries. |
| `search_giphy_gif` | `query` | none | Search GIPHY and return a GIF URL the main model can send as the full reply. |
| `web_search` | `query` | `limit` | Search the public web and return titles, snippets, URLs. |
| `fetch_web_page` | `url` | `max_chars` | Extract readable text from a public web page. |

Tool loop behavior:

- `TOOL_MAX_ROUNDS=0` means unlimited rounds.
- Any positive `TOOL_MAX_ROUNDS` caps tool-call rounds before forcing a final answer.
- Duplicate tool calls are detected and not executed again.
- If a mentioned channel summary is already preloaded, redundant `get_channel_summary` calls for that channel are skipped.
- `search_giphy_gif` requires `GIFS_ENABLED=True` and `GIPHY_API_KEY`.
- `WEB_SEARCH_ENABLED=False` disables both `web_search` and `fetch_web_page`.
- `WEB_SEARCH_AUTO_ENABLED=True` separately enables provider-side web search for models that LiteLLM reports as supporting it, but not when local tools or JSON response format are used.

## Media Processing

Attachment handling is model-aware. The decision model and the main model each get media processed according to their own detected capabilities.

Supported groups:

| Media group | Examples | Main behavior |
| --- | --- | --- |
| Images | PNG, JPEG, WebP, GIF | Vision models receive image parts. Text-only models receive OCR when available. Animated GIFs can be sampled into frames. |
| Audio | MP3, WAV, FLAC, OGG, M4A, OPUS, etc. | Transcribed locally by `faster-whisper` or `openai-whisper` fallback. |
| Video | MP4, WebM, MOV, MKV, AVI, etc. | Uses `ffprobe`/`ffmpeg`, extracts audio transcript and sampled frames. Frames go to vision models; OCR can be used for text models. |
| PDF | PDF | Direct PDF is attempted only if model support says yes; otherwise text is extracted with `pdfplumber` or `PyPDF2`. |
| Office | DOCX, XLSX, PPTX | Extracts paragraphs, sheets/rows, or slide text. PPTX needs `python-pptx`. |
| Text | `txt`, `md`, `json`, `csv`, `py`, `js`, `html`, `css`, `xml`, `yaml`, `yml`, `toml`, `ini`, `log`, `sql` | Decodes UTF-8 with replacement and includes file text. |
| Other files | Any unmatched file | Includes metadata only when enabled. |

Default media setting paths can be overridden through `/media config set`:

| Path | Default |
| --- | --- |
| `images.enabled` | `DEFAULT_MEDIA_IMAGES_ENABLED` |
| `images.max_size_mb` | `10` |
| `images.ocr_enabled` | `true` |
| `images.include_ocr_for_vision_models` | `true` |
| `images.max_ocr_chars` | `4000` |
| `images.description_fallback` | `true` |
| `images.gif.extract_frames` | `true` |
| `images.gif.max_frames` | `DEFAULT_GIF_MAX_FRAMES` |
| `images.gif.frame_quality` | `DEFAULT_GIF_FRAME_QUALITY` |
| `audio.enabled` | `DEFAULT_MEDIA_AUDIO_ENABLED` |
| `audio.max_size_mb` | `25` |
| `audio.max_duration_seconds` | `300` |
| `audio.transcribe` | `true` |
| `audio.include_timestamps` | `false` |
| `audio.max_transcript_chars` | `12000` |
| `video.enabled` | `DEFAULT_MEDIA_VIDEO_ENABLED` |
| `video.max_size_mb` | `DEFAULT_VIDEO_MAX_SIZE_MB` |
| `video.max_duration_seconds` | `120` |
| `video.extract_audio` | `true` |
| `video.extract_frames` | `true` |
| `video.frame_interval_seconds` | `10` |
| `video.max_frames` | `DEFAULT_VIDEO_MAX_FRAMES` |
| `video.frame_quality` | `DEFAULT_VIDEO_FRAME_QUALITY` |
| `video.probe_timeout_seconds` | `DEFAULT_MEDIA_PROBE_TIMEOUT_SECONDS` |
| `video.frame_timeout_seconds` | `DEFAULT_VIDEO_FRAME_TIMEOUT_SECONDS` |
| `video.ocr_frames_for_text_models` | `true` |
| `video.max_transcript_chars` | `12000` |
| `pdf.enabled` | `DEFAULT_MEDIA_PDF_ENABLED` |
| `pdf.max_size_mb` | `10` |
| `pdf.preserve_formatting` | `true` |
| `office_documents.enabled` | `DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED` |
| `office_documents.max_size_mb` | `10` |
| `office_documents.preserve_structure` | `true` |
| `text_files.enabled` | `DEFAULT_MEDIA_TEXT_FILES_ENABLED` |
| `text_files.max_size_mb` | `5` |
| `text_files.supported_extensions` | text extension list above |
| `other_files.enabled` | `DEFAULT_MEDIA_OTHER_FILES_ENABLED` |
| `other_files.max_size_mb` | `20` |
| `other_files.include_metadata_only` | `true` |

The audio and video settings also inherit STT fields from the environment: `stt_engine`, `stt_model`, `stt_device`, `stt_compute_type`, `stt_beam_size`, `stt_vad_filter`, and `stt_language`.

## Slash Commands

Commands are loaded from `bot/cogs/*.py` and synced globally by `main.py`.

### LLM and Admin

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/llm settings` | Administrator | `model`, `behavior_prompt`, `summarize_every_messages`, `initial_summarize_messages`, `summarize_every_hours` | Set server-level model, behavior prompt, and summary triggers. |
| `/llm decision` | Administrator | `enabled`, `channel` | View or set ambient decision-model behavior for the server or a channel. |
| `/llm blacklist` | Administrator | `channel`, `blacklisted` | View, block, or unblock LLM-generated output in a channel. |
| `/channel override` | Administrator | `channel`, `model`, `behavior_prompt`, `summarize_every_messages` | Override selected settings for one channel. |
| `/model info` | Administrator | `model` | Show detected model capabilities: vision, audio, PDF, web search. |
| `/backup` | Administrator | none | Create manual backups of settings and data. |
| `/reload` | Administrator | `component` = `cogs`, `config`, `prompts`, or `all` | Reload live bot components. |
| `/restart` | Administrator | none | Re-exec the current bot process. |
| `/say` | Administrator, checked at runtime | `message`, `channel` | Make the bot send a message. Opens a modal if `message` is omitted. |
| `/retry` | Origin author or Manage Messages/Admin for the recorded reply | none | Delete and regenerate the bot's last generated reply in the channel. |

### Context, Summaries, Profiles

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/context reset` | Administrator | `target` = `channel` or `guild` | Clear stored summary context for the current channel or guild. |
| `/context show` | Administrator | `message_id`, `raw_format`, `show_gif_frames`, `include_bot_identity`, `include_channel_summary`, `include_user_profiles`, `include_conversation_history`, `include_reply_chain`, `include_current_message`, `include_server_emojis` | Show the exact context that would be sent to the LLM. |
| `/summary view` | Manage Messages | `channel` | View a channel summary. |
| `/summary update` | Manage Messages | `channel` | Force a channel summary update. |
| `/summary clear` | Administrator | `channel` | Clear one channel summary and reset counters. |
| `/summary settings` | Administrator | `channel`, `summarize_every_messages`, `summarize_every_hours` | View or change per-channel summary triggers. |
| `/note add` | Manage Messages | `user`, `note` | Add or replace a manual note in a user profile. |
| `/note view` | Manage Messages | `user` | View stored manual note, AI summary, and metadata. |
| `/note refresh ai` | Manage Messages | `user` | Force an AI profile summary refresh. |
| `/user profile` | Administrator | `action`, `user`, `profile_update_every_messages`, `profile_update_every_hours` | Show profile stats, force user profile update, or configure profile triggers. |

### Media

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/media config view` | Administrator | none | View effective custom media settings for the server. |
| `/media config set` | Administrator | `media_type`, `setting`, `value` | Set a nested media setting. Example: `media_type=images`, `setting=gif.max_frames`, `value=4`. |

Valid `media_type` values:

- `images`
- `audio`
- `video`
- `pdf`
- `office_documents`
- `text_files`
- `other_files`

### Fun and Utility

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/fun insult` | None | `user` | Generate a personalized playful insult. |
| `/fun compliment` | None | `user` | Generate a personalized compliment. |
| `/fun reverse` | None | `user` | Generate aggressive-sounding praise. |
| `/mock message` | None | `user` | Mock the user's latest recent message in alternating case. |
| `/mock avatar` | None | `user` | Ask the LLM to mock the user's avatar; uses vision when supported. |

### Leveling

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/level profile` | None | `user` | Show a user's level, XP, rank, message count, and voice time. |
| `/level leaderboard` | None | none | Show an interactive server XP leaderboard. |
| `/level settings` | Administrator | `enabled`, `cooldown`, `min_length`, `notify` | View or configure leveling. |
| `/level roles` | Administrator | `level`, `role`, `action` | Add, remove, or inspect level roles. |
| `/level prestige settings` | Administrator | `prestige_level`, `role`, `emoji`, `action` | Configure prestige role data. |
| `/level prestige requirement` | Administrator | `level` | Set the level required to prestige. |
| `/level voice xp` | Administrator | `enabled`, `xp_per_minute` | Configure voice XP settings. |

### Diagnostics

| Command | Permission | Parameters | Description |
| --- | --- | --- | --- |
| `/test pagination` | Administrator | none | Test the pagination UI. |
| `/test chunking` | Administrator | none | Test long-message chunking. |

## Environment Variables

See `.env.example` for the complete template. The code reads these variables in `bot/config.py` and `main.py`.

### Discord and Logging

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | none | Required bot token. |
| `BOT_ACTIVITY_TYPE` | `watching` | One of `watching`, `playing`, `listening`, `streaming`. |
| `BOT_ACTIVITY_TEXT` | `conversations unfold` | Presence text. |
| `LOG_LEVEL` | `DEBUG` | Root logger level. |
| `LOG_CONSOLE_LEVEL` | `INFO` | Terminal log level. |
| `LOG_FILE_LEVEL` | `DEBUG` | File log level. |
| `LOG_DIR` | `logs` | Log directory. |
| `LOG_FILE` | `bot.log` | Log filename. |
| `LOG_MAX_BYTES` | `10485760` | Rotating log size. |
| `LOG_BACKUP_COUNT` | `5` | Rotating log backup count. |

### Models and Decisions

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAIN_LLM_MODEL` | `gemini/gemini-2.5-flash` | Model used for final replies and LLM command output. |
| `DECISION_LLM_MODEL` | `gemini/gemini-2.5-flash-lite` | Model used for reply/react/gif/none decisions. |
| `DECISION_LLM_ENABLED` | `True` | Enables ambient decisions. If false, only mentions/replies trigger normal replies. |
| `MAIN_LLM_RATE_LIMIT_ENABLED` | `False` | Enables main model request pacing. |
| `MAIN_LLM_RATE_LIMIT_SECONDS` | `2` | Minimum spacing for main model calls. |
| `DECISION_LLM_RATE_LIMIT_ENABLED` | `False` | Enables decision model request pacing. |
| `DECISION_LLM_RATE_LIMIT_SECONDS` | `0.5` | Minimum spacing for decision model calls. |

LiteLLM reads provider credentials from standard provider env vars. `.env.example` includes `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `MISTRAL_API_KEY`.

For Ollama models, use LiteLLM model names such as `ollama/model-name` or `ollama_chat/model-name`. The bot can inspect Ollama capabilities through `OLLAMA_API_BASE` or `OLLAMA_HOST`.

### Reply Chain and GIF Behavior

| Variable | Default | Meaning |
| --- | --- | --- |
| `REPLY_CHAIN_DEBOUNCE_ENABLED` | `True` | Groups same-user fragments before deciding. |
| `REPLY_CHAIN_DEBOUNCE_SECONDS` | `2.0` | Debounce wait. |
| `REPLY_CHAIN_WAIT_FOR_TYPING` | `True` | Wait while the user appears to still be typing. |
| `REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS` | `12.0` | Maximum typing wait. |
| `REPLY_CHAIN_LONG_TYPING_SECONDS` | `10.0` | Threshold for long-typing decision context. |
| `TYPING_ACTIVE_SECONDS` | `8.0` | How long a Discord typing event is treated as active. |
| `GIFS_ENABLED` | `True` | Allows GIPHY GIF responses when `GIPHY_API_KEY` is configured. |
| `GIPHY_API_KEY` | none | GIPHY REST API key used for GIF search. Use the GIPHY API option when creating the key. |
| `GIPHY_RATING` | `pg-13` | GIPHY content rating filter: `g`, `pg`, `pg-13`, or `r`. |
| `GIPHY_LANG` | `en` | 2-letter language code for regional GIF search. |
| `GIPHY_TIMEOUT_SECONDS` | `8.0` | Timeout for GIPHY API calls. |

GIF replies use GIPHY Search dynamically. The decision model can return a short `gif_query` for ambient GIF responses, and the main reply model can call `search_giphy_gif` during direct replies. The bot sends only the canonical GIPHY URL, with no caption, and falls back to text for direct interactions if no GIF can be found.

### Tools and Web

| Variable | Default | Meaning |
| --- | --- | --- |
| `TOOLS_ENABLED` | `True` | Enables local read-only tool calls for the main model. |
| `TOOL_MAX_ROUNDS` | `0` | Tool-call round cap. `0` means unlimited. |
| `WEB_SEARCH_ENABLED` | `True` | Enables local `web_search` and `fetch_web_page`. |
| `WEB_SEARCH_AUTO_ENABLED` | `False` | Enables provider-side web search when supported and compatible. |
| `WEB_SEARCH_CONTEXT_SIZE` | `medium` | Provider-side search context size. |
| `WEB_FETCH_MAX_CHARS` | `6000` | Default extraction cap for `fetch_web_page`. |

### Summaries and Profiles

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEFAULT_SUMMARIZE_EVERY_MESSAGES` | `100` | Message trigger for summary refresh. |
| `INITIAL_SUMMARY_MESSAGES` | `1000` | History scan limit for first summary. |
| `DEFAULT_SUMMARIZE_EVERY_HOURS` | `24` | Time trigger for summary refresh. |
| `DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES` | `50` | Message trigger for user profile refresh. |
| `DEFAULT_PROFILE_UPDATE_EVERY_HOURS` | `168` | Time trigger for user profile refresh. |

### Media and STT

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEFAULT_MEDIA_IMAGES_ENABLED` | `True` | Enables image processing by default. |
| `DEFAULT_MEDIA_AUDIO_ENABLED` | `True` | Enables audio processing by default. |
| `DEFAULT_MEDIA_VIDEO_ENABLED` | `True` | Enables video processing by default. |
| `DEFAULT_MEDIA_PDF_ENABLED` | `True` | Enables PDF processing by default. |
| `DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED` | `True` | Enables Office document processing by default. |
| `DEFAULT_MEDIA_TEXT_FILES_ENABLED` | `True` | Enables text file processing by default. |
| `DEFAULT_MEDIA_OTHER_FILES_ENABLED` | `True` | Enables metadata fallback for other files. |
| `DEFAULT_GIF_MAX_FRAMES` | `5` in code, `8` in `.env.example` | Maximum sampled GIF frames. `.env` overrides code default. |
| `DEFAULT_GIF_FRAME_QUALITY` | `85` | JPEG quality for GIF frames. |
| `LOCAL_STT_ENGINE` | `faster-whisper` | Local transcription engine. Also accepts `openai-whisper` or `whisper`. |
| `LOCAL_STT_MODEL` | `base` | STT model name/path. |
| `LOCAL_STT_DEVICE` | `cpu` | `cpu` or `cuda`. |
| `LOCAL_STT_COMPUTE_TYPE` | `int8` | Faster-whisper compute type. |
| `LOCAL_STT_BEAM_SIZE` | `5` | STT beam size. |
| `LOCAL_STT_VAD_FILTER` | `True` | Voice activity detection filter. |
| `LOCAL_STT_LANGUAGE` | unset | Optional fixed transcription language. |
| `DEFAULT_VIDEO_MAX_SIZE_MB` | `250` | Default video size cap. |
| `DEFAULT_MEDIA_PROBE_TIMEOUT_SECONDS` | `10` | `ffprobe` timeout. |
| `DEFAULT_VIDEO_FRAME_TIMEOUT_SECONDS` | `20` | Frame extraction timeout. |
| `DEFAULT_VIDEO_MAX_FRAMES` | `8` | Maximum sampled video frames. |
| `DEFAULT_VIDEO_FRAME_QUALITY` | `85` | JPEG quality for video frames. |

### Backups

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKUP_INTERVAL_HOURS` | `24` | Automatic backup interval. |

## Stored Data

The bot creates JSON files automatically if missing.

| Path | Purpose |
| --- | --- |
| `data/settings.json` | Guild settings, channel overrides, media config, blacklist, summary/profile triggers. |
| `data/data.json` | Channel summaries, summary metadata, user manual notes, AI user summaries, profile counters. |
| `data/levels.json` | Leveling config and per-user XP/voice/message data. |
| `.backup/settings_YYYYMMDD_HHMMSS.json` | Timestamped settings backups. |
| `.backup/data_YYYYMMDD_HHMMSS.json` | Timestamped runtime data backups. |
| `logs/bot.log` | Rotating file logs by default. |

## Important Settings Stored in `data/settings.json`

Common guild-level keys:

```json
{
  "model": "gemini/gemini-2.5-flash",
  "behavior_prompt": "custom behavior text",
  "decision_llm_enabled": true,
  "decision_llm_model": "gemini/gemini-2.5-flash-lite",
  "gifs_enabled": true,
  "tools_enabled": true,
  "llm_blacklisted_channels": ["123456789012345678"],
  "summarize_every_messages": 100,
  "initial_summarize_messages": 1000,
  "summarize_every_hours": 24,
  "profile_update_every_messages": 50,
  "profile_update_every_hours": 168,
  "context": {
    "history_messages": 15,
    "reply_chain_limit": 5,
    "mentioned_channel_summary_limit": 5,
    "mentioned_channel_summary_max_chars": 2500
  },
  "media": {
    "images": {
      "enabled": true,
      "gif": {
        "max_frames": 5
      }
    }
  },
  "channel_overrides": {
    "123456789012345678": {
      "model": "openai/gpt-4.1-mini",
      "decision_llm_enabled": false
    }
  }
}
```

`ContextManager.get_guild_and_channel_settings()` merges guild settings with `channel_overrides[channel_id]`. Media settings are deep-merged over code defaults, so partial media overrides are supported.

## Project Structure

```text
.
|-- main.py
|-- requirements.txt
|-- run_bot.sh
|-- bot/
|   |-- config.py
|   |-- context_manager.py
|   |-- discord_tools.py
|   |-- llm_provider.py
|   |-- store.py
|   `-- cogs/
|       |-- admin_commands.py
|       |-- event_handler.py
|       |-- fun_commands.py
|       |-- levelup_commands.py
|       |-- profile_commands.py
|       |-- say.py
|       `-- utilities.py
|-- prompts/
|   |-- BEHAVIOR_PROMPT.md
|   `-- CAPABILITIES_PROMPT.md
|-- data/
|   |-- settings.json
|   |-- data.json
|   `-- levels.json
`-- .backup/
```

Core files:

- `main.py`: bot startup, intents, cog loading, command sync, logging, backups, graceful shutdown.
- `bot/config.py`: environment loading and defaults.
- `bot/llm_provider.py`: LiteLLM calls, rate limiting, capability checks, provider-side web search handling.
- `bot/context_manager.py`: settings merge, context building, summaries, profiles, media processing.
- `bot/discord_tools.py`: local function tools exposed to tool-capable LLMs.
- `bot/store.py`: async JSON storage and backups.
- `bot/cogs/event_handler.py`: message listener, decision flow, reply generation, tool loop, retry command.

## Troubleshooting

The bot does not answer:

- Check whether the channel is blacklisted with `/llm blacklist`.
- Check `/llm decision`; if ambient decisions are disabled, the bot answers only direct mentions/replies.
- Use `/context show` to inspect what the model receives.
- Check `logs/bot.log` for provider errors.

Tools are not being called:

- Confirm `TOOLS_ENABLED=True`.
- Check guild/channel settings for `tools_enabled=false` or `tools.enabled=false`.
- Tools are skipped when the model request already contains image parts.
- Web tools also require `WEB_SEARCH_ENABLED=True`.

Images or GIFs are not understood:

- Check `/model info` for the selected model.
- For text-only models, install Tesseract and `pytesseract` for OCR.
- For Ollama vision models, make sure `OLLAMA_HOST` or `OLLAMA_API_BASE` points to the running Ollama server so capability inspection can work.

Video/audio processing fails:

- Install `ffmpeg` and `ffprobe` and make sure they are in `PATH`.
- Install or configure `faster-whisper`; optional fallback is `openai-whisper`/`whisper`.
- Check file size and duration limits in `/media config view`.

Slash commands do not appear:

- Restart the bot and check startup logs for cog load or command sync errors.
- Confirm the bot was invited with `applications.commands`.
- Global command sync can take time to appear in Discord.

Level roles are not assigned:

- Give the bot `Manage Roles`.
- Put the bot role above the roles it should assign.
- Check `/level roles` and `/level settings`.

## License

See `LICENSE`.
