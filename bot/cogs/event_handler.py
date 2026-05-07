# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/event_handler.py
import discord
from discord.ext import commands
import logging
import json
import re
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from .utilities import MessageChunker

class EventHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Lock to prevent multiple messages from triggering summaries/profiles simultaneously
        self._processing_lock = asyncio.Lock()
        self._seen_message_ids = OrderedDict()
        self._seen_message_limit = 1000
        self._summary_tasks = set()
        self._profile_tasks = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not self._claim_message(message.id):
            logging.info(f"Ignoring duplicate on_message event for message {message.id}.")
            return

        # Use lock to prevent race conditions in message processing
        async with self._processing_lock:
            # Get settings first for bypass conditions
            settings = await self.bot.context_manager.get_guild_and_channel_settings(
                str(message.guild.id),
                str(message.channel.id)
            )
            
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

    def _claim_message(self, message_id: int) -> bool:
        """Return False if this Discord message was already handled recently."""
        if message_id in self._seen_message_ids:
            return False

        self._seen_message_ids[message_id] = None
        while len(self._seen_message_ids) > self._seen_message_limit:
            self._seen_message_ids.popitem(last=False)
        return True

    async def _should_reply_or_react(self, message: discord.Message, settings: dict):
        # Bypass commands - don't trigger LLM for messages starting with "!"
        if message.content.startswith("!"):
            return False, None
        
        # Bypass conditions
        resolved_reference = message.reference.resolved if message.reference else None
        reference_author = getattr(resolved_reference, "author", None)
        is_reply_to_bot = reference_author == self.bot.user
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
        The final user message is the only live message to judge; earlier conversation history is background context only.
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
            action = str(decision_json.get("action", "none")).lower()
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
            # Use the data store's lock to prevent race conditions without
            # re-entering Store.save_data(), which also owns this lock.
            async with self.bot.store.data_lock:
                # Get fresh data to avoid conflicts
                fresh_data = await self.bot.store._read_json(self.bot.store.data_path)
                
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
                await self.bot.store._write_json(self.bot.store.data_path, fresh_data)
                
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
                self._schedule_summary_update(guild_id, channel_id)
                
            if should_update_profile:
                self._schedule_profile_update(guild_id, user_id, message.guild)

        except Exception as e:
            logging.error(f"Error updating counters and triggers: {e}")

    def _schedule_summary_update(self, guild_id: str, channel_id: str):
        key = (guild_id, channel_id)
        if key in self._summary_tasks:
            logging.info(f"Summary update already running for channel {channel_id}; skipping duplicate trigger.")
            return

        self._summary_tasks.add(key)
        task = asyncio.create_task(self.bot.context_manager.update_channel_summary(guild_id, channel_id))
        task.add_done_callback(lambda done_task, task_key=key: self._finish_background_task(done_task, self._summary_tasks, task_key, "summary"))

    def _schedule_profile_update(self, guild_id: str, user_id: str, guild: discord.Guild):
        key = (guild_id, user_id)
        if key in self._profile_tasks:
            logging.info(f"Profile update already running for user {user_id}; skipping duplicate trigger.")
            return

        self._profile_tasks.add(key)
        task = asyncio.create_task(self.bot.context_manager.update_user_profile(guild_id, user_id, guild))
        task.add_done_callback(lambda done_task, task_key=key: self._finish_background_task(done_task, self._profile_tasks, task_key, "profile"))

    def _finish_background_task(self, task: asyncio.Task, task_set: set, key: tuple, label: str):
        task_set.discard(key)
        try:
            task.result()
        except asyncio.CancelledError:
            logging.info(f"Background {label} task for {key} was cancelled.")
        except Exception as e:
            logging.error(f"Background {label} task for {key} failed: {e}")

    async def _generate_and_send_reply(self, message: discord.Message, settings: dict):
        try:
            main_model = settings.get("model", self.bot.config.MAIN_LLM_MODEL)
            
            # Start typing indicator while processing
            async with message.channel.typing():
                # Build context specifically for the main model (includes full media processing for that model)
                main_context, _ = await self.bot.context_manager.build_context(message, model_name=main_model)
                
                content = await self._generate_reply_content(main_model, main_context, message, settings)
                if not content:
                    logging.error(f"Main model failed to generate a response for message {message.id}.")
                    await self._send_generation_error(message)
                    return

                content = self._normalize_reply_content(content)
                content = self._sanitize_reply_content(content, message.guild)
                
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
                await self._send_generation_error(message, e)
            except Exception as fallback_error:
                logging.error(f"Failed to send error message: {fallback_error}")

    async def _send_generation_error(self, message: discord.Message, error: Exception = None):
        if error:
            error_text = f"{type(error).__name__}: {error}"
        else:
            error_text = self.bot.llm_provider.get_last_error_message()

        error_text = self._safe_error_text(error_text)
        await message.reply(f"Sorry, I couldn't generate a reply.\n```text\n{error_text}\n```")

    def _safe_error_text(self, error_text: str, max_length: int = 1500) -> str:
        error_text = str(error_text or "Unknown error").strip()

        sensitive_values = [
            getattr(self.bot.config, "DISCORD_TOKEN", None),
            getattr(self.bot.config, "OPENAI_API_KEY", None),
            getattr(self.bot.config, "GEMINI_API_KEY", None),
            getattr(self.bot.config, "ANTHROPIC_API_KEY", None),
        ]
        for value in sensitive_values:
            if value:
                error_text = error_text.replace(value, "[redacted]")

        error_text = error_text.replace("```", "'''")
        if len(error_text) > max_length:
            error_text = error_text[:max_length - 3].rstrip() + "..."
        return error_text

    async def _generate_reply_content(self, model: str, messages: list, origin_message: discord.Message, settings: dict):
        if not self._tools_enabled(settings):
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        tool_manager = getattr(self.bot, "tool_manager", None)
        if not tool_manager:
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        tool_messages = list(messages)
        tool_messages.append({
            "role": "system",
            "content": "Final Discord replies must be plain message text only. Do not return JSON, do not include `content` or `reactions` fields, and do not list available tools."
        })
        tools = tool_manager.tool_definitions()
        if self._has_preloaded_explicit_channel_summary(tool_messages):
            tools = self._without_tool_names(tools, {"get_channel_summary"})
            tool_messages.append({
                "role": "system",
                "content": (
                    "Stored summaries for explicitly mentioned Discord channels are already preloaded in context. "
                    "Use those summaries directly for broad channel questions instead of fetching them again."
                )
            })
        available_tool_names = self._tool_names(tools)
        max_rounds = max(0, int(getattr(self.bot.config, "TOOL_MAX_ROUNDS", 0)))
        executed_tool_results = {}
        forced_stop_reason = None

        response = await self.bot.llm_provider.create_completion(
            model=model,
            messages=tool_messages,
            tools=tools,
            tool_choice="auto"
        )
        if not response:
            logging.warning("Tool-capable completion failed; retrying without local tools.")
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        tool_round = 0
        while max_rounds == 0 or tool_round < max_rounds:
            assistant_message = self._response_message(response)
            tool_calls = tool_manager.get_tool_calls(assistant_message)
            if not tool_calls:
                return self._message_content(assistant_message)

            tool_round += 1
            logging.info(f"Model requested {len(tool_calls)} Discord tool call(s) in round {tool_round}.")
            tool_messages.append(tool_manager.assistant_message_for_history(assistant_message, tool_calls))
            repeated_tool_call = False
            unavailable_tool_call = False

            for tool_call in tool_calls:
                tool_name, arguments = tool_manager.parse_tool_call(tool_call)
                if tool_name not in available_tool_names:
                    unavailable_tool_call = True
                    result = {
                        "ok": False,
                        "tool": tool_name,
                        "error": "This tool is not available for this reply because the relevant context is already preloaded. Use the provided context and write the final Discord reply now.",
                    }
                    logging.info(f"Rejected unavailable Discord tool '{tool_name}' for message {origin_message.id}.")
                else:
                    tool_signature = self._tool_call_signature(tool_name, arguments)
                    if tool_signature in executed_tool_results:
                        repeated_tool_call = True
                        result = dict(executed_tool_results[tool_signature])
                        result["duplicate_call"] = True
                        result["instruction"] = "This exact tool call was already executed. Use the existing result and write the final Discord reply now."
                        logging.info(f"Skipped duplicate Discord tool '{tool_name}' for message {origin_message.id}.")
                    else:
                        result = await tool_manager.execute_tool_call(tool_call, origin_message)
                        executed_tool_results[tool_signature] = result
                        logging.info(f"Executed Discord tool '{tool_name}' for message {origin_message.id}.")
                tool_messages.append(tool_manager.tool_result_message(tool_call, result))

            if unavailable_tool_call:
                forced_stop_reason = "Unavailable tool call rejected because the relevant context is already preloaded. Use the existing context to write one final Discord reply now."
                break

            if repeated_tool_call:
                forced_stop_reason = "Repeated identical tool call detected. Use the tool results already provided to write one final Discord reply now."
                break

            response = await self.bot.llm_provider.create_completion(
                model=model,
                messages=tool_messages,
                tools=tools,
                tool_choice="auto"
            )
            if not response:
                logging.warning("Completion after Discord tool call failed; forcing a final response without tools.")
                forced_stop_reason = "Tool loop stopped because a completion failed. Use the tool results already provided to write one final Discord reply now."
                break

        stop_reason = forced_stop_reason or "Tool call limit reached. Use the tool results already provided to write one final Discord reply now."
        tool_messages.append({
            "role": "system",
            "content": stop_reason
        })
        response = await self.bot.llm_provider.create_completion(model=model, messages=tool_messages)
        return self._response_content(response)

    def _tools_enabled(self, settings: dict) -> bool:
        if not getattr(self.bot.config, "TOOLS_ENABLED", True):
            return False

        if settings.get("tools_enabled") is not None:
            return bool(settings.get("tools_enabled"))

        tool_settings = settings.get("tools", {})
        if isinstance(tool_settings, dict) and tool_settings.get("enabled") is not None:
            return bool(tool_settings.get("enabled"))

        return True

    def _has_preloaded_explicit_channel_summary(self, messages: list) -> bool:
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and "Preloaded Explicit Channel Summary" in content:
                return True
        return False

    def _without_tool_names(self, tools: list, names: set) -> list:
        filtered = []
        for tool in tools:
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            if function.get("name") in names:
                continue
            filtered.append(tool)
        return filtered

    def _tool_names(self, tools: list) -> set:
        names = set()
        for tool in tools:
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            name = function.get("name")
            if name:
                names.add(name)
        return names

    def _tool_call_signature(self, tool_name: str, arguments: dict) -> str:
        try:
            serialized_arguments = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            serialized_arguments = str(arguments)
        return f"{tool_name}:{serialized_arguments}"

    def _response_message(self, response):
        if not response or not getattr(response, "choices", None):
            return None
        choice = response.choices[0]
        if isinstance(choice, dict):
            return choice.get("message")
        return getattr(choice, "message", None)

    def _response_content(self, response):
        return self._message_content(self._response_message(response))

    def _message_content(self, message):
        if not message:
            return None
        if isinstance(message, dict):
            return message.get("content")
        return getattr(message, "content", None)

    def _normalize_reply_content(self, content: str) -> str:
        """Convert common model-emitted response envelopes into plain Discord text."""
        content = str(content or "").strip()
        cleaned = self._clean_json_response(content)

        jsonish_content = self._extract_jsonish_reply_field(cleaned)
        if jsonish_content:
            return jsonish_content

        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return content

        if isinstance(parsed, dict):
            for key in ("content", "message", "reply", "text"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return content

    def _extract_jsonish_reply_field(self, content: str) -> str:
        """
        Best-effort extraction for model replies that look like
        {"content":"...","reactions":...} but are not valid JSON.
        """
        if not content.lstrip().startswith("{"):
            return ""

        for key in ("content", "message", "reply", "text"):
            extracted = self._extract_jsonish_string_value(content, key)
            if extracted:
                return extracted
        return ""

    def _extract_jsonish_string_value(self, content: str, key: str) -> str:
        match = re.search(rf'["\']{re.escape(key)}["\']\s*:\s*["\']', content)
        if not match:
            return ""

        quote = content[match.end() - 1]
        value_start = match.end()
        escaped = False
        chars = []

        for char in content[value_start:]:
            if escaped:
                chars.append("\\" + char)
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == quote:
                raw_value = "".join(chars)
                try:
                    return json.loads(f'"{raw_value}"').strip()
                except json.JSONDecodeError:
                    return raw_value.replace("\\n", "\n").replace("\\t", "\t").strip()

            chars.append(char)

        raw_value = "".join(chars)
        return raw_value.replace("\\n", "\n").replace("\\t", "\t").strip()

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

    def _sanitize_reply_content(self, content: str, guild: discord.Guild = None) -> str:
        """Remove model-leaked speaker labels from the start of a Discord reply."""
        content = content.strip()
        if not self.bot.user:
            return content

        bot_id = str(self.bot.user.id)
        names = {
            getattr(self.bot.user, "name", ""),
            getattr(self.bot.user, "display_name", ""),
            str(self.bot.user),
        }
        if guild and guild.me:
            names.add(getattr(guild.me, "display_name", ""))
        names = {name for name in names if name}

        for name in names:
            escaped = re.escape(name)
            patterns = [
                rf"^\s*\*\*{escaped}\*\*\s*(?:\((?:User ID|ID):\s*{bot_id}\))?\s*[:\-]\s*",
                rf"^\s*{escaped}\s*(?:\((?:User ID|ID):\s*{bot_id}\))?\s*[:\-]\s*",
            ]
            for pattern in patterns:
                content = re.sub(pattern, "", content, count=1, flags=re.IGNORECASE).lstrip()

        content = re.sub(rf"^\s*<@!?{bot_id}>\s*[:\-]\s*", "", content, count=1).lstrip()
        content = re.sub(r"^\s*(?:assistant|bot)\s*[:\-]\s*", "", content, count=1, flags=re.IGNORECASE).lstrip()
        return content

async def setup(bot):
    await bot.add_cog(EventHandler(bot))
