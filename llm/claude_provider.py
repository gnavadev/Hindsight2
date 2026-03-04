"""
Claude LLM Provider

Integration with Anthropic's Claude API (3.5 Sonnet)
Supports multi-modal inputs and structured outputs.
"""

import logging
from typing import List, Dict, Any, Optional, Generator
from PIL import Image
import anthropic
from llm.base_provider import BaseLLMProvider

import base64
import io

logger = logging.getLogger(__name__)


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude API provider"""

    def __init__(self, api_key: str, model_name: str = "claude-3-5-sonnet-20241022"):
        super().__init__(api_key)
        self._model_name = model_name

        if not api_key or not api_key.strip():
            logger.error("Claude API key is empty or None")
            raise ValueError("Claude API key is empty or None.")

        # Initialize Claude client
        try:
            self.client = anthropic.Anthropic(api_key=api_key)
            logger.info(f"Claude provider initialized: {model_name}")
        except Exception as e:
            logger.critical(f"Failed to initialize Claude client: {e}")
            raise

    def _image_to_base64(self, image: Image.Image) -> tuple[str, str]:
        """Convert PIL Image to base64 string"""
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        img_bytes = buffer.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode('utf-8')
        return img_base64, "image/png"

    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send message to Claude"""
        try:
            # Build message content
            content = []

            # Add images first (Claude convention)
            if images:
                logger.debug(f"Attaching {len(images)} image(s) to Claude request")
                for img in images:
                    img_base64, media_type = self._image_to_base64(img)
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_base64
                        }
                    })

            # Add text
            if text:
                content.append({
                    "type": "text",
                    "text": text
                })

            # Note: Claude API doesn't support audio directly yet
            # We would need to transcribe first or use a different approach
            if audio_file:
                logger.warning(f"Audio file '{audio_file}' provided but Claude does not support native audio")
                content.append({
                    "type": "text",
                    "text": f"\n[Audio file provided: {audio_file}. Please note: audio processing requires transcription first.]"
                })

            # Create message
            message_params = {
                "model": self._model_name,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": content
                    }
                ]
            }

            # Add system prompt if provided
            if system_prompt:
                message_params["system"] = system_prompt

            logger.info(f"Sending request to Claude ({self._model_name})...")
            response = self.client.messages.create(**message_params)

            logger.info(
                f"Claude response received — stop_reason: {response.stop_reason}, "
                f"tokens: {response.usage.input_tokens}in/{response.usage.output_tokens}out"
            )

            return {
                'response': response.content[0].text,
                'metadata': {
                    'model': self._model_name,
                    'stop_reason': response.stop_reason,
                    'usage': {
                        'input_tokens': response.usage.input_tokens,
                        'output_tokens': response.usage.output_tokens
                    }
                }
            }
        except anthropic.AuthenticationError as e:
            logger.error(f"Claude authentication failed — check your API key: {e}")
            return {
                'response': "Authentication failed. Please check your Claude API key.",
                'metadata': {'error': str(e)}
            }
        except anthropic.RateLimitError as e:
            logger.warning(f"Claude rate limit hit: {e}")
            return {
                'response': "Rate limit reached. Please wait a moment and try again.",
                'metadata': {'error': str(e)}
            }
        except Exception as e:
            logger.exception(f"Claude API error: {e}")
            return {
                'response': f"Error: {str(e)}",
                'metadata': {'error': str(e)}
            }

    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Generator[str, Any, None]:
        """Stream response from Claude"""
        try:
            # Build message content
            content = []

            if images:
                for img in images:
                    img_base64, media_type = self._image_to_base64(img)
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_base64
                        }
                    })

            if text:
                content.append({
                    "type": "text",
                    "text": text
                })

            message_params = {
                "model": self._model_name,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": content
                    }
                ]
            }

            if system_prompt:
                message_params["system"] = system_prompt

            # Stream response
            logger.info("Initiating Claude stream...")
            with self.client.messages.stream(**message_params) as stream:
                for text_chunk in stream.text_stream:
                    yield text_chunk
        except Exception as e:
            logger.exception(f"Claude streaming error: {e}")
            yield f"\n\nError: {str(e)}"

    def send_with_json_output(
        self,
        text: str,
        json_schema: Dict[str, Any],
        images: Optional[List[Image.Image]] = None
    ) -> Dict[str, Any]:
        """
        Send message and request JSON output matching schema.
        """
        try:
            # Add schema instruction to prompt
            schema_prompt = f"{text}\n\nRespond ONLY with valid JSON matching this schema:\n```json\n{json_schema}\n```"

            logger.debug("Sending structured JSON request to Claude")
            response = self.send_message(schema_prompt, images=images)

            # Parse JSON from response
            return self.parse_structured_output(response['response'])
        except Exception as e:
            logger.exception(f"Claude JSON output error: {e}")
            return {'error': str(e)}
