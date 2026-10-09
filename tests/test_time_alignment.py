"""Tests for time alignment service (TASK 103 Part 2)."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.services.time_alignment import (
    SignalContext,
    build_signal_context,
    map_to_trading_date,
    normalize_to_market_tz,
    signal_cutoff_for,
)


JKT = ZoneInfo("Asia/Jakarta")
UTC = ZoneInfo("UTC")
CUTOFF = time(16, 0)

# Simple trading calendar:
# Mon 2025-01-06, Tue 07, Wed 08, Thu 09, Fri 10
# (skip weekend) Mon 13, Tue 14
TRADING_DATES = {
    date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8),
    date(2025, 1, 9), date(2025, 1, 10),
    date(2025, 1, 13), date(2025, 1, 14),
}


# ---------------------------------------------------------------------------
# normalize_to_market_tz
# ---------------------------------------------------------------------------

def test_normalize_naive_raises():
    with pytest.raises(ValueError, match="Naive"):
        normalize_to_market_tz(datetime(2025, 1, 6, 10, 0), JKT)


def test_normalize_utc_to_jakarta():
    utc_dt = datetime(2025, 1, 6, 3, 0, tzinfo=UTC)  # 10:00 WIB
    out = normalize_to_market_tz(utc_dt, JKT)
    assert out.tzinfo is not None
    assert out.hour == 10
    assert out.date() == date(2025, 1, 6)


def test_normalize_already_in_jakarta_unchanged_time():
    jkt_dt = datetime(2025, 1, 6, 10, 0, tzinfo=JKT)
    out = normalize_to_market_tz(jkt_dt, JKT)
    assert out.hour == 10


# ---------------------------------------------------------------------------
# Case A: trading day before cutoff
# ---------------------------------------------------------------------------

def test_case_a_monday_morning_same_day():
    when = datetime(2025, 1, 6, 14, 0, tzinfo=JKT)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 6)


def test_case_a_exactly_at_cutoff_same_day():
    when = datetime(2025, 1, 6, 16, 0, tzinfo=JKT)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 6)


# ---------------------------------------------------------------------------
# Case B: trading day after cutoff
# ---------------------------------------------------------------------------

def test_case_b_monday_evening_next_day():
    when = datetime(2025, 1, 6, 17, 0, tzinfo=JKT)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 7)


def test_case_b_friday_evening_rolls_to_monday():
    when = datetime(2025, 1, 10, 17, 0, tzinfo=JKT)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 13)


# ---------------------------------------------------------------------------
# Case C: weekend
# ---------------------------------------------------------------------------

def test_case_c_saturday_rolls_to_monday():
    when = datetime(2025, 1, 11, 10, 0, tzinfo=JKT)  # Saturday
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 13)


def test_case_c_sunday_rolls_to_monday():
    when = datetime(2025, 1, 12, 10, 0, tzinfo=JKT)  # Sunday
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 13)


# ---------------------------------------------------------------------------
# Case D: holiday
# ---------------------------------------------------------------------------

def test_case_d_holiday_rolls_to_next():
    # Remove Jan 7 from trading dates -> Jan 6 evening should map to Jan 8
    dates = TRADING_DATES - {date(2025, 1, 7)}
    when = datetime(2025, 1, 6, 17, 0, tzinfo=JKT)
    assert map_to_trading_date(when, dates, CUTOFF, JKT) == date(2025, 1, 8)


def test_case_d_holiday_direct_timestamp_rolls():
    # Timestamp ON a holiday (Jan 7 not trading) morning -> Jan 8
    dates = TRADING_DATES - {date(2025, 1, 7)}
    when = datetime(2025, 1, 7, 10, 0, tzinfo=JKT)
    assert map_to_trading_date(when, dates, CUTOFF, JKT) == date(2025, 1, 8)


# ---------------------------------------------------------------------------
# Case E: timezone conversion
# ---------------------------------------------------------------------------

def test_case_e_utc_before_cutoff():
    # 2025-01-06 08:00 UTC = 15:00 WIB -> same day (before cutoff)
    when = datetime(2025, 1, 6, 8, 0, tzinfo=UTC)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 6)


def test_case_e_utc_after_cutoff():
    # 2025-01-06 10:00 UTC = 17:00 WIB -> next day
    when = datetime(2025, 1, 6, 10, 0, tzinfo=UTC)
    assert map_to_trading_date(when, TRADING_DATES, CUTOFF, JKT) == date(2025, 1, 7)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_empty_calendar_raises():
    with pytest.raises(ValueError, match="empty"):
        map_to_trading_date(datetime(2025, 1, 6, 10, 0, tzinfo=JKT), set(), CUTOFF, JKT)


def test_naive_timestamp_raises():
    with pytest.raises(ValueError, match="Naive"):
        map_to_trading_date(datetime(2025, 1, 6, 10, 0), TRADING_DATES, CUTOFF, JKT)


def test_no_future_trading_date_raises():
    # Calendar with only one date; timestamp after cutoff -> no future
    dates = {date(2025, 1, 6)}
    when = datetime(2025, 1, 6, 17, 0, tzinfo=JKT)
    with pytest.raises(ValueError, match="No trading date"):
        map_to_trading_date(when, dates, CUTOFF, JKT)


# ---------------------------------------------------------------------------
# signal_cutoff_for
# ---------------------------------------------------------------------------

def test_signal_cutoff_for_produces_tz_aware():
    dt = signal_cutoff_for(date(2025, 1, 6), CUTOFF, JKT)
    assert dt.tzinfo is not None
    assert dt.hour == 16
    assert dt.minute == 0


# ---------------------------------------------------------------------------
# build_signal_context
# ---------------------------------------------------------------------------

def test_build_signal_context_default_decision_time():
    ctx = build_signal_context(date(2025, 1, 6), TRADING_DATES, CUTOFF, JKT)
    assert isinstance(ctx, SignalContext)
    assert ctx.trading_date == date(2025, 1, 6)
    assert ctx.signal_cutoff == datetime(2025, 1, 6, 16, 0, tzinfo=JKT)
    assert ctx.decision_time == ctx.signal_cutoff
    assert ctx.target_session == date(2025, 1, 7)


def test_build_signal_context_friday_target_is_monday():
    ctx = build_signal_context(date(2025, 1, 10), TRADING_DATES, CUTOFF, JKT)
    assert ctx.target_session == date(2025, 1, 13)


def test_build_signal_context_not_in_calendar_raises():
    with pytest.raises(ValueError, match="not in the trading calendar"):
        build_signal_context(date(2025, 1, 11), TRADING_DATES, CUTOFF, JKT)


def test_build_signal_context_decision_before_cutoff_raises():
    with pytest.raises(ValueError, match="precedes signal_cutoff"):
        build_signal_context(
            date(2025, 1, 6), TRADING_DATES, CUTOFF, JKT,
            decision_time=datetime(2025, 1, 6, 15, 0, tzinfo=JKT),
        )


def test_build_signal_context_after_cutoff_ok():
    ctx = build_signal_context(
        date(2025, 1, 6), TRADING_DATES, CUTOFF, JKT,
        decision_time=datetime(2025, 1, 6, 17, 0, tzinfo=JKT),
    )
    assert ctx.decision_time.hour == 17