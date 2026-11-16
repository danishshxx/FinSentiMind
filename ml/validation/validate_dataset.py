from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

from app.core.logging import get_logger, setup_logging


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Golden contract — do NOT change without updating TASK 105's OUTPUT_COLUMNS.
# This is intentionally hardcoded (defense-in-depth). A separate test verifies
# it matches the builder's OUTPUT_COLUMNS, catching drift.
EXPECTED_COLUMNS: List[str] = [
    "ticker",
    "trading_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "return_1d",
    "news_count",
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
]

KEY_COLUMNS: List[str] = ["ticker", "trading_date"]

PRICE_VALUE_COLUMNS: List[str] = [
    "open", "high", "low", "close", "volume", "return_1d",
]

OHLC_COLUMNS: List[str] = ["open", "high", "low", "close"]

SENTIMENT_FEATURE_COLUMNS: List[str] = [
    "sentiment_mean", "sentiment_std", "positive_ratio",
    "negative_ratio", "availability_live_ratio",
]

# Fields that must be non-null and finite when news_count > 0.
MATCHED_REQUIRED_FINITE: List[str] = [
    "sentiment_mean", "positive_ratio", "negative_ratio",
    "availability_live_ratio",
]

# Fields constrained to [0, 1].
RATIO_COLUMNS: List[str] = [
    "positive_ratio", "negative_ratio", "availability_live_ratio",
]

# Low-coverage warning threshold (not a hard limit, just a signal).
DEFAULT_FRESHNESS_THRESHOLD_DAYS = 7

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
from ml.preprocessing.dataset_store import resolve_active_dataset_dir
from ml.preprocessing.idx_calendar import (
    OFFICIAL_CLOSURE,
    UNVERIFIED,
    WEEKEND,
    exchange_day_status,
    load_calendar_source,
)

_ACTIVE_DATA_DIR = resolve_active_dataset_dir(_PROJECT_ROOT / "ml" / "data")
DEFAULT_BASE_PATH = _ACTIVE_DATA_DIR / "base_dataset.parquet"
DEFAULT_PRICES_PATH = _ACTIVE_DATA_DIR / "prices_clean.parquet"
DEFAULT_SENTIMENT_PATH = _ACTIVE_DATA_DIR / "daily_sentiment.parquet"
DEFAULT_REPORT_DIR = _PROJECT_ROOT / "ml" / "reports"


Severity = Literal["PASS", "INFO", "WARN", "FAIL"]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class ValidationReport:
    timestamp: datetime
    base_path: Path
    prices_path: Optional[Path]
    sentiment_path: Optional[Path]
    as_of_date: date
    checks: List[CheckResult] = field(default_factory=list)
    metrics: Dict[str, object] = field(default_factory=dict)

    @property
    def counts(self) -> Dict[str, int]:
        counter = {"PASS": 0, "INFO": 0, "WARN": 0, "FAIL": 0}
        for c in self.checks:
            counter[c.severity] += 1
        return counter

    @property
    def has_failures(self) -> bool:
        return any(c.severity == "FAIL" for c in self.checks)

    @property
    def overall_status(self) -> Literal["PASS", "FAIL"]:
        return "FAIL" if self.has_failures else "PASS"

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)


# ---------------------------------------------------------------------------
# Helper: normalized key comparison (reuse TASK 105 logic)
# ---------------------------------------------------------------------------

def _normalize_ticker(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.upper()


def _normalize_trading_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_convert(None).dt.normalize().astype("datetime64[ns]")
    return parsed.dt.normalize().astype("datetime64[ns]")


def _missing_columns(df: pd.DataFrame, required: List[str]) -> List[str]:
    return [c for c in required if c not in df.columns]


# ---------------------------------------------------------------------------
# Checkpoint A — Schema and structure
# ---------------------------------------------------------------------------

def validate_schema_structure(
    base: pd.DataFrame, prices: Optional[pd.DataFrame]
) -> List[CheckResult]:
    """Schema, dtypes, keys — structural integrity."""
    checks: List[CheckResult] = []

    missing = _missing_columns(base, EXPECTED_COLUMNS)
    if missing:
        checks.append(CheckResult(
            "column_order", "FAIL",
            "Required columns missing from dataset",
            {"missing": missing, "expected": EXPECTED_COLUMNS},
        ))
        checks.append(CheckResult(
            "dtypes", "FAIL",
            "Cannot validate dtypes because required columns are missing",
            {"missing": missing},
        ))
        checks.append(CheckResult(
            "dataset_non_empty", "FAIL",
            "Cannot validate dataset structure because required columns are missing",
            {"missing": missing},
        ))
        return checks

    # A1. Column count
    if len(base.columns) == len(EXPECTED_COLUMNS):
        checks.append(CheckResult(
            "column_count", "PASS",
            f"{len(base.columns)} columns as expected",
        ))
    else:
        checks.append(CheckResult(
            "column_count", "FAIL",
            f"Expected {len(EXPECTED_COLUMNS)} columns, got {len(base.columns)}",
            {"expected": len(EXPECTED_COLUMNS), "actual": len(base.columns)},
        ))

    # A2. Column names + order
    if list(base.columns) == EXPECTED_COLUMNS:
        checks.append(CheckResult(
            "column_order", "PASS",
            "Column names and order match contract",
        ))
    else:
        missing = [c for c in EXPECTED_COLUMNS if c not in base.columns]
        extra = [c for c in base.columns if c not in EXPECTED_COLUMNS]
        wrong_order = (
            not missing and not extra
            and list(base.columns) != EXPECTED_COLUMNS
        )
        checks.append(CheckResult(
            "column_order", "FAIL",
            "Column names or order mismatch",
            {"missing": missing, "extra": extra, "wrong_order": wrong_order},
        ))

    # A3. Duplicate column names
    dup_cols = base.columns[base.columns.duplicated()].tolist()
    if not dup_cols:
        checks.append(CheckResult(
            "no_duplicate_columns", "PASS",
            "No duplicate column names",
        ))
    else:
        checks.append(CheckResult(
            "no_duplicate_columns", "FAIL",
            f"Duplicate column names: {dup_cols}",
        ))

    # A4. Dtypes compatibility
    dtype_issues = _check_dtypes(base)
    if not dtype_issues:
        checks.append(CheckResult(
            "dtypes", "PASS", "All dtypes compatible with contract",
        ))
    else:
        checks.append(CheckResult(
            "dtypes", "FAIL",
            f"{len(dtype_issues)} dtype mismatch(es)",
            {"issues": dtype_issues},
        ))

    # A5. Empty dataset
    if len(base) > 0:
        checks.append(CheckResult(
            "dataset_non_empty", "PASS",
            f"{len(base)} rows present",
        ))
    else:
        checks.append(CheckResult(
            "dataset_non_empty", "FAIL",
            "Dataset is empty — nothing to validate",
        ))

    # A6. No NULL ticker
    if base["ticker"].isna().any():
        n = int(base["ticker"].isna().sum())
        checks.append(CheckResult(
            "no_null_ticker", "FAIL",
            f"{n} NULL ticker value(s)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "no_null_ticker", "PASS", "No NULL ticker values",
        ))

    # A7. No empty ticker
    empty_mask = base["ticker"].astype(str).str.strip().eq("")
    if empty_mask.any():
        n = int(empty_mask.sum())
        checks.append(CheckResult(
            "no_empty_ticker", "FAIL",
            f"{n} empty/whitespace-only ticker(s)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "no_empty_ticker", "PASS", "No empty ticker values",
        ))

    # A8. No NULL trading_date
    if base["trading_date"].isna().any():
        n = int(base["trading_date"].isna().sum())
        checks.append(CheckResult(
            "no_null_trading_date", "FAIL",
            f"{n} NULL trading_date value(s)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "no_null_trading_date", "PASS", "No NULL trading_date values",
        ))

    # A9. trading_date is tz-naive
    if isinstance(base["trading_date"].dtype, pd.DatetimeTZDtype):
        checks.append(CheckResult(
            "trading_date_tz_naive", "FAIL",
            "trading_date is timezone-aware; contract requires tz-naive",
        ))
    else:
        checks.append(CheckResult(
            "trading_date_tz_naive", "PASS",
            "trading_date is timezone-naive",
        ))

    # A10. No duplicate keys
    dup_mask = base.duplicated(subset=KEY_COLUMNS, keep=False)
    if not dup_mask.any():
        checks.append(CheckResult(
            "unique_keys", "PASS",
            "All (ticker, trading_date) pairs are unique",
        ))
    else:
        n = int(dup_mask.sum())
        sample = (
            base.loc[dup_mask, KEY_COLUMNS]
            .head(5)
            .to_dict(orient="records")
        )
        checks.append(CheckResult(
            "unique_keys", "FAIL",
            f"{n} duplicate (ticker, trading_date) row(s)",
            {"count": n, "sample": sample},
        ))

    # A11. Row count consistency (if prices available)
    if prices is not None:
        if len(base) == len(prices):
            checks.append(CheckResult(
                "row_count_vs_prices", "PASS",
                f"Output rows ({len(base)}) == prices rows ({len(prices)})",
            ))
        else:
            checks.append(CheckResult(
                "row_count_vs_prices", "FAIL",
                f"Output rows ({len(base)}) != prices rows ({len(prices)})",
                {"output": len(base), "prices": len(prices)},
            ))

        # A12. Key set equality
        prices_norm = prices.copy()
        prices_norm["ticker"] = _normalize_ticker(prices_norm["ticker"])
        prices_norm["trading_date"] = _normalize_trading_date(
            prices_norm["trading_date"]
        )
        price_keys = set(zip(prices_norm["ticker"], prices_norm["trading_date"]))
        base_keys = set(zip(base["ticker"], base["trading_date"]))
        if price_keys == base_keys:
            checks.append(CheckResult(
                "key_set_equality", "PASS",
                "Output key set matches normalized prices key set",
            ))
        else:
            missing = price_keys - base_keys
            extra = base_keys - price_keys
            checks.append(CheckResult(
                "key_set_equality", "FAIL",
                "Output key set does not match prices key set",
                {
                    "missing_from_output": len(missing),
                    "extra_in_output": len(extra),
                    "sample_missing": list(missing)[:3],
                    "sample_extra": list(extra)[:3],
                },
            ))

        # A13. Price values unchanged
        missing_price_cols = _missing_columns(
            prices_norm, KEY_COLUMNS + PRICE_VALUE_COLUMNS,
        )
        if missing_price_cols:
            checks.append(CheckResult(
                "price_values_unchanged", "FAIL",
                "Prices artifact missing required comparison columns",
                {"missing": missing_price_cols},
            ))
        else:
            merged = base[KEY_COLUMNS + PRICE_VALUE_COLUMNS].merge(
                prices_norm[KEY_COLUMNS + PRICE_VALUE_COLUMNS],
                on=KEY_COLUMNS,
                suffixes=("_b", "_p"),
                how="inner",
                validate="one_to_one",
            )
            mismatches: Dict[str, int] = {}
            for col in PRICE_VALUE_COLUMNS:
                left = merged[f"{col}_b"]
                right = merged[f"{col}_p"]
                diff = ~(left.eq(right) | (left.isna() & right.isna()))
                if diff.any():
                    mismatches[col] = int(diff.sum())
            if not mismatches:
                checks.append(CheckResult(
                    "price_values_unchanged", "PASS",
                    "All price values match source prices artifact",
                ))
            else:
                checks.append(CheckResult(
                    "price_values_unchanged", "FAIL",
                    f"Price values changed in {len(mismatches)} column(s)",
                    {"mismatches": mismatches},
                ))

    return checks


def _check_dtypes(base: pd.DataFrame) -> List[Dict[str, str]]:
    """Return list of dtype mismatches. Empty = all compatible.

    Defensive: skips columns that don't exist (missing-column case is
    reported separately by the column_order check).
    """
    issues: List[Dict[str, str]] = []

    # string-like: ticker
    if "ticker" in base.columns and not pd.api.types.is_string_dtype(base["ticker"]):
        issues.append({
            "column": "ticker",
            "expected": "string",
            "actual": str(base["ticker"].dtype),
        })

    # datetime: trading_date
    if "trading_date" in base.columns and not pd.api.types.is_datetime64_any_dtype(
        base["trading_date"]
    ):
        issues.append({
            "column": "trading_date",
            "expected": "datetime64",
            "actual": str(base["trading_date"].dtype),
        })

    # float: open/high/low/close, return_1d, sentiment fields
    for col in ["open", "high", "low", "close", "return_1d",
                "sentiment_mean", "sentiment_std",
                "positive_ratio", "negative_ratio", "availability_live_ratio"]:
        if col in base.columns and not pd.api.types.is_float_dtype(base[col]):
            issues.append({
                "column": col,
                "expected": "float",
                "actual": str(base[col].dtype),
            })

    # integer: volume, news_count
    for col in ["volume", "news_count"]:
        if col in base.columns and not pd.api.types.is_integer_dtype(base[col]):
            issues.append({
                "column": col,
                "expected": "integer",
                "actual": str(base[col].dtype),
            })

    return issues


# ---------------------------------------------------------------------------
# Checkpoint B — Price integrity (OHLC invariants)
# ---------------------------------------------------------------------------

def validate_price_integrity(base: pd.DataFrame) -> List[CheckResult]:
    """OHLC invariants, positivity, finiteness, volume."""
    checks: List[CheckResult] = []

    missing = _missing_columns(base, list(dict.fromkeys(OHLC_COLUMNS + ["volume"])))
    if missing:
        checks.append(CheckResult(
            "high_ge_open_close", "FAIL",
            "Missing required price columns for integrity checks",
            {"missing": missing},
        ))
        return checks

    # B1. All OHLC finite
    for col in OHLC_COLUMNS:
        vals = base[col].astype(float)
        non_finite = ~np.isfinite(vals)
        if non_finite.any():
            n = int(non_finite.sum())
            checks.append(CheckResult(
                f"ohlc_finite_{col}", "FAIL",
                f"{col} has {n} non-finite value(s) (inf/nan)",
                {"count": n},
            ))
        else:
            checks.append(CheckResult(
                f"ohlc_finite_{col}", "PASS",
                f"{col} all finite",
            ))

    # B2. All OHLC positive
    for col in OHLC_COLUMNS:
        vals = base[col].astype(float)
        non_positive = (vals <= 0)
        if non_positive.any():
            n = int(non_positive.sum())
            checks.append(CheckResult(
                f"ohlc_positive_{col}", "FAIL",
                f"{col} has {n} value(s) <= 0",
                {"count": n},
            ))
        else:
            checks.append(CheckResult(
                f"ohlc_positive_{col}", "PASS",
                f"{col} all positive",
            ))

    # B3. High >= max(open, close)
    violations = base["high"] < base[["open", "close"]].max(axis=1)
    if violations.any():
        n = int(violations.sum())
        checks.append(CheckResult(
            "high_ge_open_close", "FAIL",
            f"{n} row(s) where high < max(open, close)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "high_ge_open_close", "PASS",
            "high >= max(open, close) for all rows",
        ))

    # B4. Low <= min(open, close)
    violations = base["low"] > base[["open", "close"]].min(axis=1)
    if violations.any():
        n = int(violations.sum())
        checks.append(CheckResult(
            "low_le_open_close", "FAIL",
            f"{n} row(s) where low > min(open, close)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "low_le_open_close", "PASS",
            "low <= min(open, close) for all rows",
        ))

    # B5. High >= low
    violations = base["high"] < base["low"]
    if violations.any():
        n = int(violations.sum())
        checks.append(CheckResult(
            "high_ge_low", "FAIL",
            f"{n} row(s) where high < low",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "high_ge_low", "PASS",
            "high >= low for all rows",
        ))

    # B6. Volume non-negative
    neg_vol = (base["volume"] < 0).sum()
    if neg_vol > 0:
        checks.append(CheckResult(
            "volume_non_negative", "FAIL",
            f"{int(neg_vol)} row(s) with negative volume",
        ))
    else:
        checks.append(CheckResult(
            "volume_non_negative", "PASS",
            "All volume >= 0",
        ))

    return checks


# ---------------------------------------------------------------------------
# Checkpoint C — Sentiment integrity
# ---------------------------------------------------------------------------

def validate_sentiment_integrity(base: pd.DataFrame) -> List[CheckResult]:
    """news_count, no-news semantics, matched field rules, ratios."""
    checks: List[CheckResult] = []

    required = ["news_count"] + SENTIMENT_FEATURE_COLUMNS
    missing = _missing_columns(base, required)
    if missing:
        checks.append(CheckResult(
            "news_count_numeric", "FAIL",
            "Missing required sentiment columns for integrity checks",
            {"missing": missing},
        ))
        return checks

    # C1. news_count numeric
    coerced_news = pd.to_numeric(base["news_count"], errors="coerce")
    invalid_numeric = base["news_count"].notna() & coerced_news.isna()
    if not pd.api.types.is_numeric_dtype(base["news_count"]) or invalid_numeric.any():
        checks.append(CheckResult(
            "news_count_numeric", "FAIL",
            f"news_count dtype is {base['news_count'].dtype}, expected numeric",
            {"invalid_values": int(invalid_numeric.sum())},
        ))
    else:
        checks.append(CheckResult(
            "news_count_numeric", "PASS", "news_count is numeric",
        ))

    # C2. news_count finite
    nc_vals = coerced_news.astype(float)
    non_finite = ~np.isfinite(nc_vals)
    if non_finite.any():
        n = int(non_finite.sum())
        checks.append(CheckResult(
            "news_count_finite", "FAIL",
            f"{n} non-finite news_count value(s)",
            {"count": n},
        ))
    else:
        checks.append(CheckResult(
            "news_count_finite", "PASS", "news_count all finite",
        ))

    # C3. news_count non-negative
    negative = (base["news_count"] < 0).sum()
    if negative > 0:
        checks.append(CheckResult(
            "news_count_non_negative", "FAIL",
            f"{int(negative)} row(s) with negative news_count",
        ))
    else:
        checks.append(CheckResult(
            "news_count_non_negative", "PASS",
            "news_count >= 0 for all rows",
        ))

    # C4. news_count integer-valued
    non_integer = (nc_vals != nc_vals.astype("int64").astype("float64")).sum()
    if non_integer > 0:
        checks.append(CheckResult(
            "news_count_integer", "FAIL",
            f"{int(non_integer)} row(s) with fractional news_count",
        ))
    else:
        checks.append(CheckResult(
            "news_count_integer", "PASS",
            "news_count all integer-valued",
        ))

    # C5. No-news rows: all 5 sentiment features NULL
    no_news = base[base["news_count"] == 0]
    if len(no_news) > 0:
        violations: Dict[str, int] = {}
        for col in SENTIMENT_FEATURE_COLUMNS:
            n_non_null = int(no_news[col].notna().sum())
            if n_non_null > 0:
                violations[col] = n_non_null
        if violations:
            checks.append(CheckResult(
                "no_news_semantics", "FAIL",
                f"No-news rows contain non-NULL sentiment values in "
                f"{len(violations)} feature(s)",
                {"violations": violations, "no_news_rows": len(no_news)},
            ))
        else:
            checks.append(CheckResult(
                "no_news_semantics", "PASS",
                f"{len(no_news)} no-news rows have all 5 sentiment fields NULL",
            ))
    else:
        checks.append(CheckResult(
            "no_news_semantics", "INFO",
            "No no-news rows (all rows have news_count > 0)",
        ))

    # C6. Matched rows: required fields non-null
    matched = base[base["news_count"] > 0]
    if len(matched) > 0:
        null_fields: Dict[str, int] = {}
        for col in MATCHED_REQUIRED_FINITE:
            n_null = int(matched[col].isna().sum())
            if n_null > 0:
                null_fields[col] = n_null
        if null_fields:
            checks.append(CheckResult(
                "matched_required_non_null", "FAIL",
                f"Matched rows have NULL required fields",
                {"null_fields": null_fields, "matched_rows": len(matched)},
            ))
        else:
            checks.append(CheckResult(
                "matched_required_non_null", "PASS",
                f"All {len(matched)} matched rows have required fields populated",
            ))

        # C7. Matched required fields finite
        non_finite_fields: Dict[str, int] = {}
        for col in MATCHED_REQUIRED_FINITE:
            vals = matched[col].dropna().astype(float)
            if len(vals) > 0:
                n_nonfinite = int((~np.isfinite(vals)).sum())
                if n_nonfinite > 0:
                    non_finite_fields[col] = n_nonfinite
        if non_finite_fields:
            checks.append(CheckResult(
                "matched_required_finite", "FAIL",
                f"Matched rows have non-finite required fields",
                {"non_finite_fields": non_finite_fields},
            ))
        else:
            checks.append(CheckResult(
                "matched_required_finite", "PASS",
                "All matched required fields finite",
            ))

        # C8. Ratios in [0, 1]
        ratio_violations: Dict[str, int] = {}
        for col in RATIO_COLUMNS:
            vals = matched[col].dropna().astype(float)
            if len(vals) > 0:
                out = ((vals < 0) | (vals > 1)).sum()
                if out > 0:
                    ratio_violations[col] = int(out)
        if ratio_violations:
            checks.append(CheckResult(
                "ratio_in_range", "FAIL",
                f"Ratio fields outside [0, 1]",
                {"violations": ratio_violations},
            ))
        else:
            checks.append(CheckResult(
                "ratio_in_range", "PASS",
                "All ratio fields within [0, 1]",
            ))

        # C9. sentiment_std rules
        std_issues = _check_sentiment_std(matched)
        if std_issues["fail"]:
            checks.append(CheckResult(
                "sentiment_std_rules", "FAIL",
                std_issues["message"],
                std_issues["details"],
            ))
        else:
            checks.append(CheckResult(
                "sentiment_std_rules", "PASS",
                "sentiment_std rules satisfied",
            ))

    # C10. Reconciliation (matched + unmatched == total)
    n_matched = int((base["news_count"] > 0).sum())
    n_unmatched = int((base["news_count"] == 0).sum())
    if n_matched + n_unmatched == len(base):
        checks.append(CheckResult(
            "row_reconciliation", "PASS",
            f"{n_matched} matched + {n_unmatched} unmatched = {len(base)} total",
        ))
    else:
        checks.append(CheckResult(
            "row_reconciliation", "FAIL",
            f"Reconciliation failed: {n_matched} + {n_unmatched} != {len(base)}",
        ))

    return checks


def _check_sentiment_std(matched: pd.DataFrame) -> Dict[str, object]:
    """Apply TASK 104 rules: n=1 NaN ok / finite-if-present; n>1 must be finite and >=0."""
    fail = False
    messages: List[str] = []
    details: Dict[str, int] = {}

    # n == 1: NaN allowed; non-null must be finite and non-negative
    single = matched[matched["news_count"] == 1]
    if len(single) > 0:
        std_non_null = single["sentiment_std"].dropna().astype(float)
        if len(std_non_null) > 0:
            non_finite = (~np.isfinite(std_non_null)).sum()
            negative = (std_non_null < 0).sum()
            if non_finite > 0:
                fail = True
                messages.append(f"{int(non_finite)} non-finite std in single-article rows")
                details["single_non_finite"] = int(non_finite)
            if negative > 0:
                fail = True
                messages.append(f"{int(negative)} negative std in single-article rows")
                details["single_negative"] = int(negative)

    # n > 1: must be non-null, finite, non-negative
    multi = matched[matched["news_count"] > 1]
    if len(multi) > 0:
        std_vals = multi["sentiment_std"]
        n_null = int(std_vals.isna().sum())
        if n_null > 0:
            fail = True
            messages.append(f"{n_null} NULL std where news_count > 1")
            details["multi_null"] = n_null
        std_non_null = std_vals.dropna().astype(float)
        if len(std_non_null) > 0:
            non_finite = (~np.isfinite(std_non_null)).sum()
            negative = (std_non_null < 0).sum()
            if non_finite > 0:
                fail = True
                messages.append(f"{int(non_finite)} non-finite std where news_count > 1")
                details["multi_non_finite"] = int(non_finite)
            if negative > 0:
                fail = True
                messages.append(f"{int(negative)} negative std where news_count > 1")
                details["multi_negative"] = int(negative)

    return {
        "fail": fail,
        "message": "; ".join(messages) if messages else "OK",
        "details": details,
    }


def validate_exchange_calendar(
    base: pd.DataFrame, calendar_source=None
) -> List[CheckResult]:
    """Fail if canonical rows fall on a verified exchange closure."""
    checks: List[CheckResult] = []
    if "trading_date" not in base.columns or base.empty:
        return checks
    source = calendar_source or load_calendar_source()
    frame = base[["trading_date"]].copy()
    frame["day"] = pd.to_datetime(frame["trading_date"]).dt.date
    statuses = {day: exchange_day_status(day, source) for day in frame["day"].unique()}
    closure_days = [day for day, status in statuses.items() if status == OFFICIAL_CLOSURE]
    weekend_days = [day for day, status in statuses.items() if status == WEEKEND]
    unverified_days = [day for day, status in statuses.items() if status == UNVERIFIED]

    if closure_days:
        closure_set = set(closure_days)
        rows = frame[frame["day"].isin(closure_set)]
        checks.append(CheckResult(
            "exchange_calendar_integrity", "FAIL",
            f"{len(rows)} row(s) fall on {len(closure_days)} verified exchange "
            f"closure date(s): {[str(day) for day in sorted(closure_set)]}",
            {"closure_dates": [str(day) for day in sorted(closure_set)],
             "rows": int(len(rows))},
        ))
    elif weekend_days:
        checks.append(CheckResult(
            "exchange_calendar_integrity", "FAIL",
            f"Rows found on weekend date(s): {[str(day) for day in sorted(weekend_days)]}",
        ))
    elif unverified_days:
        checks.append(CheckResult(
            "exchange_calendar_integrity", "WARN",
            f"{len(unverified_days)} date(s) are outside authoritative calendar "
            f"coverage: {[str(day) for day in sorted(unverified_days)[:5]]}",
        ))
    else:
        checks.append(CheckResult(
            "exchange_calendar_integrity", "PASS",
            f"No rows on official closures; calendar {source.calendar_version}",
        ))
    return checks


# ---------------------------------------------------------------------------
# Checkpoint D — Consistency and coverage
# ---------------------------------------------------------------------------

def compute_consistency_metrics(
    base: pd.DataFrame,
    prices: Optional[pd.DataFrame],
    sentiment: Optional[pd.DataFrame],
) -> Dict[str, object]:
    """Compute descriptive metrics (INFO-level, not pass/fail)."""
    metrics: Dict[str, object] = {}

    metrics["total_output_rows"] = len(base)
    if "ticker" in base.columns:
        metrics["ticker_count"] = int(base["ticker"].nunique())
    else:
        metrics["ticker_count"] = 0
    if len(base) and "trading_date" in base.columns:
        metrics["date_min"] = str(base["trading_date"].min().date()) if len(base) else None
        metrics["date_max"] = str(base["trading_date"].max().date()) if len(base) else None
    else:
        metrics["date_min"] = None
        metrics["date_max"] = None

    if "news_count" in base.columns:
        n_matched = int((base["news_count"] > 0).sum())
        n_unmatched = int((base["news_count"] == 0).sum())
    else:
        n_matched = 0
        n_unmatched = 0
    metrics["matched_rows"] = n_matched
    metrics["unmatched_rows"] = n_unmatched
    metrics["matched_pct"] = (n_matched / len(base) * 100) if len(base) else 0.0
    metrics["unmatched_pct"] = (n_unmatched / len(base) * 100) if len(base) else 0.0

    # Coverage by ticker
    if len(base) > 0 and "ticker" in base.columns and "news_count" in base.columns:
        by_ticker = base.groupby("ticker").agg(
            total=("news_count", "size"),
            matched=("news_count", lambda s: int((s > 0).sum())),
        )
        by_ticker["pct"] = by_ticker["matched"] / by_ticker["total"] * 100
        metrics["coverage_by_ticker"] = by_ticker.to_dict(orient="index")

    # Orphan sentiment keys (in sentiment but not in normalized prices)
    if sentiment is not None and prices is not None and len(sentiment) > 0:
        sent_keys = set()
        price_keys = set()
        if {"ticker", "trading_date"}.issubset(sentiment.columns):
            sent_norm = sentiment.copy()
            sent_norm["ticker"] = _normalize_ticker(sent_norm["ticker"])
            sent_norm["trading_date"] = _normalize_trading_date(sent_norm["trading_date"])
            sent_keys = set(zip(sent_norm["ticker"], sent_norm["trading_date"]))
        if {"ticker", "trading_date"}.issubset(prices.columns):
            prices_norm = prices.copy()
            prices_norm["ticker"] = _normalize_ticker(prices_norm["ticker"])
            prices_norm["trading_date"] = _normalize_trading_date(prices_norm["trading_date"])
            price_keys = set(zip(prices_norm["ticker"], prices_norm["trading_date"]))
        orphan_keys = sent_keys - price_keys
        metrics["orphan_sentiment_keys"] = len(orphan_keys)
        metrics["orphan_sample"] = list(orphan_keys)[:5]

    # Missing value counts per sentiment feature
    missing: Dict[str, int] = {}
    for col in SENTIMENT_FEATURE_COLUMNS:
        if col in base.columns:
            missing[col] = int(base[col].isna().sum())
    metrics["missing_by_feature"] = missing

    # Dtype map
    metrics["dtypes"] = {str(k): str(v) for k, v in base.dtypes.items()}

    return metrics


# ---------------------------------------------------------------------------
# Checkpoint E — Freshness
# ---------------------------------------------------------------------------

def validate_freshness(
    base: pd.DataFrame, as_of_date: date, threshold_days: int
) -> Tuple[List[CheckResult], Dict[str, object]]:
    """Freshness = latest trading_date vs as_of_date (calendar days)."""
    checks: List[CheckResult] = []
    metrics: Dict[str, object] = {}

    if len(base) == 0:
        checks.append(CheckResult(
            "freshness", "INFO",
            "Cannot assess freshness — dataset is empty",
        ))
        return checks, metrics

    if "trading_date" not in base.columns:
        checks.append(CheckResult(
            "freshness", "FAIL",
            "Missing required trading_date column for freshness validation",
            {"missing": ["trading_date"]},
        ))
        return checks, metrics

    parsed = pd.to_datetime(base["trading_date"], errors="coerce")
    if parsed.notna().sum() == 0:
        checks.append(CheckResult(
            "freshness", "FAIL",
            "No valid trading_date values available for freshness validation",
            {"invalid_count": int(base["trading_date"].isna().sum())},
        ))
        return checks, metrics

    latest = parsed.max()
    latest_date = latest.date() if hasattr(latest, "date") else latest
    age_days = (as_of_date - latest_date).days

    metrics["as_of_date"] = str(as_of_date)
    metrics["latest_trading_date"] = str(latest_date)
    metrics["age_days"] = age_days
    metrics["freshness_threshold_days"] = threshold_days

    if age_days <= threshold_days:
        checks.append(CheckResult(
            "freshness", "PASS",
            f"Latest trading_date {latest_date} is {age_days} day(s) old "
            f"(threshold {threshold_days})",
        ))
    else:
        checks.append(CheckResult(
            "freshness", "WARN",
            f"Dataset is {age_days} calendar day(s) behind as_of_date "
            f"{as_of_date} (threshold {threshold_days}). "
            f"Not a structural failure — refresh data before research use.",
            {"age_days": age_days, "threshold_days": threshold_days},
        ))

    return checks, metrics


# ---------------------------------------------------------------------------
# Coverage warning (INFO/WARN, not FAIL)
# ---------------------------------------------------------------------------

def build_coverage_check(metrics: Dict[str, object]) -> CheckResult:
    """One WARN if coverage < 100%, else INFO. Never FAIL."""
    matched_pct = metrics.get("matched_pct", 0.0)
    n_matched = metrics.get("matched_rows", 0)
    total = metrics.get("total_output_rows", 0)

    if n_matched == 0:
        return CheckResult(
            "sentiment_coverage", "WARN",
            "Zero sentiment coverage — all rows are no-news. "
            "Hybrid model features unavailable for research until "
            "sentiment pipeline coverage improves.",
        )
    if matched_pct < 100:
        return CheckResult(
            "sentiment_coverage", "WARN",
            f"Sentiment coverage {matched_pct:.4f}% ({n_matched}/{total}). "
            f"Sparse coverage is a research-readiness limitation, not a "
            f"structural failure. Do not fill NULLs with zero.",
        )
    return CheckResult(
        "sentiment_coverage", "PASS",
        f"Sentiment coverage 100% ({n_matched}/{total})",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_checks(
    base_path: Path = DEFAULT_BASE_PATH,
    prices_path: Optional[Path] = DEFAULT_PRICES_PATH,
    sentiment_path: Optional[Path] = DEFAULT_SENTIMENT_PATH,
    as_of_date: Optional[date] = None,
    freshness_threshold_days: int = DEFAULT_FRESHNESS_THRESHOLD_DAYS,
) -> ValidationReport:
    """
    Run full validation. Raises FileNotFoundError if base_path missing.
    Auxiliary artifacts (prices, sentiment) optional — skipped gracefully.
    """
    if not base_path.exists():
        raise FileNotFoundError(f"Base dataset not found: {base_path}")

    base = pd.read_parquet(base_path)
    logger.info("Loaded base dataset: %d rows from %s", len(base), base_path)

    prices: Optional[pd.DataFrame] = None
    if prices_path is not None and prices_path.exists():
        prices = pd.read_parquet(prices_path)
        logger.info("Loaded prices: %d rows from %s", len(prices), prices_path)
    else:
        logger.warning("Prices artifact not available; skipping cross-checks")

    sentiment: Optional[pd.DataFrame] = None
    if sentiment_path is not None and sentiment_path.exists():
        sentiment = pd.read_parquet(sentiment_path)
        logger.info("Loaded sentiment: %d rows", len(sentiment))

    if as_of_date is None:
        as_of_date = date.today()

    report = ValidationReport(
        timestamp=datetime.now(timezone.utc),
        base_path=base_path,
        prices_path=prices_path if prices is not None else None,
        sentiment_path=sentiment_path if sentiment is not None else None,
        as_of_date=as_of_date,
    )

    # Checkpoint A
    for c in validate_schema_structure(base, prices):
        report.add(c)

    # Checkpoint B
    for c in validate_price_integrity(base):
        report.add(c)
    for c in validate_exchange_calendar(base):
        report.add(c)

    # Checkpoint C
    for c in validate_sentiment_integrity(base):
        report.add(c)

    # Checkpoint D — metrics only
    report.metrics = compute_consistency_metrics(base, prices, sentiment)
    report.add(build_coverage_check(report.metrics))

    # Checkpoint E — freshness
    fresh_checks, fresh_metrics = validate_freshness(
        base, as_of_date, freshness_threshold_days
    )
    for c in fresh_checks:
        report.add(c)
    report.metrics.update(fresh_metrics)

    # Audit: file mtimes (INFO only, not verdict)
    report.metrics["file_mtimes"] = {
        "base_dataset": _mtime(base_path),
        "prices_clean": _mtime(prices_path) if prices_path else None,
        "daily_sentiment": _mtime(sentiment_path) if sentiment_path else None,
    }

    return report


def _mtime(p: Optional[Path]) -> Optional[str]:
    if p is None or not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_markdown(report: ValidationReport) -> str:
    lines: List[str] = []
    lines.append("# FinSentiMind — Dataset Validation Report")
    lines.append("")
    lines.append(f"**Generated:** {report.timestamp.isoformat()}")
    lines.append(f"**Base dataset:** `{report.base_path}`")
    if report.prices_path:
        lines.append(f"**Prices source:** `{report.prices_path}`")
    if report.sentiment_path:
        lines.append(f"**Sentiment source:** `{report.sentiment_path}`")
    lines.append(f"**As-of date:** {report.as_of_date}")
    lines.append("")

    # Status block
    counts = report.counts
    lines.append("## Overall Status")
    lines.append("")
    status = "PASS" if not report.has_failures else "FAIL"
    emoji = "✅" if status == "PASS" else "❌"
    lines.append(f"**Structural validity: {emoji} {status}**")
    lines.append("")
    lines.append(f"- PASS: {counts['PASS']}")
    lines.append(f"- INFO: {counts['INFO']}")
    lines.append(f"- WARN: {counts['WARN']}")
    lines.append(f"- FAIL: {counts['FAIL']}")
    lines.append("")

    # Group checks by checkpoint
    sections = [
        ("Schema & Structure", [
            c for c in report.checks if c.name in _SCHEMA_CHECKS
        ]),
        ("Price Integrity (OHLCV)", [
            c for c in report.checks if c.name in _PRICE_CHECKS
        ]),
        ("Sentiment Integrity", [
            c for c in report.checks if c.name in _SENTIMENT_CHECKS
        ]),
        ("Coverage & Freshness", [
            c for c in report.checks if c.name in _COVERAGE_CHECKS
        ]),
    ]

    for section_title, section_checks in sections:
        lines.append(f"## {section_title}")
        lines.append("")
        lines.append("| Check | Result | Details |")
        lines.append("|---|---|---|")
        for c in section_checks:
            icon = _icon(c.severity)
            lines.append(f"| {c.name} | {icon} {c.severity} | {_escape(c.message)} |")
        lines.append("")

    # Metrics
    if report.metrics:
        lines.append("## Dataset Metrics")
        lines.append("")
        _render_metrics(lines, report.metrics)
        lines.append("")

    # Failures + warnings
    failures = [c for c in report.checks if c.severity == "FAIL"]
    warnings = [c for c in report.checks if c.severity == "WARN"]

    if failures:
        lines.append("## Blocking Failures")
        lines.append("")
        for c in failures:
            lines.append(f"- **{c.name}**: {c.message}")
        lines.append("")

    if warnings:
        lines.append("## Warnings (non-blocking)")
        lines.append("")
        for c in warnings:
            lines.append(f"- **{c.name}**: {c.message}")
        lines.append("")

    # Dual verdict
    lines.append("## Verdict")
    lines.append("")
    if not report.has_failures:
        lines.append("- **Structural validity:** ✅ PASS")
    else:
        lines.append(f"- **Structural validity:** ❌ FAIL ({len(failures)} blocking issue(s))")

    matched_pct = report.metrics.get("matched_pct", 0.0)
    if matched_pct >= 100:
        lines.append("- **Modeling readiness:** Not evaluated (structural validation only)")
    else:
        lines.append(
            f"- **Modeling readiness:** ⚠️ Sentiment coverage {matched_pct:.4f}%. "
            f"Structural validity is NOT a claim of predictive utility. "
            f"Address coverage via source pipeline audit (not by filling NULLs) "
            f"before relying on sentiment features."
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_Report generated by independent validator. No data modified._")
    return "\n".join(lines)


_SCHEMA_CHECKS = {
    "column_count", "column_order", "no_duplicate_columns", "dtypes",
    "dataset_non_empty", "no_null_ticker", "no_empty_ticker",
    "no_null_trading_date", "trading_date_tz_naive", "unique_keys",
    "row_count_vs_prices", "key_set_equality", "price_values_unchanged",
}

_PRICE_CHECKS = {
    "ohlc_finite_open", "ohlc_finite_high", "ohlc_finite_low", "ohlc_finite_close",
    "ohlc_positive_open", "ohlc_positive_high", "ohlc_positive_low", "ohlc_positive_close",
    "high_ge_open_close", "low_le_open_close", "high_ge_low", "volume_non_negative",
}

_SENTIMENT_CHECKS = {
    "news_count_numeric", "news_count_finite", "news_count_non_negative",
    "news_count_integer", "no_news_semantics", "matched_required_non_null",
    "matched_required_finite", "ratio_in_range", "sentiment_std_rules",
    "row_reconciliation",
}

_COVERAGE_CHECKS = {
    "sentiment_coverage", "freshness",
}


def _icon(sev: Severity) -> str:
    return {"PASS": "✅", "INFO": "ℹ️", "WARN": "⚠️", "FAIL": "❌"}[sev]


def _escape(s: str) -> str:
    return s.replace("|", "\\|")


def _render_metrics(lines: List[str], m: Dict[str, object]) -> None:
    keys = [
        ("total_output_rows", "Total output rows"),
        ("matched_rows", "Matched rows (news_count > 0)"),
        ("unmatched_rows", "Unmatched rows (news_count = 0)"),
        ("matched_pct", "Matched percentage"),
        ("unmatched_pct", "Unmatched percentage"),
        ("ticker_count", "Ticker count"),
        ("date_min", "Earliest trading_date"),
        ("date_max", "Latest trading_date"),
        ("as_of_date", "Validation as-of date"),
        ("age_days", "Age (calendar days)"),
        ("freshness_threshold_days", "Freshness threshold (days)"),
        ("orphan_sentiment_keys", "Orphan sentiment keys (not in prices)"),
    ]
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for key, label in keys:
        if key in m:
            lines.append(f"| {label} | {m[key]} |")
    lines.append("")

    missing = m.get("missing_by_feature")
    if isinstance(missing, dict):
        lines.append("### Missing Values per Sentiment Feature")
        lines.append("")
        lines.append("| Feature | NULL count |")
        lines.append("|---|---|")
        for feat, cnt in missing.items():
            lines.append(f"| {feat} | {cnt} |")
        lines.append("")

    mtimes = m.get("file_mtimes")
    if isinstance(mtimes, dict):
        lines.append("### File Modification Times (audit only)")
        lines.append("")
        lines.append("| Artifact | mtime (UTC) |")
        lines.append("|---|---|")
        for k, v in mtimes.items():
            lines.append(f"| {k} | {v if v else '(missing)'} |")
        lines.append("")


# ---------------------------------------------------------------------------
# Save (atomic)
# ---------------------------------------------------------------------------

def save_report_atomic(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.stem}.", suffix=".md.tmp", dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
        logger.info("Report saved to %s", path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_REPORT_DIR / f"dataset_validation_{stamp}.md"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Independent base dataset validator (TASK 106).",
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_BASE_PATH,
        help=f"Base dataset Parquet (default: {DEFAULT_BASE_PATH}).",
    )
    parser.add_argument(
        "--prices", type=Path, default=DEFAULT_PRICES_PATH,
        help="Prices source Parquet (default: TASK 105 default).",
    )
    parser.add_argument(
        "--sentiment", type=Path, default=DEFAULT_SENTIMENT_PATH,
        help="Sentiment source Parquet (default: TASK 104 default).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Report path (default: ml/reports/dataset_validation_<ts>.md).",
    )
    parser.add_argument(
        "--freshness-threshold-days", type=int,
        default=DEFAULT_FRESHNESS_THRESHOLD_DAYS,
        help=f"Freshness WARN threshold in calendar days "
             f"(default: {DEFAULT_FRESHNESS_THRESHOLD_DAYS}).",
    )
    parser.add_argument(
        "--as-of-date", type=str, default=None,
        help="Override as-of date (YYYY-MM-DD) for reproducible validation.",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Print report but do not write to disk.",
    )
    args = parser.parse_args(argv)

    setup_logging()

    as_of = None
    if args.as_of_date:
        try:
            as_of = date.fromisoformat(args.as_of_date)
        except ValueError as e:
            logger.error("Invalid --as-of-date: %s", e)
            return 2

    try:
        report = run_all_checks(
            base_path=args.input,
            prices_path=args.prices,
            sentiment_path=args.sentiment,
            as_of_date=as_of,
            freshness_threshold_days=args.freshness_threshold_days,
        )
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 2
    except Exception as e:
        logger.error("Validation failed unexpectedly: %s", e, exc_info=True)
        return 2

    md = render_markdown(report)
    print(md)

    if not args.no_save:
        output = args.output or _default_report_path()
        try:
            save_report_atomic(md, output)
        except Exception as e:
            logger.error("Failed to save report: %s", e, exc_info=True)
            return 2

    return 0 if not report.has_failures else 1


if __name__ == "__main__":
    sys.exit(main())