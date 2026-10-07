"""
Tests for clean price dataset pipeline (TASK 102B).

All tests use in-memory DataFrames — no network, no Supabase.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ml.preprocessing.load_prices import (
    OUTPUT_COLUMNS,
    build_clean_dataset,
    cast_types,
    compute_return_1d,
    normalize_columns,
    save_parquet,
    sort_prices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_row(
    ticker: str,
    d: str,
    close: float = 100.0,
    open_: float = None,
    high: float = None,
    low: float = None,
    volume: int = 1000,
) -> dict:
    """Build a raw (DB-named) row."""
    open_ = open_ if open_ is not None else close
    high = high if high is not None else close
    low = low if low is not None else close
    return {
        "kode_saham": ticker,
        "tanggal": d,
        "harga_buka": open_,
        "harga_tertinggi": high,
        "harga_terendah": low,
        "harga_tutup": close,
        "volume": volume,
    }


def _raw_df(rows):
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# normalize_columns
# ---------------------------------------------------------------------------

def test_normalize_columns_renames_to_ml_convention():
    df = _raw_df([_raw_row("BBCA", "2024-01-02", close=100.0)])
    out = normalize_columns(df)
    assert set(out.columns) == {
        "ticker", "trading_date", "open", "high", "low", "close", "volume"
    }


def test_normalize_columns_missing_required_raises():
    df = pd.DataFrame({"kode_saham": ["BBCA"], "tanggal": ["2024-01-02"]})
    with pytest.raises(ValueError, match="Missing required columns"):
        normalize_columns(df)


def test_normalize_columns_preserves_unknown_columns():
    df = _raw_df([_raw_row("BBCA", "2024-01-02")])
    df["unexpected_col"] = "x"
    out = normalize_columns(df)
    assert "unexpected_col" in out.columns
    assert "ticker" in out.columns


# ---------------------------------------------------------------------------
# cast_types
# ---------------------------------------------------------------------------

def test_cast_types_applies_expected_dtypes():
    df = _raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=101.0),
    ])
    df = normalize_columns(df)
    df = cast_types(df)

    assert str(df["ticker"].dtype) == "string"
    assert str(df["trading_date"].dtype).startswith("datetime64")
    for c in ("open", "high", "low", "close"):
        assert df[c].dtype == "float64"
    assert df["volume"].dtype == "int64"


def test_cast_types_trading_date_is_datetime():
    df = normalize_columns(_raw_df([
        _raw_row("BBCA", "2024-01-02"),
    ]))
    df = cast_types(df)
    assert isinstance(df["trading_date"].iloc[0], pd.Timestamp)


# ---------------------------------------------------------------------------
# sort_prices
# ---------------------------------------------------------------------------

def test_sort_prices_by_ticker_then_date():
    df = normalize_columns(_raw_df([
        _raw_row("BBRI", "2024-01-03", close=500.0),
        _raw_row("BBCA", "2024-01-03", close=101.0),
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBRI", "2024-01-02", close=499.0),
    ]))
    df = cast_types(df)
    out = sort_prices(df)

    assert list(out["ticker"]) == ["BBCA", "BBCA", "BBRI", "BBRI"]
    assert list(out["trading_date"].dt.strftime("%Y-%m-%d")) == [
        "2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03",
    ]
    assert list(out.index) == [0, 1, 2, 3]  # reset_index


# ---------------------------------------------------------------------------
# compute_return_1d
# ---------------------------------------------------------------------------

def test_return_calculation_single_ticker():
    df = normalize_columns(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=110.0),
    ]))
    df = cast_types(df)
    df = sort_prices(df)
    df = compute_return_1d(df)

    returns = df["return_1d"].tolist()
    assert pd.isna(returns[0])           # first row NaN
    assert returns[1] == pytest.approx(0.10)


def test_return_first_row_per_ticker_is_nan():
    df = normalize_columns(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=105.0),
        _raw_row("BBRI", "2024-01-02", close=500.0),
        _raw_row("BBRI", "2024-01-03", close=510.0),
    ]))
    df = cast_types(df)
    df = sort_prices(df)
    df = compute_return_1d(df)

    # First row of each ticker -> NaN
    assert pd.isna(df.iloc[0]["return_1d"])
    assert pd.isna(df.iloc[2]["return_1d"])

    # Second row computed correctly
    assert df.iloc[1]["return_1d"] == pytest.approx(0.05)
    assert df.iloc[3]["return_1d"] == pytest.approx(0.02)


def test_return_does_not_cross_tickers():
    """
    CRITICAL: last row of BBCA must NOT be compared to first row of BBRI.
    """
    df = normalize_columns(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBRI", "2024-01-02", close=500.0),
    ]))
    df = cast_types(df)
    df = sort_prices(df)
    df = compute_return_1d(df)

    # Both first rows -> NaN (no leakage)
    assert pd.isna(df["return_1d"].iloc[0])
    assert pd.isna(df["return_1d"].iloc[1])


def test_return_not_forward_filled_or_zeroed():
    df = normalize_columns(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=110.0),
    ]))
    df = cast_types(df)
    df = sort_prices(df)
    df = compute_return_1d(df)

    # First row must be NaN — not 0.0, not forward-filled
    assert pd.isna(df.iloc[0]["return_1d"])
    assert df.iloc[0]["return_1d"] != 0.0


# ---------------------------------------------------------------------------
# build_clean_dataset (integration)
# ---------------------------------------------------------------------------

def test_build_clean_dataset_column_order_and_dtypes():
    df = build_clean_dataset(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=105.0),
    ]))

    assert list(df.columns) == OUTPUT_COLUMNS
    assert df["ticker"].iloc[0] == "BBCA"
    assert df["close"].iloc[0] == 100.0
    assert df["volume"].iloc[0] == 1000
    assert pd.isna(df["return_1d"].iloc[0])
    assert df["return_1d"].iloc[1] == pytest.approx(0.05)


def test_build_clean_dataset_empty_returns_schema():
    empty = pd.DataFrame()
    out = build_clean_dataset(empty)
    assert out.empty
    assert list(out.columns) == OUTPUT_COLUMNS


def test_build_clean_dataset_missing_column_raises():
    bad = pd.DataFrame({"kode_saham": ["BBCA"]})  # missing most columns
    with pytest.raises(ValueError, match="Missing required columns"):
        build_clean_dataset(bad)


# ---------------------------------------------------------------------------
# save_parquet
# ---------------------------------------------------------------------------

def test_save_parquet_creates_file(tmp_path: Path):
    df = build_clean_dataset(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
        _raw_row("BBCA", "2024-01-03", close=105.0),
    ]))
    out = tmp_path / "subdir" / "prices_clean.parquet"
    save_parquet(df, out)

    assert out.exists()

    # Roundtrip
    reloaded = pd.read_parquet(out)
    assert list(reloaded.columns) == OUTPUT_COLUMNS
    assert len(reloaded) == 2


def test_save_parquet_preserves_dtypes(tmp_path: Path):
    df = build_clean_dataset(_raw_df([
        _raw_row("BBCA", "2024-01-02", close=100.0),
    ]))
    out = tmp_path / "prices_clean.parquet"
    save_parquet(df, out)

    reloaded = pd.read_parquet(out)
    assert reloaded["volume"].dtype == "int64"
    assert reloaded["close"].dtype == "float64"