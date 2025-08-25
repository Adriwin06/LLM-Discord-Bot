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
        self.BEHAVIOR_PROMPT = os.getenv("BEHAVIOR_PROMPT", "You are a helpful Discord bot assistant.")
        self.CAPABILITIES_PROMPT = os.getenv("CAPABILITIES_PROMPT", "You are a Discord bot with the following capabilities: You can reply to messages, react with emojis, mention users and roles using <mention user=\"Name\"> or <mention role=\"Name\"> syntax, process images/videos/audio/documents when supported by your model, and access web search when available.")

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
