"""Local calendar reconciliation dry-run.

Reuses already available price data (no provider refresh) and rebuilds a new
staging snapshot with official closure bars removed. Publication is never
attempted.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.idx_calendar import load_calendar_source
from ml.preprocessing.load_prices import (
    compute_return_1d,
    filter_exchange_closures,
    save_parquet,
    sort_prices,
    write_closure_audit,
)
from ml.preprocessing.refresh_pipeline import (
    RefreshConfig,
    RefreshContext,
    _report_rel,
    run_refresh,
)


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_PRICES = (
    _PROJECT_ROOT / "ml" / "data" / "staging"
    / "task107b_acceptance_20261114" / "prices_clean.parquet"
)


def _local_price_cleaning(ctx: RefreshContext) -> None:
    source_path = Path(ctx.metrics["reconcile_source_prices"])
    raw = pd.read_parquet(source_path)
    calendar = load_calendar_source()
    filtered, audit = filter_exchange_closures(raw, calendar_source=calendar)
    filtered = sort_prices(filtered)
    filtered = compute_return_1d(filtered)
    output = ctx.staging_dir / "prices_clean.parquet"
    save_parquet(filtered, output)

    report_path = (
        ctx.config.reports_dir / f"exchange_closure_quarantine_{ctx.run_id}.json"
    )
    if not audit.empty:
        write_closure_audit(audit, report_path, calendar.calendar_version)
        ctx.report_paths["exchange_closure_quarantine"] = (
            _report_rel(report_path) or str(report_path)
        )
    ctx.artifacts["prices_clean"] = output
    ctx.metrics["price_rows"] = int(len(filtered))
    ctx.metrics["exchange_closure_quarantine"] = {
        "rows_excluded": int(len(audit)),
        "calendar_version": calendar.calendar_version,
    }


def run_reconciliation(
    source_prices: Path = DEFAULT_SOURCE_PRICES,
    run_id: Optional[str] = None,
    reports_dir: Optional[Path] = None,
) -> dict:
    if not source_prices.exists():
        raise FileNotFoundError(f"Source prices not found: {source_prices}")
    run_id = run_id or (
        "calendar_reconciliation_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    config = RefreshConfig(
        run_id=run_id,
        dry_run=True,
        reports_dir=reports_dir or (_PROJECT_ROOT / "ml" / "reports"),
        freshness_threshold_days=60,
    )
    ctx_holder = {}

    def handler(ctx: RefreshContext) -> None:
        ctx.metrics["reconcile_source_prices"] = str(source_prices)
        ctx_holder["ctx"] = ctx
        _local_price_cleaning(ctx)

    outcome = run_refresh(config, handlers={"price_cleaning": handler})
    return {
        "status": outcome.status,
        "run_id": run_id,
        "report": str(outcome.report_path) if outcome.report_path else None,
        "data_as_of": outcome.report.get("data_as_of"),
        "price_rows": outcome.report.get("price_rows"),
        "sentiment_rows": outcome.report.get("sentiment_rows"),
        "base_dataset_rows": outcome.report.get("base_dataset_rows"),
        "stage_statuses": [
            (item.get("name"), item.get("status"))
            for item in outcome.report.get("stage_statuses", [])
        ],
        "validation": outcome.report.get("validation"),
        "metrics": outcome.report.get("metrics"),
        "published": outcome.report.get("published"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Local calendar reconciliation dry-run.")
    parser.add_argument("--source-prices", type=Path, default=DEFAULT_SOURCE_PRICES)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    setup_logging()
    result = run_reconciliation(args.source_prices, args.run_id, args.reports_dir)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"DRY_RUN", "SUCCESS"} else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
