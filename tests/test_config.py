"""Tests for centralized configuration."""
import os
from unittest.mock import patch

import pytest

from app.core.config import (
    ConfigError,
    get_settings,
    load_settings,
    reset_settings_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


def _minimal_env() -> dict:
    return {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key-not-real",
    }


def test_missing_supabase_url_raises():
    with patch.dict(os.environ, {"SUPABASE_KEY": "k"}, clear=True):
        with pytest.raises(ConfigError, match="SUPABASE_URL"):
            load_settings()


def test_missing_supabase_key_raises():
    with patch.dict(
        os.environ, {"SUPABASE_URL": "https://x.supabase.co"}, clear=True
    ):
        with pytest.raises(ConfigError, match="SUPABASE_KEY"):
            load_settings()


def test_empty_string_treated_as_missing():
    with patch.dict(
        os.environ,
        {"SUPABASE_URL": "   ", "SUPABASE_KEY": "k"},
        clear=True,
    ):
        with pytest.raises(ConfigError, match="SUPABASE_URL"):
            load_settings()


def test_minimal_valid_settings():
    with patch.dict(os.environ, _minimal_env(), clear=True):
        settings = load_settings()
        assert settings.supabase_url == "https://test.supabase.co"
        assert settings.supabase_key == "test-key-not-real"
        assert settings.log_level == "INFO"
        assert settings.telegram_api_id is None
        assert settings.telegram_channels == []


def test_telegram_configured_true():
    env = {
        **_minimal_env(),
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "abcdef",
        "TELEGRAM_CHANNELS": "@chan_a,@chan_b",
    }
    with patch.dict(os.environ, env, clear=True):
        s = load_settings()
        assert s.telegram_configured is True
        assert s.telegram_api_id == 12345
        assert s.telegram_channels == ["@chan_a", "@chan_b"]


def test_telegram_configured_false_when_incomplete():
    env = {**_minimal_env(), "TELEGRAM_API_ID": "12345"}
    with patch.dict(os.environ, env, clear=True):
        assert load_settings().telegram_configured is False


def test_stockbit_configured_true():
    env = {**_minimal_env(), "STOCKBIT_BEARER_TOKEN": "token"}
    with patch.dict(os.environ, env, clear=True):
        assert load_settings().stockbit_configured is True


def test_invalid_telegram_api_id_raises():
    env = {**_minimal_env(), "TELEGRAM_API_ID": "not-int"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="TELEGRAM_API_ID"):
            load_settings()


def test_log_level_uppercased():
    env = {**_minimal_env(), "LOG_LEVEL": "debug"}
    with patch.dict(os.environ, env, clear=True):
        assert load_settings().log_level == "DEBUG"


def test_get_settings_caches_instance():
    with patch.dict(os.environ, _minimal_env(), clear=True):
        assert get_settings() is get_settings()


def test_reset_cache_clears_instance():
    with patch.dict(os.environ, _minimal_env(), clear=True):
        s1 = get_settings()
        reset_settings_cache()
        s2 = get_settings()
        assert s1 is not s2


def test_repr_masks_secrets():
    env = {
        "SUPABASE_URL": "https://x.supabase.co",
        "SUPABASE_KEY": "SUPER-SECRET",
        "STOCKBIT_BEARER_TOKEN": "SECRET-TOKEN",
    }
    with patch.dict(os.environ, env, clear=True):
        text = repr(load_settings())
        assert "SUPER-SECRET" not in text
        assert "SECRET-TOKEN" not in text
        assert "***" in text