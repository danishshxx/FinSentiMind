"""Independent numerical tests for TASK 109 quant features."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.features.quant import (
    FEATURE_COLUMNS,
    FEATURE_VERSION,
    FeatureError,
    build_feature_dataset,
    compute_features,
)


def _prices(days: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    close = pd.Series([100.0 + index for index in range(days)])
    return pd.DataFrame(
        {
            "ticker": "AAAA",
            "trading_date": dates,
            "open": close - 0.5,
            "high": close + 2.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1000.0 + index for index in range(days)],
        }
    )


def test_returns_sma_and_ema_match_manual_examples():
    features = compute_features(_prices())
    close = _prices()["close"]

    assert features.loc[1, "return_1d"] == pytest.approx(101.0 / 100.0 - 1.0)
    assert features.loc[5, "return_5d"] == pytest.approx(105.0 / 100.0 - 1.0)
    assert features.loc[20, "return_20d"] == pytest.approx(120.0 / 100.0 - 1.0)
    assert features.loc[5, "momentum_5"] == pytest.approx(5.0)
    assert features.loc[19, "sma_20"] == pytest.approx(close.iloc[:20].mean())
    assert features.loc[199, "sma_200"] == pytest.approx(close.iloc[:200].mean())

    alpha = 2.0 / (20.0 + 1.0)
    expected_ema = close.iloc[0]
    for index in range(1, 20):
        expected_ema = close.iloc[index] * alpha + expected_ema * (1 - alpha)
    assert features.loc[19, "ema_20"] == pytest.approx(expected_ema)


def test_warmup_values_remain_null():
    features = compute_features(_prices())

    assert features.loc[:18, "sma_20"].isna().all()
    assert pd.notna(features.loc[19, "sma_20"])
    assert features.loc[:198, "sma_200"].isna().all()
    assert pd.notna(features.loc[199, "sma_200"])
    assert features.loc[:13, "rsi_14"].isna().all()
    assert pd.notna(features.loc[14, "rsi_14"])


def test_rsi_and_atr_edge_cases():
    features = compute_features(_prices())
    assert features["rsi_14"].dropna().iloc[-1] == pytest.approx(100.0)
    assert features["atr_14"].dropna().iloc[-1] == pytest.approx(3.0)

    falling = _prices()
    falling["close"] = 500.0 - np.arange(len(falling))
    falling["high"] = falling["close"] + 2.0
    falling["low"] = falling["close"] - 1.0
    falling_features = compute_features(falling)
    assert falling_features["rsi_14"].dropna().iloc[-1] == pytest.approx(0.0)

    flat = _prices()
    flat["close"] = 100.0
    flat["high"] = 100.0
    flat["low"] = 100.0
    flat_features = compute_features(flat)
    assert flat_features["rsi_14"].dropna().iloc[-1] == pytest.approx(50.0)


def test_volatility_and_relative_volume_match_manual_windows():
    prices = _prices()
    features = compute_features(prices)
    returns = prices["close"].pct_change()
    expected_vol = returns.iloc[1:21].std(ddof=1)
    assert features.loc[20, "volatility_20"] == pytest.approx(expected_vol)
    expected_rel = prices.loc[19, "volume"] / prices.loc[:19, "volume"].mean()
    assert features.loc[19, "relative_volume_20"] == pytest.approx(expected_rel)


def test_no_future_observations_change_past_features():
    prices = _prices(220)
    full = compute_features(prices)
    truncated = compute_features(prices.iloc[:150])
    row_full = full.loc[120, FEATURE_COLUMNS]
    row_truncated = truncated.loc[120, FEATURE_COLUMNS]
    pd.testing.assert_series_equal(
        row_full, row_truncated, check_names=False, rtol=1e-12, atol=1e-12
    )


def test_duplicate_keys_and_missing_columns_are_rejected():
    prices = _prices(30)
    duplicated = pd.concat([prices, prices.iloc[[0]]], ignore_index=True)
    with pytest.raises(FeatureError, match="Duplicate"):
        compute_features(duplicated)

    with pytest.raises(FeatureError, match="Missing required"):
        compute_features(prices.drop(columns=["high"]))


def test_build_writes_versioned_dataset_manifest_and_quality_report(tmp_path: Path):
    input_path = tmp_path / "prices.parquet"
    output_path = tmp_path / "features" / FEATURE_VERSION / "quant_features.parquet"
    manifest_path = tmp_path / "features" / FEATURE_VERSION / "feature_manifest.json"
    report_path = tmp_path / "reports" / "quality.json"
    _prices(80).to_parquet(input_path, index=False)

    build = build_feature_dataset(input_path, output_path, manifest_path, report_path)

    assert output_path.exists()
    assert manifest_path.exists()
    assert report_path.exists()
    assert len(build.features) == 80
    assert build.manifest["feature_version"] == FEATURE_VERSION
    assert build.manifest["lookahead_safe"] is True
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["rows"] == 80
    assert report["tickers"] == 1
    assert report["duplicate_keys"] == 0
    assert set(report["feature_stats"]) == set(FEATURE_COLUMNS)
    assert report["feature_stats"]["sma_200"]["non_null"] == 0
