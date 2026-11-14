"""Fundamental analysis orchestration for one ticker."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.fundamentals.metrics import (
    MetricResult,
    compute_metrics,
    map_line_items,
    metrics_to_dict,
    summarize_available,
)
from ml.fundamentals.repository import FundamentalRepository
from ml.fundamentals.valuation import (
    DCFInputs,
    OwnerEarningsInputs,
    build_scenarios,
    build_valuation_report,
    dcf_valuation,
    dcf_sensitivity,
    margin_of_safety,
    owner_earnings,
    validate_dcf_inputs,
)


logger = get_logger(__name__)


def _annual_periods(repo: FundamentalRepository, ticker: str, as_of=None) -> List[pd.DataFrame]:
    statements = repo.statements(ticker=ticker, as_of=as_of)
    if statements.empty:
        return []
    annual = statements[statements["period_type"] == "annual"].copy()
    if annual.empty:
        return []
    periods = []
    for period_end, group in annual.groupby("period_end", sort=True):
        periods.append(group)
    return periods


def _snapshot(period_df: pd.DataFrame) -> Dict[str, Any]:
    if period_df.empty:
        return {}
    first = period_df.iloc[0]
    return {
        "ticker": first["ticker"],
        "period_end": str(first["period_end"]),
        "period_start": str(first["period_start"]),
        "currency": first["currency"],
        "unit_scale": first.get("unit_scale"),
        "values": map_line_items(period_df),
        "source_documents": sorted(
            set(period_df["source_document"].dropna().astype(str))
        ),
    }


def _working_capital(values: Dict[str, float]) -> Optional[float]:
    current_assets = values.get("current_assets")
    current_liabilities = values.get("current_liabilities")
    if current_assets is None or current_liabilities is None:
        return None
    return current_assets - current_liabilities


def _fcff_from_statements(
    values: Dict[str, float], tax_rate: Optional[float], working_capital_change: Optional[float]
) -> Optional[float]:
    operating_income = values.get("operating_income")
    da = values.get("depreciation_amortization")
    capex = values.get("capex")
    if (
        operating_income is None or da is None or capex is None
        or tax_rate is None or working_capital_change is None
    ):
        return None
    nopat = operating_income * (1.0 - tax_rate)
    return nopat + da - abs(capex) - working_capital_change


def _latest_dividend(repo: FundamentalRepository, ticker: str, as_of=None) -> Optional[float]:
    dividends = repo.dividends(ticker=ticker, as_of=as_of)
    if dividends.empty:
        return None
    row = dividends.sort_values("ex_date").iloc[-1]
    value = row.get("dividend_per_share")
    return None if pd.isna(value) else float(value)


def build_ticker_analysis(
    repo: FundamentalRepository,
    ticker: str,
    market_price: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
    tax_rate: Optional[float] = None,
    dcf_inputs: Optional[Dict[str, Any]] = None,
    maintenance_capex: Optional[float] = None,
    working_capital_change: Optional[float] = None,
    as_of=None,
) -> Dict[str, Any]:
    """Build metrics, owner earnings, DCF scenarios, and a valuation report."""
    periods = _annual_periods(repo, ticker, as_of=as_of)
    if not periods:
        return {
            "ticker": ticker.upper(),
            "status": "UNAVAILABLE",
            "reason": "No annual fundamental statements are available.",
            "not_for_trading": True,
        }

    current = _snapshot(periods[-1])
    previous = _snapshot(periods[-2]) if len(periods) > 1 else {}
    values = current["values"]
    previous_values = previous.get("values", {})
    shares = shares_outstanding or values.get("shares_outstanding")
    dividend = _latest_dividend(repo, ticker, as_of=as_of)

    metrics = compute_metrics(
        values,
        previous_values,
        market_price=market_price,
        shares_outstanding=shares,
        dividend_per_share=dividend,
        tax_rate=tax_rate,
    )

    owner_inputs = OwnerEarningsInputs(
        net_income=values.get("net_income"),
        depreciation_amortization=values.get("depreciation_amortization"),
        maintenance_capex=maintenance_capex,
        working_capital_change=working_capital_change,
    )
    owner = owner_earnings(owner_inputs)

    # Build DCF only when every input is explicit and available.
    dcf_report = None
    scenarios = []
    sensitivity = []
    base_fcff = None
    if dcf_inputs:
        if dcf_inputs.get("fcff_base") is not None:
            base_fcff = float(dcf_inputs["fcff_base"])
        else:
            base_fcff = _fcff_from_statements(
                values, tax_rate, working_capital_change
            )
        net_debt = metrics["net_debt"].value
        if base_fcff is not None and net_debt is not None and shares:
            base = DCFInputs(
                fcff_base=base_fcff,
                growth_rate=float(dcf_inputs["growth_rate"]),
                terminal_growth=float(dcf_inputs["terminal_growth"]),
                wacc=float(dcf_inputs["wacc"]),
                forecast_years=int(dcf_inputs.get("forecast_years", 5)),
                net_debt=net_debt,
                shares_outstanding=float(shares),
            )
            scenario_inputs = {"base": base}
            for name, overrides in dcf_inputs.get("scenarios", {}).items():
                scenario_inputs[name] = DCFInputs(
                    fcff_base=base.fcff_base,
                    growth_rate=float(overrides.get("growth_rate", base.growth_rate)),
                    terminal_growth=float(
                        overrides.get("terminal_growth", base.terminal_growth)
                    ),
                    wacc=float(overrides.get("wacc", base.wacc)),
                    forecast_years=int(
                        overrides.get("forecast_years", base.forecast_years)
                    ),
                    net_debt=base.net_debt,
                    shares_outstanding=base.shares_outstanding,
                )
            scenarios = build_scenarios(base, market_price, scenario_inputs)
            base_dcf = dcf_valuation(base)
            sensitivity = dcf_sensitivity(base)
            dcf_report = base_dcf
            assumptions = {
                **dcf_inputs,
                "tax_rate": tax_rate,
                "shares_outstanding": shares,
                "net_debt": net_debt,
            }
            valuation = build_valuation_report(
                owner, base_dcf, scenarios, market_price, assumptions,
                missing_data_warnings=_missing_warnings(metrics),
            )
        else:
            valuation = {
                "status": "UNAVAILABLE",
                "reason": (
                    "DCF requires explicit FCFF or operating income, D&A, capex, "
                    "working-capital change, tax rate, net debt, and shares."
                ),
                "not_for_trading": True,
            }
    else:
        valuation = {
            "status": "UNAVAILABLE",
            "reason": "No explicit DCF assumptions were supplied.",
            "not_for_trading": True,
        }

    return {
        "ticker": ticker.upper(),
        "status": valuation.get("status", "PARTIAL"),
        "as_of": str(as_of) if as_of else None,
        "current_period": current,
        "previous_period": previous or None,
        "metrics": metrics_to_dict(metrics),
        "metric_summary": summarize_available(metrics),
        "valuation": valuation,
        "dcf_sensitivity": sensitivity,
        "not_for_trading": True,
        "disclaimer": (
            "This is an educational research report. Missing data remains "
            "unavailable; intrinsic value is scenario-dependent."
        ),
    }


def _missing_warnings(metrics: Dict[str, MetricResult]) -> List[str]:
    return [
        f"{name}: {metric.limitation}"
        for name, metric in metrics.items()
        if metric.status != "OK"
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze one ticker.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--price", type=float, default=None)
    parser.add_argument("--shares", type=float, default=None)
    parser.add_argument("--tax-rate", type=float, default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--dcf-json", type=Path, default=None)
    parser.add_argument("--maintenance-capex", type=float, default=None)
    parser.add_argument("--working-capital-change", type=float, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    from ml.fundamentals.ingestion import DEFAULT_DATA_DIR
    repo = FundamentalRepository(args.data_dir or DEFAULT_DATA_DIR)
    dcf_inputs = (
        json.loads(args.dcf_json.read_text(encoding="utf-8"))
        if args.dcf_json else None
    )
    result = build_ticker_analysis(
        repo, args.ticker, market_price=args.price, shares_outstanding=args.shares,
        tax_rate=args.tax_rate, dcf_inputs=dcf_inputs,
        maintenance_capex=args.maintenance_capex,
        working_capital_change=args.working_capital_change,
        as_of=args.as_of,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
