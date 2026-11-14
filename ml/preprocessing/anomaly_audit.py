"""Non-destructive audit helpers for material data anomalies.

These functions do not normalize, cap, or rewrite provider values. They only
identify records that changed materially between two verified snapshots so the
change can be investigated and documented.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging


logger = get_logger(__name__)

DEFAULT_RATIO_THRESHOLD = 1.5


@dataclass(frozen=True)
class VolumeChange:
    ticker: str
    trading_date: date
    old_volume: int
    new_volume: int
    ratio: float
    pct_change: float


def compare_volume_changes(
    old: pd.DataFrame,
    new: pd.DataFrame,
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
) -> List[VolumeChange]:
    """Return overlapping rows whose volume changed beyond a ratio threshold."""
    required = {"ticker", "trading_date", "volume"}
    for name, frame in (("old", old), ("new", new)):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} dataset missing columns: {sorted(missing)}")
    if ratio_threshold <= 1:
        raise ValueError("ratio_threshold must be > 1")

    left = old[list(required)].copy()
    right = new[list(required)].copy()
    left["trading_date"] = pd.to_datetime(left["trading_date"]).dt.date
    right["trading_date"] = pd.to_datetime(right["trading_date"]).dt.date
    merged = left.merge(
        right,
        on=["ticker", "trading_date"],
        suffixes=("_old", "_new"),
        how="inner",
    )
    merged = merged[
        (pd.to_numeric(merged["volume_old"], errors="coerce") > 0)
        & (pd.to_numeric(merged["volume_new"], errors="coerce") > 0)
    ].copy()
    if merged.empty:
        return []

    merged["ratio"] = merged["volume_new"] / merged["volume_old"]
    material = merged[
        (merged["ratio"] >= ratio_threshold)
        | (merged["ratio"] <= 1.0 / ratio_threshold)
    ].sort_values("ratio", ascending=False)

    changes: List[VolumeChange] = []
    for _, row in material.iterrows():
        old_volume = int(row["volume_old"])
        new_volume = int(row["volume_new"])
        ratio = float(row["ratio"])
        changes.append(
            VolumeChange(
                ticker=str(row["ticker"]),
                trading_date=row["trading_date"],
                old_volume=old_volume,
                new_volume=new_volume,
                ratio=ratio,
                pct_change=(ratio - 1.0) * 100.0,
            )
        )
    return changes


def render_volume_audit(changes: List[VolumeChange], old_path: Path, new_path: Path) -> str:
    lines = [
        "# Volume Change Audit",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Old snapshot:** `{old_path}`",
        f"**New snapshot:** `{new_path}`",
        f"**Material changes:** {len(changes)}",
        "",
        "| Ticker | Date | Old volume | New volume | Ratio | Change |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for change in changes:
        lines.append(
            f"| {change.ticker} | {change.trading_date.isoformat()} | "
            f"{change.old_volume:,} | {change.new_volume:,} | "
            f"{change.ratio:.3f}x | {change.pct_change:,.1f}% |"
        )
    lines.extend([
        "",
        "## Policy",
        "",
        "- Values are investigated, not guessed.",
        "- No unit conversion, clipping, or historical rewrite is applied by "
        "this audit.",
        "- A change requires evidence from the provider snapshot, the ingestion "
        "payload, and the current database record before acceptance.",
        "",
    ])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare volume snapshots.")
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ratio-threshold", type=float, default=DEFAULT_RATIO_THRESHOLD)
    args = parser.parse_args(argv)

    setup_logging()
    old = pd.read_parquet(args.old)
    new = pd.read_parquet(args.new)
    changes = compare_volume_changes(old, new, args.ratio_threshold)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_volume_audit(changes, args.old, args.new), encoding="utf-8"
    )
    print(json.dumps(
        [
            {
                "ticker": item.ticker,
                "trading_date": item.trading_date.isoformat(),
                "old_volume": item.old_volume,
                "new_volume": item.new_volume,
                "ratio": item.ratio,
            }
            for item in changes
        ],
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
