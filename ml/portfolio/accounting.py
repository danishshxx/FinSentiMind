"""FIFO portfolio ledger, holdings, valuation, and transaction accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.core.logging import get_logger
from ml.portfolio.schemas import PortfolioSchemaError


logger = get_logger(__name__)


@dataclass
class Lot:
    quantity: float
    cost_per_share: float
    acquired: Optional[date] = None


@dataclass
class Holding:
    ticker: str
    quantity: float = 0.0
    cost_basis: float = 0.0
    lots: List[Lot] = field(default_factory=list)
    realized_pnl: float = 0.0
    dividends: float = 0.0
    fees: float = 0.0
    taxes: float = 0.0
    market_price: Optional[float] = None
    market_value: Optional[float] = None

    @property
    def average_cost(self) -> Optional[float]:
        return self.cost_basis / self.quantity if self.quantity else None

    @property
    def unrealized_pnl(self) -> Optional[float]:
        if self.market_value is None:
            return None
        return self.market_value - self.cost_basis


@dataclass
class PortfolioSnapshot:
    cash: float
    holdings: Dict[str, Holding]
    realized_pnl: float
    dividend_income: float
    fees: float
    taxes: float
    market_value: Optional[float]
    total_value: Optional[float]
    missing_prices: List[str]
    external_flows: List[Tuple[date, float]]

    @property
    def unrealized_pnl(self) -> Optional[float]:
        values = [holding.unrealized_pnl for holding in self.holdings.values()]
        if any(value is None for value in values):
            return None
        return sum(values)


class PortfolioLedger:
    """Mutable FIFO ledger. Replay from a validated transaction frame."""

    def __init__(self) -> None:
        self.cash = 0.0
        self.holdings: Dict[str, Holding] = {}
        self.realized_pnl = 0.0
        self.dividend_income = 0.0
        self.fees = 0.0
        self.taxes = 0.0
        self.external_flows: List[Tuple[date, float]] = []

    def _holding(self, ticker: str) -> Holding:
        return self.holdings.setdefault(ticker, Holding(ticker=ticker))

    def apply(self, transaction: pd.Series) -> None:
        side = str(transaction["side"])
        ticker = transaction.get("ticker")
        quantity = float(transaction.get("quantity") or 0)
        price = float(transaction.get("price") or 0)
        fees = float(transaction.get("fees") or 0)
        taxes = float(transaction.get("taxes") or 0)
        cash_amount = float(transaction.get("cash_amount") or 0)
        trade_date = transaction["trade_date"]

        if side == "DEPOSIT":
            self.cash += cash_amount
            self.external_flows.append((trade_date, -cash_amount))
            return
        if side == "WITHDRAWAL":
            self.cash -= cash_amount
            self.external_flows.append((trade_date, cash_amount))
            return
        if side == "FEE":
            self.cash -= cash_amount
            self.fees += cash_amount
            return
        if side == "TAX":
            self.cash -= cash_amount
            self.taxes += cash_amount
            return

        if not ticker:
            raise PortfolioSchemaError(f"{side} requires a ticker")
        holding = self._holding(str(ticker))

        if side in ("BUY", "RIGHTS"):
            total_cost = quantity * price + fees + taxes
            self.cash -= total_cost
            holding.quantity += quantity
            holding.cost_basis += total_cost
            holding.fees += fees
            holding.taxes += taxes
            holding.lots.append(Lot(
                quantity=quantity,
                cost_per_share=total_cost / quantity if quantity else 0.0,
                acquired=trade_date,
            ))
            return

        if side == "SELL":
            if quantity > holding.quantity + 1e-9:
                raise PortfolioSchemaError(
                    f"Cannot sell {quantity} {ticker}; only {holding.quantity} held (no short selling)."
                )
            proceeds = quantity * price - fees - taxes
            cost_removed = 0.0
            remaining = quantity
            while remaining > 1e-9 and holding.lots:
                lot = holding.lots[0]
                take = min(lot.quantity, remaining)
                cost_removed += take * lot.cost_per_share
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 1e-9:
                    holding.lots.pop(0)
            holding.quantity -= quantity
            holding.cost_basis -= cost_removed
            if holding.quantity <= 1e-9:
                holding.quantity = 0.0
                holding.cost_basis = 0.0
                holding.lots = []
            realized = proceeds - cost_removed
            holding.realized_pnl += realized
            self.realized_pnl += realized
            holding.fees += fees
            holding.taxes += taxes
            self.cash += proceeds
            return

        if side == "DIVIDEND":
            net_cash = cash_amount - taxes
            self.cash += net_cash
            holding.dividends += cash_amount
            holding.taxes += taxes
            self.dividend_income += cash_amount
            self.taxes += taxes
            return

        if side in ("SPLIT", "REVERSE_SPLIT"):
            factor = float(transaction["ratio_to"]) / float(transaction["ratio_from"])
            if factor <= 0:
                raise PortfolioSchemaError("split factor must be positive")
            holding.quantity *= factor
            for lot in holding.lots:
                lot.quantity *= factor
                lot.cost_per_share /= factor
            return

        if side == "BONUS":
            factor = float(transaction["ratio_to"]) / float(transaction["ratio_from"])
            if factor <= 0:
                raise PortfolioSchemaError("bonus factor must be positive")
            added = holding.quantity * factor
            if holding.quantity > 0:
                new_quantity = holding.quantity + added
                for lot in holding.lots:
                    lot.quantity *= new_quantity / holding.quantity
                    lot.cost_per_share = (
                        lot.cost_per_share * holding.quantity / new_quantity
                    )
                holding.quantity = new_quantity
            return

        raise PortfolioSchemaError(f"Unsupported side: {side}")

    def snapshot(self, prices: Optional[Dict[str, float]] = None) -> PortfolioSnapshot:
        prices = prices or {}
        missing: List[str] = []
        market_value = 0.0
        for ticker, holding in self.holdings.items():
            if holding.quantity <= 1e-9:
                holding.market_price = None
                holding.market_value = 0.0
                continue
            price = prices.get(ticker)
            holding.market_price = price
            if price is None:
                holding.market_value = None
                missing.append(ticker)
            else:
                holding.market_value = holding.quantity * price
                market_value += holding.market_value
        total = None if missing else self.cash + market_value
        return PortfolioSnapshot(
            cash=self.cash,
            holdings=self.holdings,
            realized_pnl=self.realized_pnl,
            dividend_income=self.dividend_income,
            fees=self.fees,
            taxes=self.taxes,
            market_value=None if missing else market_value,
            total_value=total,
            missing_prices=missing,
            external_flows=list(self.external_flows),
        )

    def to_dict(self, prices: Optional[Dict[str, float]] = None) -> Dict[str, object]:
        snapshot = self.snapshot(prices)
        return {
            "cash": snapshot.cash,
            "realized_pnl": snapshot.realized_pnl,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "dividend_income": snapshot.dividend_income,
            "fees": snapshot.fees,
            "taxes": snapshot.taxes,
            "market_value": snapshot.market_value,
            "total_value": snapshot.total_value,
            "missing_prices": snapshot.missing_prices,
            "holdings": [
                {
                    "ticker": holding.ticker,
                    "quantity": holding.quantity,
                    "average_cost": holding.average_cost,
                    "cost_basis": holding.cost_basis,
                    "market_price": holding.market_price,
                    "market_value": holding.market_value,
                    "unrealized_pnl": holding.unrealized_pnl,
                    "realized_pnl": holding.realized_pnl,
                    "dividends": holding.dividends,
                    "lots": [
                        {
                            "quantity": lot.quantity,
                            "cost_per_share": lot.cost_per_share,
                            "acquired": str(lot.acquired) if lot.acquired else None,
                        }
                        for lot in holding.lots
                    ],
                }
                for holding in snapshot.holdings.values()
                if holding.quantity > 1e-9 or holding.realized_pnl
            ],
        }


def replay(
    transactions: pd.DataFrame,
    until: Optional[date] = None,
    prices: Optional[Dict[str, float]] = None,
) -> PortfolioLedger:
    ledger = PortfolioLedger()
    ordered = transactions.sort_values(["trade_date", "created_at", "transaction_id"])
    for _, transaction in ordered.iterrows():
        if until is not None and transaction["trade_date"] > until:
            continue
        ledger.apply(transaction)
    ledger.snapshot(prices)
    return ledger


def _price_history(prices: pd.DataFrame) -> Tuple[List[date], Dict[str, pd.DataFrame]]:
    if prices.empty or {"ticker", "trading_date", "close"}.difference(prices.columns):
        return [], {}
    frame = prices.copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"]).dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    dates = sorted(frame["trading_date"].dropna().unique())
    by_ticker = {
        ticker: group.sort_values("trading_date")
        for ticker, group in frame.groupby("ticker")
    }
    return dates, by_ticker


def _last_close(group: Optional[pd.DataFrame], day: date) -> Optional[float]:
    if group is None:
        return None
    available = group[group["trading_date"] <= day]
    if available.empty:
        return None
    return float(available.iloc[-1]["close"])


def build_value_history(
    transactions: pd.DataFrame,
    prices: pd.DataFrame,
    dates: Optional[List[date]] = None,
) -> pd.DataFrame:
    """Daily portfolio value using the last close available on or before each day."""
    price_dates, by_ticker = _price_history(prices)
    if dates is None:
        start = min(transactions["trade_date"])
        end = max(transactions["trade_date"])
        dates = [day for day in price_dates if start <= day <= end]
    rows = []
    for day in sorted(dates):
        ledger = replay(transactions, until=day)
        close_prices = {
            ticker: _last_close(group, day) for ticker, group in by_ticker.items()
        }
        snapshot = ledger.snapshot(close_prices)
        flow = -sum(
            amount for flow_day, amount in snapshot.external_flows
            if flow_day == day
        )
        rows.append({
            "date": day,
            "cash": snapshot.cash,
            "market_value": snapshot.market_value,
            "total_value": snapshot.total_value,
            "external_flow": flow,
            "missing_prices": ",".join(snapshot.missing_prices),
        })
    return pd.DataFrame(rows)
