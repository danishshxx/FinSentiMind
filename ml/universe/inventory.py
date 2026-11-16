"""Auditable IDX instrument inventory.

The authoritative all-IDX securities list is not retrievable from a free,
unauthenticated source in this environment (the official IDX endpoint returns
HTTP 403). This module therefore builds an inventory from documented project
sources and never invents company names, sectors or listing dates. Scope
completeness is recorded explicitly so a partial universe is never presented as
the full exchange.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.core.aliases import IDX30_TICKERS, LQ45_TICKERS
from app.core.logging import get_logger, setup_logging
from ml.metadata.master import (
    DEFAULT_INSTRUMENT_PATH,
    InstrumentMaster,
    InstrumentMasterError,
)
from ml.preprocessing.dataset_store import write_json_atomic


logger = get_logger(__name__)

SCHEMA_VERSION = 1
INVENTORY_VERSION = "idxl45-2026-11-16"
DEFAULT_OUTPUT = Path("ml/data/universe/instrument_inventory.json")
IDX_OFFICIAL_LIST_URL = "https://www.idx.co.id/en/market-data/indices/stock-index/"
IDX_SECURITIES_ENDPOINT = (
    "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
)
VALID_INSTRUMENT_STATUS = {
    "active", "suspended", "delisted", "new_listing", "unknown",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    ticker: str,
    *,
    company_name: Optional[str] = None,
    exchange: str = "IDX",
    board: Optional[str] = None,
    sector: Optional[str] = None,
    subsector: Optional[str] = None,
    instrument_status: str = "unknown",
    listing_date: Optional[str] = None,
    delisting_date: Optional[str] = None,
    previous_tickers: Optional[List[str]] = None,
    provenance: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if instrument_status not in VALID_INSTRUMENT_STATUS:
        raise ValueError(f"Invalid instrument status: {instrument_status!r}")
    return {
        "ticker": ticker.upper(),
        "company_name": company_name,
        "exchange": exchange,
        "board": board,
        "sector": sector,
        "subsector": subsector,
        "instrument_status": instrument_status,
        "listing_date": listing_date,
        "delisting_date": delisting_date,
        "previous_tickers": list(previous_tickers or []),
        "provenance": dict(provenance or {}),
        "last_verified_at": _utc_now(),
    }


def _from_master(master: InstrumentMaster) -> List[Dict[str, Any]]:
    records = []
    for instrument in master.instruments:
        records.append(_record(
            instrument.ticker,
            company_name=instrument.company_name,
            exchange=instrument.exchange or "IDX",
            sector=instrument.sector,
            subsector=instrument.subsector,
            instrument_status="active" if instrument.listing_status == "active" else (
                instrument.listing_status if instrument.listing_status in VALID_INSTRUMENT_STATUS
                else "unknown"
            ),
            listing_date=instrument.listing_date,
            previous_tickers=list(instrument.previous_tickers),
            provenance={"instrument_master": instrument.metadata_status},
        ))
    return records


def _from_documented_alias_list(tickers: Iterable[str]) -> List[Dict[str, Any]]:
    return [
        _record(
            ticker,
            provenance={
                "source": "app/core/aliases.py:LQ45_TICKERS",
                "metadata": "ticker_only",
            },
        )
        for ticker in tickers
    ]


def _apply_seed(records: Dict[str, Dict[str, Any]], seed_path: Path) -> None:
    """Merge owner-provided metadata. Never invents values; only fills blanks."""
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    for item in payload.get("instruments", []):
        ticker = str(item["ticker"]).upper()
        record = records.setdefault(ticker, _record(ticker))
        for field in (
            "company_name", "exchange", "board", "sector", "subsector",
            "instrument_status", "listing_date", "delisting_date",
        ):
            if item.get(field) is not None and record.get(field) in (None, "unknown"):
                record[field] = item[field]
        if item.get("previous_tickers"):
            record["previous_tickers"] = list(item["previous_tickers"])
        record["provenance"] = {
            **record.get("provenance", {}),
            "seed": str(seed_path),
        }


def build_inventory(
    *,
    include_lq45: bool = True,
    master_path: Path = DEFAULT_INSTRUMENT_PATH,
    seed_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the documented-scope inventory with explicit completeness."""
    try:
        master = InstrumentMaster.load(master_path)
    except InstrumentMasterError as exc:
        logger.warning("Instrument master unavailable: %s", exc)
        master = InstrumentMaster.empty()

    records: Dict[str, Dict[str, Any]] = {
        item["ticker"]: item for item in _from_master(master)
    }
    if include_lq45:
        for item in _from_documented_alias_list(LQ45_TICKERS):
            records.setdefault(item["ticker"], item)
    else:
        for ticker in IDX30_TICKERS:
            records.setdefault(ticker, _record(ticker))

    if seed_path is not None:
        _apply_seed(records, Path(seed_path))

    instruments = sorted(records.values(), key=lambda item: item["ticker"])
    counts: Dict[str, int] = {}
    for item in instruments:
        counts[item["instrument_status"]] = counts.get(item["instrument_status"], 0) + 1

    scope = "IDXL45_DOCUMENTED" if include_lq45 else "IDX30"
    return {
        "schema_version": SCHEMA_VERSION,
        "inventory_version": INVENTORY_VERSION,
        "generated_at": _utc_now(),
        "scope": scope,
        "complete": False,
        "completion_reason": (
            "The official all-IDX securities list could not be retrieved from a "
            "free unauthenticated source; the IDX endpoint returned HTTP 403. "
            "This inventory covers documented project universes only."
        ),
        "sources": [
            {
                "id": "instrument_master",
                "name": "FinSentiMind IDX30 instrument master",
                "location": str(master_path),
                "url": IDX_OFFICIAL_LIST_URL,
                "verification": "verified_for_idx30",
            },
            {
                "id": "lq45_aliases",
                "name": "Documented LQ45 ticker list",
                "location": "app/core/aliases.py",
                "url": None,
                "verification": "documented_in_repo",
            },
            {
                "id": "idx_official_securities",
                "name": "Official IDX securities endpoint",
                "location": IDX_SECURITIES_ENDPOINT,
                "url": IDX_OFFICIAL_LIST_URL,
                "verification": "blocked_http_403",
            },
        ],
        "counts": {"total": len(instruments), **counts},
        "instruments": instruments,
    }


def save_inventory(inventory: Dict[str, Any], path: Path = DEFAULT_OUTPUT) -> Path:
    write_json_atomic(Path(path), inventory)
    logger.info("Saved instrument inventory (%d instruments) to %s",
                len(inventory["instruments"]), path)
    return Path(path)


def render_markdown(inventory: Dict[str, Any]) -> str:
    counts = inventory["counts"]
    lines = [
        "# FinSentiMind — IDX Instrument Inventory",
        "",
        f"**Version:** {inventory['inventory_version']}",
        f"**Generated:** {inventory['generated_at']}",
        f"**Scope:** {inventory['scope']}",
        f"**Complete:** {'yes' if inventory['complete'] else 'NO'}",
        f"**Instruments:** {counts['total']}",
        "",
        f"> {inventory['completion_reason']}",
        "",
        "| Ticker | Company | Sector | Status | Provenance |",
        "|---|---|---|---|---|",
    ]
    for item in inventory["instruments"]:
        provenance = ", ".join(sorted(item.get("provenance", {}).keys()))
        lines.append(
            f"| {item['ticker']} | {item.get('company_name') or '—'} | "
            f"{item.get('sector') or '—'} | {item['instrument_status']} | {provenance} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the IDX instrument inventory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--seed", type=Path, default=None)
    parser.add_argument("--idx30-only", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()
    inventory = build_inventory(
        include_lq45=not args.idx30_only, seed_path=args.seed
    )
    save_inventory(inventory, args.output)
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(inventory), encoding="utf-8")
    print(json.dumps({
        "scope": inventory["scope"],
        "complete": inventory["complete"],
        "counts": inventory["counts"],
        "output": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
