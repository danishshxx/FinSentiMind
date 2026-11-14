"""Local portfolio repository and report orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from app.core.logging import get_logger
from ml.fundamentals.ingestion import write_parquet_atomic
from ml.portfolio.accounting import build_value_history, replay
from ml.portfolio.performance import performance_report
from ml.portfolio.schemas import (
    PORTFOLIO_COLUMNS,
    TRANSACTION_COLUMNS,
    PortfolioSchemaError,
    validate_portfolios,
    validate_transactions,
)


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _PROJECT_ROOT / "ml" / "data" / "portfolio"
DEFAULT_PORTFOLIOS = DEFAULT_DATA_DIR / "portfolios.parquet"
DEFAULT_TRANSACTIONS = DEFAULT_DATA_DIR / "transactions.parquet"


def _load(path: Path, columns) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})
    return pd.read_parquet(path)


def ingest_portfolios(input_path: Path, output_path: Path = DEFAULT_PORTFOLIOS) -> pd.DataFrame:
    raw = pd.read_csv(input_path) if input_path.suffix.lower() == ".csv" else pd.read_json(input_path)
    incoming = validate_portfolios(raw)
    existing = _load(output_path, PORTFOLIO_COLUMNS)
    combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming
    combined = combined.drop_duplicates(subset=["portfolio_id"], keep="last")
    write_parquet_atomic(combined, output_path)
    return combined


def ingest_transactions(
    input_path: Path, output_path: Path = DEFAULT_TRANSACTIONS, append: bool = True
) -> pd.DataFrame:
    raw = pd.read_csv(input_path) if input_path.suffix.lower() == ".csv" else pd.read_json(input_path)
    incoming = validate_transactions(raw)
    existing = _load(output_path, TRANSACTION_COLUMNS) if append else pd.DataFrame(
        columns=TRANSACTION_COLUMNS
    )
    combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming
    if combined["transaction_id"].duplicated().any():
        raise PortfolioSchemaError("duplicate transaction_id in repository")
    write_parquet_atomic(combined, output_path)
    return combined


class PortfolioRepository:
    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        portfolios_path: Optional[Path] = None,
        transactions_path: Optional[Path] = None,
    ) -> None:
        self.data_dir = data_dir
        self.portfolios_path = portfolios_path or data_dir / "portfolios.parquet"
        self.transactions_path = transactions_path or data_dir / "transactions.parquet"

    def portfolios(self) -> pd.DataFrame:
        return _load(self.portfolios_path, PORTFOLIO_COLUMNS)

    def transactions(self, portfolio_id: Optional[str] = None) -> pd.DataFrame:
        df = _load(self.transactions_path, TRANSACTION_COLUMNS)
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        if portfolio_id:
            df = df[df["portfolio_id"] == portfolio_id]
        return df.sort_values(["trade_date", "created_at", "transaction_id"]).reset_index(drop=True)

    def report(
        self,
        portfolio_id: str,
        prices: Optional[pd.DataFrame] = None,
        prices_as_of: Optional[Dict[str, float]] = None,
    ) -> Dict[str, object]:
        transactions = self.transactions(portfolio_id)
        metadata = self.portfolios()
        meta = (
            metadata[metadata["portfolio_id"] == portfolio_id].to_dict(orient="records")
            if not metadata.empty else []
        )
        if transactions.empty:
            return {
                "status": "UNAVAILABLE",
                "portfolio_id": portfolio_id,
                "reason": "No transactions for this portfolio.",
                "metadata": meta[0] if meta else None,
            }
        ledger = replay(transactions, prices=prices_as_of)
        snapshot = ledger.to_dict(prices=prices_as_of)
        if prices is not None and not prices.empty:
            history = build_value_history(transactions, prices)
            ending = history.dropna(subset=["total_value"]).tail(1)
            ending_value = float(ending.iloc[0]["total_value"]) if not ending.empty else None
            ending_date = ending.iloc[0]["date"] if not ending.empty else None
            performance = performance_report(history, transactions, ending_value, ending_date)
        else:
            history = pd.DataFrame()
            performance = {
                "twr": {"status": "UNAVAILABLE", "reason": "No price history."},
                "mwr": {"status": "UNAVAILABLE", "reason": "No price history."},
            }
        return {
            "status": "OK" if snapshot["total_value"] is not None else "PARTIAL",
            "portfolio_id": portfolio_id,
            "metadata": meta[0] if meta else None,
            "snapshot": snapshot,
            "performance": performance,
            "valuation_history_rows": int(len(history)),
            "not_for_trading": True,
            "disclaimer": (
                "Personal portfolio tracking is read-only. No broker connection, "
                "order routing, or custody is implemented."
            ),
        }
