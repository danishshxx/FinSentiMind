"""Staging/publication orchestration tests over the real dataset_store."""
from pathlib import Path

import pandas as pd
import pytest

from ml.ops.publish_dataset import publish_and_verify, stage_from_source
from ml.preprocessing.dataset_store import (
    DatasetStoreError,
    resolve_active_dataset,
    verify_generation,
)
from ml.preprocessing.trading_calendar import derive_trading_calendar


def _source_generation(root: Path) -> Path:
    root.mkdir(parents=True)
    prices = pd.DataFrame([
        {"ticker": "BBCA", "trading_date": pd.Timestamp("2026-05-11"),
         "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
         "volume": 1000, "return_1d": None},
        {"ticker": "BBCA", "trading_date": pd.Timestamp("2026-05-12"),
         "open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0,
         "volume": 1000, "return_1d": 0.01},
    ])
    sentiment = pd.DataFrame([
        {"ticker": "BBCA", "trading_date": pd.Timestamp("2026-05-12"),
         "news_count": 1, "sentiment_mean": 0.5, "sentiment_std": None,
         "positive_ratio": 1.0, "negative_ratio": 0.0,
         "availability_live_ratio": 1.0},
    ])
    base = prices.merge(sentiment, on=["ticker", "trading_date"], how="left")
    base["news_count"] = base["news_count"].fillna(0)
    calendar = derive_trading_calendar(prices)

    prices.to_parquet(root / "prices_clean.parquet", index=False)
    sentiment.to_parquet(root / "daily_sentiment.parquet", index=False)
    base.to_parquet(root / "base_dataset.parquet", index=False)
    calendar.to_parquet(root / "trading_calendar.parquet", index=False)
    return root


def test_stage_publish_and_activate(tmp_path: Path):
    data_root = tmp_path / "data"
    source = _source_generation(tmp_path / "source")

    staged = stage_from_source(
        source, data_root, "run-a",
        expected_calendar_version="IDX-2024-2026-v2",
        universe_scope="IDX30",
    )
    staging = Path(staged["staging"])
    assert staging.is_dir()
    assert (staging / "provenance.json").is_file()

    result = publish_and_verify(
        staging, data_root, "run-a",
        metadata={"data_as_of": "2026-05-12", "refreshed_at": "2026-05-12T00:00:00+00:00"},
    )
    assert result["active"]["run_id"] == "run-a"
    assert verify_generation(data_root, "run-a")["valid"] is True

    active = resolve_active_dataset(data_root, verify_hashes=True)
    assert active.source == "manifest"
    assert (active.active_dir / "base_dataset.parquet").is_file()
    assert active.manifest["artifacts"]["base_dataset.parquet"]["sha256"]
    assert not staging.exists()


def test_staging_refuses_to_overwrite(tmp_path: Path):
    data_root = tmp_path / "data"
    source = _source_generation(tmp_path / "source")
    stage_from_source(source, data_root, "run-a")
    with pytest.raises(DatasetStoreError, match="already exists"):
        stage_from_source(source, data_root, "run-a")


def test_failed_publication_preserves_previous_active(tmp_path: Path):
    data_root = tmp_path / "data"
    source = _source_generation(tmp_path / "source")

    first = stage_from_source(source, data_root, "run-a")
    publish_and_verify(Path(first["staging"]), data_root, "run-a")
    pointer_before = (data_root / "active.json").read_bytes()

    second = stage_from_source(source, data_root, "run-b")
    with pytest.raises(DatasetStoreError, match="already exists"):
        publish_and_verify(Path(second["staging"]), data_root, "run-a")

    assert (data_root / "active.json").read_bytes() == pointer_before
    assert resolve_active_dataset(data_root).run_id == "run-a"
