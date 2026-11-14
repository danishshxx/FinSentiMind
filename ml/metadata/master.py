"""Auditable IDX instrument master data with strict provenance.

Missing values are represented as ``None``. They are never replaced with an
invented company name, sector, subsector, or listing date.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger


logger = get_logger(__name__)

SCHEMA_VERSION = 1
DEFAULT_INSTRUMENT_PATH = (
    Path(__file__).resolve().parents[1] / "metadata" / "idx30_instruments.json"
)

_TICKER_RE = re.compile(r"^[A-Z]{4}$")
_VALID_STATUS = {"active", "inactive", "unknown"}
_FIELD_NAMES = {
    "ticker",
    "exchange",
    "company_name",
    "sector",
    "subsector",
    "listing_status",
    "listing_date",
    "previous_tickers",
    "successor_ticker",
}
_COMPLETENESS_FIELDS = (
    "company_name",
    "sector",
    "subsector",
    "listing_status",
    "listing_date",
)


class InstrumentMasterError(ValueError):
    """Raised when the instrument master is invalid or cannot be loaded."""


def _parse_date(value: Any, label: str) -> Optional[date]:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise InstrumentMasterError(f"{label} must be ISO YYYY-MM-DD: {value!r}") from exc


def _parse_datetime(value: Any, label: str) -> datetime:
    if not value:
        raise InstrumentMasterError(f"{label} is required")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InstrumentMasterError(f"{label} must be ISO-8601: {value!r}") from exc


@dataclass(frozen=True)
class Instrument:
    ticker: str
    exchange: str
    company_name: Optional[str]
    sector: Optional[str]
    subsector: Optional[str]
    listing_status: str
    listing_date: Optional[str]
    previous_tickers: List[str] = field(default_factory=list)
    successor_ticker: Optional[str] = None
    provenance: Dict[str, str] = field(default_factory=dict)
    notes: Optional[str] = None

    @property
    def missing_fields(self) -> List[str]:
        return [name for name in _COMPLETENESS_FIELDS if getattr(self, name) in (None, "")]

    @property
    def completeness_pct(self) -> float:
        present = len(_COMPLETENESS_FIELDS) - len(self.missing_fields)
        return round(present / len(_COMPLETENESS_FIELDS) * 100.0, 2)

    @property
    def metadata_status(self) -> str:
        if self.completeness_pct == 100.0:
            return "complete"
        if self.ticker and self.exchange and self.listing_status:
            return "partial"
        return "unavailable"

    def to_dict(self, include_provenance: bool = True) -> Dict[str, Any]:
        payload = {
            "ticker": self.ticker,
            "exchange": self.exchange,
            "company_name": self.company_name,
            "sector": self.sector,
            "subsector": self.subsector,
            "listing_status": self.listing_status,
            "listing_date": self.listing_date,
            "previous_tickers": list(self.previous_tickers),
            "successor_ticker": self.successor_ticker,
            "metadata_status": self.metadata_status,
            "completeness_pct": self.completeness_pct,
            "missing_fields": self.missing_fields,
            "notes": self.notes,
        }
        if include_provenance:
            payload["provenance"] = dict(self.provenance)
        return payload


@dataclass
class InstrumentMaster:
    schema_version: int
    metadata_version: str
    effective_from: str
    updated_at: str
    sources: List[Dict[str, Any]]
    revisions: List[Dict[str, Any]]
    instruments: List[Instrument]
    source_path: Optional[Path] = None

    @property
    def instruments_by_ticker(self) -> Dict[str, Instrument]:
        return {instrument.ticker: instrument for instrument in self.instruments}

    @classmethod
    def empty(cls) -> "InstrumentMaster":
        return cls(
            schema_version=SCHEMA_VERSION,
            metadata_version="unavailable",
            effective_from="",
            updated_at="",
            sources=[],
            revisions=[],
            instruments=[],
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_INSTRUMENT_PATH) -> "InstrumentMaster":
        path = Path(path)
        if not path.is_file():
            raise InstrumentMasterError(f"Instrument master not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstrumentMasterError(f"Invalid instrument JSON: {exc}") from exc
        master = cls.from_dict(payload, source_path=path)
        master.validate()
        return master

    @classmethod
    def from_dict(
        cls, payload: Dict[str, Any], source_path: Optional[Path] = None
    ) -> "InstrumentMaster":
        instruments = [
            Instrument(
                ticker=str(item.get("ticker", "")).strip(),
                exchange=str(item.get("exchange", "")).strip(),
                company_name=item.get("company_name"),
                sector=item.get("sector"),
                subsector=item.get("subsector"),
                listing_status=str(item.get("listing_status", "unknown")).strip(),
                listing_date=str(item.get("listing_date")) if item.get("listing_date") else None,
                previous_tickers=list(item.get("previous_tickers") or []),
                successor_ticker=item.get("successor_ticker"),
                provenance=dict(item.get("provenance") or {}),
                notes=item.get("notes"),
            )
            for item in payload.get("instruments", [])
        ]
        return cls(
            schema_version=int(payload.get("schema_version", 0)),
            metadata_version=str(payload.get("metadata_version", "")),
            effective_from=str(payload.get("effective_from", "")),
            updated_at=str(payload.get("updated_at", "")),
            sources=list(payload.get("sources", [])),
            revisions=list(payload.get("revisions", [])),
            instruments=instruments,
            source_path=source_path,
        )

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InstrumentMasterError(
                f"Unsupported instrument schema_version: {self.schema_version}"
            )
        if not self.metadata_version:
            raise InstrumentMasterError("metadata_version is required")
        _parse_date(self.effective_from, "effective_from")
        _parse_datetime(self.updated_at, "updated_at")
        if not self.instruments:
            raise InstrumentMasterError("Instrument master is empty")

        source_ids = set()
        for source in self.sources:
            source_id = str(source.get("id", "")).strip()
            if not source_id:
                raise InstrumentMasterError("Every source requires an id")
            if source_id in source_ids:
                raise InstrumentMasterError(f"Duplicate source id: {source_id}")
            if not source.get("name"):
                raise InstrumentMasterError(f"Source {source_id} requires a name")
            source_ids.add(source_id)
        if "unavailable" not in source_ids:
            raise InstrumentMasterError(
                "Sources must include an 'unavailable' entry for missing fields"
            )

        seen_tickers = set()
        seen_previous = set()
        for instrument in self.instruments:
            if not _TICKER_RE.fullmatch(instrument.ticker):
                raise InstrumentMasterError(
                    f"Invalid IDX ticker: {instrument.ticker!r}"
                )
            if instrument.ticker in seen_tickers:
                raise InstrumentMasterError(
                    f"Duplicate instrument ticker: {instrument.ticker}"
                )
            seen_tickers.add(instrument.ticker)
            if instrument.exchange != "IDX":
                raise InstrumentMasterError(
                    f"{instrument.ticker} exchange must be IDX, got {instrument.exchange!r}"
                )
            if instrument.listing_status not in _VALID_STATUS:
                raise InstrumentMasterError(
                    f"{instrument.ticker} invalid listing_status: {instrument.listing_status!r}"
                )
            _parse_date(instrument.listing_date, f"{instrument.ticker}.listing_date")
            for previous in instrument.previous_tickers:
                if not _TICKER_RE.fullmatch(str(previous)):
                    raise InstrumentMasterError(
                        f"{instrument.ticker} invalid previous ticker: {previous!r}"
                    )
                if previous == instrument.ticker:
                    raise InstrumentMasterError(
                        f"{instrument.ticker} cannot list itself as previous ticker"
                    )
                if previous in seen_previous:
                    raise InstrumentMasterError(
                        f"Duplicate previous ticker mapping: {previous}"
                    )
                seen_previous.add(previous)
            if instrument.successor_ticker:
                if not _TICKER_RE.fullmatch(str(instrument.successor_ticker)):
                    raise InstrumentMasterError(
                        f"{instrument.ticker} invalid successor ticker"
                    )
                if instrument.successor_ticker == instrument.ticker:
                    raise InstrumentMasterError(
                        f"{instrument.ticker} cannot succeed itself"
                    )
            for field_name, source_id in instrument.provenance.items():
                if field_name not in _FIELD_NAMES:
                    raise InstrumentMasterError(
                        f"{instrument.ticker} unknown provenance field {field_name!r}"
                    )
                if source_id not in source_ids:
                    raise InstrumentMasterError(
                        f"{instrument.ticker} provenance references unknown source {source_id!r}"
                    )
            for field_name in ("company_name", "sector"):
                value = getattr(instrument, field_name)
                source_id = instrument.provenance.get(field_name)
                if value and source_id == "unavailable":
                    raise InstrumentMasterError(
                        f"{instrument.ticker} {field_name} is populated but marked unavailable"
                    )
                if value and not source_id:
                    raise InstrumentMasterError(
                        f"{instrument.ticker} {field_name} requires provenance"
                    )

        if seen_previous & seen_tickers:
            raise InstrumentMasterError(
                "A previous ticker cannot also be an active instrument ticker"
            )

        versions = [str(revision.get("version", "")) for revision in self.revisions]
        if not versions or any(not version for version in versions):
            raise InstrumentMasterError("At least one revision with a version is required")
        if len(versions) != len(set(versions)):
            raise InstrumentMasterError("Revision versions must be unique")

    def get(self, ticker: str) -> Optional[Instrument]:
        return self.instruments_by_ticker.get(str(ticker).strip().upper())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "metadata_version": self.metadata_version,
            "effective_from": self.effective_from,
            "updated_at": self.updated_at,
            "source_path": str(self.source_path) if self.source_path else None,
            "sources": self.sources,
            "revisions": self.revisions,
            "count": len(self.instruments),
            "instruments": [instrument.to_dict() for instrument in self.instruments],
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the auditable FinSentiMind instrument master."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INSTRUMENT_PATH)
    args = parser.parse_args(argv)

    try:
        master = InstrumentMaster.load(args.input)
    except InstrumentMasterError as exc:
        print(f"INVALID: {exc}")
        return 1

    partial = [instrument for instrument in master.instruments if instrument.completeness_pct < 100]
    print(f"VALID: {len(master.instruments)} instruments, version {master.metadata_version}")
    print(f"Partial metadata: {len(partial)}")
    for instrument in partial:
        print(
            f"  {instrument.ticker}: {instrument.completeness_pct:.0f}% "
            f"missing={','.join(instrument.missing_fields)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
