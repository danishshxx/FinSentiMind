"""Generation-level acceptance audit for immutable dataset generations.

The refresh pipeline validates schema and price integrity. This module adds a
generation-wide audit: artifact checksums, calendar-version consistency, row and
ticker reconciliation, future-session protection, and null semantics. It also
records artifact provenance so every derived file is traceable to the calendar
version, feature version, data date, row count and checksum that produced it.

The audit never mutates a dataset. ``write_provenance`` only writes a small
``provenance.json`` sidecar into a staging directory before publication.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import (
    OPTIONAL_ARTIFACTS,
    REQUIRED_ARTIFACTS,
    artifact_manifest,
    sha256_file,
    write_json_atomic,
)
from ml.preprocessing.idx_calendar import (
    OFFICIAL_CLOSURE,
    UNVERIFIED,
    WEEKEND,
    exchange_day_status,
    load_calendar_source,
)


logger = get_logger(__name__)

PROVENANCE_FILENAME = "provenance.json"
SCHEMA_VERSION = 1

# Sentiment fields that must stay NULL when a session has no matched news.
SENTIMENT_FIELDS = (
    "sentiment_mean",
    "sentiment_std",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
)
# Fields that must be populated on matched rows. ``sentiment_std`` is excluded:
# it is undefined for a single-article session and stays NULL by design.
MATCHED_REQUIRED_FIELDS = (
    "sentiment_mean",
    "positive_ratio",
    "negative_ratio",
    "availability_live_ratio",
)
PRICE_FIELDS = ("open", "high", "low", "close", "volume")


class GenerationAuditError(RuntimeError):
    """Raised when a generation cannot be audited at all."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_date(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _check(
    name: str,
    status: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }


def load_artifacts(directory: Path) -> Dict[str, pd.DataFrame]:
    """Read every required artifact and the optional quant artifact."""
    missing = [name for name in REQUIRED_ARTIFACTS if not (directory / name).is_file()]
    if missing:
        raise GenerationAuditError(
            f"Generation directory is incomplete ({directory}): missing {missing}"
        )
    frames: Dict[str, pd.DataFrame] = {}
    for name in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
        path = directory / name
        if path.is_file():
            frames[name] = pd.read_parquet(path)
    return frames


def _artifact_summary(directory: Path) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for name in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
        path = directory / name
        if not path.is_file():
            continue
        frame = pd.read_parquet(path)
        summary[name] = {
            "size_bytes": int(path.stat().st_size),
            "sha256": sha256_file(path),
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
        }
    return summary


def _audit_calendar(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    versions = (
        sorted({str(value) for value in frame["calendar_version"].dropna().unique()})
        if "calendar_version" in frame.columns
        else []
    )
    if len(versions) == 1:
        checks.append(_check(
            "calendar_version_single",
            "PASS",
            f"Trading calendar records a single calendar version: {versions[0]}",
            {"calendar_version": versions[0]},
        ))
    else:
        checks.append(_check(
            "calendar_version_single",
            "FAIL",
            f"Trading calendar has {len(versions)} calendar version(s): {versions}",
            {"calendar_versions": versions},
        ))
    return checks


def _audit_price_vs_calendar(
    prices: pd.DataFrame, calendar: pd.DataFrame
) -> List[Dict[str, Any]]:
    source = load_calendar_source()
    checks: List[Dict[str, Any]] = []
    price_dates = sorted({_to_date(value) for value in prices["trading_date"]})
    price_dates = [day for day in price_dates if day is not None]

    closures = [day for day in price_dates if exchange_day_status(day, source) == OFFICIAL_CLOSURE]
    weekends = [day for day in price_dates if exchange_day_status(day, source) == WEEKEND]
    unverified = [day for day in price_dates if exchange_day_status(day, source) == UNVERIFIED]

    checks.append(_check(
        "no_rows_on_official_closure",
        "FAIL" if closures else "PASS",
        (
            f"{len(closures)} price date(s) fall on an official exchange closure"
            if closures else "No price rows fall on an official exchange closure"
        ),
        {"dates": [str(day) for day in closures]},
    ))
    checks.append(_check(
        "no_rows_on_weekend",
        "FAIL" if weekends else "PASS",
        (
            f"{len(weekends)} price date(s) fall on a weekend"
            if weekends else "No price rows fall on a weekend"
        ),
        {"dates": [str(day) for day in weekends]},
    ))
    checks.append(_check(
        "no_unverified_price_dates",
        "WARN" if unverified else "PASS",
        (
            f"{len(unverified)} price date(s) are outside authoritative calendar coverage"
            if unverified else "Every price date is inside authoritative calendar coverage"
        ),
        {"dates": [str(day) for day in unverified]},
    ))

    if "is_trading_day" in calendar.columns and "date" in calendar.columns:
        sessions = [
            _to_date(value)
            for value in calendar.loc[calendar["is_trading_day"].astype(bool), "date"]
        ]
        sessions = [day for day in sessions if day is not None]
        max_prices = max(price_dates) if price_dates else None
        future = [day for day in sessions if max_prices is not None and day > max_prices]
        checks.append(_check(
            "no_future_trading_sessions",
            "FAIL" if future else "PASS",
            (
                f"{len(future)} calendar session(s) are later than the latest price row"
                if future else "No future date is reported as a completed trading session"
            ),
            {"latest_price_date": str(max_prices) if max_prices else None,
             "future_sessions": [str(day) for day in future]},
        ))
    return checks


def _audit_reconciliation(
    frames: Dict[str, pd.DataFrame]
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    prices = frames["prices_clean.parquet"]
    base = frames["base_dataset.parquet"]
    quant = frames.get("quant_features.parquet")
    sentiment = frames["daily_sentiment.parquet"]

    checks.append(_check(
        "row_count_matches_prices",
        "PASS" if len(base) == len(prices) else "FAIL",
        f"Base dataset rows ({len(base)}) vs price rows ({len(prices)})",
        {"base_rows": int(len(base)), "price_rows": int(len(prices))},
    ))
    if quant is not None:
        checks.append(_check(
            "row_count_matches_quant",
            "PASS" if len(quant) == len(prices) else "FAIL",
            f"Quant rows ({len(quant)}) vs price rows ({len(prices)})",
            {"quant_rows": int(len(quant)), "price_rows": int(len(prices))},
        ))

    prices_tickers = {str(value) for value in prices["ticker"].dropna().unique()}
    base_tickers = {str(value) for value in base["ticker"].dropna().unique()}
    missing_in_base = sorted(prices_tickers - base_tickers)
    extra_in_base = sorted(base_tickers - prices_tickers)
    checks.append(_check(
        "ticker_coverage_matches_prices",
        "PASS" if not missing_in_base and not extra_in_base else "FAIL",
        (
            f"Ticker set matches between prices and base ({len(prices_tickers)} tickers)"
            if not missing_in_base and not extra_in_base
            else f"Ticker mismatch: missing_in_base={missing_in_base[:10]}, "
                 f"extra_in_base={extra_in_base[:10]}"
        ),
        {"price_tickers": len(prices_tickers), "base_tickers": len(base_tickers),
         "missing_in_base": missing_in_base, "extra_in_base": extra_in_base},
    ))

    if quant is not None:
        quant_tickers = {str(value) for value in quant["ticker"].dropna().unique()}
        not_in_quant = sorted(prices_tickers - quant_tickers)
        checks.append(_check(
            "quant_ticker_coverage",
            "PASS" if not not_in_quant else "FAIL",
            (
                f"Quantifies every price ticker ({len(quant_tickers)} tickers)"
                if not not_in_quant else f"{len(not_in_quant)} ticker(s) missing from quant artifact"
            ),
            {"missing": not_in_quant},
        ))

    duplicate_checks = []
    for label, frame in (("prices", prices), ("base", base), ("quant", quant)):
        if frame is None:
            continue
        duplicate_checks.append(
            (label, int(frame.duplicated(["ticker", "trading_date"]).sum()))
        )
    duplicates = {label: count for label, count in duplicate_checks}
    checks.append(_check(
        "unique_ticker_date_keys",
        "PASS" if all(count == 0 for count in duplicates.values()) else "FAIL",
        f"Duplicate (ticker, trading_date) keys: {duplicates}",
        duplicates,
    ))

    price_min = _to_date(prices["trading_date"].min())
    price_max = _to_date(prices["trading_date"].max())
    base_min = _to_date(base["trading_date"].min())
    base_max = _to_date(base["trading_date"].max())
    same_range = price_min == base_min and price_max == base_max
    checks.append(_check(
        "date_range_matches_prices",
        "PASS" if same_range else "FAIL",
        f"Prices {price_min}..{price_max} vs base {base_min}..{base_max}",
        {"price_min": str(price_min), "price_max": str(price_max),
         "base_min": str(base_min), "base_max": str(base_max)},
    ))

    sentiment_dates = {
        _to_date(value) for value in sentiment["trading_date"].dropna().unique()
    }
    sentiment_dates.discard(None)
    price_date_set = {_to_date(value) for value in prices["trading_date"].unique()}
    orphan = sorted(day for day in sentiment_dates if day not in price_date_set)
    checks.append(_check(
        "sentiment_keys_within_prices",
        "PASS" if not orphan else "FAIL",
        (
            "Every sentiment trading_date exists in the price dataset"
            if not orphan else f"{len(orphan)} sentiment date(s) are not price sessions"
        ),
        {"orphan_dates": [str(day) for day in orphan]},
    ))
    return checks


def _audit_nulls(frames: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    prices = frames["prices_clean.parquet"]
    base = frames["base_dataset.parquet"]

    null_prices = {
        column: int(prices[column].isna().sum())
        for column in PRICE_FIELDS
        if column in prices.columns
    }
    checks.append(_check(
        "price_fields_non_null",
        "PASS" if all(count == 0 for count in null_prices.values()) else "FAIL",
        f"NULL counts in price fields: {null_prices}",
        null_prices,
    ))

    # return_1d is NULL only for the first observation of each ticker.
    bad_return_null = 0
    if "return_1d" in prices.columns:
        for _, group in prices.sort_values(["ticker", "trading_date"]).groupby("ticker"):
            if group["return_1d"].iloc[1:].isna().any():
                bad_return_null += 1
    checks.append(_check(
        "return_null_only_at_warmup",
        "PASS" if bad_return_null == 0 else "FAIL",
        (
            "return_1d is NULL only at each ticker's first observation"
            if bad_return_null == 0
            else f"{bad_return_null} ticker(s) have interior NULL returns"
        ),
        {"tickers_with_interior_null_return": bad_return_null},
    ))

    if "news_count" in base.columns:
        matched = base[base["news_count"].fillna(0) > 0]
        unmatched = base[base["news_count"].fillna(0) == 0]
        present_fields = [field for field in SENTIMENT_FIELDS if field in base.columns]
        required_fields = [
            field for field in MATCHED_REQUIRED_FIELDS if field in base.columns
        ]
        unmatched_non_null = int(unmatched[present_fields].notna().any(axis=1).sum())
        matched_missing = int(matched[required_fields].isna().any(axis=1).sum())
        # sentiment_std is undefined for single-article sessions; require it only
        # when at least two articles were observed.
        multi_article = matched[matched["news_count"] >= 2]
        std_missing = int(multi_article["sentiment_std"].isna().sum()) if (
            "sentiment_std" in matched.columns
        ) else 0
        passed = unmatched_non_null == 0 and matched_missing == 0 and std_missing == 0
        checks.append(_check(
            "sentiment_null_semantics",
            "PASS" if passed else "FAIL",
            (
                f"Unmatched sessions keep all sentiment fields NULL; "
                f"matched sessions are populated ({len(matched)} matched)"
                if passed
                else f"{unmatched_non_null} unmatched row(s) carry sentiment, "
                     f"{matched_missing} matched row(s) miss required fields, "
                     f"{std_missing} multi-article row(s) miss sentiment_std"
            ),
            {"unmatched_rows_with_sentiment": unmatched_non_null,
             "matched_rows_missing_sentiment": matched_missing,
             "multi_article_missing_std": std_missing},
        ))
    return checks


def audit_generation(
    directory: Path | str,
    expected_calendar_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full generation acceptance audit and return a JSON-safe report."""
    directory = Path(directory)
    frames = load_artifacts(directory)
    calendar = frames["trading_calendar.parquet"]
    prices = frames["prices_clean.parquet"]

    checks: List[Dict[str, Any]] = [
        _check("required_artifacts", "PASS", "All required artifacts are present",
               {"artifacts": list(REQUIRED_ARTIFACTS)}),
        *_audit_calendar(calendar),
        *_audit_price_vs_calendar(prices, calendar),
        *_audit_reconciliation(frames),
        *_audit_nulls(frames),
    ]

    versions = (
        sorted({str(value) for value in calendar["calendar_version"].dropna().unique()})
        if "calendar_version" in calendar.columns
        else []
    )
    calendar_version = versions[0] if len(versions) == 1 else None
    if expected_calendar_version is not None:
        checks.append(_check(
            "expected_calendar_version",
            "PASS" if calendar_version == expected_calendar_version else "FAIL",
            f"Expected {expected_calendar_version}, found {calendar_version}",
            {"expected": expected_calendar_version, "found": calendar_version},
        ))

    failures = [item for item in checks if item["status"] == "FAIL"]
    warnings = [item for item in checks if item["status"] == "WARN"]
    data_as_of = _to_date(prices["trading_date"].max())

    return {
        "schema_version": SCHEMA_VERSION,
        "audited_at": _utc_now(),
        "directory": str(directory),
        "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
        "calendar_version": calendar_version,
        "data_as_of": str(data_as_of) if data_as_of else None,
        "price_rows": int(len(prices)),
        "tickers": int(prices["ticker"].nunique()),
        "sessions": int(prices["trading_date"].nunique()),
        "artifacts": _artifact_summary(directory),
        "checks": checks,
        "failures": [item["name"] for item in failures],
        "warnings": [item["name"] for item in warnings],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# FinSentiMind — Generation Acceptance Audit",
        "",
        f"**Audited:** {report['audited_at']}",
        f"**Directory:** `{report['directory']}`",
        f"**Status:** **{report['status']}**",
        f"**Calendar version:** {report['calendar_version']}",
        f"**Data as of:** {report['data_as_of']}",
        f"**Rows:** {report['price_rows']:,} · **Tickers:** {report['tickers']} · "
        f"**Sessions:** {report['sessions']}",
        "",
        "## Artifacts",
        "",
        "| Artifact | Rows | Size (bytes) | SHA-256 |",
        "|---|---:|---:|---|",
    ]
    for name, item in report["artifacts"].items():
        lines.append(
            f"| `{name}` | {item['rows']:,} | {item['size_bytes']:,} | "
            f"`{item['sha256'][:16]}…` |"
        )
    lines += ["", "## Checks", "", "| Check | Status | Detail |", "|---|---|---|"]
    for item in report["checks"]:
        message = item["message"].replace("|", "\\|")
        lines.append(f"| {item['name']} | {item['status']} | {message} |")
    lines.append("")
    return "\n".join(lines)


def write_provenance(
    directory: Path | str,
    *,
    source_generation: str,
    calendar_version: str,
    data_as_of: str,
    feature_version: Optional[str] = None,
    refreshed_at: Optional[str] = None,
    universe_scope: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write ``provenance.json`` into a staging directory before publication."""
    directory = Path(directory)
    artifacts = _artifact_summary(directory)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_generation": source_generation,
        "calendar_version": calendar_version,
        "feature_version": feature_version,
        "data_as_of": data_as_of,
        "refreshed_at": refreshed_at or _utc_now(),
        "universe_scope": universe_scope,
        "artifacts": artifacts,
        "notes": notes or [],
    }
    write_json_atomic(directory / PROVENANCE_FILENAME, payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a dataset generation.")
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--expected-calendar-version", default=None)
    parser.add_argument("--report-json", type=Path, default=None)
    parser.add_argument("--report-md", type=Path, default=None)
    args = parser.parse_args(argv)

    setup_logging()
    report = audit_generation(
        args.dir, expected_calendar_version=args.expected_calendar_version
    )
    if args.report_json:
        write_json_atomic(args.report_json, report)
    if args.report_md:
        args.report_md.parent.mkdir(parents=True, exist_ok=True)
        args.report_md.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps(
        {
            "status": report["status"],
            "calendar_version": report["calendar_version"],
            "data_as_of": report["data_as_of"],
            "price_rows": report["price_rows"],
            "tickers": report["tickers"],
            "failures": report["failures"],
            "warnings": report["warnings"],
        },
        indent=2,
    ))
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
