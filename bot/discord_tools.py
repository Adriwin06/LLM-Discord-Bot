import json
import logging
import re
import socket
import asyncio
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, urlparse

import aiohttp
import discord
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException


class _URLAttachment:
    def __init__(self, *, url: str, filename: str, content_type: str = "", size: int = 0):
        self.url = url
        self.filename = filename
        self.content_type = content_type
        self.size = size


class DiscordToolManager:
    """Read-only Discord, GIPHY, and web tools exposed to tool-capable LLMs."""

    def __init__(self, bot):
        self.bot = bot

    def tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_channels",
                    "description": (
                        "List Discord channels visible to the bot in the current server, including channel IDs. "
                        "Use this before fetching/searching messages when the target channel is unclear."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "messageable_only": {
                                "type": "boolean",
                                "description": "Only include channels that expose message history. Defaults to true.",
                            },
                            "include_threads": {
                                "type": "boolean",
                                "description": "Also include currently visible active threads. Defaults to false.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum channels to return. Defaults to 100, max 200.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "resolve_channel",
                    "description": (
                        "Resolve a Discord channel name, mention, or ID to visible channel metadata. "
                        "Use this to get channel IDs for message tools."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Channel name, #mention, mention string, or numeric channel ID.",
                            },
                            "include_threads": {
                                "type": "boolean",
                                "description": "Also search currently visible active threads. Defaults to true.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum matching channels to return. Defaults to 5, max 10.",
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_channel_summary",
                    "description": (
                        "Fetch the stored summary for a visible Discord channel. "
                        "Use this when a channel recap or older channel-level context is needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel_id": {
                                "type": "string",
                                "description": "Discord channel ID to summarize.",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum summary characters to return. Defaults to 3000, max 8000.",
                            },
                        },
                        "required": ["channel_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_messages",
                    "description": (
                        "Search recent Discord message history visible to the bot. "
                        "Use this when older channel context is needed before answering."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Case-insensitive text to find in message content. Leave empty to filter only by author/time/channel.",
                            },
                            "channel_id": {
                                "type": "string",
                                "description": "Discord channel ID to search. Defaults to the current channel.",
                            },
                            "author_id": {
                                "type": "string",
                                "description": "Only return messages by this Discord user ID.",
                            },
                            "include_all_readable_channels": {
                                "type": "boolean",
                                "description": "Search all readable text channels in the current server instead of only one channel.",
                            },
                            "after_iso": {
                                "type": "string",
                                "description": "Only search messages after this ISO-8601 timestamp.",
                            },
                            "before_iso": {
                                "type": "string",
                                "description": "Only search messages before this ISO-8601 timestamp.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum matching messages to return. Defaults to 10, max 25.",
                            },
                            "history_limit": {
                                "type": "integer",
                                "description": "Maximum recent messages to scan per channel. Defaults to 200, max 1000.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_message",
                    "description": "Fetch one visible Discord message by channel ID and message ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel_id": {
                                "type": "string",
                                "description": "Discord channel ID containing the message.",
                            },
                            "message_id": {
                                "type": "string",
                                "description": "Discord message ID to fetch.",
                            },
                        },
                        "required": ["channel_id", "message_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_recent_messages",
                    "description": "Get recent visible messages from a Discord channel.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "channel_id": {
                                "type": "string",
                                "description": "Discord channel ID. Defaults to the current channel.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Number of recent messages to return. Defaults to 10, max 50.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_user_profile",
                    "description": (
                        "Fetch a Discord user's profile details visible to the bot, including names, IDs, avatars, "
                        "status/activities, roles, and stored profile notes. Provide user_id or query; defaults to the message author."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user_id": {
                                "type": "string",
                                "description": "Discord user ID or mention to fetch.",
                            },
                            "query": {
                                "type": "string",
                                "description": "Username, display name, or mention to resolve within the server.",
                            },
                            "include_activities": {
                                "type": "boolean",
                                "description": "Include presence activities/status when available. Defaults to true.",
                            },
                            "include_roles": {
                                "type": "boolean",
                                "description": "Include guild role details. Defaults to true.",
                            },
                            "include_profile_notes": {
                                "type": "boolean",
                                "description": "Include stored manual notes and AI summaries. Defaults to true.",
                            },
                            "max_roles": {
                                "type": "integer",
                                "description": "Maximum roles to return (excluding @everyone). Defaults to 50, max 200.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": (
                        "Search the public web and return result titles, snippets, and URLs. "
                        "Use only when current or external information is needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Public web search query.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum search results to return. Defaults to 5, max 8.",
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_web_page",
                    "description": (
                        "Fetch and extract readable text from a public web page URL. "
                        "Use after web_search when a result looks relevant."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "Public http or https URL to fetch.",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum extracted characters to return. Defaults to config, max 12000.",
                            },
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_giphy_gif",
                    "description": (
                        "Search GIPHY for a GIF URL that can be sent in Discord. "
                        "Use this when the user asks for a GIF, when recent context says replies should be GIFs, "
                        "or when a GIF is clearly a better low-risk response than text. "
                        "Prefer famous, broadly recognizable meme/reaction GIFs over obscure or literal results."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Final GIPHY search query based on the current Discord context. "
                                    "Use concise, GIPHY-friendly terms: a known meme name, recognizable reaction phrase, emotion, or scene description. "
                                    "Do not pass the user's whole sentence."
                                ),
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    async def execute_tool_call(self, tool_call: Any, origin_message: discord.Message) -> Dict[str, Any]:
        name, arguments = self.parse_tool_call(tool_call)
        try:
            if name == "list_channels":
                return await self.list_channels(origin_message, **arguments)
            if name == "resolve_channel":
                return await self.resolve_channel(origin_message, **arguments)
            if name == "get_channel_summary":
                return await self.get_channel_summary(origin_message, **arguments)
            if name == "search_messages":
                return await self.search_messages(origin_message, **arguments)
            if name == "fetch_message":
                return await self.fetch_message(origin_message, **arguments)
            if name == "get_recent_messages":
                return await self.get_recent_messages(origin_message, **arguments)
            if name == "get_user_profile":
                return await self.get_user_profile(origin_message, **arguments)
            if name == "search_giphy_gif":
                return await self.search_giphy_gif(origin_message, **arguments)
            if name == "web_search":
                return await self.web_search(**arguments)
            if name == "fetch_web_page":
                return await self.fetch_web_page(**arguments)

            return {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as e:
            logging.exception(f"Discord tool {name} failed")
            return {"ok": False, "tool": name, "error": str(e)}

    async def list_channels(
        self,
        origin_message: discord.Message,
        messageable_only: bool = True,
        include_threads: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        guild = origin_message.guild
        if not guild:
            return {"ok": False, "tool": "list_channels", "error": "Channel listing is only available in a server."}
        actor = self._origin_member(origin_message)
        if not actor:
            return {"ok": False, "tool": "list_channels", "error": "Could not verify the requesting member's channel permissions."}

        messageable_only = self._coerce_bool(messageable_only, default=True)
        include_threads = self._coerce_bool(include_threads, default=False)
        limit = self._clamp_int(limit, default=100, minimum=1, maximum=200)
        channels = self._visible_channels(
            guild,
            messageable_only=messageable_only,
            include_threads=include_threads,
            actor=actor,
        )
        formatted = [self._format_channel(channel, actor=actor) for channel in channels[:limit]]

        return {
            "ok": True,
            "tool": "list_channels",
            "guild_id": str(guild.id),
            "guild_name": getattr(guild, "name", str(guild.id)),
            "messageable_only": messageable_only,
            "include_threads": include_threads,
            "returned": len(formatted),
            "total_visible": len(channels),
            "results": formatted,
        }

    async def resolve_channel(
        self,
        origin_message: discord.Message,
        query: str,
        include_threads: bool = True,
        limit: int = 5,
    ) -> Dict[str, Any]:
        guild = origin_message.guild
        if not guild:
            return {"ok": False, "tool": "resolve_channel", "error": "Channel resolution is only available in a server."}
        actor = self._origin_member(origin_message)
        if not actor:
            return {"ok": False, "tool": "resolve_channel", "error": "Could not verify the requesting member's channel permissions."}

        query = str(query or "").strip()
        if not query:
            return {"ok": False, "tool": "resolve_channel", "error": "Query is required."}

        include_threads = self._coerce_bool(include_threads, default=True)
        limit = self._clamp_int(limit, default=5, minimum=1, maximum=10)
        direct_id = self._parse_optional_int(query)
        matches = []
        matched_by = "name"

        if direct_id is not None:
            matched_by = "id"
            channel = self._resolve_channel(guild, query)
            if channel and self._can_view_channel(channel, actor=actor):
                matches = [channel]
        else:
            query_norm = self._normalize_channel_query(query)
            if not query_norm:
                return {"ok": False, "tool": "resolve_channel", "error": "Query is required."}

            visible_channels = self._visible_channels(guild, messageable_only=False, include_threads=include_threads, actor=actor)
            exact_matches = []
            prefix_matches = []
            contains_matches = []

            for channel in visible_channels:
                name_norm = self._normalize_channel_query(getattr(channel, "name", ""))
                if not name_norm:
                    continue
                if name_norm == query_norm:
                    exact_matches.append(channel)
                elif name_norm.startswith(query_norm):
                    prefix_matches.append(channel)
                elif query_norm in name_norm:
                    contains_matches.append(channel)

            matches = self._dedupe_channels([*exact_matches, *prefix_matches, *contains_matches])

        formatted = [self._format_channel(channel, actor=actor) for channel in matches[:limit]]
        return {
            "ok": True,
            "tool": "resolve_channel",
            "query": query,
            "matched_by": matched_by,
            "include_threads": include_threads,
            "returned": len(formatted),
            "results": formatted,
        }

    async def get_channel_summary(
        self,
        origin_message: discord.Message,
        channel_id: str,
        max_chars: int = 3000,
    ) -> Dict[str, Any]:
        guild = origin_message.guild
        if not guild:
            return {"ok": False, "tool": "get_channel_summary", "error": "Channel summaries are only available in a server."}
        actor = self._origin_member(origin_message)
        if not actor:
            return {"ok": False, "tool": "get_channel_summary", "error": "Could not verify the requesting member's channel permissions."}

        channel = self._resolve_channel(guild, channel_id)
        if not channel:
            return {"ok": False, "tool": "get_channel_summary", "error": "Channel was not found in the current server."}
        if not self._can_read_history(channel, actor=actor):
            return {"ok": False, "tool": "get_channel_summary", "error": "The bot cannot read that channel's summary."}

        context_manager = getattr(self.bot, "context_manager", None)
        if not context_manager:
            return {"ok": False, "tool": "get_channel_summary", "error": "Context manager is not available."}

        max_chars = self._clamp_int(max_chars, default=3000, minimum=500, maximum=8000)
        summary_data = await context_manager.get_channel_summary(str(guild.id), str(channel.id))
        summary = str(summary_data.get("summary") or "").strip()
        summary_available = bool(summary and summary != "No summary available yet.")
        truncated = len(summary) > max_chars
        if truncated:
            summary = summary[: max_chars - 3].rstrip() + "..."

        return {
            "ok": True,
            "tool": "get_channel_summary",
            "guild_id": str(guild.id),
            "channel": self._format_channel(channel, actor=actor),
            "summary_available": summary_available,
            "summary": summary,
            "truncated": truncated,
            "last_summary_time": summary_data.get("last_summary_time"),
            "messages_since_summary": summary_data.get("messages_since_summary"),
            "messages_processed": summary_data.get("messages_processed"),
            "summary_type": summary_data.get("summary_type"),
        }

    async def search_messages(
        self,
        origin_message: discord.Message,
        query: str = "",
        channel_id: Optional[str] = None,
        author_id: Optional[str] = None,
        include_all_readable_channels: bool = False,
        after_iso: Optional[str] = None,
        before_iso: Optional[str] = None,
        limit: int = 10,
        history_limit: int = 200,
    ) -> Dict[str, Any]:
        limit = self._clamp_int(limit, default=10, minimum=1, maximum=25)
        history_limit = self._clamp_int(history_limit, default=200, minimum=1, maximum=1000)
        include_all_readable_channels = self._coerce_bool(include_all_readable_channels, default=False)
        query_norm = (query or "").casefold().strip()
        author_id_int = self._parse_optional_int(author_id)
        after = self._parse_optional_datetime(after_iso)
        before = self._parse_optional_datetime(before_iso)

        channels = self._resolve_search_channels(origin_message, channel_id, include_all_readable_channels)
        if not channels:
            return {"ok": False, "tool": "search_messages", "error": "No readable channels matched the requested scope."}

        results = []
        scanned = 0
        for channel in channels:
            async for msg in channel.history(limit=history_limit, before=before, after=after, oldest_first=False):
                scanned += 1
                if author_id_int is not None and msg.author.id != author_id_int:
                    continue
                if query_norm and query_norm not in (msg.content or "").casefold():
                    continue

                results.append(self._format_message(msg))
                if len(results) >= limit:
                    break

            if len(results) >= limit:
                break

        return {
            "ok": True,
            "tool": "search_messages",
            "query": query,
            "channel_count": len(channels),
            "scanned": scanned,
            "returned": len(results),
            "results": results,
        }

    async def fetch_message(
        self,
        origin_message: discord.Message,
        channel_id: str,
        message_id: str,
    ) -> Dict[str, Any]:
        channel = self._resolve_channel(origin_message.guild, channel_id)
        if not channel:
            return {"ok": False, "tool": "fetch_message", "error": "Channel was not found in the current server."}
        actor = self._origin_member(origin_message)
        if not actor:
            return {"ok": False, "tool": "fetch_message", "error": "Could not verify the requesting member's channel permissions."}
        if not self._can_read_history(channel, actor=actor):
            return {"ok": False, "tool": "fetch_message", "error": "The bot cannot read message history in that channel."}
        if not self._supports_fetch_message(channel):
            return {"ok": False, "tool": "fetch_message", "error": "That channel does not support fetching messages by ID."}

        try:
            msg = await channel.fetch_message(int(message_id))
        except (TypeError, ValueError):
            return {"ok": False, "tool": "fetch_message", "error": "Invalid message_id."}
        except discord.NotFound:
            return {"ok": False, "tool": "fetch_message", "error": "Message was not found."}
        except discord.Forbidden:
            return {"ok": False, "tool": "fetch_message", "error": "Discord denied access to that message."}

        return {"ok": True, "tool": "fetch_message", "message": self._format_message(msg, max_content=1500)}

    async def get_recent_messages(
        self,
        origin_message: discord.Message,
        channel_id: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        limit = self._clamp_int(limit, default=10, minimum=1, maximum=50)
        channel = self._resolve_channel(origin_message.guild, channel_id) if channel_id else origin_message.channel
        if not channel:
            return {"ok": False, "tool": "get_recent_messages", "error": "Channel was not found in the current server."}
        actor = self._origin_member(origin_message)
        if not actor:
            return {"ok": False, "tool": "get_recent_messages", "error": "Could not verify the requesting member's channel permissions."}
        if not self._can_read_history(channel, actor=actor):
            return {"ok": False, "tool": "get_recent_messages", "error": "The bot cannot read message history in that channel."}
        if not self._supports_history(channel):
            return {"ok": False, "tool": "get_recent_messages", "error": "That channel does not expose message history."}

        messages = []
        async for msg in channel.history(limit=limit, oldest_first=False):
            messages.append(self._format_message(msg))

        messages.reverse()
        return {
            "ok": True,
            "tool": "get_recent_messages",
            "channel_id": str(channel.id),
            "channel_name": getattr(channel, "name", str(channel.id)),
            "returned": len(messages),
            "results": messages,
        }

    async def get_user_profile(
        self,
        origin_message: discord.Message,
        user_id: Optional[str] = None,
        query: Optional[str] = None,
        include_activities: bool = True,
        include_roles: bool = True,
        include_profile_notes: bool = True,
        max_roles: int = 50,
    ) -> Dict[str, Any]:
        include_activities = self._coerce_bool(include_activities, default=True)
        include_roles = self._coerce_bool(include_roles, default=True)
        include_profile_notes = self._coerce_bool(include_profile_notes, default=True)
        max_roles = self._clamp_int(max_roles, default=50, minimum=0, maximum=200)

        guild = origin_message.guild
        resolved_user_id = self._parse_optional_int(user_id)
        query = str(query or "").strip()
        matched_by = None
        member = None

        if resolved_user_id is not None:
            matched_by = "user_id"

        if resolved_user_id is None and query:
            resolved_user_id = self._parse_optional_int(query)
            if resolved_user_id is not None:
                matched_by = "query_id"

        if resolved_user_id is None and not query:
            author = getattr(origin_message, "author", None)
            if author is not None:
                resolved_user_id = getattr(author, "id", None)
                member = author if isinstance(author, discord.Member) else None
                matched_by = "message_author"

        if resolved_user_id is None and query:
            if not guild:
                return {
                    "ok": False,
                    "tool": "get_user_profile",
                    "error": "User resolution by name requires a server context.",
                }
            matches = self._find_members_by_query(guild, query, limit=5)
            if len(matches) == 1:
                member = matches[0]
                resolved_user_id = member.id
                matched_by = "query"
            elif matches:
                return {
                    "ok": False,
                    "tool": "get_user_profile",
                    "error": "Multiple users match the query. Use a user_id or a more specific name.",
                    "query": query,
                    "matches": [self._format_member_match(match) for match in matches],
                }
            else:
                return {
                    "ok": False,
                    "tool": "get_user_profile",
                    "error": "No users matched the query.",
                    "query": query,
                }

        if resolved_user_id is None:
            return {
                "ok": False,
                "tool": "get_user_profile",
                "error": "A user_id or query is required to fetch a profile.",
            }

        if guild and member is None:
            member = guild.get_member(resolved_user_id)
            if member is None and hasattr(guild, "fetch_member"):
                try:
                    member = await guild.fetch_member(resolved_user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None

        user = member or self.bot.get_user(resolved_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(resolved_user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        if user is None and member is None:
            return {
                "ok": False,
                "tool": "get_user_profile",
                "error": "User was not found.",
                "user_id": str(resolved_user_id),
            }

        user_info = self._format_user_profile(user, member)
        links = self._format_user_links(user, member)
        activities = []
        custom_status = None
        presence_intent = bool(getattr(getattr(self.bot, "intents", None), "presences", False))

        if include_activities and member is not None:
            raw_activities = list(getattr(member, "activities", []) or [])
            activities = [self._format_activity(activity) for activity in raw_activities if activity]
            custom_status = self._extract_custom_status(raw_activities)

        stored_profile = None
        if include_profile_notes and guild and hasattr(self.bot, "store"):
            try:
                data = await self.bot.store.get_data()
                user_data = data.get(str(guild.id), {}).get("users", {}).get(str(resolved_user_id), {})
                if user_data:
                    stored_profile = self._drop_empty({
                        "manual_note": user_data.get("manual_note"),
                        "ai_summary": user_data.get("ai_summary"),
                        "last_profile_update_time": user_data.get("last_profile_update_time"),
                        "messages_since_profile_update": user_data.get("messages_since_profile_update"),
                    })
            except Exception:
                stored_profile = None

        member_info = None
        if member is not None:
            member_info = self._format_member_profile(member, include_roles=include_roles, max_roles=max_roles)

        return self._drop_empty({
            "ok": True,
            "tool": "get_user_profile",
            "matched_by": matched_by,
            "query": query or None,
            "user_id": str(resolved_user_id),
            "user": user_info,
            "member": member_info,
            "links": links,
            "activities": activities,
            "custom_status": custom_status,
            "stored_profile": stored_profile,
            "availability": {
                "presence_intent_enabled": presence_intent,
                "activities_available": bool(activities),
                "bio_available": False,
                "connections_available": False,
            },
        })

    async def web_search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        if not getattr(self.bot.config, "WEB_SEARCH_ENABLED", True):
            return {"ok": False, "tool": "web_search", "error": "Web search tools are disabled in configuration."}

        query = (query or "").strip()
        if not query:
            return {"ok": False, "tool": "web_search", "error": "Query is required."}

        limit = self._clamp_int(limit, default=5, minimum=1, maximum=8)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        providers = [
            ("duckduckgo", f"https://duckduckgo.com/html/?q={quote_plus(query)}", _DuckDuckGoHTMLParser().parse),
            ("duckduckgo-lite", f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}", _DuckDuckGoHTMLParser().parse),
            ("bing-rss", f"https://www.bing.com/search?format=rss&q={quote_plus(query)}", self._parse_bing_rss_results),
            ("bing", f"https://www.bing.com/search?q={quote_plus(query)}", self._parse_bing_results),
        ]

        provider_errors = []
        results = []
        used_provider = None

        async with aiohttp.ClientSession(headers=headers) as session:
            for provider_name, search_url, parser in providers:
                try:
                    async with session.get(search_url, timeout=15, allow_redirects=True) as response:
                        html = await response.text(errors="ignore")
                        if response.status != 200:
                            provider_errors.append(f"{provider_name}: HTTP {response.status}")
                            continue
                except Exception as e:
                    provider_errors.append(f"{provider_name}: {e}")
                    continue

                parsed_results = parser(html)
                if parsed_results:
                    results = parsed_results
                    used_provider = provider_name
                    break

        deduped = []
        seen = set()
        for result in results:
            url = self._normalize_duckduckgo_url(result.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append({
                "title": result.get("title", "").strip(),
                "url": url,
                "snippet": result.get("snippet", "").strip(),
            })
            if len(deduped) >= limit:
                break

        return {
            "ok": True,
            "tool": "web_search",
            "query": query,
            "provider": used_provider,
            "returned": len(deduped),
            "results": deduped,
            "provider_errors": provider_errors,
        }

    async def search_giphy_gif(
        self,
        origin_message: discord.Message,
        query: str,
        caption: str = "",
    ) -> Dict[str, Any]:
        settings = {}
        context_manager = getattr(self.bot, "context_manager", None)
        if context_manager and origin_message.guild:
            settings = await context_manager.get_guild_and_channel_settings(
                str(origin_message.guild.id),
                str(origin_message.channel.id),
            )

        gifs_enabled = self._coerce_bool(
            settings.get("gifs_enabled"),
            default=getattr(self.bot.config, "GIFS_ENABLED", True),
        )
        if not gifs_enabled:
            return {"ok": False, "tool": "search_giphy_gif", "error": "GIF replies are disabled for this scope."}

        giphy_client = getattr(self.bot, "giphy_client", None)
        if not giphy_client or not getattr(giphy_client, "enabled", False):
            return {"ok": False, "tool": "search_giphy_gif", "error": "GIPHY_API_KEY is not configured."}

        query = self._sanitize_giphy_query(query)
        if not query:
            return {"ok": False, "tool": "search_giphy_gif", "error": "A safe GIPHY search query is required."}

        analyze = self._giphy_analysis_enabled()
        candidate_limit = self._giphy_analysis_max_candidates() if analyze else 1
        candidates = await giphy_client.search_gifs(query, limit=candidate_limit, user_key=str(origin_message.author.id))
        if not candidates:
            return {
                "ok": False,
                "tool": "search_giphy_gif",
                "query": query,
                "error": "No GIPHY result found.",
            }

        gif = candidates[0]
        verification = None
        if analyze:
            gif = None
            for candidate in candidates[:candidate_limit]:
                verification = await self._verify_giphy_candidate(origin_message, query, candidate, settings)
                logging.info(
                    "GIPHY visual verification result. message_id=%s query=%r gif_id=%s accepted=%s reason=%r",
                    origin_message.id,
                    query,
                    candidate.id,
                    verification.get("accepted"),
                    verification.get("reason"),
                )
                if verification.get("accepted"):
                    gif = candidate
                    break

            if not gif:
                return {
                    "ok": False,
                    "tool": "search_giphy_gif",
                    "query": query,
                    "error": f"No visually suitable GIPHY result found after checking {min(len(candidates), candidate_limit)} candidate(s).",
                    "last_verification": verification,
                }

        await giphy_client.register_sent(gif, user_key=str(origin_message.author.id))
        logging.info(
            "GIPHY tool result selected. message_id=%s query=%r gif_id=%s title=%r url=%s",
            origin_message.id,
            query,
            gif.id,
            gif.title,
            gif.url,
        )

        return {
            "ok": True,
            "tool": "search_giphy_gif",
            "query": query,
            "gif_id": gif.id,
            "title": gif.title,
            "rating": gif.rating,
            "url": gif.url,
            "caption": "",
            "recommended_reply": gif.url,
            "instruction": "To send this GIF in Discord, make your final reply exactly this URL and no caption or extra text.",
            "verification": verification,
        }

    async def _verify_giphy_candidate(
        self,
        origin_message: discord.Message,
        query: str,
        gif: Any,
        settings: dict,
    ) -> Dict[str, Any]:
        context_manager = getattr(self.bot, "context_manager", None)
        if not context_manager:
            return {"accepted": False, "reason": "Context manager is unavailable."}

        model = str(getattr(self.bot.config, "GIPHY_ANALYSIS_MODEL", "") or "").strip()
        if not model:
            model = settings.get("model") or getattr(self.bot.config, "MAIN_LLM_MODEL", "")
        if not model:
            return {"accepted": False, "reason": "No verifier model is configured."}

        media_settings = (settings or {}).get("media", {})
        media_url = getattr(gif, "media_url", "") or getattr(gif, "url", "")
        attachment = _URLAttachment(
            url=media_url,
            filename=f"giphy_{getattr(gif, 'id', 'candidate')}.gif",
            content_type="image/gif",
        )

        try:
            processed = await context_manager._process_attachment(attachment, model, media_settings)
        except Exception as e:
            logging.exception("GIPHY candidate media processing failed. gif_id=%s", getattr(gif, "id", None))
            return {"accepted": False, "reason": f"Could not process candidate GIF: {type(e).__name__}: {e}"}

        content_parts = [
            {
                "type": "text",
                "text": (
                    "Verify this candidate GIPHY result before it is sent in Discord.\n"
                    f"Original Discord message: {origin_message.content or '[empty message]'}\n"
                    f"Intended GIPHY search query: {query}\n"
                    f"GIPHY title: {getattr(gif, 'title', '') or '[untitled]'}\n"
                    f"GIPHY URL to send if accepted: {getattr(gif, 'url', '')}\n"
                    "Accept only if the visual content clearly fits the intended reaction/context. "
                    "Prefer famous, broadly recognizable meme/reaction GIFs over obscure personal uploads. "
                    "Reject if it is confusing, off-topic, too obscure, unsafe, or likely to look random."
                ),
            }
        ]
        content_parts.extend(self._processed_giphy_media_parts(processed))

        messages = [
            {
                "role": "system",
                "content": (
                    "You verify candidate GIPHY GIFs for a Discord bot. "
                    "Use the visual frames and OCR text when provided. "
                    "Return only JSON with keys: accepted (boolean), reason (short string), refined_query (string or null)."
                ),
            },
            {"role": "user", "content": content_parts},
        ]

        response = await self.bot.llm_provider.create_completion(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = self._response_content(response)
        if not content:
            return {"accepted": False, "reason": "Verifier model returned no content."}

        try:
            parsed = json.loads(self._clean_json_response(content))
        except (json.JSONDecodeError, TypeError):
            return {"accepted": False, "reason": f"Verifier returned invalid JSON: {str(content)[:120]}"}

        return {
            "accepted": self._coerce_bool(parsed.get("accepted"), default=False),
            "reason": str(parsed.get("reason") or "").strip()[:300],
            "refined_query": str(parsed.get("refined_query") or "").strip()[:80] or None,
            "model": model,
        }

    def _processed_giphy_media_parts(self, processed: Any) -> List[Dict[str, Any]]:
        parts = []
        if isinstance(processed, dict) and processed.get("type") == "animated_gif":
            filename = processed.get("filename") or "GIPHY GIF"
            total_frames = processed.get("total_frames", "unknown")
            extracted_frames = processed.get("extracted_frames", 0)
            parts.append({
                "type": "text",
                "text": f"Animated GIF candidate: {filename}. Showing {extracted_frames} representative frame(s) from {total_frames} total frame(s).",
            })
            ocr_text = str(processed.get("ocr_text") or "").strip()
            if ocr_text:
                parts.append({
                    "type": "text",
                    "text": f"--- OCR text extracted from candidate GIF frames ---\n{ocr_text}",
                })
            for frame_url in processed.get("frames") or []:
                parts.append({"type": "image_url", "image_url": {"url": frame_url}})
            return parts

        if isinstance(processed, list):
            return processed

        if isinstance(processed, dict):
            return [processed]

        if processed:
            return [{"type": "text", "text": str(processed)}]

        return [{"type": "text", "text": "[No visual media context was extracted for this GIF candidate.]"}]

    def _giphy_analysis_enabled(self) -> bool:
        return self._coerce_bool(getattr(self.bot.config, "GIPHY_ANALYZE_BEFORE_SEND", False), default=False)

    def _giphy_analysis_max_candidates(self) -> int:
        return self._clamp_int(
            getattr(self.bot.config, "GIPHY_ANALYSIS_MAX_CANDIDATES", 3),
            default=3,
            minimum=1,
            maximum=10,
        )

    async def fetch_web_page(self, url: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
        if not getattr(self.bot.config, "WEB_SEARCH_ENABLED", True):
            return {"ok": False, "tool": "fetch_web_page", "error": "Web tools are disabled in configuration."}

        url = (url or "").strip()
        safety_error = await self._validate_public_url(url)
        if safety_error:
            return {"ok": False, "tool": "fetch_web_page", "error": safety_error}

        configured_max = getattr(self.bot.config, "WEB_FETCH_MAX_CHARS", 6000)
        max_chars = self._clamp_int(max_chars, default=configured_max, minimum=500, maximum=12000)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; LLM-Discord-Bot/1.0)",
            "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.5",
        }

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=20, allow_redirects=True, max_redirects=5) as response:
                    final_url = str(response.url)
                    safety_error = await self._validate_public_url(final_url)
                    if safety_error:
                        return {"ok": False, "tool": "fetch_web_page", "error": f"Unsafe redirect target: {safety_error}"}
                    if response.status >= 400:
                        return {
                            "ok": False,
                            "tool": "fetch_web_page",
                            "url": url,
                            "final_url": final_url,
                            "error": f"Page returned HTTP {response.status}.",
                        }

                    content_type = response.headers.get("content-type", "")
                    if not self._is_supported_web_content_type(content_type):
                        return {
                            "ok": False,
                            "tool": "fetch_web_page",
                            "url": url,
                            "final_url": final_url,
                            "content_type": content_type,
                            "error": "Unsupported content type for text extraction.",
                        }

                    raw = await response.content.read(max_chars * 8)
                    text = raw.decode(response.charset or "utf-8", errors="ignore")
        except Exception as e:
            return {"ok": False, "tool": "fetch_web_page", "url": url, "error": f"Fetch failed: {e}"}

        title = ""
        if "html" in content_type.lower():
            title = self._extract_title(text)
            extracted = self._html_to_text(text)
        else:
            extracted = text

        extracted = self._normalize_whitespace(extracted)
        truncated = len(extracted) > max_chars
        if truncated:
            extracted = extracted[:max_chars].rstrip() + "..."

        return {
            "ok": True,
            "tool": "fetch_web_page",
            "url": url,
            "final_url": final_url,
            "title": title,
            "content_type": content_type,
            "truncated": truncated,
            "text": extracted,
        }

    def get_tool_calls(self, assistant_message: Any) -> List[Any]:
        tool_calls = self._get(assistant_message, "tool_calls")
        if not tool_calls:
            return []
        return list(tool_calls)

    def assistant_message_for_history(self, assistant_message: Any, tool_calls: Iterable[Any]) -> Dict[str, Any]:
        content = self._get(assistant_message, "content")
        return {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [self.tool_call_to_dict(tool_call) for tool_call in tool_calls],
        }

    def tool_result_message(self, tool_call: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        tool_call_id = self._get(tool_call, "id") or "tool_call"
        name, _ = self.parse_tool_call(tool_call)
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": json.dumps(result, ensure_ascii=False),
        }

    def tool_call_to_dict(self, tool_call: Any) -> Dict[str, Any]:
        name, arguments = self.parse_tool_call(tool_call)
        tool_call_id = self._get(tool_call, "id") or f"call_{name}"
        raw_arguments = self._get(self._get(tool_call, "function", {}), "arguments")
        if not isinstance(raw_arguments, str):
            raw_arguments = json.dumps(arguments)
        return {
            "id": tool_call_id,
            "type": self._get(tool_call, "type") or "function",
            "function": {
                "name": name,
                "arguments": raw_arguments,
            },
        }

    def parse_tool_call(self, tool_call: Any) -> Tuple[str, Dict[str, Any]]:
        function = self._get(tool_call, "function", {})
        name = self._get(function, "name") or self._get(tool_call, "name")
        raw_arguments = self._get(function, "arguments") or self._get(tool_call, "arguments") or {}

        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        elif isinstance(raw_arguments, dict):
            arguments = raw_arguments
        else:
            arguments = {}

        return str(name or ""), arguments

    def _response_content(self, response: Any) -> str:
        if not response or not getattr(response, "choices", None):
            return ""
        choice = response.choices[0]
        message = self._get(choice, "message")
        return str(self._get(message, "content") or "")

    def _clean_json_response(self, content: str) -> str:
        content = str(content or "").strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()

    def _resolve_search_channels(
        self,
        origin_message: discord.Message,
        channel_id: Optional[str],
        include_all_readable_channels: bool,
    ) -> List[Any]:
        actor = self._origin_member(origin_message)
        if not actor:
            return []

        if include_all_readable_channels:
            return [
                channel
                for channel in self._visible_channels(origin_message.guild, messageable_only=True, include_threads=False, actor=actor)
                if self._can_read_history(channel, actor=actor)
            ]

        channel = self._resolve_channel(origin_message.guild, channel_id) if channel_id else origin_message.channel
        if channel and self._can_read_history(channel, actor=actor) and self._supports_history(channel):
            return [channel]
        return []

    def _resolve_channel(self, guild: discord.Guild, channel_id: Optional[str]) -> Optional[Any]:
        channel_id_int = self._parse_optional_int(channel_id)
        if channel_id_int is None:
            return None

        channel = guild.get_channel(channel_id_int)
        if not channel and hasattr(guild, "get_thread"):
            channel = guild.get_thread(channel_id_int)
        if not channel:
            channel = self.bot.get_channel(channel_id_int)
        if channel and getattr(channel, "guild", None) and channel.guild.id == guild.id:
            return channel
        return None

    def _visible_channels(
        self,
        guild: discord.Guild,
        *,
        messageable_only: bool,
        include_threads: bool,
        actor: Optional[Any] = None,
    ) -> List[Any]:
        channels = list(getattr(guild, "channels", []))
        if include_threads:
            channels.extend(getattr(guild, "threads", []))

        visible = []
        for channel in self._dedupe_channels(channels):
            if getattr(getattr(channel, "guild", None), "id", None) != guild.id:
                continue
            if not self._can_view_channel(channel, actor=actor):
                continue
            if messageable_only and not self._supports_history(channel):
                continue
            visible.append(channel)

        return sorted(visible, key=self._channel_sort_key)

    def _dedupe_channels(self, channels: Iterable[Any]) -> List[Any]:
        deduped = []
        seen = set()
        for channel in channels:
            channel_id = getattr(channel, "id", None)
            if channel_id is None or channel_id in seen:
                continue
            seen.add(channel_id)
            deduped.append(channel)
        return deduped

    def _can_view_channel(self, channel: Any, actor: Optional[Any] = None) -> bool:
        return self._member_has_permissions(channel, self._guild_member(getattr(channel, "guild", None)), "view_channel") and (
            actor is None or self._member_has_permissions(channel, actor, "view_channel")
        )

    def _can_read_history(self, channel: Any, actor: Optional[Any] = None) -> bool:
        required = ("view_channel", "read_message_history")
        return self._member_has_permissions(channel, self._guild_member(getattr(channel, "guild", None)), *required) and (
            actor is None or self._member_has_permissions(channel, actor, *required)
        )

    def _member_has_permissions(self, channel: Any, member: Optional[Any], *permission_names: str) -> bool:
        permissions = self._channel_permissions(channel, member)
        return bool(permissions and all(getattr(permissions, name, False) for name in permission_names))

    def _channel_permissions(self, channel: Any, member: Optional[Any] = None) -> Optional[Any]:
        guild = getattr(channel, "guild", None)
        subject = member or self._guild_member(guild)
        if not guild or not subject or not hasattr(channel, "permissions_for"):
            return None
        subject_guild = getattr(subject, "guild", None)
        if subject_guild and getattr(subject_guild, "id", None) != getattr(guild, "id", None):
            return None
        try:
            return channel.permissions_for(subject)
        except Exception:
            return None

    def _origin_member(self, origin_message: discord.Message) -> Optional[Any]:
        guild = getattr(origin_message, "guild", None)
        author = getattr(origin_message, "author", None)
        if not guild or not author:
            return None
        if isinstance(author, discord.Member) and getattr(getattr(author, "guild", None), "id", None) == guild.id:
            return author
        if hasattr(guild, "get_member") and getattr(author, "id", None) is not None:
            return guild.get_member(author.id)
        return None

    def _guild_member(self, guild: Optional[discord.Guild]) -> Optional[Any]:
        if not guild:
            return None
        me = getattr(guild, "me", None)
        if me:
            return me
        bot_user = getattr(self.bot, "user", None)
        if bot_user and hasattr(guild, "get_member"):
            return guild.get_member(bot_user.id)
        return None

    def _supports_history(self, channel: Any) -> bool:
        return callable(getattr(channel, "history", None))

    def _supports_fetch_message(self, channel: Any) -> bool:
        return callable(getattr(channel, "fetch_message", None))

    def _format_channel(self, channel: Any, actor: Optional[Any] = None) -> Dict[str, Any]:
        parent = getattr(channel, "parent", None)
        category = getattr(channel, "category", None)
        channel_id = getattr(channel, "id", "")
        parent_id = getattr(parent, "id", None)
        category_id = getattr(category, "id", None)
        can_read_history = self._can_read_history(channel, actor=actor)

        return {
            "channel_id": str(channel_id),
            "name": getattr(channel, "name", str(channel_id)),
            "mention": getattr(channel, "mention", f"<#{channel_id}>"),
            "type": str(getattr(channel, "type", channel.__class__.__name__)),
            "category_id": str(category_id) if category_id is not None else None,
            "category_name": getattr(category, "name", None) if category else None,
            "parent_channel_id": str(parent_id) if parent_id is not None else None,
            "parent_channel_name": getattr(parent, "name", None) if parent else None,
            "can_view": self._can_view_channel(channel, actor=actor),
            "can_read_history": can_read_history,
            "can_get_recent_messages": can_read_history and self._supports_history(channel),
            "can_fetch_message": can_read_history and self._supports_fetch_message(channel),
        }

    def _format_message(self, message: discord.Message, max_content: int = 700) -> Dict[str, Any]:
        content = message.content or ""
        if len(content) > max_content:
            content = content[: max_content - 3].rstrip() + "..."

        return {
            "message_id": str(message.id),
            "channel_id": str(message.channel.id),
            "channel_name": getattr(message.channel, "name", str(message.channel.id)),
            "author_id": str(message.author.id),
            "author_name": getattr(message.author, "display_name", getattr(message.author, "name", "Unknown User")),
            "author_is_bot": bool(getattr(message.author, "bot", False)),
            "created_at": message.created_at.astimezone(timezone.utc).isoformat(),
            "content": content if content else "[no text content]",
            "jump_url": message.jump_url,
            "attachments": [attachment.filename for attachment in message.attachments],
            "reply_to_message_id": str(message.reference.message_id) if message.reference and message.reference.message_id else None,
        }

    def _format_user_profile(self, user: Any, member: Optional[discord.Member]) -> Dict[str, Any]:
        display_name = None
        if member is not None:
            display_name = getattr(member, "display_name", None)
        if not display_name:
            display_name = getattr(user, "display_name", None) or getattr(user, "name", None)

        return self._drop_empty({
            "user_id": str(getattr(user, "id", "")),
            "username": getattr(user, "name", None),
            "global_name": getattr(user, "global_name", None),
            "display_name": display_name,
            "discriminator": getattr(user, "discriminator", None),
            "mention": getattr(user, "mention", None),
            "is_bot": bool(getattr(user, "bot", False)),
            "is_system": bool(getattr(user, "system", False)),
            "created_at": self._isoformat(getattr(user, "created_at", None)),
            "accent_color": self._format_color(getattr(user, "accent_color", None)),
        })

    def _format_user_links(self, user: Any, member: Optional[discord.Member]) -> Dict[str, Any]:
        return self._drop_empty({
            "profile_url": f"https://discord.com/users/{getattr(user, 'id', '')}",
            "avatar_url": self._asset_url(getattr(user, "avatar", None)),
            "display_avatar_url": self._asset_url(getattr(user, "display_avatar", None)),
            "banner_url": self._asset_url(getattr(user, "banner", None)),
            "guild_avatar_url": self._asset_url(getattr(member, "guild_avatar", None)) if member else None,
        })

    def _format_member_profile(self, member: discord.Member, *, include_roles: bool, max_roles: int) -> Dict[str, Any]:
        guild = getattr(member, "guild", None)
        roles = []
        roles_truncated = False
        if include_roles and hasattr(member, "roles"):
            all_roles = [role for role in member.roles if not self._is_default_role(role, guild)]
            all_roles.sort(key=lambda role: role.position, reverse=True)
            if max_roles and len(all_roles) > max_roles:
                roles_truncated = True
                all_roles = all_roles[:max_roles]
            roles = [self._format_role(role) for role in all_roles]

        permissions = []
        guild_permissions = getattr(member, "guild_permissions", None)
        if guild_permissions is not None:
            try:
                permissions = [name for name, value in guild_permissions if value]
            except Exception:
                permissions = []

        status = getattr(member, "status", None)
        if status is not None and hasattr(status, "name"):
            status = status.name

        return self._drop_empty({
            "guild_id": str(getattr(guild, "id", "")) if guild else None,
            "guild_name": getattr(guild, "name", None) if guild else None,
            "nickname": getattr(member, "nick", None),
            "display_name": getattr(member, "display_name", None),
            "joined_at": self._isoformat(getattr(member, "joined_at", None)),
            "premium_since": self._isoformat(getattr(member, "premium_since", None)),
            "timed_out_until": self._isoformat(
                getattr(member, "communication_disabled_until", None)
                or getattr(member, "timed_out_until", None)
            ),
            "pending": bool(getattr(member, "pending", False)) if hasattr(member, "pending") else None,
            "is_owner": bool(guild and getattr(guild, "owner_id", None) == getattr(member, "id", None)),
            "status": status,
            "permissions": permissions,
            "top_role": self._format_role(getattr(member, "top_role", None)) if getattr(member, "top_role", None) else None,
            "roles": roles,
            "roles_truncated": roles_truncated,
        })

    def _format_role(self, role: Optional[discord.Role]) -> Optional[Dict[str, Any]]:
        if role is None:
            return None
        return self._drop_empty({
            "role_id": str(getattr(role, "id", "")),
            "name": getattr(role, "name", None),
            "color": self._format_color(getattr(role, "color", None)),
            "position": getattr(role, "position", None),
            "mentionable": bool(getattr(role, "mentionable", False)),
            "hoist": bool(getattr(role, "hoist", False)),
            "managed": bool(getattr(role, "managed", False)),
        })

    def _format_activity(self, activity: Any) -> Dict[str, Any]:
        activity_type = getattr(activity, "type", None)
        if activity_type is not None and hasattr(activity_type, "name"):
            activity_type = activity_type.name.lower()

        emoji = getattr(activity, "emoji", None)
        formatted = {
            "type": activity_type,
            "name": getattr(activity, "name", None),
            "details": getattr(activity, "details", None),
            "state": getattr(activity, "state", None),
            "url": getattr(activity, "url", None),
            "application_id": str(getattr(activity, "application_id", None)) if getattr(activity, "application_id", None) else None,
            "created_at": self._isoformat(getattr(activity, "created_at", None)),
            "emoji": str(emoji) if emoji else None,
        }

        start_time = getattr(activity, "start", None)
        end_time = getattr(activity, "end", None)
        if start_time or end_time:
            formatted["timestamps"] = self._drop_empty({
                "start": self._isoformat(start_time),
                "end": self._isoformat(end_time),
            })

        if hasattr(activity, "track_id"):
            formatted["track"] = self._drop_empty({
                "track_id": getattr(activity, "track_id", None),
                "title": getattr(activity, "title", None),
                "artist": getattr(activity, "artist", None),
                "album": getattr(activity, "album", None),
                "duration_seconds": self._duration_seconds(getattr(activity, "duration", None)),
            })

        return self._drop_empty(formatted)

    def _extract_custom_status(self, activities: Iterable[Any]) -> Optional[Dict[str, Any]]:
        for activity in activities or []:
            activity_type = getattr(activity, "type", None)
            if activity_type is not None and hasattr(activity_type, "name"):
                if activity_type.name.lower() != "custom":
                    continue
            elif str(activity_type).lower() != "custom":
                continue

            emoji = getattr(activity, "emoji", None)
            return self._drop_empty({
                "state": getattr(activity, "state", None),
                "name": getattr(activity, "name", None),
                "emoji": str(emoji) if emoji else None,
            })
        return None

    def _format_member_match(self, member: discord.Member) -> Dict[str, Any]:
        return self._drop_empty({
            "user_id": str(getattr(member, "id", "")),
            "display_name": getattr(member, "display_name", None),
            "username": getattr(member, "name", None),
            "global_name": getattr(member, "global_name", None),
            "mention": getattr(member, "mention", None),
        })

    def _find_members_by_query(self, guild: discord.Guild, query: str, limit: int = 5) -> List[discord.Member]:
        query_norm = self._normalize_user_query(query)
        if not query_norm:
            return []

        exact_matches = []
        prefix_matches = []
        contains_matches = []
        for member in getattr(guild, "members", []) or []:
            names = self._member_search_names(member)
            if not names:
                continue

            if query_norm in names:
                exact_matches.append(member)
            elif any(name.startswith(query_norm) for name in names):
                prefix_matches.append(member)
            elif any(query_norm in name for name in names):
                contains_matches.append(member)

        deduped = []
        seen = set()
        for member in [*exact_matches, *prefix_matches, *contains_matches]:
            member_id = getattr(member, "id", None)
            if member_id is None or member_id in seen:
                continue
            seen.add(member_id)
            deduped.append(member)
            if len(deduped) >= limit:
                break

        return deduped

    def _member_search_names(self, member: discord.Member) -> List[str]:
        names = [
            getattr(member, "display_name", None),
            getattr(member, "name", None),
            getattr(member, "global_name", None),
            getattr(member, "nick", None),
        ]
        return [self._normalize_user_query(value) for value in names if value]

    def _normalize_user_query(self, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    def _is_default_role(self, role: Optional[discord.Role], guild: Optional[discord.Guild]) -> bool:
        if role is None or guild is None:
            return False
        default_role = getattr(guild, "default_role", None)
        return bool(default_role and getattr(role, "id", None) == getattr(default_role, "id", None))

    def _asset_url(self, asset: Any) -> Optional[str]:
        if not asset:
            return None
        return getattr(asset, "url", None) or str(asset)

    def _format_color(self, color: Any) -> Optional[Dict[str, Any]]:
        if color is None:
            return None
        value = getattr(color, "value", None)
        if value is None:
            try:
                value = int(color)
            except (TypeError, ValueError):
                value = None
        if value is None:
            return None
        return {
            "value": int(value),
            "hex": f"#{int(value):06x}",
        }

    def _duration_seconds(self, duration: Any) -> Optional[int]:
        if duration is None:
            return None
        try:
            return int(duration.total_seconds())
        except Exception:
            try:
                return int(duration)
            except (TypeError, ValueError):
                return None

    def _isoformat(self, value: Any) -> Optional[str]:
        if not value:
            return None
        try:
            if hasattr(value, "tzinfo") and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        except Exception:
            return None

    def _drop_empty(self, data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {}
        for key, value in (data or {}).items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            cleaned[key] = value
        return cleaned

    def _parse_optional_int(self, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            value = value.strip()
            mention_match = re.fullmatch(r"<(?:#|@!?|@&)?(\d+)>", value)
            if mention_match:
                value = mention_match.group(1)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _normalize_channel_query(self, value: str) -> str:
        value = (value or "").strip().casefold()
        if value.startswith("#"):
            value = value[1:]
        return re.sub(r"[\s_]+", "-", value)

    def _coerce_bool(self, value: Any, *, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
            return default
        return bool(value)

    def _sanitize_giphy_query(self, query: str, max_length: int = 50) -> str:
        query = str(query or "").strip()
        if not query:
            return ""

        query = re.sub(r"https?://\S+", " ", query)
        query = re.sub(r"<@!?\d+>|<@&\d+>|<#\d+>", " ", query)
        query = re.sub(r"@(?:everyone|here)\b", " ", query, flags=re.IGNORECASE)
        query = re.sub(r"\s+", " ", query).strip(" .,:;!?\"'")
        if len(query) > max_length:
            query = query[:max_length].rsplit(" ", 1)[0].strip() or query[:max_length].strip()
        return "" if self._has_blocked_giphy_term(query) else query

    def _sanitize_giphy_caption(self, caption: str, max_length: int = 120) -> str:
        caption = str(caption or "").strip()
        if not caption:
            return ""
        caption = caption.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
        caption = caption.replace("```", "'''")
        caption = re.sub(r"\s+", " ", caption)
        if len(caption) > max_length:
            caption = caption[: max_length - 3].rstrip() + "..."
        return caption

    def _has_blocked_giphy_term(self, query: str) -> bool:
        blocked_terms = (
            "porn",
            "nude",
            "nsfw",
            "gore",
            "rape",
            "suicide",
            "self harm",
            "nazi",
            "hitler",
        )
        normalized = f" {str(query or '').lower()} "
        return any(f" {term} " in normalized for term in blocked_terms)

    def _channel_sort_key(self, channel: Any) -> Tuple[int, int, str, int]:
        category = getattr(channel, "category", None)
        category_position = getattr(category, "position", -1) or -1
        position = getattr(channel, "position", 0) or 0
        name = getattr(channel, "name", "")
        channel_id = self._parse_optional_int(getattr(channel, "id", 0)) or 0
        return (category_position, position, str(name).casefold(), int(channel_id))

    def _parse_optional_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _clamp_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _normalize_duckduckgo_url(self, url: str) -> str:
        url = unescape(url or "")
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            if uddg:
                return unescape(uddg)
        return url

    def _parse_bing_results(self, html: str) -> List[Dict[str, str]]:
        results = []
        blocks = re.findall(r'<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>.*?</li>', html or "", flags=re.IGNORECASE | re.DOTALL)
        for block in blocks:
            title_match = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>', block, flags=re.IGNORECASE | re.DOTALL)
            if not title_match:
                continue

            snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.IGNORECASE | re.DOTALL)
            results.append({
                "title": self._strip_html(title_match.group(2)),
                "url": unescape(title_match.group(1)),
                "snippet": self._strip_html(snippet_match.group(1)) if snippet_match else "",
            })
        return results

    def _parse_bing_rss_results(self, xml_text: str) -> List[Dict[str, str]]:
        try:
            root = ET.fromstring(xml_text or "")
        except (ET.ParseError, DefusedXmlException):
            return []

        results = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title") or ""
            url = item.findtext("link") or ""
            snippet = item.findtext("description") or ""
            if not title or not url:
                continue
            results.append({
                "title": self._normalize_whitespace(title),
                "url": self._normalize_whitespace(url),
                "snippet": self._strip_html(snippet),
            })
        return results

    def _strip_html(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text or "")
        return self._normalize_whitespace(text)

    async def _validate_public_url(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return "Only http and https URLs are allowed."
        if not parsed.hostname:
            return "URL must include a hostname."
        if parsed.username or parsed.password:
            return "URLs with embedded credentials are not allowed."

        host = parsed.hostname.strip().lower()
        if host == "localhost" or host.endswith(".local"):
            return "Local or private hostnames are not allowed."

        try:
            literal_ip = ip_address(host)
            if self._is_private_or_local_ip(literal_ip):
                return "Local or private IP addresses are not allowed."
            return None
        except ValueError:
            pass

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror:
            return "Could not resolve hostname."

        for info in infos:
            resolved_host = info[4][0]
            try:
                resolved_ip = ip_address(resolved_host)
            except ValueError:
                continue
            if self._is_private_or_local_ip(resolved_ip):
                return "URL resolves to a local or private network address."

        return None

    def _is_private_or_local_ip(self, value) -> bool:
        return any([
            value.is_private,
            value.is_loopback,
            value.is_link_local,
            value.is_multicast,
            value.is_reserved,
            value.is_unspecified,
        ])

    def _is_supported_web_content_type(self, content_type: str) -> bool:
        lowered = (content_type or "").lower()
        return any(kind in lowered for kind in ["text/html", "text/plain", "application/xhtml+xml", "application/xml", "text/xml"])

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._normalize_whitespace(unescape(re.sub(r"<[^>]+>", " ", match.group(1))))[:250]

    def _html_to_text(self, html: str) -> str:
        html = re.sub(r"(?is)<(script|style|noscript|svg|canvas|template)\b.*?</\1>", " ", html or "")
        parser = _ReadableHTMLParser()
        parser.feed(html)
        return parser.text()

    def _normalize_whitespace(self, text: str) -> str:
        return re.sub(r"\s+", " ", unescape(text or "")).strip()

    def _get(self, obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)


class _ReadableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data: str):
        if data and data.strip():
            self.parts.append(data.strip())

    def handle_starttag(self, tag: str, attrs):
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def text(self) -> str:
        return " ".join(self.parts)


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self._current = None
        self._capture = None

    def parse(self, html: str) -> List[Dict[str, str]]:
        self.feed(html or "")
        return self.results

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attrs_dict.get("href", ""), "snippet": ""}
            self._capture = "title"
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "snippet"

    def handle_data(self, data: str):
        if self._current is not None and self._capture and data.strip():
            self._current[self._capture] += data.strip() + " "

    def handle_endtag(self, tag: str):
        if tag == "a" and self._current is not None and self._capture == "title":
            self._capture = None
            if self._current.get("url"):
                self.results.append(self._current)
        elif self._capture == "snippet" and tag in {"a", "div", "td"}:
            self._capture = None
