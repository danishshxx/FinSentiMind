"""Schemas and validation for the fundamental-data layer.

The storage model is intentionally long-form: one row per reported line item.
That preserves statement structure better than a wide table whose columns
change across issuers. Units are normalized only by a documented scale factor;
currency conversion is never invented.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional

import pandas as pd


STATEMENT_TYPES = ("income_statement", "balance_sheet", "cash_flow")
PERIOD_TYPES = ("annual", "quarterly")
CONSOLIDATION_TYPES = ("consolidated", "standalone")
UNIT_SCALES = (
    "ones",
    "thousands",
    "millions",
    "billions",
    "trillions",
    "juta",
    "miliar",
    "triliun",
)
ACTION_TYPES = (
    "split",
    "reverse_split",
    "bonus_share",
    "rights_issue",
    "dividend",
    "other",
)


class FundamentalSchemaError(ValueError):
    """Raised when a fundamental import violates the documented schema."""


FINANCIAL_STATEMENT_COLUMNS: List[str] = [
    "ticker",
    "statement_type",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_start",
    "period_end",
    "consolidation",
    "currency",
    "unit_scale",
    "line_item",
    "value",
    "original_value",
    "original_unit_scale",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "source_page",
    "restatement_of",
    "revision",
    "normalization_status",
    "ingested_at",
]

FINANCIAL_STATEMENT_INPUT_COLUMNS: List[str] = [
    "ticker",
    "statement_type",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_start",
    "period_end",
    "consolidation",
    "currency",
    "unit_scale",
    "line_item",
    "value",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "source_page",
    "restatement_of",
    "revision",
]

CORPORATE_ACTION_COLUMNS: List[str] = [
    "ticker",
    "action_type",
    "ex_date",
    "record_date",
    "payment_date",
    "ratio_from",
    "ratio_to",
    "cash_amount",
    "currency",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "revision",
    "ingested_at",
]

DIVIDEND_COLUMNS: List[str] = [
    "ticker",
    "fiscal_year",
    "dividend_type",
    "ex_date",
    "payment_date",
    "dividend_per_share",
    "currency",
    "tax_rate",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "revision",
    "ingested_at",
]

LOGICAL_STATEMENT_KEYS: List[str] = [
    "ticker",
    "statement_type",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_end",
    "consolidation",
    "line_item",
    "currency",
]


def _require_columns(df: pd.DataFrame, required: Iterable[str], name: str) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise FundamentalSchemaError(
            f"{name} missing required columns: {missing}. "
            f"Present columns: {list(df.columns)}"
        )


def _parse_timestamp(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _validate_point_in_time(df: pd.DataFrame) -> None:
    published = _parse_timestamp(df["published_at"])
    available = _parse_timestamp(df["available_at"])
    invalid = published.isna() | available.isna()
    if invalid.any():
        sample = df.loc[invalid, ["ticker", "available_at", "published_at"]].head(5)
        raise FundamentalSchemaError(
            "published_at and available_at must be valid timestamps. "
            f"Invalid sample: {sample.to_dict(orient='records')}"
        )
    impossible = available < published
    if impossible.any():
        sample = df.loc[
            impossible, ["ticker", "available_at", "published_at"]
        ].head(5)
        raise FundamentalSchemaError(
            "available_at cannot precede published_at. "
            f"Invalid sample: {sample.to_dict(orient='records')}"
        )


def validate_financial_statements(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize a manual financial-statement import."""
    _require_columns(df, FINANCIAL_STATEMENT_INPUT_COLUMNS, "financial statements")
    if df.empty:
        raise FundamentalSchemaError("financial statement import is empty")

    out = df.copy()
    out["ticker"] = out["ticker"].fillna("").astype(str).str.strip().str.upper()
    if (out["ticker"] == "").any():
        raise FundamentalSchemaError("ticker cannot be empty")
    out["line_item"] = out["line_item"].fillna("").astype(str).str.strip()
    if (out["line_item"] == "").any():
        raise FundamentalSchemaError("line_item cannot be empty")

    out["statement_type"] = out["statement_type"].astype(str).str.lower().str.strip()
    if not out["statement_type"].isin(STATEMENT_TYPES).all():
        bad = sorted(set(out["statement_type"]) - set(STATEMENT_TYPES))
        raise FundamentalSchemaError(f"invalid statement_type values: {bad}")

    out["period_type"] = out["period_type"].astype(str).str.lower().str.strip()
    if not out["period_type"].isin(PERIOD_TYPES).all():
        bad = sorted(set(out["period_type"]) - set(PERIOD_TYPES))
        raise FundamentalSchemaError(f"invalid period_type values: {bad}")

    out["consolidation"] = (
        out["consolidation"].astype(str).str.lower().str.strip()
    )
    if not out["consolidation"].isin(CONSOLIDATION_TYPES).all():
        bad = sorted(set(out["consolidation"]) - set(CONSOLIDATION_TYPES))
        raise FundamentalSchemaError(f"invalid consolidation values: {bad}")

    out["fiscal_year"] = pd.to_numeric(out["fiscal_year"], errors="coerce")
    if out["fiscal_year"].isna().any():
        raise FundamentalSchemaError("fiscal_year must be numeric")
    out["fiscal_year"] = out["fiscal_year"].astype("int64")

    quarter = pd.to_numeric(out["fiscal_quarter"], errors="coerce")
    quarterly = out["period_type"] == "quarterly"
    annual = out["period_type"] == "annual"
    if quarter[quarterly].isna().any():
        raise FundamentalSchemaError("quarterly rows require fiscal_quarter")
    if (~quarter[quarterly].between(1, 4)).any():
        raise FundamentalSchemaError("quarterly fiscal_quarter must be 1..4")
    if quarter[annual].notna().any():
        raise FundamentalSchemaError("annual rows must have blank fiscal_quarter")
    out["fiscal_quarter"] = quarter.astype("Int64")

    out["period_start"] = pd.to_datetime(out["period_start"], errors="coerce").dt.date
    out["period_end"] = pd.to_datetime(out["period_end"], errors="coerce").dt.date
    if out["period_start"].isna().any() or out["period_end"].isna().any():
        raise FundamentalSchemaError("period_start and period_end must be valid dates")
    if (pd.to_datetime(out["period_start"]) > pd.to_datetime(out["period_end"])).any():
        raise FundamentalSchemaError("period_start cannot follow period_end")

    out["currency"] = out["currency"].fillna("").astype(str).str.upper().str.strip()
    if (out["currency"].str.len() != 3).any():
        raise FundamentalSchemaError("currency must be a 3-letter code")

    out["unit_scale"] = out["unit_scale"].fillna("").astype(str).str.lower().str.strip()
    if not out["unit_scale"].isin(UNIT_SCALES).all():
        bad = sorted(set(out["unit_scale"]) - set(UNIT_SCALES))
        raise FundamentalSchemaError(f"unsupported unit_scale values: {bad}")

    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out["revision"] = pd.to_numeric(
        out.get("revision", 0), errors="coerce"
    ).fillna(0).astype("int64")
    if (out["revision"] < 0).any():
        raise FundamentalSchemaError("revision must be >= 0")

    _validate_point_in_time(out)

    logical = LOGICAL_STATEMENT_KEYS + ["revision"]
    duplicates = out.duplicated(subset=logical, keep=False)
    if duplicates.any():
        sample = out.loc[duplicates, logical].head(5).to_dict(orient="records")
        raise FundamentalSchemaError(
            "duplicate statement rows for the same logical key and revision. "
            f"Sample: {sample}"
        )

    out["published_at"] = _parse_timestamp(out["published_at"])
    out["available_at"] = _parse_timestamp(out["available_at"])
    out["source_id"] = out["source_id"].fillna("").astype(str)
    out["source_document"] = out["source_document"].fillna("").astype(str)
    out["source_page"] = pd.to_numeric(
        out.get("source_page"), errors="coerce"
    ).astype("Int64")
    out["restatement_of"] = out["restatement_of"].where(
        out["restatement_of"].notna(), None
    )
    out["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return out


def validate_corporate_actions(df: pd.DataFrame) -> pd.DataFrame:
    required = [c for c in CORPORATE_ACTION_COLUMNS if c != "ingested_at"]
    _require_columns(df, required, "corporate actions")
    if df.empty:
        raise FundamentalSchemaError("corporate action import is empty")
    out = df.copy()
    out["ticker"] = out["ticker"].fillna("").astype(str).str.strip().str.upper()
    out["action_type"] = out["action_type"].astype(str).str.lower().str.strip()
    if not out["action_type"].isin(ACTION_TYPES).all():
        raise FundamentalSchemaError("invalid corporate action_type")
    for column in ("ex_date", "record_date", "payment_date"):
        out[column] = pd.to_datetime(out[column], errors="coerce").dt.date
    _validate_point_in_time(out)
    out["published_at"] = _parse_timestamp(out["published_at"])
    out["available_at"] = _parse_timestamp(out["available_at"])
    out["revision"] = pd.to_numeric(
        out.get("revision", 0), errors="coerce"
    ).fillna(0).astype("int64")
    out["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return out


def validate_dividends(df: pd.DataFrame) -> pd.DataFrame:
    required = [c for c in DIVIDEND_COLUMNS if c != "ingested_at"]
    _require_columns(df, required, "dividends")
    if df.empty:
        raise FundamentalSchemaError("dividend import is empty")
    out = df.copy()
    out["ticker"] = out["ticker"].fillna("").astype(str).str.strip().str.upper()
    for column in ("ex_date", "payment_date"):
        out[column] = pd.to_datetime(out[column], errors="coerce").dt.date
    if out["ex_date"].isna().any():
        raise FundamentalSchemaError("ex_date must be a valid date")
    out["dividend_per_share"] = pd.to_numeric(
        out["dividend_per_share"], errors="coerce"
    )
    if out["dividend_per_share"].isna().any():
        raise FundamentalSchemaError("dividend_per_share cannot be missing")
    out["currency"] = out["currency"].fillna("").astype(str).str.upper()
    _validate_point_in_time(out)
    out["published_at"] = _parse_timestamp(out["published_at"])
    out["available_at"] = _parse_timestamp(out["available_at"])
    out["ingested_at"] = datetime.now(timezone.utc).isoformat()
    return out
