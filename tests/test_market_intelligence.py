"""Tests for TASK 110 market intelligence and macro adapter safety."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ml.market.intelligence import (
    MacroProvider,
    MacroQuote,
    build_macro_context,
    compute_market_intelligence,
)
from ml.metadata.master import InstrumentMaster


def _master() -> InstrumentMaster:
    payload = {
        "schema_version": 1,
        "metadata_version": "test-market-v1",
        "effective_from": "2024-01-01",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "sources": [
            {"id": "idx30_universe", "name": "test", "fields": ["sector"]},
            {"id": "project_alias_map", "name": "test", "fields": ["company_name"]},
            {"id": "local_prices", "name": "test", "fields": ["listing_status"]},
            {"id": "unavailable", "name": "unavailable", "fields": []},
        ],
        "revisions": [{"version": "test-market-v1", "updated_at": "2024-01-01T00:00:00+00:00"}],
        "instruments": [],
    }
    sectors = ["Banking", "Energy", "Consumer"]
    for index, ticker in enumerate(["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF"]):
        payload["instruments"].append(
            {
                "ticker": ticker,
                "exchange": "IDX",
                "company_name": f"Test {ticker}",
                "sector": sectors[index % len(sectors)],
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
        )
    master = InstrumentMaster.from_dict(payload)
    master.validate()
    return master


def _base(days: int = 100) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rows = []
    for index, ticker in enumerate(["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF"]):
        drift = (index + 1) * 0.0005
        for session, trading_date in enumerate(dates):
            close = 100.0 * (1 + drift) ** session
            rows.append(
                {
                    "ticker": ticker,
                    "trading_date": trading_date,
                    "close": close,
                    "volume": 1000 + session * 10 + index,
                    "return_1d": drift,
                }
            )
    return pd.DataFrame(rows)


def test_market_intelligence_has_breadth_cross_section_and_sectors():
    result = compute_market_intelligence(_base(), _master())

    assert result["as_of"] == "2024-05-17"
    assert result["universe_size"] == 6
    assert result["breadth"]["advancers"] == 6
    assert result["breadth"]["decliners"] == 0
    assert result["cross_section"]["median_return_1d"] == pytest.approx(0.00175)
    assert result["index"]["level"] > 100
    assert result["regime"]["label"] in {
        "bullish",
        "bearish",
        "sideways",
        "high_volatility",
        "unknown",
    }
    assert result["regime"]["methodology"]
    assert len(result["sector_performance"]["sectors"]) == 3
    assert all(sector["covered"] > 0 for sector in result["sector_performance"]["sectors"])
    assert result["macro"]["status"] == "unavailable"
    assert all(item["value"] is None for item in result["macro"]["indicators"])
    assert "bukan IHSG" in result["label"]


def test_as_of_truncation_has_no_future_data():
    base = _base()
    cutoff = date(2024, 3, 15)
    truncated = compute_market_intelligence(base, _master(), as_of=cutoff)
    full = compute_market_intelligence(base, _master())

    assert truncated["as_of"] == cutoff.isoformat()
    assert truncated["index"]["level"] != full["index"]["level"]
    earlier = compute_market_intelligence(
        base[base["trading_date"] <= pd.Timestamp(cutoff)], _master()
    )
    assert truncated["index"]["level"] == pytest.approx(earlier["index"]["level"])
    assert truncated["breadth"] == earlier["breadth"]


class _AvailableProvider(MacroProvider):
    indicator = "USDIDR"
    label = "USD/IDR"
    source = "test"

    def fetch(self) -> MacroQuote:
        return MacroQuote(self.indicator, self.label, 16000.0, "2024-05-17", self.source, "available")


class _BrokenProvider(MacroProvider):
    indicator = "SP500"
    label = "S&P 500"
    source = "test"

    def fetch(self) -> MacroQuote:
        raise RuntimeError("provider offline")


def test_macro_adapters_never_fabricate_unavailable_values():
    available = build_macro_context(providers=[_AvailableProvider()])
    assert available["status"] == "available"
    assert available["indicators"][0]["value"] == 16000.0

    broken = build_macro_context(providers=[_BrokenProvider()])
    assert broken["status"] == "unavailable"
    assert broken["indicators"][0]["value"] is None
    assert "offline" in broken["indicators"][0]["reason"]

    default = build_macro_context(providers=None, yfinance_enabled=False)
    assert default["status"] == "unavailable"
    assert default["total_count"] == 7
    assert all(item["status"] == "unavailable" for item in default["indicators"])
