"""Tests for the leakage-safe ML research pipeline (TASK 111)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.research.ml_pipeline import (
    MODEL_FEATURES,
    RESEARCH_VERSION,
    ResearchConfig,
    build_research_dataset,
    load_latest_research_report,
    run_research,
    walk_forward_folds,
)


def _base(days: int = 300) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=days)
    rows = []
    for ticker_index, ticker in enumerate(["AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF"]):
        returns = 0.0004 + 0.012 * np.sin(
            np.arange(days) * 0.17 + ticker_index * 0.7
        )
        close = 100.0 * np.cumprod(1 + returns)
        for session, trading_date in enumerate(dates):
            previous_close = close[session - 1] if session else close[session] / (1 + returns[session])
            open_price = previous_close * (1 + 0.001 * np.cos(session + ticker_index))
            rows.append(
                {
                    "ticker": ticker,
                    "trading_date": trading_date,
                    "open": open_price,
                    "high": max(open_price, close[session]) * 1.01,
                    "low": min(open_price, close[session]) * 0.99,
                    "close": close[session],
                    "volume": 1000.0 + session * 7 + ticker_index * 13,
                    "return_1d": returns[session],
                    "news_count": 1 if session % 23 == 0 and ticker_index < 2 else 0,
                    "sentiment_mean": 0.3 if session % 23 == 0 and ticker_index < 2 else np.nan,
                    "sentiment_std": 0.1 if session % 23 == 0 and ticker_index < 2 else np.nan,
                    "positive_ratio": 0.5 if session % 23 == 0 and ticker_index < 2 else np.nan,
                    "negative_ratio": 0.0 if session % 23 == 0 and ticker_index < 2 else np.nan,
                    "availability_live_ratio": 1.0 if session % 23 == 0 and ticker_index < 2 else np.nan,
                }
            )
    return pd.DataFrame(rows)


def test_target_uses_next_open_and_five_session_close():
    dataset = build_research_dataset(_base())
    ticker = "AAAA"
    history = dataset[dataset["ticker"] == ticker].sort_values("trading_date").reset_index(drop=True)
    raw = _base()
    raw_history = raw[raw["ticker"] == ticker].sort_values("trading_date").reset_index(drop=True)

    assert history.loc[0, "entry_date"] == raw_history.loc[1, "trading_date"]
    assert history.loc[0, "exit_date"] == raw_history.loc[5, "trading_date"]
    expected = raw_history.loc[5, "close"] / raw_history.loc[1, "open"] - 1
    assert history.loc[0, "target_return_5d"] == pytest.approx(expected)
    assert history.tail(5)["target_return_5d"].isna().all()
    assert not set(MODEL_FEATURES) & {
        "entry_open",
        "exit_close",
        "entry_date",
        "exit_date",
        "target_return_5d",
        "target_direction",
    }


def test_missing_sentiment_is_not_zero_filled():
    dataset = build_research_dataset(_base())
    no_news = dataset[dataset["news_count"] == 0]
    assert no_news["sentiment_mean"].isna().all()
    assert (no_news["has_news"] == 0).all()
    assert dataset.duplicated(subset=["ticker", "trading_date"]).sum() == 0


def test_walk_forward_has_embargo_and_chronological_splits():
    dates = sorted(build_research_dataset(_base())["trading_date"].unique())
    config = ResearchConfig(start_fraction=0.5, test_sessions=40, embargo_sessions=5)
    folds = walk_forward_folds(dates, config)

    assert folds
    for fold in folds:
        assert pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["test_start"])
        train_index = dates.index(pd.Timestamp(fold["train_end"]))
        test_index = dates.index(pd.Timestamp(fold["test_start"]))
        assert test_index - train_index > config.embargo_sessions


def test_research_runs_deterministically_and_persists_report(tmp_path: Path):
    base = _base()
    dataset_path = tmp_path / "research.parquet"
    report_path = tmp_path / "research.json"
    config = ResearchConfig(
        start_fraction=0.5,
        test_sessions=40,
        embargo_sessions=5,
        min_train_rows=100,
        min_test_rows=20,
    )

    first = run_research(base, config=config, dataset_path=dataset_path, report_path=report_path)
    second = run_research(base, config=config, dataset_path=dataset_path, report_path=report_path)

    assert dataset_path.exists()
    assert report_path.exists()
    assert first.report["research_version"] == RESEARCH_VERSION
    assert first.report["not_for_trading"] is True
    assert first.report["status"] in {"RESEARCH_ONLY", "NOT_VALIDATED"}
    assert first.report["target"]["entry"] == "open of t+1"
    assert first.report["status"] == second.report["status"]
    if "aggregate" in first.report:
        assert first.report["aggregate"]["model"] == second.report["aggregate"]["model"]
        assert first.report["acceptance"] == second.report["acceptance"]
        assert first.report["validation"]["method"].startswith("expanding")
        assert first.report["aggregate"]["strategy"]["periods"] > 0
        assert "negative_contributors" in first.report["explainability"]


def test_research_report_loader_handles_missing_and_invalid(tmp_path: Path):
    missing = load_latest_research_report(tmp_path / "missing.json")
    assert missing["available"] is False
    assert missing["not_for_trading"] is True

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    loaded = load_latest_research_report(broken)
    assert loaded["available"] is False
    assert loaded["status"] == "INVALID_REPORT"


def test_research_report_written_as_json_and_latest_alias(tmp_path: Path):
    base = _base()
    report_path = tmp_path / "research.json"
    run_research(
        base,
        config=ResearchConfig(
            start_fraction=0.5,
            test_sessions=40,
            min_train_rows=100,
            min_test_rows=20,
        ),
        dataset_path=None,
        report_path=report_path,
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert (tmp_path / "ml_research_latest.json").exists()
