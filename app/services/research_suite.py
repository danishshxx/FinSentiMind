"""Read-only orchestration for the integrated research workstation."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.services.market_data import MarketDataRepository
from ml.capital_flow.analysis import build_market_report, build_stock_report
from ml.capital_flow.providers import provider_matrix as flow_provider_matrix
from ml.capital_flow.repository import CapitalFlowRepository
from ml.fundamentals.analysis import build_ticker_analysis
from ml.fundamentals.repository import FundamentalRepository
from ml.macro.analysis import cross_asset_summary
from ml.macro.providers import provider_matrix as macro_provider_matrix
from ml.macro.repository import MacroRepository
from ml.portfolio.repository import PortfolioRepository
from ml.research.ml_pipeline import load_latest_research_report
from ml.workspace.notes import ResearchNoteRepository
from ml.risk.engine import (
    efficient_frontier,
    optimize_weights,
    returns_from_prices,
    risk_metrics,
    risk_parity_weights,
    simulate_portfolio,
)


class ResearchSuite:
    def __init__(self, market: MarketDataRepository) -> None:
        self.market = market
        self.fundamentals = FundamentalRepository()
        self.flows = CapitalFlowRepository()
        self.macro = MacroRepository()
        self.portfolio = PortfolioRepository()
        self.notes = ResearchNoteRepository()

    def capabilities(self) -> Dict[str, Any]:
        return {
            "read_only": True,
            "dataset": {
                "status": "AVAILABLE" if self.market._base is not None and not self.market._base.empty else "UNAVAILABLE",
                "data_as_of": self.market.latest_date(),
            },
            "fundamentals": {
                "status": "AVAILABLE" if self.fundamentals.statements_path.exists() else "UNAVAILABLE",
                "reason": None if self.fundamentals.statements_path.exists() else "No verified manual fundamental import.",
            },
            "capital_flow": self.flows.status(),
            "macro": self.macro.status(),
            "portfolio": {
                "status": "AVAILABLE" if self.portfolio.transactions_path.exists() else "UNAVAILABLE",
                "reason": None if self.portfolio.transactions_path.exists() else "No portfolio ledger installed.",
            },
            "research_status": "NOT_VALIDATED",
            "live_trading": "NOT_IMPLEMENTED",
            "public_launch": "NOT_APPROVED",
            "provider_matrix": {
                "capital_flow": flow_provider_matrix(),
                "macro": macro_provider_matrix(),
            },
        }

    def fundamental_analysis(self, ticker: str, dcf: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        symbol = ticker.upper()
        statements = self.fundamentals.statements(ticker=symbol)
        if statements.empty:
            return {
                "status": "UNAVAILABLE",
                "ticker": symbol,
                "reason": "No verified fundamental statements imported for this ticker.",
                "source_policy": "Manual verified import only; no provider is fabricated.",
                "not_for_trading": True,
            }
        latest_price = None
        base = self.market._base
        if not base.empty:
            history = base[base["ticker"] == symbol].sort_values("trading_date")
            if not history.empty:
                latest_price = float(history.iloc[-1]["close"])
        shares = None
        values = statements[statements["period_type"] == "annual"].sort_values("period_end")
        if not values.empty:
            latest = values[values["period_end"] == values["period_end"].max()]
            row = latest[latest["line_item"].str.contains("share", case=False, na=False)]
            # Shares are normally supplied explicitly; never infer from an arbitrary line item.
            shares = None
        return build_ticker_analysis(
            self.fundamentals,
            symbol,
            market_price=latest_price,
            shares_outstanding=shares,
            tax_rate=(dcf or {}).get("tax_rate"),
            dcf_inputs=dcf,
            maintenance_capex=(dcf or {}).get("maintenance_capex"),
            working_capital_change=(dcf or {}).get("working_capital_change"),
            as_of=(dcf or {}).get("as_of"),
        )

    def capital_flow_market(self) -> Dict[str, Any]:
        return build_market_report(self.flows)

    def capital_flow_stock(self, ticker: str) -> Dict[str, Any]:
        prices = None
        base = self.market._base
        if not base.empty:
            history = base[base["ticker"] == ticker.upper()].sort_values("trading_date")
            if not history.empty:
                prices = pd.DataFrame({
                    "date": history["trading_date"],
                    "close": history["close"],
                })
        return build_stock_report(self.flows, ticker, prices=prices)

    def macro_summary(self) -> Dict[str, Any]:
        return cross_asset_summary(self.macro)

    def portfolio_list(self) -> List[Dict[str, Any]]:
        frame = self.portfolio.portfolios()
        return frame.to_dict(orient="records") if not frame.empty else []

    def portfolio_report(self, portfolio_id: str) -> Dict[str, Any]:
        prices = self.market._base
        if not prices.empty:
            prices = prices[["ticker", "trading_date", "close"]]
        return self.portfolio.report(portfolio_id, prices=prices)

    def risk_lab(self, tickers: Optional[List[str]] = None) -> Dict[str, Any]:
        base = self.market._base
        if base.empty:
            return {"status": "UNAVAILABLE", "reason": "No price dataset."}
        prices = base[["ticker", "trading_date", "close"]].copy()
        if tickers:
            prices = prices[prices["ticker"].isin([ticker.upper() for ticker in tickers])]
        returns = returns_from_prices(prices)
        if returns.empty or returns.shape[1] < 2:
            return {
                "status": "UNAVAILABLE",
                "reason": "Insufficient overlapping return history for risk lab.",
            }
        returns = returns.tail(252)
        try:
            minimum = optimize_weights(returns, objective="min_variance")
            maximum = optimize_weights(returns, objective="max_sharpe")
            parity = risk_parity_weights(returns)
            frontier = efficient_frontier(returns, points=6)
        except Exception as exc:
            return {"status": "UNAVAILABLE", "reason": f"Optimizer error: {exc}"}
        equal_weights = [1.0 / returns.shape[1]] * returns.shape[1]
        equal_series = returns @ pd.Series(equal_weights, index=returns.columns)
        benchmark = returns.mean(axis=1)
        metrics = risk_metrics(equal_series, benchmark=benchmark)
        simulation = simulate_portfolio(
            equal_weights, returns, horizon_days=126, simulations=1000, seed=42
        )
        return {
            "status": "OK",
            "universe": list(returns.columns),
            "observations": int(len(returns)),
            "minimum_variance": minimum,
            "maximum_sharpe": maximum,
            "risk_parity": parity,
            "efficient_frontier": frontier,
            "equal_weight_risk": metrics,
            "monte_carlo": simulation,
            "assumptions": (
                "Historical sample mean/covariance; long-only; 252-session lookback. "
                "Simulation is not a forecast."
            ),
            "not_for_trading": True,
        }

    def research_notes(self, ticker: str) -> Dict[str, Any]:
        frame = self.notes.notes(ticker=ticker)
        status = self.notes.status()
        return {
            "status": "OK" if not frame.empty else status["status"],
            "ticker": ticker.upper(),
            "items": frame.to_dict(orient="records") if not frame.empty else [],
            "reason": None if not frame.empty else status["reason"],
        }

    def research_report(self) -> Dict[str, Any]:
        return load_latest_research_report()
