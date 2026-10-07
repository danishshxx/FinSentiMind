"""Tests for price inventory analysis. Uses in-memory DataFrames — no network."""
from datetime import date, timedelta

import pandas as pd
import pytest

from ml.preprocessing.inventory import (
    build_report,
    compute_per_ticker_inventory,
    compute_session_coverage,
    identify_consensus_sessions,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df(rows):
    """Build a DataFrame from (ticker, date) tuples."""
    return pd.DataFrame(
        [{"kode_saham": t, "tanggal": d} for t, d in rows]
    )


def _dates(start: date, n: int):
    return [start + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# compute_per_ticker_inventory
# ---------------------------------------------------------------------------

def test_per_ticker_counts_and_range():
    rows = [
        ("BBCA", date(2024, 1, 1)),
        ("BBCA", date(2024, 1, 2)),
        ("BBCA", date(2024, 1, 3)),
        ("BBRI", date(2024, 1, 1)),
    ]
    inv = compute_per_ticker_inventory(_df(rows), ["BBCA", "BBRI"], min_history=3)
    by = {t.ticker: t for t in inv}

    assert by["BBCA"].row_count == 3
    assert by["BBCA"].first_date == date(2024, 1, 1)
    assert by["BBCA"].last_date == date(2024, 1, 3)
    assert by["BBCA"].meets_min_history is True

    assert by["BBRI"].row_count == 1
    assert by["BBRI"].meets_min_history is False


def test_per_ticker_handles_zero_rows():
    inv = compute_per_ticker_inventory(_df([]), ["BBCA", "GOTO"], min_history=100)
    by = {t.ticker: t for t in inv}
    assert by["BBCA"].row_count == 0
    assert by["BBCA"].first_date is None
    assert by["BBCA"].last_date is None
    assert by["BBCA"].meets_min_history is False


def test_per_ticker_empty_df_returns_all_tickers():
    inv = compute_per_ticker_inventory(pd.DataFrame(columns=["kode_saham", "tanggal"]),
                                       ["A", "B", "C"], min_history=1)
    assert {t.ticker for t in inv} == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# compute_session_coverage
# ---------------------------------------------------------------------------

def test_session_coverage_counts_unique_tickers_per_date():
    rows = [
        ("BBCA", date(2024, 1, 1)),
        ("BBRI", date(2024, 1, 1)),
        ("TLKM", date(2024, 1, 1)),
        ("BBCA", date(2024, 1, 2)),
    ]
    cov = compute_session_coverage(_df(rows), universe_size=3)
    by = {row["tanggal"]: row for _, row in cov.iterrows()}

    assert by[date(2024, 1, 1)]["ticker_count"] == 3
    assert by[date(2024, 1, 1)]["coverage_ratio"] == 1.0
    assert by[date(2024, 1, 2)]["ticker_count"] == 1
    assert by[date(2024, 1, 2)]["coverage_ratio"] == pytest.approx(1 / 3)


def test_session_coverage_empty():
    cov = compute_session_coverage(_df([]), universe_size=10)
    assert cov.empty
    assert list(cov.columns) == ["tanggal", "ticker_count", "coverage_ratio"]


def test_session_coverage_sorted_ascending():
    rows = [
        ("BBCA", date(2024, 1, 3)),
        ("BBCA", date(2024, 1, 1)),
        ("BBCA", date(2024, 1, 2)),
    ]
    cov = compute_session_coverage(_df(rows), universe_size=1)
    assert cov["tanggal"].tolist() == [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]


# ---------------------------------------------------------------------------
# identify_consensus_sessions
# ---------------------------------------------------------------------------

def test_consensus_threshold_filters():
    cov = pd.DataFrame(
        [
            {"tanggal": date(2024, 1, 1), "ticker_count": 10, "coverage_ratio": 1.0},
            {"tanggal": date(2024, 1, 2), "ticker_count": 5, "coverage_ratio": 0.5},
            {"tanggal": date(2024, 1, 3), "ticker_count": 7, "coverage_ratio": 0.7},
        ]
    )
    consensus = identify_consensus_sessions(cov, threshold=0.6)
    assert consensus == [date(2024, 1, 1), date(2024, 1, 3)]


def test_consensus_empty_input():
    assert identify_consensus_sessions(
        pd.DataFrame(columns=["tanggal", "ticker_count", "coverage_ratio"]),
        threshold=0.6,
    ) == []


# ---------------------------------------------------------------------------
# build_report — backfill decision
# ---------------------------------------------------------------------------

def test_report_no_backfill_needed():
    # 2 tickers × 750 rows over 749 days, both meet min_history, span > 700
    base = date(2024, 1, 1)
    rows = []
    for t in ("BBCA", "BBRI"):
        for d in _dates(base, 750):   # ← GANTI dari 500
            rows.append((t, d))

    report = build_report(
        df=_df(rows),
        universe=["BBCA", "BBRI"],
        min_history=400,
        consensus_threshold=0.6,
        coverage_target=0.9,
    )
    assert report.needs_backfill is False
    assert report.coverage_ratio == 1.0
    assert report.missing_tickers == []


def test_report_backfill_needed_missing_ticker():
    rows = [
        ("BBCA", date(2024, 1, 1)),
        ("BBCA", date(2024, 1, 2)),
    ]
    report = build_report(
        df=_df(rows),
        universe=["BBCA", "GOTO"],
        min_history=1,
        consensus_threshold=0.6,
        coverage_target=0.9,
    )
    assert report.needs_backfill is True
    assert "GOTO" in report.missing_tickers
    assert any("ZERO rows" in r for r in report.backfill_reasons)


def test_report_backfill_needed_short_span():
    # 2 tickers, 400+ rows each, but span only 500 days
    base = date(2024, 1, 1)
    rows = []
    for t in ("BBCA", "BBRI"):
        for d in _dates(base, 450):
            rows.append((t, d))

    report = build_report(
        df=_df(rows),
        universe=["BBCA", "BBRI"],
        min_history=400,
        consensus_threshold=0.6,
        coverage_target=0.9,
    )
    assert report.needs_backfill is True
    assert any("Date span" in r for r in report.backfill_reasons)


def test_report_coverage_below_target():
    # 3 tickers, only 2 meet min_history → 66% < 90%
    base = date(2024, 1, 1)
    rows = []
    for t in ("BBCA", "BBRI"):
        for d in _dates(base, 450):
            rows.append((t, d))
    # TLKM has only 10 rows
    for d in _dates(base, 10):
        rows.append(("TLKM", d))

    report = build_report(
        df=_df(rows),
        universe=["BBCA", "BBRI", "TLKM"],
        min_history=400,
        consensus_threshold=0.6,
        coverage_target=0.9,
    )
    assert report.needs_backfill is True
    assert report.coverage_ratio < 0.9


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_contains_key_sections():
    base = date(2024, 1, 1)
    rows = [("BBCA", d) for d in _dates(base, 450)]
    report = build_report(
        df=_df(rows),
        universe=["BBCA"],
        min_history=400,
        consensus_threshold=0.6,
        coverage_target=0.9,
    )
    md = render_markdown(report)

    assert "# FinSentiMind — Price Data Inventory" in md
    assert "## Coverage Summary" in md
    assert "## Per-Ticker Inventory" in md
    assert "## Backfill Recommendation" in md
    assert "BBCA" in md