"""Reproducible, leakage-safe ML research pipeline (TASK 111).

Research target:
    signal date t -> enter at open(t+1) -> exit at close(t+5)
    target = close(t+5) / open(t+1) - 1
    direction = target > 0

This module is research-only. It never claims profitability and never exposes a
prediction as a trusted investment signal.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler

from app.core.logging import get_logger, setup_logging
from ml.features.quant import FEATURE_VERSION, compute_features
from ml.preprocessing.dataset_store import resolve_active_dataset_dir


logger = get_logger(__name__)

RESEARCH_VERSION = "ml_research_v1"
TARGET_HORIZON = 5
DEFAULT_COST_BPS = 10.0
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = _PROJECT_ROOT / "ml" / "data" / "research" / RESEARCH_VERSION / "research_dataset.parquet"
DEFAULT_REPORT = _PROJECT_ROOT / "ml" / "reports" / f"{RESEARCH_VERSION}.json"

MODEL_FEATURES = [
    "return_1d",
    "return_5d",
    "return_20d",
    "log_return_1d",
    "rsi_14",
    "volatility_20",
    "atr_14",
    "relative_volume_20",
    "volume_change_1d",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "has_news",
    "market_return_1d",
    "market_breadth",
    "market_volatility_20",
]

SENTIMENT_COLUMNS = [
    "news_count",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]


@dataclass
class ResearchConfig:
    start_fraction: float = 0.60
    test_sessions: int = 40
    embargo_sessions: int = 5
    min_train_rows: int = 500
    min_test_rows: int = 100
    random_state: int = 42
    cost_bps: float = DEFAULT_COST_BPS


@dataclass
class ResearchResult:
    dataset: pd.DataFrame
    report: Dict[str, Any]
    dataset_path: Optional[Path] = None
    report_path: Optional[Path] = None


def _market_features(base: pd.DataFrame) -> pd.DataFrame:
    daily = (
        base.groupby("trading_date")
        .agg(
            market_return_1d=("return_1d", "mean"),
            market_breadth=("return_1d", lambda values: float((values > 0).mean())),
            market_volume=("volume", "sum"),
        )
        .reset_index()
        .sort_values("trading_date")
    )
    daily["market_volatility_20"] = (
        daily["market_return_1d"].rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252)
    )
    return daily


def build_research_dataset(base: pd.DataFrame, instrument_master=None) -> pd.DataFrame:
    """Build features, sentiment, market context, and executable target."""
    required = {"ticker", "trading_date", "open", "high", "low", "close", "volume", "return_1d"}
    missing = required - set(base.columns)
    if missing:
        raise ValueError(f"Research dataset missing columns: {sorted(missing)}")

    frame = base.copy()
    frame["ticker"] = frame["ticker"].astype("string").str.upper()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"]).dt.normalize()
    features = compute_features(frame)
    sentiment_cols = [column for column in SENTIMENT_COLUMNS if column in frame.columns]
    merged = features.merge(
        frame[["ticker", "trading_date", "open", "close", *sentiment_cols]],
        on=["ticker", "trading_date"],
        how="left",
        validate="one_to_one",
    )

    market = _market_features(frame)
    merged = merged.merge(market, on="trading_date", how="left", validate="many_to_one")

    merged = merged.sort_values(["ticker", "trading_date"]).reset_index(drop=True)
    merged["entry_date"] = merged.groupby("ticker")["trading_date"].shift(-1)
    merged["exit_date"] = merged.groupby("ticker")["trading_date"].shift(-TARGET_HORIZON)
    merged["entry_open"] = merged.groupby("ticker")["open"].shift(-1)
    merged["exit_close"] = merged.groupby("ticker")["close"].shift(-TARGET_HORIZON)
    merged["target_return_5d"] = merged["exit_close"] / merged["entry_open"] - 1.0
    merged["target_direction"] = np.where(
        merged["target_return_5d"].isna(), np.nan,
        (merged["target_return_5d"] > 0).astype(float),
    )
    if "news_count" in merged.columns:
        news_count = pd.to_numeric(merged["news_count"], errors="coerce").fillna(0)
    else:
        news_count = pd.Series(0, index=merged.index)
    merged["has_news"] = (news_count > 0).astype(int)

    sectors: Dict[str, str] = {}
    if instrument_master is not None:
        sectors = {
            instrument.ticker: instrument.sector
            for instrument in instrument_master.instruments
            if instrument.sector
        }
    merged["sector"] = merged["ticker"].map(sectors)
    return merged


def walk_forward_folds(
    trading_dates: List[pd.Timestamp], config: ResearchConfig
) -> List[Dict[str, Any]]:
    dates = list(sorted(trading_dates))
    if len(dates) < 120:
        return []
    start = max(0, int(len(dates) * config.start_fraction))
    folds: List[Dict[str, Any]] = []
    fold_number = 1
    for test_start in range(start, len(dates) - 1, config.test_sessions):
        test_end = min(test_start + config.test_sessions, len(dates))
        if test_end - test_start < 10:
            break
        train_end = max(0, test_start - config.embargo_sessions)
        train_dates = dates[:train_end]
        test_dates = dates[test_start:test_end]
        if not train_dates:
            continue
        folds.append(
            {
                "fold": fold_number,
                "train_start": train_dates[0].date().isoformat(),
                "train_end": train_dates[-1].date().isoformat(),
                "test_start": test_dates[0].date().isoformat(),
                "test_end": test_dates[-1].date().isoformat(),
                "embargo_sessions": config.embargo_sessions,
                "train_dates": train_dates,
                "test_dates": test_dates,
            }
        )
        fold_number += 1
    return folds


def _classification_metrics(y_true: np.ndarray, probabilities: np.ndarray) -> Dict[str, Any]:
    predictions = (probabilities >= 0.5).astype(int)
    labels = sorted(set(int(value) for value in y_true))
    result: Dict[str, Any] = {
        "sample_size": int(len(y_true)),
        "positive_rate": float(np.mean(y_true)) if len(y_true) else None,
        "precision": float(precision_score(y_true, predictions, zero_division=0)),
        "recall": float(recall_score(y_true, predictions, zero_division=0)),
        "f1": float(f1_score(y_true, predictions, zero_division=0)),
        "brier": float(brier_score_loss(y_true, probabilities)) if len(y_true) else None,
        "confusion_matrix": confusion_matrix(y_true, predictions, labels=[0, 1]).tolist(),
    }
    if len(labels) == 2:
        result["roc_auc"] = float(roc_auc_score(y_true, probabilities))
        result["pr_auc"] = float(average_precision_score(y_true, probabilities))
    else:
        result["roc_auc"] = None
        result["pr_auc"] = None
    return result


def _max_drawdown(equity: pd.Series) -> Optional[float]:
    if equity.empty:
        return None
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def _strategy_metrics(
    predictions: pd.DataFrame,
    cost_bps: float,
    timeframe_days: int = 5,
) -> Dict[str, Any]:
    frame = predictions.dropna(subset=["target_return_5d"]).copy()
    if frame.empty:
        return {"periods": 0}
    returns = []
    universe_returns = []
    positions = []
    for date_value, group in frame.groupby("trading_date"):
        selected = group[group["probability"] >= 0.5]
        cost = (cost_bps / 10000.0) * 2.0
        period_return = (
            float(selected["target_return_5d"].mean() - cost) if len(selected) else 0.0
        )
        universe_return = float(group["target_return_5d"].mean() - cost)
        returns.append(period_return)
        universe_returns.append(universe_return)
        positions.append(len(selected))
    series = pd.Series(returns, dtype=float)
    equity = (1 + series).cumprod()
    universe_equity = (1 + pd.Series(universe_returns, dtype=float)).cumprod()
    annualization = math.sqrt(252 / timeframe_days)
    std = series.std(ddof=1)
    return {
        "periods": int(len(series)),
        "mean_period_return": float(series.mean()),
        "cumulative_return": float(equity.iloc[-1] - 1.0),
        "max_drawdown": _max_drawdown(equity),
        "sharpe_annualized": None if not std or pd.isna(std) else float(series.mean() / std * annualization),
        "win_rate": float((series > 0).mean()),
        "average_positions": float(np.mean(positions)),
        "turnover": float(np.mean(positions)),
        "universe_cumulative_return": float(universe_equity.iloc[-1] - 1.0),
        "universe_sharpe_annualized": (
            None
            if len(universe_returns) < 2 or pd.Series(universe_returns).std(ddof=1) == 0
            else float(
                pd.Series(universe_returns).mean()
                / pd.Series(universe_returns).std(ddof=1)
                * annualization
            )
        ),
        "cost_bps_per_side": cost_bps,
    }


def _non_overlapping_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fold, group in predictions.groupby("fold"):
        dates = sorted(group["trading_date"].unique())
        selected_dates = set(dates[::TARGET_HORIZON])
        rows.append(group[group["trading_date"].isin(selected_dates)])
    return pd.concat(rows, ignore_index=True) if rows else predictions.iloc[0:0]


def run_research(
    base: pd.DataFrame,
    instrument_master=None,
    config: Optional[ResearchConfig] = None,
    dataset_path: Optional[Path | str] = DEFAULT_DATASET,
    report_path: Optional[Path | str] = DEFAULT_REPORT,
) -> ResearchResult:
    config = config or ResearchConfig()
    dataset = build_research_dataset(base, instrument_master)
    labelled = dataset.dropna(subset=["target_direction", "target_return_5d"]).copy()
    labelled = labelled.sort_values(["trading_date", "ticker"]).reset_index(drop=True)
    dates = sorted(pd.to_datetime(dataset["trading_date"]).unique())
    folds = walk_forward_folds(dates, config)

    fold_reports: List[Dict[str, Any]] = []
    predictions: List[pd.DataFrame] = []
    dummy_predictions: List[pd.DataFrame] = []
    coefficients: List[pd.Series] = []

    for fold in folds:
        train = labelled[labelled["trading_date"].isin(fold["train_dates"])]
        test = labelled[labelled["trading_date"].isin(fold["test_dates"])]
        if len(train) < config.min_train_rows or len(test) < config.min_test_rows:
            fold_reports.append(
                {
                    "fold": fold["fold"],
                    "status": "skipped",
                    "train_rows": int(len(train)),
                    "test_rows": int(len(test)),
                    "reason": "insufficient rows",
                }
            )
            continue

        x_train = train[MODEL_FEATURES]
        x_test = test[MODEL_FEATURES]
        y_train = train["target_direction"].astype(int).to_numpy()
        y_test = test["target_direction"].astype(int).to_numpy()

        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        scaler = StandardScaler()
        x_train_imputed = imputer.fit_transform(x_train)
        x_test_imputed = imputer.transform(x_test)
        x_train_scaled = scaler.fit_transform(x_train_imputed)
        x_test_scaled = scaler.transform(x_test_imputed)

        model = LogisticRegression(
            max_iter=2000,
            random_state=config.random_state,
            class_weight="balanced",
            solver="lbfgs",
        )
        model.fit(x_train_scaled, y_train)
        probabilities = model.predict_proba(x_test_scaled)[:, 1]

        dummy = DummyClassifier(strategy="most_frequent", random_state=config.random_state)
        dummy.fit(x_train_imputed, y_train)
        dummy_prob = dummy.predict_proba(x_test_imputed)[:, 1]

        fold_predictions = test[
            ["trading_date", "ticker", "target_return_5d", "target_direction"]
        ].copy()
        fold_predictions["probability"] = probabilities
        fold_predictions["fold"] = fold["fold"]
        predictions.append(fold_predictions)
        dummy_frame = fold_predictions.copy()
        dummy_frame["probability"] = dummy_prob
        dummy_predictions.append(dummy_frame)

        coefficients.append(pd.Series(model.coef_[0], index=MODEL_FEATURES))
        fold_reports.append(
            {
                "fold": fold["fold"],
                "status": "evaluated",
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "embargo_sessions": fold["embargo_sessions"],
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "metrics": _classification_metrics(y_test, probabilities),
                "dummy_metrics": _classification_metrics(y_test, dummy_prob),
            }
        )

    if not predictions:
        report = {
            "schema_version": 1,
            "research_version": RESEARCH_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "NOT_VALIDATED",
            "reason": "No walk-forward fold had sufficient data.",
            "not_for_trading": True,
            "folds": fold_reports,
        }
    else:
        all_predictions = pd.concat(predictions, ignore_index=True)
        all_dummy = pd.concat(dummy_predictions, ignore_index=True)
        y_true = all_predictions["target_direction"].astype(int).to_numpy()
        model_metrics = _classification_metrics(y_true, all_predictions["probability"].to_numpy())
        dummy_metrics = _classification_metrics(y_true, all_dummy["probability"].to_numpy())
        strategy = _strategy_metrics(
            _non_overlapping_predictions(all_predictions), config.cost_bps
        )
        strategy_0 = _strategy_metrics(_non_overlapping_predictions(all_predictions), 0.0)
        strategy_25 = _strategy_metrics(_non_overlapping_predictions(all_predictions), 25.0)
        averaged_coefficients = (
            pd.concat(coefficients, axis=1).mean(axis=1).sort_values()
            if coefficients
            else pd.Series(dtype=float)
        )
        positive = averaged_coefficients.sort_values(ascending=False).head(5)
        negative = averaged_coefficients.head(5)
        improvement = (
            model_metrics["roc_auc"] is not None
            and dummy_metrics["roc_auc"] is not None
            and model_metrics["roc_auc"] > dummy_metrics["roc_auc"] + 0.02
        )
        strategy_improvement = (
            strategy.get("sharpe_annualized") is not None
            and strategy.get("universe_sharpe_annualized") is not None
            and strategy["sharpe_annualized"] > strategy["universe_sharpe_annualized"] + 0.10
        )
        positive_strategy = (
            strategy.get("cumulative_return") is not None
            and strategy["cumulative_return"] > 0
            and strategy.get("sharpe_annualized") is not None
            and strategy["sharpe_annualized"] > 0
        )
        validated = bool(improvement and strategy_improvement and positive_strategy)
        status = "RESEARCH_ONLY" if validated else "NOT_VALIDATED"
        report = {
            "schema_version": 1,
            "research_version": RESEARCH_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "not_for_trading": True,
            "target": {
                "definition": "close(t+5) / open(t+1) - 1",
                "direction": "1 if target_return_5d > 0 else 0",
                "signal_time": "after close of t",
                "entry": "open of t+1",
                "exit": "close of t+5",
                "horizon_sessions": TARGET_HORIZON,
            },
            "dataset": {
                "rows": int(len(dataset)),
                "labelled_rows": int(len(labelled)),
                "tickers": int(dataset["ticker"].nunique()),
                "date_min": str(pd.to_datetime(dataset["trading_date"]).min().date()),
                "date_max": str(pd.to_datetime(dataset["trading_date"]).max().date()),
                "feature_version": FEATURE_VERSION,
                "features": MODEL_FEATURES,
            },
            "validation": {
                "method": "expanding walk-forward with embargo",
                "embargo_sessions": config.embargo_sessions,
                "folds": fold_reports,
            },
            "aggregate": {
                "model": model_metrics,
                "dummy": dummy_metrics,
                "strategy": strategy,
                "strategy_zero_cost": strategy_0,
                "strategy_25bps": strategy_25,
            },
            "acceptance": {
                "validated": validated,
                "status": status,
                "reasons": [
                    "Model must beat dummy ROC-AUC by > 0.02, strategy Sharpe must exceed the equal-weight universe baseline by > 0.10, and the strategy must be positive with positive Sharpe before any research-only label is considered.",
                    (
                        "Out-of-sample improvement vs dummy: "
                        f"{model_metrics['roc_auc']} vs {dummy_metrics['roc_auc']}"
                    ),
                    (
                        "Strategy vs universe Sharpe: "
                        f"{strategy.get('sharpe_annualized')} vs "
                        f"{strategy.get('universe_sharpe_annualized')}"
                    ),
                ],
            },
            "explainability": {
                "method": "average standardized logistic coefficients across folds",
                "positive_contributors": {key: float(value) for key, value in positive.items()},
                "negative_contributors": {key: float(value) for key, value in negative.items()},
                "disclaimer": "Coefficients describe model associations, not causal effects.",
            },
            "limitations": [
                "Research-only; not a trusted investment signal.",
                "Sentiment is sparse; missing values are imputed only inside each training fold and flagged by has_news.",
                "Overlapping labels are handled with a 5-session embargo and non-overlapping strategy dates.",
                "No live execution or broker integration.",
            ],
        }

    output_path = Path(dataset_path) if dataset_path else None
    if output_path:
        _write_parquet_atomic(dataset, output_path)
    report_file = Path(report_path) if report_path else None
    if report_file:
        _write_json_atomic(report_file, report)
        # Latest alias for API readers.
        _write_json_atomic(report_file.parent / "ml_research_latest.json", report)
    return ResearchResult(dataset=dataset, report=report, dataset_path=output_path, report_path=report_file)


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


def load_latest_research_report(path: Path | str = DEFAULT_REPORT) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {
            "available": False,
            "status": "NOT_RUN",
            "not_for_trading": True,
            "reason": "No research report has been generated yet.",
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "available": False,
            "status": "INVALID_REPORT",
            "not_for_trading": True,
            "reason": str(exc),
        }
    return {"available": True, **report}


def _default_base_path() -> Path:
    return resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data") / "base_dataset.parquet"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Leakage-safe ML research pipeline (TASK 111)."
    )
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--start-fraction", type=float, default=0.60)
    parser.add_argument("--test-sessions", type=int, default=40)
    parser.add_argument("--embargo-sessions", type=int, default=5)
    parser.add_argument("--min-train-rows", type=int, default=500)
    parser.add_argument("--min-test-rows", type=int, default=100)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    args = parser.parse_args(argv)
    setup_logging()

    input_path = args.input or _default_base_path()
    if not Path(input_path).is_file():
        print(f"Input not found: {input_path}")
        return 1
    base = pd.read_parquet(input_path)
    try:
        result = run_research(
            base,
            config=ResearchConfig(
                start_fraction=args.start_fraction,
                test_sessions=args.test_sessions,
                embargo_sessions=args.embargo_sessions,
                min_train_rows=args.min_train_rows,
                min_test_rows=args.min_test_rows,
                cost_bps=args.cost_bps,
            ),
            dataset_path=args.output,
            report_path=args.report,
        )
    except Exception as exc:
        logger.error("ML research failed: %s", exc)
        return 1
    print(f"status={result.report.get('status')}")
    print(f"dataset={result.dataset_path}")
    print(f"report={result.report_path}")
    aggregate = result.report.get("aggregate", {})
    if aggregate:
        print(f"model_auc={aggregate.get('model', {}).get('roc_auc')}")
        print(f"strategy_sharpe={aggregate.get('strategy', {}).get('sharpe_annualized')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
