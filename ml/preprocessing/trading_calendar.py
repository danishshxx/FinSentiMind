from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import resolve_active_dataset_dir
from ml.preprocessing.idx_calendar import (
    OFFICIAL_CLOSURE,
    UNVERIFIED,
    VERIFIED_OPEN,
    WEEKEND,
    CalendarSource,
    calendar_reference,
    exchange_day_status,
    load_calendar_source,
)


logger = get_logger(__name__)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data") / "prices_clean.parquet"
DEFAULT_OUTPUT = _PROJECT_ROOT / "ml" / "data" / "trading_calendar.parquet"

DEFAULT_THRESHOLD = 0.9

CALENDAR_COLUMNS = [
    "date",
    "ticker_count",
    "available_count",
    "coverage_ratio",
    "coverage_threshold_met",
    "is_exchange_open",
    "day_status",
    "closure_type",
    "closure_name",
    "calendar_version",
    "is_trading_day",
]


# ---------------------------------------------------------------------------
# Core derivation
# ---------------------------------------------------------------------------

def derive_trading_calendar(
    prices_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    calendar_source: Optional[CalendarSource] = None,
) -> pd.DataFrame:
    """Derive the exchange calendar from the authoritative IDX source.

    Exchange status comes from the versioned official closure map. Cross-
    sectional coverage only measures missing provider observations on verified
    open sessions; it is never used as the sole definition of an open exchange.

    Args:
        prices_df: DataFrame with 'ticker' and 'trading_date' columns.
        threshold: Coverage ratio threshold retained as a descriptive flag.
        calendar_source: Optional pre-loaded authoritative calendar source.

    Returns:
        DataFrame with CALENDAR_COLUMNS, one row per calendar day in the
        observed [min, max] range.
    """
    if prices_df.empty:
        raise ValueError("prices_df is empty - cannot derive calendar.")

    required = {"ticker", "trading_date"}
    missing = required - set(prices_df.columns)
    if missing:
        raise ValueError(f"prices_df missing required columns: {missing}")

    if not (0 < threshold <= 1):
        raise ValueError(f"threshold must be in (0, 1], got {threshold}")

    universe_size = int(prices_df["ticker"].nunique())
    if universe_size == 0:
        raise ValueError("prices_df has no tickers.")

    source = calendar_source or load_calendar_source()

    df = prices_df[["ticker", "trading_date"]].copy()
    df["date"] = pd.to_datetime(df["trading_date"]).dt.date

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

    references = [calendar_reference(day, source) for day in calendar["date"]]
    calendar["day_status"] = [exchange_day_status(day, source) for day in calendar["date"]]
    calendar["is_exchange_open"] = calendar["day_status"] == VERIFIED_OPEN
    calendar["closure_type"] = [item["closure_type"] for item in references]
    calendar["closure_name"] = [item["closure_name"] for item in references]
    calendar["calendar_version"] = source.calendar_version

    calendar = calendar.merge(coverage, on="date", how="left")
    calendar["available_count"] = (
        calendar["available_count"].fillna(0).astype("int64")
    )
    calendar["ticker_count"] = universe_size
    calendar["coverage_ratio"] = (
        calendar["available_count"] / universe_size
    ).astype("float64")
    calendar["coverage_threshold_met"] = calendar["coverage_ratio"] >= threshold
    # A session is usable only when the exchange was verifiably open AND at
    # least one provider observation exists. Missing observations are visible
    # via day_status/available_count and must be investigated, not closed.
    calendar["is_trading_day"] = (
        calendar["is_exchange_open"] & (calendar["available_count"] > 0)
    )

    calendar = calendar[CALENDAR_COLUMNS].sort_values("date").reset_index(drop=True)

    closure_count = int((calendar["day_status"] == OFFICIAL_CLOSURE).sum())
    weekend_count = int((calendar["day_status"] == WEEKEND).sum())
    unverified_count = int((calendar["day_status"] == UNVERIFIED).sum())
    missing_open = int(
        (calendar["is_exchange_open"] & (calendar["available_count"] == 0)).sum()
    )

    logger.info(
        "Derived calendar: %d days, %d usable sessions, %d closures, "
        "%d weekends, %d unverified, %d verified-open days with no observations "
        "(universe=%d, calendar=%s).",
        len(calendar),
        int(calendar["is_trading_day"].sum()),
        closure_count,
        weekend_count,
        unverified_count,
        missing_open,
        universe_size,
        source.calendar_version,
    )
    if unverified_count:
        logger.warning(
            "%d date(s) are outside the authoritative calendar coverage and "
            "were not assumed open.",
            unverified_count,
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


def get_trading_dates(path: Optional[Path] = None) -> set[date]:
    """
    Return set of usable trading dates from saved calendar.

    A usable date is a verified-open exchange session with at least one price
    observation. Official closures and verified-open days with missing
    observations are excluded.
    """
    if path is None:
        path = resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data") / "trading_calendar.parquet"
    cal = load_calendar(path)
    return set(cal.loc[cal["is_trading_day"], "date"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive calendar from the authoritative IDX source (TASK 103)."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--calendar-source", type=Path, default=None)
    args = parser.parse_args(argv)

    setup_logging()

    source = load_calendar_source(args.calendar_source) if args.calendar_source else None
    prices = load_prices(args.input)
    logger.info(
        "Loaded prices: %d rows, %d tickers",
        len(prices), prices["ticker"].nunique(),
    )

    calendar = derive_trading_calendar(
        prices, threshold=args.threshold, calendar_source=source
    )
    save_calendar(calendar, args.output)

    trading = int(calendar["is_trading_day"].sum())
    nontrading = len(calendar) - trading
    print(f"\nCalendar summary")
    print(f"  Calendar version: {calendar['calendar_version'].iloc[0]}")
    print(f"  Range:            {calendar['date'].min()} -> {calendar['date'].max()}")
    print(f"  Total days:       {len(calendar)}")
    print(f"  Usable sessions:  {trading}")
    print(f"  Non-trading:      {nontrading}")
    print(f"  Official closes:  {int((calendar['day_status'] == OFFICIAL_CLOSURE).sum())}")
    print(f"  Unverified:       {int((calendar['day_status'] == UNVERIFIED).sum())}")
    print(f"  Universe size:    {calendar['ticker_count'].iloc[0]}")
    print(f"  Coverage flag:    {args.threshold}")
    print(f"  Output:           {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
