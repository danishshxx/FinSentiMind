"""Macro observation schema and indicator catalog."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List

import pandas as pd


MACRO_CATALOG: List[Dict[str, str]] = [
    {"code": "USDIDR", "label": "USD/IDR", "category": "fx",
     "unit": "IDR per USD", "currency": "IDR", "symbol": "IDR=X"},
    {"code": "SP500", "label": "S&P 500", "category": "equity_index",
     "unit": "index points", "currency": "USD", "symbol": "^GSPC"},
    {"code": "DJIA", "label": "Dow Jones", "category": "equity_index",
     "unit": "index points", "currency": "USD", "symbol": "^DJI"},
    {"code": "NASDAQ", "label": "Nasdaq", "category": "equity_index",
     "unit": "index points", "currency": "USD", "symbol": "^IXIC"},
    {"code": "HSI", "label": "Hang Seng", "category": "equity_index",
     "unit": "index points", "currency": "HKD", "symbol": "^HSI"},
    {"code": "GOLD", "label": "Gold", "category": "commodity",
     "unit": "USD per troy ounce", "currency": "USD", "symbol": "GC=F"},
    {"code": "COAL", "label": "Coal", "category": "commodity",
     "unit": "unavailable", "currency": "USD", "symbol": ""},
    {"code": "NICKEL", "label": "Nickel", "category": "commodity",
     "unit": "unavailable", "currency": "USD", "symbol": ""},
    {"code": "CPO", "label": "CPO", "category": "commodity",
     "unit": "unavailable", "currency": "MYR", "symbol": ""},
]
CATALOG_BY_CODE = {item["code"]: item for item in MACRO_CATALOG}

MACRO_OBSERVATION_COLUMNS: List[str] = [
    "indicator",
    "date",
    "value",
    "unit",
    "currency",
    "published_at",
    "available_at",
    "source_id",
    "source_document",
    "revision",
    "ingested_at",
]

MACRO_INPUT_COLUMNS: List[str] = [
    "indicator", "date", "value", "unit", "currency",
    "published_at", "available_at", "source_id", "source_document", "revision",
]

MACRO_KEYS = ["indicator", "date", "revision"]


class MacroSchemaError(ValueError):
    """Raised for an invalid macro import."""


def _require(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise MacroSchemaError(f"macro import missing columns: {missing}")


def validate_macro_observations(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, MACRO_INPUT_COLUMNS)
    if df.empty:
        raise MacroSchemaError("macro import is empty")
    out = df.copy()
    out["indicator"] = out["indicator"].fillna("").astype(str).str.upper().str.strip()
    unknown = sorted(set(out["indicator"]) - set(CATALOG_BY_CODE))
    if unknown:
        raise MacroSchemaError(f"unknown macro indicators: {unknown}")
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    if out["date"].isna().any():
        raise MacroSchemaError("date must be valid")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    if out["value"].isna().any():
        raise MacroSchemaError("value cannot be missing")
    out["unit"] = out["unit"].fillna("").astype(str)
    out["currency"] = out["currency"].fillna("").astype(str).str.upper()
    if (out["currency"].str.len() != 3).any():
        raise MacroSchemaError("currency must be a 3-letter code")
    for column in ("published_at", "available_at"):
        out[column] = pd.to_datetime(out[column], errors="coerce", utc=True)
    if out["published_at"].isna().any() or out["available_at"].isna().any():
        raise MacroSchemaError("published_at/available_at must be valid")
    if (out["available_at"] < out["published_at"]).any():
        raise MacroSchemaError("available_at cannot precede published_at")
    out["revision"] = pd.to_numeric(
        out["revision"], errors="coerce"
    ).fillna(0).astype("int64")
    if out["revision"].lt(0).any():
        raise MacroSchemaError("revision must be >= 0")
    out["source_id"] = out["source_id"].fillna("").astype(str)
    out["source_document"] = out["source_document"].fillna("").astype(str)
    out["ingested_at"] = datetime.now(timezone.utc).isoformat()
    duplicates = out.duplicated(subset=MACRO_KEYS, keep=False)
    if duplicates.any():
        raise MacroSchemaError("duplicate indicator/date/revision")
    return out[MACRO_OBSERVATION_COLUMNS].reset_index(drop=True)
