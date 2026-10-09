"""Tests for daily sentiment aggregation (TASK 104)."""
import os
from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.core.config import get_settings, reset_settings_cache
from ml.preprocessing.daily_sentiment import (
    OUTPUT_COLUMNS,
    aggregate_daily_sentiment,
    align_news_to_trading_date,
    calculate_effective_available_at,
    classify_availability_quality,
)


JKT = ZoneInfo("Asia/Jakarta")
UTC = ZoneInfo("UTC")
CUTOFF = time(16, 0)

# Simple trading calendar for tests:
# Mon 2025-01-06 .. Fri 2025-01-10, then Mon 13, Tue 14
TRADING_DATES = {
    date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8),
    date(2025, 1, 9), date(2025, 1, 10),
    date(2025, 1, 13), date(2025, 1, 14),
}


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


def _minimal_env():
    return {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key",
    }


def _row(ticker, published, scraped, label="POSITIVE", score=1.0):
    """Build a raw news row with tz-aware ISO strings."""
    return {
        "ticker": ticker,
        "published_at": published,
        "scraped_at": scraped,
        "sentiment_label": label,
        "sentiment_score": score,
    }


def _df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CONFIG (tests 1-2)
# ---------------------------------------------------------------------------

def test_config_default_backfill_threshold():
    with patch.dict(os.environ, _minimal_env(), clear=True):
        s = get_settings()
        assert s.backfill_threshold_days == 7


def test_config_custom_backfill_threshold():
    env = {**_minimal_env(), "BACKFILL_THRESHOLD_DAYS": "3"}
    with patch.dict(os.environ, env, clear=True):
        s = get_settings()
        assert s.backfill_threshold_days == 3


# ---------------------------------------------------------------------------
# TIMESTAMP (tests 3-5)
# ---------------------------------------------------------------------------

def test_effective_available_at_picks_published_when_later():
    df = _df([_row(
        "BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-06T08:00:00+07:00",  # earlier
    )])
    out = calculate_effective_available_at(df, tz=JKT)
    eff = out["effective_available_at"].iloc[0]
    assert eff.hour == 10  # published wins


def test_effective_available_at_picks_scraped_when_later():
    df = _df([_row(
        "BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-06T10:05:00+07:00",
    )])
    out = calculate_effective_available_at(df, tz=JKT)
    eff = out["effective_available_at"].iloc[0]
    assert eff.hour == 10 and eff.minute == 5


def test_effective_available_at_timezone_conversion():
    """UTC input should convert to Jakarta tz."""
    df = _df([_row(
        "BBCA",
        published="2025-01-06T03:00:00+00:00",  # 10:00 WIB
        scraped="2025-01-06T03:05:00+00:00",
    )])
    out = calculate_effective_available_at(df, tz=JKT)
    eff = out["effective_available_at"].iloc[0]
    assert eff.hour == 10
    assert eff.minute == 5
    assert str(eff.tzinfo) in ("Asia/Jakarta", "WIB") or eff.utcoffset().total_seconds() == 7 * 3600


# ---------------------------------------------------------------------------
# QUALITY (tests 6-8)
# ---------------------------------------------------------------------------

def test_live_classification_small_delta():
    df = _df([_row(
        "BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-06T10:05:00+07:00",
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "live"


def test_reconstructed_classification_large_delta():
    df = _df([_row(
        "BBCA",
        published="2025-01-01T10:00:00+07:00",
        scraped="2025-01-15T10:00:00+07:00",  # 14 days later
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "reconstructed"


def test_quality_threshold_boundary_exactly_7_days():
    """Exactly 7 days -> live (<=)."""
    df = _df([_row(
        "BBCA",
        published="2025-01-01T10:00:00+07:00",
        scraped="2025-01-08T10:00:00+07:00",  # exactly 7 days
    )])
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    assert df["availability_quality"].iloc[0] == "live"


# ---------------------------------------------------------------------------
# ALIGNMENT (tests 9-12)
# ---------------------------------------------------------------------------

def test_alignment_before_cutoff_same_day():
    df = _df([_row("BBCA",
        published="2025-01-06T14:00:00+07:00",  # Mon, before 16:00
        scraped="2025-01-06T14:05:00+07:00")])
    df = calculate_effective_available_at(df, JKT)
    df = align_news_to_trading_date(df, TRADING_DATES, CUTOFF, JKT)
    assert df["trading_date"].iloc[0] == date(2025, 1, 6)


def test_alignment_after_cutoff_next_day():
    df = _df([_row("BBCA",
        published="2025-01-06T17:00:00+07:00",
        scraped="2025-01-06T17:05:00+07:00")])
    df = calculate_effective_available_at(df, JKT)
    df = align_news_to_trading_date(df, TRADING_DATES, CUTOFF, JKT)
    assert df["trading_date"].iloc[0] == date(2025, 1, 7)


def test_alignment_weekend_rolls_to_monday():
    df = _df([_row("BBCA",
        published="2025-01-11T10:00:00+07:00",  # Saturday
        scraped="2025-01-11T10:05:00+07:00")])
    df = calculate_effective_available_at(df, JKT)
    df = align_news_to_trading_date(df, TRADING_DATES, CUTOFF, JKT)
    assert df["trading_date"].iloc[0] == date(2025, 1, 13)


def test_alignment_holiday_rolls_to_next():
    # Remove Jan 7 from calendar -> article on Jan 7 maps to Jan 8
    dates = TRADING_DATES - {date(2025, 1, 7)}
    df = _df([_row("BBCA",
        published="2025-01-07T10:00:00+07:00",
        scraped="2025-01-07T10:05:00+07:00")])
    df = calculate_effective_available_at(df, JKT)
    df = align_news_to_trading_date(df, dates, CUTOFF, JKT)
    assert df["trading_date"].iloc[0] == date(2025, 1, 8)


# ---------------------------------------------------------------------------
# AGGREGATION (tests 13-18)
# ---------------------------------------------------------------------------

def _prep(rows):
    """Row -> full pipeline (effective_at + quality)."""
    df = _df(rows)
    df = calculate_effective_available_at(df, JKT)
    df = classify_availability_quality(df, threshold_days=7)
    df = align_news_to_trading_date(df, TRADING_DATES, CUTOFF, JKT)
    return df


def test_aggregate_single_article():
    df = _prep([_row("BBCA",
        published="2025-01-06T10:00:00+07:00",
        scraped="2025-01-06T10:00:00+07:00",
        label="POSITIVE", score=1.0)])
    out = aggregate_daily_sentiment(df)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["ticker"] == "BBCA"
    assert row["trading_date"] == date(2025, 1, 6)
    assert row["news_count"] == 1
    assert row["sentiment_mean"] == 1.0


def test_aggregate_multiple_articles_same_day():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 1.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:00:00+07:00", "POSITIVE", 1.0),
        _row("BBCA", "2025-01-06T14:00:00+07:00", "2025-01-06T14:00:00+07:00", "NEGATIVE", -1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert len(out) == 1
    assert out["news_count"].iloc[0] == 3


def test_positive_ratio_calculation():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 1.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:00:00+07:00", "NEGATIVE", -1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert out["positive_ratio"].iloc[0] == pytest.approx(0.5)


def test_negative_ratio_calculation():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "NEGATIVE", -1.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:00:00+07:00", "NEGATIVE", -1.0),
        _row("BBCA", "2025-01-06T12:00:00+07:00", "2025-01-06T12:00:00+07:00", "POSITIVE", 1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert out["negative_ratio"].iloc[0] == pytest.approx(2 / 3)


def test_sentiment_mean_calculation():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 2.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:00:00+07:00", "NEGATIVE", -1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert out["sentiment_mean"].iloc[0] == pytest.approx(0.5)


def test_sentiment_std_calculation():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 2.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:00:00+07:00", "NEGATIVE", 0.0),
        _row("BBCA", "2025-01-06T12:00:00+07:00", "2025-01-06T12:00:00+07:00", "POSITIVE", 1.0),
    ])
    out = aggregate_daily_sentiment(df)
    # std of [2, 0, 1] with ddof=1 -> ~1.0
    assert out["sentiment_std"].iloc[0] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# EDGE CASES (tests 19-21)
# ---------------------------------------------------------------------------

def test_aggregate_returns_empty_with_schema():
    empty = pd.DataFrame(columns=[
        "ticker", "trading_date", "sentiment_label",
        "sentiment_score", "availability_quality",
    ])
    out = aggregate_daily_sentiment(empty)
    assert out.empty
    assert list(out.columns) == OUTPUT_COLUMNS


def test_availability_live_ratio():
    """All live -> 1.0."""
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:05:00+07:00", "POSITIVE", 1.0),
        _row("BBCA", "2025-01-06T11:00:00+07:00", "2025-01-06T11:05:00+07:00", "POSITIVE", 1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert out["availability_live_ratio"].iloc[0] == 1.0


def test_aggregate_multiple_tickers_separate():
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 1.0),
        _row("BBRI", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "NEGATIVE", -1.0),
    ])
    out = aggregate_daily_sentiment(df)
    assert len(out) == 2
    assert set(out["ticker"]) == {"BBCA", "BBRI"}


# ---------------------------------------------------------------------------
# Sparse output verification
# ---------------------------------------------------------------------------

def test_output_is_sparse_no_filler_rows():
    """Only ticker-date pairs with news should appear."""
    df = _prep([
        _row("BBCA", "2025-01-06T10:00:00+07:00", "2025-01-06T10:00:00+07:00", "POSITIVE", 1.0),
    ])
    out = aggregate_daily_sentiment(df)
    # 1 ticker, 1 date, only 1 row (not the full 7-date calendar)
    assert len(out) == 1