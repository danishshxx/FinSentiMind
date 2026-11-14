"""Quantitative portfolio optimization, risk, and simulation.

All results are reproducible from the supplied return matrix. No expected
returns are forecast; the historical sample mean is an explicit assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm


TRADING_DAYS = 252


def returns_from_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Build a date x ticker simple-return matrix from long price data."""
    if prices.empty or {"ticker", "trading_date", "close"}.difference(prices.columns):
        return pd.DataFrame()
    wide = prices.pivot(
        index="trading_date", columns="ticker", values="close"
    ).sort_index()
    returns = wide.pct_change(fill_method=None)
    return returns.dropna(how="any")


def annualized_mean_cov(returns: pd.DataFrame) -> Tuple[pd.Series, pd.DataFrame]:
    if returns.empty:
        raise ValueError("returns matrix is empty")
    return returns.mean() * TRADING_DAYS, returns.cov() * TRADING_DAYS


def portfolio_stats(
    weights: Sequence[float],
    mean: pd.Series,
    cov: pd.DataFrame,
    risk_free_rate: float = 0.0,
) -> Dict[str, float]:
    weights = np.asarray(weights, dtype=float)
    expected = float(weights @ mean.to_numpy())
    variance = float(weights @ cov.to_numpy() @ weights)
    volatility = float(np.sqrt(max(variance, 0.0)))
    sharpe = (expected - risk_free_rate) / volatility if volatility > 0 else 0.0
    return {
        "expected_return": expected,
        "volatility": volatility,
        "sharpe": sharpe,
    }


def _bounds(n: int, bounds) -> List[Tuple[float, float]]:
    if bounds is None:
        return [(0.0, 1.0)] * n
    if isinstance(bounds, tuple) and len(bounds) == 2 and not isinstance(bounds[0], tuple):
        return [bounds] * n
    if len(bounds) != n:
        raise ValueError("bounds length must match asset count")
    return list(bounds)


def optimize_weights(
    returns: pd.DataFrame,
    objective: str = "min_variance",
    target_return: Optional[float] = None,
    risk_free_rate: float = 0.0,
    bounds=None,
    sector_map: Optional[Dict[str, str]] = None,
    sector_limits: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Long-only mean-variance optimization with optional constraints."""
    if returns.empty or returns.shape[1] < 1:
        raise ValueError("returns matrix has no assets")
    mean, cov = annualized_mean_cov(returns)
    tickers = list(returns.columns)
    n = len(tickers)
    if n == 1:
        return {
            "status": "OK", "objective": objective,
            "weights": {tickers[0]: 1.0},
            **portfolio_stats([1.0], mean, cov, risk_free_rate),
            "assumptions": "Historical sample mean/covariance; long-only.",
        }

    matrix = cov.to_numpy()
    matrix = matrix + np.eye(n) * 1e-12
    bnds = _bounds(n, bounds)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    if target_return is not None:
        constraints.append({
            "type": "eq",
            "fun": lambda w: float(w @ mean.to_numpy()) - target_return,
        })
    if sector_map and sector_limits:
        for sector, limit in sector_limits.items():
            index = [i for i, ticker in enumerate(tickers) if sector_map.get(ticker) == sector]
            if index:
                constraints.append({
                    "type": "ineq",
                    "fun": (lambda w, idx=index: limit - np.sum(w[idx])),
                })

    def variance(w):
        return float(w @ matrix @ w)

    def negative_sharpe(w):
        stats = portfolio_stats(w, mean, cov, risk_free_rate)
        return -stats["sharpe"] if stats["sharpe"] else 1e9

    objective_fn = negative_sharpe if objective == "max_sharpe" else variance
    if objective not in ("min_variance", "max_sharpe", "target_return"):
        raise ValueError("unsupported objective")
    if objective == "target_return" and target_return is None:
        raise ValueError("target_return required for target_return objective")

    start = np.repeat(1.0 / n, n)
    result = minimize(
        objective_fn, start, method="SLSQP", bounds=bnds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    if not result.success:
        raise RuntimeError(f"optimizer failed: {result.message}")
    weights = np.clip(result.x, 0.0, 1.0)
    weights = weights / weights.sum()
    stats = portfolio_stats(weights, mean, cov, risk_free_rate)
    return {
        "status": "OK",
        "objective": objective,
        "weights": {ticker: float(weight) for ticker, weight in zip(tickers, weights)},
        **stats,
        "risk_contributions": {
            ticker: float(value)
            for ticker, value in zip(tickers, risk_contributions(weights, cov))
        },
        "assumptions": (
            "Historical sample mean and covariance; long-only unless bounds "
            "state otherwise. Mean-variance inputs are estimates, not forecasts."
        ),
    }


def risk_contributions(weights: Sequence[float], cov: pd.DataFrame) -> np.ndarray:
    weights = np.asarray(weights, dtype=float)
    variance = float(weights @ cov.to_numpy() @ weights)
    if variance <= 0:
        return np.zeros_like(weights)
    marginal = cov.to_numpy() @ weights
    return weights * marginal / variance


def risk_parity_weights(returns: pd.DataFrame) -> Dict[str, Any]:
    if returns.empty:
        raise ValueError("returns matrix is empty")
    tickers = list(returns.columns)
    cov = returns.cov() * TRADING_DAYS
    n = len(tickers)
    if n == 1:
        return {"status": "OK", "weights": {tickers[0]: 1.0}}

    def objective(w):
        contributions = risk_contributions(w, cov)
        target = 1.0 / n
        return float(np.sum((contributions - target) ** 2))

    result = minimize(
        objective, np.repeat(1.0 / n, n), method="SLSQP",
        bounds=[(1e-8, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}],
        options={"maxiter": 1000, "ftol": 1e-14},
    )
    if not result.success:
        raise RuntimeError(f"risk-parity optimizer failed: {result.message}")
    weights = np.clip(result.x, 0.0, 1.0)
    weights = weights / weights.sum()
    return {
        "status": "OK",
        "weights": {ticker: float(weight) for ticker, weight in zip(tickers, weights)},
        "risk_contributions": {
            ticker: float(value)
            for ticker, value in zip(tickers, risk_contributions(weights, cov))
        },
        "limitation": (
            "Risk parity equalizes variance contributions, not expected returns "
            "or downside risk."
        ),
    }


def efficient_frontier(
    returns: pd.DataFrame, points: int = 8, risk_free_rate: float = 0.0
) -> List[Dict[str, Any]]:
    mean, cov = annualized_mean_cov(returns)
    low = float(mean.min())
    high = float(mean.max())
    targets = np.linspace(low, high, points)
    rows = []
    for target in targets:
        try:
            result = optimize_weights(
                returns, objective="target_return", target_return=float(target),
                risk_free_rate=risk_free_rate,
            )
            rows.append({
                "target_return": float(target),
                "volatility": result["volatility"],
                "weights": result["weights"],
            })
        except RuntimeError:
            continue
    return rows


def capm(
    beta: float, risk_free_rate: float, market_return: float
) -> Dict[str, float]:
    expected = risk_free_rate + beta * (market_return - risk_free_rate)
    return {
        "expected_return": expected,
        "market_risk_premium": market_return - risk_free_rate,
        "formula": "E(Ri) = Rf + beta * (E(Rm) - Rf)",
    }


def estimate_beta(asset_returns: pd.Series, market_returns: pd.Series) -> Dict[str, float]:
    aligned = pd.concat([asset_returns, market_returns], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        raise ValueError("insufficient overlapping observations")
    asset = aligned.iloc[:, 0].to_numpy()
    market = aligned.iloc[:, 1].to_numpy()
    variance = float(np.var(market, ddof=1))
    beta = float(np.cov(asset, market, ddof=1)[0, 1] / variance) if variance else 0.0
    alpha = float(np.mean(asset) - beta * np.mean(market))
    return {"beta": beta, "alpha_daily": alpha}


def max_drawdown(returns: Iterable[float]) -> float:
    values = np.asarray(list(returns), dtype=float)
    if values.size == 0:
        return 0.0
    curve = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(curve)
    drawdowns = curve / peaks - 1.0
    return float(drawdowns.min())


def risk_metrics(
    returns: pd.Series,
    benchmark: Optional[pd.Series] = None,
    risk_free_rate: float = 0.0,
) -> Dict[str, Any]:
    values = returns.dropna()
    if len(values) < 2:
        return {"status": "UNAVAILABLE", "reason": "Insufficient return observations."}
    mean_daily = float(values.mean())
    vol_daily = float(values.std(ddof=1))
    annual_return = mean_daily * TRADING_DAYS
    annual_vol = vol_daily * np.sqrt(TRADING_DAYS)
    downside = values[values < 0]
    downside_dev = float(downside.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else None
    sharpe = (annual_return - risk_free_rate) / annual_vol if annual_vol > 0 else None
    sortino = (
        (annual_return - risk_free_rate) / downside_dev
        if downside_dev and downside_dev > 0 else None
    )
    mdd = max_drawdown(values)
    calmar = annual_return / abs(mdd) if mdd < 0 else None
    result = {
        "status": "OK",
        "observations": int(len(values)),
        "annualized_return": annual_return,
        "annualized_volatility": annual_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "downside_deviation": downside_dev,
    }
    if benchmark is not None:
        aligned = pd.concat([values, benchmark], axis=1, join="inner").dropna()
        if len(aligned) > 1:
            beta_data = estimate_beta(aligned.iloc[:, 0], aligned.iloc[:, 1])
            market_return = float(aligned.iloc[:, 1].mean() * TRADING_DAYS)
            expected = risk_free_rate + beta_data["beta"] * (
                market_return - risk_free_rate
            )
            active = aligned.iloc[:, 0] - aligned.iloc[:, 1]
            tracking = float(active.std(ddof=1) * np.sqrt(TRADING_DAYS))
            result.update({
                "beta": beta_data["beta"],
                "alpha": annual_return - expected,
                "tracking_error": tracking,
                "information_ratio": (
                    float(active.mean() * TRADING_DAYS / tracking)
                    if tracking > 0 else None
                ),
                "correlation": float(aligned.corr().iloc[0, 1]),
            })
    return result


def historical_var(returns: Iterable[float], confidence: float = 0.95) -> Dict[str, float]:
    values = np.asarray(list(returns), dtype=float)
    if values.size == 0:
        raise ValueError("returns are empty")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    quantile = float(np.quantile(values, 1.0 - confidence))
    tail = values[values <= quantile]
    return {
        "var": max(0.0, -quantile),
        "cvar": max(0.0, -float(tail.mean())) if tail.size else max(0.0, -quantile),
        "confidence": confidence,
        "formula": "VaR = -quantile(1-confidence); CVaR = -mean(loss beyond VaR)",
    }


def parametric_var(
    mean: float, std: float, confidence: float = 0.95
) -> Dict[str, float]:
    if std < 0:
        raise ValueError("std cannot be negative")
    z = float(norm.ppf(1.0 - confidence))
    var = max(0.0, -(mean + z * std))
    cvar = max(0.0, -(mean - std * float(norm.pdf(z) / (1.0 - confidence))))
    return {
        "var": var, "cvar": cvar, "confidence": confidence,
        "formula": "VaR = -(mean + z*std); normal-distribution assumption",
    }


def simulate_portfolio(
    weights: Sequence[float],
    returns: pd.DataFrame,
    horizon_days: int = 252,
    simulations: int = 5000,
    initial_value: float = 1_000_000.0,
    method: str = "bootstrap",
    block_size: int = 5,
    seed: int = 42,
    target_value: Optional[float] = None,
) -> Dict[str, Any]:
    if returns.empty:
        raise ValueError("returns matrix is empty")
    weights = np.asarray(weights, dtype=float)
    if len(weights) != returns.shape[1]:
        raise ValueError("weights length must match assets")
    if horizon_days < 1 or simulations < 1:
        raise ValueError("horizon_days and simulations must be positive")
    rng = np.random.default_rng(seed)
    portfolio_returns = returns.to_numpy() @ weights

    if method == "bootstrap":
        blocks_needed = int(np.ceil(horizon_days / block_size))
        path_returns = np.empty((simulations, horizon_days))
        for simulation in range(simulations):
            pieces = []
            for _ in range(blocks_needed):
                start = rng.integers(0, len(portfolio_returns) - block_size + 1)
                pieces.append(portfolio_returns[start:start + block_size])
            sample = np.concatenate(pieces)[:horizon_days]
            path_returns[simulation] = sample
    elif method == "parametric":
        mu = float(np.mean(portfolio_returns))
        sigma = float(np.std(portfolio_returns, ddof=1))
        shocks = rng.standard_normal((simulations, horizon_days))
        path_returns = np.exp(
            (mu - 0.5 * sigma ** 2) + sigma * shocks
        ) - 1.0
    else:
        raise ValueError("method must be bootstrap or parametric")

    paths = initial_value * np.cumprod(1.0 + path_returns, axis=1)
    terminal = paths[:, -1]
    drawdowns = np.empty(simulations)
    for i in range(simulations):
        curve = np.concatenate([[initial_value], paths[i]])
        peaks = np.maximum.accumulate(curve)
        drawdowns[i] = float(np.min(curve / peaks - 1.0))

    percentiles = {
        f"p{int(q * 100)}": float(np.quantile(terminal, q))
        for q in (0.05, 0.25, 0.50, 0.75, 0.95)
    }
    return {
        "status": "OK",
        "method": method,
        "seed": seed,
        "simulations": simulations,
        "horizon_days": horizon_days,
        "terminal_percentiles": percentiles,
        "expected_terminal_value": float(terminal.mean()),
        "median_terminal_value": float(np.median(terminal)),
        "probability_of_loss": float(np.mean(terminal < initial_value)),
        "probability_of_target": (
            float(np.mean(terminal >= target_value)) if target_value is not None else None
        ),
        "max_drawdown_percentiles": {
            f"p{int(q * 100)}": float(np.quantile(drawdowns, q))
            for q in (0.05, 0.25, 0.50, 0.75, 0.95)
        },
        "formula": (
            "Bootstrap: resample historical blocks. Parametric: daily GBM "
            "P(t+1)=P(t)*exp((mu-0.5*sigma^2)+sigma*Z)."
        ),
        "assumptions": (
            "Simulated outcomes are not predictions. Bootstrap assumes the "
            "historical sample is representative; parametric assumes normality."
        ),
        "not_for_trading": True,
    }


def stress_test(
    weights: Dict[str, float],
    shocks: Dict[str, float],
    sector_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Apply explicit ticker or sector shocks; no historical crisis data is invented."""
    sector_map = sector_map or {}
    contributions = {}
    total = 0.0
    for ticker, weight in weights.items():
        shock = shocks.get(ticker)
        if shock is None and ticker in sector_map:
            shock = shocks.get(sector_map[ticker])
        if shock is None:
            shock = 0.0
        impact = weight * shock
        contributions[ticker] = impact
        total += impact
    return {
        "portfolio_impact": total,
        "contributions": contributions,
        "formula": "sum(weight_i * shock_i)",
        "limitation": "Unspecified tickers/sectors receive zero shock by convention.",
    }


def factor_exposures(
    asset_returns: pd.Series, factor_returns: pd.DataFrame
) -> Dict[str, Any]:
    aligned = pd.concat([asset_returns, factor_returns], axis=1, join="inner").dropna()
    if len(aligned) < factor_returns.shape[1] + 2:
        return {"status": "UNAVAILABLE", "reason": "Insufficient observations."}
    y = aligned.iloc[:, 0].to_numpy()
    X = np.column_stack([np.ones(len(aligned)), aligned.iloc[:, 1:].to_numpy()])
    coefficients, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    return {
        "status": "OK",
        "intercept": float(coefficients[0]),
        "exposures": {
            factor: float(value)
            for factor, value in zip(factor_returns.columns, coefficients[1:])
        },
        "formula": "OLS: asset_return = intercept + sum(beta_factor * factor_return) + error",
        "limitation": "Factor exposures are sample estimates and may be unstable.",
    }
