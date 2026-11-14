# Quantitative Portfolio and Risk Lab

Status: **ENGINE IMPLEMENTED**

## Inputs

- Daily asset returns built from local adjusted close prices.
- Only complete overlapping observations are used; no forward filling.
- Expected returns are historical sample means, an explicit assumption.

## Mean-variance optimization

```text
E(Rp) = w' mu
sigma_p^2 = w' Sigma w
```

Objectives:

- minimum variance;
- maximum Sharpe;
- target return.

Constraints:

- fully invested (`sum(w) = 1`);
- long-only by default;
- optional per-asset bounds;
- optional sector maximums.

No short selling is enabled by default. The covariance matrix receives a tiny
numerical ridge for stability; this is not an economic assumption.

## CAPM and beta

```text
E(Ri) = Rf + beta_i * (E(Rm) - Rf)
beta_i = Cov(Ri, Rm) / Var(Rm)
```

CAPM is reported as a model, not a guaranteed future return.

## Risk parity

Weights are chosen to equalize variance risk contributions:

```text
RC_i = w_i * (Sigma w)_i / (w' Sigma w)
```

Risk parity equalizes risk contributions, not expected returns or downside.

## Monte Carlo

Historical block bootstrap resamples contiguous blocks of actual returns.
Parametric mode uses the stated GBM approximation:

```text
P(t+1) = P(t) * exp((mu - 0.5 sigma^2) + sigma Z)
```

Outputs include terminal percentiles, probability of loss, probability of
reaching a target, and drawdown percentiles. Seeds are explicit and reproducible.

## VaR and CVaR

- Historical VaR: loss quantile of observed returns.
- Parametric VaR: normal-distribution assumption.
- CVaR / Expected Shortfall: mean loss beyond VaR.

Confidence level and horizon must always be shown. VaR is not a worst-case
bound.

## Risk metrics

Annualized return and volatility, Sharpe, Sortino, maximum drawdown, Calmar,
downside deviation, beta, alpha (with benchmark), tracking error, information
ratio, correlation, and rolling volatility where requested.

## Stress testing

```text
portfolio_impact = sum(weight_i * shock_i)
```

Shocks are explicit inputs. Unspecified assets receive zero shock by
convention. No historical crisis data is fabricated.

## Factor exposure

OLS regression of asset returns on supplied factor returns. Exposures are
sample estimates and may be unstable. Factors are only used when their data is
actually available.

## Limitations

- Historical mean/covariance are noisy estimates.
- Optimization is sensitive to input errors.
- Normal VaR understates fat tails.
- Monte Carlo is not a prediction.
- Stress scenarios are user-defined, not historical facts.
