"""Deterministic, leakage-safe quantitative features from local OHLCV.

All rolling calculations use only current and prior rows. Warm-up periods stay
NULL. Prices are assumed to be the adjusted/cleaned close stored in
``prices_clean.parquet``; no corporate-action adjustment is performed here.

Feature version: ``quant_features_v1``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import resolve_active_dataset_dir


logger = get_logger(__name__)

FEATURE_VERSION = "quant_features_v1"
FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "return_20d",
    "log_return_1d",
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_20",
    "ema_50",
    "rsi_14",
    "momentum_5",
    "momentum_20",
    "volatility_20",
    "atr_14",
    "volume_sma_20",
    "relative_volume_20",
    "volume_change_1d",
]
KEY_COLUMNS = ["ticker", "trading_date"]
REQUIRED_INPUT = ["ticker", "trading_date", "open", "high", "low", "close", "volume"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = _PROJECT_ROOT / "ml" / "data" / "features" / FEATURE_VERSION / "quant_features.parquet"
DEFAULT_MANIFEST = DEFAULT_OUTPUT.parent / "feature_manifest.json"
DEFAULT_REPORT = _PROJECT_ROOT / "ml" / "reports" / f"{FEATURE_VERSION}_quality.json"


class FeatureError(ValueError):
    """Raised when a feature input is invalid."""


@dataclass(frozen=True)
class FeatureBuild:
    features: pd.DataFrame
    quality: Dict[str, Any]
    manifest: Dict[str, Any]


def _require_columns(frame: pd.DataFrame, required: List[str]) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise FeatureError(f"Missing required OHLCV columns: {missing}")


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, np.nan)
    return numerator / denominator


def _compute_one(group: pd.DataFrame) -> pd.DataFrame:
    frame = group.sort_values("trading_date").copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"], errors="coerce")
    if frame["trading_date"].isna().any():
        raise FeatureError("trading_date contains invalid values")

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")

    result = frame[KEY_COLUMNS].copy()
    result["return_1d"] = close.pct_change()
    result["return_5d"] = close.pct_change(5)
    result["return_20d"] = close.pct_change(20)
    result["log_return_1d"] = np.log(close.where(close > 0) / close.shift(1).where(close.shift(1) > 0))

    result["sma_20"] = close.rolling(20, min_periods=20).mean()
    result["sma_50"] = close.rolling(50, min_periods=50).mean()
    result["sma_200"] = close.rolling(200, min_periods=200).mean()
    result["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    result["ema_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = _safe_divide(avg_gain, avg_loss)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    result["rsi_14"] = rsi

    result["momentum_5"] = close - close.shift(5)
    result["momentum_20"] = close - close.shift(20)

    daily_return = close.pct_change()
    result["volatility_20"] = daily_return.rolling(20, min_periods=20).std(ddof=1)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr_14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    result["volume_sma_20"] = volume.rolling(20, min_periods=20).mean()
    result["relative_volume_20"] = _safe_divide(volume, result["volume_sma_20"])
    result["volume_change_1d"] = volume.pct_change()
    return result


def compute_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute features per ticker with no forward-looking windows."""
    _require_columns(prices, REQUIRED_INPUT)
    if prices.empty:
        return pd.DataFrame(columns=[*KEY_COLUMNS, *FEATURE_COLUMNS])
    frame = prices.copy()
    frame["ticker"] = frame["ticker"].astype("string").str.strip().str.upper()
    if frame["ticker"].isna().any() or (frame["ticker"] == "").any():
        raise FeatureError("ticker contains NULL or empty values")
    if frame.duplicated(subset=KEY_COLUMNS).any():
        raise FeatureError("Duplicate (ticker, trading_date) keys in input")

    results = [_compute_one(group) for _, group in frame.groupby("ticker", sort=True)]
    combined = pd.concat(results, ignore_index=True)
    combined = combined.sort_values(KEY_COLUMNS, kind="mergesort").reset_index(drop=True)
    if combined.duplicated(subset=KEY_COLUMNS).any():
        raise FeatureError("Duplicate (ticker, trading_date) keys after computation")
    return combined[[*KEY_COLUMNS, *FEATURE_COLUMNS]]


def build_quality_report(features: pd.DataFrame, input_path: Path) -> Dict[str, Any]:
    stats: Dict[str, Dict[str, Any]] = {}
    for column in FEATURE_COLUMNS:
        values = features[column]
        stats[column] = {
            "non_null": int(values.notna().sum()),
            "null": int(values.isna().sum()),
            "null_pct": round(float(values.isna().mean() * 100) if len(values) else 0.0, 4),
            "min": None if values.dropna().empty else float(values.min()),
            "max": None if values.dropna().empty else float(values.max()),
        }
    return {
        "feature_version": FEATURE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "rows": int(len(features)),
        "tickers": int(features["ticker"].nunique()) if len(features) else 0,
        "date_min": str(features["trading_date"].min().date()) if len(features) else None,
        "date_max": str(features["trading_date"].max().date()) if len(features) else None,
        "duplicate_keys": int(features.duplicated(subset=KEY_COLUMNS).sum()),
        "feature_stats": stats,
        "assumptions": {
            "price_basis": "Cleaned close from prices_clean.parquet; adjusted assumption follows upstream pipeline.",
            "trading_sessions": "All rolling windows are row-count based within each ticker, not calendar days.",
            "rsi": "Wilder EWM alpha=1/14, min_periods=14; flat series maps to 50.",
            "atr": "Wilder EWM alpha=1/14 on true range, min_periods=14.",
            "ema": "pandas ewm(span=n, adjust=False, min_periods=n).",
            "warmup": "Warm-up values remain NULL; no fill or backfill.",
            "lookahead": "No negative shifts; features at t use data up to t only.",
        },
    }


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        frame.to_parquet(tmp_name, index=False)
        os.replace(tmp_name, path)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_feature_dataset(
    input_path: Path | str,
    output_path: Path | str = DEFAULT_OUTPUT,
    manifest_path: Path | str = DEFAULT_MANIFEST,
    report_path: Path | str = DEFAULT_REPORT,
) -> FeatureBuild:
    input_path = Path(input_path)
    output_path = Path(output_path)
    manifest_path = Path(manifest_path)
    report_path = Path(report_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Prices Parquet not found: {input_path}")

    prices = pd.read_parquet(input_path)
    features = compute_features(prices)
    quality = build_quality_report(features, input_path)
    _write_parquet_atomic(features, output_path)
    manifest = {
        "schema_version": 1,
        "feature_version": FEATURE_VERSION,
        "generated_at": quality["generated_at"],
        "input_path": str(input_path),
        "output_path": str(output_path),
        "rows": quality["rows"],
        "tickers": quality["tickers"],
        "date_min": quality["date_min"],
        "date_max": quality["date_max"],
        "columns": [*KEY_COLUMNS, *FEATURE_COLUMNS],
        "lookahead_safe": True,
    }
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(report_path, quality)
    logger.info(
        "Quant features saved: %d rows, %d tickers, version %s",
        len(features),
        features["ticker"].nunique() if len(features) else 0,
        FEATURE_VERSION,
    )
    return FeatureBuild(features=features, quality=quality, manifest=manifest)


def _default_input() -> Path:
    return resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data") / "prices_clean.parquet"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic quant features (TASK 109)."
    )
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    setup_logging()
    input_path = args.input or _default_input()
    try:
        build = build_feature_dataset(
            input_path=input_path,
            output_path=args.output,
            manifest_path=args.manifest,
            report_path=args.report,
        )
    except Exception as exc:
        logger.error("Quant feature build failed: %s", exc)
        return 1
    print(f"rows={len(build.features)}")
    print(f"tickers={build.features['ticker'].nunique()}")
    print(f"output={args.output}")
    print(f"quality_report={args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
