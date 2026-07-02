# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/llm_provider.py
import litellm
from litellm import acompletion
import asyncio
import json
import logging
import os
import urllib.request
from urllib.parse import urlparse
from .config import Config

class LiteLLMProvider:
    """
    LLM provider using LiteLLM for universal model support.
    
    This class implements a dual-LLM system:
    - Main LLM: High-quality model for generating responses
    - Decision LLM: Lightweight model for quick reply/reaction decisions
    
    Features:
    - Rate limiting for both models
    - Automatic web search support detection
    - Vision and audio capability detection
    """
    def __init__(self, config: Config):
        self.config = config
        self.main_llm_rate_limiter = asyncio.Semaphore(1)
        self.decision_llm_rate_limiter = asyncio.Semaphore(1)
        self.main_llm_last_call = 0
        self.decision_llm_last_call = 0
        self.last_error = None
        self._capability_cache = {}

    async def _rate_limit(self, limiter_type: str):
        """
        Apply rate limiting based on configuration.
        
        Args:
            limiter_type: Either 'main' or 'decision' to specify which rate limiter to use
        """
        if limiter_type == 'main':
            if not self.config.MAIN_LLM_RATE_LIMIT_ENABLED:
                logging.debug("Main LLM rate limiting disabled.")
                return
            async with self.main_llm_rate_limiter:
                now = asyncio.get_event_loop().time()
                elapsed = now - self.main_llm_last_call
                delay = self.config.MAIN_LLM_RATE_LIMIT_SECONDS - elapsed
                if delay > 0:
                    logging.info("Rate limiting main LLM for %.2fs.", delay)
                    await asyncio.sleep(delay)
                self.main_llm_last_call = asyncio.get_event_loop().time()
        elif limiter_type == 'decision':
            if not self.config.DECISION_LLM_RATE_LIMIT_ENABLED:
                logging.debug("Decision LLM rate limiting disabled.")
                return
            async with self.decision_llm_rate_limiter:
                now = asyncio.get_event_loop().time()
                elapsed = now - self.decision_llm_last_call
                delay = self.config.DECISION_LLM_RATE_LIMIT_SECONDS - elapsed
                if delay > 0:
                    logging.info("Rate limiting decision LLM for %.2fs.", delay)
                    await asyncio.sleep(delay)
                self.decision_llm_last_call = asyncio.get_event_loop().time()

    def _model_chain(self, model: str) -> list:
        """Build the ordered list of models to try: the requested model plus configured fallbacks."""
        if model == self.config.DECISION_LLM_MODEL:
            fallbacks = self.config.DECISION_LLM_FALLBACK_MODELS
        else:
            fallbacks = self.config.MAIN_LLM_FALLBACK_MODELS

        chain = [model]
        for fallback in fallbacks or []:
            fallback = (fallback or "").strip()
            if fallback and fallback not in chain:
                chain.append(fallback)
        return chain

    async def create_completion(self, model: str, messages: list, **kwargs):
        """
        Create a completion using the specified model, falling back through the
        configured fallback model chain when a model fails (rate limit, outage, etc.).

        Automatically handles:
        - Rate limiting based on model type
        - Provider credential resolution through LiteLLM environment variables
        - Web search capabilities for supported models
        - JSON response format compatibility

        Args:
            model: Model identifier (e.g., 'gpt-4o', 'gemini/gemini-1.5-pro')
            messages: List of message dictionaries
            **kwargs: Additional parameters for the completion

        Returns:
            LiteLLM response object or None if every model in the chain failed
        """
        self.last_error = None
        model_chain = self._model_chain(model)

        for index, attempt_model in enumerate(model_chain):
            if index:
                logging.warning(
                    "Falling back to model %s (attempt %s/%s) after failure: %s",
                    attempt_model,
                    index + 1,
                    len(model_chain),
                    self.get_last_error_summary(),
                )
            response = await self._attempt_completion(attempt_model, messages, kwargs)
            if response is not None:
                self.last_error = None
                return response

        if len(model_chain) > 1:
            logging.error("All models in the fallback chain failed: %s", model_chain)
        return None

    async def _attempt_completion(self, model: str, messages: list, kwargs: dict):
        # Only the dedicated decision model uses the decision limiter; fallback models
        # and per-guild override models must pace like the main model.
        limiter_type = 'decision' if model == self.config.DECISION_LLM_MODEL else 'main'
        await self._rate_limit(limiter_type)
        completion_kwargs = kwargs.copy()

        try:
            # Check if model supports web search and add web search options
            request_stats = self._message_stats(messages)
            logging.info(
                "LLM request starting. model=%s limiter=%s messages=%s text_chars=%s image_parts=%s tools=%s json_response=%s",
                model,
                limiter_type,
                request_stats["message_count"],
                request_stats["text_chars"],
                request_stats["image_parts"],
                len(completion_kwargs.get("tools") or []),
                completion_kwargs.get("response_format", {}).get("type") == "json_object",
            )
            logging.debug("LLM request content stats: %s", request_stats)
            
            # Check if JSON response format is requested
            has_json_response_format = (
                'response_format' in completion_kwargs and 
                completion_kwargs.get('response_format', {}).get('type') == 'json_object'
            )
            
            has_local_tools = 'tools' in completion_kwargs
            auto_web_search_enabled = (
                self.config.WEB_SEARCH_ENABLED and
                getattr(self.config, "WEB_SEARCH_AUTO_ENABLED", False)
            )

            if self.supports_web_search(model) and not has_json_response_format and not has_local_tools and auto_web_search_enabled:
                # Add web search options if not already provided in kwargs
                # Skip web search if JSON response format is requested due to tool conflicts
                if 'web_search_options' not in completion_kwargs:
                    completion_kwargs['web_search_options'] = {
                        "search_context_size": self.config.WEB_SEARCH_CONTEXT_SIZE
                    }
                logging.info(f"Using web search for model {model} with options: {completion_kwargs.get('web_search_options')}")
            elif self.supports_web_search(model) and has_json_response_format:
                logging.info(f"Skipping web search for model {model} due to JSON response format requirement")
            elif self.supports_web_search(model) and has_local_tools:
                logging.info(f"Skipping provider-side web search for model {model}; explicit local tools are available")
            elif self.supports_web_search(model) and not auto_web_search_enabled:
                logging.info(f"Provider-side automatic web search disabled in configuration for model {model}")

            # Use the async completion function directly
            response = await acompletion(
                model=model,
                messages=messages,
                **completion_kwargs
            )
            self._log_response_summary(model, response)
            return response
        except Exception as e:
            if self._should_retry_without_image_parts(e, messages):
                self._record_error(model, e)
                logging.warning(
                    "LiteLLM image completion error for model %s: %s. Retrying without image parts.",
                    model,
                    e,
                )
                try:
                    response = await acompletion(
                        model=model,
                        messages=self._messages_without_image_parts(messages),
                        **completion_kwargs
                    )
                    self.last_error = None
                    self._log_response_summary(model, response)
                    return response
                except Exception as retry_error:
                    self._record_error(model, retry_error)
                    logging.exception(f"LiteLLM retry without image parts failed for model {model}: {retry_error}")
                    return None

            if 'tools' in completion_kwargs and 'web_search_options' in completion_kwargs:
                self._record_error(model, e)
                logging.warning(
                    f"LiteLLM completion error for model {model} with local tools and web search: {e}. "
                    "Retrying without provider web search options."
                )
                completion_kwargs.pop('web_search_options', None)
                try:
                    response = await acompletion(
                        model=model,
                        messages=messages,
                        **completion_kwargs
                    )
                    self.last_error = None
                    self._log_response_summary(model, response)
                    return response
                except Exception as retry_error:
                    self._record_error(model, retry_error)
                    logging.exception(f"LiteLLM retry without web search failed for model {model}: {retry_error}")
                    return None

            self._record_error(model, e)
            logging.exception(f"LiteLLM completion error for model {model}: {e}")
            return None

    def _message_stats(self, messages: list) -> dict:
        stats = {
            "message_count": len(messages or []),
            "roles": {},
            "text_chars": 0,
            "image_parts": 0,
            "inline_image_parts": 0,
            "other_parts": 0,
        }

        for message in messages or []:
            if not isinstance(message, dict):
                stats["other_parts"] += 1
                continue

            role = message.get("role", "unknown")
            stats["roles"][role] = stats["roles"].get(role, 0) + 1
            self._add_content_stats(message.get("content"), stats)

        return stats

    def _add_content_stats(self, content, stats: dict):
        if isinstance(content, str):
            stats["text_chars"] += len(content)
            return

        if isinstance(content, list):
            for item in content:
                self._add_content_stats(item, stats)
            return

        if isinstance(content, dict):
            content_type = content.get("type")
            if content_type == "text":
                stats["text_chars"] += len(content.get("text") or "")
            elif content_type in {"image_url", "input_image"}:
                stats["image_parts"] += 1
                image_url = (content.get("image_url") or {}).get("url") or content.get("image_url") or ""
                if isinstance(image_url, str) and image_url.startswith("data:image/"):
                    stats["inline_image_parts"] += 1
            else:
                stats["other_parts"] += 1
            return

        if content is not None:
            stats["other_parts"] += 1

    def _should_retry_without_image_parts(self, error: Exception, messages: list) -> bool:
        stats = self._message_stats(messages)
        if stats["image_parts"] <= 0:
            return False

        error_text = str(error).lower()
        image_markers = (
            "image",
            "png",
            "jpeg",
            "jpg",
            "webp",
            "gif",
            "pixel data",
        )
        decode_markers = (
            "failed to process inputs",
            "invalid format",
            "not enough pixel data",
            "unsupported image",
            "cannot identify image",
            "invalid image",
        )
        return any(marker in error_text for marker in image_markers) and any(marker in error_text for marker in decode_markers)

    def _messages_without_image_parts(self, messages: list) -> list:
        stripped_messages = []
        for message in messages or []:
            if not isinstance(message, dict):
                stripped_messages.append(message)
                continue

            stripped_message = dict(message)
            stripped_message["content"] = self._content_without_image_parts(message.get("content"))
            stripped_messages.append(stripped_message)
        return stripped_messages

    def _content_without_image_parts(self, content):
        if isinstance(content, list):
            stripped_items = []
            for item in content:
                stripped = self._content_without_image_parts(item)
                if stripped is None:
                    continue
                stripped_items.append(stripped)
            return stripped_items

        if isinstance(content, dict):
            content_type = content.get("type")
            if content_type in {"image_url", "input_image"}:
                return {
                    "type": "text",
                    "text": "[Image omitted because the model provider rejected the image data.]",
                }

            return {
                key: self._content_without_image_parts(value)
                for key, value in content.items()
            }

        return content

    def _log_response_summary(self, model: str, response):
        if not response or not getattr(response, "choices", None):
            logging.warning("LLM response was empty or missing choices. model=%s", model)
            return

        choice = response.choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        logging.info(
            "LLM response received. model=%s finish_reason=%s content_chars=%s choices=%s",
            model,
            getattr(choice, "finish_reason", None),
            len(content or ""),
            len(response.choices),
        )

    def _record_error(self, model: str, error: Exception):
        self.last_error = {
            "model": model,
            "type": type(error).__name__,
            "message": str(error),
        }

    def get_last_error_message(self) -> str:
        if not self.last_error:
            return "No provider error was recorded."

        error_type = self.last_error.get("type", "Error")
        model = self.last_error.get("model", "unknown model")
        message = self.last_error.get("message", "")
        return f"{error_type} from {model}: {message}"

    def get_last_error_summary(self, max_length: int = 200) -> str:
        """Short single-line version of the last error, safe to surface in chat."""
        if not self.last_error:
            return "No provider error was recorded."

        error_type = self.last_error.get("type", "Error")
        model = self.last_error.get("model", "unknown model")
        message = str(self.last_error.get("message", "")).strip()
        first_line = message.splitlines()[0] if message else ""
        summary = f"{error_type} from {model}"
        if first_line:
            summary = f"{summary}: {first_line}"
        if len(summary) > max_length:
            summary = summary[:max_length - 3].rstrip() + "..."
        return summary

    def supports_vision(self, model: str) -> bool:
        if not model:
            return False

        cache_key = ("vision", model.lower())
        if cache_key in self._capability_cache:
            return self._capability_cache[cache_key]

        try:
            if litellm.supports_vision(model=model):
                self._capability_cache[cache_key] = True
                return True
        except Exception as e:
            logging.debug(f"LiteLLM vision detection failed for model {model}: {e}")

        supports = self._ollama_model_has_capability(model, "vision")
        if supports is None:
            return False

        self._capability_cache[cache_key] = supports
        return supports

    def prefers_inline_image_data(self, model: str) -> bool:
        """Return True when the provider works best with base64 image data."""
        return self._ollama_model_name(model) is not None

    def _ollama_model_name(self, model: str):
        if not model:
            return None

        model_s = model.strip()
        model_l = model_s.lower()
        for prefix in ("ollama_chat/", "ollama/"):
            if model_l.startswith(prefix):
                return model_s[len(prefix):]
        return None

    def _ollama_model_has_capability(self, model: str, capability: str) -> bool | None:
        ollama_model = self._ollama_model_name(model)
        if not ollama_model:
            return False

        cache_key = ("ollama", ollama_model.lower(), capability.lower())
        if cache_key in self._capability_cache:
            return self._capability_cache[cache_key]

        api_base = (
            os.getenv("OLLAMA_API_BASE")
            or os.getenv("OLLAMA_HOST")
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        if not api_base.startswith(("http://", "https://")):
            api_base = f"http://{api_base}"
        if api_base.endswith("/v1"):
            api_base = api_base[:-3]
        parsed_api_base = urlparse(api_base)
        if parsed_api_base.scheme not in {"http", "https"} or not parsed_api_base.netloc:
            logging.debug("Skipping Ollama capability inspection for invalid API base: %s", api_base)
            return None

        try:
            payload = json.dumps({"model": ollama_model}).encode("utf-8")
            request = urllib.request.Request(
                f"{api_base}/api/show",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # api_base is restricted to http/https above before urllib receives it.
            with urllib.request.urlopen(request, timeout=2) as response:  # nosec B310
                model_info = json.load(response)
            capabilities = model_info.get("capabilities") or []
            supports = capability.lower() in {str(item).lower() for item in capabilities}
            self._capability_cache[cache_key] = supports
            return supports
        except Exception as e:
            logging.debug(f"Could not inspect Ollama model capabilities for {ollama_model}: {e}")
            # Cache the failure so an unreachable Ollama can't block the event loop
            # (~2s synchronous probe) on every attachment.
            self._capability_cache[cache_key] = False
            return None

    def supports_audio(self, model: str) -> bool:
        # LiteLLM doesn't have a direct audio support check, so we can maintain a list
        # or assume models with vision might support audio in the future.
        # For now, let's check for known models.
        known_audio_models = ["whisper-1"] # This is for transcription, not general audio input
        return model in known_audio_models

    def supports_pdf(self, model: str) -> bool:
        # LiteLLM doesn't have a direct pdf support check.
        # This would typically be handled by converting PDF to text/images.
        # For now, return False as most models don't directly support PDF input
        return False

    def supports_web_search(self, model: str) -> bool:
        # Use LiteLLM's built-in web search support detection
        try:
            return litellm.supports_web_search(model=model)
        except Exception as e:
            logging.warning(f"Error checking web search support for model {model}: {e}")
            return False

    def get_model_capabilities(self, model: str) -> dict:
        """Get a summary of model capabilities for debugging/info purposes."""
        return {
            "vision": self.supports_vision(model),
            "audio": self.supports_audio(model),
            "pdf": self.supports_pdf(model),
            "web_search": self.supports_web_search(model)
        }

    async def cleanup(self):
        """Clean up any resources used by the LLM provider."""
        try:
            # Cancel any pending rate limiter tasks
            if hasattr(self, 'main_llm_rate_limiter'):
                # Clean up semaphores if needed
                pass
            if hasattr(self, 'decision_llm_rate_limiter'):
                # Clean up semaphores if needed  
                pass
            
            # Clean up LiteLLM resources
            try:
                # Attempt to clean up LiteLLM's logging worker if it exists
                if hasattr(litellm, '_logging_worker') and litellm._logging_worker:
                    litellm._logging_worker.cancel()
                    # Give it a moment to cancel gracefully
                    await asyncio.sleep(0.1)
                
                # Clean up any other LiteLLM resources
                if hasattr(litellm, 'cleanup'):
                    await litellm.cleanup()
                
            except Exception as litellm_error:
                # Don't let LiteLLM cleanup errors prevent overall cleanup
                logging.warning(f"LiteLLM cleanup warning (non-critical): {litellm_error}")
            
            logging.info("LLM provider cleanup completed")
        except Exception as e:
            logging.error(f"Error during LLM provider cleanup: {e}")
