from __future__ import annotations

import argparse
from datetime import time
from pathlib import Path
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all
from app.services.time_alignment import map_to_trading_date
from ml.preprocessing.trading_calendar import get_trading_dates


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWS_SELECT_COLUMNS = [
    "ticker",
    "sentiment_label",
    "sentiment_score",
    "published_at",
    "scraped_at",
]

OUTPUT_COLUMNS = [
    "ticker",
    "trading_date",
    "news_count",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = _PROJECT_ROOT / "ml" / "data" / "daily_sentiment.parquet"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_news(tickers: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Fetch raw news from Supabase `berita_saham`.

    Filter di Python: ticker IS NOT NULL AND sentiment_label IS NOT NULL AND
    sentiment_score IS NOT NULL.

    Args:
        tickers: Optional ticker subset (list).

    Returns:
        DataFrame with NEWS_SELECT_COLUMNS, filtered.
        Empty DataFrame (with columns) if no rows.
    """
    logger.info("Loading news from Supabase (tickers=%s)...", tickers or "ALL")

    filters = {"ticker": tickers} if tickers else None
    rows = fetch_all(
        table="berita_saham",
        select=", ".join(NEWS_SELECT_COLUMNS),
        filters=filters,
        order_by="id",
    )

    if not rows:
        logger.warning("No news rows returned from berita_saham.")
        return pd.DataFrame(columns=NEWS_SELECT_COLUMNS)

    df = pd.DataFrame(rows)

    # Ensure all expected columns exist (defensive)
    for col in NEWS_SELECT_COLUMNS:
        if col not in df.columns:
            df[col] = None

    before = len(df)
    df = df.dropna(subset=["ticker", "sentiment_label", "sentiment_score"])
    df = df[df["ticker"].astype(str).str.strip() != ""]
    after = len(df)

    if before != after:
        logger.info("Filtered %d -> %d rows (NULL ticker/label/score).", before, after)

    logger.info("Loaded %d usable news rows.", len(df))
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Timestamp policy
# ---------------------------------------------------------------------------

def calculate_effective_available_at(
    df: pd.DataFrame, tz: ZoneInfo
) -> pd.DataFrame:
    """
    Add `effective_available_at` = max(published_at, scraped_at), in market tz.

    Rationale:
        A system cannot use information before it actually observed it.
        Taking the max is conservative — it never lets the model "see"
        an article earlier than it was realistically available.

    Args:
        df: DataFrame with `published_at`, `scraped_at` (ISO strings or datetimes).
        tz: Market timezone (e.g., Asia/Jakarta).

    Returns:
        DataFrame copy with tz-aware `published_at`, `scraped_at`, and
        new column `effective_available_at` (tz-aware, in `tz`).
    """
    df = df.copy()

    df["published_at"] = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True, errors="coerce")

    # Drop rows where either is unparseable
    df = df.dropna(subset=["published_at", "scraped_at"]).copy()

    # max() between two tz-aware columns (both in UTC here)
    df["effective_available_at"] = df["published_at"].where(
        df["published_at"] >= df["scraped_at"], df["scraped_at"]
    )

    # Convert to market tz (idempotent if already there)
    df["published_at"] = df["published_at"].dt.tz_convert(tz)
    df["scraped_at"] = df["scraped_at"].dt.tz_convert(tz)
    df["effective_available_at"] = df["effective_available_at"].dt.tz_convert(tz)

    return df


def classify_availability_quality(
    df: pd.DataFrame, threshold_days: int
) -> pd.DataFrame:
    """
    Add `availability_quality` column: 'live' or 'reconstructed'.

    Rule:
        delta_days = (scraped_at - published_at) in days
        if delta_days <= threshold_days: 'live'
        else: 'reconstructed'

    Negative delta (scraped before published — likely source bug) is
    clamped to live classification.

    Args:
        df: DataFrame with tz-aware `published_at` and `scraped_at`.
        threshold_days: Max gap (days) to still call it 'live'.

    Returns:
        DataFrame copy with new column `availability_quality`.
    """
    df = df.copy()

    delta_seconds = (df["scraped_at"] - df["published_at"]).dt.total_seconds()
    delta_days = delta_seconds / 86400.0

    df["availability_quality"] = "reconstructed"
    df.loc[delta_days <= threshold_days, "availability_quality"] = "live"

    return df


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def align_news_to_trading_date(
    df: pd.DataFrame,
    trading_dates: Iterable,
    cutoff: time,
    tz: ZoneInfo,
) -> pd.DataFrame:
    """
    Add `trading_date` column via TASK 103 map_to_trading_date().

    Args:
        df: DataFrame with tz-aware `effective_available_at`.
        trading_dates: Set of valid trading dates.
        cutoff: Signal cutoff (e.g., time(16, 0)).
        tz: Market timezone.

    Returns:
        DataFrame copy with new column `trading_date`.
    """
    df = df.copy()
    trading_dates_set = set(trading_dates)

    df["trading_date"] = [
        map_to_trading_date(ts, trading_dates_set, cutoff, tz)
        for ts in df["effective_available_at"]
    ]
    return df


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_daily_sentiment(df: pd.DataFrame) -> pd.DataFrame:
    """
    Group by (ticker, trading_date) and compute daily sentiment metrics.

    Sparse output: only ticker-date pairs that actually have articles are
    represented. No filler rows for "no-news days".

    Args:
        df: DataFrame with columns:
            ticker, trading_date, sentiment_label, sentiment_score,
            availability_quality.

    Returns:
        Aggregated DataFrame with OUTPUT_COLUMNS, sorted by (ticker, trading_date).
        Empty DataFrame with OUTPUT_COLUMNS if input empty.
    """
    if df.empty:
        return _empty_output()

    df = df.copy()

    # Boolean masks for ratio computation
    df["_is_positive"] = (df["sentiment_label"].str.upper() == "POSITIVE").astype(int)
    df["_is_negative"] = (df["sentiment_label"].str.upper() == "NEGATIVE").astype(int)
    df["_is_live"] = (df["availability_quality"] == "live").astype(int)

    grouped = (
        df.groupby(["ticker", "trading_date"], sort=False)
        .agg(
            news_count=("sentiment_label", "size"),
            sentiment_mean=("sentiment_score", "mean"),
            sentiment_std=("sentiment_score", "std"),
            _pos=("_is_positive", "sum"),
            _neg=("_is_negative", "sum"),
            _live=("_is_live", "sum"),
        )
        .reset_index()
    )

    grouped["positive_ratio"] = grouped["_pos"] / grouped["news_count"]
    grouped["negative_ratio"] = grouped["_neg"] / grouped["news_count"]
    grouped["availability_live_ratio"] = grouped["_live"] / grouped["news_count"]

    grouped = grouped[OUTPUT_COLUMNS]
    grouped = (
        grouped.sort_values(["ticker", "trading_date"], kind="mergesort")
        .reset_index(drop=True)
    )

    return grouped


def _empty_output() -> pd.DataFrame:
    """Return empty DataFrame with the correct output schema & dtypes."""
    return pd.DataFrame({
        "ticker": pd.Series(dtype="string"),
        "trading_date": pd.Series(dtype="object"),  # python date
        "news_count": pd.Series(dtype="int64"),
        "sentiment_mean": pd.Series(dtype="float64"),
        "sentiment_std": pd.Series(dtype="float64"),
        "positive_ratio": pd.Series(dtype="float64"),
        "negative_ratio": pd.Series(dtype="float64"),
        "availability_live_ratio": pd.Series(dtype="float64"),
    })


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def build_daily_sentiment_dataset(
    news_df: Optional[pd.DataFrame] = None,
    tickers: Optional[List[str]] = None,
    save: bool = True,
    output_path: Path = DEFAULT_OUTPUT,
) -> pd.DataFrame:
    """
    End-to-end: load -> effective_available_at -> quality -> align -> aggregate -> save.

    Args:
        news_df: Optional pre-loaded DataFrame (for tests). If None, calls load_news().
        tickers: Passed to load_news() if news_df is None.
        save: Whether to write Parquet.
        output_path: Parquet destination.

    Returns:
        Aggregated DataFrame.
    """
    settings = get_settings()
    tz = settings.market_tz()
    cutoff = time(settings.signal_cutoff_hour, settings.signal_cutoff_minute)

    df = news_df if news_df is not None else load_news(tickers=tickers)

    if df.empty:
        logger.warning("No news available. Writing empty daily_sentiment.")
        result = _empty_output()
        if save:
            _save_parquet(result, output_path)
        return result

    df = calculate_effective_available_at(df, tz=tz)
    df = classify_availability_quality(df, threshold_days=settings.backfill_threshold_days)

    trading_dates = get_trading_dates()
    logger.info("Loaded %d trading dates from calendar.", len(trading_dates))

    df = align_news_to_trading_date(df, trading_dates, cutoff=cutoff, tz=tz)

    result = aggregate_daily_sentiment(df)

    if save:
        _save_parquet(result, output_path)

    logger.info(
        "Daily sentiment: %d rows, %d tickers, date range %s -> %s.",
        len(result),
        result["ticker"].nunique() if not result.empty else 0,
        result["trading_date"].min() if not result.empty else "-",
        result["trading_date"].max() if not result.empty else "-",
    )
    return result


def _save_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("Saved %d rows to %s", len(df), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build daily sentiment dataset (TASK 104)."
    )
    parser.add_argument("--tickers", type=str, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers else None
    )

    result = build_daily_sentiment_dataset(
        tickers=tickers,
        save=not args.no_save,
        output_path=args.output,
    )

    print(f"\nDaily sentiment summary")
    print(f"  Rows:         {len(result)}")
    print(f"  Tickers:      {result['ticker'].nunique() if not result.empty else 0}")
    if not result.empty:
        print(f"  Date range:   {result['trading_date'].min()} -> {result['trading_date'].max()}")
        print(f"  Total news:   {int(result['news_count'].sum())}")
    print(f"  Output:       {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
