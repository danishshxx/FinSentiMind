"""Numerical tests for TASK 117 portfolio and risk analytics."""
import numpy as np
import pandas as pd
import pytest

from ml.risk.engine import (
    capm,
    efficient_frontier,
    estimate_beta,
    factor_exposures,
    historical_var,
    max_drawdown,
    optimize_weights,
    parametric_var,
    returns_from_prices,
    risk_metrics,
    risk_parity_weights,
    simulate_portfolio,
    stress_test,
)


def _returns():
    index = pd.date_range("2025-01-01", periods=12, freq="B")
    return pd.DataFrame({
        "A": [0.01, -0.005, 0.008, -0.004, 0.006, 0.004, -0.002, 0.005, 0.003, -0.001, 0.007, 0.002],
        "B": [-0.004, 0.006, -0.003, 0.005, -0.002, 0.004, 0.003, -0.001, 0.002, 0.004, -0.003, 0.001],
    }, index=index)


def test_returns_from_long_prices():
    prices = pd.DataFrame([
        {"ticker": "A", "trading_date": "2025-01-01", "close": 100},
        {"ticker": "A", "trading_date": "2025-01-02", "close": 110},
        {"ticker": "A", "trading_date": "2025-01-03", "close": 121},
    ])
    returns = returns_from_prices(prices)
    assert returns.shape == (2, 1)
    assert returns.iloc[-1, 0] == pytest.approx(0.10)


def test_min_variance_weights_sum_to_one():
    result = optimize_weights(_returns(), objective="min_variance")
    assert result["status"] == "OK"
    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert result["volatility"] > 0


def test_max_sharpe_respects_bounds():
    result = optimize_weights(
        _returns(), objective="max_sharpe", bounds=(0.0, 0.8)
    )
    assert max(result["weights"].values()) <= 0.8 + 1e-8
    assert sum(result["weights"].values()) == pytest.approx(1.0)


def test_target_return_is_met():
    returns = _returns()
    target = float((returns.mean() * 252).mean())
    result = optimize_weights(
        returns, objective="target_return", target_return=target
    )
    assert result["expected_return"] == pytest.approx(target, rel=1e-4, abs=1e-5)


def test_risk_parity_equalizes_contributions_for_uncorrelated_equal_risk_assets():
    index = pd.date_range("2025-01-01", periods=8, freq="B")
    returns = pd.DataFrame({
        "A": [0.01, -0.01] * 4,
        "B": [0.01, 0.01, -0.01, -0.01] * 2,
    }, index=index)
    result = risk_parity_weights(returns)
    contributions = result["risk_contributions"]
    assert contributions["A"] == pytest.approx(0.5, abs=1e-3)
    assert contributions["B"] == pytest.approx(0.5, abs=1e-3)


def test_efficient_frontier_returns_points():
    frontier = efficient_frontier(_returns(), points=5)
    assert frontier
    assert all(point["volatility"] >= 0 for point in frontier)


def test_capm_formula():
    result = capm(beta=1.2, risk_free_rate=0.05, market_return=0.10)
    assert result["expected_return"] == pytest.approx(0.11)


def test_estimate_beta():
    market = pd.Series([0.01, -0.02, 0.03, 0.00, 0.02])
    asset = 2.0 * market
    result = estimate_beta(asset, market)
    assert result["beta"] == pytest.approx(2.0)


def test_max_drawdown():
    assert max_drawdown([0.10, -0.20, 0.10]) == pytest.approx(-0.20)


def test_historical_and_parametric_var_cvar():
    historical = historical_var([-0.10, -0.05, 0.0, 0.05, 0.10], confidence=0.75)
    assert historical["var"] == pytest.approx(0.05)
    assert historical["cvar"] == pytest.approx(0.075)
    parametric = parametric_var(mean=0.0, std=0.02, confidence=0.95)
    assert parametric["var"] == pytest.approx(0.0329, abs=1e-4)
    assert parametric["cvar"] >= parametric["var"]


def test_monte_carlo_is_reproducible_and_ordered():
    first = simulate_portfolio(
        [0.5, 0.5], _returns(), horizon_days=20, simulations=500, seed=7
    )
    second = simulate_portfolio(
        [0.5, 0.5], _returns(), horizon_days=20, simulations=500, seed=7
    )
    assert first["terminal_percentiles"] == second["terminal_percentiles"]
    values = list(first["terminal_percentiles"].values())
    assert values == sorted(values)
    assert first["probability_of_loss"] >= 0
    assert first["probability_of_loss"] <= 1


def test_monte_carlo_parametric_runs():
    result = simulate_portfolio(
        [0.5, 0.5], _returns(), method="parametric", horizon_days=10,
        simulations=100, seed=1,
    )
    assert result["status"] == "OK"
    assert len(result["max_drawdown_percentiles"]) == 5


def test_stress_test_uses_explicit_shocks():
    result = stress_test(
        {"A": 0.5, "B": 0.5},
        {"A": -0.2, "energy": -0.1},
        sector_map={"B": "energy"},
    )
    assert result["portfolio_impact"] == pytest.approx(-0.15)


def test_factor_exposures_recover_synthetic_betas():
    index = pd.date_range("2025-01-01", periods=60, freq="B")
    rng = np.random.default_rng(42)
    factor = pd.DataFrame({
        "market": rng.normal(0, 0.01, 60),
        "value": rng.normal(0, 0.01, 60),
    }, index=index)
    asset = 0.001 + 2.0 * factor["market"] - 0.5 * factor["value"]
    result = factor_exposures(asset, factor)
    assert result["exposures"]["market"] == pytest.approx(2.0)
    assert result["exposures"]["value"] == pytest.approx(-0.5)


def test_risk_metrics_with_benchmark():
    returns = pd.Series([0.01, -0.02, 0.03, 0.01, -0.01])
    benchmark = pd.Series([0.005, -0.01, 0.02, 0.0, 0.0])
    result = risk_metrics(returns, benchmark, risk_free_rate=0.02)
    assert result["status"] == "OK"
    assert "sharpe" in result
    assert "beta" in result
    assert "information_ratio" in result
