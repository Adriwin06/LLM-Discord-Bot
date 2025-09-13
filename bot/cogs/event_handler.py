# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/event_handler.py
import discord
from discord.ext import commands
import logging
import json
import re
import asyncio
from datetime import datetime, timezone
from .utilities import MessageChunker

class EventHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Lock to prevent multiple messages from triggering summaries/profiles simultaneously
        self._processing_lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Use lock to prevent race conditions in message processing
        async with self._processing_lock:
            # Get settings first for bypass conditions
            _, settings = await self.bot.context_manager.build_context(message)
            
            # Update counters and trigger summaries/profiles
            await self._update_counters_and_triggers(message, settings)

            # Decision making
            should_reply, reaction = await self._should_reply_or_react(message, settings)

            if should_reply:
                await self._generate_and_send_reply(message, settings)
            elif reaction:
                try:
                    await message.add_reaction(reaction)
                except discord.HTTPException:
                    logging.warning(f"Failed to add reaction '{reaction}'. It might be an invalid or custom emoji not available.")

    async def _should_reply_or_react(self, message: discord.Message, settings: dict):
        # Bypass commands - don't trigger LLM for messages starting with "!"
        if message.content.startswith("!"):
            return False, None
        
        # Bypass conditions
        is_reply_to_bot = message.reference and message.reference.resolved.author == self.bot.user
        mentions_bot = self.bot.user in message.mentions

        if (settings.get("bypass_on_reply", True) and is_reply_to_bot) or \
           (settings.get("bypass_on_ping", True) and mentions_bot):
            logging.info(f"Bypassing decision model for message {message.id} due to direct interaction.")
            return True, None

        # Use decision LLM with context built specifically for that model
        decision_model = settings.get("decision_llm_model", self.bot.config.DECISION_LLM_MODEL)
        
        # Only build separate context if decision model is different from main model
        if decision_model == self.bot.config.MAIN_LLM_MODEL:
            # Reuse the existing context from build_context call
            decision_context, _ = await self.bot.context_manager.build_context(message)
        else:
            # Build context specifically for the decision model
            decision_context, _ = await self.bot.context_manager.build_context(message, model_name=decision_model)
        
        decision_prompt = """
        You are a decision-making model for a Discord bot.
        Based on the provided context, decide if the bot should reply, react with an emoji, or do nothing.
        The bot should reply if it's directly addressed, asked a question, or can provide a meaningful contribution.
        The bot should react if the message is emotional, a simple acknowledgement is needed, or contains engaging media.
        Otherwise, the bot should do nothing.
        
        Respond with a single JSON object with two keys:
        1. "action": a string, either "reply", "react", or "none".
        2. "reaction": a string containing a single emoji if the action is "react", otherwise null.
        
        Example: {"action": "reply", "reaction": null}
        Example: {"action": "react", "reaction": "👍"}
        Example: {"action": "none", "reaction": null}
        """
        
        decision_context[0]["content"] = decision_prompt

        response = await self.bot.llm_provider.create_completion(
            model=decision_model,
            messages=decision_context,
            response_format={"type": "json_object"}
        )

        if not response or not response.choices:
            # Fallback to main model if decision model fails and they are different
            if decision_model != self.bot.config.MAIN_LLM_MODEL:
                # Build context for main model and retry decision
                main_context, _ = await self.bot.context_manager.build_context(message, model_name=self.bot.config.MAIN_LLM_MODEL)
                main_context[0]["content"] = decision_prompt
                
                response = await self.bot.llm_provider.create_completion(
                    model=self.bot.config.MAIN_LLM_MODEL,
                    messages=main_context,
                    response_format={"type": "json_object"}
                )
            if not response or not response.choices:
                logging.error(f"Both decision and main models failed to make a decision for message {message.id}.")
                return False, None

        # Parse decision response
        try:
            # Clean the response content by removing markdown code blocks if present
            raw_content = response.choices[0].message.content.strip()
            cleaned_content = self._clean_json_response(raw_content)
            
            decision_json = json.loads(cleaned_content)
            action = decision_json.get("action", "none")
            reaction = decision_json.get("reaction")

            if action == "reply":
                return True, None
            if action == "react" and reaction:
                return False, reaction
            return False, None
        except (json.JSONDecodeError, KeyError):
            logging.error(f"Failed to parse decision JSON: {response.choices[0].message.content}")
            return False, None

    async def _update_counters_and_triggers(self, message: discord.Message, settings: dict):
        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)

        try:
            # Use the data store's lock to prevent race conditions
            async with self.bot.store._lock:
                # Get fresh data to avoid conflicts
                fresh_data = await self.bot.store.get_data()
                
                # Ensure paths exist
                if str(guild_id) not in fresh_data:
                    fresh_data[str(guild_id)] = {}
                if "channels" not in fresh_data[str(guild_id)]:
                    fresh_data[str(guild_id)]["channels"] = {}
                if channel_id not in fresh_data[str(guild_id)]["channels"]:
                    fresh_data[str(guild_id)]["channels"][channel_id] = {}
                if "users" not in fresh_data[str(guild_id)]:
                    fresh_data[str(guild_id)]["users"] = {}
                if user_id not in fresh_data[str(guild_id)]["users"]:
                    fresh_data[str(guild_id)]["users"][user_id] = {}

                # Update channel counters
                channel_data = fresh_data[str(guild_id)]["channels"][channel_id]
                msg_count = channel_data.get("messages_since_summary", 0) + 1
                channel_data["messages_since_summary"] = msg_count
                
                # Update user counters  
                user_data = fresh_data[str(guild_id)]["users"][user_id]
                profile_msg_count = user_data.get("messages_since_profile_update", 0) + 1
                user_data["messages_since_profile_update"] = profile_msg_count

                # Save counter updates first
                await self.bot.store.save_data(fresh_data)
                
                # Check triggers and schedule asynchronously to avoid blocking
                should_update_summary = False
                should_update_profile = False
                
                # Channel Summary Trigger Check
                summarize_every_messages = settings.get("summarize_every_messages", self.bot.config.DEFAULT_SUMMARIZE_EVERY_MESSAGES)
                if msg_count >= summarize_every_messages:
                    should_update_summary = True
                    logging.info(f"Will trigger channel summary update for {channel_id} due to message count ({msg_count} >= {summarize_every_messages})")
                else:
                    # Check time-based trigger only if message count not met
                    summarize_every_hours = settings.get("summarize_every_hours", self.bot.config.DEFAULT_SUMMARIZE_EVERY_HOURS)
                    last_summary_time_str = channel_data.get("last_summary_time")
                    if last_summary_time_str:
                        try:
                            last_summary_time = datetime.fromisoformat(last_summary_time_str)
                            hours_since_summary = (datetime.now(timezone.utc) - last_summary_time).total_seconds() / 3600
                            if hours_since_summary >= summarize_every_hours:
                                should_update_summary = True
                                logging.info(f"Will trigger channel summary update for {channel_id} due to time ({hours_since_summary:.1f} hours >= {summarize_every_hours} hours)")
                        except ValueError as e:
                            logging.warning(f"Invalid last_summary_time format for channel {channel_id}: {last_summary_time_str} - {e}")
                    elif msg_count >= 10:  # Create initial summary after some activity
                        should_update_summary = True
                        logging.info(f"Will create initial channel summary for {channel_id} after {msg_count} messages")

                # User Profile Trigger Check
                profile_update_messages = settings.get("profile_update_every_messages", self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES)
                if profile_msg_count >= profile_update_messages:
                    should_update_profile = True
                    logging.info(f"Will trigger profile update for user {user_id} due to message count ({profile_msg_count} >= {profile_update_messages})")
                else:
                    # Check time-based trigger
                    profile_update_hours = settings.get("profile_update_every_hours", self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_HOURS)
                    last_profile_update_str = user_data.get("last_profile_update_time")
                    if last_profile_update_str:
                        try:
                            last_profile_update_time = datetime.fromisoformat(last_profile_update_str)
                            hours_since_update = (datetime.now(timezone.utc) - last_profile_update_time).total_seconds() / 3600
                            if hours_since_update > profile_update_hours:
                                should_update_profile = True
                                logging.info(f"Will trigger profile update for user {user_id} due to time ({hours_since_update:.1f} hours >= {profile_update_hours} hours)")
                        except ValueError as e:
                            logging.warning(f"Invalid last_profile_update_time format for user {user_id}: {last_profile_update_str} - {e}")

            # Execute updates asynchronously outside the lock to prevent deadlock
            if should_update_summary:
                # Schedule summary update asynchronously
                asyncio.create_task(self.bot.context_manager.update_channel_summary(guild_id, channel_id))
                
            if should_update_profile:
                # Schedule profile update asynchronously
                asyncio.create_task(self.bot.context_manager.update_user_profile(guild_id, user_id, message.guild))

        except Exception as e:
            logging.error(f"Error updating counters and triggers: {e}")

    async def _generate_and_send_reply(self, message: discord.Message, settings: dict):
        try:
            main_model = settings.get("model", self.bot.config.MAIN_LLM_MODEL)
            
            # Start typing indicator while processing
            async with message.channel.typing():
                # Build context specifically for the main model (includes full media processing for that model)
                main_context, _ = await self.bot.context_manager.build_context(message, model_name=main_model)
                
                response = await self.bot.llm_provider.create_completion(model=main_model, messages=main_context)

                if not response or not response.choices or not response.choices[0].message.content:
                    logging.error(f"Main model failed to generate a response for message {message.id}.")
                    return

                content = response.choices[0].message.content
                
                # Resolve mentions
                content = await self._resolve_mentions(content, message.guild)

            # Handle message chunking (typing stops automatically when exiting the context)
            await MessageChunker.send_chunked_message(
                target=message.channel,
                content=content,
                reply_to=message
            )
            
        except Exception as e:
            logging.error(f"Error in _generate_and_send_reply: {e}")
            try:
                await message.reply("Sorry, I encountered an error while generating a response.")
            except Exception as fallback_error:
                logging.error(f"Failed to send error message: {fallback_error}")

    async def _resolve_mentions(self, content: str, guild: discord.Guild) -> str:
        mention_pattern = re.compile(r'<mention (user|role)="([^"]+)">')
        
        def replace_mention(match):
            m_type, name = match.groups()
            if m_type == "user":
                # Fuzzy match user - this is a simple version
                member = discord.utils.find(lambda m: name.lower() in m.display_name.lower(), guild.members)
                return member.mention if member else f"@{name}"
            elif m_type == "role":
                role = discord.utils.find(lambda r: name.lower() == r.name.lower(), guild.roles)
                if role and role.mentionable:
                    return role.mention
                return f"@{name}"
            return name

        return mention_pattern.sub(replace_mention, content)

    def _clean_json_response(self, content: str) -> str:
        """
        Clean JSON response by removing markdown code blocks and other formatting.
        
        Args:
            content: Raw response content that might contain markdown formatting
            
        Returns:
            Cleaned JSON string ready for parsing
        """
        # Remove leading/trailing whitespace
        content = content.strip()
        
        # Remove markdown code block markers
        if content.startswith('```json'):
            content = content[7:]  # Remove ```json
        elif content.startswith('```'):
            content = content[3:]   # Remove ```
            
        if content.endswith('```'):
            content = content[:-3]  # Remove closing ```
            
        # Remove any remaining leading/trailing whitespace
        content = content.strip()
        
        return content

async def setup(bot):
    await bot.add_cog(EventHandler(bot))
