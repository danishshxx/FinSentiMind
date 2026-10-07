from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from app.core.aliases import WATCH_TICKERS
from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all, get_supabase_client
from app.models.schemas import StockPrice
from app.scraper.sources.yahoo import YahooFinanceScraper


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PERIOD = "2y"
DEFAULT_MAX_RETRIES = 3

# Fields compared during change detection.
PRICE_FIELDS = ("harga_buka", "harga_tertinggi", "harga_terendah", "harga_tutup")

# Relative-change thresholds (percent) to trigger a WARNING log.
PRICE_CHANGE_WARN_PCT = 0.5
VOLUME_CHANGE_WARN_PCT = 1.0


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class ChangeRecord:
    """A single field-level change for an existing (ticker, tanggal)."""

    ticker: str
    tanggal: date
    field: str
    old_value: float
    new_value: float
    pct_diff: float


@dataclass
class TickerBackfillResult:
    """Outcome for a single ticker."""

    ticker: str
    status: Literal["ok", "empty", "error"]
    rows_fetched: int = 0
    rows_new: int = 0
    rows_overlap: int = 0
    changes: List[ChangeRecord] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class BackfillSummary:
    """Aggregate report across the whole run."""

    period: str
    dry_run: bool
    total_tickers: int
    results: List[TickerBackfillResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def empty_count(self) -> int:
        return sum(1 for r in self.results if r.status == "empty")

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.status == "error")

    @property
    def total_fetched(self) -> int:
        return sum(r.rows_fetched for r in self.results)

    @property
    def total_new(self) -> int:
        return sum(r.rows_new for r in self.results)

    @property
    def total_overlap(self) -> int:
        return sum(r.rows_overlap for r in self.results)

    @property
    def total_changes(self) -> int:
        return sum(len(r.changes) for r in self.results)


# ---------------------------------------------------------------------------
# Fetch with retry
# ---------------------------------------------------------------------------

def fetch_ticker_with_retry(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> List[StockPrice]:
    """
    Fetch OHLCV from yfinance with exponential-backoff retry.

    Args:
        ticker: IDX ticker without suffix (e.g., 'BBCA').
        period: yfinance period string ('1mo', '1y', '2y', 'max').
        max_retries: Number of attempts on transient error.

    Returns:
        List of StockPrice (empty if no data available).

    Raises:
        Exception: If all retries exhausted.
    """
    yf_ticker = f"{ticker}.JK"
    scraper = YahooFinanceScraper(raise_on_error=True)
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            return scraper.fetch_historical_data(yf_ticker, period=period)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Fetch %s failed (attempt %d/%d), retry in %ss: %s",
                    ticker, attempt + 1, max_retries, wait, e,
                )
                time.sleep(wait)

    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def _pct_diff(old: float, new: float) -> float:
    """Relative change as percentage. Zero-safe."""
    if old == 0:
        return 0.0 if new == 0 else float("inf")
    return abs(new - old) / abs(old) * 100.0


def detect_changes(
    ticker: str,
    new_records: List[Dict[str, Any]],
    existing_rows: List[Dict[str, Any]],
) -> List[ChangeRecord]:
    """
    Compare new records against existing DB rows and flag material diffs.

    Only fields whose relative change exceeds the configured threshold
    are returned — avoids log spam from trivial rounding differences.
    """
    existing_by_date: Dict[str, Dict[str, Any]] = {
        r["tanggal"]: r for r in existing_rows
    }

    changes: List[ChangeRecord] = []
    for new in new_records:
        date_str = new["tanggal"]
        old = existing_by_date.get(date_str)
        if old is None:
            continue  # new date, no comparison needed

        for f in PRICE_FIELDS:
            old_val, new_val = old.get(f), new.get(f)
            if old_val is None or new_val is None or old_val == new_val:
                continue
            diff = _pct_diff(float(old_val), float(new_val))
            if diff >= PRICE_CHANGE_WARN_PCT:
                changes.append(ChangeRecord(
                    ticker=ticker,
                    tanggal=date.fromisoformat(date_str),
                    field=f,
                    old_value=float(old_val),
                    new_value=float(new_val),
                    pct_diff=diff,
                ))

        old_vol, new_vol = old.get("volume"), new.get("volume")
        if old_vol is not None and new_vol is not None and old_vol != new_vol:
            diff = _pct_diff(float(old_vol), float(new_vol))
            if diff >= VOLUME_CHANGE_WARN_PCT:
                changes.append(ChangeRecord(
                    ticker=ticker,
                    tanggal=date.fromisoformat(date_str),
                    field="volume",
                    old_value=float(old_vol),
                    new_value=float(new_vol),
                    pct_diff=diff,
                ))

    return changes


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def _to_record(price: StockPrice) -> Dict[str, Any]:
    return {
        "kode_saham": price.kode_saham,
        "tanggal": price.tanggal.isoformat(),
        "harga_buka": price.harga_buka,
        "harga_tertinggi": price.harga_tertinggi,
        "harga_terendah": price.harga_terendah,
        "harga_tutup": price.harga_tutup,
        "volume": price.volume,
    }


def upsert_records(records: List[Dict[str, Any]]) -> int:
    """
    Upsert records into 'harga_saham' using UNIQUE(kode_saham, tanggal).

    Returns:
        Number of rows reported by Supabase as affected.
    """
    if not records:
        return 0
    supabase = get_supabase_client()
    response = (
        supabase.table("harga_saham")
        .upsert(records, on_conflict="kode_saham,tanggal")
        .execute()
    )
    return len(response.data) if response and response.data else 0


# ---------------------------------------------------------------------------
# Per-ticker pipeline
# ---------------------------------------------------------------------------

def backfill_ticker(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    dry_run: bool = False,
) -> TickerBackfillResult:
    """
    Fetch + diff + upsert for a single ticker. Never raises.
    """
    # 1. Fetch
    try:
        prices = fetch_ticker_with_retry(ticker, period=period)
    except Exception as e:
        logger.error("Failed to fetch %s after retries: %s", ticker, e)
        return TickerBackfillResult(ticker=ticker, status="error", error=str(e))

    if not prices:
        logger.warning("No data returned for %s (period=%s)", ticker, period)
        return TickerBackfillResult(ticker=ticker, status="empty")

    new_records = [_to_record(p) for p in prices]

    # 2. Fetch existing rows (for change detection)
    existing_rows: List[Dict[str, Any]] = []
    try:
        existing_rows = fetch_all(
            "harga_saham",
            select="kode_saham, tanggal, harga_buka, harga_tertinggi, "
                   "harga_terendah, harga_tutup, volume",
            filters={"kode_saham": ticker},
            order_by="id",
        )
    except Exception as e:
        logger.warning(
            "Could not fetch existing rows for %s: %s — change detection skipped",
            ticker, e,
        )

    existing_dates = {r["tanggal"] for r in existing_rows}
    new_dates = {r["tanggal"] for r in new_records}
    rows_new = len(new_dates - existing_dates)
    rows_overlap = len(new_dates & existing_dates)

    # 3. Detect and log changes
    changes = detect_changes(ticker, new_records, existing_rows)
    for c in changes:
        logger.warning(
            "Change %s %s %s: %s -> %s (%.2f%%)",
            c.ticker, c.tanggal, c.field,
            c.old_value, c.new_value, c.pct_diff,
        )

    # 4. Dry-run short-circuit
    if dry_run:
        logger.info(
            "[dry-run] %s: fetched=%d new=%d overlap=%d changes=%d",
            ticker, len(new_records), rows_new, rows_overlap, len(changes),
        )
        return TickerBackfillResult(
            ticker=ticker, status="ok",
            rows_fetched=len(new_records),
            rows_new=rows_new,
            rows_overlap=rows_overlap,
            changes=changes,
        )

    # 5. Upsert
    try:
        affected = upsert_records(new_records)
    except Exception as e:
        logger.error("Upsert failed for %s: %s", ticker, e)
        return TickerBackfillResult(
            ticker=ticker, status="error",
            rows_fetched=len(new_records), error=str(e),
        )

    logger.info(
        "%s backfilled: fetched=%d new=%d overlap=%d affected=%d",
        ticker, len(new_records), rows_new, rows_overlap, affected,
    )

    return TickerBackfillResult(
        ticker=ticker, status="ok",
        rows_fetched=len(new_records),
        rows_new=rows_new,
        rows_overlap=rows_overlap,
        changes=changes,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_backfill(
    tickers: Optional[List[str]] = None,
    period: str = DEFAULT_PERIOD,
    dry_run: bool = False,
    progress_every: int = 5,
) -> BackfillSummary:
    """
    Run backfill across the given tickers (sequential). Prints summary.
    """
    universe = list(tickers) if tickers else list(WATCH_TICKERS)
    summary = BackfillSummary(
        period=period, dry_run=dry_run, total_tickers=len(universe),
    )

    logger.info(
        "Starting backfill: %d tickers, period=%s, dry_run=%s",
        len(universe), period, dry_run,
    )

    for idx, ticker in enumerate(universe, start=1):
        result = backfill_ticker(ticker, period=period, dry_run=dry_run)
        summary.results.append(result)

        if idx % progress_every == 0 or idx == len(universe):
            logger.info(
                "Progress: %d/%d done (ok=%d empty=%d error=%d)",
                idx, len(universe),
                summary.ok_count, summary.empty_count, summary.error_count,
            )

    _log_summary(summary)
    return summary


def _log_summary(summary: BackfillSummary) -> None:
    """Print the final summary block."""
    logger.info("=" * 60)
    logger.info("BACKFILL SUMMARY")
    logger.info("=" * 60)
    logger.info("Period:           %s", summary.period)
    logger.info("Dry run:          %s", summary.dry_run)
    logger.info("Total tickers:    %d", summary.total_tickers)
    logger.info("  Succeeded:      %d", summary.ok_count)
    logger.info("  Empty:          %d", summary.empty_count)
    logger.info("  Errors:         %d", summary.error_count)
    logger.info("Total rows fetched:  %d", summary.total_fetched)
    logger.info("Total new rows:      %d", summary.total_new)
    logger.info("Total overlap rows:  %d", summary.total_overlap)
    logger.info("Total material changes: %d", summary.total_changes)

    if summary.empty_count or summary.error_count:
        logger.info("-" * 60)
        logger.info("Tickers needing attention:")
        for r in summary.results:
            if r.status in ("empty", "error"):
                msg = f"  {r.ticker:6} [{r.status}]"
                if r.error:
                    msg += f" — {r.error}"
                logger.info(msg)
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill OHLCV from yfinance into Supabase (TASK 102A Step 2)."
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker list. Default: WATCH_TICKERS.",
    )
    parser.add_argument(
        "--period", type=str, default=DEFAULT_PERIOD,
        help=f"yfinance period string (default: {DEFAULT_PERIOD}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and diff, but skip upsert.",
    )
    args = parser.parse_args(argv)

    setup_logging()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else None
    )

    summary = run_backfill(
        tickers=tickers, period=args.period, dry_run=args.dry_run,
    )
    return 0 if summary.error_count == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())