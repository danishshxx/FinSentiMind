"""Quality checks for manually ingested fundamental data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.fundamentals.normalization import STATUS_NON_IDR
from ml.fundamentals.repository import FundamentalRepository
from ml.fundamentals.schemas import LOGICAL_STATEMENT_KEYS


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = _PROJECT_ROOT / "ml" / "reports" / "fundamental_quality.md"


@dataclass
class FundamentalFinding:
    check: str
    severity: str
    message: str
    ticker: Optional[str] = None
    period_end: Optional[str] = None
    details: Dict[str, object] = field(default_factory=dict)


@dataclass
class FundamentalQualityReport:
    generated_at: str
    rows: int
    findings: List[FundamentalFinding]

    @property
    def errors(self) -> List[FundamentalFinding]:
        return [item for item in self.findings if item.severity == "ERROR"]

    @property
    def warnings(self) -> List[FundamentalFinding]:
        return [item for item in self.findings if item.severity == "WARNING"]

    @property
    def infos(self) -> List[FundamentalFinding]:
        return [item for item in self.findings if item.severity == "INFO"]


def run_quality_checks(df: pd.DataFrame) -> FundamentalQualityReport:
    findings: List[FundamentalFinding] = []
    if df.empty:
        return FundamentalQualityReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            rows=0,
            findings=[
                FundamentalFinding(
                    check="dataset_present",
                    severity="ERROR",
                    message="Fundamental statement dataset is empty.",
                )
            ],
        )

    required = LOGICAL_STATEMENT_KEYS + [
        "period_start", "value", "published_at", "available_at", "unit_scale",
    ]
    for column in required:
        if column not in df.columns or df[column].isna().any():
            count = 0 if column not in df.columns else int(df[column].isna().sum())
            findings.append(FundamentalFinding(
                check="required_value",
                severity="ERROR",
                message=f"Missing or null required value in {column} ({count} row(s)).",
                details={"column": column, "null_count": count},
            ))

    if "revision" in df.columns:
        duplicate = df.duplicated(
            subset=LOGICAL_STATEMENT_KEYS + ["revision"], keep=False
        )
        if duplicate.any():
            findings.append(FundamentalFinding(
                check="duplicate_revision",
                severity="ERROR",
                message=f"{int(duplicate.sum())} duplicate key+revision row(s).",
            ))

    if "period_start" in df.columns and "period_end" in df.columns:
        start = pd.to_datetime(df["period_start"], errors="coerce")
        end = pd.to_datetime(df["period_end"], errors="coerce")
        invalid = start > end
        if invalid.any():
            findings.append(FundamentalFinding(
                check="period_order",
                severity="ERROR",
                message=f"{int(invalid.sum())} row(s) have period_start > period_end.",
            ))

    if {"published_at", "available_at"}.issubset(df.columns):
        published = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
        available = pd.to_datetime(df["available_at"], errors="coerce", utc=True)
        invalid = available < published
        if invalid.any():
            findings.append(FundamentalFinding(
                check="point_in_time",
                severity="ERROR",
                message=f"{int(invalid.sum())} row(s) are available before publication.",
            ))

    if {"unit_scale", "currency", "ticker", "statement_type", "period_end"}.issubset(df.columns):
        group_keys = ["ticker", "statement_type", "period_end", "consolidation"]
        for key, group in df.groupby(group_keys, dropna=False):
            units = set(group["unit_scale"].dropna().astype(str))
            currencies = set(group["currency"].dropna().astype(str))
            if len(units) > 1:
                findings.append(FundamentalFinding(
                    check="mixed_units",
                    severity="ERROR",
                    message=f"Mixed unit scales within a statement period: {sorted(units)}.",
                    ticker=str(key[0]),
                    period_end=str(key[2]),
                ))
            if len(currencies) > 1:
                findings.append(FundamentalFinding(
                    check="mixed_currency",
                    severity="ERROR",
                    message=f"Mixed currencies within a statement period: {sorted(currencies)}.",
                    ticker=str(key[0]),
                    period_end=str(key[2]),
                ))

    if "normalization_status" in df.columns:
        non_idr = df["normalization_status"] == STATUS_NON_IDR
        if non_idr.any():
            findings.append(FundamentalFinding(
                check="currency_not_converted",
                severity="WARNING",
                message=(
                    f"{int(non_idr.sum())} row(s) are not in IDR and were not "
                    "currency-converted; do not compare directly with IDR metrics."
                ),
            ))

    if "restatement_of" in df.columns:
        count = int(df["restatement_of"].notna().sum())
        if count:
            findings.append(FundamentalFinding(
                check="restatements_present",
                severity="INFO",
                message=f"{count} restatement/revision row(s) preserved in history.",
            ))

    null_values = int(df["value"].isna().sum()) if "value" in df.columns else len(df)
    if null_values:
        findings.append(FundamentalFinding(
            check="missing_values_preserved",
            severity="INFO",
            message=(
                f"{null_values} line-item value(s) are missing and were preserved "
                "as NULL, not converted to zero."
            ),
        ))

    return FundamentalQualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        rows=len(df),
        findings=findings,
    )


def render_markdown(report: FundamentalQualityReport) -> str:
    lines = [
        "# Fundamental Data Quality Report",
        "",
        f"**Generated:** {report.generated_at}",
        f"**Rows:** {report.rows}",
        f"**ERROR:** {len(report.errors)}",
        f"**WARNING:** {len(report.warnings)}",
        f"**INFO:** {len(report.infos)}",
        "",
        "| Severity | Check | Ticker | Period | Message |",
        "|---|---|---|---|---|",
    ]
    for item in report.findings:
        lines.append(
            f"| {item.severity} | {item.check} | {item.ticker or '-'} | "
            f"{item.period_end or '-'} | {item.message} |"
        )
    if not report.findings:
        lines.append("| - | - | - | - | No findings. |")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check fundamental data quality.")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    setup_logging()
    from ml.fundamentals.ingestion import DEFAULT_DATA_DIR
    repo = FundamentalRepository(args.data_dir or DEFAULT_DATA_DIR)
    report = run_quality_checks(repo.statements(include_history=True))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    return 1 if report.errors else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
