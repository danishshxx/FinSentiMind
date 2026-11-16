"""Orchestrated local universe refresh: inventory -> EOD ingestion -> coverage.

This is an additive local workflow. It never writes to Supabase, never mutates
the active dataset generation, and writes its own report. A failed ingestion
stage leaves the previous normalized price file intact because writes are
atomic.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import (
    resolve_active_dataset_dir,
    write_json_atomic,
)
from ml.universe.coverage import build_coverage_matrix, render_markdown as render_coverage
from ml.universe.inventory import (
    DEFAULT_OUTPUT as INVENTORY_PATH,
    build_inventory,
    render_markdown as render_inventory,
    save_inventory,
)
from ml.universe.yfinance_eod import (
    DEFAULT_DATA_DIR,
    IngestConfig,
    ingest_universe,
)


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORTS = _PROJECT_ROOT / "ml" / "reports"


@dataclass
class UniverseRefreshConfig:
    data_dir: Path = DEFAULT_DATA_DIR
    reports_dir: Path = DEFAULT_REPORTS
    end: Optional[date] = None
    history_start: date = date(2024, 10, 7)
    freshness_days: int = 3
    batch_size: int = 20
    rate_limit_seconds: float = 1.0
    retries: int = 3
    inventory_path: Path = INVENTORY_PATH


def _stage(name: str, status: str, **details: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, "details": details}


def refresh_universe(
    config: Optional[UniverseRefreshConfig] = None,
    *,
    fetcher: Optional[Callable[[str, date, date], pd.DataFrame]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    config = config or UniverseRefreshConfig()
    end = config.end or date.today()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    stages: List[Dict[str, Any]] = []

    # 1. Universe inventory
    try:
        inventory = build_inventory(include_lq45=True)
        save_inventory(inventory, config.inventory_path)
        stages.append(_stage(
            "universe_sync", "ok",
            scope=inventory["scope"], instruments=inventory["counts"]["total"],
            complete=inventory["complete"],
        ))
    except Exception as exc:
        logger.exception("Universe sync failed")
        inventory = None
        stages.append(_stage("universe_sync", "failed", error=str(exc)))

    # 2. EOD price ingestion (incremental, atomic writes)
    tickers = (
        [item["ticker"] for item in inventory["instruments"]] if inventory else []
    )
    ingestion_report = None
    if tickers:
        try:
            ingestion_report = ingest_universe(
                tickers,
                config.data_dir,
                IngestConfig(
                    history_start=config.history_start,
                    end=end,
                    batch_size=config.batch_size,
                    rate_limit_seconds=config.rate_limit_seconds,
                    retries=config.retries,
                ),
                fetcher=fetcher,
                sleep_fn=sleep_fn,
            )
            status = "ok" if not ingestion_report["failed"] else "partial"
            stages.append(_stage(
                "price_ingestion", status,
                requested=len(ingestion_report["requested"]),
                returned=len(ingestion_report["returned"]),
                failed=ingestion_report["failed"],
                missing=ingestion_report["missing"],
                new_rows=ingestion_report["new_rows"],
                total_rows=ingestion_report["total_rows"],
            ))
        except Exception as exc:
            logger.exception("Universe price ingestion failed")
            stages.append(_stage("price_ingestion", "failed", error=str(exc)))
    else:
        stages.append(_stage("price_ingestion", "skipped", reason="no inventory"))

    # 3. Coverage matrix from the authoritative calendar
    coverage_report = None
    normalized_path = config.data_dir / "normalized" / "prices_universe.parquet"
    if inventory and normalized_path.is_file():
        try:
            prices = pd.read_parquet(normalized_path)
            active_dir = resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data")
            calendar_path = active_dir / "trading_calendar.parquet"
            calendar = (
                pd.read_parquet(calendar_path) if calendar_path.is_file() else None
            )
            coverage_report = build_coverage_matrix(
                inventory, prices, calendar,
                freshness_days=config.freshness_days,
            )
            write_json_atomic(
                config.reports_dir / "universe_coverage.json", coverage_report
            )
            (config.reports_dir / "universe_coverage.md").write_text(
                render_coverage(coverage_report), encoding="utf-8"
            )
            stages.append(_stage(
                "coverage_matrix", "ok",
                instruments=coverage_report["instrument_count"],
                usable=coverage_report["usable_instruments"],
                coverage_pct=coverage_report["coverage_pct"],
                status_counts=coverage_report["status_counts"],
            ))
        except Exception as exc:
            logger.exception("Coverage matrix failed")
            stages.append(_stage("coverage_matrix", "failed", error=str(exc)))
    else:
        stages.append(_stage(
            "coverage_matrix", "skipped", reason="normalized prices unavailable"
        ))

    failed = [stage["name"] for stage in stages if stage["status"] == "failed"]
    partial = [stage["name"] for stage in stages if stage["status"] == "partial"]
    status = "FAILED" if failed else ("PARTIAL" if partial else "SUCCESS")

    run_id = "universe_refresh_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "end": str(end),
        "history_start": str(config.history_start),
        "stages": stages,
        "inventory": None if inventory is None else {
            "scope": inventory["scope"],
            "complete": inventory["complete"],
            "counts": inventory["counts"],
        },
        "coverage": None if coverage_report is None else {
            "instruments": coverage_report["instrument_count"],
            "usable": coverage_report["usable_instruments"],
            "coverage_pct": coverage_report["coverage_pct"],
            "status_counts": coverage_report["status_counts"],
        },
        "ingestion": None if ingestion_report is None else {
            "returned": ingestion_report["returned"],
            "failed": ingestion_report["failed"],
            "missing": ingestion_report["missing"],
            "new_rows": ingestion_report["new_rows"],
            "total_rows": ingestion_report["total_rows"],
        },
        "active_generation_touched": False,
        "supabase_writes": False,
    }
    report_path = config.reports_dir / f"{run_id}.json"
    write_json_atomic(report_path, report)

    if inventory:
        (config.reports_dir / "instrument_inventory.md").write_text(
            render_inventory(inventory), encoding="utf-8"
        )
    report["report_path"] = str(report_path)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local universe refresh.")
    parser.add_argument("--end", default=None)
    parser.add_argument("--history-start", default="2024-10-07")
    parser.add_argument("--freshness-days", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(argv)

    setup_logging()
    report = refresh_universe(UniverseRefreshConfig(
        end=date.fromisoformat(args.end) if args.end else None,
        history_start=date.fromisoformat(args.history_start),
        freshness_days=args.freshness_days,
        batch_size=args.batch_size,
        rate_limit_seconds=args.rate_limit,
        retries=args.retries,
    ))
    print(json.dumps({
        "status": report["status"],
        "run_id": report["run_id"],
        "stages": [(stage["name"], stage["status"]) for stage in report["stages"]],
        "coverage": report["coverage"],
        "report": report["report_path"],
    }, indent=2))
    return 0 if report["status"] in {"SUCCESS", "PARTIAL"} else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
