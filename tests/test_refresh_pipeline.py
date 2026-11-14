"""Failure-safe refresh workflow tests (TASK 107).

No real provider, Supabase, or network call is made. Stage handlers are
replaced with deterministic fakes that write real temporary Parquet files.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import ml.preprocessing.refresh_pipeline as refresh_pipeline
from ml.preprocessing.build_base_dataset import build_base_dataset
from ml.preprocessing.dataset_store import read_active_manifest, resolve_active_dataset
from ml.preprocessing.refresh_pipeline import (
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_PUBLISH_FAILED,
    STAGE_SPECS,
    RefreshConfig,
    run_refresh,
)


def _bundle(ctx) -> None:
    """Write a deterministic, schema-valid generation into staging."""
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    prices = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "trading_date": pd.Timestamp("2025-01-06"),
                "open": 100.0,
                "high": 110.0,
                "low": 99.0,
                "close": 105.0,
                "volume": 1000,
                "return_1d": None,
            },
            {
                "ticker": "AAA",
                "trading_date": pd.Timestamp("2025-01-07"),
                "open": 105.0,
                "high": 112.0,
                "low": 104.0,
                "close": 110.0,
                "volume": 1200,
                "return_1d": 110.0 / 105.0 - 1.0,
            },
        ]
    )
    sentiment = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "trading_date": pd.Timestamp("2025-01-07"),
                "news_count": 2,
                "sentiment_mean": 0.5,
                "sentiment_std": 0.1,
                "positive_ratio": 0.5,
                "negative_ratio": 0.0,
                "availability_live_ratio": 1.0,
            }
        ]
    )
    base = build_base_dataset(prices, sentiment)
    calendar = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2025-01-06").date(),
                "ticker_count": 1,
                "available_count": 1,
                "coverage_ratio": 1.0,
                "is_trading_day": True,
            },
            {
                "date": pd.Timestamp("2025-01-07").date(),
                "ticker_count": 1,
                "available_count": 1,
                "coverage_ratio": 1.0,
                "is_trading_day": True,
            },
        ]
    )
    prices.to_parquet(ctx.staging_dir / "prices_clean.parquet", index=False)
    sentiment.to_parquet(ctx.staging_dir / "daily_sentiment.parquet", index=False)
    base.to_parquet(ctx.staging_dir / "base_dataset.parquet", index=False)
    calendar.to_parquet(ctx.staging_dir / "trading_calendar.parquet", index=False)
    for name in (
        "prices_clean",
        "daily_sentiment",
        "base_dataset",
        "trading_calendar",
    ):
        ctx.artifacts[name] = ctx.staging_dir / f"{name}.parquet"


def _validation_ok(ctx) -> None:
    ctx.validation_summary = {
        "status": "PASS",
        "counts": {"pass": 2, "warn": 0, "info": 0, "fail": 0},
        "has_failures": False,
    }


def _handlers(**overrides):
    noop = lambda _ctx: None
    handlers = {spec.name: noop for spec in STAGE_SPECS}
    handlers["price_cleaning"] = _bundle
    handlers["validation"] = _validation_ok
    handlers.update(overrides)
    return handlers


def _config(root: Path, run_id: str = "run-1", **kwargs) -> RefreshConfig:
    return RefreshConfig(
        data_root=root,
        reports_dir=root / "reports",
        run_id=run_id,
        retries=0,
        retry_backoff_seconds=0,
        **kwargs,
    )


def _seed_active(root: Path) -> None:
    outcome = run_refresh(_config(root, "seed"), _handlers())
    assert outcome.exit_code == EXIT_OK, outcome.report


def test_successful_refresh_publishes_generation(tmp_path: Path):
    outcome = run_refresh(_config(tmp_path, "run-1"), _handlers())

    assert outcome.exit_code == EXIT_OK
    assert outcome.status == "SUCCESS"
    assert outcome.report_path and outcome.report_path.exists()
    active = read_active_manifest(tmp_path)
    assert active is not None
    assert active["run_id"] == "run-1"
    assert resolve_active_dataset(tmp_path).run_id == "run-1"
    generation = tmp_path / "generations" / "run-1"
    assert (generation / "base_dataset.parquet").exists()
    assert not (tmp_path / "staging" / "run-1").exists()
    stage_names = [row["name"] for row in outcome.report["stage_statuses"]]
    assert stage_names == [spec.name for spec in STAGE_SPECS] + ["publication"]
    assert all(row["status"] == "ok" for row in outcome.report["stage_statuses"])


def test_refresh_metadata_separates_refreshed_at_and_data_as_of(tmp_path: Path):
    outcome = run_refresh(_config(tmp_path, "metadata-run"), _handlers())
    report = outcome.report

    assert report["run_id"] == "metadata-run"
    assert report["status"] == "SUCCESS"
    assert report["started_at"]
    assert report["completed_at"]
    assert report["data_as_of"] == "2025-01-07"
    assert report["latest_market_data_date"] == "2025-01-07"
    assert report["refreshed_at"] is not None
    assert report["refreshed_at"] != report["data_as_of"]
    assert report["refreshed_at"].startswith(str(date.today().year))
    assert report["price_rows"] == 2
    assert report["sentiment_rows"] == 1
    assert report["base_dataset_rows"] == 2
    assert report["ticker_count"] == 1
    assert report["validation"]["status"] == "PASS"
    assert report["published"] is True
    assert report["generation"]["run_id"] == "metadata-run"


def test_dry_run_does_not_publish(tmp_path: Path):
    outcome = run_refresh(_config(tmp_path, "dry-run", dry_run=True), _handlers())

    assert outcome.status == "DRY_RUN"
    assert outcome.exit_code == EXIT_OK
    assert outcome.report["published"] is False
    assert outcome.report["refreshed_at"] is None
    assert not (tmp_path / "active.json").exists()
    assert (tmp_path / "staging" / "dry-run" / "base_dataset.parquet").exists()
    skipped = {
        row["name"]
        for row in outcome.report["stage_statuses"]
        if row["status"] == "skipped"
    }
    assert {"price_ingestion", "news_ingestion", "sentiment_processing"} <= skipped


@pytest.mark.parametrize(
    "stage,message",
    [
        ("price_ingestion", "price provider down"),
        ("news_ingestion", "news provider down"),
        ("sentiment_processing", "sentiment provider down"),
        ("base_dataset", "base build failed"),
        ("validation", "validation failed"),
    ],
)
def test_stage_failure_preserves_active_dataset(
    tmp_path: Path, stage: str, message: str
):
    _seed_active(tmp_path)
    pointer_before = (tmp_path / "active.json").read_bytes()
    generation_before = (
        tmp_path / "generations" / "seed" / "base_dataset.parquet"
    ).read_bytes()

    def boom(_ctx):
        raise RuntimeError(message)

    outcome = run_refresh(
        _config(tmp_path, f"failed-{stage}"),
        _handlers(**{stage: boom}),
    )

    assert outcome.exit_code == EXIT_FAILED
    assert outcome.status == "FAILED"
    assert message in outcome.report["failure_reason"]
    assert outcome.report["refreshed_at"] is None
    assert outcome.report["published"] is False
    assert (tmp_path / "active.json").read_bytes() == pointer_before
    assert (
        tmp_path / "generations" / "seed" / "base_dataset.parquet"
    ).read_bytes() == generation_before
    assert not (tmp_path / "generations" / f"failed-{stage}").exists()


def test_publication_failure_preserves_active_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _seed_active(tmp_path)
    pointer_before = (tmp_path / "active.json").read_bytes()

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk full during activation")

    monkeypatch.setattr(refresh_pipeline, "publish_generation", boom)
    outcome = run_refresh(_config(tmp_path, "publication-fail"), _handlers())

    assert outcome.exit_code == EXIT_PUBLISH_FAILED
    assert outcome.status == "FAILED"
    assert "disk full" in outcome.report["failure_reason"]
    assert (tmp_path / "active.json").read_bytes() == pointer_before
    assert (tmp_path / "staging" / "publication-fail").exists()


def test_repeated_refresh_is_unique_and_updates_active(tmp_path: Path):
    first = run_refresh(_config(tmp_path, "run-1"), _handlers())
    second = run_refresh(_config(tmp_path, "run-2"), _handlers())

    assert first.exit_code == EXIT_OK
    assert second.exit_code == EXIT_OK
    active = read_active_manifest(tmp_path)
    assert active["run_id"] == "run-2"
    base = pd.read_parquet(
        tmp_path / "generations" / "run-2" / "base_dataset.parquet"
    )
    assert len(base) == 2
    assert not base.duplicated(subset=["ticker", "trading_date"]).any()
    assert (tmp_path / "generations" / "run-1" / "base_dataset.parquet").exists()


def test_concurrent_refresh_is_rejected(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    def slow_ingestion(_ctx):
        started.set()
        assert release.wait(timeout=10)

    first_result = {}

    def first_run():
        first_result["outcome"] = run_refresh(
            _config(tmp_path, "concurrent-1"),
            _handlers(price_ingestion=slow_ingestion),
        )

    thread = threading.Thread(target=first_run)
    thread.start()
    assert started.wait(timeout=10)

    second = run_refresh(_config(tmp_path, "concurrent-2"), _handlers())
    assert second.exit_code == EXIT_LOCKED
    assert second.status == "LOCKED"

    release.set()
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert first_result["outcome"].exit_code == EXIT_OK


def test_config_error_for_existing_staging(tmp_path: Path):
    (tmp_path / "staging" / "duplicate").mkdir(parents=True)
    outcome = run_refresh(_config(tmp_path, "duplicate"), _handlers())
    assert outcome.exit_code == EXIT_CONFIG
    assert outcome.status == "CONFIG_ERROR"


def test_missing_credentials_are_reported(tmp_path: Path):
    def missing_credentials(_ctx):
        raise RuntimeError(
            "Missing required environment variable: SUPABASE_URL"
        )

    outcome = run_refresh(
        _config(tmp_path, "missing-credentials"),
        _handlers(price_ingestion=missing_credentials),
    )
    assert outcome.exit_code == EXIT_FAILED
    assert "SUPABASE_URL" in outcome.report["failure_reason"]
    assert outcome.report["published"] is False


def test_validate_only_reports_active_dataset(tmp_path: Path):
    _seed_active(tmp_path)
    outcome = run_refresh(_config(tmp_path, "validate-only", validate_only=True))

    assert outcome.status == "VALIDATION_ONLY"
    assert outcome.report["published"] is False
    assert outcome.report["refreshed_at"] is None
    assert outcome.report["validation"]["status"] in {"PASS", "FAIL"}
    assert outcome.report_path and outcome.report_path.exists()
    assert read_active_manifest(tmp_path)["run_id"] == "seed"


def test_partial_price_ingestion_errors_block_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Summary:
        total_tickers = 2
        ok_count = 1
        empty_count = 0
        error_count = 1
        total_fetched = 10
        total_new = 2
        total_overlap = 8

    monkeypatch.setattr(refresh_pipeline, "run_backfill", lambda: Summary())
    handlers = _handlers(price_ingestion=refresh_pipeline.stage_price_ingestion)
    outcome = run_refresh(_config(tmp_path, "partial-ingest"), handlers)

    assert outcome.exit_code == EXIT_FAILED
    assert "Price ingestion failed" in outcome.report["failure_reason"]
    assert not (tmp_path / "active.json").exists()


def test_empty_price_ingestion_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class Summary:
        total_tickers = 2
        ok_count = 1
        empty_count = 1
        error_count = 0
        total_fetched = 10
        total_new = 2
        total_overlap = 8

    monkeypatch.setattr(refresh_pipeline, "run_backfill", lambda: Summary())
    handlers = _handlers(price_ingestion=refresh_pipeline.stage_price_ingestion)
    outcome = run_refresh(_config(tmp_path, "empty-ingest"), handlers)

    assert outcome.exit_code == EXIT_FAILED
    assert "returned no data" in outcome.report["failure_reason"]
    assert not (tmp_path / "active.json").exists()


def test_successful_refresh_publishes_quant_features_when_stage_creates_them(tmp_path: Path):
    def write_features(ctx):
        (ctx.staging_dir / "quant_features.parquet").write_bytes(b"feature-artifact")
        ctx.artifacts["quant_features"] = ctx.staging_dir / "quant_features.parquet"

    outcome = run_refresh(
        _config(tmp_path, "feature-run"),
        _handlers(quant_features=write_features),
    )
    assert outcome.exit_code == EXIT_OK
    assert (tmp_path / "generations" / "feature-run" / "quant_features.parquet").exists()
