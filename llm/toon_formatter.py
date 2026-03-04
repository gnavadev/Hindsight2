"""
TOON Format Helper

Utilities for encoding data in TOON format for efficient LLM token usage.
TOON achieves 30-60% token reduction compared to JSON for LLM prompts.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_toon_available: Optional[bool] = None  # cached after first check


def is_toon_available() -> bool:
    """Check whether the toon-format package is installed."""
    global _toon_available
    if _toon_available is None:
        try:
            import toon_format  # noqa: F401
            _toon_available = True
        except ImportError:
            _toon_available = False
    return _toon_available


def encode_toon(data: Any) -> str:
    """
    Encode data to TOON format.
    Falls back to JSON if toon-format package is not available or encoding fails.
    """
    try:
        from toon_format import encode
        result = encode(data)
        logger.debug(f"TOON encoded ({len(result)} chars)")
        return result
    except ImportError:
        logger.debug("toon-format package not available, utilizing JSON fallback.")
        return json.dumps(data, indent=2)
    except Exception as e:
        logger.error(f"TOON encoding error: {e}. Falling back to JSON.")
        return json.dumps(data, indent=2)


def decode_toon(toon_str: str) -> Any:
    """
    Decode TOON format string to Python data.
    """
    try:
        from toon_format import decode
        return decode(toon_str)
    except ImportError:
        logger.debug("toon-format package not available, utilizing JSON fallback.")
        return json.loads(toon_str)
    except Exception as e:
        logger.error(f"TOON decoding error: {e}")
        return None


def encode_problem_context(problem_info: Dict[str, Any], problem_type: str) -> str:
    """
    Encode Step 1 classification results into a rich, problem-type-aware 
    TOON context block for Step 2 (solution generation).

    This structures the context so the LLM gets maximum signal with minimum tokens.
    """
    context = {
        "classification": {
            "type": problem_type,
            "summary": problem_info.get("problem_summary", ""),
        },
        "details": problem_info.get("details", {}),
    }

    # Add type-specific hints to guide the solver
    if problem_type == "coding":
        context["output_expectations"] = {
            "required_fields": ["algorithm_steps", "code", "time_complexity", "space_complexity", "edge_cases"],
            "code_format": "raw code only, no markdown backticks, use \\n for newlines",
            "steps_format": "each step as a SEPARATE array element, never merge steps",
        }
    elif problem_type == "multiple_choice":
        context["output_expectations"] = {
            "required_fields": ["selected_option", "option_letter", "all_options", "explanation"],
            "answer_format": "state the letter and full text of the correct option",
        }
    elif problem_type == "math":
        context["output_expectations"] = {
            "required_fields": ["final_answer", "step_by_step", "formula_used"],
            "math_format": "show each step clearly, use plain text for math expressions",
        }
    else:  # general
        context["output_expectations"] = {
            "required_fields": ["answer", "reasoning"],
            "answer_format": "clear, concise answer followed by detailed reasoning",
        }

    return encode_toon(context)


def encode_extraction_hints(user_prompt: str) -> str:
    """
    Encode the extraction task instructions in TOON format for Step 1.
    Provides structured hints to the classifier about what to look for.
    """
    hints = {
        "task": "classify_and_extract",
        "user_note": user_prompt,
        "type_definitions": {
            "coding": "Code snippets, IDEs, terminal output, programming errors, algorithm problems",
            "multiple_choice": "Quizzes, exams, tests with lettered/numbered options (A/B/C/D)",
            "math": "Equations, calculus, geometry, algebra, word problems with numeric answers",
            "general": "Text questions, logic puzzles, reading comprehension, open-ended questions",
        },
        "extraction_rules": {
            "summarize": "1-2 sentence problem summary, do NOT transcribe the entire image",
            "details": "Extract key entities: language, options, variables, constraints",
        },
    }
    return encode_toon(hints)


def format_context_toon(context: Dict[str, Any]) -> str:
    """
    Format conversation context in TOON for efficient LLM prompts.
    """
    try:
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
        logger.error(f"Context formatting error: {e}")
        return json.dumps(context, indent=2)


def create_llm_prompt_with_context(
    user_message: str,
    context: Optional[Dict[str, Any]] = None,
    use_toon: bool = True
) -> str:
    """
    Create an LLM prompt with optional context in TOON format.
    """
    if not context:
        return user_message

    if use_toon:
        context_str = format_context_toon(context)
        return f"Context (TOON format):\n{context_str}\n\nUser Message: {user_message}"

    context_str = json.dumps(context, indent=2)
    return f"Context (JSON format):\n{context_str}\n\nUser Message: {user_message}"