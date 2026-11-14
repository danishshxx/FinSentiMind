"""Point-in-time macro observation repository."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from ml.macro.schemas import MACRO_OBSERVATION_COLUMNS, MacroSchemaError


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _PROJECT_ROOT / "ml" / "data" / "macro"
DEFAULT_PATH = DEFAULT_DATA_DIR / "macro_observations.parquet"

MARKET_TZ = "Asia/Jakarta"


def _empty() -> pd.DataFrame:
    return pd.DataFrame({
        column: pd.Series(dtype="object") for column in MACRO_OBSERVATION_COLUMNS
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


class MacroRepository:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    @property
    def available(self) -> bool:
        return self.path.exists()

    def status(self) -> dict:
        if not self.available:
            return {
                "status": "UNAVAILABLE",
                "reason": (
                    "No verified macro history installed. Automated providers "
                    "are disabled unless explicitly configured and licensed."
                ),
                "path": str(self.path),
            }
        return {"status": "AVAILABLE", "reason": None, "path": str(self.path)}

    def observations(
        self,
        indicator: Optional[str] = None,
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
        if indicator:
            df = df[df["indicator"] == indicator.upper()]
        if not include_history:
            df = df.sort_values(["available_at", "revision"])
            df = df.drop_duplicates(subset=["indicator", "date"], keep="last")
        return df.sort_values(["indicator", "date"]).reset_index(drop=True)

    def coverage(self) -> dict:
        df = self.observations(include_history=True)
        if df.empty:
            return {"indicators": [], "first_date": None, "last_date": None, "rows": 0}
        return {
            "indicators": sorted(df["indicator"].unique().tolist()),
            "first_date": str(df["date"].min()),
            "last_date": str(df["date"].max()),
            "rows": int(len(df)),
        }


def load_macro_import(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Macro import not found: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in (".json", ".jsonl"):
        return pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    raise MacroSchemaError("Use CSV or JSON for macro imports.")
