# FinSentiMind

Personal, read-only equity research and portfolio intelligence workstation for
the Indonesia Stock Exchange (IDX).

FinSentiMind combines a local market terminal, fundamental analysis and DCF,
capital-flow context, portfolio accounting, quantitative risk analytics, macro
cross-asset context, and research notes.

## Status

| Area | Status |
|---|---|
| Personal research | Ready |
| Market monitoring | Ready (local snapshot) |
| Portfolio monitoring | Ready (manual ledger) |
| Fundamental research | Ready where verified data exists |
| Quantitative risk | Ready where input quality supports it |
| Public launch | **NOT APPROVED** |
| Live trading | **NOT IMPLEMENTED** |
| ML research | **NOT VALIDATED** / `not_for_trading=true` |

There is no broker integration, order routing, custody, or automated trading.

## Quick start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The API docs are at `/docs`.

The server binds to localhost by default. Do not expose it publicly without
authentication, TLS, rate limiting, and a security review.

## Main capabilities

- Market Overview, Market Monitor, Stock Explorer, Stock Detail, Watchlist.
- Company Research: fundamental ratios, owner earnings, DCF scenarios, margin
  of safety, research notes.
- Capital Flow: market/stock scope, broker concentration, persistence,
  price-flow divergence.
- Portfolio: personal and model ledgers, FIFO cost basis, TWR and XIRR.
- Quant Risk Lab: Markowitz, CAPM, risk parity, Monte Carlo, VaR/CVaR, stress,
  factor exposure.
- Macro: cross-asset context with native units, unavailable states, and
  correlation-not-causation warning.
- Data Health, command palette, desktop/tablet/mobile layouts.

## Data and safety

- Local Parquet datasets; no live market feed.
- Immutable refresh generations with SHA-256 manifests and atomic
  `active.json`; legacy flat-file fallback.
- Future news becomes `PENDING_FUTURE_SESSION`, never mapped backwards.
- Missing data stays NULL/UNAVAILABLE; no nullptr-to-zero or fabricated values.
- Full provider refresh, Supabase writes, and generation publication require
  explicit owner approval.
- Manual imports require verified source provenance.

## Tests

```powershell
python -m pytest -q
node --check frontend/app.js
```

Current baseline: **501 passed**, 5 pre-existing Pydantic warnings.

## Documentation

- `docs/PROJECT_HANDOFF.md`
- `docs/FINAL_ARCHITECTURE.md`
- `docs/FINAL_PRODUCT_ACCEPTANCE.md`
- `docs/MVP_RUNBOOK.md`
- `docs/refresh-workflow.md`
- `docs/FINANCIAL_MODELS_CATALOG.md`
- `docs/FUNDAMENTAL_VALUATION.md`
- `docs/CAPITAL_FLOW.md`
- `docs/PORTFOLIO.md`
- `docs/RISK_LAB.md`
- `docs/MACRO.md`
- `docs/SECURITY_AND_OPERATIONS.md`
- `docs/PUBLIC_READINESS.md`
- `docs/PRODUCT_VISION.md`
- `docs/PRODUCT_ROADMAP.md`
- `docs/DATA_QUALITY_AUDIT.md`
- `docs/REFRESH_ACCEPTANCE.md`

Screenshots: `docs/screenshots/final-product-acceptance/`.

## Disclaimer

This is an educational and analytical research tool, not investment advice.
Intrinsic value is assumption-dependent, simulations are not predictions, and
ML research has not been validated.
