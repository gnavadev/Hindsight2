"""
Claude LLM Provider

Integration with Anthropic's Claude API (3.5 Sonnet)
Supports multi-modal inputs and structured outputs.
"""

from typing import List, Dict, Any, Optional
from PIL import Image
import anthropic
from llm.base_provider import BaseLLMProvider
import base64
import io


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude API provider"""
    
    def __init__(self, api_key: str, model_name: str = "claude-3-5-sonnet-20241022"):
        super().__init__(api_key)
        self._model_name = model_name
        
        # Initialize Claude client
        self.client = anthropic.Anthropic(api_key=api_key)
        
        print(f"✓ Claude provider initialized: {model_name}")
    
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
            
            response = self.client.messages.create(**message_params)
            
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
        except Exception as e:
            print(f"Claude error: {e}")
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
    ):
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
            with self.client.messages.stream(**message_params) as stream:
                for text_chunk in stream.text_stream:
                    yield text_chunk
        except Exception as e:
            yield f"\n\nError: {str(e)}"
    
    def send_with_json_output(
        self,
        text: str,
        json_schema: Dict[str, Any],
        images: Optional[List[Image.Image]] = None
    ) -> Dict[str, Any]:
        """
        Send message and request JSON output matching schema.
        
        Args:
            text: Prompt text
            json_schema: JSON schema for output
            images: Optional images
        
        Returns:
            Parsed JSON response
        """
        try:
            # Add schema instruction to prompt
            schema_prompt = f"{text}\n\nRespond ONLY with valid JSON matching this schema:\n```json\n{json_schema}\n```"
            
            response = self.send_message(schema_prompt, images=images)
            
            # Parse JSON from response
            return self.parse_structured_output(response['response'])
        except Exception as e:
            print(f"Claude JSON output error: {e}")
            return {'error': str(e)}
