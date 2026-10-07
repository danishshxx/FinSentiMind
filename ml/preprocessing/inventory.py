from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from app.core.aliases import WATCH_TICKERS
from app.core.logging import get_logger, setup_logging
from app.database.supabase_client import fetch_all


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# From docs/universe.md — freeze this per experiment.
UNIVERSE_SNAPSHOT_DATE = "2026-08-03"

# 2 years × ~250 trading days = ~500. Give margin for holidays.
DEFAULT_MIN_HISTORY = 400

# A date is a "trading session" if >= this fraction of the universe has data.
DEFAULT_CONSENSUS_THRESHOLD = 0.6

# Coverage threshold for the backfill recommendation (GPT's "coverage-based").
DEFAULT_COVERAGE_TARGET = 0.90

# Project root — used to resolve default report path.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REPORT_DIR = _PROJECT_ROOT / "ml" / "reports"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TickerInventory:
    """Per-ticker coverage stats."""

    ticker: str
    row_count: int
    first_date: Optional[date]
    last_date: Optional[date]
    meets_min_history: bool


@dataclass
class InventoryReport:
    """Full inventory snapshot."""

    generated_at: datetime
    universe: List[str]
    universe_snapshot_date: str
    total_rows: int
    date_range_start: Optional[date]
    date_range_end: Optional[date]
    per_ticker: List[TickerInventory]
    consensus_sessions: List[date]
    missing_tickers: List[str]
    tickers_below_min_history: List[TickerInventory]
    coverage_ratio: float
    min_history_threshold: int
    needs_backfill: bool
    backfill_reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_raw_prices(universe: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """
    Load all price rows for the given universe from Supabase.

    Args:
        universe: Ticker list. Defaults to WATCH_TICKERS.

    Returns:
        DataFrame with columns: kode_saham, tanggal (as date).
        Empty DataFrame if no rows.
    """
    tickers = list(universe) if universe is not None else list(WATCH_TICKERS)
    logger.info("Loading price data for %d tickers from Supabase...", len(tickers))

    rows = fetch_all(
        table="harga_saham",
        select="kode_saham, tanggal",
        filters={"kode_saham": tickers},
        order_by="id",
    )

    if not rows:
        logger.warning("No rows returned from harga_saham for the universe.")
        return pd.DataFrame(columns=["kode_saham", "tanggal"])

    df = pd.DataFrame(rows)
    df["tanggal"] = pd.to_datetime(df["tanggal"], errors="coerce").dt.date
    df = df.dropna(subset=["tanggal"]).reset_index(drop=True)

    logger.info("Loaded %d raw price rows.", len(df))
    return df


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------

def compute_per_ticker_inventory(
    df: pd.DataFrame,
    universe: List[str],
    min_history: int,
) -> List[TickerInventory]:
    """
    Compute per-ticker stats. Tickers with zero rows are included with
    row_count=0.
    """
    grouped = df.groupby("kode_saham") if not df.empty else None
    results: List[TickerInventory] = []

    for ticker in universe:
        if grouped is None or ticker not in grouped.groups:
            results.append(
                TickerInventory(
                    ticker=ticker,
                    row_count=0,
                    first_date=None,
                    last_date=None,
                    meets_min_history=False,
                )
            )
            continue

        sub = df[df["kode_saham"] == ticker]
        dates = sorted(sub["tanggal"].tolist())
        count = len(dates)

        results.append(
            TickerInventory(
                ticker=ticker,
                row_count=count,
                first_date=dates[0],
                last_date=dates[-1],
                meets_min_history=count >= min_history,
            )
        )

    return results


def compute_session_coverage(
    df: pd.DataFrame, universe_size: int
) -> pd.DataFrame:
    """
    Cross-sectional coverage per date.

    Returns DataFrame with: tanggal, ticker_count, coverage_ratio.
    Sorted ascending by tanggal.
    """
    if df.empty:
        return pd.DataFrame(columns=["tanggal", "ticker_count", "coverage_ratio"])

    counts = df.groupby("tanggal")["kode_saham"].nunique().reset_index()
    counts.columns = ["tanggal", "ticker_count"]
    counts["coverage_ratio"] = counts["ticker_count"] / universe_size
    return counts.sort_values("tanggal").reset_index(drop=True)


def identify_consensus_sessions(
    session_coverage: pd.DataFrame, threshold: float
) -> List[date]:
    """Dates where coverage >= threshold are consensus trading sessions."""
    if session_coverage.empty:
        return []
    mask = session_coverage["coverage_ratio"] >= threshold
    return session_coverage.loc[mask, "tanggal"].tolist()


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(
    df: pd.DataFrame,
    universe: List[str],
    min_history: int,
    consensus_threshold: float,
    coverage_target: float,
) -> InventoryReport:
    """Assemble the full report object from raw data."""
    per_ticker = compute_per_ticker_inventory(df, universe, min_history)
    session_coverage = compute_session_coverage(df, len(universe))
    consensus = identify_consensus_sessions(session_coverage, consensus_threshold)

    missing_tickers = [t.ticker for t in per_ticker if t.row_count == 0]
    below_min = [t for t in per_ticker if not t.meets_min_history and t.row_count > 0]

    # Coverage: fraction of universe meeting min_history.
    meets = sum(1 for t in per_ticker if t.meets_min_history)
    coverage_ratio = meets / len(universe) if universe else 0.0

    date_start = df["tanggal"].min() if not df.empty else None
    date_end = df["tanggal"].max() if not df.empty else None

    # --- Backfill decision ---
    needs_backfill = False
    reasons: List[str] = []

    if coverage_ratio < coverage_target:
        needs_backfill = True
        reasons.append(
            f"Coverage {coverage_ratio:.1%} < target {coverage_target:.0%} "
            f"({meets}/{len(universe)} tickers meet min_history={min_history})"
        )
    if missing_tickers:
        needs_backfill = True
        reasons.append(
            f"{len(missing_tickers)} ticker(s) have ZERO rows: "
            f"{', '.join(missing_tickers[:10])}"
            + (" ..." if len(missing_tickers) > 10 else "")
        )
    if not consensus:
        needs_backfill = True
        reasons.append(
            "No consensus trading sessions identified "
            f"(threshold={consensus_threshold:.0%})"
        )
    if date_start and date_end:
        span_days = (date_end - date_start).days
        if span_days < 700:
            needs_backfill = True
            reasons.append(
                f"Date span {span_days} days < 700 days "
                f"(~2 years expected)"
            )
    if not reasons:
        reasons.append("Coverage and span within targets — no backfill needed.")

    return InventoryReport(
        generated_at=datetime.now(timezone.utc),
        universe=universe,
        universe_snapshot_date=UNIVERSE_SNAPSHOT_DATE,
        total_rows=len(df),
        date_range_start=date_start,
        date_range_end=date_end,
        per_ticker=per_ticker,
        consensus_sessions=consensus,
        missing_tickers=missing_tickers,
        tickers_below_min_history=below_min,
        coverage_ratio=coverage_ratio,
        min_history_threshold=min_history,
        needs_backfill=needs_backfill,
        backfill_reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_markdown(report: InventoryReport) -> str:
    """Render the report as human-readable Markdown."""
    lines: List[str] = []
    lines.append("# FinSentiMind — Price Data Inventory")
    lines.append("")
    lines.append(f"**Generated:** {report.generated_at.isoformat()}")
    lines.append(f"**Universe:** {len(report.universe)} tickers "
                 f"(snapshot {report.universe_snapshot_date})")
    lines.append(f"**Total rows:** {report.total_rows:,}")
    if report.date_range_start and report.date_range_end:
        lines.append(
            f"**Date range:** {report.date_range_start} → {report.date_range_end} "
            f"({(report.date_range_end - report.date_range_start).days} days)"
        )
    else:
        lines.append("**Date range:** (no data)")
    lines.append("")

    # --- Coverage summary ---
    lines.append("## Coverage Summary")
    lines.append("")
    lines.append(f"- Tickers meeting min_history ({report.min_history_threshold}): "
                 f"**{report.coverage_ratio:.1%}**")
    lines.append(f"- Tickers with zero rows: **{len(report.missing_tickers)}**")
    lines.append(f"- Tickers below min_history (but >0): "
                 f"**{len(report.tickers_below_min_history)}**")
    lines.append(f"- Consensus trading sessions: **{len(report.consensus_sessions)}**")
    lines.append("")

    # --- Per-ticker table ---
    lines.append("## Per-Ticker Inventory")
    lines.append("")
    lines.append("| Ticker | Rows | First Date | Last Date | Meets Min? |")
    lines.append("|---|---:|---|---|:---:|")
    for t in report.per_ticker:
        first = str(t.first_date) if t.first_date else "—"
        last = str(t.last_date) if t.last_date else "—"
        meets = "✅" if t.meets_min_history else "❌"
        lines.append(f"| {t.ticker} | {t.row_count} | {first} | {last} | {meets} |")
    lines.append("")

    # --- Recommendation ---
    lines.append("## Backfill Recommendation")
    lines.append("")
    if report.needs_backfill:
        lines.append("### 🟡 BACKFILL RECOMMENDED")
    else:
        lines.append("### ✅ NO BACKFILL NEEDED")
    lines.append("")
    for reason in report.backfill_reasons:
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_This report is an inventory snapshot only. No backfill or "
                 "quality gate was applied._")

    return "\n".join(lines)


def save_report(report: InventoryReport, path: Path) -> None:
    """Write report to a Markdown file. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_markdown(report)
    path.write_text(content, encoding="utf-8")
    logger.info("Report saved to %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _DEFAULT_REPORT_DIR / f"inventory_{stamp}.md"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inventory existing price data (TASK 102A Step 1)."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Report output path (default: ml/reports/inventory_<timestamp>.md).",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=DEFAULT_MIN_HISTORY,
        help=f"Minimum rows per ticker (default: {DEFAULT_MIN_HISTORY}).",
    )
    parser.add_argument(
        "--consensus-threshold",
        type=float,
        default=DEFAULT_CONSENSUS_THRESHOLD,
        help=f"Cross-sectional threshold (default: {DEFAULT_CONSENSUS_THRESHOLD}).",
    )
    parser.add_argument(
        "--coverage-target",
        type=float,
        default=DEFAULT_COVERAGE_TARGET,
        help=f"Coverage ratio target (default: {DEFAULT_COVERAGE_TARGET}).",
    )
    args = parser.parse_args(argv)

    setup_logging()
    universe = list(WATCH_TICKERS)

    df = load_raw_prices(universe)
    report = build_report(
        df=df,
        universe=universe,
        min_history=args.min_history,
        consensus_threshold=args.consensus_threshold,
        coverage_target=args.coverage_target,
    )

    output = args.output or _default_output_path()
    save_report(report, output)

    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
