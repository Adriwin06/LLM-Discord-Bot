# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/event_handler.py
import discord
from discord.ext import commands
import logging
import json
import re
from datetime import datetime, timezone

class EventHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

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
        
        # Build context specifically for the decision model (includes full media processing for that model)
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

            # Channel Summary Trigger
            channel_data = fresh_data[str(guild_id)]["channels"][channel_id]
            msg_count = channel_data.get("messages_since_summary", 0) + 1
            channel_data["messages_since_summary"] = msg_count
            
            summarize_every_messages = settings.get("summarize_every_messages", self.bot.config.DEFAULT_SUMMARIZE_EVERY_MESSAGES)
            if msg_count >= summarize_every_messages:
                logging.info(f"Triggering channel summary update for {channel_id} due to message count ({msg_count} >= {summarize_every_messages})")
                await self.bot.context_manager.update_channel_summary(guild_id, channel_id)
            else:
                # Check time-based trigger only if message count not met
                summarize_every_hours = settings.get("summarize_every_hours", self.bot.config.DEFAULT_SUMMARIZE_EVERY_HOURS)
                last_summary_time_str = channel_data.get("last_summary_time")
                if last_summary_time_str:
                    try:
                        last_summary_time = datetime.fromisoformat(last_summary_time_str)
                        hours_since_summary = (datetime.now(timezone.utc) - last_summary_time).total_seconds() / 3600
                        if hours_since_summary >= summarize_every_hours:
                            logging.info(f"Triggering channel summary update for {channel_id} due to time ({hours_since_summary:.1f} hours >= {summarize_every_hours} hours)")
                            await self.bot.context_manager.update_channel_summary(guild_id, channel_id)
                    except ValueError as e:
                        logging.warning(f"Invalid last_summary_time format for channel {channel_id}: {last_summary_time_str} - {e}")
                elif msg_count >= 10:  # Create initial summary after some activity
                    logging.info(f"Creating initial channel summary for {channel_id} after {msg_count} messages")
                    await self.bot.context_manager.update_channel_summary(guild_id, channel_id)

            # User Profile Trigger
            user_data = fresh_data[str(guild_id)]["users"][user_id]
            profile_msg_count = user_data.get("messages_since_profile_update", 0) + 1
            user_data["messages_since_profile_update"] = profile_msg_count

            profile_update_messages = settings.get("profile_update_every_messages", self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_MESSAGES)
            if profile_msg_count >= profile_update_messages:
                await self.bot.context_manager.update_user_profile(guild_id, user_id, message.guild)
            else:
                # Check time-based trigger
                profile_update_hours = settings.get("profile_update_every_hours", self.bot.config.DEFAULT_PROFILE_UPDATE_EVERY_HOURS)
                last_profile_update_str = user_data.get("last_profile_update_time")
                if last_profile_update_str:
                    last_profile_update_time = datetime.fromisoformat(last_profile_update_str)
                    if (datetime.now(timezone.utc) - last_profile_update_time).total_seconds() > profile_update_hours * 3600:
                        await self.bot.context_manager.update_user_profile(guild_id, user_id, message.guild)

            # Save updated data
            await self.bot.store.save_data(fresh_data)

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

                if not response or not response.choices:
                    logging.error(f"Main model failed to generate a response for message {message.id}.")
                    return

                content = response.choices[0].message.content
                
                # Resolve mentions
                content = await self._resolve_mentions(content, message.guild)

            # Handle message chunking (typing stops automatically when exiting the context)
            await self._send_chunked_message(message, content, settings)
            
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

    async def _send_chunked_message(self, message: discord.Message, content: str, settings: dict):
        """
        Send message content, splitting into multiple messages if needed.
        Only the first message uses reply, subsequent messages are sent normally.
        No limit on number of chunks - will send as many as needed.
        """
        try:
            max_len = 1950  # Leave room for chunk indicators
            
            # If content fits in a single message, send it as a reply
            if len(content) <= 2000:
                await message.reply(content)
                return

            logging.info(f"Splitting long message ({len(content)} chars) into chunks for message {message.id}")
            
            # Safety check for extremely long messages
            if len(content) > 500000:  # 500KB limit
                logging.warning(f"Message is extremely long ({len(content)} chars), truncating to prevent performance issues")
                content = content[:500000] + "\n\n... [Message truncated due to extreme length]"
            
            # Split content into chunks
            chunks = self._split_content_smartly(content, max_len)
            
            # Safety limit to prevent Discord rate limiting
            max_chunks = 50  # Reasonable limit to prevent spam
            if len(chunks) > max_chunks:
                logging.warning(f"Too many chunks ({len(chunks)}), limiting to {max_chunks}")
                chunks = chunks[:max_chunks]
                chunks[-1] += "\n\n... [Response truncated - too many chunks]"
            
            logging.info(f"Split into {len(chunks)} chunks")
            
            # Send first chunk as a reply
            first_chunk = chunks[0]
            if len(chunks) > 1:
                chunk_indicator = f" (1/{len(chunks)})"
                # Ensure the indicator fits within Discord's limit
                if len(first_chunk) + len(chunk_indicator) <= 2000:
                    first_chunk += chunk_indicator
            
            await message.reply(first_chunk)
            logging.info("Sent first chunk as reply")
            
            # Send remaining chunks as normal messages (not replies)
            for i, chunk in enumerate(chunks[1:], 2):
                chunk_indicator = f" ({i}/{len(chunks)})"
                # Ensure the indicator fits within Discord's limit
                if len(chunk) + len(chunk_indicator) <= 2000:
                    chunk += chunk_indicator
                
                await message.channel.send(chunk)
                logging.info(f"Sent chunk {i}/{len(chunks)}")
                
        except Exception as e:
            logging.error(f"Error in _send_chunked_message: {e}")
            # Fallback: send original content as a single reply
            try:
                await message.reply(content[:2000])
                if len(content) > 2000:
                    await message.channel.send("... [Response was too long and chunking failed]")
            except Exception as fallback_error:
                logging.error(f"Fallback also failed: {fallback_error}")

    def _split_content_smartly(self, content: str, max_len: int) -> list:
        """
        Split content into chunks at natural break points.
        Simple, guaranteed-to-terminate algorithm.
        """
        if len(content) <= max_len:
            return [content]
        
        chunks = []
        remaining = content
        
        while remaining:
            if len(remaining) <= max_len:
                # Last chunk
                chunks.append(remaining)
                break
            
            # Take a slice of max_len
            chunk = remaining[:max_len]
            
            # Find the best place to split within this chunk
            split_pos = max_len
            
            # Try to split at paragraph break
            last_paragraph = chunk.rfind('\n\n')
            if last_paragraph > max_len // 2:  # Don't split too early
                split_pos = last_paragraph + 2
            else:
                # Try to split at sentence end
                sentence_ends = [chunk.rfind('. '), chunk.rfind('! '), chunk.rfind('? ')]
                last_sentence = max(sentence_ends)
                if last_sentence > max_len // 2:
                    split_pos = last_sentence + 2
                else:
                    # Try to split at word boundary
                    last_space = chunk.rfind(' ')
                    if last_space > max_len * 0.7:  # Don't split too early
                        split_pos = last_space + 1
                    # Otherwise use max_len (arbitrary split)
            
            # Extract the chunk and update remaining content
            final_chunk = remaining[:split_pos].rstrip()
            if final_chunk:  # Only add non-empty chunks
                chunks.append(final_chunk)
            
            # Move to next section
            remaining = remaining[split_pos:].lstrip()
        
        return chunks

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
