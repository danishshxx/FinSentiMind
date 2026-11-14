"""Tests for the MVP market data repository using temporary Parquet fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.market_data import (
    MarketDataRepository,
    TickerNotFoundError,
)
from ml.preprocessing.dataset_store import publish_generation


TICKERS = ["AAA", "BBB", "CCC"]
DATES = pd.bdate_range("2025-01-02", periods=7)


def _build_frames():
    rows = []
    for ticker_index, ticker in enumerate(TICKERS):
        closes = []
        for index in range(len(DATES)):
            if ticker == "AAA":
                close = 1000.0 + index * 10
            elif ticker == "BBB":
                close = 2000.0 - index * 5
            else:
                close = 500.0 + (index % 2) * 5
            closes.append(close)
            rows.append(
                {
                    "ticker": ticker,
                    "trading_date": DATES[index],
                    "open": close - 4,
                    "high": close + 10,
                    "low": close - 10,
                    "close": close,
                    "volume": 1000 + index * 100 + ticker_index * 10,
                    "return_1d": None,
                }
            )
    prices = pd.DataFrame(rows)
    prices["ticker"] = prices["ticker"].astype("string")
    prices["trading_date"] = pd.to_datetime(prices["trading_date"])
    prices["return_1d"] = prices.groupby("ticker")["close"].pct_change()
    prices = prices.sort_values(["ticker", "trading_date"]).reset_index(drop=True)

    base = prices.copy()
    base["news_count"] = 0
    for column in (
        "sentiment_mean",
        "sentiment_std",
        "positive_ratio",
        "negative_ratio",
        "availability_live_ratio",
    ):
        base[column] = np.nan
    news_mask = (base["ticker"] == "BBB") & (base["trading_date"] == DATES[5])
    base.loc[news_mask, "news_count"] = 2
    base.loc[news_mask, "sentiment_mean"] = 0.5
    base.loc[news_mask, "sentiment_std"] = 0.1
    base.loc[news_mask, "positive_ratio"] = 0.5
    base.loc[news_mask, "negative_ratio"] = 0.0
    base.loc[news_mask, "availability_live_ratio"] = 1.0
    base = base[
        [
            "ticker",
            "trading_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "return_1d",
            "news_count",
            "sentiment_mean",
            "sentiment_std",
            "positive_ratio",
            "negative_ratio",
            "availability_live_ratio",
        ]
    ]

    sentiment = base.loc[news_mask, [
        "ticker",
        "trading_date",
        "news_count",
        "sentiment_mean",
        "sentiment_std",
        "positive_ratio",
        "negative_ratio",
        "availability_live_ratio",
    ]].copy()
    sentiment["trading_date"] = sentiment["trading_date"].dt.date

    calendar = pd.DataFrame(
        {
            "date": DATES,
            "ticker_count": len(TICKERS),
            "available_count": len(TICKERS),
            "coverage_ratio": 1.0,
            "is_trading_day": True,
        }
    )
    return base, prices, sentiment, calendar


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    base, prices, sentiment, calendar = _build_frames()
    base.to_parquet(tmp_path / "base_dataset.parquet", index=False)
    prices.to_parquet(tmp_path / "prices_clean.parquet", index=False)
    sentiment.to_parquet(tmp_path / "daily_sentiment.parquet", index=False)
    calendar.to_parquet(tmp_path / "trading_calendar.parquet", index=False)
    return tmp_path


@pytest.fixture()
def repo(data_dir: Path) -> MarketDataRepository:
    return MarketDataRepository(data_dir)


def test_meta_and_overview(repo: MarketDataRepository):
    meta = repo.meta()
    assert meta["universe_size"] == 3
    assert meta["latest_trading_date"] == "2025-01-10"
    assert meta["sentiment_rows_with_news"] == 1
    assert all(source["exists"] for source in meta["sources"])

    overview = repo.overview()
    assert overview["as_of"] == "2025-01-10"
    assert overview["universe_size"] == 3
    assert overview["advancers"] == 1
    assert overview["decliners"] == 2
    assert overview["top_gainers"][0]["ticker"] == "AAA"
    assert overview["top_losers"][0]["ticker"] == "CCC"
    assert all(item["return_1d"] > 0 for item in overview["top_gainers"])
    assert all(item["return_1d"] < 0 for item in overview["top_losers"])
    assert overview["sentiment"]["rows_with_news"] == 1
    assert overview["sentiment"]["tickers_with_news"] == 1


def test_list_stocks_filters_and_sorting(repo: MarketDataRepository):
    payload = repo.list_stocks(sort="return_1d", order="desc")
    assert payload["count"] == 3
    assert payload["items"][0]["ticker"] == "AAA"
    assert payload["items"][0]["price_rows"] == 7

    filtered = repo.list_stocks(search="BB")
    assert [item["ticker"] for item in filtered["items"]] == ["BBB"]

    selected = repo.list_stocks(tickers=["CCC", "AAA"])
    assert {item["ticker"] for item in selected["items"]} == {"AAA", "CCC"}

    unknown = repo.list_stocks(tickers=["ZZZ"])
    assert unknown["items"] == []


def test_stock_detail_and_sentiment_history(repo: MarketDataRepository):
    detail = repo.stock_detail("bbb")
    assert detail["ticker"] == "BBB"
    assert detail["latest"]["close"] == 1970.0
    assert detail["stats"]["price_rows"] == 7
    assert detail["data_quality"]["sentiment_days"] == 1
    assert detail["data_quality"]["session_coverage_pct"] == 100.0
    assert len(detail["sentiment_history"]) == 1
    assert detail["sentiment_history"][0]["sentiment_mean"] == 0.5
    assert detail["recent_sessions"][0]["trading_date"] == "2025-01-10"

    with pytest.raises(TickerNotFoundError):
        repo.stock_detail("ZZZ")


def test_ohlcv_ranges_and_limit(repo: MarketDataRepository):
    payload = repo.ohlcv("AAA", limit=3)
    assert payload["count"] == 3
    assert payload["items"][0]["trading_date"] == "2025-01-08"
    assert payload["items"][-1]["trading_date"] == "2025-01-10"
    assert payload["items"][-1]["close"] == 1060.0

    window = repo.ohlcv("AAA", start=DATES[1].date(), end=DATES[3].date(), limit=100)
    assert window["count"] == 3
    assert window["start"] == "2025-01-03"
    assert window["end"] == "2025-01-07"


def test_data_health_reuses_validator(repo: MarketDataRepository):
    health = repo.data_health(freshness_threshold_days=7)
    assert health["summary"]["total"] > 0
    assert health["status"] in {"PASS", "FAIL"}
    assert health["dataset"]["rows"] == 21
    assert health["dataset"]["tickers"] == 3
    assert health["sources"][0]["exists"] is True
    names = {check["name"] for check in health["checks"]}
    assert "freshness" in names
    assert "sentiment_coverage" in names


def test_missing_dataset_is_reported(tmp_path: Path):
    missing = MarketDataRepository(tmp_path)
    from app.services.market_data import DataUnavailableError

    with pytest.raises(DataUnavailableError):
        missing.meta()


def test_repository_resolves_active_generation_and_reload(tmp_path: Path):
    base, prices, sentiment, calendar = _build_frames()
    generation_dir = tmp_path / "staging" / "gen-1"
    generation_dir.mkdir(parents=True)
    newer = pd.concat(
        [
            base,
            pd.DataFrame(
                [
                    {
                        "ticker": "AAA",
                        "trading_date": pd.Timestamp("2025-01-13"),
                        "open": 1060.0,
                        "high": 1070.0,
                        "low": 1050.0,
                        "close": 1065.0,
                        "volume": 2000,
                        "return_1d": 0.01,
                        "news_count": 0,
                        "sentiment_mean": np.nan,
                        "sentiment_std": np.nan,
                        "positive_ratio": np.nan,
                        "negative_ratio": np.nan,
                        "availability_live_ratio": np.nan,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    newer.to_parquet(generation_dir / "base_dataset.parquet", index=False)
    prices.to_parquet(generation_dir / "prices_clean.parquet", index=False)
    sentiment.to_parquet(generation_dir / "daily_sentiment.parquet", index=False)
    calendar.to_parquet(generation_dir / "trading_calendar.parquet", index=False)
    publish_generation(
        generation_dir,
        tmp_path,
        "gen-1",
        {"data_as_of": "2025-01-13", "refreshed_at": "2025-01-14T00:00:00+00:00"},
    )

    repo = MarketDataRepository(tmp_path)
    assert repo.active_dataset.source == "manifest"
    assert repo.active_dataset.run_id == "gen-1"
    assert repo.latest_date() == "2025-01-13"
    assert repo.meta()["latest_trading_date"] == "2025-01-13"

    generation_two = tmp_path / "staging" / "gen-2"
    generation_two.mkdir(parents=True)
    newest = newer.copy()
    newest.loc[newest.index[-1], "trading_date"] = pd.Timestamp("2025-01-14")
    newest.to_parquet(generation_two / "base_dataset.parquet", index=False)
    prices.to_parquet(generation_two / "prices_clean.parquet", index=False)
    sentiment.to_parquet(generation_two / "daily_sentiment.parquet", index=False)
    calendar.to_parquet(generation_two / "trading_calendar.parquet", index=False)
    publish_generation(generation_two, tmp_path, "gen-2", {"data_as_of": "2025-01-14"})

    repo.reload()
    assert repo.active_dataset.run_id == "gen-2"
    assert repo.latest_date() == "2025-01-14"


def test_repository_integrates_verified_instrument_metadata(tmp_path: Path):
    payload = {
        "schema_version": 1,
        "metadata_version": "test-v1",
        "effective_from": "2025-01-01",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "sources": [
            {"id": "idx30_universe", "name": "test", "fields": ["sector"]},
            {"id": "project_alias_map", "name": "test", "fields": ["company_name"]},
            {"id": "local_prices", "name": "test", "fields": ["listing_status"]},
            {"id": "unavailable", "name": "unavailable", "fields": []},
        ],
        "revisions": [{"version": "test-v1", "updated_at": "2025-01-01T00:00:00+00:00"}],
        "instruments": [
            {
                "ticker": "AAAA",
                "exchange": "IDX",
                "company_name": "Test Company",
                "sector": "Energy",
                "subsector": None,
                "listing_status": "active",
                "listing_date": None,
                "previous_tickers": [],
                "successor_ticker": None,
                "provenance": {
                    "company_name": "project_alias_map",
                    "sector": "idx30_universe",
                    "listing_status": "local_prices",
                },
            }
        ],
    }
    master_path = tmp_path / "instruments.json"
    master_path.write_text(json.dumps(payload), encoding="utf-8")
    base, prices, sentiment, calendar = _build_frames()
    for frame in (base, prices, sentiment):
        frame["ticker"] = frame["ticker"].replace("AAA", "AAAA")
    base.to_parquet(tmp_path / "base_dataset.parquet", index=False)
    prices.to_parquet(tmp_path / "prices_clean.parquet", index=False)
    sentiment.to_parquet(tmp_path / "daily_sentiment.parquet", index=False)
    calendar.to_parquet(tmp_path / "trading_calendar.parquet", index=False)

    repo = MarketDataRepository(tmp_path, instrument_master_path=master_path)
    item = next(row for row in repo.list_stocks()["items"] if row["ticker"] == "AAAA")
    assert item["company_name"] == "Test Company"
    assert item["sector"] == "Energy"
    assert item["metadata_status"] == "partial"
    detail = repo.stock_detail("AAAA")
    assert detail["instrument"]["company_name"] == "Test Company"
