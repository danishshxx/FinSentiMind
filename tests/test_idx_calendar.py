"""Authoritative IDX calendar tests (P0 remediation)."""
from datetime import date, timedelta

import pandas as pd
import pytest

from ml.preprocessing.idx_calendar import (
    OFFICIAL_CLOSURE,
    UNVERIFIED,
    VERIFIED_OPEN,
    WEEKEND,
    calendar_reference,
    exchange_day_status,
    is_official_closure,
    load_calendar_source,
    source_metadata,
    verified_open_days,
)
from ml.preprocessing.trading_calendar import derive_trading_calendar


def test_all_five_verified_2026_holidays_are_closed():
    for day in (
        date(2026, 5, 14),
        date(2026, 5, 15),
        date(2026, 5, 27),
        date(2026, 5, 28),
        date(2026, 8, 25),
    ):
        assert is_official_closure(day)
        reference = calendar_reference(day)
        assert reference["calendar_version"] == "IDX-2024-2026-v2"
        assert reference["source_id"] == "Peng-00171/BEI.POP/09-2025"


def test_2025_amended_holiday_is_closed():
    reference = calendar_reference(date(2025, 8, 18))
    assert is_official_closure(date(2025, 8, 18))
    assert reference["source_id"] == "IDX-2025-HOLIDAY-AMENDMENT"


def test_adjacent_sessions_are_open_and_closures_are_not():
    assert exchange_day_status(date(2026, 5, 13)) == VERIFIED_OPEN
    assert exchange_day_status(date(2026, 5, 14)) == OFFICIAL_CLOSURE
    assert exchange_day_status(date(2026, 5, 18)) == VERIFIED_OPEN
    assert exchange_day_status(date(2026, 5, 16)) == WEEKEND


def test_unverified_dates_are_not_assumed_open():
    assert exchange_day_status(date(2023, 1, 2)) == UNVERIFIED


def test_source_metadata_and_coverage():
    metadata = source_metadata()
    assert metadata["calendar_version"] == "IDX-2024-2026-v2"
    assert metadata["effective_from"] == "2024-10-07"
    assert metadata["effective_to"] == "2026-12-31"
    assert metadata["closure_count"] >= 48


def test_verified_open_days_skip_weekends_and_holidays():
    days = verified_open_days(date(2026, 5, 11), date(2026, 5, 18))
    assert date(2026, 5, 11) in days
    assert date(2026, 5, 12) in days
    assert date(2026, 5, 13) in days
    assert date(2026, 5, 14) not in days
    assert date(2026, 5, 15) not in days
    assert date(2026, 5, 18) in days


def test_calendar_does_not_manufacture_future_sessions_beyond_price_range():
    prices = pd.DataFrame([
        {"ticker": "A", "trading_date": "2026-05-12"},
        {"ticker": "A", "trading_date": "2026-05-13"},
    ])
    calendar = derive_trading_calendar(prices)
    assert calendar["date"].max() == date(2026, 5, 13)
    assert date(2026, 5, 14) not in set(calendar["date"])


def test_holiday_with_fake_bars_is_not_a_trading_session():
    prices = pd.DataFrame([
        {"ticker": "A", "trading_date": "2026-05-14"},
        {"ticker": "A", "trading_date": "2026-05-15"},
    ])
    calendar = derive_trading_calendar(prices)
    may = calendar[calendar["date"].isin([date(2026, 5, 14), date(2026, 5, 15)])]
    assert (may["day_status"] == OFFICIAL_CLOSURE).all()
    assert (may["is_trading_day"] == False).all()  # noqa: E712


def test_partial_coverage_on_open_session_is_not_closed():
    prices = pd.DataFrame([
        {"ticker": "A", "trading_date": "2026-05-13"},
        {"ticker": "A", "trading_date": "2026-05-18"},
        {"ticker": "B", "trading_date": "2026-05-13"},
    ])
    calendar = derive_trading_calendar(prices, threshold=0.9)
    may18 = calendar[calendar["date"] == date(2026, 5, 18)].iloc[0]
    assert may18["day_status"] == VERIFIED_OPEN
    assert may18["is_trading_day"] == True  # noqa: E712
    assert may18["coverage_threshold_met"] == False  # noqa: E712
