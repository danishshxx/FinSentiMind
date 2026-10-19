"""Tests for refresh_all orchestration status reporting (TASK 104 patch)."""
from unittest.mock import patch

import pytest

from ml.preprocessing.refresh_all import refresh_all


# Common patch targets — mock every external dependency so no real I/O happens.
_DERIVED_PATCHES = [
    "ml.preprocessing.refresh_all.load_prices_clean",
    "ml.preprocessing.refresh_all.load_prices_for_calendar",
    "ml.preprocessing.refresh_all.derive_trading_calendar",
    "ml.preprocessing.refresh_all.save_calendar",
    "ml.preprocessing.refresh_all.build_daily_sentiment_dataset",
]

_NEWS_PATCHES = [
    "app.jobs.run_news_scraper.run_news_pipeline",
    "app.jobs.run_sentiment_pipeline.run_sentiment_analysis",
]


def _patch_all(**overrides):
    """Stack patches for all dependencies. Overrides can set side_effect."""
    from contextlib import ExitStack
    stack = ExitStack()
    for target in _DERIVED_PATCHES + _NEWS_PATCHES + [
        "ml.preprocessing.refresh_all.run_backfill"
    ]:
        patcher = patch(target, side_effect=overrides.get(target))
        stack.enter_context(patcher)
    return stack


def test_refresh_success_returns_zero():
    with _patch_all():
        code = refresh_all(skip_news=False, skip_backfill=False)
    assert code == 0


def test_refresh_skip_all_flags_success():
    with _patch_all():
        code = refresh_all(skip_news=True, skip_backfill=True)
    assert code == 0


def test_refresh_news_failure_returns_partial():
    with _patch_all(**{
        "app.jobs.run_news_scraper.run_news_pipeline": RuntimeError("news down"),
    }):
        code = refresh_all(skip_news=False, skip_backfill=False)
    assert code == 1  # PARTIAL


def test_refresh_backfill_failure_returns_partial():
    with _patch_all(**{
        "ml.preprocessing.refresh_all.run_backfill": RuntimeError("yf down"),
    }):
        code = refresh_all(skip_news=False, skip_backfill=False)
    assert code == 1  # PARTIAL


def test_refresh_derived_failure_returns_failed():
    with _patch_all(**{
        "ml.preprocessing.refresh_all.load_prices_clean": RuntimeError("db down"),
    }):
        code = refresh_all(skip_news=False, skip_backfill=False)
    assert code == 2  # FAILED


def test_refresh_partial_with_skip_flags():
    """Even with --skip-backfill, a news failure yields PARTIAL."""
    with _patch_all(**{
        "app.jobs.run_news_scraper.run_news_pipeline": RuntimeError("news down"),
    }):
        code = refresh_all(skip_news=False, skip_backfill=True)
    assert code == 1