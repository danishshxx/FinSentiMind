"""Universe refresh orchestration tests (no network)."""
from datetime import date

import pandas as pd

from ml.universe.refresh import UniverseRefreshConfig, refresh_universe


def _fake_rows():
    days = [date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)]
    return pd.DataFrame([
        {"trading_date": pd.Timestamp(day), "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "adj_close": 100.4, "volume": 1000,
         "dividends": 0.0, "stock_splits": 0.0}
        for day in days
    ])


def _config(tmp_path):
    return UniverseRefreshConfig(
        data_dir=tmp_path / "data",
        reports_dir=tmp_path / "reports",
        inventory_path=tmp_path / "inventory.json",
        end=date(2026, 6, 5),
        history_start=date(2026, 5, 1),
        freshness_days=3,
        rate_limit_seconds=0,
    )


def test_refresh_runs_all_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ml.universe.refresh.resolve_active_dataset_dir", lambda root: tmp_path
    )

    def fetcher(symbol, start, end):
        return _fake_rows()

    report = refresh_universe(
        _config(tmp_path), fetcher=fetcher, sleep_fn=lambda _: None
    )
    assert report["status"] == "SUCCESS"
    assert [stage["name"] for stage in report["stages"]] == [
        "universe_sync", "price_ingestion", "coverage_matrix"
    ]
    assert report["inventory"]["counts"]["total"] >= 45
    assert report["coverage"]["usable"] >= 45
    assert report["active_generation_touched"] is False
    assert report["supabase_writes"] is False
    assert (tmp_path / "reports" / "universe_coverage.json").is_file()


def test_refresh_marks_provider_failures_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ml.universe.refresh.resolve_active_dataset_dir", lambda root: tmp_path
    )

    def failing_fetcher(symbol, start, end):
        raise RuntimeError("provider unavailable")

    report = refresh_universe(
        _config(tmp_path), fetcher=failing_fetcher, sleep_fn=lambda _: None
    )
    assert report["status"] == "PARTIAL"
    ingestion = next(
        stage for stage in report["stages"] if stage["name"] == "price_ingestion"
    )
    assert ingestion["status"] == "partial"
    assert ingestion["details"]["failed"]
    # No fabricated prices are written.
    assert report["coverage"]["usable"] == 0
