"""
Base LLM Provider

Abstract base class defining the interface for all LLM providers.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from PIL import Image
import json


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._model_name = None
    
    @abstractmethod
    def send_message(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a message to the LLM.
        
        Args:
            text: Text message
            images: List of PIL Images
            audio_file: Path to audio file
            system_prompt: System prompt/instructions
        
        Returns:
            Dict with 'response' and 'metadata' keys
        """
        pass
    
    @abstractmethod
    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ):
        """
        Stream response from LLM (generator).
        
        Yields:
            Text chunks as they arrive
        """
        pass
    
    def parse_structured_output(self, response_text: str) -> Dict[str, Any]:
        """
        Parse structured JSON output from LLM response.
        
        Args:
            response_text: Raw response text
        
        Returns:
            Parsed JSON dict, or {'raw': response_text} if parsing fails
        """
        try:
            # Try to extract JSON from markdown code blocks
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.find("```", start)
                json_str = response_text[start:end].strip()
                return json.loads(json_str)
            
            # Try to parse as pure JSON
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Return raw text if not JSON
            return {"raw": response_text}
    
    @property
    def model_name(self) -> str:
        """Get the current model name"""
        return self._model_name or "Unknown"
