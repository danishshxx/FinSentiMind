"""Per-instrument data coverage matrix.

Coverage is measured from real local observations against the authoritative
trading calendar. Nothing is inferred from an expected provider universe: a
ticker with no price history is reported UNAVAILABLE, not assumed complete.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import write_json_atomic
from ml.preprocessing.idx_calendar import verified_open_days

logger = get_logger(__name__)

PRICE_PROVIDERS_DEFAULT = "yfinance"
COMPLETE_THRESHOLD = 0.95
STALE_DAYS = 3


def _to_date(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _calendar_sessions(calendar: Optional[pd.DataFrame]) -> Optional[set[date]]:
    if calendar is None or calendar.empty or "date" not in calendar.columns:
        return None
    frame = calendar
    if "is_trading_day" in frame.columns:
        frame = frame[frame["is_trading_day"].astype(bool)]
    return {_to_date(value) for value in frame["date"] if _to_date(value) is not None}


def build_coverage_matrix(
    inventory: Dict[str, Any],
    prices: pd.DataFrame,
    calendar: Optional[pd.DataFrame] = None,
    *,
    as_of: Optional[date] = None,
    freshness_days: int = STALE_DAYS,
    base: Optional[pd.DataFrame] = None,
    news_tickers: Optional[Iterable[str]] = None,
    capital_flow_tickers: Optional[Iterable[str]] = None,
    fundamental_tickers: Optional[Iterable[str]] = None,
    providers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    sessions = _calendar_sessions(calendar)
    prices = prices.copy()
    prices["day"] = pd.to_datetime(prices["trading_date"], errors="coerce").dt.date
    providers = providers or {}
    news_set = {str(t).upper() for t in (news_tickers or [])}
    flow_set = {str(t).upper() for t in (capital_flow_tickers or [])}
    fund_set = {str(t).upper() for t in (fundamental_tickers or [])}

    if as_of is None:
        as_of = _to_date(prices["trading_date"].max()) if len(prices) else date.today()

    matrix: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    usable = 0

    for instrument in inventory["instruments"]:
        ticker = instrument["ticker"]
        subset = prices[prices["ticker"].astype(str).str.upper() == ticker]
        record: Dict[str, Any] = {
            "ticker": ticker,
            "instrument_status": instrument.get("instrument_status", "unknown"),
            "price_provider": providers.get(ticker, PRICE_PROVIDERS_DEFAULT),
            "first_available_date": None,
            "last_available_date": None,
            "available_sessions": 0,
            "expected_sessions": 0,
            "coverage_ratio": 0.0,
            "latest_data_age": None,
            "fundamentals_available": ticker in fund_set,
            "news_available": ticker in news_set,
            "sentiment_available": ticker in news_set,
            "capital_flow_available": ticker in flow_set,
            "macro_relevance": None,
            "data_quality_status": "UNAVAILABLE",
            "failure_reason": "no_price_history",
        }

        if not subset.empty:
            first = _to_date(subset["trading_date"].min())
            last = _to_date(subset["trading_date"].max())
            observed = sorted({day for day in subset["day"].dropna()})
            record["first_available_date"] = str(first)
            record["last_available_date"] = str(last)
            record["available_sessions"] = len(observed)

            # Expected sessions come from the authoritative exchange calendar,
            # never from cross-sectional observations. Provider outages must
            # reduce coverage rather than shrink the denominator.
            if first is not None and last is not None:
                expected = len(verified_open_days(first, last))
                if expected == 0 and sessions is not None:
                    expected = len([day for day in sessions if first <= day <= last])
            else:
                expected = len(observed)
            record["expected_sessions"] = expected
            ratio = (len(observed) / expected) if expected else 0.0
            record["coverage_ratio"] = round(ratio, 6)
            record["latest_data_age"] = (
                (as_of - last).days if as_of is not None and last is not None else None
            )

            invalid_close = 0
            if "close" in subset.columns:
                close = pd.to_numeric(subset["close"], errors="coerce")
                invalid_close = int((close.isna() | (close <= 0)).sum())

            age = record["latest_data_age"]
            if invalid_close == len(subset):
                status = "FAILED"
                record["failure_reason"] = "all_closes_invalid"
            elif age is not None and age > freshness_days:
                status = "STALE"
                record["failure_reason"] = f"latest_data_age={age}d"
            elif ratio >= COMPLETE_THRESHOLD:
                status = "COMPLETE"
                record["failure_reason"] = None
            else:
                status = "PARTIAL"
                record["failure_reason"] = f"coverage_ratio={ratio:.3f}"
            record["data_quality_status"] = status
            if status in {"COMPLETE", "PARTIAL"}:
                usable += 1

        # Sentiment/news availability from the base dataset when provided.
        if base is not None and not base.empty and "news_count" in base.columns:
            ticker_rows = base[base["ticker"].astype(str).str.upper() == ticker]
            has_news = bool((ticker_rows["news_count"].fillna(0) > 0).any())
            record["news_available"] = record["news_available"] or has_news
            record["sentiment_available"] = record["news_available"]

        status_counts[record["data_quality_status"]] = (
            status_counts.get(record["data_quality_status"], 0) + 1
        )
        matrix.append(record)

    total = len(matrix)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": str(as_of) if as_of else None,
        "freshness_days": freshness_days,
        "inventory_scope": inventory.get("scope"),
        "inventory_complete": inventory.get("complete", False),
        "instrument_count": total,
        "usable_instruments": usable,
        "coverage_pct": round((usable / total * 100.0), 4) if total else 0.0,
        "status_counts": status_counts,
        "matrix": matrix,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# FinSentiMind — Universe Coverage Matrix",
        "",
        f"**Generated:** {report['generated_at']}",
        f"**As of:** {report['as_of']}",
        f"**Inventory scope:** {report['inventory_scope']} "
        f"({'complete' if report['inventory_complete'] else 'INCOMPLETE'})",
        f"**Instruments:** {report['instrument_count']} · "
        f"**Usable:** {report['usable_instruments']} "
        f"({report['coverage_pct']}%)",
        "",
        "Status counts: " + ", ".join(
            f"{key}={value}" for key, value in sorted(report["status_counts"].items())
        ),
        "",
        "| Ticker | Status | Sessions | Expected | Coverage | Last | Age | Provider |",
        "|---|---|---:|---:|---:|---|---:|---|",
    ]
    for item in report["matrix"]:
        lines.append(
            f"| {item['ticker']} | {item['data_quality_status']} | "
            f"{item['available_sessions']} | {item['expected_sessions']} | "
            f"{item['coverage_ratio']:.3f} | {item['last_available_date'] or '—'} | "
            f"{item['latest_data_age'] if item['latest_data_age'] is not None else '—'} | "
            f"{item['price_provider']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from ml.preprocessing.dataset_store import resolve_active_dataset_dir
    from ml.universe.inventory import build_inventory, DEFAULT_OUTPUT

    parser = argparse.ArgumentParser(description="Build the universe coverage matrix.")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--prices", type=Path, default=None)
    parser.add_argument("--calendar", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("ml/reports/universe_coverage.json"))
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--freshness-days", type=int, default=STALE_DAYS)
    args = parser.parse_args(argv)

    setup_logging()
    inventory = (
        json.loads(args.inventory.read_text(encoding="utf-8"))
        if args.inventory.is_file() else build_inventory()
    )
    active = resolve_active_dataset_dir(Path("ml/data"))
    prices_path = args.prices or (active / "prices_clean.parquet")
    calendar_path = args.calendar or (active / "trading_calendar.parquet")
    prices = pd.read_parquet(prices_path)
    calendar = (
        pd.read_parquet(calendar_path) if calendar_path.is_file() else None
    )
    report = build_coverage_matrix(
        inventory, prices, calendar, freshness_days=args.freshness_days
    )
    write_json_atomic(args.output, report)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "instruments": report["instrument_count"],
        "usable": report["usable_instruments"],
        "coverage_pct": report["coverage_pct"],
        "status_counts": report["status_counts"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
