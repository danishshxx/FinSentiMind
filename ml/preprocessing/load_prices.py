from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all
from ml.preprocessing.dataset_store import write_json_atomic
from ml.preprocessing.idx_calendar import (
    OFFICIAL_CLOSURE,
    CalendarSource,
    calendar_reference,
    exchange_day_status,
    load_calendar_source,
)


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Database -> ML column rename. Order matters for output column order.
COLUMN_RENAME = {
    "kode_saham": "ticker",
    "tanggal": "trading_date",
    "harga_buka": "open",
    "harga_tertinggi": "high",
    "harga_terendah": "low",
    "harga_tutup": "close",
    "volume": "volume",
}

REQUIRED_DB_COLUMNS = list(COLUMN_RENAME.keys())

OUTPUT_COLUMNS = [
    "ticker",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "return_1d",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "ml" / "data" / "prices_clean.parquet"


class ExchangeClosureContradictionError(ValueError):
    """Raised when an official exchange closure contains contradictory bars."""


def filter_exchange_closures(
    df: pd.DataFrame,
    calendar_source: Optional[CalendarSource] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Remove official-closure bars and return a full quarantine audit.

    Zero-volume flat bars on verified exchange closures are excluded from the
    canonical research dataset. A closure bar with positive volume, or a bar
    whose OHLC is not flat, is a blocking contradiction and is never silently
    deleted.
    """
    source = calendar_source or load_calendar_source()
    if df.empty:
        return df.copy(), df.iloc[0:0].copy()

    dates = pd.to_datetime(df["trading_date"], errors="raise").dt.date
    closures = [
        exchange_day_status(day, source) == OFFICIAL_CLOSURE for day in dates
    ]
    mask = pd.Series(closures, index=df.index, dtype=bool)
    audit = df[mask].copy()

    if not audit.empty:
        references = [calendar_reference(day, source) for day in dates[mask]]
        audit["reason"] = "OFFICIAL_EXCHANGE_CLOSURE"
        audit["date"] = dates[mask].astype(str)
        audit["calendar_version"] = source.calendar_version
        audit["source_id"] = [item["source_id"] for item in references]
        audit["source_url"] = [item["source_url"] for item in references]
        audit["announcement"] = [item["announcement"] for item in references]
        audit["closure_type"] = [item["closure_type"] for item in references]
        audit["closure_name"] = [item["closure_name"] for item in references]
        audit["source_system"] = "supabase.harga_saham"

        flat_ohlc = (
            (audit["open"] == audit["high"])
            & (audit["high"] == audit["low"])
            & (audit["low"] == audit["close"])
        )
        contradiction = (audit["volume"] > 0) | (~flat_ohlc)
        if contradiction.any():
            sample = audit.loc[
                contradiction,
                [
                    "ticker", "trading_date", "open", "high", "low",
                    "close", "volume",
                ],
            ].head(20)
            raise ExchangeClosureContradictionError(
                "Official exchange closure contains positive-volume or non-flat "
                "OHLC bars; refusing to delete silently. Sample: "
                f"{sample.to_dict(orient='records')}"
            )
    return df[~mask].copy(), audit.reset_index(drop=True)


def render_closure_audit(audit: pd.DataFrame, calendar_version: str) -> str:
    lines = [
        "# Exchange Closure Quarantine",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Calendar version:** {calendar_version}",
        f"**Rows excluded:** {len(audit)}",
        "",
        "| Ticker | Date | Open | High | Low | Close | Volume | Closure | Source |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for _, row in audit.iterrows():
        lines.append(
            f"| {row['ticker']} | {row['trading_date']} | {row['open']} | "
            f"{row['high']} | {row['low']} | {row['close']} | {row['volume']} | "
            f"{row['closure_name']} | {row['source_id']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_closure_audit(
    audit: pd.DataFrame, path: Path, calendar_version: str
) -> None:
    if path.suffix.lower() == ".md":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_closure_audit(audit, calendar_version), encoding="utf-8"
        )
        return
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calendar_version": calendar_version,
        "rows_excluded": int(len(audit)),
        "records": json.loads(audit.to_json(orient="records", date_format="iso")),
    }
    write_json_atomic(path, payload)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_raw_prices(tickers: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Fetch raw OHLCV rows from Supabase.

    Uses explicit column projection (not '*') so internal columns like
    'id' / 'created_at' never leak into the DataFrame.

    Args:
        tickers: Optional ticker subset. None = all rows in table.

    Returns:
        DataFrame with columns matching REQUIRED_DB_COLUMNS.
        Empty DataFrame (with correct columns) if no rows.
    """
    logger.info("Loading raw prices from Supabase (tickers=%s)...",
                tickers or "ALL")

    select_cols = ", ".join(REQUIRED_DB_COLUMNS)
    filters = {"kode_saham": tickers} if tickers else None

    rows = fetch_all(
        table="harga_saham",
        select=select_cols,
        filters=filters,
        order_by="id",
    )

    if not rows:
        logger.warning("No rows returned from harga_saham.")
        return pd.DataFrame(columns=REQUIRED_DB_COLUMNS)

    df = pd.DataFrame(rows)
    logger.info("Loaded %d raw rows.", len(df))
    return df


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate required DB columns and rename to ML convention.

    Raises:
        ValueError: if any required column is missing.

    Unknown extra columns are preserved (not dropped silently).
    """
    missing = [c for c in REQUIRED_DB_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns in source data: {missing}. "
            f"Present columns: {list(df.columns)}"
        )

    extra = [c for c in df.columns if c not in COLUMN_RENAME]
    if extra:
        logger.warning(
            "Unknown columns present (preserved but not renamed): %s", extra
        )

    return df.rename(columns=COLUMN_RENAME)


def cast_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize dtypes:
        ticker       -> string
        trading_date -> datetime64[ns]
        open/high/low/close -> float64
        volume       -> int64
        return_1d    -> float64 (if present)
    """
    df = df.copy()

    df["ticker"] = df["ticker"].astype("string")
    df["trading_date"] = pd.to_datetime(df["trading_date"], errors="raise")

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="raise").astype("float64")

    df["volume"] = pd.to_numeric(df["volume"], errors="raise").astype("int64")

    if "return_1d" in df.columns:
        df["return_1d"] = df["return_1d"].astype("float64")

    return df


def sort_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Deterministic sort by (ticker, trading_date) ascending. Index reset."""
    return (
        df.sort_values(["ticker", "trading_date"], kind="mergesort")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Return calculation
# ---------------------------------------------------------------------------

def compute_return_1d(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add `return_1d` = close_t / close_{t-1} - 1, computed PER TICKER.

    First row per ticker -> NaN (no previous close).
    Never forward-filled, never replaced with zero.

    Rationale:
        - NaN is semantically correct: no prior close means no return.
        - Replacing with 0 would falsely imply "flat day".
        - Forward-filling would introduce fabricated persistence.

    Assumes df is already sorted by (ticker, trading_date).
    """
    df = df.copy()
    df["return_1d"] = (
        df.groupby("ticker", sort=False)["close"]
        .pct_change(fill_method=None)
    )
    return df


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_clean_dataset(
    df_raw: pd.DataFrame,
    calendar_source: Optional[CalendarSource] = None,
    quarantine_report_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Compose: normalize -> cast -> closure quarantine -> sort -> return.
    Returns the cleaned DataFrame with stable column order.

    Official-closure bars are removed before returns are computed so rolling
    features never see holiday bars. Quarantine metadata is attached to
    ``df.attrs``.
    """
    if df_raw.empty:
        logger.info("Empty input — returning empty cleaned dataset with schema.")
        empty = pd.DataFrame({
            "ticker": pd.Series(dtype="string"),
            "trading_date": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="int64"),
            "return_1d": pd.Series(dtype="float64"),
        })
        return empty

    df = normalize_columns(df_raw)
    df = cast_types(df)
    source = calendar_source or load_calendar_source()
    df, audit = filter_exchange_closures(df, calendar_source=source)
    if not audit.empty:
        logger.warning(
            "Excluded %d official-closure price row(s) using calendar %s.",
            len(audit),
            source.calendar_version,
        )
        if quarantine_report_path is not None:
            write_closure_audit(audit, quarantine_report_path, source.calendar_version)
    df = sort_prices(df)
    df = compute_return_1d(df)
    df = df[OUTPUT_COLUMNS].reset_index(drop=True)
    df.attrs["closure_audit"] = json.loads(
        audit.to_json(orient="records", date_format="iso")
    )
    df.attrs["closure_excluded_rows"] = int(len(audit))
    df.attrs["calendar_version"] = source.calendar_version
    return df


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_parquet(df: pd.DataFrame, path: Path) -> None:
    """Save DataFrame as Parquet. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("Saved %d rows to %s", len(df), path)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def load_prices_clean(
    tickers: Optional[List[str]] = None,
    save: bool = True,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    calendar_source: Optional[CalendarSource] = None,
    quarantine_report_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    End-to-end: fetch + clean + closure quarantine + (optional) save.
    """
    df_raw = load_raw_prices(tickers=tickers)
    df = build_clean_dataset(
        df_raw,
        calendar_source=calendar_source,
        quarantine_report_path=quarantine_report_path,
    )

    if save:
        save_parquet(df, output_path)

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build clean price dataset (TASK 102B)."
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated tickers. Default: all rows.",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH,
        help=f"Output Parquet path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Build and print summary but do not write Parquet.",
    )
    parser.add_argument(
        "--quarantine-report", type=Path, default=None,
        help="Optional JSON/Markdown report for excluded closure bars.",
    )
    args = parser.parse_args(argv)

    setup_logging()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else None
    )

    df = load_prices_clean(
        tickers=tickers,
        save=not args.no_save,
        output_path=args.output,
        quarantine_report_path=args.quarantine_report,
    )

    if not df.empty:
        logger.info(
            "Clean dataset ready: %d rows, %d tickers, %d columns.",
            len(df), df["ticker"].nunique(), len(df.columns),
        )

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())