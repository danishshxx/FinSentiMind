# FinSentiMind — Data Platform Acceptance

Status: **local acceptance complete — publication of the IDX30 generation done
locally; broader universe not published**

## 1. P1 — Corrected generation acceptance

Audit: `ml.validation.generation_audit` → **PASS** (0 FAIL, 0 WARN).

| Artifact | Rows | Size (bytes) |
|---|---:|---:|
| prices_clean.parquet | 15,018 | 296,242 |
| daily_sentiment.parquet | 8 | 6,848 |
| trading_calendar.parquet | 768 | 12,381 |
| base_dataset.parquet | 15,018 | 300,803 |
| quant_features.parquet | 15,018 | 1,669,528 |

- Calendar version: **IDX-2024-2026-v2** (single version).
- Date range: 2024-10-07 → 2026-11-13; 502 usable sessions.
- Zero price rows on official closures, weekends, or future sessions.
- Base/prices/quant row and ticker reconciliation: match.
- Unique `(ticker, trading_date)` keys: 0 duplicates.
- Sentiment NULL semantics: pass.
- Provenance written for the published generation (`provenance.json`) with
  source generation, calendar version, feature version, data date, row count and
  SHA-256 per artifact.

The 2024 IDX announcement identifier is not archived; the official IDX calendar
page is cited. This remains a documented limitation, not a fabricated source.

## 2. P2 — Local publication

| Field | Value |
|---|---|
| Generation | `idx30_calendar_v2_20261116` |
| Path | `ml/data/generations/idx30_calendar_v2_20261116/` |
| Active pointer | `ml/data/active.json` |
| Calendar version | IDX-2024-2026-v2 |
| Universe scope | IDX30 (30 tickers) |
| data_as_of | 2026-11-13 |
| Checksum verification | PASS (all artifacts) |
| Reader resolution | `manifest` source, hashes verified |

Data Health against the active generation:

- Structural validity: **PASS**
- PASS 37 / WARN 1 / FAIL 0 (WARN = sparse sentiment coverage)
- Dataset: 15,018 rows, 30 tickers, 502 sessions, 2024-10-07 → 2026-11-13

Rollback verification (isolated temp root using the real artifacts):

- publish `gen-a` → publish `gen-b` → active = `gen-b`
- rollback to `gen-a` → active = `gen-a`, `rolled_back_at` recorded
- tampered `gen-b` → refused; active remains `gen-a`

Failure preservation: publishing over an existing run id raises and leaves
`active.json` byte-identical.

## 3. P3 — Downstream revalidation

| Subsystem | Status | Evidence |
|---|---|---|
| Quant features | **MATCH** | Recomputation equals published artifact for all feature columns |
| Market intelligence | OK | Equal-weight proxy (not IHSG), regime `high_volatility` |
| Fundamentals | UNAVAILABLE | No verified manual dataset installed |
| Capital flow | UNAVAILABLE | No licensed provider; manual import only |
| Macro | UNAVAILABLE | yfinance macro adapters disabled; coal/nickel/CPO have no proxy |
| Portfolio / risk | UNAVAILABLE | No local ledger; ledger is never auto-created |
| ML research | **NOT_VALIDATED** | ROC-AUC 0.5543; strategy Sharpe -1.4099 |

Report: `ml/reports/downstream_revalidation_idx30_calendar_v2_20261116.json`.

## 4. P4 — Universe expansion

| Field | Value |
|---|---|
| Inventory scope | IDXL45_DOCUMENTED (45 documented tickers) |
| Inventory complete | **false** (IDX securities endpoint HTTP 403) |
| EOD ingestion | 45/45 returned, 22,593 rows through 2026-11-16 |
| Coverage matrix | 45 usable, 45 COMPLETE relative to each listing window |
| Incremental re-run | 45 skipped, 0 new rows (idempotent) |
| Universe refresh | SUCCESS (inventory → ingestion → coverage) |
| Active generation touched | false |
| Supabase writes | false |

The 45-instrument universe is a documented project scope, not the full IDX. The
active generation remains IDX30 until a broader generation is separately
validated and published.

Known limitations:

- AADI first appears on 2024-12-05; coverage is measured from each instrument's
  first available date.
- yfinance is not a point-in-time archive and may revise history.
- Full universe enumeration, fundamentals, flow, and macro remain unavailable.

## 5. Verification

- Full test suite: **544 passed**, 5 pre-existing Pydantic warnings.
- `node --check frontend/app.js`: pass.
- `git diff --check`: pass (only CRLF normalization warnings).
- No Parquet, `.env`, or credentials committed.

## 6. Verdict

The corrected IDX30 generation is accepted, published locally, and read by the
application with verified checksums. Broader IDX coverage is implemented as a
provider adapter, inventory, coverage diagnostics and supported subset, but is
**not** complete and is not published as a generation.
