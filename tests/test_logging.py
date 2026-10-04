"""Tests for centralized logging."""
import logging
import os
from unittest.mock import patch

import pytest

from app.core.config import reset_settings_cache
from app.core.logging import (
    get_logger,
    reset_logging,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _isolate_logging_and_config():
    """Reset logging + config cache around every test."""
    reset_logging()
    reset_settings_cache()
    yield
    reset_logging()
    reset_settings_cache()


def _minimal_env() -> dict:
    return {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key-not-real",
    }


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------

def test_get_logger_returns_named_logger():
    logger = get_logger("my.module")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "my.module"


def test_get_logger_default_name():
    logger = get_logger()
    assert logger.name == "finsentimind"


def test_get_logger_does_not_configure_handlers():
    """get_logger() must not install handlers as a side effect."""
    reset_logging()
    before = len(logging.getLogger().handlers)
    get_logger("x")
    after = len(logging.getLogger().handlers)
    assert before == after


# ---------------------------------------------------------------------------
# setup_logging — idempotency
# ---------------------------------------------------------------------------

def test_setup_logging_creates_handler():
    setup_logging(level="INFO")
    root = logging.getLogger()
    names = [getattr(h, "name", None) for h in root.handlers]
    assert "_finsentimind_stream" in names


def test_setup_logging_is_idempotent():
    setup_logging(level="INFO")
    count_1 = sum(
        1 for h in logging.getLogger().handlers
        if getattr(h, "name", None) == "_finsentimind_stream"
    )
    setup_logging(level="DEBUG")  # second call — should be a no-op
    count_2 = sum(
        1 for h in logging.getLogger().handlers
        if getattr(h, "name", None) == "_finsentimind_stream"
    )
    assert count_1 == count_2 == 1


def test_setup_logging_preserves_existing_handlers():
    """Handlers from pytest/external libs must not be removed."""
    root = logging.getLogger()
    external = logging.NullHandler()
    external.name = "_external_test_handler"
    root.addHandler(external)
    try:
        setup_logging(level="INFO")
        names = [getattr(h, "name", None) for h in root.handlers]
        assert "_external_test_handler" in names
        assert "_finsentimind_stream" in names
    finally:
        root.removeHandler(external)


# ---------------------------------------------------------------------------
# Level resolution
# ---------------------------------------------------------------------------

def test_setup_logging_explicit_level():
    setup_logging(level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_reads_level_from_config():
    with patch.dict(
        os.environ, {**_minimal_env(), "LOG_LEVEL": "WARNING"}, clear=True
    ):
        reset_settings_cache()
        setup_logging()  # no explicit level → read from config
        assert logging.getLogger().level == logging.WARNING


def test_setup_logging_defaults_to_info_on_bad_level():
    setup_logging(level="NOT_A_LEVEL")
    assert logging.getLogger().level == logging.INFO


# ---------------------------------------------------------------------------
# Secret safety
# ---------------------------------------------------------------------------

def test_settings_repr_masks_secrets_in_logs(caplog):
    """Logging a Settings object must not expose secrets."""
    from app.core.config import Settings

    s = Settings(
        supabase_url="https://x.supabase.co",
        supabase_key="MY-SECRET-KEY",
        stockbit_bearer_token="MY-SECRET-TOKEN",
    )
    with caplog.at_level(logging.INFO):
        logging.getLogger("test.secrets").info("Loaded: %r", s)

    assert "MY-SECRET-KEY" not in caplog.text
    assert "MY-SECRET-TOKEN" not in caplog.text
    assert "***" in caplog.text


# ---------------------------------------------------------------------------
# reset_logging
# ---------------------------------------------------------------------------

def test_reset_logging_removes_only_our_handler():
    setup_logging(level="INFO")
    external = logging.NullHandler()
    external.name = "_external_reset_test"
    logging.getLogger().addHandler(external)
    try:
        reset_logging()
        names = [
            getattr(h, "name", None) for h in logging.getLogger().handlers
        ]
        assert "_finsentimind_stream" not in names
        assert "_external_reset_test" in names
    finally:
        logging.getLogger().removeHandler(external)