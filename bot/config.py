# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/config.py
import logging
import os
import re
from dotenv import load_dotenv

class Config:
    @staticmethod
    def _parse_model_list(raw: str) -> list:
        models = [item.strip() for item in (raw or "").split(",") if item.strip()]
        for model in models:
            if "/" not in model and any(hint in model.lower() for hint in ("gemini", "mistral", "claude", "llama")):
                logging.warning(
                    "Configured model %r has no provider prefix; LiteLLM may route it to the wrong "
                    "backend (e.g. bare gemini-* goes to Vertex AI, not AI Studio). Did you mean %r?",
                    model,
                    f"{'gemini' if 'gemini' in model.lower() else 'mistral' if 'mistral' in model.lower() else 'anthropic' if 'claude' in model.lower() else 'ollama'}/{model}",
                )
        return models

    @staticmethod
    def _parse_id_list(raw: str) -> set:
        """Parse a comma/space-separated list of Discord IDs into a set of strings."""
        ids = set()
        for item in re.split(r"[\s,]+", raw or ""):
            item = item.strip()
            if not item:
                continue
            if item.isdigit():
                ids.add(item)
            else:
                logging.warning("Ignoring non-numeric Discord ID in ID list: %r", item)
        return ids

    def __init__(self):
        load_dotenv()

        # Discord Configuration
        self.DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
        self.BOT_ACTIVITY_TYPE = os.getenv("BOT_ACTIVITY_TYPE", "watching")
        self.BOT_ACTIVITY_TEXT = os.getenv("BOT_ACTIVITY_TEXT", "conversations unfold")

        # LLM Configuration
        self.MAIN_LLM_MODEL = os.getenv("MAIN_LLM_MODEL", "gemini/gemini-2.5-flash")
        self.DECISION_LLM_MODEL = os.getenv("DECISION_LLM_MODEL", "gemini/gemini-2.5-flash-lite")
        self.DECISION_LLM_ENABLED = os.getenv("DECISION_LLM_ENABLED", "True").lower() == "true"
        # Comma-separated model lists tried in order when the primary model fails (rate limits, outages, etc.)
        self.MAIN_LLM_FALLBACK_MODELS = self._parse_model_list(os.getenv("MAIN_LLM_FALLBACK_MODELS", ""))
        self.DECISION_LLM_FALLBACK_MODELS = self._parse_model_list(os.getenv("DECISION_LLM_FALLBACK_MODELS", ""))

        # Prompt Configuration
        behavior_prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/BEHAVIOR_PROMPT.md")
        with open(behavior_prompt_path, "r", encoding="utf-8") as f:
            self.BEHAVIOR_PROMPT = f.read().strip()

        capabilities_prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/CAPABILITIES_PROMPT.md")
        with open(capabilities_prompt_path, "r", encoding="utf-8") as f:
            self.CAPABILITIES_PROMPT = f.read().strip()

        developer_prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/DEVELOPER_PROMPT.md")
        with open(developer_prompt_path, "r", encoding="utf-8") as f:
            self.DEVELOPER_PROMPT = f.read().strip()

        # Developer Override Configuration
        # Comma/space-separated Discord user IDs treated as trusted developers/operators.
        # When one of them sends a message, a high-priority override is injected so the bot
        # obeys them directly instead of deflecting in-character.
        self.DEVELOPER_USER_IDS = self._parse_id_list(os.getenv("DEVELOPER_USER_IDS", ""))
        self.DEVELOPER_OVERRIDE_ENABLED = os.getenv("DEVELOPER_OVERRIDE_ENABLED", "True").lower() == "true"

        # Rate Limiting
        self.MAIN_LLM_RATE_LIMIT_ENABLED = os.getenv("MAIN_LLM_RATE_LIMIT_ENABLED", "False").lower() == "true"
        self.MAIN_LLM_RATE_LIMIT_SECONDS = float(os.getenv("MAIN_LLM_RATE_LIMIT_SECONDS", 2))
        self.DECISION_LLM_RATE_LIMIT_ENABLED = os.getenv("DECISION_LLM_RATE_LIMIT_ENABLED", "False").lower() == "true"
        self.DECISION_LLM_RATE_LIMIT_SECONDS = float(os.getenv("DECISION_LLM_RATE_LIMIT_SECONDS", 0.5))
        self.REPLY_CHAIN_DEBOUNCE_ENABLED = os.getenv("REPLY_CHAIN_DEBOUNCE_ENABLED", "True").lower() == "true"
        self.REPLY_CHAIN_DEBOUNCE_SECONDS = float(os.getenv("REPLY_CHAIN_DEBOUNCE_SECONDS", 2.0))
        self.REPLY_CHAIN_WAIT_FOR_TYPING = os.getenv("REPLY_CHAIN_WAIT_FOR_TYPING", "True").lower() == "true"
        self.REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS = float(os.getenv("REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS", 12.0))
        self.REPLY_CHAIN_LONG_TYPING_SECONDS = float(os.getenv("REPLY_CHAIN_LONG_TYPING_SECONDS", 10.0))
        self.TYPING_ACTIVE_SECONDS = float(os.getenv("TYPING_ACTIVE_SECONDS", 12.0))
        self.AMBIENT_REPLY_COOLDOWN_SECONDS = float(os.getenv("AMBIENT_REPLY_COOLDOWN_SECONDS", 90.0))
        self.GIFS_ENABLED = os.getenv("GIFS_ENABLED", "True").lower() == "true"
        self.GIPHY_API_KEY = os.getenv("GIPHY_API_KEY", "")
        self.GIPHY_RATING = os.getenv("GIPHY_RATING", "pg-13")
        self.GIPHY_LANG = os.getenv("GIPHY_LANG", "en")
        self.GIPHY_TIMEOUT_SECONDS = float(os.getenv("GIPHY_TIMEOUT_SECONDS", 8.0))
        self.GIPHY_ANALYZE_BEFORE_SEND = os.getenv("GIPHY_ANALYZE_BEFORE_SEND", "False").lower() == "true"
        self.GIPHY_ANALYSIS_MAX_CANDIDATES = int(os.getenv("GIPHY_ANALYSIS_MAX_CANDIDATES", 3))
        self.GIPHY_ANALYSIS_MODEL = os.getenv("GIPHY_ANALYSIS_MODEL", "")
        self.GIPHY_CANDIDATE_POOL = int(os.getenv("GIPHY_CANDIDATE_POOL", 5))
        self.GIPHY_PICK_TOP_N = int(os.getenv("GIPHY_PICK_TOP_N", 3))

        # Web Search Configuration
        self.WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "True").lower() == "true"
        self.WEB_SEARCH_AUTO_ENABLED = os.getenv("WEB_SEARCH_AUTO_ENABLED", "False").lower() == "true"
        self.WEB_SEARCH_CONTEXT_SIZE = os.getenv("WEB_SEARCH_CONTEXT_SIZE", "medium")
        self.WEB_FETCH_MAX_CHARS = int(os.getenv("WEB_FETCH_MAX_CHARS", 6000))

        # Agentic Tool Configuration
        self.TOOLS_ENABLED = os.getenv("TOOLS_ENABLED", "True").lower() == "true"
        self.TOOL_MAX_ROUNDS = int(os.getenv("TOOL_MAX_ROUNDS", 0))

        # Backup Configuration
        self.BACKUP_INTERVAL_HOURS = int(os.getenv("BACKUP_INTERVAL_HOURS", 24))

        # Summary and Profile Update Triggers
        self.DEFAULT_SUMMARIZE_EVERY_MESSAGES = int(os.getenv("DEFAULT_SUMMARIZE_EVERY_MESSAGES", 100))
        self.INITIAL_SUMMARY_MESSAGES = int(os.getenv("INITIAL_SUMMARY_MESSAGES", 1000))
        self.DEFAULT_SUMMARIZE_EVERY_HOURS = int(os.getenv("DEFAULT_SUMMARIZE_EVERY_HOURS", 24))
        self.DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES = int(os.getenv("DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES", 50))
        self.DEFAULT_PROFILE_UPDATE_EVERY_HOURS = int(os.getenv("DEFAULT_PROFILE_UPDATE_EVERY_HOURS", 168))

        # Media Processing Defaults
        self.DEFAULT_MEDIA_IMAGES_ENABLED = os.getenv("DEFAULT_MEDIA_IMAGES_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_AUDIO_ENABLED = os.getenv("DEFAULT_MEDIA_AUDIO_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_VIDEO_ENABLED = os.getenv("DEFAULT_MEDIA_VIDEO_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_PDF_ENABLED = os.getenv("DEFAULT_MEDIA_PDF_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED = os.getenv("DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_TEXT_FILES_ENABLED = os.getenv("DEFAULT_MEDIA_TEXT_FILES_ENABLED", "True").lower() == "true"
        self.DEFAULT_MEDIA_OTHER_FILES_ENABLED = os.getenv("DEFAULT_MEDIA_OTHER_FILES_ENABLED", "True").lower() == "true"

        # GIF Processing Defaults
        self.DEFAULT_GIF_MAX_FRAMES = int(os.getenv("DEFAULT_GIF_MAX_FRAMES", 5))
        self.DEFAULT_GIF_FRAME_QUALITY = int(os.getenv("DEFAULT_GIF_FRAME_QUALITY", 85))

        # Local Speech-to-Text Defaults
        self.LOCAL_STT_ENGINE = os.getenv("LOCAL_STT_ENGINE", "faster-whisper")
        self.LOCAL_STT_MODEL = os.getenv("LOCAL_STT_MODEL", "base")
        self.LOCAL_STT_DEVICE = os.getenv("LOCAL_STT_DEVICE", "cpu")
        self.LOCAL_STT_COMPUTE_TYPE = os.getenv("LOCAL_STT_COMPUTE_TYPE", "int8")
        self.LOCAL_STT_BEAM_SIZE = int(os.getenv("LOCAL_STT_BEAM_SIZE", 5))
        self.LOCAL_STT_VAD_FILTER = os.getenv("LOCAL_STT_VAD_FILTER", "True").lower() == "true"
        self.LOCAL_STT_LANGUAGE = os.getenv("LOCAL_STT_LANGUAGE") or None

        # Video Processing Defaults
        self.DEFAULT_VIDEO_MAX_SIZE_MB = float(os.getenv("DEFAULT_VIDEO_MAX_SIZE_MB", 250))
        self.DEFAULT_MEDIA_PROBE_TIMEOUT_SECONDS = float(os.getenv("DEFAULT_MEDIA_PROBE_TIMEOUT_SECONDS", 10))
        self.DEFAULT_VIDEO_FRAME_TIMEOUT_SECONDS = float(os.getenv("DEFAULT_VIDEO_FRAME_TIMEOUT_SECONDS", 20))
        self.DEFAULT_VIDEO_MAX_FRAMES = int(os.getenv("DEFAULT_VIDEO_MAX_FRAMES", 8))
        self.DEFAULT_VIDEO_FRAME_QUALITY = int(os.getenv("DEFAULT_VIDEO_FRAME_QUALITY", 85))
