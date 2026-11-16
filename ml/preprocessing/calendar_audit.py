"""Calendar gap audit for the canonical IDX price universe.

The quality gate reports *per-ticker* missing weekday groups. This module
classifies those groups without changing the derived trading calendar:

- OFFICIAL_HOLIDAY
- COLLECTIVE_LEAVE
- EXCHANGE_CLOSURE
- MIXED_HOLIDAY_WINDOW
- PARTIAL_OBSERVATION
- UNEXPLAINED

Classification is evidence-oriented. The curated closure map below must be
re-validated against the annual IDX exchange decree before publication.
Unknown dates are never assumed to be holidays.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import write_json_atomic
from ml.preprocessing.idx_calendar import load_calendar_source
from ml.preprocessing.quality_gate import MissingDateGroup, check_missing_weekdays


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICES = (
    _PROJECT_ROOT / "ml" / "data" / "staging"
    / "20261114T154144Z_acedb3bb" / "prices_clean.parquet"
)
DEFAULT_REPORT = _PROJECT_ROOT / "ml" / "reports" / "calendar_gap_audit.md"
DEFAULT_JSON = _PROJECT_ROOT / "ml" / "reports" / "calendar_gap_audit.json"

OFFICIAL_HOLIDAY = "OFFICIAL_HOLIDAY"
COLLECTIVE_LEAVE = "COLLECTIVE_LEAVE"
EXCHANGE_CLOSURE = "EXCHANGE_CLOSURE"
MIXED_HOLIDAY_WINDOW = "MIXED_HOLIDAY_WINDOW"
PARTIAL_OBSERVATION = "PARTIAL_OBSERVATION"
UNEXPLAINED = "UNEXPLAINED"

# Derived from the versioned authoritative IDX calendar source.
_SOURCE = load_calendar_source()
_TYPE_MAP = {
    "official_holiday": OFFICIAL_HOLIDAY,
    "collective_leave": COLLECTIVE_LEAVE,
    "exchange_closure": EXCHANGE_CLOSURE,
}
CLOSURE_MAP: Dict[date, Tuple[str, str]] = {
    day: (_TYPE_MAP[closure.closure_type], closure.name)
    for day, closure in _SOURCE.closures.items()
}


@dataclass
class GroupAudit:
    ticker: str
    start: date
    end: date
    missing_count: int
    category: str
    label: str
    dates: List[date] = field(default_factory=list)


@dataclass
class CalendarAudit:
    generated_at: str
    total_groups: int
    unique_date_ranges: int
    category_group_counts: Dict[str, int]
    zero_coverage_days: int
    partial_days: int
    groups: List[GroupAudit]


def _weekdays(start: date, end: date) -> List[date]:
    values: List[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def classify_gap_dates(
    start: date, end: date, ticker_present_dates: Optional[Iterable[date]] = None
) -> Tuple[str, str]:
    """Classify a missing weekday range without assuming unknown gaps."""
    days = _weekdays(start, end)
    if not days:
        return UNEXPLAINED, "No weekday in range"

    classes = []
    labels = []
    for day in days:
        if ticker_present_dates is not None and day in set(ticker_present_dates):
            classes.append(PARTIAL_OBSERVATION)
            labels.append("Ticker has data while peers are missing")
            continue
        item = CLOSURE_MAP.get(day)
        if item is None:
            classes.append(UNEXPLAINED)
            labels.append("No verified closure classification")
        else:
            classes.append(item[0])
            labels.append(item[1])

    unique = set(classes)
    if UNEXPLAINED in unique:
        return UNEXPLAINED, "One or more dates lack verified evidence"
    if PARTIAL_OBSERVATION in unique:
        return PARTIAL_OBSERVATION, "Partial cross-sectional observation"
    if unique == {OFFICIAL_HOLIDAY}:
        return OFFICIAL_HOLIDAY, "All weekdays are official holidays"
    if unique == {COLLECTIVE_LEAVE}:
        return COLLECTIVE_LEAVE, "All weekdays are collective leave"
    if unique == {EXCHANGE_CLOSURE}:
        return EXCHANGE_CLOSURE, "All weekdays are exchange closures"
    return MIXED_HOLIDAY_WINDOW, "Mixed official/collective/exchange closures"


def audit_missing_weekday_groups(
    prices: pd.DataFrame,
) -> CalendarAudit:
    """Classify every per-ticker missing weekday group."""
    groups = check_missing_weekdays(prices)
    present_dates = set(pd.to_datetime(prices["trading_date"]).dt.date)
    audit_groups: List[GroupAudit] = []
    for group in groups:
        category, label = classify_gap_dates(
            group.start, group.end, ticker_present_dates=present_dates
        )
        audit_groups.append(
            GroupAudit(
                ticker=group.ticker,
                start=group.start,
                end=group.end,
                missing_count=group.missing_count,
                category=category,
                label=label,
                dates=_weekdays(group.start, group.end),
            )
        )

    categories = Counter(group.category for group in audit_groups)
    ranges = {(group.start, group.end) for group in audit_groups}
    all_days = {day for group in audit_groups for day in group.dates}
    partial_days = {
        day for group in audit_groups if group.category == PARTIAL_OBSERVATION
        for day in group.dates
    }
    return CalendarAudit(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_groups=len(audit_groups),
        unique_date_ranges=len(ranges),
        category_group_counts=dict(sorted(categories.items())),
        zero_coverage_days=len(all_days - partial_days),
        partial_days=len(partial_days),
        groups=audit_groups,
    )


def _range_rows(audit: CalendarAudit) -> List[Dict[str, Any]]:
    by_range: Dict[Tuple[date, date, int], Dict[str, Any]] = {}
    for group in audit.groups:
        key = (group.start, group.end, group.missing_count)
        item = by_range.setdefault(
            key,
            {
                "start": group.start.isoformat(),
                "end": group.end.isoformat(),
                "weekdays_missing": group.missing_count,
                "tickers": 0,
                "category": group.category,
                "label": group.label,
                "ticker_list": [],
            },
        )
        item["tickers"] += 1
        item["ticker_list"].append(group.ticker)
    return sorted(by_range.values(), key=lambda item: item["start"])


def render_markdown(audit: CalendarAudit, source: Path) -> str:
    lines = [
        "# Calendar Gap Audit",
        "",
        f"**Generated:** {audit.generated_at}",
        f"**Input:** `{source}`",
        f"**Per-ticker missing weekday groups:** {audit.total_groups}",
        f"**Unique missing date ranges:** {audit.unique_date_ranges}",
        "",
        "## Result",
        "",
        f"- Weekday dates with zero coverage: {audit.zero_coverage_days}",
        f"- Weekday dates with partial coverage: {audit.partial_days}",
        "- Group classification counts:",
    ]
    for category, count in audit.category_group_counts.items():
        lines.append(f"  - `{category}`: {count}")

    lines.extend([
        "",
        "The 822 figure is the number of *per-ticker* missing weekday groups "
        "reported by the price quality gate, not 822 missing exchange dates.",
        "",
        "## Missing Date Ranges",
        "",
        "| Start | End | Weekdays | Tickers | Classification | Evidence |",
        "|---|---|---:|---:|---|---|",
    ])
    for row in _range_rows(audit):
        lines.append(
            f"| {row['start']} | {row['end']} | {row['weekdays_missing']} | "
            f"{row['tickers']} | `{row['category']}` | {row['label']} |"
        )

    unexplained = [
        group for group in audit.groups if group.category == UNEXPLAINED
    ]
    partial = [
        group for group in audit.groups if group.category == PARTIAL_OBSERVATION
    ]

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `OFFICIAL_HOLIDAY`, `COLLECTIVE_LEAVE`, `EXCHANGE_CLOSURE`, and "
        "`MIXED_HOLIDAY_WINDOW` groups align with the curated IDX/government "
        "closure map.",
        "- `PARTIAL_OBSERVATION` means the calendar date is a real trading day "
        "but some tickers are absent. It is not reclassified as a holiday.",
        "- `UNEXPLAINED` is never silently treated as a holiday.",
    ])
    if partial:
        partial_dates = sorted({day.isoformat() for group in partial for day in group.dates})
        lines.append(
            f"- Partial observation dates: {', '.join(partial_dates)}. "
            "Provider-level verification is still required."
        )
    if not unexplained:
        lines.append("- No unexplained per-ticker groups were found.")
    else:
        lines.append(
            f"- Unexplained groups require investigation: {len(unexplained)}."
        )

    lines.extend([
        "",
        "## Limitations",
        "",
        "- The closure map is curated, not fetched from an authoritative API.",
        "- Revalidate against the annual IDX exchange decree before using this "
        "audit as a licensing or compliance statement.",
        "- The audit does not fabricate sessions and does not alter "
        "`trading_calendar.parquet`.",
        "",
    ])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit missing weekday groups.")
    parser.add_argument("--input", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args(argv)

    setup_logging()
    prices = pd.read_parquet(args.input)
    audit = audit_missing_weekday_groups(prices)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(audit, args.input), encoding="utf-8")
    payload = {
        "generated_at": audit.generated_at,
        "input": str(args.input),
        "total_groups": audit.total_groups,
        "unique_date_ranges": audit.unique_date_ranges,
        "category_group_counts": audit.category_group_counts,
        "zero_coverage_days": audit.zero_coverage_days,
        "partial_days": audit.partial_days,
        "ranges": _range_rows(audit),
    }
    write_json_atomic(args.json, payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
