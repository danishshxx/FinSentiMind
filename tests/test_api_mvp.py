"""API and static-frontend tests for the FinSentiMind MVP terminal."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_market_data import _build_frames


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    base, prices, sentiment, calendar = _build_frames()
    base.to_parquet(tmp_path / "base_dataset.parquet", index=False)
    prices.to_parquet(tmp_path / "prices_clean.parquet", index=False)
    sentiment.to_parquet(tmp_path / "daily_sentiment.parquet", index=False)
    calendar.to_parquet(tmp_path / "trading_calendar.parquet", index=False)
    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    return TestClient(create_app(data_dir=data_dir))


def test_liveness_and_meta(client: TestClient):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["dataset_available"] is True
    assert health.json()["read_only"] is True

    meta = client.get("/api/meta")
    assert meta.status_code == 200
    payload = meta.json()
    assert payload["universe_size"] == 3
    assert payload["latest_trading_date"] == "2025-01-10"
    assert payload["mode"] == "local-research-snapshot"


def test_market_overview(client: TestClient):
    response = client.get("/api/market/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == "2025-01-10"
    assert payload["top_gainers"]
    assert payload["sentiment"]["rows_with_news"] == 1


def test_stock_explorer_filters(client: TestClient):
    response = client.get("/api/stocks", params={"search": "AA", "sort": "ticker"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["ticker"] == "AAA"

    watch = client.get("/api/stocks", params={"tickers": "BBB,CCC"})
    assert {item["ticker"] for item in watch.json()["items"]} == {"BBB", "CCC"}

    invalid = client.get("/api/stocks", params={"order": "sideways"})
    assert invalid.status_code == 422


def test_stock_detail_and_404(client: TestClient):
    detail = client.get("/api/stocks/AAA")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["ticker"] == "AAA"
    assert payload["latest"]["close"] == 1060.0
    assert payload["data_quality"]["observed_sessions"] == 7
    assert "sentiment_history" in payload

    missing = client.get("/api/stocks/ZZZ")
    assert missing.status_code == 404
    assert "tidak ada" in missing.json()["detail"].lower()


def test_ohlcv_endpoint(client: TestClient):
    response = client.get("/api/stocks/AAA/ohlcv", params={"limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["items"][-1]["trading_date"] == "2025-01-10"

    bad_range = client.get(
        "/api/stocks/AAA/ohlcv",
        params={"start": "2025-02-01", "end": "2025-01-01"},
    )
    assert bad_range.status_code == 422

    bad_limit = client.get("/api/stocks/AAA/ohlcv", params={"limit": 5000})
    assert bad_limit.status_code == 422

    missing = client.get("/api/stocks/ZZZ/ohlcv")
    assert missing.status_code == 404


def test_data_health_endpoint(client: TestClient):
    response = client.get("/api/data/health", params={"freshness_days": 7})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] > 0
    assert payload["dataset"]["tickers"] == 3
    assert payload["sources"]
    assert payload["notes"]


def test_write_methods_are_not_exposed(client: TestClient):
    response = client.post("/api/stocks", json={"ticker": "AAA"})
    assert response.status_code == 405


def test_frontend_is_served(data_dir: Path):
    app = create_app(data_dir=data_dir)
    with TestClient(app) as test_client:
        index = test_client.get("/")
        assert index.status_code == 200
        assert "FinSentiMind" in index.text
        assert 'id="price-chart"' in index.text
        assert 'id="monitor-chart"' in index.text
        assert 'id="command-palette"' in index.text
        assert 'id="watchlist-stats"' in index.text

        script = test_client.get("/app.js")
        assert script.status_code == 200
        assert "renderChart" in script.text
        assert "renderMonitor" in script.text
        assert "openPalette" in script.text

        styles = test_client.get("/styles.css")
        assert styles.status_code == 200
        assert ".data-table" in styles.text
        assert ".monitor-top" in styles.text
        assert ".palette-panel" in styles.text
