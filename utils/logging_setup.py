"""App-wide logging. Debug mode raises the console to DEBUG."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

_console: Optional[logging.Handler] = None
_configured = False


def debug_requested() -> bool:
    if "--debug" in sys.argv:
        return True
    val = os.getenv("HINDSIGHT_DEBUG", "")
    return val.strip().lower() in ("1", "true", "yes", "on")


def configure_logging() -> None:
    global _console, _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
        datefmt="%H:%M:%S",
    )

    _console = logging.StreamHandler(sys.stdout)
    _console.setLevel(logging.DEBUG if debug_requested() else logging.INFO)
    _console.setFormatter(fmt)
    root.addHandler(_console)

    log_path = Path.home() / ".privacy_llm_assistant" / "app.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_h = logging.FileHandler(log_path, encoding="utf-8")
        file_h.setLevel(logging.DEBUG)
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
    except OSError as e:
        _console.setLevel(logging.DEBUG)
        logging.warning(f"Could not create {log_path}: {e}")

    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging to %s (console=%s)",
        log_path,
        logging.getLevelName(_console.level),
    )


def set_debug_logging(enabled: bool) -> None:
    """Raise or lower console verbosity. File stays at DEBUG either way."""
    global _console
    if _console is None:
        configure_logging()
    if _console is not None:
        _console.setLevel(logging.DEBUG if enabled else logging.INFO)
    logging.info("Console debug logging %s", "ON" if enabled else "OFF")
