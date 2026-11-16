"""Tests for non-destructive calendar gap classification (TASK 107A)."""
from datetime import date

import pandas as pd

from ml.preprocessing.calendar_audit import (
    COLLECTIVE_LEAVE,
    OFFICIAL_HOLIDAY,
    PARTIAL_OBSERVATION,
    UNEXPLAINED,
    audit_missing_weekday_groups,
    classify_gap_dates,
    render_markdown,
)


def _prices(rows):
    return pd.DataFrame(
        [{"ticker": ticker, "trading_date": pd.Timestamp(day)}
         for ticker, day in rows]
    )


def test_official_holiday_is_classified_without_changing_calendar():
    category, _ = classify_gap_dates(date(2025, 4, 18), date(2025, 4, 18))
    assert category == OFFICIAL_HOLIDAY


def test_collective_leave_is_distinguished_from_official_holiday():
    category, _ = classify_gap_dates(date(2025, 4, 7), date(2025, 4, 7))
    assert category == COLLECTIVE_LEAVE


def test_unknown_weekday_is_not_assumed_to_be_holiday():
    category, _ = classify_gap_dates(date(2025, 2, 3), date(2025, 2, 3))
    assert category == UNEXPLAINED


def test_partial_cross_section_is_not_reclassified_as_holiday():
    prices = _prices([
        ("A", "2025-01-06"), ("B", "2025-01-06"),
        ("A", "2025-01-07"), ("B", "2025-01-07"),
        ("A", "2025-01-08"),
        ("A", "2025-01-09"), ("B", "2025-01-09"),
    ])
    audit = audit_missing_weekday_groups(prices)
    assert audit.total_groups == 1
    assert audit.groups[0].ticker == "B"
    assert audit.groups[0].category == PARTIAL_OBSERVATION
    assert audit.partial_days == 1


def test_official_holiday_is_not_counted_as_missing_provider_group():
    rows = []
    for ticker in ("A", "B", "C"):
        rows.extend([
            (ticker, "2025-04-17"),
            (ticker, "2025-04-21"),
        ])
    audit = audit_missing_weekday_groups(_prices(rows))
    assert audit.total_groups == 0
    assert audit.category_group_counts == {}


def test_report_is_explicit_and_does_not_invent_closures():
    prices = _prices([
        ("A", "2025-01-06"),
        ("A", "2025-01-08"),
    ])
    audit = audit_missing_weekday_groups(prices)
    text = render_markdown(audit, source=__import__("pathlib").Path("fixture.parquet"))
    assert "822" in text
    assert "UNEXPLAINED" in text
    assert "does not fabricate sessions" in text
