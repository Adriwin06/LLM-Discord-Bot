# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/cogs/event_handler.py
import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
import re
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from .utilities import MessageChunker

class EventHandler(commands.Cog):
    GIF_LIBRARY = {
        "typing": {
            "url": "https://media.giphy.com/media/13GIgrGdslD9oQ/giphy.gif",
            "description": "someone intensely typing at a computer",
        },
        "waiting": {
            "url": "https://media.giphy.com/media/l0HlBO7eyXzSZkJri/giphy.gif",
            "description": "waiting patiently",
        },
        "popcorn": {
            "url": "https://media.giphy.com/media/tyqcJoNjNv0Fq/giphy.gif",
            "description": "watching drama with popcorn",
        },
        "laugh": {
            "url": "https://media.giphy.com/media/10JhviFuU2gWD6/giphy.gif",
            "description": "laughing hard",
        },
        "thumbs_up": {
            "url": "https://media.giphy.com/media/111ebonMs90YLu/giphy.gif",
            "description": "quick approval",
        },
        "mind_blown": {
            "url": "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif",
            "description": "mind blown",
        },
    }

    def __init__(self, bot):
        self.bot = bot
        # Lock to prevent multiple messages from triggering summaries/profiles simultaneously
        self._processing_lock = asyncio.Lock()
        self._seen_message_ids = OrderedDict()
        self._seen_message_limit = 1000
        self._summary_tasks = set()
        self._profile_tasks = set()
        self._pending_reply_tasks = {}
        self._pending_reply_chains = {}
        self._typing_state = {}
        self._last_reply_by_channel = {}
        self._recent_human_activity_by_channel = {}
        self._last_bot_involvement_by_channel = {}

    def cog_unload(self):
        for task in self._pending_reply_tasks.values():
            task.cancel()

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        if getattr(user, "bot", False):
            return

        guild = getattr(channel, "guild", None)
        if not guild:
            return

        key = (str(guild.id), str(channel.id), str(user.id))
        now = self._utc_now()
        existing = self._typing_state.get(key)
        first_typing_at = existing.get("first_typing_at") if existing and self._is_typing_state_active(existing) else now
        self._typing_state[key] = {
            "first_typing_at": first_typing_at,
            "last_typing_at": now,
        }
        self._prune_typing_state()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if not self._claim_message(message.id):
            logging.info(f"Ignoring duplicate on_message event for message {message.id}.")
            return

        logging.info(
            "Received Discord message. message_id=%s guild_id=%s channel_id=%s author_id=%s text_chars=%s attachments=%s embeds=%s mentions_bot=%s",
            message.id,
            message.guild.id,
            message.channel.id,
            message.author.id,
            len(message.content or ""),
            len(message.attachments or []),
            len(message.embeds or []),
            self.bot.user in message.mentions if self.bot.user else False,
        )

        # Use lock to prevent race conditions in message processing
        async with self._processing_lock:
            # Get settings first for bypass conditions
            settings = await self.bot.context_manager.get_guild_and_channel_settings(
                str(message.guild.id),
                str(message.channel.id)
            )
            logging.debug(
                "Loaded message settings. message_id=%s model=%s decision_model=%s tools_enabled=%s",
                message.id,
                settings.get("model", self.bot.config.MAIN_LLM_MODEL),
                settings.get("decision_llm_model", self.bot.config.DECISION_LLM_MODEL),
                settings.get("tools_enabled", getattr(self.bot.config, "TOOLS_ENABLED", True)),
            )
            
            # Update counters and trigger summaries/profiles
            await self._update_counters_and_triggers(message, settings)

        self._track_human_channel_activity(message)

        if self.bot.context_manager.is_llm_blacklisted_settings(settings, message.channel.id):
            logging.info(
                "Skipping LLM reply decision in blacklisted channel. message_id=%s channel_id=%s",
                message.id,
                message.channel.id,
            )
            return

        await self._queue_reply_decision(message, settings)

    def _claim_message(self, message_id: int) -> bool:
        """Return False if this Discord message was already handled recently."""
        if message_id in self._seen_message_ids:
            return False

        self._seen_message_ids[message_id] = None
        while len(self._seen_message_ids) > self._seen_message_limit:
            self._seen_message_ids.popitem(last=False)
        return True

    @app_commands.command(name="retry", description="Delete and regenerate the bot's last generated reply in this channel.")
    async def retry(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("Retry can only be used in a server text channel or thread.", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        settings = await self.bot.context_manager.get_guild_and_channel_settings(guild_id, channel_id)
        if self.bot.context_manager.is_llm_blacklisted_settings(settings, channel_id):
            await interaction.followup.send("LLM output is blacklisted in this channel.", ephemeral=True)
            return

        record_key = (guild_id, channel_id)
        record = self._last_reply_by_channel.get(record_key)
        if not record:
            await interaction.followup.send("I do not have a generated reply to retry in this channel yet.", ephemeral=True)
            return

        if not self._can_retry_record(interaction, record):
            await interaction.followup.send("Only the original author or someone with Manage Messages can retry that reply.", ephemeral=True)
            return

        try:
            origin_message = await interaction.channel.fetch_message(int(record["origin_message_id"]))
        except discord.NotFound:
            self._last_reply_by_channel.pop(record_key, None)
            await interaction.followup.send("The original message is gone, so I cannot retry that reply.", ephemeral=True)
            return
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(f"I could not fetch the original message: {e}", ephemeral=True)
            return

        chain_messages = await self._fetch_retry_chain_messages(interaction.channel, record)
        if not chain_messages:
            chain_messages = [origin_message]

        try:
            deleted_count = await self._delete_recorded_reply_messages(interaction.channel, record)
        except discord.Forbidden:
            await interaction.followup.send("I do not have permission to delete my previous reply.", ephemeral=True)
            return

        self._last_reply_by_channel.pop(record_key, None)
        logging.info(
            "Retrying generated reply. origin_message_id=%s chain_count=%s deleted_chunks=%s requested_by=%s",
            origin_message.id,
            len(chain_messages),
            deleted_count,
            interaction.user.id,
        )

        regenerated = await self._generate_and_send_reply(origin_message, settings, chain_messages=chain_messages)
        if regenerated:
            await interaction.followup.send("Regenerated the last reply.", ephemeral=True)
        else:
            await interaction.followup.send("I deleted the previous reply, but regeneration did not complete.", ephemeral=True)

    async def _queue_reply_decision(self, message: discord.Message, settings: dict):
        if self.bot.context_manager.is_llm_blacklisted_settings(settings, message.channel.id):
            logging.info("Skipping reply decision for blacklisted channel. message_id=%s", message.id)
            return

        if message.content.startswith("!"):
            logging.info("Skipping reply decision for command message. message_id=%s", message.id)
            return

        key = self._reply_chain_key(message)
        force_reply = self._is_direct_interaction(message, settings)
        chain = self._pending_reply_chains.get(key)
        pending_forced_chain = bool(chain and chain.get("force_reply"))

        if force_reply:
            self._mark_bot_involved(message)

        if not self._decision_llm_enabled(settings) and not (force_reply or pending_forced_chain):
            logging.info(
                "Skipping decision model because it is disabled and the message is not a direct interaction. message_id=%s",
                message.id,
            )
            return

        if self._should_skip_uninvolved_conversation(
            message,
            settings,
            force_reply=force_reply,
            pending_forced_chain=pending_forced_chain,
        ):
            logging.info(
                "Skipping decision model for active conversation the bot is not part of. message_id=%s channel_id=%s",
                message.id,
                message.channel.id,
            )
            return

        debounce_seconds = self._reply_chain_debounce_seconds(settings)
        logging.info(
            "Queueing reply decision. message_id=%s debounce_seconds=%.2f",
            message.id,
            debounce_seconds,
        )
        if debounce_seconds <= 0:
            await self._process_reply_decision(message, settings, chain_messages=[message])
            return

        if chain:
            chain["messages"].append(message)
            chain["latest_message"] = message
            chain["settings"] = settings
            chain["force_reply"] = bool(chain.get("force_reply")) or force_reply
        else:
            self._pending_reply_chains[key] = {
                "started_at": self._utc_now(),
                "messages": [message],
                "latest_message": message,
                "settings": settings,
                "force_reply": force_reply,
                "long_typing": False,
            }
        logging.debug(
            "Reply decision chain updated. key=%s message_count=%s latest_message_id=%s force_reply=%s",
            key,
            len(self._pending_reply_chains[key]["messages"]),
            self._pending_reply_chains[key]["latest_message"].id,
            self._pending_reply_chains[key]["force_reply"],
        )

        existing_task = self._pending_reply_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = asyncio.create_task(self._run_debounced_reply_decision(key, message.id, debounce_seconds))
        self._pending_reply_tasks[key] = task
        logging.debug("Reply decision debounce task scheduled. key=%s latest_message_id=%s", key, message.id)

    async def _run_debounced_reply_decision(self, key: tuple, latest_message_id: int, debounce_seconds: float):
        try:
            await asyncio.sleep(debounce_seconds)
        except asyncio.CancelledError:
            return

        chain = self._pending_reply_chains.get(key)
        if not chain or getattr(chain.get("latest_message"), "id", None) != latest_message_id:
            return

        try:
            await self._wait_for_typing_to_pause(key, chain, debounce_seconds)
        except asyncio.CancelledError:
            return

        chain = self._pending_reply_chains.get(key)
        if not chain or getattr(chain.get("latest_message"), "id", None) != latest_message_id:
            return

        current_task = asyncio.current_task()
        if self._pending_reply_tasks.get(key) is current_task:
            self._pending_reply_tasks.pop(key, None)
        self._pending_reply_chains.pop(key, None)

        latest_message = chain["latest_message"]
        try:
            logging.info(
                "Running debounced reply decision. latest_message_id=%s chain_count=%s long_typing=%s",
                latest_message.id,
                len(chain.get("messages") or []),
                bool(chain.get("long_typing")),
            )
            await self._process_reply_decision(
                latest_message,
                chain["settings"],
                force_reply=bool(chain.get("force_reply")),
                chain_messages=chain.get("messages") or [latest_message],
                long_typing=bool(chain.get("long_typing")),
            )
        except Exception as e:
            logging.error(f"Debounced reply decision failed for message {latest_message.id}: {e}")

    async def _process_reply_decision(
        self,
        message: discord.Message,
        settings: dict,
        *,
        force_reply: bool = False,
        chain_messages: list = None,
        long_typing: bool = False,
    ):
        chain_messages = chain_messages or [message]
        settings = await self.bot.context_manager.get_guild_and_channel_settings(message.guild.id, message.channel.id)
        if self.bot.context_manager.is_llm_blacklisted_settings(settings, message.channel.id):
            logging.info("Skipping LLM action in blacklisted channel. message_id=%s", message.id)
            return

        if self._should_skip_uninvolved_conversation(message, settings, force_reply=force_reply):
            logging.info(
                "Skipping queued decision for active conversation the bot is not part of. message_id=%s channel_id=%s",
                message.id,
                message.channel.id,
            )
            return

        logging.info(
            "Processing reply decision. message_id=%s force_reply=%s chain_count=%s long_typing=%s",
            message.id,
            force_reply,
            len(chain_messages),
            long_typing,
        )
        decision = await self._decide_message_action(
            message,
            settings,
            force_reply=force_reply,
            chain_messages=chain_messages,
            long_typing=long_typing,
        )

        if self._has_pending_newer_chain(message):
            logging.info(f"Skipping reply/reaction for message {message.id}; a newer same-user message chain is pending.")
            return

        action = str(decision.get("action", "none")).lower()
        logging.info("Reply decision result. message_id=%s action=%s decision=%s", message.id, action, decision)
        if action == "reply":
            await self._generate_and_send_reply(
                message,
                settings,
                chain_messages=chain_messages,
                long_typing=long_typing,
            )
        elif action == "react" and decision.get("reaction"):
            reaction = self._normalize_reaction_emoji(decision["reaction"], message.guild)
            if not reaction:
                logging.warning("Decision model returned an unusable reaction for message %s: %r", message.id, decision.get("reaction"))
                return
            try:
                await message.add_reaction(reaction)
                self._mark_bot_involved(message)
            except discord.HTTPException:
                logging.warning(
                    "Failed to add reaction %r (normalized from %r). It might be invalid or unavailable.",
                    reaction,
                    decision.get("reaction"),
                )
        elif action == "gif":
            await self._send_gif_decision(message, settings, decision)

    def _reply_chain_key(self, message: discord.Message) -> tuple:
        return (str(message.guild.id), str(message.channel.id), str(message.author.id))

    def _channel_key(self, message: discord.Message) -> tuple:
        return (str(message.guild.id), str(message.channel.id))

    def _decision_llm_enabled(self, settings: dict) -> bool:
        value = settings.get(
            "decision_llm_enabled",
            getattr(self.bot.config, "DECISION_LLM_ENABLED", True),
        )
        return self._coerce_bool(value, default=True)

    def _track_human_channel_activity(self, message: discord.Message):
        key = self._channel_key(message)
        now = self._utc_now()
        activity = self._recent_human_activity_by_channel.setdefault(key, [])
        activity.append({
            "message_id": int(message.id),
            "author_id": str(message.author.id),
            "created_at": now,
        })

        max_age = max(self._uninvolved_conversation_window_seconds(), self._bot_conversation_idle_seconds())
        self._recent_human_activity_by_channel[key] = [
            entry
            for entry in activity[-50:]
            if (now - entry["created_at"]).total_seconds() <= max_age
        ]

    def _mark_bot_involved(self, message: discord.Message):
        self._last_bot_involvement_by_channel[self._channel_key(message)] = self._utc_now()

    def _bot_recently_involved(self, message: discord.Message) -> bool:
        last_involved_at = self._last_bot_involvement_by_channel.get(self._channel_key(message))
        if not isinstance(last_involved_at, datetime):
            return False
        return (self._utc_now() - last_involved_at).total_seconds() <= self._bot_conversation_idle_seconds()

    def _should_skip_uninvolved_conversation(
        self,
        message: discord.Message,
        settings: dict,
        *,
        force_reply: bool = False,
        pending_forced_chain: bool = False,
    ) -> bool:
        if not self._decision_llm_enabled(settings):
            return False
        if force_reply or pending_forced_chain:
            return False

        resolved_reference = message.reference.resolved if message.reference else None
        reference_author = getattr(resolved_reference, "author", None)
        if reference_author and reference_author != self.bot.user and not getattr(reference_author, "bot", False):
            return True

        if self._bot_recently_involved(message):
            return False

        now = self._utc_now()
        window_seconds = self._uninvolved_conversation_window_seconds()
        recent_activity = [
            entry
            for entry in self._recent_human_activity_by_channel.get(self._channel_key(message), [])
            if (now - entry["created_at"]).total_seconds() <= window_seconds
        ]
        authors = {entry["author_id"] for entry in recent_activity}
        return len(recent_activity) >= 4 and len(authors) >= 2

    def _uninvolved_conversation_window_seconds(self) -> float:
        return 45.0

    def _bot_conversation_idle_seconds(self) -> float:
        return 120.0

    def _reply_chain_debounce_seconds(self, settings: dict) -> float:
        enabled = settings.get(
            "reply_chain_debounce_enabled",
            getattr(self.bot.config, "REPLY_CHAIN_DEBOUNCE_ENABLED", True),
        )
        if not self._coerce_bool(enabled, default=True):
            return 0.0

        value = settings.get(
            "reply_chain_debounce_seconds",
            getattr(self.bot.config, "REPLY_CHAIN_DEBOUNCE_SECONDS", 2.0),
        )
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 2.0

    async def _wait_for_typing_to_pause(self, key: tuple, chain: dict, debounce_seconds: float):
        if not self._typing_wait_enabled(chain["settings"]):
            return

        poll_seconds = max(0.5, min(1.5, debounce_seconds))
        while self._is_user_typing(key):
            elapsed = self._chain_elapsed_seconds(chain)
            if elapsed >= self._long_typing_seconds(chain["settings"]):
                chain["long_typing"] = True
                logging.info(f"Processing message chain for {key}; user has been typing for {elapsed:.1f}s.")
                return

            if elapsed >= self._typing_max_wait_seconds(chain["settings"]):
                logging.info(f"Processing message chain for {key}; typing wait reached {elapsed:.1f}s.")
                return

            await asyncio.sleep(poll_seconds)

    def _typing_wait_enabled(self, settings: dict) -> bool:
        value = settings.get(
            "reply_chain_wait_for_typing",
            getattr(self.bot.config, "REPLY_CHAIN_WAIT_FOR_TYPING", True),
        )
        return self._coerce_bool(value, default=True)

    def _typing_active_seconds(self) -> float:
        try:
            return max(1.0, float(getattr(self.bot.config, "TYPING_ACTIVE_SECONDS", 8.0)))
        except (TypeError, ValueError):
            return 8.0

    def _typing_max_wait_seconds(self, settings: dict) -> float:
        value = settings.get(
            "reply_chain_typing_max_wait_seconds",
            getattr(self.bot.config, "REPLY_CHAIN_TYPING_MAX_WAIT_SECONDS", 12.0),
        )
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 12.0

    def _long_typing_seconds(self, settings: dict) -> float:
        value = settings.get(
            "reply_chain_long_typing_seconds",
            getattr(self.bot.config, "REPLY_CHAIN_LONG_TYPING_SECONDS", 10.0),
        )
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 10.0

    def _chain_elapsed_seconds(self, chain: dict) -> float:
        started_at = chain.get("started_at")
        if not isinstance(started_at, datetime):
            return 0.0
        return max(0.0, (self._utc_now() - started_at).total_seconds())

    def _is_user_typing(self, key: tuple) -> bool:
        state = self._typing_state.get(key)
        if not state:
            return False
        if self._is_typing_state_active(state):
            return True
        self._typing_state.pop(key, None)
        return False

    def _is_typing_state_active(self, state: dict) -> bool:
        last_typing_at = state.get("last_typing_at")
        if not isinstance(last_typing_at, datetime):
            return False
        return (self._utc_now() - last_typing_at).total_seconds() <= self._typing_active_seconds()

    def _prune_typing_state(self):
        stale_keys = [
            key
            for key, state in self._typing_state.items()
            if not self._is_typing_state_active(state)
        ]
        for key in stale_keys:
            self._typing_state.pop(key, None)

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _coerce_bool(self, value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        if value is None:
            return default
        return bool(value)

    def _is_direct_interaction(self, message: discord.Message, settings: dict) -> bool:
        resolved_reference = message.reference.resolved if message.reference else None
        reference_author = getattr(resolved_reference, "author", None)
        is_reply_to_bot = reference_author == self.bot.user
        mentions_bot = self.bot.user in message.mentions
        bypass_on_reply = self._coerce_bool(settings.get("bypass_on_reply"), default=True)
        bypass_on_ping = self._coerce_bool(settings.get("bypass_on_ping"), default=True)
        return (
            bypass_on_reply and is_reply_to_bot
        ) or (
            bypass_on_ping and mentions_bot
        )

    def _has_pending_newer_chain(self, message: discord.Message) -> bool:
        chain = self._pending_reply_chains.get(self._reply_chain_key(message))
        latest_message = chain.get("latest_message") if chain else None
        return bool(latest_message and latest_message.id != message.id)

    def _can_retry_record(self, interaction: discord.Interaction, record: dict) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions and (getattr(permissions, "manage_messages", False) or getattr(permissions, "administrator", False)):
            return True
        return str(interaction.user.id) == str(record.get("origin_author_id"))

    async def _fetch_retry_chain_messages(self, channel, record: dict) -> list:
        messages = []
        for message_id in record.get("chain_message_ids") or []:
            try:
                messages.append(await channel.fetch_message(int(message_id)))
            except (discord.NotFound, discord.Forbidden):
                continue
            except discord.HTTPException as e:
                logging.warning("Could not fetch retry chain message %s: %s", message_id, e)
        return messages

    async def _delete_recorded_reply_messages(self, channel, record: dict) -> int:
        deleted_count = 0
        bot_id = getattr(self.bot.user, "id", None)
        for message_id in reversed(record.get("bot_message_ids") or []):
            try:
                message = await channel.fetch_message(int(message_id))
                if bot_id and getattr(message.author, "id", None) != bot_id:
                    logging.warning("Refusing to delete retry message %s because it was not authored by this bot.", message_id)
                    continue
                await message.delete()
                deleted_count += 1
            except discord.NotFound:
                continue
            except discord.Forbidden:
                raise
            except discord.HTTPException as e:
                logging.warning("Could not delete retry message %s: %s", message_id, e)
        return deleted_count

    def _record_generated_reply(self, origin_message: discord.Message, chain_messages: list, sent_messages: list, content: str):
        if not sent_messages:
            return

        bot_id = getattr(self.bot.user, "id", None)
        bot_message_ids = [
            int(sent_message.id)
            for sent_message in sent_messages
            if sent_message and (not bot_id or getattr(sent_message.author, "id", None) == bot_id)
        ]
        if not bot_message_ids:
            return

        chain_messages = chain_messages or [origin_message]
        record_key = (str(origin_message.guild.id), str(origin_message.channel.id))
        self._last_reply_by_channel[record_key] = {
            "origin_message_id": int(origin_message.id),
            "origin_author_id": int(origin_message.author.id),
            "chain_message_ids": [int(message.id) for message in chain_messages if getattr(message, "id", None)],
            "bot_message_ids": bot_message_ids,
            "created_at": self._utc_now().isoformat(),
            "content_chars": len(content or ""),
        }
        self._mark_bot_involved(origin_message)

    async def _decide_message_action(
        self,
        message: discord.Message,
        settings: dict,
        *,
        force_reply: bool = False,
        chain_messages: list = None,
        long_typing: bool = False,
    ):
        # Bypass commands - don't trigger LLM for messages starting with "!"
        if message.content.startswith("!"):
            logging.info("Decision model skipped for command message. message_id=%s", message.id)
            return {"action": "none"}

        # Bypass conditions
        if force_reply or self._is_direct_interaction(message, settings):
            logging.info(f"Bypassing decision model for message {message.id} due to direct interaction.")
            return {"action": "reply"}

        if not self._decision_llm_enabled(settings):
            logging.info("Decision model disabled for non-direct message. message_id=%s", message.id)
            return {"action": "none"}

        # Use decision LLM with context built specifically for that model
        decision_model = settings.get("decision_llm_model", self.bot.config.DECISION_LLM_MODEL)
        logging.info(
            "Decision model context build starting. message_id=%s decision_model=%s main_model=%s",
            message.id,
            decision_model,
            self.bot.config.MAIN_LLM_MODEL,
        )
        
        # Only build separate context if decision model is different from main model
        if decision_model == self.bot.config.MAIN_LLM_MODEL:
            # Reuse the existing context from build_context call
            decision_context, _ = await self.bot.context_manager.build_context(message)
        else:
            # Build context specifically for the decision model
            decision_context, _ = await self.bot.context_manager.build_context(message, model_name=decision_model)
        decision_context = self._with_message_chain_context(decision_context, chain_messages, long_typing=long_typing)
        
        decision_prompt = """
        You are a decision-making model for a Discord bot.
        Based on the provided context, decide if the bot should reply, react with an emoji, send a GIF, or do nothing.
        The final user message is the only live message to judge; earlier conversation history is background context only.
        If a current same-user message chain note is present, treat that chain as the live message to judge.
        The bot should reply if it's directly addressed, asked a question, or can provide a meaningful contribution.
        The bot should react if the message is emotional, a simple acknowledgement is needed, or contains engaging media.
        The bot may send a GIF only when it is clearly funny, logical, and low-risk for the current context.
        Do not send GIFs for serious, sensitive, sad, medical, legal, financial, or moderation-related contexts.
        If a long-typing note is present, you may choose a light joke or a typing/waiting GIF if it fits.
        Otherwise, the bot should do nothing.

        Available GIF keys:
        typing: someone intensely typing at a computer
        waiting: waiting patiently
        popcorn: watching drama with popcorn
        laugh: laughing hard
        thumbs_up: quick approval
        mind_blown: mind blown
        
        Respond with a single JSON object with four keys:
        1. "action": a string, either "reply", "react", "gif", or "none".
        2. "reaction": a string containing a single emoji if the action is "react", otherwise null.
           If an Available Server Emojis block is present, you may use one of those custom emojis.
           For custom emoji reactions, return either its message_format (<:name:id> or <a:name:id>) or reaction_format (name:id).
        3. "gif_key": one available GIF key if the action is "gif", otherwise null.
        4. "caption": a short caption if the action is "gif" and text would improve it, otherwise null.
        
        Example: {"action": "reply", "reaction": null, "gif_key": null, "caption": null}
        Example: {"action": "react", "reaction": "👍", "gif_key": null, "caption": null}
        Example: {"action": "gif", "reaction": null, "gif_key": "typing", "caption": "bro is writing chapter 4"}
        Example: {"action": "none", "reaction": null, "gif_key": null, "caption": null}
        """
        
        decision_context[0]["content"] = decision_prompt

        response = await self.bot.llm_provider.create_completion(
            model=decision_model,
            messages=decision_context,
            response_format={"type": "json_object"}
        )
        logging.info("Decision model response received. message_id=%s has_response=%s", message.id, bool(response and response.choices))

        if not response or not response.choices:
            # Fallback to main model if decision model fails and they are different
            if decision_model != self.bot.config.MAIN_LLM_MODEL:
                # Build context for main model and retry decision
                logging.warning(
                    "Decision model failed; retrying decision with main model. message_id=%s decision_model=%s main_model=%s",
                    message.id,
                    decision_model,
                    self.bot.config.MAIN_LLM_MODEL,
                )
                main_context, _ = await self.bot.context_manager.build_context(message, model_name=self.bot.config.MAIN_LLM_MODEL)
                main_context = self._with_message_chain_context(main_context, chain_messages, long_typing=long_typing)
                main_context[0]["content"] = decision_prompt
                
                response = await self.bot.llm_provider.create_completion(
                    model=self.bot.config.MAIN_LLM_MODEL,
                    messages=main_context,
                    response_format={"type": "json_object"}
                )
            if not response or not response.choices:
                logging.error(f"Both decision and main models failed to make a decision for message {message.id}.")
                return {"action": "none"}

        # Parse decision response
        try:
            # Clean the response content by removing markdown code blocks if present
            raw_content = response.choices[0].message.content.strip()
            cleaned_content = self._clean_json_response(raw_content)
            logging.debug(
                "Decision model raw output. message_id=%s raw_chars=%s cleaned_chars=%s cleaned=%s",
                message.id,
                len(raw_content),
                len(cleaned_content),
                cleaned_content[:500],
            )
            
            decision_json = json.loads(cleaned_content)
            action = str(decision_json.get("action", "none")).lower()
            reaction = decision_json.get("reaction")

            if action == "reply":
                return {"action": "reply"}
            if action == "react" and reaction:
                return {"action": "react", "reaction": reaction}
            if action == "gif":
                return self._normalize_gif_decision(decision_json, settings)
            return {"action": "none"}
        except (json.JSONDecodeError, KeyError):
            logging.exception(f"Failed to parse decision JSON: {response.choices[0].message.content}")
            return {"action": "none"}

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

    async def _generate_and_send_reply(
        self,
        message: discord.Message,
        settings: dict,
        chain_messages: list = None,
        long_typing: bool = False,
    ):
        try:
            settings = await self.bot.context_manager.get_guild_and_channel_settings(message.guild.id, message.channel.id)
            if self.bot.context_manager.is_llm_blacklisted_settings(settings, message.channel.id):
                logging.info("Skipping generated reply in blacklisted channel. message_id=%s", message.id)
                return False

            main_model = settings.get("model", self.bot.config.MAIN_LLM_MODEL)
            logging.info(
                "Generating reply. message_id=%s model=%s chain_count=%s long_typing=%s",
                message.id,
                main_model,
                len(chain_messages or []),
                long_typing,
            )
            
            # Start typing indicator while processing
            async with message.channel.typing():
                # Build context specifically for the main model (includes full media processing for that model)
                main_context, _ = await self.bot.context_manager.build_context(message, model_name=main_model)
                main_context = self._with_message_chain_context(main_context, chain_messages, long_typing=long_typing)
                logging.info(
                    "Main reply context ready. message_id=%s model=%s messages=%s",
                    message.id,
                    main_model,
                    len(main_context),
                )
                
                content = await self._generate_reply_content(main_model, main_context, message, settings)
                if not content:
                    logging.error(f"Main model failed to generate a response for message {message.id}.")
                    await self._send_generation_error(message)
                    return False

                content = self._normalize_reply_content(content)
                content = self._sanitize_reply_content(content, message.guild)
                logging.info(
                    "Reply content generated. message_id=%s chars=%s",
                    message.id,
                    len(content or ""),
                )
                
                # Resolve mentions
                content = await self._resolve_mentions(content, message.guild)

            if self._has_pending_newer_chain(message):
                logging.info(f"Skipping generated reply for message {message.id}; a newer same-user message chain is pending.")
                return False

            safe_mentions = discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False)

            # Handle message chunking (typing stops automatically when exiting the context)
            sent_messages = await MessageChunker.send_chunked_message(
                target=message.channel,
                content=content,
                reply_to=message,
                allowed_mentions=safe_mentions,
            )
            self._record_generated_reply(message, chain_messages, sent_messages, content)
            logging.info("Reply sent. message_id=%s chars=%s", message.id, len(content or ""))
            return True
            
        except Exception as e:
            logging.exception(f"Error in _generate_and_send_reply: {e}")
            try:
                await self._send_generation_error(message, e)
            except Exception as fallback_error:
                logging.error(f"Failed to send error message: {fallback_error}")
            return False

    async def _send_generation_error(self, message: discord.Message, error: Exception = None):
        if error:
            error_text = f"{type(error).__name__}: {error}"
        else:
            error_text = self.bot.llm_provider.get_last_error_message()

        error_text = self._safe_error_text(error_text)
        await message.reply(
            f"Sorry, I couldn't generate a reply.\n```text\n{error_text}\n```",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False),
        )

    async def _send_gif_decision(self, message: discord.Message, settings: dict, decision: dict):
        gif_key = decision.get("gif_key")
        gif = self.GIF_LIBRARY.get(gif_key)
        if not gif:
            logging.warning(f"Decision model selected unknown GIF key '{gif_key}' for message {message.id}.")
            return

        caption = self._sanitize_gif_caption(decision.get("caption"))
        content = gif["url"]
        if caption:
            content = f"{caption}\n{content}"

        try:
            await message.reply(
                content,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False, replied_user=False),
            )
            self._mark_bot_involved(message)
        except discord.HTTPException as e:
            logging.warning(f"Failed to send GIF '{gif_key}' for message {message.id}: {e}")

    def _normalize_gif_decision(self, decision_json: dict, settings: dict) -> dict:
        if not self._gifs_enabled(settings):
            return {"action": "none"}

        gif_key = str(decision_json.get("gif_key") or "").strip().lower()
        if gif_key not in self.GIF_LIBRARY:
            return {"action": "none"}

        return {
            "action": "gif",
            "gif_key": gif_key,
            "caption": self._sanitize_gif_caption(decision_json.get("caption")),
        }

    def _normalize_reaction_emoji(self, reaction, guild: discord.Guild = None):
        reaction_text = str(reaction or "").strip()
        if not reaction_text:
            return None

        custom_match = re.fullmatch(r"<a?:([A-Za-z0-9_]+):(\d+)>", reaction_text)
        if custom_match:
            name, emoji_id = custom_match.groups()
            emoji = self._guild_emoji_by_id(guild, emoji_id)
            return emoji or f"{name}:{emoji_id}"

        reaction_format_match = re.fullmatch(r"([A-Za-z0-9_]+):(\d+)", reaction_text)
        if reaction_format_match:
            name, emoji_id = reaction_format_match.groups()
            emoji = self._guild_emoji_by_id(guild, emoji_id)
            return emoji or f"{name}:{emoji_id}"

        name_match = re.fullmatch(r":([A-Za-z0-9_]+):", reaction_text)
        if name_match and guild:
            emoji = discord.utils.get(getattr(guild, "emojis", []) or [], name=name_match.group(1))
            if emoji:
                return emoji

        return reaction_text

    def _guild_emoji_by_id(self, guild: discord.Guild, emoji_id):
        if not guild:
            return None

        try:
            emoji_id = int(emoji_id)
        except (TypeError, ValueError):
            return None

        if hasattr(guild, "get_emoji"):
            return guild.get_emoji(emoji_id)
        return discord.utils.get(getattr(guild, "emojis", []) or [], id=emoji_id)

    def _gifs_enabled(self, settings: dict) -> bool:
        value = settings.get(
            "gifs_enabled",
            getattr(self.bot.config, "GIFS_ENABLED", True),
        )
        return self._coerce_bool(value, default=True)

    def _sanitize_gif_caption(self, caption: str, max_length: int = 180) -> str:
        caption = str(caption or "").strip()
        if not caption:
            return ""

        caption = caption.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
        caption = caption.replace("```", "'''")
        caption = re.sub(r"\s+", " ", caption)
        if len(caption) > max_length:
            caption = caption[: max_length - 3].rstrip() + "..."
        return caption

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
            logging.info("Generating reply without tools; tools disabled. message_id=%s model=%s", origin_message.id, model)
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        if self._messages_have_image_content(messages):
            logging.info(f"Skipping Discord tools for message {origin_message.id}; visual content is already attached to the model request.")
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        tool_manager = getattr(self.bot, "tool_manager", None)
        if not tool_manager:
            logging.info("Generating reply without tools; tool manager missing. message_id=%s model=%s", origin_message.id, model)
            response = await self.bot.llm_provider.create_completion(model=model, messages=messages)
            return self._response_content(response)

        tool_messages = list(messages)
        tool_messages.append({
            "role": "system",
            "content": "Final Discord replies must be plain message text only. Do not return JSON, do not include `content` or `reactions` fields, and do not list available tools."
        })
        tools = tool_manager.tool_definitions()
        logging.info(
            "Generating reply with local Discord tools available. message_id=%s model=%s tools=%s max_rounds=%s",
            origin_message.id,
            model,
            len(tools),
            getattr(self.bot.config, "TOOL_MAX_ROUNDS", 0),
        )
        preloaded_summary_channel_ids = self._preloaded_explicit_channel_summary_ids(tool_messages)
        if preloaded_summary_channel_ids:
            tool_messages.append({
                "role": "system",
                "content": (
                    "Stored summaries for explicitly mentioned Discord channels are already preloaded in context. "
                    "Do not call get_channel_summary for those exact channel IDs."
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
            redundant_summary_call = False

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
                    requested_channel_id = self._tool_channel_id_argument(arguments)
                    if tool_name == "get_channel_summary" and requested_channel_id in preloaded_summary_channel_ids:
                        redundant_summary_call = True
                        result = {
                            "ok": True,
                            "tool": tool_name,
                            "channel_id": requested_channel_id,
                            "already_preloaded": True,
                            "instruction": "The summary for this exact channel is already present in the conversation context. Use the preloaded summary and write the final Discord reply now.",
                        }
                        logging.info(f"Skipped redundant Discord tool '{tool_name}' for preloaded channel {requested_channel_id}.")
                    elif tool_signature in executed_tool_results:
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

            if redundant_summary_call:
                forced_stop_reason = "Redundant summary tool call skipped because the exact channel summary is already preloaded. Use the preloaded summary to write one final Discord reply now."
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

    def _messages_have_image_content(self, messages: list) -> bool:
        for message in messages or []:
            if self._content_has_image_part(message.get("content") if isinstance(message, dict) else None):
                return True
        return False

    def _content_has_image_part(self, content) -> bool:
        if isinstance(content, list):
            return any(self._content_has_image_part(item) for item in content)
        if not isinstance(content, dict):
            return False
        if content.get("type") in {"image_url", "input_image"}:
            return True
        return any(self._content_has_image_part(value) for value in content.values())

    def _preloaded_explicit_channel_summary_ids(self, messages: list) -> set:
        channel_ids = set()
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or "Preloaded Explicit Channel Summary" not in content:
                continue
            channel_ids.update(re.findall(r"Preloaded Explicit Channel Summary.*?\(Channel ID:\s*(\d+)\)", content, flags=re.DOTALL))
        return channel_ids

    def _tool_names(self, tools: list) -> set:
        names = set()
        for tool in tools:
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            name = function.get("name")
            if name:
                names.add(name)
        return names

    def _tool_channel_id_argument(self, arguments: dict) -> str:
        channel_id = (arguments or {}).get("channel_id")
        if channel_id is None:
            return ""

        channel_id = str(channel_id).strip()
        mention_match = re.fullmatch(r"<#(\d+)>", channel_id)
        if mention_match:
            return mention_match.group(1)
        return channel_id

    def _tool_call_signature(self, tool_name: str, arguments: dict) -> str:
        try:
            serialized_arguments = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            serialized_arguments = str(arguments)
        return f"{tool_name}:{serialized_arguments}"

    def _with_message_chain_context(self, messages: list, chain_messages: list = None, long_typing: bool = False) -> list:
        if not chain_messages and not long_typing:
            return messages

        chain_note = self._format_message_chain_note(chain_messages, long_typing=long_typing)
        if not chain_note:
            return messages

        updated_messages = list(messages)
        insert_at = len(updated_messages)
        if updated_messages and updated_messages[-1].get("role") == "user":
            insert_at = len(updated_messages) - 1

        updated_messages.insert(insert_at, {"role": "system", "content": chain_note})
        return updated_messages

    def _format_message_chain_note(
        self,
        chain_messages: list,
        max_messages: int = 12,
        max_chars: int = 2500,
        long_typing: bool = False,
    ) -> str:
        visible_messages = list(chain_messages or [])[-max_messages:]
        if len(visible_messages) <= 1 and not long_typing:
            return ""

        latest = visible_messages[-1] if visible_messages else None
        if not latest:
            return ""
        author_name = getattr(latest.author, "display_name", getattr(latest.author, "name", "Unknown User"))
        author_id = getattr(latest.author, "id", "unknown")
        lines = [
            (
                f"Current same-user Discord message chain from {author_name} (User ID: {author_id}), "
                "oldest to newest. The user sent these as separate messages in quick succession; "
                "treat them as one current input and produce at most one reply or reaction for the whole chain."
            )
        ]
        if long_typing:
            lines.append(
                "Long-typing note: after this message chain, the same user kept typing for a long time. "
                "A light joke about writing a book is allowed only if it fits the channel tone."
            )

        if len(chain_messages) > max_messages:
            lines.append(f"[{len(chain_messages) - max_messages} older chain message(s) omitted]")

        for chain_message in visible_messages:
            timestamp = chain_message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            content = (chain_message.content or "[empty message]").replace("\r", " ").replace("\n", " ").strip()
            extra_parts = []
            if chain_message.attachments:
                filenames = ", ".join(attachment.filename for attachment in chain_message.attachments)
                extra_parts.append(f"attachments: {filenames}")
            if chain_message.embeds:
                extra_parts.append(f"embeds: {len(chain_message.embeds)}")
            extra = f" ({'; '.join(extra_parts)})" if extra_parts else ""
            lines.append(f"[{timestamp}] message_id={chain_message.id}: {content}{extra}")

        note = "\n".join(lines)
        if len(note) > max_chars:
            note = note[: max_chars - 3].rstrip() + "..."
        return note

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
        content = content.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
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
