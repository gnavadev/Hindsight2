"""
TOON Format Helper

Utilities for encoding data in TOON format for efficient LLM token usage.
"""

from typing import Any, Dict, List
import json


def encode_toon(data: Any) -> str:
    """
    Encode data to TOON format.
    Falls back to JSON if toon-format package is not available or encoding fails.
    
    Args:
        data: Python data structure (dict, list, etc.)
    
    Returns:
        TOON formatted string
    """
    try:
        from toon_format import encode
        return encode(data)
    except ImportError:
        print("Warning: toon-format package not available, using JSON")
        return json.dumps(data, indent=2)
    except Exception as e:
        print(f"TOON encoding error: {e}, falling back to JSON")
        return json.dumps(data, indent=2)


def decode_toon(toon_str: str) -> Any:
    """
    Decode TOON format string to Python data.
    
    Args:
        toon_str: TOON formatted string
    
    Returns:
        Python data structure
    """
    try:
        from toon_format import decode
        return decode(toon_str)
    except ImportError:
        # Try JSON if TOON not available
        return json.loads(toon_str)
    except Exception as e:
        print(f"TOON decoding error: {e}")
        return None


def format_context_toon(context: Dict[str, Any]) -> str:
    """
    Format conversation context in TOON for efficient LLM prompts.
    
    Args:
        context: Dict with chat history, files, metadata
    
    Returns:
        TOON formatted context string
    """
    try:
        # Structure context for TOON efficiency
        formatted_context = {
            "conversation": {
                "messages": context.get("messages", []),
                "message_count": len(context.get("messages", []))
            },
            "files": context.get("files", []),
            "metadata": {
                "timestamp": context.get("timestamp"),
                "user_id": context.get("user_id", "default")
            }
        }
        
        return encode_toon(formatted_context)
    except Exception as e:
        print(f"Context formatting error: {e}")
        return json.dumps(context, indent=2)


def create_llm_prompt_with_context(
    user_message: str,
    context: Dict[str, Any] = None,
    use_toon: bool = True
) -> str:
    """
    Create an LLM prompt with optional context in TOON format.
    
    Args:
        user_message: The main user message
        context: Optional context data
        use_toon: Whether to use TOON format
    
    Returns:
        Formatted prompt string
    """
    if not context:
        return user_message
    
    if use_toon:
        context_str = format_context_toon(context)
        return f"""Context (TOON format):
```toon
{context_str}
```

User message:
{user_message}"""
    else:
        context_str = json.dumps(context, indent=2)
        return f"""Context (JSON):
```json
{context_str}
```

User message:
{user_message}"""
