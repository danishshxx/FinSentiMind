"""Exchange-closure price quarantine tests (P0 remediation)."""
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from ml.features.quant import compute_features
from ml.preprocessing.daily_sentiment import align_news_to_trading_date
from ml.preprocessing.load_prices import (
    ExchangeClosureContradictionError,
    build_clean_dataset,
)
from ml.preprocessing.idx_calendar import verified_open_days
from ml.preprocessing.quality_gate import check_missing_weekdays
from ml.validation.validate_dataset import validate_exchange_calendar

JAKARTA = ZoneInfo("Asia/Jakarta")


def _raw(ticker, day, close, volume, *, open_=None, high=None, low=None):
    open_ = close if open_ is None else open_
    high = close if high is None else high
    low = close if low is None else low
    return {
        "kode_saham": ticker,
        "tanggal": day,
        "harga_buka": open_,
        "harga_tertinggi": high,
        "harga_terendah": low,
        "harga_tutup": close,
        "volume": volume,
    }


def _frame(rows):
    return pd.DataFrame(rows)


def _holiday_rows(days):
    return [
        _raw("BBCA", day.isoformat(), 100.0, 0)
        for day in days
    ]


def test_all_verified_2026_holidays_are_excluded(tmp_path):
    days = [
        date(2026, 5, 14), date(2026, 5, 15),
        date(2026, 5, 27), date(2026, 5, 28), date(2026, 8, 25),
    ]
    raw = _frame(_holiday_rows(days))
    report = tmp_path / "quarantine.json"
    out = build_clean_dataset(raw, quarantine_report_path=report)
    assert out.empty
    assert out.attrs["closure_excluded_rows"] == 5
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["rows_excluded"] == 5
    assert {item["date"] for item in payload["records"]} == {str(day) for day in days}
    assert all(item["source_id"] == "Peng-00171/BEI.POP/09-2025" for item in payload["records"])


def test_2025_amended_holiday_is_excluded():
    out = build_clean_dataset(_frame([_raw("BBCA", "2025-08-18", 100.0, 0)]))
    assert out.empty
    assert out.attrs["closure_excluded_rows"] == 1


def test_closure_with_positive_volume_is_blocking():
    raw = _frame([_raw("BBCA", "2026-05-14", 100.0, 1000)])
    with pytest.raises(ExchangeClosureContradictionError, match="positive-volume"):
        build_clean_dataset(raw)


def test_closure_with_non_flat_ohlc_is_blocking():
    raw = _frame([_raw(
        "BBCA", "2026-05-14", 100.0, 0,
        open_=99.0, high=101.0, low=98.0,
    )])
    with pytest.raises(ExchangeClosureContradictionError, match="non-flat"):
        build_clean_dataset(raw)


def test_genuine_zero_volume_on_open_session_is_preserved():
    raw = _frame([_raw("BBCA", "2026-05-13", 100.0, 0)])
    out = build_clean_dataset(raw)
    assert len(out) == 1
    assert out.iloc[0]["volume"] == 0
    assert out.attrs["closure_excluded_rows"] == 0


def test_returns_across_holiday_gap_use_last_open_observation():
    raw = _frame([
        _raw("BBCA", "2026-05-13", 100.0, 1000),
        _raw("BBCA", "2026-05-14", 100.0, 0),
        _raw("BBCA", "2026-05-15", 100.0, 0),
        _raw("BBCA", "2026-05-18", 110.0, 1200),
    ])
    out = build_clean_dataset(raw)
    assert list(out["trading_date"].dt.date) == [
        date(2026, 5, 13), date(2026, 5, 18)
    ]
    assert out.iloc[1]["return_1d"] == pytest.approx(0.10)


def test_features_are_not_influenced_by_holiday_bars():
    open_days = verified_open_days(date(2026, 4, 1), date(2026, 6, 15))
    rows = []
    for index, day in enumerate(open_days):
        rows.append(_raw("BBCA", day.isoformat(), 100.0 + index, 1000 + index))
    # Insert closure bars in the middle; they must not affect rolling windows.
    rows.extend(_holiday_rows([date(2026, 5, 14), date(2026, 5, 15)]))
    cleaned = build_clean_dataset(_frame(rows))
    control = build_clean_dataset(_frame([
        row for row in rows if row["tanggal"] not in ("2026-05-14", "2026-05-15")
    ]))
    features = compute_features(cleaned)
    control_features = compute_features(control)
    pd.testing.assert_frame_equal(
        features.reset_index(drop=True), control_features.reset_index(drop=True)
    )


def test_previous_volume_zero_is_null_not_infinite():
    days = verified_open_days(date(2026, 4, 1), date(2026, 5, 10))[:21]
    rows = [
        _raw("BBCA", day.isoformat(), 100.0 + index, 0 if index == 0 else 1000 + index)
        for index, day in enumerate(days)
    ]
    cleaned = build_clean_dataset(_frame(rows))
    features = compute_features(cleaned)
    assert pd.isna(features.iloc[1]["volume_change_1d"])
    assert features["volume_change_1d"].dropna().map(lambda value: value == value).all()


def test_missing_weekday_check_ignores_closures_but_detects_open_gaps():
    frame = pd.DataFrame([
        {"ticker": "BBCA", "trading_date": pd.Timestamp("2026-05-13")},
        {"ticker": "BBCA", "trading_date": pd.Timestamp("2026-05-18")},
    ])
    assert check_missing_weekdays(frame) == []

    partial = pd.DataFrame([
        {"ticker": "A", "trading_date": pd.Timestamp("2026-05-12")},
        {"ticker": "B", "trading_date": pd.Timestamp("2026-05-12")},
        {"ticker": "A", "trading_date": pd.Timestamp("2026-05-13")},
        {"ticker": "A", "trading_date": pd.Timestamp("2026-05-18")},
        {"ticker": "B", "trading_date": pd.Timestamp("2026-05-18")},
    ])
    groups = check_missing_weekdays(partial)
    assert len(groups) == 1
    assert groups[0].ticker == "B"
    assert groups[0].start == date(2026, 5, 13)
    assert groups[0].end == date(2026, 5, 13)


def test_validator_rejects_rows_on_official_closure():
    base = pd.DataFrame([
        {"trading_date": pd.Timestamp("2026-05-14")},
    ])
    checks = validate_exchange_calendar(base)
    assert checks[0].severity == "FAIL"
    assert "closure" in checks[0].message.lower()


def test_news_maps_across_consecutive_closures_to_next_open_session():
    dates = set(verified_open_days(date(2026, 5, 11), date(2026, 5, 20)))
    assert date(2026, 5, 14) not in dates
    assert date(2026, 5, 15) not in dates
    frame = pd.DataFrame({
        "effective_available_at": [
            datetime(2026, 5, 14, 10, 0, tzinfo=JAKARTA),
            datetime(2026, 5, 15, 17, 0, tzinfo=JAKARTA),
        ]
    })
    out = align_news_to_trading_date(frame, dates, time(16, 0), JAKARTA)
    assert list(out["trading_date"]) == [date(2026, 5, 18), date(2026, 5, 18)]
