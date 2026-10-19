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
    Fetch raw news from Supabase `berita_saham`, filter invalid rows with audit.

    Filter rules:
        - ticker NOT NULL and not empty
        - sentiment_label NOT NULL
        - sentiment_score NOT NULL

    Audit: logs per-reason exclusion counts so silent drops are impossible.

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

    # Defensive: ensure all expected columns exist
    for col in NEWS_SELECT_COLUMNS:
        if col not in df.columns:
            df[col] = None

    initial = len(df)

    n_missing_ticker = int(df["ticker"].isna().sum())

    ticker_text = df["ticker"].fillna("").astype(str).str.strip()
    n_empty_ticker = int(
        (df["ticker"].notna() & ticker_text.eq("")).sum()
    )

    n_missing_label = int(df["sentiment_label"].isna().sum())
    n_missing_score = int(df["sentiment_score"].isna().sum())

    df = df.dropna(subset=["ticker", "sentiment_label", "sentiment_score"])
    df = df[df["ticker"].astype(str).str.strip() != ""].copy()
    final = len(df)

    excluded = initial - final
    if excluded == 0:
        logger.info(
            "News audit: initial=%d, all valid (0 excluded).", initial
        )
    else:
        logger.info(
            "News audit: initial=%d final=%d excluded=%d "
            "(missing_ticker=%d, empty_ticker=%d, missing_label=%d, missing_score=%d).",
            initial,
            final,
            excluded,
            n_missing_ticker,
            n_empty_ticker,
            n_missing_label,
            n_missing_score,
        )

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
        Taking max is conservative — never lets the model "see" an article
        earlier than realistically available.

    Audit:
        - Missing scraped_at: excluded (observation time cannot be established).
        - Missing published_at with valid scraped_at: fallback to scraped_at;
          flagged via internal `_published_was_missing` for reconstructed
          classification.
        - Missing both: excluded (counted as missing_scraped).

    Naive timestamps:
        Supabase `timestamptz` returns ISO 8601 with tz offset. `utc=True`
        treats naive input as UTC — documented assumption; verify source
        convention if unexpected timestamps appear.

    Args:
        df: DataFrame with `published_at`, `scraped_at` (ISO strings or datetimes).
        tz: Market timezone (e.g., Asia/Jakarta).

    Returns:
        DataFrame copy with tz-aware `published_at`, `scraped_at`, and new
        column `effective_available_at` (all in `tz`).
    """
    df = df.copy()

    df["published_at"] = pd.to_datetime(
        df["published_at"], utc=True, errors="coerce"
    )
    df["scraped_at"] = pd.to_datetime(
        df["scraped_at"], utc=True, errors="coerce"
    )

    n_scraped_missing = int(df["scraped_at"].isna().sum())
    n_published_missing_but_scraped_valid = int(
        (df["published_at"].isna() & df["scraped_at"].notna()).sum()
    )
    n_both_present = int(
        (df["published_at"].notna() & df["scraped_at"].notna()).sum()
    )

    if n_scraped_missing == 0 and n_published_missing_but_scraped_valid == 0:
        logger.info(
            "Timestamp audit: all %d records valid (0 excluded).", n_both_present
        )
    else:
        logger.info(
            "Timestamp audit: scraped_missing=%d (excluded), "
            "published_missing_but_scraped_valid=%d (fallback -> reconstructed), "
            "both_valid=%d.",
            n_scraped_missing,
            n_published_missing_but_scraped_valid,
            n_both_present,
        )

    # Exclude rows without scraped_at (observation time unknown)
    df = df[df["scraped_at"].notna()].copy()

    # Fallback: published_at missing but scraped_at valid
    df["_published_was_missing"] = df["published_at"].isna()
    df.loc[df["_published_was_missing"], "published_at"] = df.loc[
        df["_published_was_missing"], "scraped_at"
    ]

    # effective_available_at = max(published_at, scraped_at)
    df["effective_available_at"] = df["published_at"].where(
        df["published_at"] >= df["scraped_at"], df["scraped_at"]
    )

    # Convert all timestamp columns to market tz
    df["published_at"] = df["published_at"].dt.tz_convert(tz)
    df["scraped_at"] = df["scraped_at"].dt.tz_convert(tz)
    df["effective_available_at"] = df["effective_available_at"].dt.tz_convert(tz)

    return df


def classify_availability_quality(
    df: pd.DataFrame, threshold_days: int
) -> pd.DataFrame:
    """
    Add `availability_quality` column: 'live' or 'reconstructed'.

    Strict rule:
        'live' iff:
            - published_at was present in source (not fallback), AND
            - 0 <= delta_days <= threshold_days

        'reconstructed' otherwise, including:
            - delta_days > threshold_days  (late scrape / backfill)
            - delta_days < 0               (timestamp anomaly)
            - published_at was missing     (fallback path)

    Negative delta (scraped_at < published_at) is an anomaly — count logged.
    Rows with missing published_at fallback are also counted and logged.

    Args:
        df: DataFrame with tz-aware `published_at` and `scraped_at`.
            May contain internal `_published_was_missing` column.
        threshold_days: Max gap (days) to still call it 'live'.

    Returns:
        DataFrame copy with new column `availability_quality`.
        Internal `_published_was_missing` column is dropped.
    """
    df = df.copy()

    delta_days = (
        (df["scraped_at"] - df["published_at"]).dt.total_seconds() / 86400.0
    )

    if "_published_was_missing" in df.columns:
        pub_was_missing = df["_published_was_missing"].fillna(False).astype(bool)
    else:
        pub_was_missing = pd.Series(False, index=df.index)

    live_mask = (
        (~pub_was_missing)
        & (delta_days >= 0)
        & (delta_days <= threshold_days)
    )

    df["availability_quality"] = "reconstructed"
    df.loc[live_mask, "availability_quality"] = "live"

    n_negative = int((delta_days < 0).sum())
    n_fallback = int(pub_was_missing.sum())

    if n_negative > 0:
        logger.warning(
            "Timestamp anomaly: %d record(s) have scraped_at < published_at; "
            "classified as reconstructed.",
            n_negative,
        )
    if n_fallback > 0:
        logger.warning(
            "%d record(s) had missing published_at and used scraped_at "
            "fallback; classified as reconstructed.",
            n_fallback,
        )

    if "_published_was_missing" in df.columns:
        df = df.drop(columns=["_published_was_missing"])

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

    Single source of truth: do not duplicate alignment logic.

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

    Sparse output: only ticker-date pairs with at least one article are
    represented. No filler rows for "no news" days (no news != neutral).

    sentiment_std: sample standard deviation (ddof=1). NaN for single-article
    groups — statistically correct, downstream decides imputation.

    Args:
        df: DataFrame with columns:
            ticker, trading_date, sentiment_label, sentiment_score,
            availability_quality.

    Returns:
        Aggregated DataFrame with OUTPUT_COLUMNS, sorted by
        (ticker, trading_date). Empty DataFrame with OUTPUT_COLUMNS if
        input is empty.
    """
    if df.empty:
        return _empty_output()

    df = df.copy()

    df["_is_positive"] = (
        df["sentiment_label"].str.upper() == "POSITIVE"
    ).astype(int)
    df["_is_negative"] = (
        df["sentiment_label"].str.upper() == "NEGATIVE"
    ).astype(int)
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
    grouped = grouped.sort_values(
        ["ticker", "trading_date"], kind="mergesort"
    ).reset_index(drop=True)

    return grouped


def _empty_output() -> pd.DataFrame:
    """Return empty DataFrame with correct output schema and dtypes."""
    return pd.DataFrame({
        "ticker": pd.Series(dtype="string"),
        "trading_date": pd.Series(dtype="object"),
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
    End-to-end: load -> effective_available_at -> quality -> align ->
    aggregate -> save.

    Args:
        news_df: Optional pre-loaded DataFrame (for tests).
            If None, calls load_news().
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
        logger.warning(
            "No news available. Writing empty daily_sentiment."
        )
        result = _empty_output()
        if save:
            _save_parquet(result, output_path)
        return result

    df = calculate_effective_available_at(df, tz=tz)
    df = classify_availability_quality(
        df, threshold_days=settings.backfill_threshold_days
    )

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
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated tickers. Default: all rows.",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Output Parquet path (default: {DEFAULT_OUTPUT}).",
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

    result = build_daily_sentiment_dataset(
        tickers=tickers,
        save=not args.no_save,
        output_path=args.output,
    )

    print(f"\nDaily sentiment summary")
    print(f"  Rows:         {len(result)}")
    print(
        f"  Tickers:      "
        f"{result['ticker'].nunique() if not result.empty else 0}"
    )
    if not result.empty:
        print(
            f"  Date range:   "
            f"{result['trading_date'].min()} -> {result['trading_date'].max()}"
        )
        print(f"  Total news:   {int(result['news_count'].sum())}")
    print(f"  Output:       {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())