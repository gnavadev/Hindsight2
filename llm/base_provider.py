"""
Base LLM Provider

Abstract base class defining the interface for all LLM providers.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Generator
from PIL import Image

logger = logging.getLogger(__name__)

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
        """
        pass
    
    @abstractmethod
    def stream_response(
        self,
        text: str,
        images: Optional[List[Image.Image]] = None,
        audio_file: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Generator[str, Any, None]:
        """
        Stream response from LLM (generator).
        """
        pass
    
    def parse_structured_output(self, response_text: str) -> Dict[str, Any]:
        """
        Parse structured JSON output from LLM response reliably.
        """
        if not response_text:
            logger.warning("Received empty response text to parse.")
            return {"error": "Empty response from API"}

        try:
            # Try to extract JSON from markdown code blocks
            if "```json" in response_text:
                start = response_text.find("```json") + 7
                end = response_text.rfind("```")
                json_str = response_text[start:end].strip()
                return json.loads(json_str)
            
            # Fallback to direct JSON parsing
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e} | Raw text preview: {response_text[:150]}...")
            return {"raw": response_text, "error": f"JSON Parsing Error: {str(e)}"}
    
    @property
    def model_name(self) -> str:
        """Get the current model name"""
        return self._model_name or "Unknown"