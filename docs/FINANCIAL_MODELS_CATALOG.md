# Financial Models Catalog

This catalog documents formulas actually implemented in FinSentiMind. It is an
educational reference, not investment advice. Every model depends on data
quality and stated assumptions.

## 1. Price and technical features

| Model | Formula | Inputs | Assumptions / limits |
|---|---|---|---|
| Simple return | `close_t / close_{t-1} - 1` | adjusted close | First row per ticker is NULL |
| SMA(n) | mean(close over n sessions) | close | Warm-up NULL |
| EMA(n) | recursive EMA with smoothing `2/(n+1)` | close | Warm-up NULL |
| RSI(14) | `100 - 100/(1+RS)`, RS = avg gain / avg loss | close | Not meaningful in thin markets |
| ATR(14) | average true range | OHLC | Uses high/low/previous close |
| Realized volatility | `std(daily return) * sqrt(252)` | returns | Annualization assumes 252 sessions |
| Relative volume | `volume_t / average(volume, 20)` | volume | Ratio only; no signal |

## 2. Market context

| Model | Formula | Assumptions / limits |
|---|---|---|
| Equal-weight return | mean of available ticker returns | Not IHSG |
| Breadth | count return > 0, < 0, = 0 | Missing returns excluded |
| Cross-section | median / P10 / P90 | Descriptive only |
| Regime | return/volatility percentile thresholds | Statistical classification, not a forecast |
| Sector performance | mean/median 20D return by sector | Metadata coverage matters |

## 3. Fundamental ratios

| Model | Formula | Required inputs |
|---|---|---|
| Gross margin | gross profit / revenue | gross profit, revenue |
| Operating margin | operating income / revenue | EBIT, revenue |
| Net margin | net income / revenue | net income, revenue |
| ROE | net income / average equity | net income, current + prior equity |
| ROA | net income / average assets | net income, current + prior assets |
| ROIC | NOPAT / invested capital | EBIT, tax rate, debt, equity, cash |
| Growth | `(value_t - value_{t-1}) / abs(value_{t-1})` | current + prior period |
| Debt/equity | total debt / total equity | debt, equity |
| Net debt | total debt - cash | debt, cash |
| Net debt/EBITDA | net debt / EBITDA | net debt, EBITDA |
| Interest coverage | EBIT / interest expense | EBIT, interest |
| Cash conversion | CFO / net income | CFO, net income |
| P/E | price / EPS | price, EPS |
| P/B | price / BVPS | price, equity, shares |
| EV/EBITDA | (market cap + net debt) / EBITDA | price, shares, debt, cash, EBITDA |
| P/S | market cap / revenue | price, shares, revenue |
| FCF yield | FCF / market cap | FCF, market cap |
| Dividend yield | DPS / price | DPS, price |

Missing inputs produce `UNAVAILABLE`, never zero.

## 4. Owner earnings and DCF

```text
Owner Earnings = net income + D&A - maintenance capex - Δworking capital
```

Maintenance capex is an explicit scenario input, not total capex.

```text
FCFF = EBIT * (1 - tax) + D&A - capex - Δworking capital
EV = sum(FCFF_t / (1+WACC)^t) + TV / (1+WACC)^n
TV = FCFF_n * (1+g) / (WACC - g)
Equity = EV - net debt
Intrinsic per share = Equity / diluted shares
```

Assumptions: `WACC > g`, stable terminal growth, valid share count. Terminal
value dominates long horizons. Results are scenario ranges, not certainties.

```text
Ke = Rf + beta * ERP
WACC = E/(D+E)*Ke + D/(D+E)*Kd*(1-tax)
Margin of Safety = (Intrinsic - Price) / Intrinsic
```

## 5. Portfolio performance

```text
TWR = product(1 + subperiod_return) - 1
subperiod_return = end / (start + start-of-day external flow) - 1
0 = sum(CF_t / (1+MWR)^(days/365))
```

TWR removes external cash-flow timing; MWR/XIRR reflects actual investor flows.
FIFO is the documented cost-basis policy.

## 6. Mean-variance and risk

```text
E(Rp) = w' mu
sigma_p^2 = w' Sigma w
Sharpe = (E(Rp) - Rf) / sigma_p
Sortino = (E(Rp) - Rf) / downside deviation
Max drawdown = min(cumulative / running peak - 1)
Calmar = annualized return / abs(max drawdown)
```

Historical mean and covariance are estimates. Optimization is sensitive to
input errors.

## 7. CAPM and risk parity

```text
E(Ri) = Rf + beta_i * (E(Rm) - Rf)
beta_i = Cov(Ri, Rm) / Var(Rm)
RC_i = w_i * (Sigma w)_i / (w' Sigma w)
```

Risk parity equalizes variance contributions, not expected returns.

## 8. Monte Carlo

Historical block bootstrap: resample contiguous blocks of actual returns.

```text
P(t+1) = P(t) * exp((mu - 0.5 sigma^2) + sigma Z)
```

Outputs: terminal percentiles, probability of loss, target probability, and
drawdown percentiles. Seeds are deterministic. Simulations are never
predictions.

## 9. VaR and CVaR

```text
Historical VaR = -quantile(returns, 1 - confidence)
CVaR = -mean(losses beyond VaR)
Parametric VaR = -(mean + z * std)   [normal assumption]
```

Always state confidence and horizon. VaR is not a worst-case bound.

## 10. Factor and stress analysis

```text
asset_return = intercept + sum(beta_factor * factor_return) + error
portfolio_impact = sum(weight_i * shock_i)
```

Factor exposures require actual factor data. Stress shocks are explicit inputs;
unspecified assets get zero shock by convention.

## 11. Capital flow

```text
net_value = buy_value - sell_value
gross_value = buy_value + sell_value
net_to_gross = net_value / gross_value
```

Net flow is an analytical aggregation, not proof of investor identity. Broker
codes are intermediaries, not beneficial owners.

## 12. Macro context

Cross-asset returns are computed from native observations. Correlation is
context, not causation. No FX conversion or commodity proxy is invented.
