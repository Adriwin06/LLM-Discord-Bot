# c:/Users/adri1/Documents/GitHub/LLM-Discord-Bot/bot/llm_provider.py
import litellm
from litellm import acompletion
import asyncio
import logging
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

    async def _rate_limit(self, limiter_type: str):
        """
        Apply rate limiting based on configuration.
        
        Args:
            limiter_type: Either 'main' or 'decision' to specify which rate limiter to use
        """
        if limiter_type == 'main':
            if not self.config.MAIN_LLM_RATE_LIMIT_ENABLED:
                return
            async with self.main_llm_rate_limiter:
                now = asyncio.get_event_loop().time()
                elapsed = now - self.main_llm_last_call
                delay = self.config.MAIN_LLM_RATE_LIMIT_SECONDS - elapsed
                if delay > 0:
                    await asyncio.sleep(delay)
                self.main_llm_last_call = asyncio.get_event_loop().time()
        elif limiter_type == 'decision':
            if not self.config.DECISION_LLM_RATE_LIMIT_ENABLED:
                return
            async with self.decision_llm_rate_limiter:
                now = asyncio.get_event_loop().time()
                elapsed = now - self.decision_llm_last_call
                delay = self.config.DECISION_LLM_RATE_LIMIT_SECONDS - elapsed
                if delay > 0:
                    await asyncio.sleep(delay)
                self.decision_llm_last_call = asyncio.get_event_loop().time()

    async def create_completion(self, model: str, messages: list, **kwargs):
        """
        Create a completion using the specified model.
        
        Automatically handles:
        - Rate limiting based on model type
        - API key selection based on model provider
        - Web search capabilities for supported models
        - JSON response format compatibility
        
        Args:
            model: Model identifier (e.g., 'gpt-4o', 'gemini/gemini-1.5-pro')
            messages: List of message dictionaries
            **kwargs: Additional parameters for the completion
            
        Returns:
            LiteLLM response object or None if error occurred
        """
        limiter_type = 'main' if model == self.config.MAIN_LLM_MODEL else 'decision'
        await self._rate_limit(limiter_type)
        self.last_error = None
        
        try:
            # Set API keys for providers
            litellm.api_key = self.config.OPENAI_API_KEY # default
            if model.startswith("gemini/"):
                litellm.gemini_api_key = self.config.GEMINI_API_KEY
            elif model.startswith("claude-"):
                litellm.anthropic_api_key = self.config.ANTHROPIC_API_KEY

            # Check if model supports web search and add web search options
            completion_kwargs = kwargs.copy()
            
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
            return response
        except Exception as e:
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
                    return response
                except Exception as retry_error:
                    self._record_error(model, retry_error)
                    logging.error(f"LiteLLM retry without web search failed for model {model}: {retry_error}")
                    return None

            self._record_error(model, e)
            logging.error(f"LiteLLM completion error for model {model}: {e}")
            return None

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

    def supports_vision(self, model: str) -> bool:
        # Check if LiteLLM has built-in support detection
        try:
            # LiteLLM may not have this function, so we'll implement our own logic
            vision_models = [
                "gpt-4-vision", "gpt-4o", "gpt-4o-mini", 
                "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash", 
                "gemini-2.5-flash", "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"
            ]
            return any(vm in model.lower() for vm in vision_models)
        except Exception:
            # Fallback: assume new Gemini models support vision
            if "gemini" in model.lower():
                return True
            return False

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
