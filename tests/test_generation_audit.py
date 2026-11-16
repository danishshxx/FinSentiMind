"""Generation acceptance audit tests (data platform completion)."""
from datetime import date

import pandas as pd
import pytest

from ml.preprocessing.dataset_store import REQUIRED_ARTIFACTS, artifact_manifest
from ml.preprocessing.trading_calendar import derive_trading_calendar
from ml.validation.generation_audit import (
    audit_generation,
    write_provenance,
)


def _prices(rows):
    frame = pd.DataFrame(
        [
            {
                "ticker": "BBCA",
                "trading_date": pd.Timestamp(day),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "return_1d": ret,
            }
            for day, close, ret in rows
        ]
    )
    return frame


def _sentiment(rows):
    return pd.DataFrame(
        [
            {
                "ticker": "BBCA",
                "trading_date": date.fromisoformat(day),
                "news_count": 1,
                "sentiment_mean": 0.5,
                "sentiment_std": 0.1,
                "positive_ratio": 1.0,
                "negative_ratio": 0.0,
                "availability_live_ratio": 1.0,
            }
            for day in rows
        ],
        columns=[
            "ticker",
            "trading_date",
            "news_count",
            "sentiment_mean",
            "sentiment_std",
            "positive_ratio",
            "negative_ratio",
            "availability_live_ratio",
        ],
    )


def _base(prices, sentiment):
    base = prices.copy()
    base["trading_date"] = pd.to_datetime(base["trading_date"])
    sent = sentiment.copy()
    sent["trading_date"] = pd.to_datetime(sent["trading_date"])
    base = base.merge(sent, on=["ticker", "trading_date"], how="left")
    base["news_count"] = base["news_count"].fillna(0)
    return base


def _write_generation(root, prices, base, sentiment, calendar=None, quant=None):
    root.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(root / "prices_clean.parquet", index=False)
    base.to_parquet(root / "base_dataset.parquet", index=False)
    sentiment.to_parquet(root / "daily_sentiment.parquet", index=False)
    cal = calendar if calendar is not None else derive_trading_calendar(prices)
    cal.to_parquet(root / "trading_calendar.parquet", index=False)
    if quant is not None:
        quant.to_parquet(root / "quant_features.parquet", index=False)
    return root


def _valid_generation(root):
    prices = _prices([
        ("2026-05-11", 100.0, None),
        ("2026-05-12", 101.0, 0.01),
        ("2026-05-13", 102.0, 0.009900990099009901),
    ])
    sentiment = _sentiment(["2026-05-12"])
    base = _base(prices, sentiment)
    return _write_generation(root, prices, base, sentiment)


def test_valid_generation_passes(tmp_path):
    report = audit_generation(_valid_generation(tmp_path / "gen"))
    assert report["status"] == "PASS"
    assert report["calendar_version"] == "IDX-2024-2026-v2"
    assert report["price_rows"] == 3
    assert report["tickers"] == 1
    assert report["data_as_of"] == "2026-05-13"
    assert set(report["artifacts"]) == set(REQUIRED_ARTIFACTS)


def test_price_row_on_official_closure_fails(tmp_path):
    prices = _prices([
        ("2026-05-13", 100.0, None),
        ("2026-05-14", 100.0, 0.0),
    ])
    sentiment = _sentiment([])
    base = _base(prices, sentiment)
    root = _write_generation(tmp_path / "gen", prices, base, sentiment)
    report = audit_generation(root)
    assert report["status"] == "FAIL"
    assert "no_rows_on_official_closure" in report["failures"]


def test_row_count_mismatch_fails(tmp_path):
    root = _valid_generation(tmp_path / "gen")
    base = pd.read_parquet(root / "base_dataset.parquet").iloc[:-1]
    base.to_parquet(root / "base_dataset.parquet", index=False)
    report = audit_generation(root)
    assert report["status"] == "FAIL"
    assert "row_count_matches_prices" in report["failures"]


def test_future_trading_session_fails(tmp_path):
    root = _valid_generation(tmp_path / "gen")
    calendar = pd.read_parquet(root / "trading_calendar.parquet")
    extra = calendar.iloc[[-1]].copy()
    extra["date"] = pd.Timestamp("2026-05-20").date()
    extra["day_status"] = "VERIFIED_OPEN"
    extra["is_exchange_open"] = True
    extra["is_trading_day"] = True
    pd.concat([calendar, extra], ignore_index=True).to_parquet(
        root / "trading_calendar.parquet", index=False
    )
    report = audit_generation(root)
    assert report["status"] == "FAIL"
    assert "no_future_trading_sessions" in report["failures"]


def test_unmatched_sentiment_must_stay_null(tmp_path):
    prices = _prices([
        ("2026-05-11", 100.0, None),
        ("2026-05-12", 101.0, 0.01),
    ])
    sentiment = _sentiment(["2026-05-12"])
    base = _base(prices, sentiment)
    base.loc[base["trading_date"] == pd.Timestamp("2026-05-11"), "sentiment_mean"] = 0.0
    root = _write_generation(tmp_path / "gen", prices, base, sentiment)
    report = audit_generation(root)
    assert report["status"] == "FAIL"
    assert "sentiment_null_semantics" in report["failures"]


def test_expected_calendar_version_mismatch_fails(tmp_path):
    root = _valid_generation(tmp_path / "gen")
    report = audit_generation(root, expected_calendar_version="IDX-2099-v1")
    assert report["status"] == "FAIL"
    assert "expected_calendar_version" in report["failures"]


def test_provenance_records_checksums_and_versions(tmp_path):
    root = _valid_generation(tmp_path / "gen")
    payload = write_provenance(
        root,
        source_generation="task107b_calendar_reconciliation_20261115",
        calendar_version="IDX-2024-2026-v2",
        data_as_of="2026-05-13",
        feature_version="quant_features_v1",
        universe_scope="IDX30",
    )
    assert payload["calendar_version"] == "IDX-2024-2026-v2"
    assert payload["source_generation"] == "task107b_calendar_reconciliation_20261115"

    manifest = artifact_manifest(root)
    for name, item in manifest.items():
        assert payload["artifacts"][name]["sha256"] == item["sha256"]
        assert payload["artifacts"][name]["rows"] > 0

    # Written provenance must not change the audit outcome.
    assert audit_generation(root)["status"] == "PASS"
