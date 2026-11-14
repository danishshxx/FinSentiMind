# FinSentiMind — Project Handoff

## Product

FinSentiMind is a personal, read-only equity research and portfolio
intelligence workstation for IDX. It combines a local market terminal,
fundamental valuation, capital-flow context, portfolio accounting, quantitative
risk analytics, and macro context.

**Not implemented / not approved:** live trading, broker routing, custody,
public multi-user deployment, guaranteed investment advice.

## Branch and commits

Current integration branch: `feat/finsentimind-investment-suite`
(still based on the accepted TASK 107A branch).

| Commit | Milestone |
|---|---|
| `a5834db` | TASK 107 audit hardening |
| `cee7f9a` | TASK 108 instrument master |
| `5a0846b` | TASK 109 quant features |
| `5be5600` | TASK 110 market intelligence |
| `dea96bf` | TASK 111 ML research (NOT VALIDATED) |
| `1eb8f21` | Final acceptance evidence / execution plan |
| `be26f01` | TASK 107A pending news + anomaly audits |
| `ecb3582` | TASK 107B local refresh acceptance |
| `45e7d4c` | TASK 113 fundamental data foundation |
| `1c67dac` | TASK 114 fundamental metrics + valuation |
| `f161ce6` | TASK 115 capital flow |
| `570272a` | TASK 116 portfolio ledger |
| `4525b72` | TASK 117 risk lab |
| `8c40220` | TASK 118 macro / cross-asset |
| `256f7c6` | TASK 119 integrated workstation |
| `78f5323` | TASK 120 security / operations |

TASK 107A is on `feat/task-107a-refresh-edge-cases`; its commit is `be26f01`.

## Current dataset

- `data_as_of = 2026-10-09`.
- 30 tickers, 14,405 legacy flat base rows, 481 trading sessions.
- The active reader still uses the legacy flat dataset.
- No published generation and no `ml/data/active.json`.
- Failed refresh staging `20261114T154144Z_acedb3bb/` is preserved.
- TASK 107B dry-run produced a verified staging snapshot as of `2026-11-13`
  but publication remains pending owner approval.

Do not present `refreshed_at` as the market date. The snapshot is historical.

## Architecture summary

### Data layer

- `ml/preprocessing/refresh_pipeline.py`: staged refresh, failure blocking,
  reports, publication gate.
- `ml/preprocessing/dataset_store.py`: immutable generations, SHA-256 manifest,
  atomic `active.json`, rollback, legacy fallback.
- `ml/preprocessing/daily_sentiment.py`: MAPPED / PENDING_FUTURE_SESSION /
  QUARANTINED_UNRESOLVABLE news handling.
- `ml/preprocessing/calendar_audit.py`: non-destructive weekday-gap audit.
- `ml/preprocessing/anomaly_audit.py`: volume-change audit.
- `ml/validation/validate_dataset.py`: independent structural validation.

### Research layer

- `ml/metadata/master.py`: auditable instrument metadata.
- `ml/features/quant.py`: leakage-safe price features.
- `ml/market/intelligence.py`: equal-weight market context, breadth, regime.
- `ml/research/ml_pipeline.py`: walk-forward ML research, `NOT_VALIDATED`.
- `ml/fundamentals/`: manual fundamental imports, point-in-time repository,
  ratios, owner earnings, DCF, scenarios, quality checks.
- `ml/capital_flow/`: manual flow observations, broker/investor aggregation,
  availability states.
- `ml/portfolio/`: FIFO ledger, holdings, TWR/XIRR, personal/model portfolios.
- `ml/risk/`: Markowitz, CAPM, risk parity, Monte Carlo, VaR/CVaR, stress,
  factor exposure.
- `ml/macro/`: manual/disabled macro history, cross-asset summary, correlations.
- `ml/workspace/notes.py`: thesis/risk/catalyst notes with revisions.

### API and UI

- FastAPI read-only API in `app/api/routes.py`.
- `app/services/research_suite.py` composes the read-only research endpoints.
- Vanilla JS terminal in `frontend/` with:
  Market Overview, Market Monitor, Stock Explorer, Company Research, Capital
  Flow, Portfolio, Quant Risk Lab, Macro Context, Watchlist, Research Reports,
  Data Health, command palette, stock detail drawer.
- Existing API contracts are preserved; new endpoints are additive.

## Verification

- Last full suite: **501 passed, 5 pre-existing Pydantic warnings**.
- `node --check frontend/app.js`: pass.
- Browser acceptance: all views load without console errors; mobile and tablet
  have no horizontal overflow.
- Local dry-run refresh: `DRY_RUN`, data as of `2026-11-13`, validation
  PASS 36 / WARN 1 / FAIL 0.
- Research status remains `NOT_VALIDATED`; `not_for_trading=true`.

## Known limitations

- No automated fundamental, capital-flow, or macro provider is licensed. Those
  modules use verified manual imports and explicit `UNAVAILABLE` states.
- No public authentication, multi-user isolation, or production deployment.
- No full provider refresh or generation publication without owner approval.
- Sentiment coverage is sparse; NULL is not neutral.
- Macro commodity proxies (coal, nickel, CPO) remain unavailable.
- ML research underperformed the dummy baseline and is not a trading signal.
- Calendar holiday mapping is curated and must be revalidated against the IDX
  annual decree.
- `pip-audit` is not installed; `pip check` is clean but this is not a CVE scan.

## Recommended next milestone

Owner review of TASK 121 acceptance evidence, followed by a controlled full
refresh/publication only after explicit approval and verified data coverage.
Do not start public deployment or live trading without separate legal,
licensing, security, and operational approval.
