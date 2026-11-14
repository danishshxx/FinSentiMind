"""Point-in-time local repository for capital-flow observations."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from app.core.logging import get_logger
from ml.capital_flow.schemas import FLOW_OBSERVATION_COLUMNS


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _PROJECT_ROOT / "ml" / "data" / "capital_flow"
DEFAULT_FLOW_PATH = DEFAULT_DATA_DIR / "flow_observations.parquet"

MARKET_TZ = "Asia/Jakarta"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({
        column: pd.Series(dtype="object") for column in FLOW_OBSERVATION_COLUMNS
    })


def _bound(as_of: Optional[Union[str, date, datetime]]) -> Optional[pd.Timestamp]:
    if as_of is None:
        return None
    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(MARKET_TZ)
    return (
        stamp + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    ).tz_convert("UTC")


class CapitalFlowRepository:
    def __init__(self, path: Path = DEFAULT_FLOW_PATH) -> None:
        self.path = path

    @property
    def available(self) -> bool:
        return self.path.exists()

    def status(self) -> dict:
        if not self.available:
            return {
                "status": "UNAVAILABLE",
                "reason": (
                    "No verified capital-flow dataset is installed. "
                    "Automated provider access is not licensed in this build."
                ),
                "path": str(self.path),
                "provenance": "MANUAL_IMPORT_ONLY",
            }
        return {
            "status": "AVAILABLE",
            "reason": None,
            "path": str(self.path),
            "provenance": "MANUAL_IMPORT_ONLY",
        }

    def observations(
        self,
        scope: Optional[str] = None,
        ticker: Optional[str] = None,
        as_of: Optional[Union[str, date, datetime]] = None,
        include_history: bool = False,
    ) -> pd.DataFrame:
        if not self.available:
            return _empty()
        df = pd.read_parquet(self.path)
        if df.empty:
            return df
        for column in ("published_at", "available_at"):
            df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
        bound = _bound(as_of)
        if bound is not None:
            df = df[df["available_at"].notna() & (df["available_at"] <= bound)]
        if scope:
            df = df[df["scope"] == scope]
        if ticker:
            df = df[df["ticker"].fillna("").str.upper() == ticker.upper()]
        if not include_history:
            keys = ["date", "scope", "ticker", "investor_class", "broker_code"]
            df = df.sort_values(["available_at", "revision"])
            df = df.drop_duplicates(subset=keys, keep="last")
        return df.sort_values(
            ["date", "scope", "ticker", "investor_class", "broker_code"]
        ).reset_index(drop=True)

    def coverage(self) -> dict:
        df = self.observations()
        if df.empty:
            return {
                "rows": 0, "dates": 0, "tickers": 0,
                "sources": [], "first_date": None, "last_date": None,
            }
        return {
            "rows": int(len(df)),
            "dates": int(df["date"].nunique()),
            "tickers": int(df["ticker"].dropna().nunique()),
            "sources": sorted(df["source_id"].dropna().astype(str).unique()),
            "first_date": str(df["date"].min()),
            "last_date": str(df["date"].max()),
        }
