"""Explainable owner earnings, DCF, and margin-of-safety calculations.

All assumptions are explicit inputs. Invalid economics (for example
WACC <= terminal growth) are rejected rather than smoothed over. Intrinsic
value is always presented as a scenario range, never as a certainty.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

OK = "OK"
UNAVAILABLE = "UNAVAILABLE"


class ValuationInputError(ValueError):
    """Raised for invalid or missing valuation assumptions."""


@dataclass(frozen=True)
class OwnerEarningsInputs:
    net_income: float
    depreciation_amortization: float
    maintenance_capex: float
    working_capital_change: float
    assumption_note: str = (
        "Maintenance capex is an explicit scenario input, not assumed equal "
        "to total capex."
    )


@dataclass
class OwnerEarningsResult:
    status: str
    value: Optional[float]
    formula: str
    inputs: Dict[str, Any]
    limitation: str


@dataclass(frozen=True)
class DCFInputs:
    fcff_base: float
    growth_rate: float
    terminal_growth: float
    wacc: float
    forecast_years: int
    net_debt: float
    shares_outstanding: float


@dataclass
class DCFResult:
    status: str
    enterprise_value: Optional[float]
    equity_value: Optional[float]
    intrinsic_value_per_share: Optional[float]
    present_value_fcff: Optional[float]
    present_value_terminal: Optional[float]
    terminal_value: Optional[float]
    projected_fcff: List[float]
    formula: str
    inputs: Dict[str, Any]
    limitation: str


def owner_earnings(inputs: OwnerEarningsInputs) -> OwnerEarningsResult:
    formula = (
        "net_income + depreciation_amortization - maintenance_capex "
        "- working_capital_change"
    )
    missing = [
        name for name in (
            "net_income", "depreciation_amortization",
            "maintenance_capex", "working_capital_change",
        )
        if getattr(inputs, name) is None
    ]
    if missing:
        return OwnerEarningsResult(
            status=UNAVAILABLE, value=None, formula=formula,
            inputs=asdict(inputs),
            limitation=f"Missing explicit inputs: {missing}.",
        )
    value = (
        inputs.net_income
        + inputs.depreciation_amortization
        - inputs.maintenance_capex
        - inputs.working_capital_change
    )
    return OwnerEarningsResult(
        status=OK, value=value, formula=formula, inputs=asdict(inputs),
        limitation=inputs.assumption_note,
    )


def validate_dcf_inputs(inputs: DCFInputs) -> None:
    if inputs.forecast_years < 1 or inputs.forecast_years > 30:
        raise ValuationInputError("forecast_years must be between 1 and 30")
    if inputs.wacc <= inputs.terminal_growth:
        raise ValuationInputError(
            "WACC must be greater than terminal growth"
        )
    if inputs.terminal_growth <= -1:
        raise ValuationInputError("terminal_growth must be greater than -100%")
    if inputs.shares_outstanding <= 0:
        raise ValuationInputError("shares_outstanding must be positive")


def dcf_valuation(inputs: DCFInputs) -> DCFResult:
    validate_dcf_inputs(inputs)
    projected: List[float] = []
    fcff = inputs.fcff_base
    for _ in range(inputs.forecast_years):
        fcff = fcff * (1.0 + inputs.growth_rate)
        projected.append(fcff)

    present_fcff = sum(
        value / ((1.0 + inputs.wacc) ** year)
        for year, value in enumerate(projected, start=1)
    )
    terminal = (
        projected[-1] * (1.0 + inputs.terminal_growth)
        / (inputs.wacc - inputs.terminal_growth)
    )
    present_terminal = terminal / ((1.0 + inputs.wacc) ** inputs.forecast_years)
    enterprise_value = present_fcff + present_terminal
    equity_value = enterprise_value - inputs.net_debt
    per_share = equity_value / inputs.shares_outstanding if equity_value else equity_value

    return DCFResult(
        status=OK,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        intrinsic_value_per_share=per_share,
        present_value_fcff=present_fcff,
        present_value_terminal=present_terminal,
        terminal_value=terminal,
        projected_fcff=projected,
        formula=(
            "EV = sum(FCFF_t / (1+WACC)^t) + TV/(1+WACC)^n; "
            "TV = FCFF_n * (1+g) / (WACC-g); "
            "Equity = EV - net_debt; per_share = Equity / diluted shares"
        ),
        inputs=asdict(inputs),
        limitation=(
            "Terminal growth and WACC dominate long-horizon value. "
            "This is a scenario, not a forecast."
        ),
    )


def margin_of_safety(intrinsic_value: Optional[float], market_price: Optional[float]) -> Optional[float]:
    if intrinsic_value is None or market_price is None or intrinsic_value == 0:
        return None
    return (intrinsic_value - market_price) / intrinsic_value


def dcf_sensitivity(
    inputs: DCFInputs,
    wacc_values: Optional[List[float]] = None,
    growth_values: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    wacc_values = wacc_values or [
        inputs.wacc - 0.02, inputs.wacc - 0.01,
        inputs.wacc, inputs.wacc + 0.01, inputs.wacc + 0.02,
    ]
    growth_values = growth_values or [
        inputs.terminal_growth - 0.01,
        inputs.terminal_growth,
        inputs.terminal_growth + 0.01,
    ]
    rows: List[Dict[str, Any]] = []
    for wacc in wacc_values:
        for growth in growth_values:
            if wacc <= growth or wacc <= 0:
                continue
            row_inputs = DCFInputs(
                fcff_base=inputs.fcff_base,
                growth_rate=inputs.growth_rate,
                terminal_growth=growth,
                wacc=wacc,
                forecast_years=inputs.forecast_years,
                net_debt=inputs.net_debt,
                shares_outstanding=inputs.shares_outstanding,
            )
            result = dcf_valuation(row_inputs)
            rows.append({
                "wacc": wacc,
                "terminal_growth": growth,
                "intrinsic_value_per_share": result.intrinsic_value_per_share,
            })
    return rows


@dataclass(frozen=True)
class WACCInputs:
    risk_free_rate: float
    beta: float
    equity_risk_premium: float
    cost_of_debt: float
    market_value_equity: float
    market_value_debt: float
    tax_rate: float


@dataclass
class WACCResult:
    status: str
    wacc: Optional[float]
    cost_of_equity: Optional[float]
    formula: str
    inputs: Dict[str, Any]
    limitation: str


def compute_wacc(inputs: WACCInputs) -> WACCResult:
    total = inputs.market_value_equity + inputs.market_value_debt
    if total <= 0:
        return WACCResult(
            status=UNAVAILABLE, wacc=None, cost_of_equity=None,
            formula="WACC = E/(D+E)*Ke + D/(D+E)*Kd*(1-tax)",
            inputs=asdict(inputs), limitation="Total capital must be positive.",
        )
    ke = inputs.risk_free_rate + inputs.beta * inputs.equity_risk_premium
    wacc = (
        inputs.market_value_equity / total * ke
        + inputs.market_value_debt / total
        * inputs.cost_of_debt * (1.0 - inputs.tax_rate)
    )
    return WACCResult(
        status=OK, wacc=wacc, cost_of_equity=ke,
        formula=(
            "Ke = Rf + beta * ERP; "
            "WACC = E/(D+E)*Ke + D/(D+E)*Kd*(1-tax)"
        ),
        inputs=asdict(inputs),
        limitation="Inputs are estimates; CAPM is not a guaranteed return.",
    )


@dataclass
class ScenarioResult:
    name: str
    intrinsic_value_per_share: Optional[float]
    margin_of_safety: Optional[float]
    dcf: DCFResult
    margin_of_safety_formula: str = "(intrinsic_value - market_price) / intrinsic_value"


def build_scenarios(
    base_inputs: DCFInputs,
    market_price: Optional[float],
    scenarios: Optional[Dict[str, DCFInputs]] = None,
) -> List[ScenarioResult]:
    scenarios = scenarios or {"base": base_inputs}
    results: List[ScenarioResult] = []
    for name, inputs in scenarios.items():
        dcf = dcf_valuation(inputs)
        results.append(ScenarioResult(
            name=name,
            intrinsic_value_per_share=dcf.intrinsic_value_per_share,
            margin_of_safety=margin_of_safety(
                dcf.intrinsic_value_per_share, market_price
            ),
            dcf=dcf,
        ))
    return results


def build_valuation_report(
    owner: OwnerEarningsResult,
    base_dcf: DCFResult,
    scenarios: List[ScenarioResult],
    market_price: Optional[float],
    assumptions: Dict[str, Any],
    missing_data_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "status": "OK" if base_dcf.status == OK else UNAVAILABLE,
        "market_price": market_price,
        "owner_earnings": asdict(owner),
        "base_dcf": asdict(base_dcf),
        "scenarios": [
            {
                "name": scenario.name,
                "intrinsic_value_per_share": scenario.intrinsic_value_per_share,
                "margin_of_safety": scenario.margin_of_safety,
                "margin_of_safety_formula": scenario.margin_of_safety_formula,
                "dcf": asdict(scenario.dcf),
            }
            for scenario in scenarios
        ],
        "assumptions": assumptions,
        "missing_data_warnings": missing_data_warnings or [],
        "disclaimer": (
            "Intrinsic value is assumption-dependent and is not a guarantee "
            "of future returns. Do not present a single scenario as certain."
        ),
    }
