"""
Base dataset builder for FinSentiMind (TASK 105).

Joins prices_clean.parquet (left) with daily_sentiment.parquet (right)
on (ticker, trading_date), producing a deterministic, validated base
research dataset.

Design (approved):
    - LEFT JOIN: prices is source of truth for row universe.
    - Timezone-naive session dates normalized to midnight; tz-aware input
      is rejected (not silently stripped).
    - Duplicate keys fail hard on either side (no silent dedup).
    - Empty sentiment with valid schema is supported (all rows filled).
    - Empty sentiment with invalid schema fails at load/build boundary.
    - Fill policy: news_count=0, sentiment features NULL for unmatched rows.
    - Matched rows: sentiment_mean / positive_ratio / negative_ratio /
      availability_live_ratio must be non-null, finite, in [0,1] where
      applicable. sentiment_std may be NaN only for news_count == 1.
    - news_count strict: numeric, non-NaN, finite, >0, integer-valued.
    - Extra columns in inputs are warned and excluded from output.
    - Atomic publication: temp file + os.replace, cleanup on failure,
      existing valid artifact preserved on failed write.

Output schema (14 columns, deterministic order):
    ticker, trading_date, open, high, low, close, volume, return_1d,
    news_count, sentiment_mean, sentiment_std, positive_ratio,
    negative_ratio, availability_live_ratio

Explicitly out of scope:
    - Target generation / labels
    - Technical indicators
    - Train/test split / model training
    - External scraping / Supabase access

Usage:
    python -m ml.preprocessing.build_base_dataset
    python -m ml.preprocessing.build_base_dataset --no-save
"""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger, setup_logging


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRICE_COLUMNS: List[str] = [
    "ticker",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "return_1d",
]

SENTIMENT_COLUMNS: List[str] = [
    "ticker",
    "trading_date",
    "news_count",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]

SENTIMENT_FEATURE_COLUMNS: List[str] = SENTIMENT_COLUMNS[2:]

OUTPUT_COLUMNS: List[str] = PRICE_COLUMNS + SENTIMENT_FEATURE_COLUMNS

JOIN_KEYS: List[str] = ["ticker", "trading_date"]

# Columns in matched rows that must be finite and non-null.
MATCHED_REQUIRED_FINITE: List[str] = [
    "sentiment_mean",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]

# Columns in matched rows that must be within [0, 1].
MATCHED_RATIO_COLUMNS: List[str] = [
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICES_PATH = _PROJECT_ROOT / "ml" / "data" / "prices_clean.parquet"
DEFAULT_SENTIMENT_PATH = _PROJECT_ROOT / "ml" / "data" / "daily_sentiment.parquet"
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "ml" / "data" / "base_dataset.parquet"


# ---------------------------------------------------------------------------
# Load + schema validation
# ---------------------------------------------------------------------------

def _validate_required_columns(
    df: pd.DataFrame, required: List[str], name: str
) -> None:
    """Fail if any required column missing. Runs even for empty DataFrames."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{name} dataset missing required columns: {missing}. "
            f"Present columns: {list(df.columns)}"
        )


def _warn_extra_columns(
    df: pd.DataFrame, required: List[str], name: str
) -> None:
    """
    Warn on unexpected columns not in the TASK 105 output schema.

    Extra columns are excluded from output (deterministic 14-column
    contract). Loud warning prevents silent data discard.
    """
    extra = [c for c in df.columns if c not in required]
    if extra:
        logger.warning(
            "%s dataset has %d extra column(s) not in TASK 105 output "
            "schema: %s. They will be EXCLUDED from the base dataset.",
            name, len(extra), extra,
        )


def load_prices(path: Path) -> pd.DataFrame:
    """Load prices_clean parquet and validate required columns."""
    if not path.exists():
        raise FileNotFoundError(
            f"prices_clean parquet not found: {path}. "
            f"Run `python -m ml.preprocessing.load_prices` first."
        )
    df = pd.read_parquet(path)
    logger.info("Loaded %d price rows from %s", len(df), path)
    _validate_required_columns(df, PRICE_COLUMNS, "prices")
    return df


def load_sentiment(path: Path) -> pd.DataFrame:
    """
    Load daily_sentiment parquet and validate required columns.

    Schema validation runs even when the file has 0 rows — empty valid
    schema is distinguished from invalid schema.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"daily_sentiment parquet not found: {path}. "
            f"Run `python -m ml.preprocessing.daily_sentiment` first."
        )
    df = pd.read_parquet(path)
    logger.info("Loaded %d sentiment rows from %s", len(df), path)
    _validate_required_columns(df, SENTIMENT_COLUMNS, "sentiment")
    return df


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

def _validate_no_null_keys(df: pd.DataFrame, name: str) -> None:
    """Reject null ticker, empty/whitespace ticker, null date."""
    if df["ticker"].isna().any():
        n = int(df["ticker"].isna().sum())
        raise ValueError(f"{name} dataset has {n} NULL ticker key(s).")

    empty_mask = df["ticker"].astype(str).str.strip().eq("")
    if empty_mask.any():
        n = int(empty_mask.sum())
        raise ValueError(f"{name} dataset has {n} empty ticker key(s).")

    if df["trading_date"].isna().any():
        n = int(df["trading_date"].isna().sum())
        raise ValueError(f"{name} dataset has {n} NULL/invalid trading_date(s).")


def _validate_unique_keys(df: pd.DataFrame, name: str) -> None:
    """Reject duplicate (ticker, trading_date). Never dedup silently."""
    dup_mask = df.duplicated(subset=JOIN_KEYS, keep=False)
    if dup_mask.any():
        dup_sample = (
            df.loc[dup_mask, JOIN_KEYS]
            .head(5)
            .to_dict(orient="records")
        )
        raise ValueError(
            f"{name} dataset has duplicate {JOIN_KEYS} keys. "
            f"Sample: {dup_sample}. "
            f"Refusing to silently deduplicate — fix upstream."
        )


def _validate_news_count_strict(df: pd.DataFrame) -> None:
    """
    Strict news_count validation for sentiment input.

    Each row must have: numeric, non-NaN, finite, strictly positive,
    integer-valued news_count. Fractional counts rejected (no truncation).
    Silent coercion to 0 forbidden.
    """
    if df.empty:
        return

    col = df["news_count"]

    # 1. NULL / NaN
    if col.isna().any():
        n = int(col.isna().sum())
        raise ValueError(
            f"Sentiment news_count has {n} NULL/NaN value(s)."
        )

    # 2. Numeric (allow numeric strings; reject non-coercible)
    try:
        numeric = pd.to_numeric(col, errors="raise").astype("float64")
    except (ValueError, TypeError) as e:
        bad = col[pd.to_numeric(col, errors="coerce").isna()].head(5).tolist()
        raise ValueError(
            f"Sentiment news_count must be numeric. "
            f"Non-numeric sample: {bad}. Error: {e}"
        )

    # 3. Finite
    finite_mask = np.isfinite(numeric)
    if not finite_mask.all():
        n = int((~finite_mask).sum())
        sample = numeric[~finite_mask].head(5).tolist()
        raise ValueError(
            f"Sentiment news_count has {n} non-finite value(s) "
            f"(inf/-inf). Sample: {sample}"
        )

    # 4. Strictly positive
    positive_mask = numeric > 0
    if not positive_mask.all():
        n = int((~positive_mask).sum())
        sample = numeric[~positive_mask].head(5).tolist()
        raise ValueError(
            f"Sentiment news_count has {n} non-positive value(s). "
            f"Sample: {sample}"
        )

    # 5. Integer-valued (reject fractional counts like 1.5)
    int_mask = numeric == numeric.astype("int64").astype("float64")
    if not int_mask.all():
        n = int((~int_mask).sum())
        sample = numeric[~int_mask].head(5).tolist()
        raise ValueError(
            f"Sentiment news_count has {n} fractional value(s). "
            f"news_count must be integer-valued. Sample: {sample}"
        )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_ticker(series: pd.Series) -> pd.Series:
    """Normalize to uppercase stripped string. Preserves NA."""
    return series.astype("string").str.strip().str.upper()


def normalize_trading_date(series: pd.Series) -> pd.Series:
    """
    Normalize trading_date to tz-naive datetime64[ns] at midnight.

    Rejects tz-aware inputs — caller must strip tz explicitly. Silent
    tz conversion could shift a session across dates.

    Invalid values become NaT; caller must validate and reject them.
    """
    parsed = pd.to_datetime(series, errors="coerce")

    # Datetime dtype with tzinfo -> reject
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        raise ValueError(
            "trading_date must be timezone-naive. "
            "Convert to date (session) before passing to base dataset."
        )

    # Object dtype (mixed tz-aware/naive) -> inspect each value
    if parsed.dtype == object:
        for val in parsed.dropna():
            if isinstance(val, pd.Timestamp) and val.tz is not None:
                raise ValueError(
                    f"trading_date must be timezone-naive. Found: {val}"
                )
            if hasattr(val, "tzinfo") and val.tzinfo is not None:
                raise ValueError(
                    f"trading_date must be timezone-naive. Found: {val}"
                )
        # Re-parse (should succeed now that we've verified no tz)
        parsed = pd.to_datetime(series, errors="coerce")

    if not pd.api.types.is_datetime64_any_dtype(parsed):
        raise ValueError(
            f"Cannot normalize trading_date to datetime. dtype={parsed.dtype}"
        )

    return parsed.dt.normalize().astype("datetime64[ns]")


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_base_dataset(
    prices: pd.DataFrame, sentiment: pd.DataFrame
) -> pd.DataFrame:
    """
    LEFT JOIN prices with sentiment on (ticker, trading_date).

    Returns:
        Base dataset with OUTPUT_COLUMNS, sorted by (ticker, trading_date).

    Raises:
        ValueError: on any validation failure. No partial output.
    """
    # --- Schema validation at public boundary ---
    # Runs even when inputs are empty (empty != invalid schema).
    _validate_required_columns(prices, PRICE_COLUMNS, "prices")
    _validate_required_columns(sentiment, SENTIMENT_COLUMNS, "sentiment")

    # --- Warn on extra columns ---
    _warn_extra_columns(prices, PRICE_COLUMNS, "prices")
    _warn_extra_columns(sentiment, SENTIMENT_COLUMNS, "sentiment")

    # --- Empty prices check ---
    if prices.empty:
        raise ValueError(
            "prices dataset is empty — nothing to build from."
        )

    # Normalize keys (copies — do not mutate caller's data)
    prices = prices.copy()
    sentiment = sentiment.copy()

    prices["ticker"] = normalize_ticker(prices["ticker"])
    prices["trading_date"] = normalize_trading_date(prices["trading_date"])

    if not sentiment.empty:
        sentiment["ticker"] = normalize_ticker(sentiment["ticker"])
        sentiment["trading_date"] = normalize_trading_date(
            sentiment["trading_date"]
        )

    # Validate pre-merge (fail fast)
    _validate_no_null_keys(prices, "prices")
    _validate_unique_keys(prices, "prices")

    if not sentiment.empty:
        _validate_no_null_keys(sentiment, "sentiment")
        _validate_unique_keys(sentiment, "sentiment")
        _validate_news_count_strict(sentiment)

    # LEFT JOIN
    if sentiment.empty:
        logger.warning(
            "Sentiment dataset is empty — all price rows will have "
            "news_count=0 and NULL sentiment features."
        )
        base = prices.copy()
        base["news_count"] = 0
        for col in SENTIMENT_FEATURE_COLUMNS[1:]:  # skip news_count
            base[col] = np.nan
    else:
        base = prices.merge(
            sentiment[JOIN_KEYS + SENTIMENT_FEATURE_COLUMNS],
            on=JOIN_KEYS,
            how="left",
            validate="one_to_one",
        )
        # Unmatched -> news_count=0 (explicit no-news signal)
        base["news_count"] = base["news_count"].fillna(0).astype("int64")

    # Deterministic sort + column order
    base = base.sort_values(JOIN_KEYS, kind="mergesort").reset_index(drop=True)
    base = base[OUTPUT_COLUMNS].copy()

    # Validate output invariants
    validate_output_invariants(base, prices, sentiment)

    return base


# ---------------------------------------------------------------------------
# Output invariants
# ---------------------------------------------------------------------------

def validate_output_invariants(
    base: pd.DataFrame,
    prices: pd.DataFrame,
    sentiment: pd.DataFrame,
) -> None:
    """
    Enforce all TASK 105 invariants. Raise on any failure.

    Invariants:
        1. Row count == input price row count.
        2. Output (ticker, trading_date) keys unique.
        3. No price row lost / no extra row (set equality).
        4. Original price values unchanged.
        5. No-news rows: news_count=0 and ALL sentiment features NULL.
        6. Matched rows: required fields non-null, finite, in range.
        7. sentiment_std may be NaN only for news_count == 1; for
           news_count > 1 must be non-null and finite.
        8. Deterministic column order.
    """
    # 1. Row count
    if len(base) != len(prices):
        raise ValueError(
            f"Row count invariant failed: base={len(base)} != prices={len(prices)}"
        )

    # 2. Unique keys
    if base.duplicated(subset=JOIN_KEYS).any():
        raise ValueError("Output has duplicate (ticker, trading_date) keys.")

    # 3. Key set equality
    price_keys = set(zip(prices["ticker"], prices["trading_date"]))
    base_keys = set(zip(base["ticker"], base["trading_date"]))
    if price_keys != base_keys:
        missing = price_keys - base_keys
        extra = base_keys - price_keys
        raise ValueError(
            f"Key set mismatch. Missing from output: {list(missing)[:5]}. "
            f"Extra in output: {list(extra)[:5]}."
        )

    # 4. Price values unchanged (spot-check via re-merge)
    price_val_cols = ["open", "high", "low", "close", "volume", "return_1d"]
    merged = base[JOIN_KEYS + price_val_cols].merge(
        prices[JOIN_KEYS + price_val_cols],
        on=JOIN_KEYS,
        suffixes=("_b", "_p"),
        how="inner",
        validate="one_to_one",
    )
    for col in price_val_cols:
        left = merged[f"{col}_b"]
        right = merged[f"{col}_p"]
        mismatched = ~(left.eq(right) | (left.isna() & right.isna()))
        if mismatched.any():
            raise ValueError(
                f"Price column {col!r} changed during join. "
                f"{int(mismatched.sum())} row(s) differ."
            )

    # 5. No-news rows: all sentiment features NULL
    no_news = base[base["news_count"] == 0]
    for col in SENTIMENT_FEATURE_COLUMNS[1:]:
        if not no_news[col].isna().all():
            n = int(no_news[col].notna().sum())
            raise ValueError(
                f"No-news rows have {n} non-NULL value(s) in {col!r}. "
                f"Expected all NULL for news_count == 0."
            )

    # 6. Matched rows: required finite + range checks
    matched = base[base["news_count"] > 0]
    if not matched.empty:
        for col in MATCHED_REQUIRED_FINITE:
            vals = matched[col]
            if vals.isna().any():
                n = int(vals.isna().sum())
                raise ValueError(
                    f"Matched rows have {n} NULL value(s) in {col!r}."
                )
            finite = np.isfinite(vals.astype(float))
            if not finite.all():
                n = int((~finite).sum())
                raise ValueError(
                    f"Matched rows have {n} non-finite value(s) in {col!r}."
                )

        for col in MATCHED_RATIO_COLUMNS:
            vals = matched[col].astype(float)
            out_of_range = ((vals < 0) | (vals > 1)).sum()
            if out_of_range > 0:
                raise ValueError(
                    f"Matched rows have {int(out_of_range)} value(s) "
                    f"outside [0, 1] in {col!r}."
                )

        # sentiment_std for n>1 must be non-null AND finite
        multi = matched[matched["news_count"] > 1]
        if not multi.empty:
            std_vals = multi["sentiment_std"]
            if std_vals.isna().any():
                n = int(std_vals.isna().sum())
                raise ValueError(
                    f"Matched rows with news_count > 1 have {n} NULL "
                    f"sentiment_std. Aggregation should populate std."
                )
            if not np.isfinite(std_vals.astype(float)).all():
                raise ValueError(
                    "Matched rows with news_count > 1 have non-finite "
                    "sentiment_std."
                )

        # sentiment_std for n==1: NaN allowed; any non-null value
        # must be numeric and finite (rejects infinity).
        single = matched[matched["news_count"] == 1]
        if not single.empty:
            single_std_non_null = single["sentiment_std"].dropna()
            if len(single_std_non_null) > 0:
                try:
                    single_std_numeric = single_std_non_null.astype("float64")
                except (ValueError, TypeError):
                    raise ValueError(
                        "Single-article rows have non-numeric sentiment_std."
                    )
                if not np.isfinite(single_std_numeric).all():
                    n = int((~np.isfinite(single_std_numeric)).sum())
                    raise ValueError(
                        f"Single-article rows have {n} non-finite "
                        f"sentiment_std value(s). Expected NaN or finite."
                    )

    # 8. Column order
    if list(base.columns) != OUTPUT_COLUMNS:
        raise ValueError(
            f"Column order mismatch. Expected {OUTPUT_COLUMNS}, "
            f"got {list(base.columns)}"
        )

    logger.info(
        "Output invariants passed: rows=%d matched=%d no_news=%d",
        len(base), len(matched), len(no_news),
    )


# ---------------------------------------------------------------------------
# Atomic save
# ---------------------------------------------------------------------------

def save_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """
    Write Parquet atomically: unique temp file in destination dir,
    then os.replace() on success.

    - Existing artifact preserved on write failure.
    - Temp file cleaned up on failure.
    - No partial final artifact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Unique temp file in same directory (same filesystem for atomic rename)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}.",
        suffix=".parquet.tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)  # Atomic on same filesystem
        logger.info("Saved %d rows to %s (atomic)", len(df), path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_build(
    prices_path: Path = DEFAULT_PRICES_PATH,
    sentiment_path: Path = DEFAULT_SENTIMENT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    save: bool = True,
) -> pd.DataFrame:
    """End-to-end: load -> normalize -> join -> validate -> save."""
    prices = load_prices(prices_path)
    sentiment = load_sentiment(sentiment_path)
    base = build_base_dataset(prices, sentiment)

    if save:
        save_parquet_atomic(base, output_path)

    matched = int((base["news_count"] > 0).sum())
    unmatched = len(base) - matched

    logger.info("=" * 60)
    logger.info("BASE DATASET BUILD SUMMARY")
    logger.info("=" * 60)
    logger.info("Prices rows:        %d", len(prices))
    logger.info("Sentiment rows:     %d", len(sentiment))
    logger.info("Output rows:        %d", len(base))
    logger.info("  Matched:          %d", matched)
    logger.info("  Unmatched:        %d", unmatched)
    logger.info("Tickers:            %d", base["ticker"].nunique())
    if len(base):
        logger.info(
            "Date range:         %s -> %s",
            base["trading_date"].min().date(),
            base["trading_date"].max().date(),
        )
    logger.info("=" * 60)
    return base


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build base dataset (TASK 105)."
    )
    parser.add_argument(
        "--prices", type=Path, default=DEFAULT_PRICES_PATH,
        help=f"Prices parquet (default: {DEFAULT_PRICES_PATH}).",
    )
    parser.add_argument(
        "--sentiment", type=Path, default=DEFAULT_SENTIMENT_PATH,
        help=f"Sentiment parquet (default: {DEFAULT_SENTIMENT_PATH}).",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH,
        help=f"Output parquet (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()

    base = run_build(
        prices_path=args.prices,
        sentiment_path=args.sentiment,
        output_path=args.output,
        save=not args.no_save,
    )

    print(f"\nBase dataset summary")
    print(f"  Rows:      {len(base)}")
    print(f"  Tickers:   {base['ticker'].nunique()}")
    print(f"  Columns:   {len(base.columns)}")
    print(f"  Output:    {args.output}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())