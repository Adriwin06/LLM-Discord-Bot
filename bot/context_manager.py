# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/context_manager.py
import discord
import logging
from .store import Store
from .llm_provider import LiteLLMProvider
from .config import Config
import mimetypes
import aiohttp
import PyPDF2
import docx
import openpyxl
from PIL import Image
import io

# Optional imports for media processing
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logging.warning("whisper not available. Audio transcription features will be limited.")

try:
    import moviepy
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    logging.warning("moviepy not available. Video processing features will be limited.")

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

class ContextManager:
    def __init__(self, store: Store, llm_provider: LiteLLMProvider, bot=None):
        self.store = store
        self.llm_provider = llm_provider
        self.config = Config()
        self.bot = bot
        # Consider loading the whisper model once if it's going to be used
        # self.whisper_model = whisper.load_model("base")

    async def get_guild_and_channel_settings(self, guild_id, channel_id):
        guild_settings = await self.store.get_guild_settings(guild_id)
        channel_overrides = guild_settings.get("channel_overrides", {}).get(str(channel_id), {})
        
        final_settings = guild_settings.copy()
        final_settings.update(channel_overrides)
        
        return final_settings

    async def build_context_for_message(self, message: discord.Message, model_name: str = None):
        """
        Build context for a specific model. If model_name is not provided, uses MAIN_LLM_MODEL.
        
        According to the spec, both decision and main models should receive identical media processing
        but processed according to each model's individual capabilities.
        """
        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)

        settings = await self.get_guild_and_channel_settings(guild_id, channel_id)
        data = await self.store.get_guild_data(guild_id)

        # Use specified model or default to main model
        target_model = model_name or self.config.MAIN_LLM_MODEL

        # 1. System Prompts
        behavior_prompt = settings.get("behavior_prompt", self.config.BEHAVIOR_PROMPT)
        system_prompt = f"{self.config.CAPABILITIES_PROMPT}\n\n{behavior_prompt}"
        
        messages = [{"role": "system", "content": system_prompt}]

        # 2. Channel Summary
        channel_data = data.get("channels", {}).get(channel_id, {})
        if "summary" in channel_data:
            messages.append({"role": "system", "content": f"Channel Summary:\n{channel_data['summary']}"})

        # 3. User Profiles
        user_data = data.get("users", {}).get(user_id, {})
        user_profile_content = []
        if "manual_note" in user_data:
            user_profile_content.append(f"Manual note about {message.author.display_name}: {user_data['manual_note']}")
        if "ai_summary" in user_data:
            user_profile_content.append(f"AI summary of {message.author.display_name}: {user_data['ai_summary']}")
        
        if user_profile_content:
            messages.append({"role": "system", "content": "\n".join(user_profile_content)})

        # 4. Conversation History (Reply Chain + Recent Messages)
        history_limit = settings.get("context", {}).get("history_messages", 15)
        reply_chain_limit = settings.get("context", {}).get("reply_chain_limit", 5)

        # Fetch reply chain
        reply_chain = []
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
        
        # Don't include the current message itself in the history
        if message.id in all_messages:
            del all_messages[message.id]

        sorted_messages = sorted(all_messages.values(), key=lambda m: m.created_at)

        for msg in sorted_messages:
            content = await self._format_message_content(msg, target_model)
            role = "assistant" if self.bot and msg.author.id == self.bot.user.id else "user"
            messages.append({"role": role, "content": content})

        # 5. Current Message
        current_message_content = await self._format_message_content(message, target_model)
        messages.append({"role": "user", "content": current_message_content})

        return messages, settings

    async def _format_message_content(self, message: discord.Message, model_name: str = None):
        """
        Format message content with attachments processed for the specified model.
        If model_name is not provided, uses MAIN_LLM_MODEL.
        """
        content_parts = []
        
        # Always add text content first
        if message.content:
            content_parts.append({"type": "text", "text": message.content})

        if message.attachments:
            # Use specified model or default to main model for attachment processing
            target_model = model_name or self.config.MAIN_LLM_MODEL
            media_settings = (await self.get_guild_and_channel_settings(message.guild.id, message.channel.id)).get("media", {})
            
            for attachment in message.attachments:
                processed_content = await self._process_attachment(attachment, target_model, media_settings)
                if isinstance(processed_content, dict):
                    # This is a structured content (like image_url)
                    content_parts.append(processed_content)
                else:
                    # This is a text fallback
                    content_parts.append({"type": "text", "text": processed_content})
        
        # If we only have text content, return it as a string for simplicity
        if len(content_parts) == 1 and content_parts[0].get("type") == "text":
            return content_parts[0]["text"]
        elif content_parts:
            return content_parts
        else:
            return "[empty message]"

    async def _process_attachment(self, attachment: discord.Attachment, model_name: str, media_settings: dict):
        mime_type = mimetypes.guess_type(attachment.filename)[0]
        file_ext = attachment.filename.lower().split('.')[-1] if '.' in attachment.filename else ''
        
        try:
            # Check file size limits first
            file_size_mb = attachment.size / (1024 * 1024)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        return f"[Could not fetch attachment: {attachment.filename}]"
                    file_bytes = await resp.read()

            # Image processing
            if mime_type and mime_type.startswith("image/"):
                images_config = media_settings.get("images", {})
                if not images_config.get("enabled", True):
                    return f"[Image processing disabled: {attachment.filename}]"
                
                max_size = images_config.get("max_size_mb", 10)
                if file_size_mb > max_size:
                    return f"[Image too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                if self.llm_provider.supports_vision(model_name):
                    # Send directly as image data for vision-capable models
                    return {"type": "image_url", "image_url": {"url": attachment.url}}
                else:
                    # Fallback: OCR for text-only models
                    if images_config.get("ocr_enabled", True):
                        try:
                            # TODO: Implement OCR using PIL and pytesseract
                            return f"[Image OCR placeholder: {attachment.filename} - OCR would extract text here]"
                        except Exception as e:
                            if images_config.get("description_fallback", True):
                                return f"[Image with OCR failure: {attachment.filename} - {str(e)[:50]}...]"
                            else:
                                return f"[Image omitted: {attachment.filename}]"
                    else:
                        return f"[Image omitted - OCR disabled: {attachment.filename}]"

            # Audio processing
            elif mime_type and mime_type.startswith("audio/"):
                audio_config = media_settings.get("audio", {})
                if not audio_config.get("enabled", True):
                    return f"[Audio processing disabled: {attachment.filename}]"
                
                # TODO: Check actual audio duration when implementing full audio processing
                # max_duration = audio_config.get("max_duration_seconds", 300)
                
                if self.llm_provider.supports_audio(model_name):
                    # Send directly as audio data for audio-capable models
                    return {"type": "audio_url", "audio_url": {"url": attachment.url}}
                else:
                    # Fallback: Transcription for text-only models
                    if WHISPER_AVAILABLE:
                        try:
                            # TODO: Implement actual Whisper transcription
                            include_timestamps = audio_config.get("include_timestamps", False)
                            return f"[Audio transcription placeholder: {attachment.filename} - Whisper would transcribe here with timestamps={include_timestamps}]"
                        except Exception as e:
                            return f"[Audio transcription failed: {attachment.filename} - {str(e)[:50]}...]"
                    else:
                        return f"[Audio transcription unavailable - whisper not installed: {attachment.filename}]"

            # Video processing
            elif mime_type and mime_type.startswith("video/"):
                video_config = media_settings.get("video", {})
                if not video_config.get("enabled", True):
                    return f"[Video processing disabled: {attachment.filename}]"
                
                # TODO: Check actual video duration when implementing full video processing
                # max_duration = video_config.get("max_duration_seconds", 120)
                
                if self.llm_provider.supports_vision(model_name):  # Assuming video support correlates with vision
                    # Send directly as video data for video-capable models
                    return {"type": "video_url", "video_url": {"url": attachment.url}}
                else:
                    # Fallback: Extract audio and frames for text-only models
                    if MOVIEPY_AVAILABLE:
                        try:
                            extract_audio = video_config.get("extract_audio", True)
                            extract_frames = video_config.get("extract_frames", True)
                            frame_interval = video_config.get("frame_interval_seconds", 10)
                            
                            # TODO: Implement actual video processing
                            return f"[Video processing placeholder: {attachment.filename} - Would extract audio (transcribe={extract_audio}) and frames every {frame_interval}s (extract={extract_frames})]"
                        except Exception as e:
                            return f"[Video processing failed: {attachment.filename} - {str(e)[:50]}...]"
                    else:
                        return f"[Video processing unavailable - moviepy not installed: {attachment.filename}]"

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
                        with io.BytesIO(file_bytes) as f:
                            reader = PyPDF2.PdfReader(f)
                            for page_num, page in enumerate(reader.pages):
                                page_text = page.extract_text()
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
                               "application/vnd.openxmlformats-officedocument.presentationml.presentation"]:
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
                        # TODO: Implement Excel processing with openpyxl
                        return f"[Excel processing placeholder: {attachment.filename} - Would extract all sheets with structure preservation={preserve_structure}]"
                    
                    elif file_ext == "pptx":
                        # TODO: Implement PowerPoint processing with python-pptx
                        return f"[PowerPoint processing placeholder: {attachment.filename} - Would extract slides with structure preservation={preserve_structure}]"
                        
                except Exception as e:
                    return f"[Document processing failed: {attachment.filename} - {str(e)[:50]}...]"

            # Text files
            elif mime_type and mime_type.startswith("text/") or file_ext in ["txt", "md", "json", "csv", "py", "js", "html", "css", "xml", "yaml", "yml"]:
                text_config = media_settings.get("text_files", {})
                if not text_config.get("enabled", True):
                    return f"[Text file processing disabled: {attachment.filename}]"
                
                max_size = text_config.get("max_size_mb", 5)
                if file_size_mb > max_size:
                    return f"[Text file too large for processing: {attachment.filename} - {file_size_mb:.1f}MB]"
                
                supported_extensions = text_config.get("supported_extensions", ["txt", "md", "json", "csv", "py", "js", "html", "css"])
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

    async def update_channel_summary(self, guild_id, channel_id, new_messages):
        # This is a placeholder for the summary logic.
        # It would involve calling the LLM with the old summary and new messages.
        logging.info(f"Placeholder: Would update summary for channel {channel_id} in guild {guild_id}.")
        pass

    async def update_user_profile(self, guild_id, user_id, new_messages):
        # This is a placeholder for the user profile update logic.
        logging.info(f"Placeholder: Would update profile for user {user_id} in guild {guild_id}.")
        pass
