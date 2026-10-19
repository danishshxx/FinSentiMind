from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import List, Literal, Optional

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.backfill import run_backfill
from ml.preprocessing.daily_sentiment import build_daily_sentiment_dataset
from ml.preprocessing.load_prices import load_prices_clean
from ml.preprocessing.trading_calendar import (
    DEFAULT_OUTPUT as CALENDAR_OUTPUT,
    derive_trading_calendar,
    load_prices as load_prices_for_calendar,
    save_calendar,
)


logger = get_logger(__name__)

StepStatus = Literal["ok", "failed", "skipped"]


@dataclass
class StepResult:
    name: str
    status: StepStatus
    error: Optional[str] = None


def _run_news_step() -> StepResult:
    try:
        from app.jobs.run_news_scraper import run_news_pipeline
        run_news_pipeline()
        from app.jobs.run_sentiment_pipeline import run_sentiment_analysis
        run_sentiment_analysis()
        return StepResult("news", "ok")
    except Exception as e:
        logger.error("News step failed: %s", e)
        return StepResult("news", "failed", str(e))


def _run_backfill_step() -> StepResult:
    try:
        run_backfill()
        return StepResult("backfill", "ok")
    except Exception as e:
        logger.error("Backfill step failed: %s", e)
        return StepResult("backfill", "failed", str(e))


def _run_derived_chain() -> StepResult:
    """
    Sequential derived chain (steps 3-5). NOT atomic.

    Each step writes its artifact independently. On failure, earlier
    steps may have already updated their artifacts, so the caller must
    treat a FAILED result as requiring a successful re-run before any
    downstream use.
    """
    try:
        logger.info("[3/5] Rebuild prices_clean.parquet")
        load_prices_clean(save=True)

        logger.info("[4/5] Rebuild trading_calendar.parquet")
        prices = load_prices_for_calendar()
        cal = derive_trading_calendar(prices)
        save_calendar(cal, CALENDAR_OUTPUT)

        logger.info("[5/5] Rebuild daily_sentiment.parquet")
        build_daily_sentiment_dataset(save=True)

        return StepResult("derived", "ok")
    except Exception as e:
        logger.error("Derived chain failed: %s", e)
        return StepResult("derived", "failed", str(e))


def refresh_all(skip_news: bool = False, skip_backfill: bool = False) -> int:
    setup_logging()
    logger.info("=" * 60)
    logger.info("FULL REFRESH START")
    logger.info("=" * 60)

    results: List[StepResult] = []

    # --- Source steps ---
    if skip_news:
        logger.info("[1/5] News step SKIPPED (--skip-news)")
        results.append(StepResult("news", "skipped"))
    else:
        logger.info("[1/5] Refresh news")
        results.append(_run_news_step())

    if skip_backfill:
        logger.info("[2/5] Backfill SKIPPED (--skip-backfill)")
        results.append(StepResult("backfill", "skipped"))
    else:
        logger.info("[2/5] Backfill prices from yfinance")
        results.append(_run_backfill_step())

    # --- Derived chain (always run — rebuilds from current source state) ---
    derived = _run_derived_chain()
    results.append(derived)

    # --- Determine overall status ---
    source_failures = [
        r for r in results if r.name != "derived" and r.status == "failed"
    ]

    if derived.status == "failed":
        status = "FAILED"
        exit_code = 2
    elif source_failures:
        status = "PARTIAL"
        exit_code = 1
    else:
        status = "SUCCESS"
        exit_code = 0

    # --- Summary ---
    logger.info("=" * 60)
    logger.info("REFRESH STATUS: %s (exit=%d)", status, exit_code)
    logger.info("=" * 60)
    for r in results:
        line = f"  {r.name:<10} {r.status}"
        if r.error:
            line += f"  — {r.error}"
        logger.info(line)
    if status == "PARTIAL":
        logger.warning(
            "PARTIAL: derived artifacts rebuilt from existing data. "
            "Investigate source step failure before relying on freshness."
        )
    elif status == "FAILED":
        logger.error(
            "FAILED: derived artifacts may be inconsistent. "
            "Do not use downstream. Re-run the pipeline successfully before "
            "consuming any derived artifact."
        )
    logger.info("=" * 60)

    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Full pipeline refresh (TASK 104 utility)."
    )
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-backfill", action="store_true")
    args = parser.parse_args(argv)
    return refresh_all(skip_news=args.skip_news, skip_backfill=args.skip_backfill)


if __name__ == "__main__":
    sys.exit(main())