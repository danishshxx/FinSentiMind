"""Versioned, auditable IDX exchange calendar.

This module is the single source of truth for whether the exchange was open.
It is deliberately offline and deterministic. Cross-sectional price coverage is
*not* used to decide exchange status; it only detects missing observations on
verified open sessions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

WEEKEND = "WEEKEND"
OFFICIAL_CLOSURE = "OFFICIAL_CLOSURE"
VERIFIED_OPEN = "VERIFIED_OPEN"
UNVERIFIED = "UNVERIFIED"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path(__file__).resolve().with_name("idx_exchange_calendar.json")


class CalendarSourceError(ValueError):
    """Raised when the authoritative calendar source is invalid."""


@dataclass(frozen=True)
class Closure:
    day: date
    closure_type: str
    name: str
    source_id: str


@dataclass(frozen=True)
class CalendarSource:
    calendar_version: str
    effective_from: date
    effective_to: date
    sources: Dict[str, Dict[str, Any]]
    closures: Dict[date, Closure]

    def covers(self, day: date) -> bool:
        return self.effective_from <= day <= self.effective_to

    def closure(self, day: date) -> Optional[Closure]:
        return self.closures.get(day)

    def reference(self, day: date) -> Dict[str, Any]:
        closure = self.closure(day)
        source_id = closure.source_id if closure else None
        source = self.sources.get(source_id, {}) if source_id else {}
        return {
            "calendar_version": self.calendar_version,
            "source_id": source_id,
            "source_url": source.get("url"),
            "announcement": source.get("announcement"),
            "closure_type": closure.closure_type if closure else None,
            "closure_name": closure.name if closure else None,
        }


def _parse_day(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CalendarSourceError(f"Invalid ISO date in calendar source: {value!r}") from exc


@lru_cache(maxsize=4)
def load_calendar_source(path: Path = DEFAULT_SOURCE) -> CalendarSource:
    """Load and validate the versioned calendar JSON."""
    path = Path(path)
    if not path.exists():
        raise CalendarSourceError(f"IDX calendar source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = {item["id"]: item for item in payload.get("sources", [])}
    closures: Dict[date, Closure] = {}
    for item in payload.get("closures", []):
        day = _parse_day(item["date"])
        if day in closures:
            raise CalendarSourceError(f"Duplicate closure date: {day}")
        source_id = item.get("source_id")
        if source_id not in sources:
            raise CalendarSourceError(
                f"Closure {day} references unknown source_id {source_id!r}"
            )
        closures[day] = Closure(
            day=day,
            closure_type=item["type"],
            name=item["name"],
            source_id=source_id,
        )
    return CalendarSource(
        calendar_version=str(payload["calendar_version"]),
        effective_from=_parse_day(payload["effective_from"]),
        effective_to=_parse_day(payload["effective_to"]),
        sources=sources,
        closures=closures,
    )


def exchange_day_status(
    day: date, source: Optional[CalendarSource] = None
) -> str:
    """Return WEEKEND / OFFICIAL_CLOSURE / VERIFIED_OPEN / UNVERIFIED."""
    source = source or load_calendar_source()
    if day.weekday() >= 5:
        return WEEKEND
    if day in source.closures:
        return OFFICIAL_CLOSURE
    if source.covers(day):
        return VERIFIED_OPEN
    return UNVERIFIED


def is_official_closure(day: date, source: Optional[CalendarSource] = None) -> bool:
    return exchange_day_status(day, source) == OFFICIAL_CLOSURE


def is_verified_open(day: date, source: Optional[CalendarSource] = None) -> bool:
    return exchange_day_status(day, source) == VERIFIED_OPEN


def calendar_reference(
    day: date, source: Optional[CalendarSource] = None
) -> Dict[str, Any]:
    return (source or load_calendar_source()).reference(day)


def verified_open_days(
    start: date,
    end: date,
    source: Optional[CalendarSource] = None,
) -> List[date]:
    source = source or load_calendar_source()
    days: List[date] = []
    current = start
    while current <= end:
        if exchange_day_status(current, source) == VERIFIED_OPEN:
            days.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return days


def closure_rows(
    days: Iterable[date], source: Optional[CalendarSource] = None
) -> List[Closure]:
    source = source or load_calendar_source()
    return [source.closures[day] for day in days if day in source.closures]


def source_metadata(path: Path = DEFAULT_SOURCE) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "calendar_version": payload["calendar_version"],
        "effective_from": payload["effective_from"],
        "effective_to": payload["effective_to"],
        "sources": payload["sources"],
        "closure_count": len(payload["closures"]),
    }
