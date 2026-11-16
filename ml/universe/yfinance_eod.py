"""Incremental yfinance EOD ingestion for IDX instruments.

Design constraints:
- Local staging only; no Supabase writes.
- Raw OHLCV and adjusted close are stored in separate columns. Adjusted prices
  are never mixed into the unadjusted OHLC columns.
- Dividend and split actions are retained alongside the price rows.
- Re-running the same window is idempotent: duplicates are dropped on
  ``(ticker, trading_date)`` keeping the latest retrieval.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import write_json_atomic

logger = get_logger(__name__)

DEFAULT_HISTORY_START = date(2024, 10, 7)
DEFAULT_DATA_DIR = Path("ml/data/universe")
RAW_FILENAME = "yfinance_eod_raw.parquet"
NORMALIZED_FILENAME = "prices_universe.parquet"
MISSING_REPORT = "ingestion_report.json"

NORMALIZED_COLUMNS = [
    "ticker",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "stock_splits",
    "source",
    "retrieved_at",
    "price_basis",
]


@dataclass
class IngestConfig:
    history_start: date = DEFAULT_HISTORY_START
    end: Optional[date] = None
    batch_size: int = 20
    rate_limit_seconds: float = 1.0
    retries: int = 3
    backoff_seconds: float = 2.0
    provider: str = "yfinance"
    incremental: bool = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_symbol(ticker: str) -> str:
    raw = ticker.strip().upper()
    return raw if raw.endswith(".JK") else f"{raw}.JK"


def _default_fetcher(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fetch one symbol. Returns a long frame of unadjusted OHLCV plus actions."""
    import yfinance as yf

    frame = yf.download(
        symbol,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        actions=True,
        progress=False,
        threads=False,
    )
    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.reset_index()
    rename = {
        "Date": "trading_date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
        "Dividends": "dividends",
        "Stock Splits": "stock_splits",
    }
    frame = frame.rename(columns=rename)
    frame["ticker"] = symbol.removesuffix(".JK")
    return frame


def _coerce_frame(frame: pd.DataFrame, ticker: str, provider: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=NORMALIZED_COLUMNS)
    data = frame.copy()
    if "ticker" not in data.columns:
        data["ticker"] = ticker.upper()
    data["ticker"] = data["ticker"].astype(str).str.upper().str.removesuffix(".JK")
    data["trading_date"] = pd.to_datetime(data["trading_date"], errors="coerce")
    data = data.dropna(subset=["trading_date"])
    for column in ("open", "high", "low", "close", "adj_close", "volume"):
        if column not in data.columns:
            data[column] = pd.NA
        data[column] = pd.to_numeric(data[column], errors="coerce")
    for column in ("dividends", "stock_splits"):
        if column not in data.columns:
            data[column] = 0.0
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0.0)
    data["source"] = data.get("source", provider)
    data["retrieved_at"] = data.get("retrieved_at", _utc_now())
    data["price_basis"] = "unadjusted"
    return data[NORMALIZED_COLUMNS]


def _fetch_with_retry(
    ticker: str,
    start: date,
    end: date,
    config: IngestConfig,
    fetcher: Callable[[str, date, date], pd.DataFrame],
    sleep_fn: Callable[[float], None],
    failures: Dict[str, str],
) -> pd.DataFrame:
    symbol = _normalize_symbol(ticker)
    last_error = None
    for attempt in range(1, config.retries + 1):
        try:
            frame = fetcher(symbol, start, end)
            if frame is not None and not frame.empty:
                return _coerce_frame(frame, ticker, config.provider)
            last_error = "empty_response"
        except Exception as exc:  # provider errors are reported, never hidden
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < config.retries:
            sleep_fn(config.backoff_seconds * (2 ** (attempt - 1)))
    failures[ticker] = last_error or "unknown_provider_error"
    return pd.DataFrame(columns=NORMALIZED_COLUMNS)


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write parquet via a temp file + replace so a failed run cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
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


def _load_existing(path: Path, columns: List[str]) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=columns)
    return pd.read_parquet(path)


def _last_dates(frame: pd.DataFrame) -> Dict[str, date]:
    if frame.empty:
        return {}
    work = frame.copy()
    work["trading_date"] = pd.to_datetime(work["trading_date"], errors="coerce")
    result = {}
    for ticker, group in work.dropna(subset=["trading_date"]).groupby("ticker"):
        result[str(ticker).upper()] = group["trading_date"].max().date()
    return result


def ingest_universe(
    tickers: Iterable[str],
    data_dir: Path | str = DEFAULT_DATA_DIR,
    config: Optional[IngestConfig] = None,
    *,
    fetcher: Optional[Callable[[str, date, date], pd.DataFrame]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Fetch EOD data for ``tickers`` incrementally and write local layers."""
    config = config or IngestConfig()
    fetcher = fetcher or _default_fetcher
    sleep_fn = sleep_fn or time.sleep
    data_dir = Path(data_dir)
    raw_path = data_dir / "raw" / RAW_FILENAME
    normalized_path = data_dir / "normalized" / NORMALIZED_FILENAME
    report_path = data_dir / MISSING_REPORT

    end = config.end or date.today()
    wanted = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    existing_raw = _load_existing(raw_path, NORMALIZED_COLUMNS)
    existing_normalized = _load_existing(normalized_path, NORMALIZED_COLUMNS)
    last_dates = _last_dates(existing_normalized)

    fetched_frames: List[pd.DataFrame] = []
    request_plan: Dict[str, Optional[str]] = {}
    failures: Dict[str, str] = {}
    skipped: List[str] = []

    batches = [
        wanted[index:index + config.batch_size]
        for index in range(0, len(wanted), config.batch_size)
    ]
    for batch_index, batch in enumerate(batches):
        for ticker in batch:
            last = last_dates.get(ticker)
            if config.incremental and last is not None:
                start = last + timedelta(days=1)
                if start > end:
                    skipped.append(ticker)
                    request_plan[ticker] = None
                    continue
            else:
                start = config.history_start
            request_plan[ticker] = f"{start.isoformat()}..{end.isoformat()}"
            frame = _fetch_with_retry(
                ticker, start, end, config, fetcher, sleep_fn, failures
            )
            if not frame.empty:
                fetched_frames.append(frame)
        if batch_index < len(batches) - 1 and config.rate_limit_seconds:
            sleep_fn(config.rate_limit_seconds)

    new_rows = (
        pd.concat(fetched_frames, ignore_index=True)
        if fetched_frames else pd.DataFrame(columns=NORMALIZED_COLUMNS)
    )
    combined_raw = pd.concat(
        [existing_raw, new_rows], ignore_index=True
    ).drop_duplicates(subset=["ticker", "trading_date"], keep="last")
    combined_normalized = pd.concat(
        [existing_normalized, new_rows], ignore_index=True
    ).drop_duplicates(subset=["ticker", "trading_date"], keep="last")
    for frame in (combined_raw, combined_normalized):
        if not frame.empty:
            frame.sort_values(["ticker", "trading_date"], inplace=True)
            frame.reset_index(drop=True, inplace=True)

    _write_parquet_atomic(combined_raw, raw_path)
    _write_parquet_atomic(combined_normalized, normalized_path)

    returned = (
        sorted({str(t) for t in new_rows["ticker"].unique()})
        if not new_rows.empty else []
    )
    failed = sorted(failures)
    missing = sorted(set(wanted) - set(returned) - set(failed) - set(skipped))

    report = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "provider": config.provider,
        "incremental": config.incremental,
        "history_start": str(config.history_start),
        "end": str(end),
        "requested": wanted,
        "request_plan": request_plan,
        "returned": returned,
        "failed": failed,
        "failures": failures,
        "missing": missing,
        "skipped_up_to_date": sorted(skipped),
        "new_rows": int(len(new_rows)),
        "total_rows": int(len(combined_normalized)),
        "tickers_with_history": int(
            combined_normalized["ticker"].nunique()
        ) if not combined_normalized.empty else 0,
        "raw_path": str(raw_path),
        "normalized_path": str(normalized_path),
        "adjusted_price_note": (
            "OHLC columns are unadjusted. Adjusted close is stored separately "
            "in adj_close; the two bases are never mixed."
        ),
    }
    write_json_atomic(report_path, report)
    report["report_path"] = str(report_path)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    from ml.universe.inventory import build_inventory

    parser = argparse.ArgumentParser(description="Incremental yfinance EOD ingestion.")
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--start", default=str(DEFAULT_HISTORY_START))
    parser.add_argument("--end", default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--full", action="store_true", help="Disable incremental mode.")
    args = parser.parse_args(argv)

    setup_logging()
    tickers = args.tickers
    if not tickers:
        inventory = build_inventory()
        tickers = [item["ticker"] for item in inventory["instruments"]]
    config = IngestConfig(
        history_start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end) if args.end else None,
        batch_size=args.batch_size,
        rate_limit_seconds=args.rate_limit,
        retries=args.retries,
        incremental=not args.full,
    )
    report = ingest_universe(tickers, args.data_dir, config)
    print(json.dumps({
        "requested": len(report["requested"]),
        "returned": len(report["returned"]),
        "failed": report["failed"],
        "missing": report["missing"],
        "skipped_up_to_date": len(report["skipped_up_to_date"]),
        "new_rows": report["new_rows"],
        "total_rows": report["total_rows"],
        "report": report["report_path"],
    }, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
