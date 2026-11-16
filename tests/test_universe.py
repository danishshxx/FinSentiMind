"""Universe inventory, coverage and EOD ingestion tests."""
from datetime import date, timedelta

import pandas as pd
import pytest

from app.core.aliases import LQ45_TICKERS
from ml.universe.coverage import build_coverage_matrix
from ml.universe.inventory import build_inventory
from ml.universe.yfinance_eod import IngestConfig, ingest_universe


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def test_inventory_covers_documented_lq45_and_is_not_claimed_complete():
    inventory = build_inventory(include_lq45=True)
    tickers = {item["ticker"] for item in inventory["instruments"]}
    assert set(LQ45_TICKERS).issubset(tickers)
    assert inventory["complete"] is False
    assert "403" in inventory["completion_reason"]
    assert inventory["counts"]["total"] == len(tickers)


def test_inventory_does_not_fabricate_missing_metadata():
    inventory = build_inventory(include_lq45=True)
    by_ticker = {item["ticker"]: item for item in inventory["instruments"]}
    extra = by_ticker["AKRA"]
    assert extra["company_name"] is None
    assert extra["sector"] is None
    assert extra["instrument_status"] == "unknown"


def test_inventory_seed_fills_blank_fields(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(
        '{"instruments":[{"ticker":"AKRA","company_name":"AKR Corporindo",'
        '"sector":"Industrials","instrument_status":"active"}]}',
        encoding="utf-8",
    )
    inventory = build_inventory(include_lq45=True, seed_path=seed)
    by_ticker = {item["ticker"]: item for item in inventory["instruments"]}
    assert by_ticker["AKRA"]["company_name"] == "AKR Corporindo"
    assert by_ticker["AKRA"]["sector"] == "Industrials"


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _inventory(*tickers):
    return {
        "scope": "TEST",
        "complete": False,
        "instruments": [
            {"ticker": ticker, "instrument_status": "unknown"} for ticker in tickers
        ],
    }


def _sessions(start="2026-06-02", count=10):
    days = []
    cursor = date.fromisoformat(start)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _prices(ticker, days):
    return pd.DataFrame([
        {"ticker": ticker, "trading_date": pd.Timestamp(day),
         "close": 100.0 + index, "volume": 1000}
        for index, day in enumerate(days)
    ])


def _calendar(days):
    return pd.DataFrame([
        {"date": day, "is_trading_day": True} for day in days
    ])


def test_coverage_classifies_complete_partial_unavailable():
    days = _sessions()
    prices = pd.concat([
        _prices("AAAA", days),
        _prices("BBBB", days[:3] + days[-2:]),
    ], ignore_index=True)
    report = build_coverage_matrix(
        _inventory("AAAA", "BBBB", "CCCC"), prices, _calendar(days),
        as_of=days[-1], freshness_days=3,
    )
    by_ticker = {item["ticker"]: item for item in report["matrix"]}
    assert by_ticker["AAAA"]["data_quality_status"] == "COMPLETE"
    assert by_ticker["BBBB"]["data_quality_status"] == "PARTIAL"
    assert by_ticker["CCCC"]["data_quality_status"] == "UNAVAILABLE"
    assert by_ticker["CCCC"]["failure_reason"] == "no_price_history"


def test_coverage_marks_stale_and_failed():
    days = _sessions()
    prices = _prices("AAAA", days)
    report = build_coverage_matrix(
        _inventory("AAAA"), prices, _calendar(days),
        as_of=days[-1] + timedelta(days=10), freshness_days=3,
    )
    assert report["matrix"][0]["data_quality_status"] == "STALE"

    invalid = prices.copy()
    invalid["close"] = 0.0
    failed = build_coverage_matrix(
        _inventory("AAAA"), invalid, _calendar(days), as_of=days[-1]
    )
    assert failed["matrix"][0]["data_quality_status"] == "FAILED"


# ---------------------------------------------------------------------------
# yfinance ingestion (injected fetcher, no network)
# ---------------------------------------------------------------------------

def _fake_rows(start="2026-05-11", count=2):
    days = _sessions(start, count)
    return pd.DataFrame([
        {"trading_date": pd.Timestamp(day), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "adj_close": 100.4, "volume": 1000,
         "dividends": 0.0, "stock_splits": 0.0}
        for day in days
    ])


def test_ingestion_writes_layers_and_reports_missing(tmp_path):
    calls = []

    def fetcher(symbol, start, end):
        calls.append((symbol, start, end))
        if symbol == "BAD.JK":
            raise RuntimeError("provider down")
        return _fake_rows()

    report = ingest_universe(
        ["AAAA", "BAD", "CCCC"], tmp_path,
        IngestConfig(end=date(2026, 5, 12), retries=2, rate_limit_seconds=0),
        fetcher=fetcher, sleep_fn=lambda _: None,
    )
    assert report["returned"] == ["AAAA", "CCCC"]
    assert report["failed"] == ["BAD"]
    assert report["failures"]["BAD"].startswith("RuntimeError")
    assert (tmp_path / "raw" / "yfinance_eod_raw.parquet").is_file()
    assert (tmp_path / "normalized" / "prices_universe.parquet").is_file()
    assert report["total_rows"] == 4


def test_incremental_ingestion_requests_only_new_sessions(tmp_path):
    ranges = []

    def fetcher(symbol, start, end):
        ranges.append((symbol, start, end))
        return _fake_rows(end.isoformat(), count=1)

    ingest_universe(
        ["AAAA"], tmp_path,
        IngestConfig(end=date(2026, 5, 12), rate_limit_seconds=0),
        fetcher=fetcher, sleep_fn=lambda _: None,
    )
    ingest_universe(
        ["AAAA"], tmp_path,
        IngestConfig(end=date(2026, 5, 13), rate_limit_seconds=0),
        fetcher=fetcher, sleep_fn=lambda _: None,
    )
    assert ranges[-1][0] == "AAAA.JK"
    assert ranges[-1][1] == date(2026, 5, 13)
    assert ranges[-1][2] == date(2026, 5, 13)


def test_ingestion_is_idempotent_and_deduplicates(tmp_path):
    def fetcher(symbol, start, end):
        return _fake_rows()

    config = IngestConfig(end=date(2026, 5, 12), rate_limit_seconds=0)
    ingest_universe(["AAAA"], tmp_path, config, fetcher=fetcher, sleep_fn=lambda _: None)
    second = ingest_universe(
        ["AAAA"], tmp_path, config, fetcher=fetcher, sleep_fn=lambda _: None
    )
    # No new fetch is needed once the ticker is up to date.
    assert second["new_rows"] == 0
    assert second["skipped_up_to_date"] == ["AAAA"]
    assert second["total_rows"] == 2
