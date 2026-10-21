"""Tests for base dataset builder (TASK 105). In-memory DataFrames only."""
import logging
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from ml.preprocessing.build_base_dataset import (
    JOIN_KEYS,
    OUTPUT_COLUMNS,
    PRICE_COLUMNS,
    SENTIMENT_COLUMNS,
    _validate_required_columns,
    build_base_dataset,
    normalize_ticker,
    normalize_trading_date,
    run_build,
    save_parquet_atomic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prices_df(rows):
    return pd.DataFrame(rows)


def _price(ticker, d, close=100.0, ret=0.0):
    return {
        "ticker": ticker, "trading_date": d,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "return_1d": ret,
    }


def _sentiment(ticker, d, news_count=1, mean=1.0, std=np.nan,
               pos=1.0, neg=0.0, live=1.0):
    return {
        "ticker": ticker, "trading_date": d,
        "news_count": news_count, "sentiment_mean": mean,
        "sentiment_std": std, "positive_ratio": pos,
        "negative_ratio": neg, "availability_live_ratio": live,
    }


def _empty_sentiment():
    return pd.DataFrame(columns=SENTIMENT_COLUMNS)


# ---------------------------------------------------------------------------
# Core join (invariant 1, 2, 5, 7)
# ---------------------------------------------------------------------------

def test_left_join_with_matching_sentiment():
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        _price("BBCA", "2025-01-07"),
    ])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=2, std=0.5),
        _sentiment("BBCA", "2025-01-07", news_count=3, std=0.3),
    ])
    base = build_base_dataset(prices, sentiment)
    assert len(base) == 2
    assert (base["news_count"] > 0).all()


def test_row_count_matches_prices():
    """CHECKPOINT 1: output row count == input price row count."""
    prices = _prices_df([_price("BBCA", f"2025-01-0{i}") for i in range(1, 8)])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-03")])
    base = build_base_dataset(prices, sentiment)
    assert len(base) == len(prices)


def test_price_rows_without_sentiment_preserved():
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        _price("BBCA", "2025-01-07"),
        _price("BBCA", "2025-01-08"),
    ])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    base = build_base_dataset(prices, sentiment)
    assert len(base) == 3


def test_unmatched_news_count_is_zero_and_sentiment_null():
    """CHECKPOINT 2: no-news fill policy exact."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = _empty_sentiment()
    base = build_base_dataset(prices, sentiment)
    row = base.iloc[0]
    assert row["news_count"] == 0
    assert pd.isna(row["sentiment_mean"])
    assert pd.isna(row["sentiment_std"])
    assert pd.isna(row["positive_ratio"])
    assert pd.isna(row["negative_ratio"])
    assert pd.isna(row["availability_live_ratio"])


def test_news_count_dtype_is_int64():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = _empty_sentiment()
    base = build_base_dataset(prices, sentiment)
    assert base["news_count"].dtype == "int64"


# ---------------------------------------------------------------------------
# Duplicate keys (CHECKPOINT 3a)
# ---------------------------------------------------------------------------

def test_duplicate_price_keys_raise():
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        _price("BBCA", "2025-01-06"),
    ])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    with pytest.raises(ValueError, match="duplicate"):
        build_base_dataset(prices, sentiment)


def test_duplicate_sentiment_keys_raise():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06"),
        _sentiment("BBCA", "2025-01-06"),
    ])
    with pytest.raises(ValueError, match="duplicate"):
        build_base_dataset(prices, sentiment)


def test_duplicate_detected_with_actionable_sample():
    """Error message includes offending key sample."""
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        _price("BBCA", "2025-01-06"),
    ])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    with pytest.raises(ValueError, match="BBCA"):
        build_base_dataset(prices, sentiment)


# ---------------------------------------------------------------------------
# Invalid keys (CHECKPOINT 3b)
# ---------------------------------------------------------------------------

def test_null_ticker_rejected():
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        {**_price("BBCA", "2025-01-07"), "ticker": None},
    ])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    with pytest.raises(ValueError, match="NULL ticker"):
        build_base_dataset(prices, sentiment)


def test_empty_ticker_rejected():
    prices = _prices_df([
        {**_price("BBCA", "2025-01-06"), "ticker": "   "},
    ])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    with pytest.raises(ValueError, match="empty ticker"):
        build_base_dataset(prices, sentiment)


def test_invalid_trading_date_rejected():
    """Invalid dates become NaT and are rejected."""
    prices = _prices_df([
        _price("BBCA", "not-a-date"),
    ])
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="invalid trading_date"):
        build_base_dataset(prices, sentiment)


def test_timezone_aware_date_rejected():
    """TZ-aware timestamps must fail, not silently strip."""
    prices = _prices_df([
        {**_price("BBCA", "2025-01-06"),
         "trading_date": pd.Timestamp("2025-01-06", tz="Asia/Jakarta")},
    ])
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="timezone-naive"):
        build_base_dataset(prices, sentiment)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_ticker_whitespace_and_case_normalized():
    prices = _prices_df([_price(" bbca ", "2025-01-06")])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    base = build_base_dataset(prices, sentiment)
    assert base["ticker"].iloc[0] == "BBCA"
    assert base["news_count"].iloc[0] == 1


def test_normalization_collision_detected():
    """Two rows differing only by whitespace collide after normalize -> dup error."""
    prices = _prices_df([
        _price("BBCA", "2025-01-06"),
        _price(" bbca ", "2025-01-06"),
    ])
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="duplicate"):
        build_base_dataset(prices, sentiment)


def test_equivalent_date_representations_join():
    """Python date vs ISO string join correctly."""
    prices = _prices_df([_price("BBCA", date(2025, 1, 6))])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    base = build_base_dataset(prices, sentiment)
    assert base["news_count"].iloc[0] == 1


def test_normalize_ticker_helper():
    s = pd.Series([" bbca ", "BBRI", "  tlkm  "])
    out = normalize_ticker(s)
    assert list(out) == ["BBCA", "BBRI", "TLKM"]


def test_normalize_date_helper_midnight():
    s = pd.Series([date(2025, 1, 6), "2025-01-07"])
    out = normalize_trading_date(s)
    assert out.dtype == "datetime64[ns]"
    assert out.iloc[0] == pd.Timestamp("2025-01-06 00:00:00")
    assert out.iloc[1] == pd.Timestamp("2025-01-07 00:00:00")


def test_normalize_date_rejects_tz_aware():
    s = pd.Series([pd.Timestamp("2025-01-06 12:00:00", tz="UTC")])
    with pytest.raises(ValueError, match="timezone-naive"):
        normalize_trading_date(s)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_missing_price_column_raises():
    prices = _prices_df([_price("BBCA", "2025-01-06")]).drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing required"):
        _validate_required_columns(prices, PRICE_COLUMNS, "prices")


def test_empty_sentiment_with_valid_schema_supported():
    """Empty sentiment DataFrame with required columns is valid."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = _empty_sentiment()  # has required columns, 0 rows
    base = build_base_dataset(prices, sentiment)
    assert len(base) == 1
    assert base["news_count"].iloc[0] == 0


def test_empty_sentiment_with_invalid_schema_rejected():
    """Empty sentiment missing columns must fail — empty != invalid schema."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    bad_sentiment = pd.DataFrame(columns=["ticker", "trading_date"])
    with pytest.raises(ValueError, match="missing required"):
        _validate_required_columns(bad_sentiment, SENTIMENT_COLUMNS, "sentiment")


def test_empty_prices_with_valid_schema_raises():
    """Empty prices WITH valid schema -> 'empty' message."""
    prices = pd.DataFrame(columns=PRICE_COLUMNS)  # valid schema, 0 rows
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="empty"):
        build_base_dataset(prices, sentiment)


def test_empty_prices_without_schema_raises_missing_columns():
    """Empty prices WITHOUT schema -> 'missing required columns'."""
    prices = _prices_df([])  # no columns at all
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="missing required"):
        build_base_dataset(prices, sentiment)


# ---------------------------------------------------------------------------
# Matched sentiment validation
# ---------------------------------------------------------------------------

def test_matched_sentiment_mean_nonfinite_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=2, mean=np.inf, std=0.1),
    ])
    with pytest.raises(ValueError, match="non-finite"):
        build_base_dataset(prices, sentiment)


def test_matched_ratio_out_of_range_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=2, std=0.1, pos=1.5),
    ])
    with pytest.raises(ValueError, match="outside \\[0, 1\\]"):
        build_base_dataset(prices, sentiment)


def test_matched_std_nan_with_multi_article_rejected():
    """news_count > 1 must have non-NaN sentiment_std."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=2, std=np.nan),
    ])
    with pytest.raises(ValueError, match="news_count > 1"):
        build_base_dataset(prices, sentiment)


def test_single_article_sentiment_std_nan_allowed():
    """news_count == 1 -> sentiment_std may be NaN (statistically correct)."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=1, std=np.nan),
    ])
    base = build_base_dataset(prices, sentiment)
    assert pd.isna(base["sentiment_std"].iloc[0])


def test_matched_news_count_non_positive_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=0),
    ])
    with pytest.raises(ValueError, match="news_count"):
        build_base_dataset(prices, sentiment)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_output_ordering_deterministic():
    prices = _prices_df([
        _price("BBRI", "2025-01-07"),
        _price("BBCA", "2025-01-07"),
        _price("BBCA", "2025-01-06"),
        _price("BBRI", "2025-01-06"),
    ])
    sentiment = _empty_sentiment()
    base = build_base_dataset(prices, sentiment)
    pairs = list(zip(
        base["ticker"],
        base["trading_date"].dt.strftime("%Y-%m-%d"),
    ))
    expected = [
        ("BBCA", "2025-01-06"),
        ("BBCA", "2025-01-07"),
        ("BBRI", "2025-01-06"),
        ("BBRI", "2025-01-07"),
    ]
    assert pairs == expected


def test_no_target_or_label_columns():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([_sentiment("BBCA", "2025-01-06")])
    base = build_base_dataset(prices, sentiment)
    forbidden = {"target", "label", "y", "future_return", "class"}
    assert not (forbidden & set(base.columns))
    assert list(base.columns) == OUTPUT_COLUMNS


def test_preserve_original_price_values():
    prices = _prices_df([
        _price("BBCA", "2025-01-06", close=100.0, ret=0.05),
        _price("BBCA", "2025-01-07", close=105.0, ret=np.nan),
    ])
    sentiment = _empty_sentiment()
    base = build_base_dataset(prices, sentiment)
    assert base["close"].iloc[0] == 100.0
    assert base["close"].iloc[1] == 105.0
    assert base["return_1d"].iloc[0] == 0.05
    assert pd.isna(base["return_1d"].iloc[1])


# ---------------------------------------------------------------------------
# Atomic write (CHECKPOINT 3c)
# ---------------------------------------------------------------------------

def test_save_parquet_atomic_success(tmp_path: Path):
    df = pd.DataFrame({"a": [1, 2, 3]})
    target = tmp_path / "out.parquet"
    save_parquet_atomic(df, target)
    assert target.exists()
    # No tmp files left behind
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_write_preserves_existing_artifact(tmp_path: Path):
    """CHECKPOINT 3c: existing valid artifact survives failed write."""
    target = tmp_path / "out.parquet"
    original = pd.DataFrame({"a": [1, 2, 3]})
    save_parquet_atomic(original, target)
    assert target.exists()

    # Simulate write failure
    new_df = pd.DataFrame({"b": [4, 5, 6]})
    with patch(
        "pandas.DataFrame.to_parquet", side_effect=RuntimeError("disk full")
    ):
        with pytest.raises(RuntimeError, match="disk full"):
            save_parquet_atomic(new_df, target)

    # Original intact
    reloaded = pd.read_parquet(target)
    assert list(reloaded.columns) == ["a"]
    # Temp cleaned up
    assert not list(tmp_path.glob("*.tmp"))


def test_save_cleanup_on_failure(tmp_path: Path):
    """Temp file cleaned up when to_parquet raises."""
    df = pd.DataFrame({"a": [1]})
    target = tmp_path / "out.parquet"
    with patch(
        "pandas.DataFrame.to_parquet", side_effect=RuntimeError("io error")
    ):
        with pytest.raises(RuntimeError, match="io error"):
            save_parquet_atomic(df, target)
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

def test_run_build_end_to_end(tmp_path: Path):
    prices_path = tmp_path / "prices.parquet"
    sentiment_path = tmp_path / "sentiment.parquet"
    output_path = tmp_path / "base.parquet"

    _prices_df([_price("BBCA", "2025-01-06")]).to_parquet(prices_path, index=False)
    pd.DataFrame([_sentiment("BBCA", "2025-01-06")]).to_parquet(
        sentiment_path, index=False
    )

    base = run_build(
        prices_path=prices_path,
        sentiment_path=sentiment_path,
        output_path=output_path,
        save=True,
    )

    assert output_path.exists()
    assert len(base) == 1
    reloaded = pd.read_parquet(output_path)
    assert list(reloaded.columns) == OUTPUT_COLUMNS
    assert len(reloaded) == 1


def test_run_build_missing_prices_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="prices_clean"):
        run_build(
            prices_path=tmp_path / "nonexistent.parquet",
            sentiment_path=tmp_path / "nonexistent.parquet",
            output_path=tmp_path / "out.parquet",
            save=False,
        )


# ---------------------------------------------------------------------------
# Fix 1 — Schema validation at public boundary
# ---------------------------------------------------------------------------

def test_build_rejects_missing_price_columns_directly():
    """build_base_dataset() validates schema directly (not just load_*)."""
    prices = pd.DataFrame({"ticker": ["BBCA"], "trading_date": ["2025-01-06"]})
    sentiment = _empty_sentiment()
    with pytest.raises(ValueError, match="missing required"):
        build_base_dataset(prices, sentiment)


def test_build_rejects_missing_sentiment_columns_directly():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    bad_sentiment = pd.DataFrame(columns=["ticker", "trading_date"])
    with pytest.raises(ValueError, match="missing required"):
        build_base_dataset(prices, bad_sentiment)


def test_build_empty_sentiment_with_valid_schema_directly():
    """Empty sentiment + complete schema works through public builder."""
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = _empty_sentiment()  # has all required columns, 0 rows
    base = build_base_dataset(prices, sentiment)
    assert len(base) == 1
    assert base["news_count"].iloc[0] == 0


# ---------------------------------------------------------------------------
# Fix 2 — Strict news_count validation
# ---------------------------------------------------------------------------

def test_news_count_fractional_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=1.5, std=0.1),
    ])
    with pytest.raises(ValueError, match="fractional"):
        build_base_dataset(prices, sentiment)


def test_news_count_nan_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=np.nan, std=0.1),
    ])
    with pytest.raises(ValueError, match="NULL|NaN"):
        build_base_dataset(prices, sentiment)


def test_news_count_infinity_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=np.inf, std=0.1),
    ])
    with pytest.raises(ValueError, match="non-finite"):
        build_base_dataset(prices, sentiment)


def test_news_count_non_numeric_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count="two", std=0.1),
    ])
    with pytest.raises(ValueError):
        build_base_dataset(prices, sentiment)


def test_news_count_valid_positive_int_ok():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=5, std=0.2),
    ])
    base = build_base_dataset(prices, sentiment)
    assert base["news_count"].iloc[0] == 5


# ---------------------------------------------------------------------------
# Fix 3 — sentiment_std infinity check for single-article rows
# ---------------------------------------------------------------------------

def test_sentiment_std_infinity_with_single_article_rejected():
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        _sentiment("BBCA", "2025-01-06", news_count=1, std=np.inf),
    ])
    with pytest.raises(ValueError, match="non-finite"):
        build_base_dataset(prices, sentiment)


# ---------------------------------------------------------------------------
# Fix 4 — Extra column warnings
# ---------------------------------------------------------------------------

def test_extra_price_columns_warn(caplog):
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    prices["unexpected_col"] = 42
    sentiment = _empty_sentiment()
    with caplog.at_level(logging.WARNING):
        build_base_dataset(prices, sentiment)
    assert any(
        "extra column" in r.message.lower() for r in caplog.records
    )


def test_extra_sentiment_columns_warn(caplog):
    prices = _prices_df([_price("BBCA", "2025-01-06")])
    sentiment = pd.DataFrame([
        {**_sentiment("BBCA", "2025-01-06"), "unexpected_col": "value"},
    ])
    with caplog.at_level(logging.WARNING):
        build_base_dataset(prices, sentiment)
    assert any(
        "extra column" in r.message.lower() for r in caplog.records
    )