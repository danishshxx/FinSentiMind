# FinSentiMind — Data Platform Architecture

Status: **local research platform, publication pending owner approval**

## 1. Layers

```text
provider sources
  Supabase (read-only SELECT)  ->  harga_saham, berita_saham
  yfinance (.JK EOD)           ->  raw + normalized universe prices
  manual imports               ->  fundamentals, capital flow, macro
        |
        v
staging generation (immutable after publication)
  prices_clean.parquet
  daily_sentiment.parquet
  trading_calendar.parquet
  base_dataset.parquet
  quant_features.parquet (optional)
  generation.json / provenance.json
        |
        v
generations/<run_id>/   (immutable)
        |
        v
ml/data/active.json      (atomic pointer, SHA-256 per artifact)
        |
        v
readers: FastAPI services, terminal, research, risk, ML
```

## 2. Generation model

- Every refresh writes into `ml/data/staging/<run_id>/`.
- Validation and the generation acceptance audit must pass before publication.
- `ml.preprocessing.dataset_store.publish_generation` moves the directory into
  `ml/data/generations/<run_id>/` and atomically replaces `active.json`.
- `active.json` records `run_id`, `published_at`, `refreshed_at`, `data_as_of`
  and a SHA-256 checksum for every required and optional artifact.
- Readers resolve `active.json` and verify checksums by default
  (`resolve_active_dataset(..., verify_hashes=True)`).
- A failed or tampered generation is ignored; the previous active generation
  remains in use. `rollback_to_generation` reactivates a verified generation.

## 3. Authoritative exchange calendar

- Source of truth: `ml/preprocessing/idx_exchange_calendar.json`
  (version `IDX-2024-2026-v2`, coverage `2024-10-07`..`2026-12-31`).
- Loader: `ml/preprocessing/idx_calendar.py` (offline, deterministic,
  `@lru_cache`).
- Day status: `WEEKEND`, `OFFICIAL_CLOSURE`, `VERIFIED_OPEN`, `UNVERIFIED`.
- Cross-sectional ticker coverage is descriptive only. It never defines whether
  the exchange was open.
- Rows on official closures are quarantined before returns and features are
  computed. Positive-volume or non-flat closure bars are a blocking
  contradiction, never silently deleted.

## 4. Module map

| Concern | Module |
|---|---|
| Exchange calendar | `ml/preprocessing/idx_calendar.py` |
| Price cleaning + quarantine | `ml/preprocessing/load_prices.py` |
| Calendar derivation | `ml/preprocessing/trading_calendar.py` |
| Quality gate | `ml/preprocessing/quality_gate.py` |
| Sentiment alignment | `ml/preprocessing/daily_sentiment.py` |
| Base dataset | `ml/preprocessing/build_base_dataset.py` |
| Quant features | `ml/features/quant.py` |
| Generation acceptance | `ml/validation/generation_audit.py` |
| Safe publication | `ml/ops/publish_dataset.py` + `dataset_store.py` |
| Downstream revalidation | `ml/ops/revalidate_downstream.py` |
| Universe inventory | `ml/universe/inventory.py` |
| EOD ingestion | `ml/universe/yfinance_eod.py` |
| Coverage matrix | `ml/universe/coverage.py` |
| Universe refresh | `ml/universe/refresh.py` |

## 5. Active generation (current)

| Field | Value |
|---|---|
| Generation | `idx30_calendar_v2_20261116` |
| Calendar version | `IDX-2024-2026-v2` |
| Universe scope | IDX30 (30 tickers) |
| Date range | 2024-10-07 → 2026-11-13 |
| Price rows | 15,018 |
| Sessions | 502 |
| Feature version | `quant_features_v1` |

## 6. Universe expansion

`ml/universe/` maintains a documented inventory (IDX30 + LQ45 = 45 tickers) and
an incremental yfinance ingestion layer. This is **not** the full IDX universe:
the official IDX securities endpoint returned HTTP 403, so completeness is
recorded as `false` in the inventory. The active generation remains scoped to
IDX30 until a validated broader generation is published separately.

## 7. Boundaries

- No Supabase INSERT/UPDATE/UPSERT/DELETE.
- No provider subscription or paid API key.
- No public deployment or redistribution of market data.
- No broker integration or order execution.
- EOD data is never presented as real-time.
