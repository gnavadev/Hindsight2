"""
LLM Exception Hierarchy

Structured exceptions for each stage of the smart provider pipeline.
"""

from typing import Optional


class LLMError(Exception):
    """Base exception for all LLM-related errors."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        self.original_error = original_error
        super().__init__(message)


class LLMConnectionError(LLMError):
    """Failed to connect to the LLM API (network, auth, rate-limit)."""
    pass


class LLMExtractionError(LLMError):
    """Step 1 failed: could not classify or extract problem info from image."""
    pass


class LLMGenerationError(LLMError):
    """Step 2 failed: could not generate a solution."""
    pass


class LLMFormattingError(LLMError):
    """Post-processing failed: could not format the solution into markdown."""
    pass
