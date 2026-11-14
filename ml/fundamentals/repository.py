"""Point-in-time repository for auditable fundamental data."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from app.core.logging import get_logger
from ml.fundamentals.ingestion import (
    DEFAULT_ACTIONS,
    DEFAULT_DATA_DIR,
    DEFAULT_DIVIDENDS,
    DEFAULT_STATEMENTS,
)
from ml.fundamentals.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DIVIDEND_COLUMNS,
    FINANCIAL_STATEMENT_COLUMNS,
    LOGICAL_STATEMENT_KEYS,
)


logger = get_logger(__name__)

MARKET_TZ = "Asia/Jakarta"


def _empty(columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def _as_of_bound(as_of: Optional[Union[str, date, datetime]]) -> Optional[pd.Timestamp]:
    if as_of is None:
        return None
    stamp = pd.Timestamp(as_of)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(MARKET_TZ)
    end = stamp + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return end.tz_convert("UTC")


class FundamentalRepository:
    """Read-only access to manually ingested fundamental datasets."""

    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        statements_path: Optional[Path] = None,
        actions_path: Optional[Path] = None,
        dividends_path: Optional[Path] = None,
    ) -> None:
        self.data_dir = data_dir
        self.statements_path = statements_path or data_dir / DEFAULT_STATEMENTS.name
        self.actions_path = actions_path or data_dir / DEFAULT_ACTIONS.name
        self.dividends_path = dividends_path or data_dir / DEFAULT_DIVIDENDS.name

    def _read(self, path: Path, columns: List[str]) -> pd.DataFrame:
        if not path.exists():
            return _empty(columns)
        df = pd.read_parquet(path)
        for column in ("published_at", "available_at"):
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
        return df

    def statements(
        self,
        ticker: Optional[str] = None,
        as_of: Optional[Union[str, date, datetime]] = None,
        latest_revision_only: bool = True,
        include_history: bool = False,
    ) -> pd.DataFrame:
        df = self._read(self.statements_path, FINANCIAL_STATEMENT_COLUMNS)
        if df.empty:
            return df
        bound = _as_of_bound(as_of)
        if bound is not None:
            df = df[df["available_at"].notna() & (df["available_at"] <= bound)]
        if ticker:
            df = df[df["ticker"].str.upper() == ticker.upper()]
        if include_history:
            return df.sort_values(
                ["ticker", "period_end", "statement_type", "line_item", "revision"]
            ).reset_index(drop=True)
        if latest_revision_only:
            df = df.sort_values(["available_at", "revision"])
            df = df.drop_duplicates(subset=LOGICAL_STATEMENT_KEYS, keep="last")
        return df.sort_values(
            ["ticker", "period_end", "statement_type", "line_item", "revision"]
        ).reset_index(drop=True)

    def periods(
        self,
        ticker: str,
        as_of: Optional[Union[str, date, datetime]] = None,
    ) -> pd.DataFrame:
        df = self.statements(ticker=ticker, as_of=as_of)
        if df.empty:
            return df
        keys = [
            "ticker", "period_type", "fiscal_year", "fiscal_quarter",
            "period_start", "period_end", "consolidation", "currency",
        ]
        return df[keys].drop_duplicates().sort_values(
            ["period_end", "period_type"]
        ).reset_index(drop=True)

    def corporate_actions(
        self,
        ticker: Optional[str] = None,
        as_of: Optional[Union[str, date, datetime]] = None,
    ) -> pd.DataFrame:
        df = self._read(self.actions_path, CORPORATE_ACTION_COLUMNS)
        if df.empty:
            return df
        bound = _as_of_bound(as_of)
        if bound is not None:
            df = df[df["available_at"].notna() & (df["available_at"] <= bound)]
        if ticker:
            df = df[df["ticker"].str.upper() == ticker.upper()]
        return df.sort_values(["ticker", "ex_date", "revision"]).reset_index(drop=True)

    def dividends(
        self,
        ticker: Optional[str] = None,
        as_of: Optional[Union[str, date, datetime]] = None,
    ) -> pd.DataFrame:
        df = self._read(self.dividends_path, DIVIDEND_COLUMNS)
        if df.empty:
            return df
        bound = _as_of_bound(as_of)
        if bound is not None:
            df = df[df["available_at"].notna() & (df["available_at"] <= bound)]
        if ticker:
            df = df[df["ticker"].str.upper() == ticker.upper()]
        return df.sort_values(["ticker", "ex_date", "revision"]).reset_index(drop=True)

    def coverage(self) -> pd.DataFrame:
        df = self.statements()
        if df.empty:
            return pd.DataFrame(
                columns=["ticker", "periods", "first_period", "last_period"]
            )
        rows = []
        for ticker, group in df.groupby("ticker"):
            rows.append({
                "ticker": ticker,
                "periods": int(
                    group[["period_end", "period_type", "fiscal_quarter"]]
                    .drop_duplicates()
                    .shape[0]
                ),
                "first_period": group["period_end"].min(),
                "last_period": group["period_end"].max(),
            })
        return pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
