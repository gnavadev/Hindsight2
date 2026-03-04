"""
Gemini LLM Provider

Integration with Google's Gemini API (2.0 Flash)
Supports multi-modal inputs and structured outputs.
"""

import logging
from typing import List, Dict, Any, Optional, Generator
from PIL import Image
from google import genai
from google.genai import types
from llm.base_provider import BaseLLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider"""

    def __init__(self, api_key: str, model_name: str = "gemini-3.1-flash-lite-preview"):
        super().__init__(api_key)
        self._model_name = model_name

        # Strict validation to catch empty keys before hitting the API
        if not api_key or not api_key.strip():
            logger.error("API key initialization failed: Key is empty.")
            raise ValueError("API key is empty or None. Restart your IDE or terminal to load the new environment variable.")

        # Configure new Gemini Client
        try:
            self.client = genai.Client(api_key=api_key)
            logger.info(f"Gemini provider initialized successfully with model: {model_name}")
        except Exception as e:
            logger.critical(f"Failed to initialize Gemini Client: {e}")
            raise

    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send message to Gemini"""
        try:
            parts: List[Any] = []

            if text:
                parts.append(text)

            if images:
                logger.debug(f"Attaching {len(images)} image(s) to Gemini request")
                parts.extend(images)

            if audio_file:
                logger.info(f"Uploading audio file: {audio_file}")
                audio_part = self.client.files.upload(file=audio_file)
                parts.append(audio_part)

            config = types.GenerateContentConfig(
                system_instruction=system_prompt
            ) if system_prompt else None

            logger.info(f"Sending message to Gemini API ({self._model_name}, {len(parts)} part(s))...")
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=parts,
                config=config
            )

            finish_reason = None
            if response.candidates and response.candidates[0].finish_reason:
                finish_reason = response.candidates[0].finish_reason.name

            logger.info(f"Gemini response received — finish_reason: {finish_reason}")

            return {
                'response': response.text or "",
                'metadata': {
                    'model': self._model_name,
                    'finish_reason': finish_reason
                }
            }
        except Exception as e:
            error_type = type(e).__name__
            logger.exception(f"Gemini API Error ({error_type}): {e}")

            # Provide user-friendly messages for common errors
            user_msg = f"Error connecting to Gemini: {str(e)}"
            if "API key" in str(e) or "401" in str(e) or "403" in str(e):
                user_msg = "Gemini authentication failed. Please check your API key."
            elif "429" in str(e) or "rate" in str(e).lower():
                user_msg = "Gemini rate limit reached. Please wait a moment and try again."
            elif "timeout" in str(e).lower():
                user_msg = "Gemini request timed out. Please try again."

            return {
                'response': user_msg,
                'metadata': {'error': str(e), 'error_type': error_type}
            }

    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Generator[str, Any, None]:
        """Stream response from Gemini"""
        try:
            parts: List[Any] = []

            if text:
                parts.append(text)

            if images:
                parts.extend(images)

            if audio_file:
                audio_part = self.client.files.upload(file=audio_file)
                parts.append(audio_part)

            config = types.GenerateContentConfig(
                system_instruction=system_prompt
            ) if system_prompt else None

            logger.info("Initiating Gemini stream...")
            response = self.client.models.generate_content_stream(
                model=self._model_name,
                contents=parts,
                config=config
            )

            for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logger.exception(f"Gemini API Error in stream_response: {e}")
            yield f"\n\nError connecting to Gemini: {str(e)}"

    def send_with_json_output(
        self,
        text: str,
        json_schema: Dict[str, Any],
        images: Optional[List[Image.Image]] = None
    ) -> Dict[str, Any]:
        """
        Send message and request JSON output matching schema natively.
        """
        try:
            parts: List[Any] = [text]

            if images:
                parts.extend(images)

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=json_schema,
                temperature=0.1  # Low temperature to force strict structure adherence
            )

            logger.info("Sending JSON structured request to Gemini...")
            logger.debug(f"Schema keys: {list(json_schema.get('properties', {}).keys())}")
            response = self.client.models.generate_content(
                model=self._model_name,
                contents=parts,
                config=config
            )

            raw_text = response.text or ""
            logger.debug(f"JSON response length: {len(raw_text)} chars")
            return self.parse_structured_output(raw_text)
        except Exception as e:
            logger.exception(f"Gemini JSON output error: {e}")
            return {'error': str(e)}