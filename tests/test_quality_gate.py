"""Tests for price quality gate. In-memory DataFrames only."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ml.preprocessing.quality_gate import (
    build_report,
    check_duplicate_sessions,
    check_missing_weekdays,
    check_ohlc_invariants,
    check_positivity,
    check_return_anomalies,
    check_volume,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if not df.empty:
        df["trading_date"] = pd.to_datetime(df["trading_date"])
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype("float64")
        df["volume"] = df["volume"].astype("int64")
        df["return_1d"] = df["return_1d"].astype("float64")
    return df


def _row(ticker, d, close=100.0, high=None, low=None, open_=None, volume=1000, ret=0.0):
    high = high if high is not None else close
    low = low if low is not None else close
    open_ = open_ if open_ is not None else close
    return {
        "ticker": ticker, "trading_date": d, "open": open_, "high": high,
        "low": low, "close": close, "volume": volume, "return_1d": ret,
    }


# ---------------------------------------------------------------------------
# check_duplicate_sessions
# ---------------------------------------------------------------------------

def test_duplicates_detected():
    df = _df([
        _row("BBCA", "2024-01-02"),
        _row("BBCA", "2024-01-02"),  # duplicate
    ])
    findings = check_duplicate_sessions(df)
    assert len(findings) == 1
    assert findings[0].ticker == "BBCA"
    assert findings[0].severity == "ERROR"


def test_duplicates_none_when_unique():
    df = _df([
        _row("BBCA", "2024-01-02"),
        _row("BBCA", "2024-01-03"),
    ])
    assert check_duplicate_sessions(df) == []


# ---------------------------------------------------------------------------
# check_ohlc_invariants
# ---------------------------------------------------------------------------

def test_ohlc_high_lt_open_detected():
    df = _df([_row("BBCA", "2024-01-02", close=100, high=90, low=95, open_=100)])
    findings = check_ohlc_invariants(df)
    assert any("high" in f.message and "open" in f.message for f in findings)


def test_ohlc_high_lt_close_detected():
    df = _df([_row("BBCA", "2024-01-02", close=100, high=95, low=90, open_=92)])
    findings = check_ohlc_invariants(df)
    assert any("high" in f.message and "close" in f.message for f in findings)


def test_ohlc_low_gt_open_detected():
    df = _df([_row("BBCA", "2024-01-02", close=100, high=105, low=101, open_=100)])
    findings = check_ohlc_invariants(df)
    assert any("low" in f.message and "open" in f.message for f in findings)


def test_ohlc_valid_no_findings():
    df = _df([_row("BBCA", "2024-01-02", close=100, high=105, low=95, open_=98)])
    assert check_ohlc_invariants(df) == []


# ---------------------------------------------------------------------------
# check_positivity
# ---------------------------------------------------------------------------

def test_positivity_negative_close():
    df = _df([_row("BBCA", "2024-01-02", close=-5, high=0, low=-10, open_=-1)])
    findings = check_positivity(df)
    # Multiple fields invalid
    assert len(findings) >= 1
    assert all(f.severity == "ERROR" for f in findings)


def test_positivity_zero_volume_ok():
    """Volume = 0 is allowed (suspended). Only prices must be > 0."""
    df = _df([_row("BBCA", "2024-01-02", close=100, volume=0)])
    assert check_positivity(df) == []


# ---------------------------------------------------------------------------
# check_volume
# ---------------------------------------------------------------------------

def test_volume_negative_detected():
    df = _df([_row("BBCA", "2024-01-02", close=100, volume=-1)])
    findings = check_volume(df)
    assert len(findings) == 1
    assert findings[0].severity == "ERROR"


def test_volume_zero_ok():
    df = _df([_row("BBCA", "2024-01-02", close=100, volume=0)])
    assert check_volume(df) == []


# ---------------------------------------------------------------------------
# check_return_anomalies
# ---------------------------------------------------------------------------

def test_return_anomaly_above_threshold():
    df = _df([_row("BBCA", "2024-01-02", close=100, ret=0.50)])
    findings = check_return_anomalies(df, threshold=0.35)
    assert len(findings) == 1
    assert findings[0].severity == "WARNING"
    assert findings[0].details["return_1d"] == 0.50


def test_return_anomaly_negative_above_threshold():
    df = _df([_row("BBCA", "2024-01-02", close=100, ret=-0.40)])
    findings = check_return_anomalies(df, threshold=0.35)
    assert len(findings) == 1


def test_return_anomaly_below_threshold_ignored():
    df = _df([_row("BBCA", "2024-01-02", close=100, ret=0.20)])
    assert check_return_anomalies(df, threshold=0.35) == []


def test_return_anomaly_nan_ignored():
    df = _df([_row("BBCA", "2024-01-02", close=100, ret=float("nan"))])
    assert check_return_anomalies(df, threshold=0.35) == []


# ---------------------------------------------------------------------------
# check_missing_weekdays
# ---------------------------------------------------------------------------

def test_missing_weekday_gap_detected():
    # Mon 2024-01-01, skip Tue/Wed, resume Thu 2024-01-04
    df = _df([
        _row("BBCA", "2024-01-01"),
        _row("BBCA", "2024-01-04"),
    ])
    groups = check_missing_weekdays(df)
    # Expect 1 group covering Tue-Wed
    assert len(groups) == 1
    assert groups[0].missing_count == 2
    assert groups[0].start == date(2024, 1, 2)
    assert groups[0].end == date(2024, 1, 3)


def test_weekend_gap_not_flagged():
    # Fri 2024-01-05 -> Mon 2024-01-08 (no weekdays missing)
    df = _df([
        _row("BBCA", "2024-01-05"),
        _row("BBCA", "2024-01-08"),
    ])
    assert check_missing_weekdays(df) == []


def test_consecutive_dates_no_gap():
    df = _df([
        _row("BBCA", "2024-01-01"),
        _row("BBCA", "2024-01-02"),
        _row("BBCA", "2024-01-03"),
    ])
    assert check_missing_weekdays(df) == []


# ---------------------------------------------------------------------------
# build_report integration
# ---------------------------------------------------------------------------

def test_build_report_clean_data_passes():
    df = _df([
        _row("BBCA", "2024-01-02", close=100, ret=float("nan")),
        _row("BBCA", "2024-01-03", close=101, ret=0.01),
    ])
    report = build_report(df, input_path=Path("dummy.parquet"))
    assert report.has_errors is False
    assert report.total_rows == 2
    assert report.total_tickers == 1


def test_build_report_with_errors():
    df = _df([
        _row("BBCA", "2024-01-02", close=-1, high=0, low=-5, open_=-2),
    ])
    report = build_report(df, input_path=Path("dummy.parquet"))
    assert report.has_errors is True
    assert report.error_count > 0


def test_build_report_empty_df():
    df = _df([])
    report = build_report(df, input_path=Path("dummy.parquet"))
    assert report.total_rows == 0
    assert report.total_tickers == 0
    assert report.has_errors is False


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_contains_sections():
    df = _df([_row("BBCA", "2024-01-02", ret=0.50)])
    report = build_report(df, input_path=Path("dummy.parquet"), split_threshold=0.35)
    md = render_markdown(report)
    assert "# FinSentiMind — Price Quality Gate Report" in md
    assert "## Summary" in md
    assert "## Duplicate Sessions" in md
    assert "## OHLC Invariant Violations" in md
    assert "## Return Anomalies" in md
    assert "## Missing Weekday Groups" in md