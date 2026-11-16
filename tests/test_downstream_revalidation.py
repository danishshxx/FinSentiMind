"""Downstream revalidation tests (quant artifact consistency)."""
from datetime import date

import pandas as pd

from ml.features.quant import compute_features
from ml.ops.revalidate_downstream import _quant_consistency
from ml.preprocessing.idx_calendar import verified_open_days


def _frames(sessions: int = 40):
    days = verified_open_days(date(2025, 1, 2), date(2025, 4, 1))[:sessions]
    rows = []
    close = 1000.0
    for index, day in enumerate(days):
        close = close * (1.0 + (0.005 if index % 2 == 0 else -0.003))
        rows.append({
            "ticker": "BBCA",
            "trading_date": pd.Timestamp(day),
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000 + index,
        })
    prices = pd.DataFrame(rows)
    return prices, compute_features(prices)


def test_quant_artifact_matches_recomputation(tmp_path):
    prices, stored = _frames()
    stored.to_parquet(tmp_path / "quant_features.parquet", index=False)
    result = _quant_consistency(tmp_path, prices)
    assert result["status"] == "MATCH"
    assert result["mismatched_columns"] == {}
    # Warm-up periods remain NULL rather than being filled.
    assert result["warmup_null_counts"]["sma_200"] > 0


def test_quant_artifact_mismatch_is_detected(tmp_path):
    prices, stored = _frames()
    stored.loc[stored.index[-1], "rsi_14"] = stored["rsi_14"].iloc[-1] + 1.0
    stored.to_parquet(tmp_path / "quant_features.parquet", index=False)
    result = _quant_consistency(tmp_path, prices)
    assert result["status"] == "MISMATCH"
    assert "rsi_14" in result["mismatched_columns"]


def test_missing_quant_artifact_is_unavailable(tmp_path):
    prices, _ = _frames()
    result = _quant_consistency(tmp_path, prices)
    assert result["status"] == "UNAVAILABLE"
