import json
import logging
import re
import socket
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, urlparse

import aiohttp
import discord


class DiscordToolManager:
    """Read-only Discord tools exposed to tool-capable LLMs."""

    def __init__(self, bot):
        self.bot = bot

    def tool_definitions(self) -> List[Dict[str, Any]]:
        return [
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
        ]

    async def execute_tool_call(self, tool_call: Any, origin_message: discord.Message) -> Dict[str, Any]:
        name, arguments = self.parse_tool_call(tool_call)
        try:
            if name == "search_messages":
                return await self.search_messages(origin_message, **arguments)
            if name == "fetch_message":
                return await self.fetch_message(origin_message, **arguments)
            if name == "get_recent_messages":
                return await self.get_recent_messages(origin_message, **arguments)
            if name == "web_search":
                return await self.web_search(**arguments)
            if name == "fetch_web_page":
                return await self.fetch_web_page(**arguments)

            return {"ok": False, "error": f"Unknown tool: {name}"}
        except Exception as e:
            logging.exception(f"Discord tool {name} failed")
            return {"ok": False, "tool": name, "error": str(e)}

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
        if not self._can_read_history(channel):
            return {"ok": False, "tool": "fetch_message", "error": "The bot cannot read message history in that channel."}

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
        if not self._can_read_history(channel):
            return {"ok": False, "tool": "get_recent_messages", "error": "The bot cannot read message history in that channel."}

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

    def _resolve_search_channels(
        self,
        origin_message: discord.Message,
        channel_id: Optional[str],
        include_all_readable_channels: bool,
    ) -> List[Any]:
        if include_all_readable_channels:
            return [channel for channel in origin_message.guild.text_channels if self._can_read_history(channel)]

        channel = self._resolve_channel(origin_message.guild, channel_id) if channel_id else origin_message.channel
        if channel and self._can_read_history(channel):
            return [channel]
        return []

    def _resolve_channel(self, guild: discord.Guild, channel_id: Optional[str]) -> Optional[Any]:
        channel_id_int = self._parse_optional_int(channel_id)
        if channel_id_int is None:
            return None

        channel = guild.get_channel(channel_id_int)
        if not channel:
            channel = self.bot.get_channel(channel_id_int)
        if channel and getattr(channel, "guild", None) and channel.guild.id == guild.id:
            return channel
        return None

    def _can_read_history(self, channel: Any) -> bool:
        guild = getattr(channel, "guild", None)
        me = getattr(guild, "me", None)
        if not guild or not me or not hasattr(channel, "permissions_for"):
            return False
        permissions = channel.permissions_for(me)
        return bool(permissions.view_channel and permissions.read_message_history)

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

    def _parse_optional_int(self, value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
        except ET.ParseError:
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
        if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
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
