# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/config.py
import os
from dotenv import load_dotenv

class Config:
    def __init__(self):
        load_dotenv()

        # Discord Configuration
        self.DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
        self.BOT_ACTIVITY_TYPE = os.getenv("BOT_ACTIVITY_TYPE", "watching")
        self.BOT_ACTIVITY_TEXT = os.getenv("BOT_ACTIVITY_TEXT", "conversations unfold")

        # LLM Configuration
        self.MAIN_LLM_MODEL = os.getenv("MAIN_LLM_MODEL", "gemini/gemini-2.5-flash")
        self.DECISION_LLM_MODEL = os.getenv("DECISION_LLM_MODEL", "gemini/gemini-2.5-flash-lite")

        # Prompt Configuration
        behavior_prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/BEHAVIOR_PROMPT.md")
        with open(behavior_prompt_path, "r", encoding="utf-8") as f:
            self.BEHAVIOR_PROMPT = f.read().strip()

        capabilities_prompt_path = os.path.join(os.path.dirname(__file__), "../prompts/CAPABILITIES_PROMPT.md")
        with open(capabilities_prompt_path, "r", encoding="utf-8") as f:
            self.CAPABILITIES_PROMPT = f.read().strip()

        # API Keys
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

        # Rate Limiting
        self.MAIN_LLM_RATE_LIMIT_ENABLED = os.getenv("MAIN_LLM_RATE_LIMIT_ENABLED", "False").lower() == "true"
        self.MAIN_LLM_RATE_LIMIT_SECONDS = float(os.getenv("MAIN_LLM_RATE_LIMIT_SECONDS", 2))
        self.DECISION_LLM_RATE_LIMIT_ENABLED = os.getenv("DECISION_LLM_RATE_LIMIT_ENABLED", "False").lower() == "true"
        self.DECISION_LLM_RATE_LIMIT_SECONDS = float(os.getenv("DECISION_LLM_RATE_LIMIT_SECONDS", 0.5))

        # Web Search Configuration
        self.WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "True").lower() == "true"
        self.WEB_SEARCH_CONTEXT_SIZE = os.getenv("WEB_SEARCH_CONTEXT_SIZE", "medium")

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
