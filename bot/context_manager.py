# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/context_manager.py
import discord
import logging
from .store import Store
from .llm_provider import LiteLLMProvider
from .config import Config
import asyncio
import base64
import copy
import importlib.util
import mimetypes
import os
import tempfile
import aiohttp
import PyPDF2
import docx
import openpyxl
from PIL import Image
import io
from datetime import datetime, timezone

# Optional imports for media processing
try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    FASTER_WHISPER_AVAILABLE = False
    logging.warning("faster-whisper not available. Local audio transcription will use fallback engines if available.")

openai_whisper = None
OPENAI_WHISPER_AVAILABLE = importlib.util.find_spec("whisper") is not None

try:
    from moviepy import AudioFileClip, VideoFileClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    try:
        from moviepy.editor import AudioFileClip, VideoFileClip
        MOVIEPY_AVAILABLE = True
    except ImportError:
        AudioFileClip = None
        VideoFileClip = None
        MOVIEPY_AVAILABLE = False
        logging.warning("moviepy not available. Audio/video duration and frame extraction will be limited.")

# Optional imports for advanced document processing
try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logging.warning("pytesseract not available. OCR features will be limited.")

try:
    import pptx
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False
    logging.warning("python-pptx not available. PowerPoint processing will be limited.")

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logging.warning("pdfplumber not available. Advanced PDF processing will be limited.")

AUDIO_EXTENSIONS = {
    "aac", "aiff", "alac", "amr", "flac", "m4a", "mp3", "oga", "ogg",
    "opus", "wav", "weba", "wma",
}
VIDEO_EXTENSIONS = {
    "avi", "flv", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "ogv",
    "webm", "wmv",
}
TEXT_EXTENSIONS = {
    "txt", "md", "json", "csv", "py", "js", "html", "css", "xml",
    "yaml", "yml", "toml", "ini", "log", "sql",
}
OFFICE_EXTENSIONS = {"docx", "xlsx", "pptx"}

class ContextManager:
    """
    Manages conversation context for the Discord bot.
    
    This class handles:
    - Building context from message history and reply chains
    - Channel summaries and automatic updates
    - User profile management and AI-generated summaries
    - Media processing integration
    - Settings inheritance (guild -> channel overrides)
    """
    def __init__(self, store: Store, llm_provider: LiteLLMProvider, bot=None):
        self.store = store
        self.llm_provider = llm_provider
        self.config = Config()
        self.bot = bot
        self._stt_lock = asyncio.Lock()
        self._faster_whisper_model = None
        self._faster_whisper_model_key = None
        self._openai_whisper_model = None
        self._openai_whisper_model_name = None

    async def get_guild_and_channel_settings(self, guild_id, channel_id):
        guild_settings = await self.store.get_guild_settings(guild_id)
        channel_overrides = guild_settings.get("channel_overrides", {}).get(str(channel_id), {})
        
        final_settings = guild_settings.copy()
        final_settings.update(channel_overrides)

        media_settings = self._default_media_settings()
        media_settings = self._deep_merge_dict(media_settings, guild_settings.get("media", {}))
        media_settings = self._deep_merge_dict(media_settings, channel_overrides.get("media", {}))
        final_settings["media"] = media_settings
        
        return final_settings

    def _default_media_settings(self) -> dict:
        return {
            "images": {
                "enabled": self.config.DEFAULT_MEDIA_IMAGES_ENABLED,
                "max_size_mb": 10,
                "ocr_enabled": True,
                "description_fallback": True,
                "gif": {
                    "extract_frames": True,
                    "max_frames": self.config.DEFAULT_GIF_MAX_FRAMES,
                    "frame_quality": self.config.DEFAULT_GIF_FRAME_QUALITY,
                },
            },
            "audio": {
                "enabled": self.config.DEFAULT_MEDIA_AUDIO_ENABLED,
                "max_size_mb": 25,
                "max_duration_seconds": 300,
                "transcribe": True,
                "include_timestamps": False,
                "max_transcript_chars": 12000,
                "stt_engine": self.config.LOCAL_STT_ENGINE,
                "stt_model": self.config.LOCAL_STT_MODEL,
                "stt_device": self.config.LOCAL_STT_DEVICE,
                "stt_compute_type": self.config.LOCAL_STT_COMPUTE_TYPE,
                "stt_beam_size": self.config.LOCAL_STT_BEAM_SIZE,
                "stt_vad_filter": self.config.LOCAL_STT_VAD_FILTER,
                "stt_language": self.config.LOCAL_STT_LANGUAGE,
            },
            "video": {
                "enabled": self.config.DEFAULT_MEDIA_VIDEO_ENABLED,
                "max_size_mb": 50,
                "max_duration_seconds": 120,
                "extract_audio": True,
                "extract_frames": True,
                "frame_interval_seconds": 10,
                "max_frames": self.config.DEFAULT_VIDEO_MAX_FRAMES,
                "frame_quality": self.config.DEFAULT_VIDEO_FRAME_QUALITY,
                "ocr_frames_for_text_models": True,
                "max_transcript_chars": 12000,
                "stt_engine": self.config.LOCAL_STT_ENGINE,
                "stt_model": self.config.LOCAL_STT_MODEL,
                "stt_device": self.config.LOCAL_STT_DEVICE,
                "stt_compute_type": self.config.LOCAL_STT_COMPUTE_TYPE,
                "stt_beam_size": self.config.LOCAL_STT_BEAM_SIZE,
                "stt_vad_filter": self.config.LOCAL_STT_VAD_FILTER,
                "stt_language": self.config.LOCAL_STT_LANGUAGE,
            },
            "pdf": {
                "enabled": self.config.DEFAULT_MEDIA_PDF_ENABLED,
                "max_size_mb": 10,
                "preserve_formatting": True,
            },
            "office_documents": {
                "enabled": self.config.DEFAULT_MEDIA_OFFICE_DOCUMENTS_ENABLED,
                "max_size_mb": 10,
                "preserve_structure": True,
            },
            "text_files": {
                "enabled": self.config.DEFAULT_MEDIA_TEXT_FILES_ENABLED,
                "max_size_mb": 5,
                "supported_extensions": sorted(TEXT_EXTENSIONS),
            },
            "other_files": {
                "enabled": self.config.DEFAULT_MEDIA_OTHER_FILES_ENABLED,
                "max_size_mb": 20,
                "include_metadata_only": True,
            },
        }

    def _deep_merge_dict(self, base: dict, override: dict) -> dict:
        result = copy.deepcopy(base) if isinstance(base, dict) else {}
        if not isinstance(override, dict):
            return result

        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge_dict(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    async def build_context(self, message: discord.Message = None, channel: discord.TextChannel = None, model_name: str = None, 
                           prompt: str = None, behavior_override: str = None, capabilities_override: str = None,
                           include_bot_identity: bool = True, include_channel_summary: bool = True, 
                           include_user_profiles: bool = True, include_conversation_history: bool = True,
                           include_reply_chain: bool = True, include_current_message: bool = True):
        """
        Build context for a specific model. If model_name is not provided, uses MAIN_LLM_MODEL.
        
        Args:
            message: Optional. The message to build context for. If not provided, uses the latest message from the channel.
            channel: Optional. The channel to build context for. Required if message is not provided.
            model_name: Optional. The model to build context for. Defaults to MAIN_LLM_MODEL.
            prompt: Optional. Custom prompt to use instead of the current message content. Useful for cogs.
            behavior_override: Optional. Custom behavior prompt to override the default/settings behavior prompt.
            capabilities_override: Optional. Custom capabilities prompt to override the default capabilities prompt.
            include_bot_identity: Optional. Include bot name and ID in system messages. Default True.
            include_channel_summary: Optional. Include channel summary in context. Default True.
            include_user_profiles: Optional. Include user profiles (manual notes + AI summaries). Default True.
            include_conversation_history: Optional. Include recent message history. Default True.
            include_reply_chain: Optional. Include reply chain context (requires include_conversation_history). Default True.
            include_current_message: Optional. Include the current message in history (ignored if prompt is provided). Default True.
        
        According to the spec, both decision and main models should receive identical media processing
        but processed according to each model's individual capabilities.
        """
        # Handle the case where no message is provided
        if message is None:
            if channel is None:
                raise ValueError("Either message or channel must be provided")
            
            # Fetch the latest message from the channel
            try:
                async for latest_msg in channel.history(limit=1):
                    message = latest_msg
                    break
                else:
                    # No messages in channel
                    raise ValueError(f"No messages found in channel {channel.name}")
            except Exception as e:
                raise ValueError(f"Could not fetch latest message from channel: {e}")
        
        # If channel wasn't provided but we have a message, get channel from message
        if channel is None:
            channel = message.channel
            
        guild_id = str(channel.guild.id)
        channel_id = str(channel.id)
        user_id = str(message.author.id)

        settings = await self.get_guild_and_channel_settings(guild_id, channel_id)
        data = await self.store.get_guild_data(guild_id)

        # Use specified model or default to main model
        target_model = model_name or self.config.MAIN_LLM_MODEL

        # 1. System Prompts with optional overrides
        capabilities_prompt = capabilities_override or self.config.CAPABILITIES_PROMPT
        behavior_prompt = behavior_override or settings.get("behavior_prompt", self.config.BEHAVIOR_PROMPT)
        system_prompt = f"{capabilities_prompt}\n\n{behavior_prompt}"
        
        messages = [{"role": "system", "content": system_prompt}]

        # Add bot identity info as a system message
        if include_bot_identity and self.bot and self.bot.user:
            bot_identity = f"Your Bot name: {self.bot.user.name}\nYour Bot user ID: {self.bot.user.id}"
            messages.append({"role": "system", "content": bot_identity})

        # 2. Channel Summary
        if include_channel_summary:
            mentioned_channel_ids = self._message_channel_mention_ids(message)
            channel_data = data.get("channels", {}).get(channel_id, {})
            current_summary = (channel_data.get("summary") or "").strip()
            if current_summary:
                messages.append({
                    "role": "system",
                    "content": self._format_channel_summary_context(
                        channel_name=getattr(channel, "name", channel_id),
                        channel_id=channel_id,
                        summary=current_summary,
                        channel_data=channel_data,
                        preloaded_explicit=channel_id in mentioned_channel_ids,
                        current_channel=True,
                    ),
                })
            messages.extend(
                self._mentioned_channel_summary_messages(
                    message=message,
                    data=data,
                    current_channel_id=channel_id,
                    settings=settings,
                )
            )

        # 3. User Profiles
        if include_user_profiles:
            user_data = data.get("users", {}).get(user_id, {})
            user_profile_content = []
            if "manual_note" in user_data:
                user_profile_content.append(
                    f"Manual note about {message.author.display_name} (User ID: {user_id}): {user_data['manual_note']}"
                )
            if "ai_summary" in user_data:
                user_profile_content.append(
                    f"AI summary of {message.author.display_name} (User ID: {user_id}): {user_data['ai_summary']}"
                )
            
            if user_profile_content:
                messages.append({"role": "system", "content": "\n".join(user_profile_content)})

        # 4. Conversation History (Reply Chain + Recent Messages)
        if include_conversation_history:
            history_limit = settings.get("context", {}).get("history_messages", 15)
            reply_chain_limit = settings.get("context", {}).get("reply_chain_limit", 5)

            # Fetch reply chain
            reply_chain = []
            if include_reply_chain:
                current_message = message
                for _ in range(reply_chain_limit):
                    if current_message.reference and current_message.reference.message_id:
                        try:
                            ref_message = await message.channel.fetch_message(current_message.reference.message_id)
                            reply_chain.insert(0, ref_message)
                            current_message = ref_message
                        except discord.NotFound:
                            break
                    else:
                        break
            
            # Fetch recent messages
            recent_messages = [msg async for msg in message.channel.history(limit=history_limit)]
            recent_messages.reverse() # Oldest to newest

            # Combine and deduplicate
            all_messages = {msg.id: msg for msg in reply_chain}
            all_messages.update({msg.id: msg for msg in recent_messages})
            
            # Always exclude the current message from history to prevent duplication
            # The current message will be added separately at the end
            if message.id in all_messages:
                del all_messages[message.id]

            sorted_messages = sorted(all_messages.values(), key=lambda m: m.created_at)

            if sorted_messages:
                context_lines = [
                    "Recent Discord conversation context, oldest to newest.",
                    "These lines are background only. Do not answer them one by one; answer only the current message that follows.",
                ]
                context_lines.extend(self._format_message_context_line(msg) for msg in sorted_messages)
                messages.append({"role": "system", "content": "\n".join(context_lines)})

        # 5. Current Message or Custom Prompt
        if include_current_message or prompt:
            messages.append({
                "role": "system",
                "content": (
                    "Reply only to the next/current Discord message or task. "
                    "Do not prefix the reply with your bot name, username, role label, or user ID."
                )
            })

        if prompt:
            # Use custom prompt instead of message content
            messages.append({"role": "user", "content": prompt})
        else:
            # Add the current message only if include_current_message is True
            if include_current_message:
                current_message_content = await self._format_message_content(
                    message,
                    target_model,
                    current_message=True
                )
                messages.append({"role": "user", "content": current_message_content})

        return messages, settings

    def _message_channel_mention_ids(self, message: discord.Message) -> set:
        return {
            str(getattr(channel, "id", ""))
            for channel in (getattr(message, "channel_mentions", []) or [])
            if getattr(channel, "id", None)
        }

    def _mentioned_channel_summary_messages(
        self,
        *,
        message: discord.Message,
        data: dict,
        current_channel_id: str,
        settings: dict,
    ) -> list:
        """Return stored summaries for channels explicitly mentioned in the current Discord message."""
        mentioned_channels = getattr(message, "channel_mentions", []) or []
        if not mentioned_channels:
            return []

        context_settings = settings.get("context", {}) if isinstance(settings, dict) else {}
        if not isinstance(context_settings, dict):
            context_settings = {}
        max_channels = self._safe_int(context_settings.get("mentioned_channel_summary_limit"), default=5, minimum=1, maximum=10)
        max_chars = self._safe_int(context_settings.get("mentioned_channel_summary_max_chars"), default=2500, minimum=500, maximum=8000)
        summaries = []
        seen_channel_ids = {str(current_channel_id)}
        channel_data_by_id = data.get("channels", {})

        for mentioned_channel in mentioned_channels:
            if len(summaries) >= max_channels:
                break

            mentioned_channel_id = str(getattr(mentioned_channel, "id", ""))
            if not mentioned_channel_id or mentioned_channel_id in seen_channel_ids:
                continue
            seen_channel_ids.add(mentioned_channel_id)

            if not self._can_include_mentioned_channel_summary(mentioned_channel, message.guild):
                continue

            channel_data = channel_data_by_id.get(mentioned_channel_id, {})
            summary = (channel_data.get("summary") or "").strip()
            if not summary:
                continue

            channel_name = getattr(mentioned_channel, "name", mentioned_channel_id)
            summary = self._truncate_context_text(summary, max_chars)
            summaries.append({
                "role": "system",
                "content": self._format_channel_summary_context(
                    channel_name=channel_name,
                    channel_id=mentioned_channel_id,
                    summary=summary,
                    channel_data=channel_data,
                    preloaded_explicit=True,
                    current_channel=False,
                ),
            })

        return summaries

    def _format_channel_summary_context(
        self,
        *,
        channel_name: str,
        channel_id: str,
        summary: str,
        channel_data: dict,
        preloaded_explicit: bool,
        current_channel: bool,
    ) -> str:
        if preloaded_explicit:
            label = "Preloaded Explicit Channel Summary"
        elif current_channel:
            label = "Current Channel Summary"
        else:
            label = "Channel Summary"

        metadata_parts = []
        if channel_data.get("last_summary_time"):
            metadata_parts.append(f"last_summary_time={channel_data['last_summary_time']}")
        if channel_data.get("messages_since_summary") is not None:
            metadata_parts.append(f"messages_since_summary={channel_data['messages_since_summary']}")
        if channel_data.get("summary_type"):
            metadata_parts.append(f"summary_type={channel_data['summary_type']}")

        metadata = f"\nSummary metadata: {', '.join(metadata_parts)}" if metadata_parts else ""
        instruction = ""
        if preloaded_explicit:
            instruction = (
                "\nThis summary was preloaded because the current message explicitly mentions this channel. "
                "Use it directly for broad recaps; do not call get_channel_summary for this channel unless the user asks for a fresh tool check."
            )

        return f"{label} for #{channel_name} (Channel ID: {channel_id}):\n{summary}{metadata}{instruction}"

    def _can_include_mentioned_channel_summary(self, channel: discord.abc.GuildChannel, current_guild: discord.Guild) -> bool:
        if not channel or not current_guild:
            return False

        guild = getattr(channel, "guild", None)
        if not guild or guild.id != current_guild.id:
            return False

        me = getattr(guild, "me", None)
        if not me and self.bot and self.bot.user and hasattr(guild, "get_member"):
            me = guild.get_member(self.bot.user.id)
        if not me or not hasattr(channel, "permissions_for"):
            return False

        try:
            permissions = channel.permissions_for(me)
        except Exception:
            return False

        return bool(
            getattr(permissions, "view_channel", False)
            and getattr(permissions, "read_message_history", False)
        )

    def _safe_int(self, value, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _safe_float(self, value, *, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    def _safe_bool(self, value, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        value = str(value).strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off"}:
            return False
        return default

    def _truncate_context_text(self, text: str, max_chars: int) -> str:
        text = str(text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _format_message_context_line(self, message: discord.Message) -> str:
        """
        Format a Discord message as plain background context.

        History is intentionally kept out of user/assistant turns so the model
        does not treat older messages as prompts waiting for answers.
        """
        author_name = getattr(message.author, "display_name", getattr(message.author, "name", "Unknown User"))
        author_id = getattr(message.author, "id", "unknown")
        author_marker = " [bot]" if getattr(message.author, "bot", False) else ""
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = (message.content or "[empty message]").replace("\r", " ").replace("\n", " ").strip()

        extra_parts = []
        if message.attachments:
            filenames = ", ".join(att.filename for att in message.attachments)
            extra_parts.append(f"attachments: {filenames}")
        if message.embeds:
            extra_parts.append(f"embeds: {len(message.embeds)}")
        if message.reference and message.reference.message_id:
            extra_parts.append(f"reply_to_message_id: {message.reference.message_id}")

        extra = f" ({'; '.join(extra_parts)})" if extra_parts else ""
        return f"[{timestamp}] {author_name}{author_marker} (User ID: {author_id}): {content}{extra}"

    async def _format_message_content(self, message: discord.Message, model_name: str = None, *, current_message: bool = False):
        """
        Format message content with attachments processed for the specified model.
        If model_name is not provided, uses MAIN_LLM_MODEL.
        """
        content_parts = []
        
        # Add user information for identification
        user_info = f"{message.author.display_name} (User ID: {message.author.id})"
        prefix = "Current Discord message to answer from " if current_message else "Discord message from "
        if message.content:
            content_parts.append({"type": "text", "text": f"{prefix}{user_info}:\n{message.content}"})
        else:
            content_parts.append({"type": "text", "text": f"{prefix}{user_info}:\n[empty message]"})


        if message.attachments:
            # Use specified model or default to main model for attachment processing
            target_model = model_name or self.config.MAIN_LLM_MODEL
            media_settings = (await self.get_guild_and_channel_settings(message.guild.id, message.channel.id)).get("media", {})
            
            for attachment in message.attachments:
                processed_content = await self._process_attachment(attachment, target_model, media_settings)
                if isinstance(processed_content, dict) and processed_content.get("type") == "animated_gif":
                    # Handle animated GIF: convert to list of content parts for message processing
                    frame_info = f"Animated GIF: {processed_content['filename']}"
                    total_frames = processed_content.get("total_frames", "unknown")
                    extracted_frames = processed_content.get("extracted_frames", 0)
                    if total_frames > extracted_frames:
                        frame_info += f" (showing {extracted_frames} representative frames from {total_frames} total frames)"
                    else:
                        frame_info += f" ({extracted_frames} frames)"
                    
                    content_parts.append({"type": "text", "text": frame_info})
                    for frame_data in processed_content["frames"]:
                        content_parts.append({
                            "type": "image_url", 
                            "image_url": {"url": frame_data}
                        })
                elif isinstance(processed_content, list):
                    # This is a list of content parts (legacy or other processing)
                    content_parts.extend(processed_content)
                elif isinstance(processed_content, dict):
                    # This is a single structured content (like image_url)
                    content_parts.append(processed_content)
                else:
                    # This is a text fallback
                    content_parts.append({"type": "text", "text": processed_content})

        # Process embeds (important for Tenor GIFs and other embedded media)
        if message.embeds:
            target_model = model_name or self.config.MAIN_LLM_MODEL
            media_settings = (await self.get_guild_and_channel_settings(message.guild.id, message.channel.id)).get("media", {})
            
            for embed in message.embeds:
                processed_embed = await self._process_embed(embed, target_model, media_settings)
                if isinstance(processed_embed, list):
                    content_parts.extend(processed_embed)
                elif isinstance(processed_embed, dict):
                    content_parts.append(processed_embed)
                elif processed_embed:  # Only add non-empty text
                    content_parts.append({"type": "text", "text": processed_embed})
        
        # If we only have text content, return it as a string for simplicity
        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            return content_parts[0]["text"]
        elif content_parts:
            return content_parts
        else:
            return "[empty message]"

    def _is_audio_file(self, mime_type: str, file_ext: str) -> bool:
        return bool((mime_type and mime_type.startswith("audio/")) or file_ext in AUDIO_EXTENSIONS)

    def _is_video_file(self, mime_type: str, file_ext: str) -> bool:
        return bool((mime_type and mime_type.startswith("video/")) or file_ext in VIDEO_EXTENSIONS)

    def _image_content_part(self, url: str, file_bytes: bytes = None, mime_type: str = None, model_name: str = None) -> dict:
        image_url = url
        if file_bytes and self.llm_provider.prefers_inline_image_data(model_name):
            image_mime = mime_type if mime_type and mime_type.startswith("image/") else "image/jpeg"
            encoded_image = base64.b64encode(file_bytes).decode()
            image_url = f"data:{image_mime};base64,{encoded_image}"

        return {"type": "image_url", "image_url": {"url": image_url}}

    async def _write_temp_file(self, file_bytes: bytes, file_ext: str) -> str:
        suffix = f".{file_ext.lstrip('.')}" if file_ext else ""
        return await asyncio.to_thread(self._write_temp_file_sync, file_bytes, suffix)

    def _write_temp_file_sync(self, file_bytes: bytes, suffix: str) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(file_bytes)
            return temp_file.name

    def _remove_temp_file(self, path: str):
        if not path:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logging.warning(f"Could not remove temporary media file {path}: {e}")

    async def _get_media_duration(self, file_path: str, media_kind: str):
        if not MOVIEPY_AVAILABLE:
            return None
        return await asyncio.to_thread(self._get_media_duration_sync, file_path, media_kind)

    def _get_media_duration_sync(self, file_path: str, media_kind: str):
        clip = None
        try:
            clip_class = VideoFileClip if media_kind == "video" else AudioFileClip
            clip = clip_class(file_path)
            duration = getattr(clip, "duration", None)
            return float(duration) if duration is not None else None
        except Exception as e:
            logging.warning(f"Could not read {media_kind} duration from {file_path}: {e}")
            return None
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass

    async def _transcribe_media_file(self, file_path: str, media_config: dict) -> dict:
        async with self._stt_lock:
            return await asyncio.to_thread(self._transcribe_media_file_sync, file_path, media_config)

    def _transcribe_media_file_sync(self, file_path: str, media_config: dict) -> dict:
        engine = str(media_config.get("stt_engine") or self.config.LOCAL_STT_ENGINE).strip().lower().replace("_", "-")

        if engine in {"faster", "faster-whisper"}:
            if FASTER_WHISPER_AVAILABLE:
                return self._transcribe_with_faster_whisper(file_path, media_config)
            if OPENAI_WHISPER_AVAILABLE:
                logging.warning("faster-whisper is unavailable; falling back to openai-whisper local transcription.")
                return self._transcribe_with_openai_whisper(file_path, media_config)
            raise RuntimeError("No local speech-to-text engine is installed. Install faster-whisper.")

        if engine in {"openai-whisper", "whisper"}:
            if OPENAI_WHISPER_AVAILABLE:
                return self._transcribe_with_openai_whisper(file_path, media_config)
            if FASTER_WHISPER_AVAILABLE:
                logging.warning("openai-whisper is unavailable; falling back to faster-whisper local transcription.")
                return self._transcribe_with_faster_whisper(file_path, media_config)
            raise RuntimeError("No local speech-to-text engine is installed. Install faster-whisper.")

        raise RuntimeError(f"Unsupported local speech-to-text engine: {engine}")

    def _transcribe_with_faster_whisper(self, file_path: str, media_config: dict) -> dict:
        model_name = str(media_config.get("stt_model") or self.config.LOCAL_STT_MODEL)
        device = str(media_config.get("stt_device") or self.config.LOCAL_STT_DEVICE)
        compute_type = str(media_config.get("stt_compute_type") or self.config.LOCAL_STT_COMPUTE_TYPE)
        beam_size = self._safe_int(
            media_config.get("stt_beam_size", self.config.LOCAL_STT_BEAM_SIZE),
            default=self.config.LOCAL_STT_BEAM_SIZE,
            minimum=1,
            maximum=10,
        )
        vad_filter = self._safe_bool(
            media_config.get("stt_vad_filter", self.config.LOCAL_STT_VAD_FILTER),
            default=self.config.LOCAL_STT_VAD_FILTER,
        )
        language = media_config.get("stt_language", self.config.LOCAL_STT_LANGUAGE)
        language = str(language).strip() if language else None

        model = self._get_faster_whisper_model(model_name, device, compute_type)
        kwargs = {
            "beam_size": beam_size,
            "vad_filter": vad_filter,
        }
        if language:
            kwargs["language"] = language

        segments, info = model.transcribe(file_path, **kwargs)
        segment_list = [
            {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
            for segment in segments
        ]
        text = " ".join(segment["text"] for segment in segment_list if segment["text"]).strip()
        return {
            "engine": "faster-whisper",
            "model": model_name,
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "segments": segment_list,
            "text": text,
        }

    def _get_faster_whisper_model(self, model_name: str, device: str, compute_type: str):
        key = (model_name, device, compute_type)
        if self._faster_whisper_model is None or self._faster_whisper_model_key != key:
            logging.info(f"Loading faster-whisper model '{model_name}' on {device} with compute_type={compute_type}.")
            self._faster_whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
            self._faster_whisper_model_key = key
        return self._faster_whisper_model

    def _transcribe_with_openai_whisper(self, file_path: str, media_config: dict) -> dict:
        model_name = str(media_config.get("stt_model") or self.config.LOCAL_STT_MODEL)
        beam_size = self._safe_int(
            media_config.get("stt_beam_size", self.config.LOCAL_STT_BEAM_SIZE),
            default=self.config.LOCAL_STT_BEAM_SIZE,
            minimum=1,
            maximum=10,
        )
        language = media_config.get("stt_language", self.config.LOCAL_STT_LANGUAGE)
        language = str(language).strip() if language else None

        model = self._get_openai_whisper_model(model_name)
        kwargs = {"fp16": False, "beam_size": beam_size}
        if language:
            kwargs["language"] = language
        result = model.transcribe(file_path, **kwargs)
        segment_list = [
            {
                "start": segment.get("start", 0),
                "end": segment.get("end", 0),
                "text": str(segment.get("text", "")).strip(),
            }
            for segment in result.get("segments", [])
        ]
        text = str(result.get("text", "")).strip()
        if not text:
            text = " ".join(segment["text"] for segment in segment_list if segment["text"]).strip()
        return {
            "engine": "openai-whisper-local",
            "model": model_name,
            "language": result.get("language"),
            "language_probability": None,
            "segments": segment_list,
            "text": text,
        }

    def _get_openai_whisper_model(self, model_name: str):
        global openai_whisper
        if openai_whisper is None:
            import whisper as openai_whisper_module
            openai_whisper = openai_whisper_module

        if self._openai_whisper_model is None or self._openai_whisper_model_name != model_name:
            logging.info(f"Loading openai-whisper fallback model '{model_name}'.")
            self._openai_whisper_model = openai_whisper.load_model(model_name)
            self._openai_whisper_model_name = model_name
        return self._openai_whisper_model

    def _format_transcript_block(self, title: str, filename: str, transcript: dict, duration, media_config: dict) -> str:
        include_timestamps = self._safe_bool(media_config.get("include_timestamps"), default=False)
        max_chars = self._safe_int(
            media_config.get("max_transcript_chars", 12000),
            default=12000,
            minimum=500,
            maximum=50000,
        )

        metadata = []
        if duration is not None:
            metadata.append(f"Duration: {self._format_seconds(duration)}")
        if transcript.get("engine"):
            metadata.append(f"Engine: {transcript['engine']}")
        if transcript.get("model"):
            metadata.append(f"STT Model: {transcript['model']}")
        if transcript.get("language"):
            language = transcript["language"]
            probability = transcript.get("language_probability")
            if isinstance(probability, (int, float)):
                metadata.append(f"Detected Language: {language} ({probability:.2f})")
            else:
                metadata.append(f"Detected Language: {language}")

        if include_timestamps and transcript.get("segments"):
            body = "\n".join(
                f"[{self._format_seconds(segment['start'])} -> {self._format_seconds(segment['end'])}] {segment['text']}"
                for segment in transcript["segments"]
                if segment.get("text")
            )
        else:
            body = transcript.get("text", "").strip()

        if not body:
            body = "[No speech detected.]"

        body = self._truncate_context_text(body, max_chars)
        header = f"--- {title}: {filename} ---"
        if metadata:
            return f"{header}\n" + "\n".join(metadata) + f"\n\n{body}"
        return f"{header}\n{body}"

    def _format_seconds(self, seconds) -> str:
        try:
            seconds = max(0.0, float(seconds))
        except (TypeError, ValueError):
            return "unknown"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    async def _extract_video_frames(self, file_path: str, video_config: dict) -> dict:
        if not MOVIEPY_AVAILABLE:
            raise RuntimeError("moviepy is not installed")
        return await asyncio.to_thread(self._extract_video_frames_sync, file_path, video_config)

    def _extract_video_frames_sync(self, file_path: str, video_config: dict) -> dict:
        clip = None
        try:
            clip = VideoFileClip(file_path)
            duration = float(getattr(clip, "duration", 0) or 0)
            max_frames = self._safe_int(video_config.get("max_frames"), default=self.config.DEFAULT_VIDEO_MAX_FRAMES, minimum=1, maximum=30)
            frame_interval = self._safe_float(video_config.get("frame_interval_seconds"), default=10.0, minimum=0.1, maximum=3600.0)
            frame_quality = self._safe_int(video_config.get("frame_quality"), default=self.config.DEFAULT_VIDEO_FRAME_QUALITY, minimum=1, maximum=100)
            frame_times = self._sample_video_times(duration, max_frames, frame_interval)

            frames = []
            for frame_time in frame_times:
                try:
                    frame_array = clip.get_frame(frame_time)
                    frame = Image.fromarray(frame_array).convert("RGB")
                    buffer = io.BytesIO()
                    frame.save(buffer, format="JPEG", quality=frame_quality)
                    frame_bytes = buffer.getvalue()
                    frame_b64 = base64.b64encode(frame_bytes).decode()
                    frames.append({
                        "timestamp": frame_time,
                        "data_url": f"data:image/jpeg;base64,{frame_b64}",
                        "jpeg_bytes": frame_bytes,
                    })
                except Exception as e:
                    logging.warning(f"Could not extract video frame at {frame_time:.2f}s from {file_path}: {e}")

            return {"duration": duration, "frames": frames}
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass

    def _sample_video_times(self, duration: float, max_frames: int, frame_interval: float) -> list:
        if duration <= 0:
            return [0.0]

        safe_end = max(0.0, duration - 0.1)
        interval_times = []
        current = 0.0
        while current <= safe_end:
            interval_times.append(round(current, 3))
            current += frame_interval

        if interval_times and len(interval_times) <= max_frames:
            return interval_times

        if max_frames == 1:
            return [round(min(safe_end, duration / 2), 3)]

        step = safe_end / (max_frames - 1)
        return [round(min(safe_end, i * step), 3) for i in range(max_frames)]

    async def _ocr_video_frames(self, frames: list) -> str:
        if not TESSERACT_AVAILABLE:
            return ""
        return await asyncio.to_thread(self._ocr_video_frames_sync, frames)

    def _ocr_video_frames_sync(self, frames: list) -> str:
        lines = []
        for frame in frames:
            try:
                with Image.open(io.BytesIO(frame["jpeg_bytes"])) as img:
                    text = pytesseract.image_to_string(img).strip()
                if text:
                    lines.append(f"[{self._format_seconds(frame['timestamp'])}] {text}")
            except Exception as e:
                logging.warning(f"Could not OCR video frame at {frame.get('timestamp', 0)}s: {e}")
        return "\n".join(lines)

    async def _process_audio_attachment(
        self,
        attachment: discord.Attachment,
        file_bytes: bytes,
        file_ext: str,
        file_size_mb: float,
        model_name: str,
        audio_config: dict,
    ):
        if not self._safe_bool(audio_config.get("enabled"), default=True):
            return f"[Audio processing disabled: {attachment.filename}]"

        max_size = self._safe_float(audio_config.get("max_size_mb"), default=25.0, minimum=0.1, maximum=500.0)
        if file_size_mb > max_size:
            return f"[Audio too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"

        if self.llm_provider.supports_audio(model_name) and self._safe_bool(audio_config.get("send_direct_if_supported"), default=False):
            return {"type": "audio_url", "audio_url": {"url": attachment.url}}

        if not self._safe_bool(audio_config.get("transcribe"), default=True):
            return f"--- Audio Metadata: {attachment.filename} ---\nFile Size: {file_size_mb:.2f} MB\nTranscription disabled."

        temp_path = await self._write_temp_file(file_bytes, file_ext)
        try:
            duration = await self._get_media_duration(temp_path, "audio")
            max_duration = self._safe_float(audio_config.get("max_duration_seconds"), default=300.0, minimum=0.0, maximum=86400.0)
            if duration is not None and max_duration > 0 and duration > max_duration:
                return (
                    f"[Audio too long for processing: {attachment.filename} - "
                    f"{self._format_seconds(duration)} > {self._format_seconds(max_duration)}]"
                )

            transcript = await self._transcribe_media_file(temp_path, audio_config)
            return self._format_transcript_block("Audio Transcript", attachment.filename, transcript, duration, audio_config)
        except Exception as e:
            return f"[Audio transcription failed: {attachment.filename} - {str(e)[:80]}]"
        finally:
            self._remove_temp_file(temp_path)

    async def _process_video_attachment(
        self,
        attachment: discord.Attachment,
        file_bytes: bytes,
        file_ext: str,
        file_size_mb: float,
        model_name: str,
        video_config: dict,
    ):
        if not self._safe_bool(video_config.get("enabled"), default=True):
            return f"[Video processing disabled: {attachment.filename}]"

        max_size = self._safe_float(video_config.get("max_size_mb"), default=50.0, minimum=0.1, maximum=1000.0)
        if file_size_mb > max_size:
            return f"[Video too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"

        temp_path = await self._write_temp_file(file_bytes, file_ext)
        try:
            duration = await self._get_media_duration(temp_path, "video")
            max_duration = self._safe_float(video_config.get("max_duration_seconds"), default=120.0, minimum=0.0, maximum=86400.0)
            if duration is not None and max_duration > 0 and duration > max_duration:
                return (
                    f"[Video too long for processing: {attachment.filename} - "
                    f"{self._format_seconds(duration)} > {self._format_seconds(max_duration)}]"
                )

            content_parts = []
            extract_audio = self._safe_bool(video_config.get("extract_audio"), default=True)
            extract_frames = self._safe_bool(video_config.get("extract_frames"), default=True)

            if extract_audio:
                try:
                    transcript = await self._transcribe_media_file(temp_path, video_config)
                    content_parts.append({
                        "type": "text",
                        "text": self._format_transcript_block("Video Audio Transcript", attachment.filename, transcript, duration, video_config),
                    })
                except Exception as e:
                    content_parts.append({
                        "type": "text",
                        "text": f"[Video audio transcription failed: {attachment.filename} - {str(e)[:80]}]",
                    })

            if extract_frames:
                try:
                    frame_result = await self._extract_video_frames(temp_path, video_config)
                    frames = frame_result.get("frames", [])
                    timestamps = ", ".join(self._format_seconds(frame["timestamp"]) for frame in frames)
                    frame_summary = (
                        f"--- Video Frame Samples: {attachment.filename} ---\n"
                        f"Duration: {self._format_seconds(duration if duration is not None else frame_result.get('duration'))}\n"
                        f"Extracted {len(frames)} representative frame(s)"
                    )
                    if timestamps:
                        frame_summary += f" at: {timestamps}"

                    if frames and self.llm_provider.supports_vision(model_name):
                        content_parts.append({"type": "text", "text": frame_summary})
                        for frame in frames:
                            content_parts.append({"type": "image_url", "image_url": {"url": frame["data_url"]}})
                    elif frames:
                        ocr_text = ""
                        if self._safe_bool(video_config.get("ocr_frames_for_text_models"), default=True):
                            ocr_text = await self._ocr_video_frames(frames)

                        if ocr_text:
                            content_parts.append({
                                "type": "text",
                                "text": f"--- Video Frame OCR: {attachment.filename} ---\n{ocr_text}",
                            })
                        else:
                            content_parts.append({
                                "type": "text",
                                "text": frame_summary + "\n[Frames were extracted locally but not attached because the target model does not support vision.]",
                            })
                    else:
                        content_parts.append({"type": "text", "text": f"[No video frames extracted: {attachment.filename}]"})
                except Exception as e:
                    content_parts.append({"type": "text", "text": f"[Video frame extraction failed: {attachment.filename} - {str(e)[:80]}]"})

            if not content_parts:
                duration_text = self._format_seconds(duration) if duration is not None else "unknown"
                return f"--- Video Metadata: {attachment.filename} ---\nDuration: {duration_text}\nFile Size: {file_size_mb:.2f} MB"

            return content_parts
        finally:
            self._remove_temp_file(temp_path)

    async def _process_attachment(self, attachment: discord.Attachment, model_name: str, media_settings: dict):
        mime_type = mimetypes.guess_type(attachment.filename)[0]
        file_ext = attachment.filename.lower().split('.')[-1] if '.' in attachment.filename else ''
        
        try:
            # Check file size limits first
            file_size_mb = getattr(attachment, "size", 0) / (1024 * 1024)
            attachment_url = getattr(attachment, "url", "")
            actual_content_type = ""
            
            # Enhanced detection for GIFs, especially Tenor GIFs
            is_likely_gif = (
                mime_type == "image/gif" or 
                file_ext == "gif" or 
                'gif' in attachment_url.lower() or
                'tenor.com' in attachment_url.lower() or
                'giphy.com' in attachment_url.lower() or
                '?format=gif' in attachment_url.lower()
            )
            
            # Configure headers for better compatibility with GIF services
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'image/gif,image/webp,image/apng,image/*,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_url, headers=headers, allow_redirects=True, timeout=30) as resp:
                    if resp.status != 200:
                        logging.warning(f"HTTP {resp.status} when fetching {attachment_url}")
                        return f"[Could not fetch attachment: {attachment.filename} (HTTP {resp.status})]"
                    
                    # Check actual content type from response headers
                    actual_content_type = resp.headers.get('content-type', '').lower()
                    actual_mime_type = actual_content_type.split(';', 1)[0].strip()
                    content_length = resp.headers.get('content-length')
                    
                    logging.debug(f"Fetched {attachment_url}: Content-Type={actual_content_type}, Content-Length={content_length}")
                    
                    file_bytes = await resp.read()
                    
                    # Update file size based on actual downloaded content
                    if len(file_bytes) > 0:
                        file_size_mb = len(file_bytes) / (1024 * 1024)
                    
                    # Update MIME type based on actual response if it's more specific
                    if actual_mime_type and actual_mime_type != "application/octet-stream":
                        mime_type = actual_mime_type
                    
                    # Enhanced GIF detection with actual content type
                    if not is_likely_gif and actual_mime_type == 'image/gif':
                        is_likely_gif = True

            # Image processing (including GIFs)
            if (mime_type and mime_type.startswith("image/")) or is_likely_gif:
                images_config = media_settings.get("images", {})
                if not images_config.get("enabled", True):
                    return f"[Image processing disabled: {attachment.filename}]"
                
                max_size = images_config.get("max_size_mb", 10)
                if file_size_mb > max_size:
                    return f"[Image too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                if self.llm_provider.supports_vision(model_name):
                    # Check if this is an animated GIF using enhanced detection
                    if is_likely_gif:
                        logging.info(f"Processing potential GIF: {attachment.filename}, URL: {attachment.url}, Content-Type: {actual_content_type}, MIME: {mime_type}")
                        # Handle animated GIF by extracting frames
                        gif_config = images_config.get("gif", {})
                        if gif_config.get("extract_frames", True):
                            try:
                                import base64
                                
                                # Validate that we have image data before trying to process it
                                if len(file_bytes) == 0:
                                    logging.warning(f"No data received for {attachment.filename}")
                                    return self._image_content_part(attachment.url, model_name=model_name)
                                
                                gif = Image.open(io.BytesIO(file_bytes))
                                
                                # Check if it's actually animated
                                is_animated = getattr(gif, 'is_animated', False)
                                logging.info(f"GIF analysis for {attachment.filename}: is_animated={is_animated}, format={gif.format}")
                                if is_animated:
                                    # Extract frames from animated GIF with equal distribution across entire length
                                    max_frames = gif_config.get("max_frames", self.config.DEFAULT_GIF_MAX_FRAMES)
                                    frame_quality = gif_config.get("frame_quality", self.config.DEFAULT_GIF_FRAME_QUALITY)
                                    
                                    frames = []
                                    total_frames = getattr(gif, 'n_frames', 1)
                                    
                                    # Calculate frame indices for equal distribution across the entire GIF
                                    if total_frames <= max_frames:
                                        # If GIF has fewer frames than max, take all frames
                                        frame_indices = list(range(total_frames))
                                    else:
                                        # Distribute frames evenly across the entire GIF length
                                        # This ensures we cover the beginning, middle, and end of the animation
                                        if max_frames == 1:
                                            frame_indices = [0]
                                        elif max_frames == 2:
                                            frame_indices = [0, total_frames - 1]
                                        else:
                                            # For 3+ frames, distribute evenly including first and last frames
                                            step = (total_frames - 1) / (max_frames - 1)
                                            frame_indices = [round(i * step) for i in range(max_frames)]
                                            # Ensure the last frame is exactly the last frame
                                            frame_indices[-1] = total_frames - 1
                                            # Remove duplicates while preserving order
                                            seen = set()
                                            frame_indices = [x for x in frame_indices if not (x in seen or seen.add(x))]
                                    
                                    logging.info(f"Processing animated GIF {attachment.filename}: {total_frames} total frames, extracting frames at indices {frame_indices}")
                                    
                                    try:
                                        for frame_index in frame_indices:
                                            try:
                                                gif.seek(frame_index)
                                                # Convert frame to RGB (remove transparency)
                                                frame = gif.convert('RGB')
                                                
                                                # Convert frame to base64 for the API
                                                buffer = io.BytesIO()
                                                frame.save(buffer, format='JPEG', quality=frame_quality)
                                                buffer.seek(0)
                                                frame_b64 = base64.b64encode(buffer.getvalue()).decode()
                                                frames.append(f"data:image/jpeg;base64,{frame_b64}")
                                                
                                            except (EOFError, OSError) as e:
                                                logging.warning(f"Could not access frame {frame_index} in {attachment.filename}: {e}")
                                                continue
                                                
                                    except Exception as frame_error:
                                        logging.warning(f"Error extracting frames from {attachment.filename}: {frame_error}")
                                        # Try fallback method with sequential frame access
                                        frames = []
                                        try:
                                            frame_count = 0
                                            current_frame = 0
                                            while frame_count < max_frames and current_frame < total_frames:
                                                # Convert frame to RGB (remove transparency)
                                                frame = gif.convert('RGB')
                                                
                                                # Convert frame to base64 for the API
                                                buffer = io.BytesIO()
                                                frame.save(buffer, format='JPEG', quality=frame_quality)
                                                buffer.seek(0)
                                                frame_b64 = base64.b64encode(buffer.getvalue()).decode()
                                                frames.append(f"data:image/jpeg;base64,{frame_b64}")
                                                
                                                frame_count += 1
                                                current_frame += max(1, total_frames // max_frames)
                                                gif.seek(current_frame)
                                        except (EOFError, OSError):
                                            # End of GIF frames
                                            pass
                                    
                                    if frames:
                                        # Return structured data for animated GIFs that can be used in different contexts
                                        return {
                                            "type": "animated_gif",
                                            "total_frames": total_frames,
                                            "extracted_frames": len(frames),
                                            "frames": frames,
                                            "filename": attachment.filename
                                        }
                                    else:
                                        # Fallback to treating as static image
                                        return self._image_content_part(attachment.url, file_bytes, mime_type, model_name)
                                else:
                                    # Static GIF, treat as regular image
                                    return self._image_content_part(attachment.url, file_bytes, mime_type, model_name)
                            except Exception as e:
                                logging.warning(f"Failed to process potential GIF {attachment.filename} from {attachment.url}: {type(e).__name__}: {e}")
                                # Check if this was an image format issue
                                if "cannot identify image file" in str(e).lower() or "truncated" in str(e).lower():
                                    logging.info(f"Image format issue with {attachment.filename}, might not be a valid image file")
                                # Fallback to treating as static image
                                return self._image_content_part(attachment.url, model_name=model_name)
                        else:
                            # GIF frame extraction disabled, treat as static image
                            return self._image_content_part(attachment.url, file_bytes, mime_type, model_name)
                    else:
                        # Regular static image
                        return self._image_content_part(attachment.url, file_bytes, mime_type, model_name)
                else:
                    # Fallback: OCR for text-only models
                    if images_config.get("ocr_enabled", True) and TESSERACT_AVAILABLE:
                        try:
                            with Image.open(io.BytesIO(file_bytes)) as img:
                                ocr_text = pytesseract.image_to_string(img)
                                if ocr_text.strip():
                                    return f"--- Image OCR Text: {attachment.filename} ---\n{ocr_text.strip()}"
                                else:
                                    return f"[Image contains no readable text: {attachment.filename}]"
                        except Exception as e:
                            if images_config.get("description_fallback", True):
                                return f"[Image with OCR failure: {attachment.filename} - {str(e)[:50]}...]"
                            else:
                                return f"[Image omitted: {attachment.filename}]"
                    else:
                        return f"[Image omitted - OCR disabled or unavailable: {attachment.filename}]"

            # Audio processing
            elif self._is_audio_file(mime_type, file_ext):
                audio_config = media_settings.get("audio", {})
                return await self._process_audio_attachment(
                    attachment,
                    file_bytes,
                    file_ext,
                    file_size_mb,
                    model_name,
                    audio_config,
                )

            # Video processing
            elif self._is_video_file(mime_type, file_ext):
                video_config = media_settings.get("video", {})
                return await self._process_video_attachment(
                    attachment,
                    file_bytes,
                    file_ext,
                    file_size_mb,
                    model_name,
                    video_config,
                )

            # PDF processing
            elif mime_type == "application/pdf":
                pdf_config = media_settings.get("pdf", {})
                if not pdf_config.get("enabled", True):
                    return f"[PDF processing disabled: {attachment.filename}]"
                
                max_size = pdf_config.get("max_size_mb", 10)
                if file_size_mb > max_size:
                    return f"[PDF too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                if self.llm_provider.supports_pdf(model_name):
                    # Send directly as PDF data for PDF-capable models
                    return {"type": "pdf_url", "pdf_url": {"url": attachment.url}}
                else:
                    # Fallback: Text extraction for all models
                    try:
                        text = ""
                        if PDFPLUMBER_AVAILABLE:
                            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                                for i, page in enumerate(pdf.pages):
                                    page_text = page.extract_text() or ""
                                    if pdf_config.get("preserve_formatting", True):
                                        text += f"\n--- Page {i + 1} ---\n{page_text}\n"
                                    else:
                                        text += page_text + "\n"
                        else: # Fallback to PyPDF2
                            with io.BytesIO(file_bytes) as f:
                                reader = PyPDF2.PdfReader(f)
                                for page_num, page in enumerate(reader.pages):
                                    page_text = page.extract_text() or ""
                                    if pdf_config.get("preserve_formatting", True):
                                        text += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
                                    else:
                                        text += page_text + "\n"
                        return f"--- PDF Content: {attachment.filename} ---\n{text.strip()}"
                    except Exception as e:
                        return f"[PDF text extraction failed: {attachment.filename} - {str(e)[:50]}...]"

            # Office Documents (DOCX, XLSX, PPTX)
            elif mime_type in ["application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                               "application/vnd.openxmlformats-officedocument.presentationml.presentation"] or file_ext in OFFICE_EXTENSIONS:
                office_config = media_settings.get("office_documents", {})
                if not office_config.get("enabled", True):
                    return f"[Office document processing disabled: {attachment.filename}]"
                
                max_size = office_config.get("max_size_mb", 10)
                if file_size_mb > max_size:
                    return f"[Document too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                preserve_structure = office_config.get("preserve_structure", True)
                
                try:
                    if file_ext == "docx":
                        doc = docx.Document(io.BytesIO(file_bytes))
                        if preserve_structure:
                            text = ""
                            for para in doc.paragraphs:
                                if para.style.name.startswith('Heading'):
                                    text += f"\n## {para.text}\n"
                                else:
                                    text += f"{para.text}\n"
                        else:
                            text = "\n".join([para.text for para in doc.paragraphs])
                        return f"--- Document Content: {attachment.filename} ---\n{text.strip()}"
                    
                    elif file_ext == "xlsx":
                        wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
                        text = ""
                        for sheet_name in wb.sheetnames:
                            sheet = wb[sheet_name]
                            if preserve_structure:
                                text += f"\n--- Sheet: {sheet_name} ---\n"
                            for row in sheet.iter_rows(values_only=True):
                                text += ", ".join([str(cell) if cell is not None else "" for cell in row]) + "\n"
                        return f"--- Excel Content: {attachment.filename} ---\n{text.strip()}"
                    
                    elif file_ext == "pptx" and PPTX_AVAILABLE:
                        prs = pptx.Presentation(io.BytesIO(file_bytes))
                        text = ""
                        for i, slide in enumerate(prs.slides):
                            if preserve_structure:
                                text += f"\n--- Slide {i+1} ---\n"
                            for shape in slide.shapes:
                                if hasattr(shape, "text"):
                                    text += shape.text + "\n"
                        return f"--- PowerPoint Content: {attachment.filename} ---\n{text.strip()}"
                    elif file_ext == "pptx" and not PPTX_AVAILABLE:
                        return f"[PowerPoint processing unavailable - python-pptx not installed: {attachment.filename}]"
                        
                except Exception as e:
                    return f"[Document processing failed: {attachment.filename} - {str(e)[:50]}...]"

            # Text files
            elif (mime_type and mime_type.startswith("text/")) or file_ext in TEXT_EXTENSIONS:
                text_config = media_settings.get("text_files", {})
                if not text_config.get("enabled", True):
                    return f"[Text file processing disabled: {attachment.filename}]"
                
                max_size = text_config.get("max_size_mb", 5)
                if file_size_mb > max_size:
                    return f"[Text file too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                supported_extensions = text_config.get("supported_extensions", sorted(TEXT_EXTENSIONS))
                if file_ext not in supported_extensions:
                    return f"[Text file extension not supported: {attachment.filename}]"
                
                try:
                    # Full content read and included as text context for both decision and main models
                    text_content = file_bytes.decode('utf-8', errors='ignore')
                    return f"--- File Content: {attachment.filename} ---\n{text_content}"
                except Exception as e:
                    return f"[Text file reading failed: {attachment.filename} - {str(e)[:50]}...]"

            # Other files - metadata only
            else:
                other_config = media_settings.get("other_files", {})
                if not other_config.get("enabled", True):
                    return f"[Other file processing disabled: {attachment.filename}]"
                
                max_size = other_config.get("max_size_mb", 20)
                if file_size_mb > max_size:
                    return f"[File too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                if other_config.get("include_metadata_only", True):
                    # File name, size, type, and basic metadata included as text description
                    return f"--- File Metadata: {attachment.filename} ---\nFile Type: {mime_type or 'Unknown'}\nFile Size: {file_size_mb:.2f} MB\nFile Extension: .{file_ext}\nUpload URL: {attachment.url}"
                else:
                    return f"[Unsupported file type: {attachment.filename}]"

        except Exception as e:
            logging.error(f"Error processing attachment {attachment.filename}: {e}")
            return f"[Could not process file: {attachment.filename} - Error: {str(e)[:50]}...]"

    async def _process_embed(self, embed: discord.Embed, model_name: str, media_settings: dict):
        """
        Process Discord embeds, with special handling for Tenor GIFs and other media.
        """
        try:
            # Check if this embed contains media we can process
            embed_url = embed.url
            embed_image_url = embed.image.url if embed.image else None
            embed_video_url = embed.video.url if embed.video else None
            
            # Enhanced detection for embedded GIFs (especially Tenor)
            potential_gif_urls = []
            if embed_image_url:
                potential_gif_urls.append(embed_image_url)
            if embed_video_url:
                potential_gif_urls.append(embed_video_url)
            if embed_url:
                potential_gif_urls.append(embed_url)
            
            for url in potential_gif_urls:
                if url and (
                    'tenor.com' in url.lower() or 
                    'giphy.com' in url.lower() or 
                    'gif' in url.lower() or
                    url.endswith('.gif')
                ):
                    logging.info(f"Processing embedded GIF from URL: {url}")
                    
                    # Create a fake attachment-like object for processing
                    class EmbedAttachment:
                        def __init__(self, url, filename=None):
                            self.url = url
                            self.filename = filename or url.split('/')[-1] or 'embedded_gif.gif'
                            self.size = 0  # Unknown size for embeds
                    
                    fake_attachment = EmbedAttachment(url, f"embedded_{embed.type or 'media'}.gif")
                    
                    # Process using the same logic as attachments
                    try:
                        processed_content = await self._process_attachment(fake_attachment, model_name, media_settings)
                        if processed_content:
                            return processed_content
                    except Exception as e:
                        logging.warning(f"Failed to process embedded media from {url}: {e}")
                        continue
            
            # If no media was processed, return basic embed information
            embed_info = []
            if embed.title:
                embed_info.append(f"Embed Title: {embed.title}")
            if embed.description:
                embed_info.append(f"Embed Description: {embed.description[:200]}...")
            if embed.url and not any(url in potential_gif_urls for url in [embed.url]):
                embed_info.append(f"Embed URL: {embed.url}")
            
            if embed_info:
                return "--- Embedded Content ---\n" + "\n".join(embed_info)
            
            return None  # No processable content found
            
        except Exception as e:
            logging.warning(f"Error processing embed: {e}")
            return f"[Could not process embedded content - Error: {str(e)[:50]}...]"

    async def update_channel_summary(self, guild_id: str, channel_id: str):
        """
        Update the channel summary by integrating recent messages with the existing summary.
        This uses an incremental approach to build a comprehensive, evolving narrative.
        """
        logging.info(f"Updating summary for channel {channel_id} in guild {guild_id}...")
        
        try:
            data = await self.store.get_guild_data(guild_id)
            settings = await self.get_guild_and_channel_settings(guild_id, channel_id)
            
            # Get channel and validate it exists
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                logging.warning(f"Cannot update summary, channel {channel_id} not found.")
                return
            
            # Get current channel data
            channel_data = data.get("channels", {}).get(channel_id, {})
            old_summary = channel_data.get("summary", "")
            last_summary_time = channel_data.get("last_summary_time")
            
            # Determine if this is the first summary
            is_first_summary = not old_summary or old_summary.strip() == "" or old_summary == "No summary yet."
            
            # Determine how many messages to fetch based on settings
            if is_first_summary:
                history_limit = settings.get("initial_summarize_messages", self.config.INITIAL_SUMMARY_MESSAGES)
            else:
                history_limit = settings.get("summarize_every_messages", self.config.DEFAULT_SUMMARIZE_EVERY_MESSAGES)
            
            # If we have a last summary time, try to fetch messages since then
            # Otherwise, use the message count limit
            after_time = None
            if last_summary_time:
                try:
                    after_time = datetime.fromisoformat(last_summary_time)
                    # Add a small buffer to avoid missing messages due to timing
                    after_time = after_time.replace(microsecond=0)
                except ValueError:
                    logging.warning(f"Invalid last_summary_time format: {last_summary_time}")
            
            # Fetch recent messages
            recent_messages = []
            message_count = 0
            
            async for msg in channel.history(limit=min(history_limit * 2, 500), after=after_time):
                # Filter out bot messages (except for important context)
                if msg.author.bot and msg.author.id != self.bot.user.id:
                    continue
                
                # Skip very short messages that don't add context
                if len(msg.content.strip()) < 3 and not msg.attachments:
                    continue
                
                recent_messages.append(msg)
                message_count += 1
                
                # If we have enough messages and no specific time constraint, stop
                if not after_time and message_count >= history_limit:
                    break
            
            recent_messages.reverse()  # Oldest to newest
            
            if not recent_messages:
                logging.info(f"No new messages to summarize for channel {channel_id}.")
                return
            
            logging.info(f"Processing {len(recent_messages)} messages for channel summary update.")
            
            # Build conversation text with rich context
            conversation_lines = []
            for msg in recent_messages:
                # Format message with timestamp and user info
                timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                author_name = msg.author.display_name
                author_id = msg.author.id
                
                # Handle message content
                content = msg.content.strip() if msg.content else ""
                
                # Add attachment information
                attachment_info = ""
                if msg.attachments:
                    attachments = [f"[{att.filename}]" for att in msg.attachments]
                    attachment_info = f" (attachments: {', '.join(attachments)})"
                
                # Handle replies
                reply_info = ""
                if msg.reference and msg.reference.resolved:
                    if hasattr(msg.reference.resolved, 'author'):
                        try:
                            reply_to = msg.reference.resolved.author.display_name
                            reply_to_id = msg.reference.resolved.author.id
                            reply_info = f" (replying to {reply_to} [{reply_to_id}])"
                        except AttributeError:
                            # Handle deleted accounts
                            reply_info = " (replying to [Deleted Account])"
                    else:
                        # Handle deleted messages
                        reply_info = " (replying to [Deleted Message])"
                
                # Combine all parts
                if content or attachment_info:
                    full_message = f"[{timestamp}] {author_name} [{author_id}]{reply_info}: {content}{attachment_info}"
                    conversation_lines.append(full_message)
            
            conversation_text = "\n".join(conversation_lines)
            
            # Determine if this is the first summary or an update
            is_first_summary = not old_summary or old_summary.strip() == "" or old_summary == "No summary yet."
            
            if is_first_summary:
                # Create initial summary
                prompt = f"""You are a conversation summarizer for a Discord channel. 
Analyze the following conversation and create a comprehensive summary that captures:
- Main topics and themes discussed
- Key questions asked and answers provided
- Important decisions or conclusions reached
- Notable events or announcements
- Active participants and their contributions

The summary should be well-organized, informative, and provide a clear overview of what this channel is about and what has been discussed.

Channel: #{channel.name}
Server: {channel.guild.name}

Conversation History:
---
{conversation_text}
---

Provide a comprehensive channel summary:"""
            else:
                # Update existing summary incrementally
                prompt = f"""You are a conversation summarizer for a Discord channel.
You have an existing summary of the channel's conversation history. Now you need to integrate new messages into this summary.

Current Channel Summary:
---
{old_summary}
---

New Messages to Integrate:
---
{conversation_text}
---

Instructions:
1. Review the existing summary and the new messages
2. Identify new topics, developments, or conclusions from the recent messages
3. Update the existing summary by integrating the new information
4. Maintain the chronological flow and thematic organization
5. Remove or update outdated information if necessary
6. Keep the summary comprehensive but concise

Provide the updated, complete channel summary:"""
            
            # Generate the summary using the LLM
            response = await self.llm_provider.create_completion(
                model=settings.get("model", self.config.MAIN_LLM_MODEL),
                messages=[
                    {"role": "system", "content": "You are an expert conversation summarizer. Create clear, comprehensive, and well-organized summaries that capture the essence of Discord channel conversations."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            if not response or not response.choices:
                logging.error(f"Failed to get a valid response from LLM for channel summary {channel_id}.")
                return
            
            new_summary = response.choices[0].message.content.strip()
            
            # Validate the summary isn't empty or generic
            if len(new_summary) < 50 or new_summary.lower().startswith("i cannot") or new_summary.lower().startswith("i can't"):
                logging.warning(f"Generated summary for channel {channel_id} seems invalid or too short: {new_summary[:100]}...")
                return
            
            # Update data file with new summary and metadata
            fresh_data = await self.store.get_data()
            if str(guild_id) not in fresh_data:
                fresh_data[str(guild_id)] = {}
            if "channels" not in fresh_data[str(guild_id)]:
                fresh_data[str(guild_id)]["channels"] = {}
            if channel_id not in fresh_data[str(guild_id)]["channels"]:
                fresh_data[str(guild_id)]["channels"][channel_id] = {}
            
            # Store the new summary and reset counters
            fresh_data[str(guild_id)]["channels"][channel_id]["summary"] = new_summary
            fresh_data[str(guild_id)]["channels"][channel_id]["messages_since_summary"] = 0
            fresh_data[str(guild_id)]["channels"][channel_id]["last_summary_time"] = datetime.now(timezone.utc).isoformat()
            fresh_data[str(guild_id)]["channels"][channel_id]["messages_processed"] = len(recent_messages)
            fresh_data[str(guild_id)]["channels"][channel_id]["summary_type"] = "initial" if is_first_summary else "incremental"
            
            # Save the updated data
            await self.store.save_data(fresh_data)
            
            summary_type = "Initial" if is_first_summary else "Incremental"
            logging.info(f"Successfully updated {summary_type.lower()} summary for channel #{channel.name} ({channel_id}). Processed {len(recent_messages)} messages.")
            
        except Exception as e:
            logging.error(f"Error updating channel summary for {channel_id}: {e}")
            # Don't re-raise to avoid breaking the message processing flow

    async def get_channel_summary(self, guild_id: str, channel_id: str) -> dict:
        """
        Get the current channel summary and metadata.
        
        Returns:
            dict: Contains summary text, last update time, message count, etc.
        """
        try:
            data = await self.store.get_guild_data(guild_id)
            channel_data = data.get("channels", {}).get(channel_id, {})
            
            return {
                "summary": channel_data.get("summary", "No summary available yet."),
                "last_summary_time": channel_data.get("last_summary_time"),
                "messages_since_summary": channel_data.get("messages_since_summary", 0),
                "messages_processed": channel_data.get("messages_processed", 0),
                "summary_type": channel_data.get("summary_type", "none")
            }
        except Exception as e:
            logging.error(f"Error getting channel summary for {channel_id}: {e}")
            return {
                "summary": "Error retrieving summary.",
                "last_summary_time": None,
                "messages_since_summary": 0,
                "messages_processed": 0,
                "summary_type": "error"
            }

    async def force_channel_summary_update(self, guild_id: str, channel_id: str) -> bool:
        """
        Force an immediate update of the channel summary regardless of message count or time.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logging.info(f"Forcing channel summary update for {channel_id} in guild {guild_id}")
            await self.update_channel_summary(guild_id, channel_id)
            return True
        except Exception as e:
            logging.error(f"Error forcing channel summary update for {channel_id}: {e}")
            return False

    async def clear_channel_summary(self, guild_id: str, channel_id: str) -> bool:
        """
        Clear the channel summary and reset counters.
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            fresh_data = await self.store.get_data()
            
            if str(guild_id) not in fresh_data:
                fresh_data[str(guild_id)] = {}
            if "channels" not in fresh_data[str(guild_id)]:
                fresh_data[str(guild_id)]["channels"] = {}
            if channel_id not in fresh_data[str(guild_id)]["channels"]:
                return False  # No summary to clear
            
            # Clear summary but keep the channel entry
            channel_data = fresh_data[str(guild_id)]["channels"][channel_id]
            channel_data.pop("summary", None)
            channel_data.pop("last_summary_time", None)
            channel_data["messages_since_summary"] = 0
            channel_data.pop("messages_processed", None)
            channel_data.pop("summary_type", None)
            
            await self.store.save_data(fresh_data)
            logging.info(f"Cleared channel summary for {channel_id}")
            return True
            
        except Exception as e:
            logging.error(f"Error clearing channel summary for {channel_id}: {e}")
            return False

    async def update_user_profile(self, guild_id: str, user_id: str, guild_obj=None):
        """
        Updates the AI-generated profile for a user by gathering their recent messages
        across all channels in the guild and generating/updating their summary.
        """
        logging.info(f"Updating AI profile for user {user_id} in guild {guild_id}...")
        
        try:
            # Get current data and settings
            data = await self.store.get_guild_data(guild_id)
            settings = await self.get_guild_and_channel_settings(guild_id, None)
            
            # Initialize guild data structure if needed
            if "users" not in data:
                data["users"] = {}
            if user_id not in data["users"]:
                data["users"][user_id] = {}
                
            user_data = data["users"][user_id]
            old_summary = user_data.get("ai_summary", "")
            manual_note = user_data.get("manual_note", "")
            
            # Gather recent messages from the user across all accessible channels
            user_messages = await self._gather_user_messages(guild_id, user_id, guild_obj)
            
            if not user_messages:
                logging.warning(f"No messages found for user {user_id} in guild {guild_id}")
                # Reset counters even if no messages found
                user_data["messages_since_profile_update"] = 0
                user_data["last_profile_update_time"] = datetime.now(timezone.utc).isoformat()
                
                # Get fresh data and update to avoid overwriting concurrent changes
                fresh_data = await self.store.get_data()
                if str(guild_id) not in fresh_data:
                    fresh_data[str(guild_id)] = {}
                if "users" not in fresh_data[str(guild_id)]:
                    fresh_data[str(guild_id)]["users"] = {}
                if user_id not in fresh_data[str(guild_id)]["users"]:
                    fresh_data[str(guild_id)]["users"][user_id] = {}
                
                fresh_data[str(guild_id)]["users"][user_id].update(user_data)
                await self.store.save_data(fresh_data)
                return
            
            # Generate AI summary using LLM
            new_summary = await self._generate_user_profile_summary(
                user_messages, old_summary, manual_note, user_id, settings
            )
            
            if new_summary:
                # Update user data
                user_data["ai_summary"] = new_summary
                user_data["messages_since_profile_update"] = 0
                user_data["last_profile_update_time"] = datetime.now(timezone.utc).isoformat()
                
                # Get fresh data and update to avoid overwriting concurrent changes
                fresh_data = await self.store.get_data()
                if str(guild_id) not in fresh_data:
                    fresh_data[str(guild_id)] = {}
                if "users" not in fresh_data[str(guild_id)]:
                    fresh_data[str(guild_id)]["users"] = {}
                if user_id not in fresh_data[str(guild_id)]["users"]:
                    fresh_data[str(guild_id)]["users"][user_id] = {}
                
                fresh_data[str(guild_id)]["users"][user_id].update(user_data)
                await self.store.save_data(fresh_data)
                logging.info(f"AI profile updated successfully for user {user_id}")
            else:
                logging.error(f"Failed to generate AI summary for user {user_id}")
                
        except Exception as e:
            logging.error(f"Error updating user profile for {user_id}: {e}")
            
    async def _gather_user_messages(self, guild_id: str, user_id: str, guild_obj=None, max_messages: int = 100):
        """
        Gathers recent messages from a user across all accessible channels in the guild.
        """
        if not guild_obj:
            # Try to get guild from bot if not provided
            try:
                guild_obj = discord.utils.get(self.bot.guilds, id=int(guild_id))
                if not guild_obj:
                    logging.error(f"Guild {guild_id} not found")
                    return []
            except Exception as e:
                logging.error(f"Error getting guild {guild_id}: {e}")
                return []
        
        user_messages = []
        channels_checked = 0
        
        try:
            # Get user object
            user = guild_obj.get_member(int(user_id))
            if not user:
                logging.warning(f"User {user_id} not found in guild {guild_id}")
                return []
            
            # Iterate through all text channels the bot can access
            for channel in guild_obj.text_channels:
                try:
                    # Check if bot has permission to read message history
                    if not channel.permissions_for(guild_obj.me).read_message_history:
                        continue
                        
                    channels_checked += 1
                    
                    # Fetch recent messages from this channel (limit per channel to avoid overload)
                    async for message in channel.history(limit=200):  # Look through more messages to find user's
                        if message.author.id == int(user_id):
                            if len(user_messages) >= max_messages:
                                break
                                
                            # Format message with context
                            formatted_message = {
                                'content': message.content or '[No text content]',
                                'channel': channel.name,
                                'timestamp': message.created_at.isoformat(),
                                'has_attachments': len(message.attachments) > 0,
                                'attachment_types': [att.content_type or 'unknown' for att in message.attachments] if message.attachments else [],
                                'embeds_count': len(message.embeds),
                                'reactions_count': len(message.reactions)
                            }
                            user_messages.append(formatted_message)
                            
                        if len(user_messages) >= max_messages:
                            break
                            
                except discord.Forbidden:
                    # Bot doesn't have permission to read this channel
                    continue
                except Exception as e:
                    logging.warning(f"Error reading messages from channel {channel.name}: {e}")
                    continue
                    
                if len(user_messages) >= max_messages:
                    break
            
            # Sort messages by timestamp (oldest first for chronological context)
            user_messages.sort(key=lambda x: x['timestamp'])
            
            logging.info(f"Gathered {len(user_messages)} messages from {channels_checked} channels for user {user_id}")
            return user_messages
            
        except Exception as e:
            logging.error(f"Error gathering messages for user {user_id}: {e}")
            return []
    
    async def _generate_user_profile_summary(self, user_messages, old_summary, manual_note, user_id, settings):
        """
        Generates or updates an AI summary of the user based on their messages.
        """
        try:
            model = settings.get("model", self.config.MAIN_LLM_MODEL)
            
            # Prepare message context (limit to prevent overly long prompts)
            message_context = []
            for msg in user_messages[-30:]:  # Reduced from 50 to 30 messages to keep prompt shorter
                context_line = f"[{msg['channel']}] {msg['content'][:200]}"  # Truncate long messages
                if msg['has_attachments']:
                    context_line += f" [Attachments: {len(msg['attachment_types'])} files]"
                if msg['embeds_count'] > 0:
                    context_line += f" [Embeds: {msg['embeds_count']}]"
                message_context.append(context_line)
            
            messages_text = "\n".join(message_context)
            
            # Build the prompt for profile generation/update (more concise)
            if old_summary and old_summary.strip():
                # Incremental update
                prompt = f"""Update this user's Discord profile summary with their recent messages.

CURRENT SUMMARY:
{old_summary}

RECENT MESSAGES:
{messages_text}

ADMIN NOTE: {manual_note or 'None'}

Provide an updated 2-paragraph summary focusing on communication style, interests, and notable traits."""
            else:
                # Initial profile generation
                prompt = f"""Create a Discord user profile summary from these messages.

MESSAGES:
{messages_text}

ADMIN NOTE: {manual_note or 'None'}

Provide a 2-paragraph summary covering communication style, interests, activity patterns, and key traits."""

            # Make the API call
            response = await self.llm_provider.create_completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,  # Further increased token limit
                temperature=0.3
            )
            
            if response and response.choices and len(response.choices) > 0:
                choice = response.choices[0]
                if choice.message and choice.message.content:
                    return choice.message.content.strip()
                elif choice.finish_reason == 'length':
                    logging.error("LLM response was truncated due to token limit. Consider increasing max_tokens.")
                    return None
                else:
                    logging.error(f"LLM response has no content. Finish reason: {choice.finish_reason}")
                    return None
            else:
                logging.error("Empty or invalid response from LLM for user profile generation")
                return None
                
        except Exception as e:
            logging.error(f"Error generating user profile summary: {e}")
            return None
