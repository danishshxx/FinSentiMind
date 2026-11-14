"""Portfolio ledger schema and validation.

Cost basis policy: FIFO lot accounting. Fees on purchases are capitalized;
fees and taxes on sales reduce proceeds. Short selling is rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

import pandas as pd


PORTFOLIO_TYPES = ("personal", "model")
SIDES = (
    "BUY", "SELL", "DIVIDEND", "SPLIT", "REVERSE_SPLIT", "BONUS",
    "RIGHTS", "DEPOSIT", "WITHDRAWAL", "FEE", "TAX",
)
SECURITY_SIDES = {"BUY", "SELL", "DIVIDEND", "SPLIT", "REVERSE_SPLIT", "BONUS", "RIGHTS"}

TRANSACTION_COLUMNS: List[str] = [
    "transaction_id",
    "portfolio_id",
    "portfolio_type",
    "trade_date",
    "side",
    "ticker",
    "quantity",
    "price",
    "fees",
    "taxes",
    "cash_amount",
    "ratio_from",
    "ratio_to",
    "currency",
    "notes",
    "created_at",
]

PORTFOLIO_COLUMNS: List[str] = [
    "portfolio_id",
    "name",
    "portfolio_type",
    "currency",
    "benchmark",
    "description",
    "created_at",
]


class PortfolioSchemaError(ValueError):
    """Raised for an invalid portfolio ledger."""


def _require(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise PortfolioSchemaError(f"portfolio ledger missing columns: {missing}")


def validate_transactions(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, TRANSACTION_COLUMNS)
    if df.empty:
        raise PortfolioSchemaError("portfolio ledger is empty")
    out = df.copy()
    out["transaction_id"] = out["transaction_id"].fillna("").astype(str).str.strip()
    if (out["transaction_id"] == "").any():
        raise PortfolioSchemaError("transaction_id cannot be empty")
    if out["transaction_id"].duplicated().any():
        raise PortfolioSchemaError("duplicate transaction_id")
    out["portfolio_id"] = out["portfolio_id"].fillna("").astype(str).str.strip()
    if (out["portfolio_id"] == "").any():
        raise PortfolioSchemaError("portfolio_id cannot be empty")
    out["portfolio_type"] = out["portfolio_type"].fillna("").astype(str).str.lower()
    if not out["portfolio_type"].isin(PORTFOLIO_TYPES).all():
        raise PortfolioSchemaError("portfolio_type must be personal or model")
    out["side"] = out["side"].fillna("").astype(str).str.upper().str.strip()
    if not out["side"].isin(SIDES).all():
        raise PortfolioSchemaError("invalid transaction side")
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.date
    if out["trade_date"].isna().any():
        raise PortfolioSchemaError("trade_date must be valid")
    out["ticker"] = out["ticker"].where(out["ticker"].notna(), None)
    out.loc[out["ticker"].notna(), "ticker"] = (
        out.loc[out["ticker"].notna(), "ticker"].astype(str).str.upper()
    )
    needs_ticker = out["side"].isin(SECURITY_SIDES)
    if out.loc[needs_ticker, "ticker"].isna().any():
        raise PortfolioSchemaError("security transactions require ticker")

    for column in ("quantity", "price", "fees", "taxes", "cash_amount",
                   "ratio_from", "ratio_to"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    for column in ("fees", "taxes"):
        out[column] = out[column].fillna(0.0)
        if (out[column] < 0).any():
            raise PortfolioSchemaError(f"{column} cannot be negative")

    if (out["side"].isin(["BUY", "SELL", "RIGHTS"]) & out["quantity"].isna()).any():
        raise PortfolioSchemaError("BUY/SELL/RIGHTS require quantity")
    if (out["side"].isin(["BUY", "SELL", "RIGHTS"]) & out["price"].isna()).any():
        raise PortfolioSchemaError("BUY/SELL/RIGHTS require price")
    if (out["quantity"].fillna(0) < 0).any() or (out["price"].fillna(0) < 0).any():
        raise PortfolioSchemaError("quantity and price cannot be negative")
    split = out["side"].isin(["SPLIT", "REVERSE_SPLIT", "BONUS"])
    if (split & (out["ratio_from"].isna() | out["ratio_to"].isna())).any():
        raise PortfolioSchemaError("split/bonus require ratio_from and ratio_to")
    if (split & ((out["ratio_from"] <= 0) | (out["ratio_to"] <= 0))).any():
        raise PortfolioSchemaError("split/bonus ratios must be positive")
    cash_sides = out["side"].isin(["DIVIDEND", "DEPOSIT", "WITHDRAWAL", "FEE", "TAX"])
    if (cash_sides & out["cash_amount"].isna()).any():
        raise PortfolioSchemaError("cash transactions require cash_amount")
    if (cash_sides & (out["cash_amount"] < 0)).any():
        raise PortfolioSchemaError("cash_amount cannot be negative; use side semantics")
    out["currency"] = out["currency"].fillna("IDR").astype(str).str.upper()
    out["notes"] = out["notes"].fillna("").astype(str)
    out["created_at"] = out["created_at"].where(
        out["created_at"].notna(), datetime.now(timezone.utc).isoformat()
    )
    return out[TRANSACTION_COLUMNS].reset_index(drop=True)


def validate_portfolios(df: pd.DataFrame) -> pd.DataFrame:
    _require(df, PORTFOLIO_COLUMNS)
    out = df.copy()
    out["portfolio_id"] = out["portfolio_id"].fillna("").astype(str).str.strip()
    out["name"] = out["name"].fillna("").astype(str).str.strip()
    if (out["portfolio_id"] == "").any() or (out["name"] == "").any():
        raise PortfolioSchemaError("portfolio_id and name cannot be empty")
    if out["portfolio_id"].duplicated().any():
        raise PortfolioSchemaError("duplicate portfolio_id")
    out["portfolio_type"] = out["portfolio_type"].fillna("").astype(str).str.lower()
    if not out["portfolio_type"].isin(PORTFOLIO_TYPES).all():
        raise PortfolioSchemaError("portfolio_type must be personal or model")
    out["currency"] = out["currency"].fillna("IDR").astype(str).str.upper()
    out["benchmark"] = out["benchmark"].fillna("").astype(str)
    out["description"] = out["description"].fillna("").astype(str)
    out["created_at"] = out["created_at"].where(
        out["created_at"].notna(), datetime.now(timezone.utc).isoformat()
    )
    return out[PORTFOLIO_COLUMNS].reset_index(drop=True)
