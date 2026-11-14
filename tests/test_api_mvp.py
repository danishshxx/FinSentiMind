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


def test_market_intelligence_endpoint(client: TestClient):
    response = client.get("/api/market/intelligence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["label"] == "Equal-weight local universe proxy (bukan IHSG)"
    assert payload["regime"]["label"] in {
        "bullish",
        "bearish",
        "sideways",
        "high_volatility",
        "unknown",
    }
    assert payload["macro"]["status"] == "unavailable"
    assert payload["sector_performance"]["sectors"] == []


def test_research_latest_endpoint_is_research_only(client: TestClient):
    response = client.get("/api/research/latest")
    assert response.status_code == 200
    payload = response.json()
    assert "available" in payload


def test_capabilities_expose_unavailable_modules(client: TestClient):
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["research_status"] == "NOT_VALIDATED"
    assert payload["live_trading"] == "NOT_IMPLEMENTED"
    assert "provider_matrix" in payload


def test_fundamental_endpoint_is_explicitly_unavailable(client: TestClient):
    response = client.get("/api/fundamentals/AAA")
    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"


def test_capital_flow_endpoint_is_explicitly_unavailable(client: TestClient):
    response = client.get("/api/capital-flow/market")
    assert response.status_code == 200
    assert response.json()["summary"]["status"] == "UNAVAILABLE"


def test_macro_endpoint_is_explicitly_unavailable(client: TestClient):
    response = client.get("/api/macro")
    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"


def test_portfolio_endpoint_returns_installed_items(client: TestClient):
    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.json()["status"] == "OK"


def test_risk_lab_endpoint_runs_on_local_history(client: TestClient):
    response = client.get("/api/risk/lab", params={"tickers": "AAA,BBB"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["minimum_variance"]["weights"]
    assert payload["not_for_trading"] is True


def test_security_headers_are_present(client: TestClient):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_research_notes_endpoint_is_explicit(client: TestClient):
    response = client.get("/api/research/notes/AAA")
    assert response.status_code == 200
    assert response.json()["status"] in {"OK", "UNAVAILABLE"}


def test_invalid_portfolio_and_risk_inputs_are_rejected(client: TestClient):
    assert client.get("/api/portfolio/bad id").status_code == 422
    assert client.get("/api/risk/lab", params={"tickers": "AAA,<bad>"}).status_code == 422


def test_data_health_endpoint(client: TestClient):
    response = client.get("/api/data/health", params={"freshness_days": 7})
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] > 0
    assert payload["dataset"]["tickers"] == 3
    assert payload["sources"]
    assert payload["notes"]


def test_instrument_master_endpoints(client: TestClient):
    listing = client.get("/api/instruments")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["count"] == 30
    assert payload["metadata_version"] == "idx30-2026-08-03-v1"
    bbca = next(item for item in payload["instruments"] if item["ticker"] == "BBCA")
    assert bbca["sector"] == "Banking"
    assert bbca["company_name"] == "Bank Central Asia"
    aadi = next(item for item in payload["instruments"] if item["ticker"] == "AADI")
    assert aadi["company_name"] is None

    detail = client.get("/api/instruments/bbca")
    assert detail.status_code == 200
    assert detail.json()["ticker"] == "BBCA"

    missing = client.get("/api/instruments/ZZZZ")
    assert missing.status_code == 404


def test_stock_detail_and_explorer_expose_metadata_fields(client: TestClient):
    detail = client.get("/api/stocks/AAA")
    assert detail.status_code == 200
    instrument = detail.json()["instrument"]
    assert instrument["metadata_status"] == "unavailable"
    assert instrument["company_name"] is None
    quant = detail.json()["quant_features"]
    assert quant["version"] == "quant_features_v1"
    assert "return_5d" in quant["values"]
    assert quant["missing"]

    listing = client.get("/api/stocks", params={"search": "AAA"}).json()
    item = listing["items"][0]
    assert "sector" in item
    assert item["metadata_status"] == "unavailable"


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
        assert 'id="detail-company"' in index.text
        assert 'id="intel-regime"' in index.text
        assert 'id="intel-sector-meta"' in index.text

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
