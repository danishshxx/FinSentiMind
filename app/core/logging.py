import logging
import sys
from typing import Optional


# --- Constants --------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HANDLER_NAME = "_finsentimind_stream"
_DEFAULT_LOGGER_NAME = "finsentimind"


# --- Module state -----------------------------------------------------------

_configured: bool = False


# --- Level resolution -------------------------------------------------------

def _resolve_level(level: Optional[str] = None) -> int:
    """
    Resolve a level into a logging level constant (int).

    Priority:
        1. Explicit `level` argument (str or int)
        2. LOG_LEVEL from app.core.config (soft import)
        3. Fallback: INFO

    Raises nothing — returns INFO on any failure.
    """
    if level is None:
        try:
            from app.core.config import get_settings
            level = get_settings().log_level
        except Exception:
            level = "INFO"

    if isinstance(level, int):
        return level

    resolved = logging.getLevelName(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.INFO


# --- Public API -------------------------------------------------------------

def setup_logging(level: Optional[str] = None) -> None:
    """
    Configure the root logger. Idempotent — subsequent calls are no-ops.

    Args:
        level: Optional explicit level ('DEBUG', 'INFO', etc.) or int.
            If None, reads LOG_LEVEL from app.core.config (fallback INFO).
    """
    global _configured
    if _configured:
        return

    resolved = _resolve_level(level)
    root = logging.getLogger()
    root.setLevel(resolved)

    # --- Handler registration (guarded) ---
    already_present = any(
        getattr(h, "name", None) == _HANDLER_NAME for h in root.handlers
    )
    if not already_present:
        handler = logging.StreamHandler(sys.stdout)
        handler.name = _HANDLER_NAME
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)

    # --- Third-party logger silencing (independent concern) ---
    # httpx/httpcore log full HTTP request URLs at INFO — too verbose.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Return a named logger.

    Does NOT call setup_logging(). Caller is responsible for invoking
    setup_logging() at the application entry point.

    Args:
        name: Logger name (typically __name__). Defaults to 'finsentimind'.
    """
    return logging.getLogger(name or _DEFAULT_LOGGER_NAME)


def reset_logging() -> None:
    """
    Remove FinSentiMind's StreamHandler and reset the configured flag.

    For tests only. Does NOT touch handlers installed by pytest or
    external libraries.
    """
    global _configured
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "name", None) == _HANDLER_NAME:
            root.removeHandler(h)
    _configured = False