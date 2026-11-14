"""Numerical tests for TASK 114 metrics, valuation, and scenarios."""
import pytest

from ml.fundamentals.metrics import compute_metrics
from ml.fundamentals.valuation import (
    DCFInputs,
    OwnerEarningsInputs,
    ValuationInputError,
    build_scenarios,
    compute_wacc,
    dcf_sensitivity,
    dcf_valuation,
    margin_of_safety,
    owner_earnings,
    WACCInputs,
)


def test_owner_earnings_formula():
    result = owner_earnings(OwnerEarningsInputs(
        net_income=100,
        depreciation_amortization=20,
        maintenance_capex=10,
        working_capital_change=5,
    ))
    assert result.status == "OK"
    assert result.value == 105


def test_owner_earnings_requires_explicit_maintenance_capex():
    result = owner_earnings(OwnerEarningsInputs(
        net_income=100,
        depreciation_amortization=20,
        maintenance_capex=None,
        working_capital_change=5,
    ))
    assert result.status == "UNAVAILABLE"
    assert result.value is None


def test_dcf_terminal_value_and_equity_bridge():
    inputs = DCFInputs(
        fcff_base=100,
        growth_rate=0.0,
        terminal_growth=0.02,
        wacc=0.10,
        forecast_years=2,
        net_debt=50,
        shares_outstanding=10,
    )
    result = dcf_valuation(inputs)
    expected_tv = 100 * 1.02 / (0.10 - 0.02)
    expected_ev = (
        100 / 1.10 + 100 / 1.10**2
        + expected_tv / 1.10**2
    )
    assert result.terminal_value == pytest.approx(expected_tv)
    assert result.enterprise_value == pytest.approx(expected_ev)
    assert result.equity_value == pytest.approx(expected_ev - 50)
    assert result.intrinsic_value_per_share == pytest.approx((expected_ev - 50) / 10)


def test_dcf_rejects_wacc_not_above_growth():
    with pytest.raises(ValuationInputError, match="WACC"):
        dcf_valuation(DCFInputs(
            fcff_base=100, growth_rate=0, terminal_growth=0.03,
            wacc=0.03, forecast_years=5, net_debt=0, shares_outstanding=1,
        ))


def test_margin_of_safety():
    assert margin_of_safety(100, 70) == pytest.approx(0.30)
    assert margin_of_safety(100, 130) == pytest.approx(-0.30)
    assert margin_of_safety(None, 100) is None


def test_wacc_capm_formula():
    result = compute_wacc(WACCInputs(
        risk_free_rate=0.06,
        beta=1.2,
        equity_risk_premium=0.05,
        cost_of_debt=0.09,
        market_value_equity=700,
        market_value_debt=300,
        tax_rate=0.25,
    ))
    ke = 0.06 + 1.2 * 0.05
    expected = 0.7 * ke + 0.3 * 0.09 * 0.75
    assert result.wacc == pytest.approx(expected)


def test_dcf_sensitivity_skips_invalid_wacc_growth_pairs():
    base = DCFInputs(
        fcff_base=100, growth_rate=0.05, terminal_growth=0.02,
        wacc=0.10, forecast_years=5, net_debt=0, shares_outstanding=1,
    )
    rows = dcf_sensitivity(base, wacc_values=[0.02, 0.10], growth_values=[0.03])
    assert len(rows) == 1
    assert rows[0]["wacc"] == 0.10


def test_scenarios_are_reported_separately():
    base = DCFInputs(
        fcff_base=100, growth_rate=0.05, terminal_growth=0.02,
        wacc=0.10, forecast_years=5, net_debt=0, shares_outstanding=10,
    )
    bear = DCFInputs(
        fcff_base=100, growth_rate=0.0, terminal_growth=0.01,
        wacc=0.12, forecast_years=5, net_debt=0, shares_outstanding=10,
    )
    results = build_scenarios(base, 50, {"base": base, "bear": bear})
    assert [item.name for item in results] == ["base", "bear"]
    assert results[0].intrinsic_value_per_share > results[1].intrinsic_value_per_share
    assert all(item.margin_of_safety is not None for item in results)


def test_metric_ratios_are_numerically_correct():
    current = {
        "revenue": 1000, "gross_profit": 400, "operating_income": 200,
        "net_income": 100, "total_assets": 1000, "total_equity": 500,
        "total_debt": 200, "cash": 50, "interest_expense": 20,
        "operating_cash_flow": 150, "capex": 50,
        "depreciation_amortization": 50, "eps": 1.0,
    }
    previous = {"revenue": 900, "net_income": 80, "total_equity": 450,
                "total_assets": 950, "eps": 0.8}
    metrics = compute_metrics(
        current, previous, market_price=10, shares_outstanding=100,
        dividend_per_share=0.2, tax_rate=0.25,
    )
    assert metrics["gross_margin"].value == pytest.approx(0.4)
    assert metrics["net_margin"].value == pytest.approx(0.1)
    assert metrics["return_on_equity"].value == pytest.approx(100 / 475)
    assert metrics["return_on_assets"].value == pytest.approx(100 / 975)
    assert metrics["return_on_invested_capital"].value == pytest.approx(150 / 650)
    assert metrics["debt_to_equity"].value == pytest.approx(0.4)
    assert metrics["net_debt"].value == pytest.approx(150)
    assert metrics["net_debt_to_ebitda"].value == pytest.approx(150 / 250)
    assert metrics["interest_coverage"].value == pytest.approx(10)
    assert metrics["free_cash_flow"].value == pytest.approx(100)
    assert metrics["cash_conversion"].value == pytest.approx(1.5)
    assert metrics["price_to_earnings"].value == pytest.approx(10)
    assert metrics["price_to_book"].value == pytest.approx(2)
    assert metrics["enterprise_value"].value == pytest.approx(1150)
    assert metrics["ev_to_ebitda"].value == pytest.approx(1150 / 250)
    assert metrics["price_to_sales"].value == pytest.approx(1)
    assert metrics["fcf_yield"].value == pytest.approx(0.1)
    assert metrics["dividend_yield"].value == pytest.approx(0.02)


def test_missing_inputs_are_unavailable_not_zero():
    metrics = compute_metrics({"revenue": 100})
    assert metrics["net_margin"].status == "UNAVAILABLE"
    assert metrics["net_margin"].value is None
