import os
from dataclasses import dataclass, field
from typing import List, Optional


# --- Soft dotenv loading ----------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(override=False)
except ImportError:
    pass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment."""

    # Supabase (required)
    supabase_url: str
    supabase_key: str

    # Telegram (optional)
    telegram_api_id: Optional[int] = None
    telegram_api_hash: Optional[str] = None
    telegram_channels: List[str] = field(default_factory=list)

    # Stockbit (optional)
    stockbit_bearer_token: Optional[str] = None

    # Logging
    log_level: str = "INFO"

    @property
    def telegram_configured(self) -> bool:
        return bool(
            self.telegram_api_id
            and self.telegram_api_hash
            and self.telegram_channels
        )

    @property
    def stockbit_configured(self) -> bool:
        return bool(self.stockbit_bearer_token)

    def __repr__(self) -> str:
        return (
            f"Settings("
            f"supabase_url={self.supabase_url!r}, "
            f"supabase_key='***', "
            f"telegram_api_id={self.telegram_api_id}, "
            f"telegram_api_hash={'***' if self.telegram_api_hash else None}, "
            f"telegram_channels={self.telegram_channels}, "
            f"stockbit_bearer_token={'***' if self.stockbit_bearer_token else None}, "
            f"log_level={self.log_level!r}"
            f")"
        )


# --- Env parsing helpers ----------------------------------------------------

def _require(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ConfigError(f"Missing required environment variable: {name}")
    return value.strip()


def _optional(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _optional_int(name: str) -> Optional[int]:
    value = _optional(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as e:
        raise ConfigError(
            f"Environment variable {name} must be an integer, got: {value!r}"
        ) from e


def _optional_list(name: str) -> List[str]:
    value = _optional(name)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


# --- Public API -------------------------------------------------------------

def load_settings() -> Settings:
    """Load settings from environment. Raises ConfigError on invalid config."""
    return Settings(
        supabase_url=_require("SUPABASE_URL"),
        supabase_key=_require("SUPABASE_KEY"),
        telegram_api_id=_optional_int("TELEGRAM_API_ID"),
        telegram_api_hash=_optional("TELEGRAM_API_HASH"),
        telegram_channels=_optional_list("TELEGRAM_CHANNELS"),
        stockbit_bearer_token=_optional("STOCKBIT_BEARER_TOKEN"),
        log_level=(_optional("LOG_LEVEL") or "INFO").upper(),
    )


_settings_cache: Optional[Settings] = None


def get_settings() -> Settings:
    """Return cached Settings. Loads on first call."""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = load_settings()
    return _settings_cache


def reset_settings_cache() -> None:
    """Clear cache. Used in tests."""
    global _settings_cache
    _settings_cache = None