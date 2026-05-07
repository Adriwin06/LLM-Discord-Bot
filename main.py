# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/main.py
import discord
from discord.ext import commands, tasks
import os
import asyncio
import sys
import logging

# Import core bot components
from bot.config import Config
from bot.store import Store
from bot.llm_provider import LiteLLMProvider
from bot.context_manager import ContextManager
from bot.discord_tools import DiscordToolManager


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LLMDiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)

        self.config = Config()
        self.store = Store()
        self.llm_provider = LiteLLMProvider(self.config)
        self.context_manager = ContextManager(self.store, self.llm_provider, self)
        self.tool_manager = DiscordToolManager(self)
        self.shutdown_event = asyncio.Event()

    async def setup_hook(self):
        """The setup hook is called when the bot is ready to start."""
        logging.info("Starting setup hook...")
        
        # Load cogs
        cogs_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "bot", "cogs")
        for filename in os.listdir(cogs_path):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = filename[:-3]
                try:
                    await self.load_extension(f"bot.cogs.{cog_name}")
                    logging.info(f"Successfully loaded cog: {cog_name}")
                except Exception as e:
                    logging.error(f"Failed to load cog {cog_name}: {e}")

        # Sync slash commands with Discord
        try:
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} command(s) with Discord")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")

        # Start background tasks
        self.backup_task.start()
        logging.info("Backup task started.")

    async def on_ready(self):
        """Event handler for when the bot is connected and ready."""
        logging.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logging.info('------')
        
        # Set bot's presence
        activity_type_str = self.config.BOT_ACTIVITY_TYPE.lower()
        activity_type = discord.ActivityType.watching
        if activity_type_str == "playing":
            activity_type = discord.ActivityType.playing
        elif activity_type_str == "listening":
            activity_type = discord.ActivityType.listening
        elif activity_type_str == "streaming":
            activity_type = discord.ActivityType.streaming
            
        activity = discord.Activity(name=self.config.BOT_ACTIVITY_TEXT, type=activity_type)
        await self.change_presence(activity=activity)
        logging.info(f"Bot activity set to: {self.config.BOT_ACTIVITY_TYPE} '{self.config.BOT_ACTIVITY_TEXT}'")

    @tasks.loop(hours=Config().BACKUP_INTERVAL_HOURS)
    async def backup_task(self):
        """Background task to automatically back up data files."""
        logging.info("Starting automatic backup...")
        await self.store.backup_data()
        logging.info("Automatic backup completed.")

    @backup_task.before_loop
    async def before_backup_task(self):
        """Wait until the bot is ready before starting the backup loop."""
        await self.wait_until_ready()

    async def close(self):
        """Gracefully close the bot and clean up resources."""
        logging.info("Starting graceful shutdown...")
        
        # Set shutdown event
        if hasattr(self, 'shutdown_event'):
            self.shutdown_event.set()
        
        try:
            # Cancel background tasks
            if hasattr(self, 'backup_task') and self.backup_task.is_running():
                self.backup_task.cancel()
                try:
                    await asyncio.wait_for(self.backup_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                logging.info("Backup task cancelled")
            
            # Close LLM provider if it has cleanup methods
            if hasattr(self.llm_provider, 'cleanup'):
                try:
                    await asyncio.wait_for(self.llm_provider.cleanup(), timeout=5.0)
                    logging.info("LLM provider cleaned up")
                except asyncio.TimeoutError:
                    logging.warning("LLM provider cleanup timed out")
                except Exception as e:
                    logging.error(f"Error cleaning up LLM provider: {e}")
            
            # Give a brief moment for any remaining cleanup
            await asyncio.sleep(0.2)
            
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
        finally:
            # Close the Discord client
            try:
                await super().close()
                logging.info("Discord client closed")
            except Exception as e:
                logging.error(f"Error closing Discord client: {e}")

async def run_bot():
    """Run the bot with proper async handling."""
    bot = LLMDiscordBot()
    
    token = bot.config.DISCORD_TOKEN
    if not token:
        logging.critical("DISCORD_TOKEN is not set in the environment variables. Bot cannot start.")
        return

    try:
        logging.info("Starting Discord bot... (Press Ctrl+C to stop)")
        await bot.start(token)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("Shutdown signal received. Shutting down gracefully...")
    except discord.errors.LoginFailure:
        logging.critical("Failed to log in. Please check your DISCORD_TOKEN.")
        raise
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}")
        raise
    finally:
        if not bot.is_closed():
            try:
                # Give the bot time to close gracefully
                await asyncio.wait_for(bot.close(), timeout=10.0)
            except asyncio.TimeoutError:
                logging.warning("Bot shutdown timed out, forcing close")
            except Exception as e:
                logging.error(f"Error during bot shutdown: {e}")
        
        # Wait a moment for any remaining cleanup
        try:
            await asyncio.sleep(0.5)
        except Exception:
            pass
            
        logging.info("Bot shutdown complete.")

def main():
    """Main function to run the bot."""
    try:
        # Use asyncio.run for cross-platform compatibility
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt (Ctrl+C). Shutdown complete.")
        print("\nBot stopped by user. Goodbye! 👋")
    except Exception as e:
        logging.critical(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
