"""Canonical capital-flow observation schema.

A flow observation is an analytical aggregation of reported buy/sell values.
It is not proof of the identity or motive of an investor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

import pandas as pd


SCOPES = ("market", "stock")
INVESTOR_CLASSES = (
    "foreign",
    "domestic_institutional",
    "retail",
    "broker",
    "unknown",
)

FLOW_OBSERVATION_COLUMNS: List[str] = [
    "date",
    "scope",
    "ticker",
    "investor_class",
    "broker_code",
    "buy_value",
    "sell_value",
    "net_value",
    "gross_value",
    "currency",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "revision",
    "ingested_at",
]

FLOW_INPUT_COLUMNS: List[str] = [
    "date",
    "scope",
    "ticker",
    "investor_class",
    "broker_code",
    "buy_value",
    "sell_value",
    "currency",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "revision",
]

FLOW_KEYS: List[str] = [
    "date",
    "scope",
    "ticker",
    "investor_class",
    "broker_code",
    "revision",
]


class CapitalFlowSchemaError(ValueError):
    """Raised for an invalid capital-flow import."""


def _require(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise CapitalFlowSchemaError(
            f"capital-flow import missing columns: {missing}"
        )


def validate_flow_observations(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, FLOW_INPUT_COLUMNS)
    if df.empty:
        raise CapitalFlowSchemaError("capital-flow import is empty")
    out = df.copy()
    out["scope"] = out["scope"].fillna("").astype(str).str.lower().str.strip()
    if not out["scope"].isin(SCOPES).all():
        raise CapitalFlowSchemaError("scope must be market or stock")
    out["ticker"] = out["ticker"].where(out["ticker"].notna(), None)
    out.loc[out["ticker"].notna(), "ticker"] = (
        out.loc[out["ticker"].notna(), "ticker"].astype(str).str.upper()
    )
    stock_without_ticker = (out["scope"] == "stock") & out["ticker"].isna()
    if stock_without_ticker.any():
        raise CapitalFlowSchemaError("stock-scope observations require a ticker")

    out["investor_class"] = (
        out["investor_class"].fillna("unknown").astype(str).str.lower().str.strip()
    )
    if not out["investor_class"].isin(INVESTOR_CLASSES).all():
        raise CapitalFlowSchemaError("invalid investor_class")

    out["broker_code"] = out["broker_code"].where(
        out["broker_code"].notna(), None
    )
    out.loc[out["broker_code"].notna(), "broker_code"] = (
        out.loc[out["broker_code"].notna(), "broker_code"]
        .astype(str).str.upper().str.strip()
    )
    broker_without_code = (out["investor_class"] == "broker") & out["broker_code"].isna()
    if broker_without_code.any():
        raise CapitalFlowSchemaError("broker observations require broker_code")

    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    if out["date"].isna().any():
        raise CapitalFlowSchemaError("date must be a valid date")

    out["buy_value"] = pd.to_numeric(out["buy_value"], errors="coerce")
    out["sell_value"] = pd.to_numeric(out["sell_value"], errors="coerce")
    if (out["buy_value"] < 0).any() or (out["sell_value"] < 0).any():
        raise CapitalFlowSchemaError("buy_value and sell_value cannot be negative")
    if out["buy_value"].isna().all() and out["sell_value"].isna().all():
        raise CapitalFlowSchemaError("buy_value and sell_value cannot both be empty")

    out["net_value"] = out["buy_value"] - out["sell_value"]
    out["gross_value"] = out["buy_value"] + out["sell_value"]
    out["currency"] = out["currency"].fillna("").astype(str).str.upper()
    if (out["currency"].str.len() != 3).any():
        raise CapitalFlowSchemaError("currency must be a 3-letter code")

    out["published_at"] = pd.to_datetime(
        out["published_at"], errors="coerce", utc=True
    )
    out["available_at"] = pd.to_datetime(
        out["available_at"], errors="coerce", utc=True
    )
    if out["published_at"].isna().any() or out["available_at"].isna().any():
        raise CapitalFlowSchemaError("published_at/available_at must be valid")
    if (out["available_at"] < out["published_at"]).any():
        raise CapitalFlowSchemaError("available_at cannot precede published_at")

    out["revision"] = pd.to_numeric(
        out["revision"], errors="coerce"
    ).fillna(0).astype("int64")
    if (out["revision"] < 0).any():
        raise CapitalFlowSchemaError("revision must be >= 0")

    duplicates = out.duplicated(subset=FLOW_KEYS, keep=False)
    if duplicates.any():
        raise CapitalFlowSchemaError("duplicate flow key and revision")

    out["source_id"] = out["source_id"].fillna("").astype(str)
    out["source_document"] = out["source_document"].fillna("").astype(str)
    out["ingested_at"] = datetime.now(timezone.utc).isoformat()
    for column in FLOW_OBSERVATION_COLUMNS:
        if column not in out.columns:
            out[column] = None
    return out[FLOW_OBSERVATION_COLUMNS].reset_index(drop=True)
