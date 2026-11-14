"""Tests for non-destructive volume anomaly auditing (TASK 107A)."""
from datetime import date

import pandas as pd

from ml.preprocessing.anomaly_audit import (
    compare_volume_changes,
    render_volume_audit,
)


def _frame(rows):
    return pd.DataFrame([
        {"ticker": ticker, "trading_date": pd.Timestamp(day), "volume": volume}
        for ticker, day, volume in rows
    ])


def test_material_volume_change_is_reported_without_rewriting_values():
    old = _frame([("GOTO", "2026-10-09", 32_994_912)])
    new = _frame([("GOTO", "2026-10-09", 12_917_896_800)])
    changes = compare_volume_changes(old, new)
    assert len(changes) == 1
    change = changes[0]
    assert change.ticker == "GOTO"
    assert change.trading_date == date(2026, 10, 9)
    assert change.old_volume == 32_994_912
    assert change.new_volume == 12_917_896_800
    assert change.ratio > 1.5


def test_small_volume_change_is_not_flagged():
    old = _frame([("BBCA", "2026-10-09", 100)])
    new = _frame([("BBCA", "2026-10-09", 110)])
    assert compare_volume_changes(old, new) == []


def test_zero_and_missing_rows_are_skipped():
    old = _frame([
        ("A", "2026-10-09", 0),
        ("B", "2026-10-09", 100),
    ])
    new = _frame([
        ("A", "2026-10-09", 1000),
        ("B", "2026-10-09", 100),
    ])
    assert compare_volume_changes(old, new) == []


def test_report_keeps_both_observed_values(tmp_path):
    old = _frame([("GOTO", "2026-10-09", 32_994_912)])
    new = _frame([("GOTO", "2026-10-09", 12_917_896_800)])
    text = render_volume_audit(
        compare_volume_changes(old, new),
        old_path=tmp_path / "old.parquet",
        new_path=tmp_path / "new.parquet",
    )
    assert "32,994,912" in text
    assert "12,917,896,800" in text
    assert "No unit conversion" in text
