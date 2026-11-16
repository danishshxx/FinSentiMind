"""TASK 107 — failure-safe, versioned dataset refresh workflow.

Order:
    price ingestion -> price cleaning -> quality gate -> trading calendar
    -> news ingestion -> sentiment processing -> sentiment aggregation
    -> base dataset -> full validation -> atomic dataset publication

Every derived artifact is written into ``ml/data/staging/<run_id>/``. The
active generation is switched only after all required stages and the full
TASK 106 validation succeed. Failed runs leave ``active.json`` untouched.

This module uses the existing pipeline functions; it does not duplicate their
financial or data-cleaning logic.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from app.jobs.run_news_scraper import run_news_pipeline
from app.jobs.run_sentiment_pipeline import run_sentiment_analysis
from ml.features.quant import build_feature_dataset
from ml.preprocessing.backfill import run_backfill
from ml.preprocessing.build_base_dataset import run_build
from ml.preprocessing.daily_sentiment import build_daily_sentiment_dataset
from ml.preprocessing.dataset_store import (
    GENERATION_MANIFEST,
    DatasetStoreError,
    publish_generation,
    read_active_manifest,
    resolve_active_dataset,
    rollback_to_generation,
    write_json_atomic,
    list_generations,
)
from ml.preprocessing.load_prices import load_prices_clean
from ml.preprocessing.quality_gate import (
    build_report as build_quality_report,
    load_clean_prices,
    save_report as save_quality_report,
)
from ml.preprocessing.refresh_lock import ExecutionLock, RefreshLocked
from ml.preprocessing.trading_calendar import (
    derive_trading_calendar,
    load_prices as load_prices_for_calendar,
    save_calendar,
)
from ml.validation.validate_dataset import (
    render_markdown,
    run_all_checks,
    save_report_atomic,
)


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = _PROJECT_ROOT / "ml" / "data"
DEFAULT_REPORTS_DIR = _PROJECT_ROOT / "ml" / "reports"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_LOCKED = 3
EXIT_PUBLISH_FAILED = 4

MUTATING_STAGES = {"price_ingestion", "news_ingestion", "sentiment_processing"}
StageStatus = Literal["ok", "failed", "skipped", "warning"]


class StageFailure(RuntimeError):
    """A refresh stage failed and publication must be prevented."""


class RefreshConfigError(RuntimeError):
    """Invalid refresh configuration."""


@dataclass(frozen=True)
class StageSpec:
    name: str
    label: str
    retryable: bool = False


STAGE_SPECS = (
    StageSpec("price_ingestion", "Price ingestion"),
    StageSpec("price_cleaning", "Price normalization and cleaning"),
    StageSpec("quality_gate", "Price quality gate"),
    StageSpec("trading_calendar", "Trading calendar update"),
    StageSpec("news_ingestion", "News ingestion"),
    StageSpec("sentiment_processing", "Sentiment processing"),
    StageSpec("sentiment_aggregation", "Daily sentiment aggregation"),
    StageSpec("base_dataset", "Base dataset construction"),
    StageSpec("quant_features", "Quant feature build"),
    StageSpec("validation", "Full dataset validation"),
)
PUBLICATION_SPEC = StageSpec("publication", "Dataset publication")


@dataclass
class RefreshConfig:
    data_root: Path = DEFAULT_DATA_ROOT
    reports_dir: Path = DEFAULT_REPORTS_DIR
    run_id: Optional[str] = None
    dry_run: bool = False
    validate_only: bool = False
    retries: int = 1
    retry_backoff_seconds: float = 1.0
    freshness_threshold_days: int = 7
    lock_timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.retries < 0:
            raise RefreshConfigError("retries must be >= 0")
        if self.retry_backoff_seconds < 0:
            raise RefreshConfigError("retry_backoff_seconds must be >= 0")
        if self.freshness_threshold_days < 0:
            raise RefreshConfigError("freshness_threshold_days must be >= 0")


@dataclass
class StageResult:
    name: str
    label: str
    status: StageStatus
    attempts: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "details": _json_safe(self.details),
        }


@dataclass
class RefreshContext:
    config: RefreshConfig
    run_id: str
    started_at: str
    staging_dir: Path
    previous_manifest: Optional[Dict[str, Any]]
    artifacts: Dict[str, Path] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    stage_results: List[StageResult] = field(default_factory=list)
    validation_summary: Optional[Dict[str, Any]] = None
    failure_reason: Optional[str] = None
    data_as_of: Optional[str] = None
    latest_market_data_date: Optional[str] = None
    latest_sentiment_date: Optional[str] = None
    published_at: Optional[str] = None
    generation: Optional[Dict[str, Any]] = None
    report_paths: Dict[str, str] = field(default_factory=dict)


@dataclass
class RefreshOutcome:
    exit_code: int
    status: str
    report: Dict[str, Any]
    report_path: Optional[Path]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


def _date_string(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def _report_rel(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return path.relative_to(_PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Default stage handlers (reuse existing pipeline modules)
# ---------------------------------------------------------------------------

def stage_price_ingestion(ctx: RefreshContext) -> None:
    summary = run_backfill()
    ctx.metrics["price_ingestion"] = {
        "total_tickers": summary.total_tickers,
        "ok": summary.ok_count,
        "empty": summary.empty_count,
        "errors": summary.error_count,
        "rows_fetched": summary.total_fetched,
        "rows_new": summary.total_new,
        "rows_overlap": summary.total_overlap,
    }
    if summary.error_count or summary.empty_count:
        raise StageFailure(
            f"Price ingestion failed for {summary.error_count} ticker(s) and "
            f"returned no data for {summary.empty_count} ticker(s); "
            "publication is blocked."
        )
    if summary.ok_count == 0:
        raise StageFailure(
            "Price ingestion produced no successful ticker; publication is blocked."
        )


def stage_price_cleaning(ctx: RefreshContext) -> None:
    output = ctx.staging_dir / "prices_clean.parquet"
    quarantine_path = (
        ctx.config.reports_dir / f"exchange_closure_quarantine_{ctx.run_id}.json"
    )
    prices = load_prices_clean(
        save=True, output_path=output, quarantine_report_path=quarantine_path
    )
    if prices.empty:
        raise StageFailure("Cleaned price dataset is empty.")
    excluded = int(prices.attrs.get("closure_excluded_rows", 0))
    ctx.metrics["exchange_closure_quarantine"] = {
        "rows_excluded": excluded,
        "calendar_version": prices.attrs.get("calendar_version"),
    }
    if excluded and quarantine_path.exists():
        ctx.report_paths["exchange_closure_quarantine"] = (
            _report_rel(quarantine_path) or str(quarantine_path)
        )
    ctx.artifacts["prices_clean"] = output
    ctx.metrics["price_rows"] = int(len(prices))


def stage_quality_gate(ctx: RefreshContext) -> None:
    prices_path = ctx.artifacts.get("prices_clean")
    if prices_path is None or not prices_path.exists():
        raise StageFailure("prices_clean.parquet is missing before quality gate.")
    prices = load_clean_prices(prices_path)
    report = build_quality_report(prices, input_path=prices_path)
    report_path = ctx.config.reports_dir / f"quality_gate_{ctx.run_id}.md"
    save_quality_report(report, report_path)
    ctx.report_paths["quality_gate"] = _report_rel(report_path) or str(report_path)
    ctx.metrics["quality_gate"] = {
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "rows": report.total_rows,
        "tickers": report.total_tickers,
    }
    if report.has_errors:
        raise StageFailure(
            f"Price quality gate found {report.error_count} error(s)."
        )


def stage_trading_calendar(ctx: RefreshContext) -> None:
    prices_path = ctx.artifacts.get("prices_clean")
    if prices_path is None or not prices_path.exists():
        raise StageFailure("prices_clean.parquet is missing before calendar update.")
    prices = load_prices_for_calendar(prices_path)
    calendar = derive_trading_calendar(prices)
    if calendar.empty:
        raise StageFailure("Derived trading calendar is empty.")
    output = ctx.staging_dir / "trading_calendar.parquet"
    save_calendar(calendar, output)
    ctx.artifacts["trading_calendar"] = output


def stage_news_ingestion(_ctx: RefreshContext) -> None:
    run_news_pipeline(strict=True)


def stage_sentiment_processing(_ctx: RefreshContext) -> None:
    run_sentiment_analysis(strict=True)


def stage_sentiment_aggregation(ctx: RefreshContext) -> None:
    calendar_path = ctx.artifacts.get("trading_calendar")
    if calendar_path is None or not calendar_path.exists():
        raise StageFailure("trading_calendar.parquet is missing before sentiment aggregation.")
    output = ctx.staging_dir / "daily_sentiment.parquet"
    sentiment = build_daily_sentiment_dataset(
        save=True,
        output_path=output,
        calendar_path=calendar_path,
        run_id=ctx.run_id,
        reports_dir=ctx.config.reports_dir,
    )
    audit = sentiment.attrs.get("mapping_audit", {})
    ctx.metrics["sentiment_mapping"] = audit
    pending_report = sentiment.attrs.get("pending_report_path")
    if pending_report:
        ctx.report_paths["pending_news"] = _report_rel(Path(pending_report)) or pending_report

    mapped = int(audit.get("mapped", 0))
    pending = int(audit.get("pending_future_session", 0))
    quarantined = int(audit.get("quarantined_unresolvable", 0))

    if sentiment.empty:
        if pending > 0 and quarantined == 0:
            logger.warning(
                "Daily sentiment is empty because all %d eligible article(s) "
                "are pending a future verified session; publishing an explicit "
                "empty sentiment artifact.",
                pending,
            )
            ctx.metrics["sentiment_empty_reason"] = "all_articles_pending_future_session"
        else:
            raise StageFailure(
                "Daily sentiment dataset is empty and no all-pending explanation "
                f"exists (mapped={mapped}, pending={pending}, quarantined={quarantined})."
            )
    elif quarantined > 0:
        logger.warning(
            "%d news article(s) were quarantined with explicit reasons.",
            quarantined,
        )

    ctx.artifacts["daily_sentiment"] = output
    ctx.metrics["sentiment_rows"] = int(len(sentiment))


def stage_base_dataset(ctx: RefreshContext) -> None:
    prices_path = ctx.artifacts.get("prices_clean")
    sentiment_path = ctx.artifacts.get("daily_sentiment")
    if prices_path is None or sentiment_path is None:
        raise StageFailure("Required price or sentiment artifact is missing.")
    output = ctx.staging_dir / "base_dataset.parquet"
    base = run_build(
        prices_path=prices_path,
        sentiment_path=sentiment_path,
        output_path=output,
        save=True,
    )
    if base.empty:
        raise StageFailure("Base dataset is empty.")
    ctx.artifacts["base_dataset"] = output
    ctx.metrics["base_dataset_rows"] = int(len(base))


def stage_quant_features(ctx: RefreshContext) -> None:
    prices_path = ctx.artifacts.get("prices_clean")
    if prices_path is None or not prices_path.exists():
        raise StageFailure("prices_clean.parquet is missing before quant features.")
    output = ctx.staging_dir / "quant_features.parquet"
    build = build_feature_dataset(
        input_path=prices_path,
        output_path=output,
        manifest_path=ctx.config.reports_dir / f"quant_features_{ctx.run_id}_manifest.json",
        report_path=ctx.config.reports_dir / f"quant_features_{ctx.run_id}_quality.json",
    )
    if build.features.empty:
        raise StageFailure("Quant feature dataset is empty.")
    ctx.artifacts["quant_features"] = output
    ctx.report_paths["quant_features"] = str(
        ctx.config.reports_dir / f"quant_features_{ctx.run_id}_quality.json"
    )


def stage_validation(ctx: RefreshContext) -> None:
    base_path = ctx.artifacts.get("base_dataset")
    prices_path = ctx.artifacts.get("prices_clean")
    sentiment_path = ctx.artifacts.get("daily_sentiment")
    if base_path is None:
        raise StageFailure("base_dataset.parquet is missing before validation.")
    report = run_all_checks(
        base_path=base_path,
        prices_path=prices_path,
        sentiment_path=sentiment_path,
        as_of_date=date.today(),
        freshness_threshold_days=ctx.config.freshness_threshold_days,
    )
    validation_path = ctx.config.reports_dir / f"validation_{ctx.run_id}.md"
    save_report_atomic(render_markdown(report), validation_path)
    ctx.report_paths["validation"] = _report_rel(validation_path) or str(validation_path)
    counts = report.counts
    metrics = report.metrics or {}
    ctx.validation_summary = {
        "status": report.overall_status,
        "counts": {key.lower(): value for key, value in counts.items()},
        "has_failures": report.has_failures,
        "freshness_age_days": metrics.get("age_days"),
        "latest_trading_date": metrics.get("latest_trading_date"),
        "sentiment_matched_rows": metrics.get("matched_rows"),
        "sentiment_matched_pct": metrics.get("matched_pct"),
    }
    if report.has_failures:
        raise StageFailure(
            f"TASK 106 validation failed: {counts.get('FAIL', 0)} FAIL check(s)."
        )


DEFAULT_HANDLERS: Dict[str, Callable[[RefreshContext], None]] = {
    "price_ingestion": stage_price_ingestion,
    "price_cleaning": stage_price_cleaning,
    "quality_gate": stage_quality_gate,
    "trading_calendar": stage_trading_calendar,
    "news_ingestion": stage_news_ingestion,
    "sentiment_processing": stage_sentiment_processing,
    "sentiment_aggregation": stage_sentiment_aggregation,
    "base_dataset": stage_base_dataset,
    "quant_features": stage_quant_features,
    "validation": stage_validation,
}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute_stage(
    spec: StageSpec,
    handler: Callable[[RefreshContext], None],
    ctx: RefreshContext,
) -> StageResult:
    attempts = 0
    started = _utc_now()
    start_clock = time.monotonic()
    last_error: Optional[Exception] = None

    while True:
        attempts += 1
        try:
            handler(ctx)
            return StageResult(
                name=spec.name,
                label=spec.label,
                status="ok",
                attempts=attempts,
                started_at=started,
                completed_at=_utc_now(),
                duration_ms=int((time.monotonic() - start_clock) * 1000),
            )
        except Exception as exc:  # stage handlers intentionally raise freely
            last_error = exc
            if spec.retryable and attempts <= ctx.config.retries:
                logger.warning(
                    "Stage %s failed (attempt %d/%d): %s",
                    spec.name,
                    attempts,
                    ctx.config.retries + 1,
                    exc,
                )
                time.sleep(ctx.config.retry_backoff_seconds)
                continue
            logger.error("Stage %s failed: %s", spec.name, exc)
            return StageResult(
                name=spec.name,
                label=spec.label,
                status="failed",
                attempts=attempts,
                started_at=started,
                completed_at=_utc_now(),
                duration_ms=int((time.monotonic() - start_clock) * 1000),
                error=str(last_error),
            )


def _collect_metrics(ctx: RefreshContext) -> None:
    base_path = ctx.artifacts.get("base_dataset")
    prices_path = ctx.artifacts.get("prices_clean")
    sentiment_path = ctx.artifacts.get("daily_sentiment")

    if base_path and base_path.exists():
        base = pd.read_parquet(base_path)
        if not base.empty and "trading_date" in base.columns:
            ctx.data_as_of = _date_string(pd.to_datetime(base["trading_date"]).max())
            ctx.latest_market_data_date = ctx.data_as_of
        ctx.metrics["base_dataset_rows"] = int(len(base))
        ctx.metrics["ticker_count"] = int(base["ticker"].nunique()) if "ticker" in base.columns else 0
    elif prices_path and prices_path.exists():
        prices = pd.read_parquet(prices_path)
        if not prices.empty and "trading_date" in prices.columns:
            ctx.data_as_of = _date_string(pd.to_datetime(prices["trading_date"]).max())
            ctx.latest_market_data_date = ctx.data_as_of
        ctx.metrics["ticker_count"] = int(prices["ticker"].nunique()) if "ticker" in prices.columns else 0

    if prices_path and prices_path.exists():
        ctx.metrics.setdefault("price_rows", int(len(pd.read_parquet(prices_path))))
    if sentiment_path and sentiment_path.exists():
        sentiment = pd.read_parquet(sentiment_path)
        ctx.metrics["sentiment_rows"] = int(len(sentiment))
        if not sentiment.empty and "trading_date" in sentiment.columns:
            ctx.latest_sentiment_date = _date_string(
                pd.to_datetime(sentiment["trading_date"]).max()
            )


def _stage_result_records(results: List[StageResult]) -> List[Dict[str, Any]]:
    return [result.to_dict() for result in results]


def _previous_generation(manifest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not manifest:
        return None
    return {
        "run_id": manifest.get("run_id"),
        "generation": manifest.get("generation"),
        "data_as_of": manifest.get("data_as_of"),
    }


def _build_report_payload(
    ctx: RefreshContext, status: str, completed_at: str
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": ctx.run_id,
        "started_at": ctx.started_at,
        "completed_at": completed_at,
        "status": status,
        "dry_run": ctx.config.dry_run,
        "refreshed_at": ctx.published_at,
        "published": ctx.published_at is not None,
        "data_as_of": ctx.data_as_of,
        "latest_market_data_date": ctx.latest_market_data_date,
        "latest_sentiment_date": ctx.latest_sentiment_date,
        "price_rows": ctx.metrics.get("price_rows"),
        "sentiment_rows": ctx.metrics.get("sentiment_rows"),
        "base_dataset_rows": ctx.metrics.get("base_dataset_rows"),
        "ticker_count": ctx.metrics.get("ticker_count"),
        "validation": ctx.validation_summary or {"status": "NOT_RUN"},
        "stage_statuses": _stage_result_records(ctx.stage_results),
        "failure_reason": ctx.failure_reason,
        "generation": ctx.generation,
        "previous_generation": _previous_generation(ctx.previous_manifest),
        "staging_dir": str(ctx.staging_dir) if ctx.staging_dir.exists() else None,
        "reports": ctx.report_paths,
        "metrics": _json_safe(ctx.metrics),
    }


def _write_report(
    ctx: RefreshContext, report: Dict[str, Any], status: str
) -> Path:
    report_path = ctx.config.reports_dir / f"refresh_{ctx.run_id}.json"
    write_json_atomic(report_path, report)
    write_json_atomic(ctx.config.reports_dir / "refresh_latest.json", report)
    if status == "SUCCESS":
        write_json_atomic(ctx.config.reports_dir / "refresh_last_success.json", report)
    return report_path


def _run_validation_only(config: RefreshConfig) -> RefreshOutcome:
    run_id = config.run_id or _new_run_id()
    started = _utc_now()
    active = resolve_active_dataset(config.data_root)
    base_path = active.active_dir / "base_dataset.parquet"
    prices_path = active.active_dir / "prices_clean.parquet"
    sentiment_path = active.active_dir / "daily_sentiment.parquet"
    summary: Dict[str, Any] = {}
    failure = None
    exit_code = EXIT_OK
    data_as_of = None
    try:
        if not base_path.exists():
            raise StageFailure(
                f"No active base dataset found under {active.active_dir}."
            )
        report = run_all_checks(
            base_path=base_path,
            prices_path=prices_path if prices_path.exists() else None,
            sentiment_path=sentiment_path if sentiment_path.exists() else None,
            as_of_date=date.today(),
            freshness_threshold_days=config.freshness_threshold_days,
        )
        counts = report.counts
        metrics = report.metrics or {}
        base = pd.read_parquet(base_path)
        if not base.empty:
            data_as_of = _date_string(pd.to_datetime(base["trading_date"]).max())
        summary = {
            "status": report.overall_status,
            "counts": {key.lower(): value for key, value in counts.items()},
            "has_failures": report.has_failures,
            "freshness_age_days": metrics.get("age_days"),
            "latest_trading_date": metrics.get("latest_trading_date"),
        }
        if report.has_failures:
            failure = f"Validation failed: {counts.get('FAIL', 0)} FAIL check(s)."
            exit_code = EXIT_FAILED
    except Exception as exc:
        failure = str(exc)
        exit_code = EXIT_FAILED
        summary = {"status": "FAIL", "has_failures": True, "error": str(exc)}

    completed = _utc_now()
    report_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started,
        "completed_at": completed,
        "status": "VALIDATION_ONLY",
        "dry_run": False,
        "refreshed_at": None,
        "published": False,
        "data_as_of": data_as_of,
        "latest_market_data_date": data_as_of,
        "latest_sentiment_date": None,
        "price_rows": None,
        "sentiment_rows": None,
        "base_dataset_rows": None,
        "ticker_count": None,
        "validation": summary,
        "stage_statuses": [],
        "failure_reason": failure,
        "generation": None,
        "previous_generation": _previous_generation(active.manifest),
        "staging_dir": None,
        "reports": {},
        "metrics": {"active_source": active.source},
    }
    report_path = config.reports_dir / f"refresh_{run_id}.json"
    write_json_atomic(report_path, report_payload)
    write_json_atomic(config.reports_dir / "refresh_latest.json", report_payload)
    return RefreshOutcome(exit_code, "VALIDATION_ONLY", report_payload, report_path)


def _execute_refresh(
    config: RefreshConfig,
    handlers: Optional[Mapping[str, Callable[[RefreshContext], None]]] = None,
) -> RefreshOutcome:
    run_id = config.run_id or _new_run_id()
    staging = config.data_root / "staging" / run_id
    if staging.exists():
        raise RefreshConfigError(
            f"Staging directory already exists for run_id {run_id}: {staging}"
        )
    previous = read_active_manifest(config.data_root)
    ctx = RefreshContext(
        config=config,
        run_id=run_id,
        started_at=_utc_now(),
        staging_dir=staging,
        previous_manifest=previous,
    )
    staging.mkdir(parents=True, exist_ok=False)

    chosen = {**DEFAULT_HANDLERS, **(handlers or {})}
    failed_stage: Optional[str] = None

    for spec in STAGE_SPECS:
        if failed_stage is not None:
            ctx.stage_results.append(
                StageResult(
                    name=spec.name,
                    label=spec.label,
                    status="skipped",
                    error=f"Not run after failure in {failed_stage}.",
                )
            )
            continue
        if config.dry_run and spec.name in MUTATING_STAGES:
            ctx.stage_results.append(
                StageResult(
                    name=spec.name,
                    label=spec.label,
                    status="skipped",
                    details={"reason": "dry-run does not mutate external providers"},
                )
            )
            continue
        result = _execute_stage(spec, chosen[spec.name], ctx)
        ctx.stage_results.append(result)
        if result.status == "failed":
            failed_stage = spec.name
            ctx.failure_reason = f"{spec.label}: {result.error}"

    # Metrics must be collected before publication because publishing moves the
    # staging directory into an immutable generation.
    try:
        _collect_metrics(ctx)
    except Exception as exc:
        logger.error("Failed to collect refresh metrics: %s", exc)
        ctx.metrics["metrics_error"] = str(exc)
        if failed_stage is None:
            failed_stage = "metrics_collection"
            ctx.failure_reason = f"Refresh metrics collection: {exc}"
            ctx.stage_results.append(
                StageResult(
                    name="metrics_collection",
                    label="Refresh metrics",
                    status="failed",
                    error=str(exc),
                )
            )

    # Publication is never attempted after a skipped/failed required stage.
    publication_failed = False
    if failed_stage is None and not config.dry_run:
        ctx.published_at = _utc_now()
        result = _execute_stage(
            PUBLICATION_SPEC,
            lambda current: _publish(ctx),
            ctx,
        )
        ctx.stage_results.append(result)
        if result.status == "failed":
            publication_failed = True
            ctx.published_at = None
            ctx.failure_reason = f"{PUBLICATION_SPEC.label}: {result.error}"

    completed = _utc_now()

    if failed_stage is not None or publication_failed:
        status = "FAILED"
        exit_code = EXIT_PUBLISH_FAILED if publication_failed else EXIT_FAILED
    elif config.dry_run:
        status = "DRY_RUN"
        exit_code = EXIT_OK
    else:
        status = "SUCCESS"
        exit_code = EXIT_OK

    report = _build_report_payload(ctx, status, completed)
    report_path: Optional[Path] = None
    try:
        report_path = _write_report(ctx, report, status)
    except Exception as exc:
        logger.error(
            "Refresh finished with status %s but the JSON report could not be written: %s",
            status,
            exc,
        )
    return RefreshOutcome(exit_code, status, report, report_path)


def _publish(ctx: RefreshContext) -> None:
    published = publish_generation(
        ctx.staging_dir,
        ctx.config.data_root,
        ctx.run_id,
        metadata={
            "refreshed_at": ctx.published_at,
            "data_as_of": ctx.data_as_of,
            "report": f"refresh_{ctx.run_id}.json",
            "notes": [
                "Generation published only after all required stages and TASK 106 validation passed."
            ],
        },
    )
    ctx.generation = {
        "run_id": ctx.run_id,
        "generation": published.manifest.get("generation"),
        "manifest": f"{published.manifest.get('generation')}/{GENERATION_MANIFEST}",
        "published_at": published.manifest.get("published_at"),
        "data_as_of": published.manifest.get("data_as_of"),
        "refreshed_at": published.manifest.get("refreshed_at"),
    }


def load_report(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_refresh(
    config: Optional[RefreshConfig] = None,
    handlers: Optional[Mapping[str, Callable[[RefreshContext], None]]] = None,
) -> RefreshOutcome:
    config = config or RefreshConfig()
    if config.validate_only:
        return _run_validation_only(config)
    try:
        with ExecutionLock(
            config.data_root / "refresh.lock",
            timeout=config.lock_timeout_seconds,
        ):
            return _execute_refresh(config, handlers)
    except RefreshLocked as exc:
        logger.error("Refresh lock is already held: %s", exc)
        return RefreshOutcome(
            EXIT_LOCKED,
            "LOCKED",
            {
                "status": "LOCKED",
                "run_id": None,
                "failure_reason": str(exc),
                "published": False,
            },
            None,
        )
    except RefreshConfigError as exc:
        logger.error("Refresh configuration error: %s", exc)
        return RefreshOutcome(
            EXIT_CONFIG,
            "CONFIG_ERROR",
            {"status": "CONFIG_ERROR", "failure_reason": str(exc), "published": False},
            None,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_outcome(outcome: RefreshOutcome) -> None:
    print(f"status={outcome.status} exit={outcome.exit_code}")
    if outcome.report_path:
        print(f"report={outcome.report_path}")
    reason = outcome.report.get("failure_reason")
    if reason:
        print(f"failure_reason={reason}")
    generation = outcome.report.get("generation")
    if generation:
        print(f"generation={generation.get('generation')}")
    if outcome.report.get("data_as_of"):
        print(f"data_as_of={outcome.report.get('data_as_of')}")
    if outcome.report.get("refreshed_at"):
        print(f"refreshed_at={outcome.report.get('refreshed_at')}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TASK 107 failure-safe dataset refresh and publication."
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run derived stages without provider writes or publication.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the active dataset only; no refresh or publication.",
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-backoff", type=float, default=1.0)
    parser.add_argument("--freshness-threshold-days", type=int, default=7)
    parser.add_argument("--lock-timeout", type=float, default=0.0)
    parser.add_argument(
        "--rollback",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Atomically point active.json back to an existing verified generation.",
    )
    parser.add_argument(
        "--list-generations",
        action="store_true",
        help="Print known dataset generations as JSON.",
    )
    args = parser.parse_args(argv)

    setup_logging()

    if args.list_generations:
        print(json.dumps(list_generations(args.data_root), indent=2))
        return EXIT_OK

    if args.rollback:
        try:
            with ExecutionLock(
                args.data_root / "refresh.lock", timeout=args.lock_timeout
            ):
                published = rollback_to_generation(args.data_root, args.rollback)
        except RefreshLocked as exc:
            print(f"status=LOCKED exit={EXIT_LOCKED}\nfailure_reason={exc}")
            return EXIT_LOCKED
        except DatasetStoreError as exc:
            print(f"status=ROLLBACK_FAILED exit={EXIT_FAILED}\nfailure_reason={exc}")
            return EXIT_FAILED
        print(f"status=ROLLED_BACK exit={EXIT_OK}")
        print(f"generation={published.manifest.get('generation')}")
        print(f"data_as_of={published.manifest.get('data_as_of')}")
        return EXIT_OK

    try:
        config = RefreshConfig(
            data_root=args.data_root,
            reports_dir=args.reports_dir,
            run_id=args.run_id,
            dry_run=args.dry_run,
            validate_only=args.validate_only,
            retries=args.retries,
            retry_backoff_seconds=args.retry_backoff,
            freshness_threshold_days=args.freshness_threshold_days,
            lock_timeout_seconds=args.lock_timeout,
        )
    except RefreshConfigError as exc:
        print(f"status=CONFIG_ERROR exit={EXIT_CONFIG}\nfailure_reason={exc}")
        return EXIT_CONFIG

    outcome = run_refresh(config)
    _print_outcome(outcome)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
