"""
Gemini LLM Provider

Integration with Google's Gemini API (2.0 Flash)
Supports multi-modal inputs and structured outputs.
"""

from typing import List, Dict, Any, Optional
from PIL import Image
import google.generativeai as genai
from llm.base_provider import BaseLLMProvider
import io


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider"""
    
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash-lite"):
        super().__init__(api_key)
        self._model_name = model_name
        
        # Configure Gemini
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        
        print(f"✓ Gemini provider initialized: {model_name}")
    
    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send message to Gemini"""
        try:
            # Build prompt parts
            parts = []
            
            # Add system prompt if provided
            if system_prompt:
                parts.append(f"System instructions: {system_prompt}\n\n")
            
            # Add text
            if text:
                parts.append(text)
            
            # Add images
            if images:
                for img in images:
                    parts.append(img)
            
            # Add audio (Gemini supports audio via File API)
            if audio_file:
                # Upload audio file
                audio_part = genai.upload_file(audio_file)
                parts.append(audio_part)
            
            # Generate response
            response = self.model.generate_content(parts)
            
            return {
                'response': response.text,
                'metadata': {
                    'model': self._model_name,
                    'finish_reason': response.candidates[0].finish_reason.name if response.candidates else None
                }
            }
        except Exception as e:
            print(f"Gemini error: {e}")
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
        """Stream response from Gemini"""
        try:
            # Build prompt parts
            parts = []
            
            if system_prompt:
                parts.append(f"System instructions: {system_prompt}\n\n")
            
            if text:
                parts.append(text)
            
            if images:
                for img in images:
                    parts.append(img)
            
            if audio_file:
                audio_part = genai.upload_file(audio_file)
                parts.append(audio_part)
            
            # Stream response
            response = self.model.generate_content(parts, stream=True)
            
            for chunk in response:
                if chunk.text:
                    yield chunk.text
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
            parts = []
            
            # Add schema instruction
            schema_str = f"\n\nRespond ONLY with valid JSON matching this schema:\n```json\n{json_schema}\n```"
            parts.append(text + schema_str)
            
            if images:
                for img in images:
                    parts.append(img)
            
            response = self.model.generate_content(parts)
            
            # Parse JSON from response
            return self.parse_structured_output(response.text)
        except Exception as e:
            print(f"Gemini JSON output error: {e}")
            return {'error': str(e)}
