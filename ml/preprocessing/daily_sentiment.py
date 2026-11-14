from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all
from app.services.time_alignment import map_to_trading_date
from ml.preprocessing.dataset_store import write_json_atomic
from ml.preprocessing.trading_calendar import get_trading_dates


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWS_SELECT_COLUMNS = [
    "id",
    "ticker",
    "sentiment_label",
    "sentiment_score",
    "published_at",
    "scraped_at",
]

MAPPING_MAPPED = "MAPPED"
MAPPING_PENDING = "PENDING_FUTURE_SESSION"
MAPPING_QUARANTINED = "QUARANTINED_UNRESOLVABLE"

PENDING_REASON_FUTURE = "next_verified_session_not_yet_available"
QUARANTINE_REASON_TIMESTAMP = "missing_or_invalid_effective_timestamp"
QUARANTINE_REASON_UNRESOLVABLE = "no_verified_session_within_lookahead_window"

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
DEFAULT_REPORTS_DIR = _PROJECT_ROOT / "ml" / "reports"
_LOOKAHEAD_DAYS = 30


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
        - Missing scraped_at: retained and marked with `timestamp_error`;
          the alignment stage quarantines it explicitly. It is never silently
          dropped.
        - Missing published_at with valid scraped_at: fallback to scraped_at;
          flagged via internal `_published_was_missing` for reconstructed
          classification.
        - Missing both: retained and marked with `timestamp_error`.

    Naive timestamps:
        Supabase `timestamptz` returns ISO 8601 with tz offset. `utc=True`
        treats naive input as UTC - documented assumption; verify source
        convention if unexpected timestamps appear.

    Args:
        df: DataFrame with `published_at`, `scraped_at` (ISO strings or datetimes).
        tz: Market timezone (e.g., Asia/Jakarta).

    Returns:
        DataFrame copy with tz-aware `published_at`, `scraped_at`, and new
        columns `effective_available_at` and `timestamp_error` (all in `tz`).
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
            "Timestamp audit: all %d records valid (0 quarantined).", n_both_present
        )
    else:
        logger.info(
            "Timestamp audit: scraped_missing=%d (quarantined), "
            "published_missing_but_scraped_valid=%d (fallback -> reconstructed), "
            "both_valid=%d.",
            n_scraped_missing,
            n_published_missing_but_scraped_valid,
            n_both_present,
        )

    df["timestamp_error"] = None
    df.loc[
        df["scraped_at"].isna() & df["published_at"].isna(),
        "timestamp_error",
    ] = "missing_published_and_scraped_at"
    df.loc[
        df["scraped_at"].isna() & df["published_at"].notna(),
        "timestamp_error",
    ] = "missing_scraped_at"

    # Fallback: published_at missing but scraped_at valid
    df["_published_was_missing"] = df["published_at"].isna()
    df.loc[
        df["_published_was_missing"] & df["scraped_at"].notna(),
        "published_at",
    ] = df.loc[
        df["_published_was_missing"] & df["scraped_at"].notna(),
        "scraped_at",
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


def _article_identifier(row: pd.Series, position: int) -> str:
    """Return a stable article identifier for audit reports."""
    if "id" in row.index:
        value = row.get("id")
        if value is not None and not pd.isna(value):
            return str(value)
    return f"row_{position:06d}"


def _candidate_session_date(
    timestamp: datetime, cutoff: time, tz: ZoneInfo
) -> date:
    """Apply the existing cutoff rule without inventing a session."""
    local_dt = timestamp.astimezone(tz)
    if local_dt.time() <= cutoff:
        return local_dt.date()
    return local_dt.date() + timedelta(days=1)


def classify_news_alignment(
    df: pd.DataFrame,
    trading_dates: Iterable,
    cutoff: time,
    tz: ZoneInfo,
) -> pd.DataFrame:
    """Classify every article as MAPPED, PENDING, or QUARANTINED.

    Safety rules:
        - A future article is never mapped backwards.
        - Weekday arithmetic never creates a session that is absent from the
          verified calendar.
        - Pending articles remain reprocessable on later runs.
        - Malformed/unresolvable historical articles receive an explicit
          quarantine reason.
    """
    dates = set(trading_dates)
    if not dates:
        raise ValueError("trading_dates is empty - cannot classify news.")

    max_date = max(dates)
    df = df.copy()

    statuses: List[str] = []
    mapped_dates: List[Optional[date]] = []
    reasons: List[Optional[str]] = []
    eligible: List[bool] = []

    for position, (_, row) in enumerate(df.iterrows()):
        timestamp = row.get("effective_available_at")
        timestamp_error = row.get("timestamp_error")

        if timestamp is None or pd.isna(timestamp) or timestamp_error:
            statuses.append(MAPPING_QUARANTINED)
            mapped_dates.append(None)
            reasons.append(
                str(timestamp_error) if timestamp_error else QUARANTINE_REASON_TIMESTAMP
            )
            eligible.append(False)
            continue

        try:
            mapped = map_to_trading_date(timestamp, dates, cutoff, tz)
        except (ValueError, TypeError):
            candidate = _candidate_session_date(timestamp, cutoff, tz)
            statuses.append(MAPPING_PENDING)
            mapped_dates.append(None)
            reasons.append(PENDING_REASON_FUTURE)
            eligible.append(True)
            if candidate <= max_date:
                statuses[-1] = MAPPING_QUARANTINED
                reasons[-1] = QUARANTINE_REASON_UNRESOLVABLE
                eligible[-1] = False
            continue

        statuses.append(MAPPING_MAPPED)
        mapped_dates.append(mapped)
        reasons.append(None)
        eligible.append(False)

    df["trading_date"] = mapped_dates
    df["mapping_status"] = statuses
    df["mapping_reason"] = reasons
    df["reprocessing_eligible"] = eligible
    return df


def summarize_mapping(df: pd.DataFrame) -> Dict[str, Any]:
    """Return machine-readable counts and calendar bounds for an alignment."""
    if "mapping_status" not in df.columns:
        return {
            "mapped": 0,
            "pending_future_session": 0,
            "quarantined_unresolvable": 0,
            "total": int(len(df)),
        }
    counts = df["mapping_status"].value_counts(dropna=False)
    return {
        "mapped": int(counts.get(MAPPING_MAPPED, 0)),
        "pending_future_session": int(counts.get(MAPPING_PENDING, 0)),
        "quarantined_unresolvable": int(counts.get(MAPPING_QUARANTINED, 0)),
        "total": int(len(df)),
    }


def _iso_or_none(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return str(value)


def _audit_article(row: pd.Series, position: int) -> Dict[str, Any]:
    return {
        "article_id": _article_identifier(row, position),
        "ticker": None if pd.isna(row.get("ticker")) else str(row.get("ticker")),
        "published_at": _iso_or_none(row.get("published_at")),
        "scraped_at": _iso_or_none(row.get("scraped_at")),
        "effective_available_at": _iso_or_none(
            row.get("effective_available_at")
        ),
        "mapping_status": row.get("mapping_status"),
        "reason": row.get("mapping_reason"),
        "reprocessing_eligible": bool(row.get("reprocessing_eligible", False)),
    }


def build_pending_news_report(
    df: pd.DataFrame,
    run_id: str,
    calendar_dates: Iterable,
    cutoff: time,
    tz: ZoneInfo,
) -> Dict[str, Any]:
    """Build an auditable report of pending and quarantined articles."""
    dates = sorted(set(calendar_dates))
    pending: List[Dict[str, Any]] = []
    quarantined: List[Dict[str, Any]] = []

    for position, (_, row) in enumerate(df.iterrows()):
        status = row.get("mapping_status")
        if status == MAPPING_PENDING:
            pending.append(_audit_article(row, position))
        elif status == MAPPING_QUARANTINED:
            quarantined.append(_audit_article(row, position))

    counts = summarize_mapping(df)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timezone": str(tz),
        "signal_cutoff": cutoff.isoformat(),
        "calendar_start": dates[0].isoformat() if dates else None,
        "calendar_end": dates[-1].isoformat() if dates else None,
        "calendar_session_count": len(dates),
        "counts": counts,
        "pending_articles": pending,
        "quarantined_articles": quarantined,
    }


def write_pending_news_report(
    df: pd.DataFrame,
    run_id: str,
    calendar_dates: Iterable,
    cutoff: time,
    tz: ZoneInfo,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
) -> Path:
    """Write ``ml/reports/pending_news_<run_id>.json`` atomically."""
    payload = build_pending_news_report(
        df, run_id=run_id, calendar_dates=calendar_dates, cutoff=cutoff, tz=tz
    )
    path = reports_dir / f"pending_news_{run_id}.json"
    write_json_atomic(path, payload)
    return path


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
    if "mapping_status" in df.columns:
        df = df[df["mapping_status"] == MAPPING_MAPPED].copy()
    if df.empty:
        return _empty_output()

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
    calendar_path: Optional[Path] = None,
    run_id: Optional[str] = None,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
) -> pd.DataFrame:
    """
    End-to-end: load -> effective_available_at -> quality -> classify ->
    aggregate -> save.

    Articles are never dropped silently. Future articles become
    PENDING_FUTURE_SESSION and are written to
    ``pending_news_<run_id>.json``. Malformed or historically unresolvable
    articles become QUARANTINED_UNRESOLVABLE with an explicit reason.

    Returns:
        Aggregated DataFrame. Mapping audit is attached to
        ``result.attrs["mapping_audit"]`` and, when a run_id is given, the
        pending report path to ``result.attrs["pending_report_path"]``.
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
        result.attrs["mapping_audit"] = summarize_mapping(df)
        if save:
            _save_parquet(result, output_path)
        return result

    df = calculate_effective_available_at(df, tz=tz)
    df = classify_availability_quality(
        df, threshold_days=settings.backfill_threshold_days
    )

    trading_dates = get_trading_dates(calendar_path)
    logger.info("Loaded %d trading dates from calendar.", len(trading_dates))

    df = classify_news_alignment(df, trading_dates, cutoff=cutoff, tz=tz)
    audit = summarize_mapping(df)

    report_path: Optional[Path] = None
    if run_id is not None or audit["pending_future_session"] or audit["quarantined_unresolvable"]:
        effective_run_id = run_id or datetime.now(timezone.utc).strftime(
            "sentiment_%Y%m%dT%H%M%SZ"
        )
        report_path = write_pending_news_report(
            df,
            run_id=effective_run_id,
            calendar_dates=trading_dates,
            cutoff=cutoff,
            tz=tz,
            reports_dir=reports_dir,
        )
        logger.info("Pending/quarantine audit: %s", report_path)

    result = aggregate_daily_sentiment(df)
    result.attrs["mapping_audit"] = audit
    if report_path is not None:
        result.attrs["pending_report_path"] = str(report_path)

    if save:
        _save_parquet(result, output_path)

    logger.info(
        "Daily sentiment: %d rows, %d tickers, date range %s -> %s "
        "(mapped=%d pending=%d quarantined=%d).",
        len(result),
        result["ticker"].nunique() if not result.empty else 0,
        result["trading_date"].min() if not result.empty else "-",
        result["trading_date"].max() if not result.empty else "-",
        audit["mapped"],
        audit["pending_future_session"],
        audit["quarantined_unresolvable"],
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