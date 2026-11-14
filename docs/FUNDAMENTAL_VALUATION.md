# Fundamental Analysis and Valuation

Status: **ENGINE IMPLEMENTED — DATA COVERAGE DEPENDS ON VERIFIED MANUAL IMPORTS**

## Ratio formulas

| Metric | Formula | Required inputs |
|---|---|---|
| Gross margin | gross_profit / revenue | gross profit, revenue |
| Operating margin | operating_income / revenue | operating income, revenue |
| Net margin | net_income / revenue | net income, revenue |
| ROE | net_income / average total equity | net income, equity current/prior |
| ROA | net_income / average total assets | net income, assets current/prior |
| ROIC | NOPAT / invested capital | EBIT, explicit tax rate, debt, equity, cash |
| Revenue growth | (revenue_t - revenue_t-1) / abs(revenue_t-1) | current and prior |
| Earnings growth | (net_income_t - net_income_t-1) / abs(prior) | current and prior |
| EPS growth | (EPS_t - EPS_t-1) / abs(EPS_t-1) | current and prior EPS |
| FCF growth | (FCF_t - FCF_t-1) / abs(FCF_t-1) | current and prior FCF |
| Debt/equity | total_debt / total_equity | debt, equity |
| Net debt | total_debt - cash | debt, cash |
| Net debt/EBITDA | net_debt / EBITDA | net debt, EBITDA |
| Interest coverage | operating_income / interest_expense | EBIT, interest |
| Cash conversion | operating_cash_flow / net_income | CFO, net income |
| P/E | market_price / EPS | price, EPS |
| P/B | market_price / book_value_per_share | price, equity/shares |
| EV/EBITDA | (market_cap + net_debt) / EBITDA | price, shares, debt, cash, EBITDA |
| P/S | market_cap / revenue | price, shares, revenue |
| FCF yield | FCF / market_cap | FCF, market cap |
| Dividend yield | dividend_per_share / market_price | DPS, price |

Every result carries `status`, `formula`, `inputs`, and `limitation`.
Missing inputs produce `UNAVAILABLE`, never zero.

## Owner earnings

```text
Owner Earnings =
Net Income
+ Depreciation and Amortization
- Maintenance Capital Expenditure
- Additional Working Capital Requirements
```

Maintenance capex is an explicit scenario input. It is not assumed equal to
total capex.

## DCF

```text
FCFF = EBIT * (1 - tax rate) + D&A - capex - change in working capital
EV   = sum(FCFF_t / (1+WACC)^t) + TV / (1+WACC)^n
TV   = FCFF_n * (1+g) / (WACC - g)
Equity value = EV - net debt
Intrinsic value per share = equity value / diluted shares
```

Validation:

- `WACC > terminal growth` is mandatory.
- Forecast horizon is limited to 1..30 years.
- Shares outstanding must be positive.

Sensitivity is shown across WACC and terminal-growth inputs.

## WACC

```text
Ke   = risk_free_rate + beta * equity_risk_premium
WACC = E/(D+E) * Ke + D/(D+E) * cost_of_debt * (1 - tax_rate)
```

## Margin of safety

```text
Margin of Safety = (Intrinsic Value - Market Price) / Intrinsic Value
```

Bear/base/bull scenarios are computed separately. The engine never presents a
single intrinsic value as certain.

## Limitations

- No automated fundamental provider is integrated.
- No automatic FX conversion.
- Maintenance capex requires an explicit assumption.
- Terminal value dominates long-horizon DCF output.
- EBITDA is not a cash-flow measure.
- A negative P/E or unstable growth base can make ratios not meaningful.
- The output is educational research, not investment advice.
