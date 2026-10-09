"""Tests for trading calendar derivation (TASK 103 Part 1)."""
from datetime import date

import pandas as pd
import pytest

from ml.preprocessing.trading_calendar import (
    CALENDAR_COLUMNS,
    derive_trading_calendar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(rows) -> pd.DataFrame:
    """rows: list of (ticker, date_str)."""
    return pd.DataFrame(
        [{"ticker": t, "trading_date": d} for t, d in rows]
    )


def _full_universe_prices(start: str, days: int, n_tickers: int = 3) -> pd.DataFrame:
    """Every ticker present every day from `start` for `days` days."""
    base = pd.Timestamp(start)
    tickers = [f"T{i:02d}" for i in range(n_tickers)]
    rows = []
    for i in range(days):
        d = (base + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        for t in tickers:
            rows.append((t, d))
    return _df(rows)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_full_coverage_all_days_are_trading():
    df = _full_universe_prices("2025-01-06", days=5, n_tickers=3)
    cal = derive_trading_calendar(df, threshold=0.9)
    assert len(cal) == 5
    assert cal["is_trading_day"].all()
    assert (cal["coverage_ratio"] == 1.0).all()


def test_output_schema():
    df = _full_universe_prices("2025-01-06", days=3, n_tickers=3)
    cal = derive_trading_calendar(df)
    assert list(cal.columns) == CALENDAR_COLUMNS


def test_ticker_count_is_universe_size():
    df = _full_universe_prices("2025-01-06", days=3, n_tickers=5)
    cal = derive_trading_calendar(df)
    assert (cal["ticker_count"] == 5).all()


# ---------------------------------------------------------------------------
# Coverage + threshold
# ---------------------------------------------------------------------------

def test_zero_coverage_is_non_trading():
    """A day where no ticker has data must be marked non-trading."""
    # Day 1: all 3. Day 2: none. Day 3: all 3.
    df = _df([
        ("A", "2025-01-06"), ("B", "2025-01-06"), ("C", "2025-01-06"),
        ("A", "2025-01-08"), ("B", "2025-01-08"), ("C", "2025-01-08"),
    ])
    cal = derive_trading_calendar(df, threshold=0.9)
    jan7 = cal.loc[cal["date"] == date(2025, 1, 7)].iloc[0]
    assert jan7["is_trading_day"] == False  # noqa: E712
    assert jan7["available_count"] == 0
    assert jan7["coverage_ratio"] == 0.0


def test_coverage_just_below_threshold_is_non_trading():
    # 3 tickers, 2 have data on day 2 -> 0.667 < 0.9
    df = _df([
        ("A", "2025-01-06"), ("B", "2025-01-06"), ("C", "2025-01-06"),
        ("A", "2025-01-07"), ("B", "2025-01-07"),
        ("A", "2025-01-08"), ("B", "2025-01-08"), ("C", "2025-01-08"),
    ])
    cal = derive_trading_calendar(df, threshold=0.9)
    jan7 = cal.loc[cal["date"] == date(2025, 1, 7)].iloc[0]
    assert jan7["is_trading_day"] == False  # noqa: E712
    assert jan7["coverage_ratio"] == pytest.approx(2 / 3)


def test_coverage_above_threshold_is_trading():
    # 10 tickers, 9 have data -> 0.9 >= 0.9 -> True
    base = pd.Timestamp("2025-01-06")
    tickers = [f"T{i:02d}" for i in range(10)]
    rows = []
    for t in tickers:
        rows.append((t, "2025-01-06"))
    for t in tickers[:9]:  # only 9 on day 2
        rows.append((t, "2025-01-07"))
    for t in tickers:
        rows.append((t, "2025-01-08"))

    cal = derive_trading_calendar(_df(rows), threshold=0.9)
    jan7 = cal.loc[cal["date"] == date(2025, 1, 7)].iloc[0]
    assert jan7["is_trading_day"] == True  # noqa: E712
    assert jan7["coverage_ratio"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Weekend + gap
# ---------------------------------------------------------------------------

def test_weekend_dates_included_with_zero_coverage():
    # Mon-Fri with full universe
    df = _full_universe_prices("2025-01-06", days=5, n_tickers=3)
    cal = derive_trading_calendar(df)
    # 2025-01-11 (Sat) and 2025-01-12 (Sun) should NOT be in output
    # because the calendar range is [min, max] of prices — Mon to Fri only.
    weekend = cal.loc[cal["date"].isin([date(2025, 1, 11), date(2025, 1, 12)])]
    assert weekend.empty


def test_holiday_gap_appears_as_non_trading():
    # Mon, Tue, [skip Wed-Thu], Fri (Idul Fitri simulation)
    df = _df([
        ("A", "2025-03-31"), ("B", "2025-03-31"), ("C", "2025-03-31"),
        ("A", "2025-04-01"), ("B", "2025-04-01"), ("C", "2025-04-01"),
        ("A", "2025-04-07"), ("B", "2025-04-07"), ("C", "2025-04-07"),
    ])
    cal = derive_trading_calendar(df)
    # Apr 2, 3, 4 (Wed-Fri) should be non-trading
    gap = cal.loc[
        (cal["date"] >= date(2025, 4, 2)) & (cal["date"] <= date(2025, 4, 4))
    ]
    assert (gap["is_trading_day"] == False).all()  # noqa: E712
    # Apr 5-6 (Sat-Sun) also non-trading
    weekend = cal.loc[
        (cal["date"] >= date(2025, 4, 5)) & (cal["date"] <= date(2025, 4, 6))
    ]
    assert (weekend["is_trading_day"] == False).all()  # noqa: E712


def test_partial_coverage_below_threshold_flagged():
    # 30 tickers, only 20 on day 2 -> 0.667 < 0.9
    df = _full_universe_prices("2025-01-06", days=1, n_tickers=30)
    base = pd.Timestamp("2025-01-06")
    # Day 2: only 20
    rows = [("T%02d" % i, "2025-01-07") for i in range(20)]
    df2 = _df(rows)
    combined = pd.concat([df, df2], ignore_index=True)
    cal = derive_trading_calendar(combined)
    jan7 = cal.loc[cal["date"] == date(2025, 1, 7)].iloc[0]
    assert jan7["is_trading_day"] == False  # noqa: E712
    assert jan7["coverage_ratio"] == pytest.approx(20 / 30)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_empty_df_raises():
    with pytest.raises(ValueError, match="empty"):
        derive_trading_calendar(pd.DataFrame(columns=["ticker", "trading_date"]))


def test_missing_columns_raises():
    df = pd.DataFrame({"foo": [1, 2]})
    with pytest.raises(ValueError, match="missing required columns"):
        derive_trading_calendar(df)


@pytest.mark.parametrize("bad_threshold", [0, -0.1, 1.1, 2])
def test_invalid_threshold_raises(bad_threshold):
    df = _full_universe_prices("2025-01-06", days=3, n_tickers=3)
    with pytest.raises(ValueError, match="threshold"):
        derive_trading_calendar(df, threshold=bad_threshold)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_coverage_ratio_matches_available_over_ticker_count():
    df = _full_universe_prices("2025-01-06", days=3, n_tickers=4)
    cal = derive_trading_calendar(df)
    for _, row in cal.iterrows():
        expected = row["available_count"] / row["ticker_count"]
        assert row["coverage_ratio"] == pytest.approx(expected)