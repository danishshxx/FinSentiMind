from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging


logger = get_logger(__name__)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = _PROJECT_ROOT / "ml" / "data" / "prices_clean.parquet"
DEFAULT_OUTPUT = _PROJECT_ROOT / "ml" / "data" / "trading_calendar.parquet"

DEFAULT_THRESHOLD = 0.9

CALENDAR_COLUMNS = [
    "date",
    "ticker_count",
    "available_count",
    "coverage_ratio",
    "is_trading_day",
]


# ---------------------------------------------------------------------------
# Core derivation
# ---------------------------------------------------------------------------

def derive_trading_calendar(
    prices_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
) -> pd.DataFrame:
    """
    Derive trading calendar from cross-sectional price availability.

    Args:
        prices_df: DataFrame with 'ticker' and 'trading_date' columns.
        threshold: Minimum coverage ratio for a date to be trading day.
            Default 0.9 (90% of universe).

    Returns:
        DataFrame with columns CALENDAR_COLUMNS, one row per calendar day
        in the [min, max] range of prices_df. Non-trading days (weekends,
        holidays, missing data) appear with coverage_ratio=0 (or low) and
        is_trading_day=False.

    Raises:
        ValueError: If prices_df is empty or missing required columns.
        ValueError: If threshold not in (0, 1].
    """
    if prices_df.empty:
        raise ValueError("prices_df is empty — cannot derive calendar.")

    required = {"ticker", "trading_date"}
    missing = required - set(prices_df.columns)
    if missing:
        raise ValueError(f"prices_df missing required columns: {missing}")

    if not (0 < threshold <= 1):
        raise ValueError(f"threshold must be in (0, 1], got {threshold}")

    universe_size = int(prices_df["ticker"].nunique())
    if universe_size == 0:
        raise ValueError("prices_df has no tickers.")

    # Normalize to plain dates
    df = prices_df[["ticker", "trading_date"]].copy()
    df["date"] = pd.to_datetime(df["trading_date"]).dt.date

    # Count unique tickers per date
    coverage = (
        df.groupby("date")["ticker"]
        .nunique()
        .reset_index()
        .rename(columns={"ticker": "available_count"})
    )

    min_date: date = df["date"].min()
    max_date: date = df["date"].max()

    full_range = pd.date_range(min_date, max_date, freq="D").date
    calendar = pd.DataFrame({"date": full_range})

    calendar = calendar.merge(coverage, on="date", how="left")
    calendar["available_count"] = (
        calendar["available_count"].fillna(0).astype("int64")
    )
    calendar["ticker_count"] = universe_size
    calendar["coverage_ratio"] = (
        calendar["available_count"] / universe_size
    ).astype("float64")
    calendar["is_trading_day"] = calendar["coverage_ratio"] >= threshold

    calendar = calendar[CALENDAR_COLUMNS].sort_values("date").reset_index(drop=True)

    logger.info(
        "Derived calendar: %d days total, %d trading, %d non-trading "
        "(universe=%d, threshold=%.2f)",
        len(calendar),
        int(calendar["is_trading_day"].sum()),
        int((~calendar["is_trading_day"]).sum()),
        universe_size,
        threshold,
    )
    return calendar


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_prices(path: Path = DEFAULT_INPUT) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Prices parquet not found: {path}. "
            f"Run `python -m ml.preprocessing.load_prices` first."
        )
    return pd.read_parquet(path)


def save_calendar(calendar: pd.DataFrame, path: Path = DEFAULT_OUTPUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    calendar.to_parquet(path, index=False)
    logger.info("Calendar saved to %s (%d rows)", path, len(calendar))


def load_calendar(path: Path = DEFAULT_OUTPUT) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Trading calendar not found: {path}. "
            f"Run `python -m ml.preprocessing.trading_calendar` first."
        )
    return pd.read_parquet(path)


def get_trading_dates(path: Path = DEFAULT_OUTPUT) -> set[date]:
    """
    Return set of trading dates from saved calendar. Convenience for
    time_alignment callers needing O(1) lookup.
    """
    cal = load_calendar(path)
    return set(cal.loc[cal["is_trading_day"], "date"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive trading calendar from price availability (TASK 103)."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)

    setup_logging()

    prices = load_prices(args.input)
    logger.info("Loaded prices: %d rows, %d tickers",
                len(prices), prices["ticker"].nunique())

    calendar = derive_trading_calendar(prices, threshold=args.threshold)
    save_calendar(calendar, args.output)

    # Summary
    trading = int(calendar["is_trading_day"].sum())
    nontrading = len(calendar) - trading
    print(f"\nCalendar summary")
    print(f"  Range:          {calendar['date'].min()} -> {calendar['date'].max()}")
    print(f"  Total days:     {len(calendar)}")
    print(f"  Trading days:   {trading}")
    print(f"  Non-trading:    {nontrading}")
    print(f"  Universe size:  {calendar['ticker_count'].iloc[0]}")
    print(f"  Threshold:      {args.threshold}")
    print(f"  Output:         {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())