from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all


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

def build_clean_dataset(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Compose: normalize -> cast -> sort -> return.
    Returns the cleaned DataFrame with stable column order.
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
    df = sort_prices(df)
    df = compute_return_1d(df)
    df = df[OUTPUT_COLUMNS].reset_index(drop=True)

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
) -> pd.DataFrame:
    """
    End-to-end: fetch + clean + (optional) save. Returns cleaned DataFrame.
    """
    df_raw = load_raw_prices(tickers=tickers)
    df = build_clean_dataset(df_raw)

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