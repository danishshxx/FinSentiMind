# Macro and Cross-Asset Intelligence

Status: **ENGINE IMPLEMENTED — AUTOMATED PROVIDERS UNAVAILABLE/DISABLED**

## Indicators

| Code | Label | Native unit/currency |
|---|---|---|
| USDIDR | USD/IDR | IDR per USD |
| SP500 | S&P 500 | index points / USD |
| DJIA | Dow Jones | index points / USD |
| NASDAQ | Nasdaq | index points / USD |
| HSI | Hang Seng | index points / HKD |
| GOLD | Gold | USD per troy ounce |
| COAL | Coal | UNAVAILABLE |
| NICKEL | Nickel | UNAVAILABLE |
| CPO | CPO | UNAVAILABLE |

Coal, nickel, and CPO intentionally have no speculative symbol. They remain
unavailable until an authorized source is configured.

## Provider status

- Manual verified CSV/JSON import: available if installed.
- yfinance: disabled unless `FINSENTIMIND_ENABLE_MACRO_YFINANCE=1`; use only
  after licensing/redistribution review.
- Licensed IDX/vendor feed: not integrated.

Unavailable providers return `UNAVAILABLE` with a reason. No dummy values are
created.

## Observation schema

`indicator, date, value, unit, currency, published_at, available_at,
source_id, source_document, revision`.

Template: `ml/macro/templates/macro_observations_template.csv`.

Import:

```powershell
python -m ml.macro.ingestion --input macro.csv
```

## Analysis

- Latest value, native unit/currency, freshness.
- 1D/5D/20D returns (unitless).
- Cross-asset correlation matrix from overlapping dates.
- Sector-to-macro correlations when both histories exist.
- Explicit `causation=false` and a correlation-is-not-causation warning.

No FX conversion is invented. Index points, FX rates, and commodity prices stay
in their native units.

## Timezone and point-in-time

Observations are stored with `published_at` and `available_at` in UTC.
Date-level queries use an Asia/Jakarta end-of-day bound. Future observations are
excluded from historical reports.

## Limitations

- No licensed macro provider is integrated.
- Correlations are sample estimates and can be spurious.
- No causal claims or trading signals.
- Partial coverage is normal; unavailable indicators remain visible.
