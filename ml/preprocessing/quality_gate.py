from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import pandas as pd

from app.core.logging import get_logger, setup_logging


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

Severity = Literal["ERROR", "WARNING", "INFO"]

DEFAULT_SPLIT_THRESHOLD = 0.35  # 35% — flag for manual review

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = _PROJECT_ROOT / "ml" / "data" / "prices_clean.parquet"
DEFAULT_REPORT_DIR = _PROJECT_ROOT / "ml" / "reports"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """A single quality finding."""

    check: str
    severity: Severity
    ticker: Optional[str]
    trading_date: Optional[date]
    message: str
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class MissingDateGroup:
    """A contiguous gap of missing weekday sessions for one ticker."""

    ticker: str
    start: date
    end: date
    missing_count: int


@dataclass
class QualityReport:
    """Aggregate quality report."""

    generated_at: datetime
    input_path: Path
    total_rows: int
    total_tickers: int
    date_start: Optional[date]
    date_end: Optional[date]
    split_threshold: float

    duplicates: List[Finding] = field(default_factory=list)
    ohlc_violations: List[Finding] = field(default_factory=list)
    positivity_violations: List[Finding] = field(default_factory=list)
    volume_violations: List[Finding] = field(default_factory=list)
    return_anomalies: List[Finding] = field(default_factory=list)
    missing_dates: List[MissingDateGroup] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return (
            len(self.duplicates)
            + len(self.ohlc_violations)
            + len(self.positivity_violations)
            + len(self.volume_violations)
        )

    @property
    def warning_count(self) -> int:
        return len(self.return_anomalies)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_duplicate_sessions(df: pd.DataFrame) -> List[Finding]:
    """Detect duplicate (ticker, trading_date) rows."""
    dup_mask = df.duplicated(subset=["ticker", "trading_date"], keep=False)
    dups = df[dup_mask]

    findings: List[Finding] = []
    for (ticker, trading_date), group in dups.groupby(["ticker", "trading_date"]):
        findings.append(Finding(
            check="duplicate_session",
            severity="ERROR",
            ticker=ticker,
            trading_date=trading_date.date() if hasattr(trading_date, "date") else trading_date,
            message=f"Duplicate row for {ticker} on {trading_date} ({len(group)} occurrences)",
            details={"count": len(group)},
        ))
    return findings


def check_ohlc_invariants(df: pd.DataFrame) -> List[Finding]:
    """high >= max(open, close), low <= min(open, close), high >= low."""
    findings: List[Finding] = []

    # high >= open
    bad = df[df["high"] < df["open"]]
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="ohlc_invariant",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"high ({row['high']}) < open ({row['open']})",
            details={"field": "high<open"},
        ))

    # high >= close
    bad = df[df["high"] < df["close"]]
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="ohlc_invariant",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"high ({row['high']}) < close ({row['close']})",
            details={"field": "high<close"},
        ))

    # low <= open
    bad = df[df["low"] > df["open"]]
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="ohlc_invariant",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"low ({row['low']}) > open ({row['open']})",
            details={"field": "low>open"},
        ))

    # low <= close
    bad = df[df["low"] > df["close"]]
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="ohlc_invariant",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"low ({row['low']}) > close ({row['close']})",
            details={"field": "low>close"},
        ))

    # high >= low
    bad = df[df["high"] < df["low"]]
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="ohlc_invariant",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"high ({row['high']}) < low ({row['low']})",
            details={"field": "high<low"},
        ))

    return findings


def check_positivity(df: pd.DataFrame) -> List[Finding]:
    """open/high/low/close must be > 0."""
    findings: List[Finding] = []
    for col in ("open", "high", "low", "close"):
        bad = df[df[col] <= 0]
        for _, row in bad.iterrows():
            findings.append(Finding(
                check="positivity",
                severity="ERROR",
                ticker=row["ticker"],
                trading_date=row["trading_date"].date(),
                message=f"{col} <= 0: {row[col]}",
                details={"field": col, "value": row[col]},
            ))
    return findings


def check_volume(df: pd.DataFrame) -> List[Finding]:
    """volume >= 0."""
    bad = df[df["volume"] < 0]
    findings: List[Finding] = []
    for _, row in bad.iterrows():
        findings.append(Finding(
            check="volume",
            severity="ERROR",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=f"negative volume: {row['volume']}",
            details={"value": row["volume"]},
        ))
    return findings


def check_return_anomalies(
    df: pd.DataFrame, threshold: float = DEFAULT_SPLIT_THRESHOLD
) -> List[Finding]:
    """
    Flag |return_1d| > threshold as split-candidate / data-anomaly.

    Not an ERROR — could be legitimate (IPO, big news) or corporate action.
    Requires manual review before downstream use.
    """
    mask = df["return_1d"].notna() & (df["return_1d"].abs() > threshold)
    anomalies = df[mask]

    findings: List[Finding] = []
    for _, row in anomalies.iterrows():
        ret = float(row["return_1d"])
        findings.append(Finding(
            check="return_anomaly",
            severity="WARNING",
            ticker=row["ticker"],
            trading_date=row["trading_date"].date(),
            message=(
                f"return_1d = {ret:+.2%} exceeds ±{threshold:.0%} threshold "
                f"(possible split / corporate action)"
            ),
            details={"return_1d": ret, "close": float(row["close"])},
        ))
    return findings


def check_missing_weekdays(df: pd.DataFrame) -> List[MissingDateGroup]:
    """
    Detect contiguous weekday gaps per ticker.

    Weekends excluded (expected non-trading). Gaps grouped into single
    findings to avoid noise from consecutive holidays.
    """
    results: List[MissingDateGroup] = []

    for ticker, group in df.groupby("ticker"):
        dates = sorted(group["trading_date"].dt.date.tolist())
        if len(dates) < 2:
            continue

        # Walk forward; collect contiguous gaps of weekdays only
        gap_start: Optional[date] = None
        gap_end: Optional[date] = None

        for prev, curr in zip(dates, dates[1:]):
            delta = (curr - prev).days
            if delta <= 1:
                continue  # consecutive (or same day — impossible here)

            # Days strictly between prev and curr
            cursor = prev + timedelta(days=1)
            missing_weekdays: List[date] = []
            while cursor < curr:
                if cursor.weekday() < 5:  # Mon-Fri
                    missing_weekdays.append(cursor)
                cursor += timedelta(days=1)

            if not missing_weekdays:
                continue

            # Group contiguous missing weekdays
            local_start = missing_weekdays[0]
            local_end = missing_weekdays[0]
            for d in missing_weekdays[1:]:
                if (d - local_end).days == 1:
                    local_end = d
                else:
                    results.append(MissingDateGroup(
                        ticker=ticker,
                        start=local_start,
                        end=local_end,
                        missing_count=(local_end - local_start).days + 1,
                    ))
                    local_start = d
                    local_end = d

            results.append(MissingDateGroup(
                ticker=ticker,
                start=local_start,
                end=local_end,
                missing_count=(local_end - local_start).days + 1,
            ))

    return results


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(
    df: pd.DataFrame,
    input_path: Path,
    split_threshold: float = DEFAULT_SPLIT_THRESHOLD,
) -> QualityReport:
    """Run all checks and assemble the report."""
    date_start = df["trading_date"].min().date() if not df.empty else None
    date_end = df["trading_date"].max().date() if not df.empty else None

    report = QualityReport(
        generated_at=datetime.now(timezone.utc),
        input_path=input_path,
        total_rows=len(df),
        total_tickers=df["ticker"].nunique() if not df.empty else 0,
        date_start=date_start,
        date_end=date_end,
        split_threshold=split_threshold,
    )

    if df.empty:
        return report

    report.duplicates = check_duplicate_sessions(df)
    report.ohlc_violations = check_ohlc_invariants(df)
    report.positivity_violations = check_positivity(df)
    report.volume_violations = check_volume(df)
    report.return_anomalies = check_return_anomalies(df, threshold=split_threshold)
    report.missing_dates = check_missing_weekdays(df)

    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _finding_table(findings: List[Finding]) -> List[str]:
    lines = ["| Ticker | Date | Message |", "|---|---|---|"]
    for f in findings[:200]:  # cap to avoid megabyte reports
        d = f.trading_date.isoformat() if f.trading_date else "—"
        t = f.ticker or "—"
        lines.append(f"| {t} | {d} | {f.message} |")
    if len(findings) > 200:
        lines.append(f"| ... | ... | (+{len(findings) - 200} more) |")
    return lines


def render_markdown(report: QualityReport) -> str:
    lines: List[str] = []
    lines.append("# FinSentiMind — Price Quality Gate Report")
    lines.append("")
    lines.append(f"**Generated:** {report.generated_at.isoformat()}")
    lines.append(f"**Input:** `{report.input_path}`")
    lines.append(f"**Split threshold:** ±{report.split_threshold:.0%}")
    lines.append(f"**Rows:** {report.total_rows:,}")
    lines.append(f"**Tickers:** {report.total_tickers}")
    if report.date_start and report.date_end:
        lines.append(
            f"**Date range:** {report.date_start} → {report.date_end}"
        )
    lines.append("")

    # --- Summary ---
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- 🔴 **ERROR**: {report.error_count}")
    lines.append(f"- 🟡 **WARNING**: {report.warning_count}")
    lines.append(f"- 📅 Missing weekday groups: {len(report.missing_dates)}")
    lines.append("")

    if report.has_errors:
        lines.append("### ❌ FAILED — resolve ERROR findings before proceeding")
    else:
        lines.append("### ✅ PASSED — no blocking errors")
    lines.append("")

    # --- Duplicates ---
    lines.append(f"## Duplicate Sessions ({len(report.duplicates)})")
    lines.append("")
    if report.duplicates:
        lines.extend(_finding_table(report.duplicates))
    else:
        lines.append("_None._")
    lines.append("")

    # --- OHLC ---
    lines.append(f"## OHLC Invariant Violations ({len(report.ohlc_violations)})")
    lines.append("")
    if report.ohlc_violations:
        lines.extend(_finding_table(report.ohlc_violations))
    else:
        lines.append("_None._")
    lines.append("")

    # --- Positivity ---
    lines.append(f"## Price Positivity Violations ({len(report.positivity_violations)})")
    lines.append("")
    if report.positivity_violations:
        lines.extend(_finding_table(report.positivity_violations))
    else:
        lines.append("_None._")
    lines.append("")

    # --- Volume ---
    lines.append(f"## Volume Violations ({len(report.volume_violations)})")
    lines.append("")
    if report.volume_violations:
        lines.extend(_finding_table(report.volume_violations))
    else:
        lines.append("_None._")
    lines.append("")

    # --- Return anomalies / split candidates ---
    lines.append(f"## Return Anomalies / Split Candidates ({len(report.return_anomalies)})")
    lines.append("")
    if report.return_anomalies:
        lines.append("⚠️ Manual review required. These may be legitimate (IPO, split, big news).")
        lines.append("")
        lines.extend(_finding_table(report.return_anomalies))
    else:
        lines.append("_None._")
    lines.append("")

    # --- Missing dates ---
    lines.append(f"## Missing Weekday Groups ({len(report.missing_dates)})")
    lines.append("")
    if report.missing_dates:
        lines.append("| Ticker | Start | End | Weekdays Missing |")
        lines.append("|---|---|---|---:|")
        for g in report.missing_dates[:200]:
            lines.append(
                f"| {g.ticker} | {g.start} | {g.end} | {g.missing_count} |"
            )
        if len(report.missing_dates) > 200:
            lines.append(f"| ... | ... | ... | (+{len(report.missing_dates) - 200} more) |")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Report generated automatically. No data was modified._")

    return "\n".join(lines)


def save_report(report: QualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    logger.info("Report saved to %s", path)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_clean_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Input Parquet not found: {path}. "
            f"Run `python -m ml.preprocessing.load_prices` first."
        )
    df = pd.read_parquet(path)
    logger.info("Loaded %d rows from %s", len(df), path)
    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_REPORT_DIR / f"quality_gate_{stamp}.md"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Price quality gate (TASK 102C)."
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help=f"Input Parquet (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Report output path (default: ml/reports/quality_gate_<ts>.md).",
    )
    parser.add_argument(
        "--split-threshold", type=float, default=DEFAULT_SPLIT_THRESHOLD,
        help=f"Return anomaly threshold (default: {DEFAULT_SPLIT_THRESHOLD}).",
    )
    args = parser.parse_args(argv)

    setup_logging()
    df = load_clean_prices(args.input)
    report = build_report(df, input_path=args.input, split_threshold=args.split_threshold)

    output = args.output or _default_report_path()
    save_report(report, output)

    print(render_markdown(report))
    return 0 if not report.has_errors else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
