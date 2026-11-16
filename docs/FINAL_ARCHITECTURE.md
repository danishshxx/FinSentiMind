# FinSentiMind — Final Architecture (TASK 107–121)

## 1. Gambaran Sistem

FinSentiMind adalah terminal riset ekuitas IDX read-only berbasis:

- FastAPI untuk API lokal,
- HTML/CSS/Vanilla JavaScript untuk frontend,
- SVG chart tanpa framework eksternal,
- dataset Parquet lokal,
- generation/versioned publishing,
- scikit-learn, NumPy, SciPy untuk riset dan risiko.

Tidak ada broker integration, live trading, custody, atau data pasar real-time.

## 2. Alur Data

```text
Supabase harga_saham / berita_saham
        |
        +-- yfinance backfill (harga)
        +-- RSS / Telegram / Stockbit (berita)
        +-- sentiment service lokal
        |
        v
refresh_pipeline.py
  1 price ingestion
  2 price cleaning
  3 quality gate
  4 trading calendar
  5 news ingestion
  6 sentiment processing
  7 daily sentiment (mapped/pending/quarantine)
  8 base dataset
  9 quant features (optional artifact)
 10 validation
 11 generation publication (approval-gated)
 12 refresh report
        |
        v
ml/data/generations/<run_id>/
  prices_clean.parquet
  daily_sentiment.parquet
  trading_calendar.parquet
  base_dataset.parquet
  quant_features.parquet (optional)
  generation.json
        |
        v
ml/data/active.json (atomic pointer)
        |
        +--> MarketDataRepository
        +--> validator / quality gate defaults
        +--> Market Monitor / Data Health
```

Manual research imports feed separate read-only repositories:

```text
verified CSV/JSON -> ml/fundamentals -> FundamentalRepository
verified CSV/JSON -> ml/capital_flow -> CapitalFlowRepository
verified CSV/JSON -> ml/macro      -> MacroRepository
verified CSV/JSON -> ml/portfolio  -> PortfolioRepository
verified CSV/JSON -> ml/workspace  -> ResearchNoteRepository
```

Unavailable data remains `UNAVAILABLE`; it is never replaced with a dummy.

## 3. Safe Publishing

- Staging: `ml/data/staging/<run_id>/`.
- Generation immutable: `ml/data/generations/<run_id>/`.
- Publikasi hanya setelah semua stage dan validasi lulus.
- `active.json` diganti dengan `os.replace` atomik.
- Generation lama tetap ada.
- Checksum SHA-256 disimpan di manifest dan diverifikasi reader.
- Multi-file atomicity tidak diklaim; switch pointer yang atomik.
- Full provider refresh, Supabase write, publication, dan `active.json` change
  memerlukan approval owner.

## 4. Modul Utama

| Modul | Fungsi |
|---|---|
| `ml/preprocessing/refresh_pipeline.py` | Orkestrasi refresh aman |
| `ml/preprocessing/dataset_store.py` | Generation, pointer, rollback, checksum |
| `ml/preprocessing/refresh_lock.py` | Single-instance lock |
| `ml/preprocessing/load_prices.py` | Cleaning harga |
| `ml/preprocessing/quality_gate.py` | Quality gate harga |
| `ml/preprocessing/trading_calendar.py` | Calendar dari coverage |
| `ml/preprocessing/daily_sentiment.py` | Sentimen harian + pending/quarantine |
| `ml/preprocessing/calendar_audit.py` | Audit weekday gap |
| `ml/preprocessing/anomaly_audit.py` | Audit volume change |
| `ml/preprocessing/build_base_dataset.py` | Join price + sentiment |
| `ml/validation/validate_dataset.py` | Validator independen |
| `ml/metadata/master.py` | Instrument master auditabel |
| `ml/features/quant.py` | Fitur kuantitatif |
| `ml/market/intelligence.py` | Breadth, regime, sector, macro adapter |
| `ml/research/ml_pipeline.py` | Walk-forward ML research |
| `ml/fundamentals/schemas.py` | Schema statement/action/dividend |
| `ml/fundamentals/ingestion.py` | Import manual + provenance |
| `ml/fundamentals/normalization.py` | Unit scale normalization |
| `ml/fundamentals/repository.py` | Point-in-time repository |
| `ml/fundamentals/quality.py` | Fundamental quality checks |
| `ml/fundamentals/metrics.py` | Rasio dan growth |
| `ml/fundamentals/valuation.py` | Owner earnings, DCF, MoS |
| `ml/fundamentals/analysis.py` | Orkestrasi analisis ticker |
| `ml/capital_flow/*` | Flow schema, import, provider, analisis |
| `ml/portfolio/*` | FIFO ledger, TWR/XIRR |
| `ml/risk/engine.py` | Markowitz, CAPM, risk parity, MC, VaR |
| `ml/macro/*` | Macro history, provider, cross-asset |
| `ml/workspace/notes.py` | Thesis/catalyst/risk notes |
| `ml/ops/backup.py` | Backup checksummed |
| `app/services/market_data.py` | Reader API read-only |
| `app/services/research_suite.py` | Orkestrasi endpoint riset |
| `frontend/*` | Terminal UI |

## 5. Data Contracts

Base dataset (14 kolom):

```text
ticker, trading_date, open, high, low, close, volume, return_1d,
news_count, sentiment_mean, sentiment_std, positive_ratio,
negative_ratio, availability_live_ratio
```

Quant features:

```text
return_1d, return_5d, return_20d, log_return_1d,
sma_20, sma_50, sma_200, ema_20, ema_50,
rsi_14, momentum_5, momentum_20,
volatility_20, atr_14,
volume_sma_20, relative_volume_20, volume_change_1d
```

Daily sentiment outcomes:

```text
MAPPED
PENDING_FUTURE_SESSION
QUARANTINED_UNRESOLVABLE
```

Fundamental statements are long-form with `value`, `original_value`,
`unit_scale`, `currency`, `published_at`, `available_at`, `source_id`,
`source_document`, `revision`, and `restatement_of`.

## 6. API Read-Only

| Endpoint | Fungsi |
|---|---|
| `/api/health` | Liveness + dataset availability |
| `/api/meta` | Provenance dan ukuran dataset |
| `/api/market/overview` | Snapshot sesi terakhir |
| `/api/market/intelligence` | Regime, breadth, sector, macro |
| `/api/stocks` | Explorer + metadata |
| `/api/stocks/{ticker}` | Detail + quant features + instrument |
| `/api/stocks/{ticker}/ohlcv` | OHLCV historis |
| `/api/instruments` | Instrument master |
| `/api/instruments/{ticker}` | Satu instrumen |
| `/api/research/latest` | Report riset ML (research-only) |
| `/api/research/notes/{ticker}` | Catatan riset owner |
| `/api/data/health` | Validator TASK 106 |
| `/api/capabilities` | Status modul dan provider |
| `/api/fundamentals/{ticker}` | Metrik + optional DCF |
| `/api/capital-flow/market` | Flow pasar |
| `/api/capital-flow/{ticker}` | Flow saham |
| `/api/portfolio` | Daftar portfolio |
| `/api/portfolio/{id}` | Holdings + TWR/XIRR |
| `/api/risk/lab` | Optimasi, risiko, Monte Carlo |
| `/api/macro` | Cross-asset context |

Semua endpoint read-only; tidak ada write endpoint untuk holdings, notes,
fundamentals, atau order.

## 7. Frontend

- Market Overview
- Market Monitor + market intelligence
- Stock Explorer + sector
- Company Research + metrics + valuation + notes
- Capital Flow
- Portfolio (personal/model)
- Quant Risk Lab
- Macro Context
- Watchlist localStorage
- Research Reports
- Data Health
- Stock Detail drawer + quant features + metadata
- Ctrl+K command palette
- Stale data banner

ML research tetap `NOT_VALIDATED`; tidak ditampilkan sebagai sinyal tepercaya.

## 8. Integritas Data

- Tidak ada OHLCV, sentimen, fundamental, flow, macro, atau portofolio yang
  difabrikasi.
- Tidak ada NULL-to-zero.
- Berita masa depan tidak dipetakan ke sesi sebelumnya dan tidak membuat sesi
  palsu.
- Feature window hanya memakai data sampai t.
- Walk-forward + embargo 5 sesi.
- Point-in-time `available_at` untuk fundamental, flow, macro, dan notes.
- Unit scale dinormalisasi; currency conversion tidak diciptakan.
- Full refresh dan publication tetap approval-gated.

## 9. Riset ML

Target:

```text
entry = open(t+1)
exit  = close(t+5)
target_return_5d = close(t+5) / open(t+1) - 1
```

Model: DummyClassifier dan LogisticRegression.

Status: **NOT_VALIDATED**.

- ROC-AUC 0.5208 vs dummy 0.5000.
- Strategy Sharpe -0.887 vs universe -1.045.
- Cumulative strategy return -32.60%.

Model disimpan sebagai artefak riset, bukan sinyal trading.

## 10. Model Keuangan

Formula lengkap ada di `docs/FINANCIAL_MODELS_CATALOG.md`, meliputi:

- returns, SMA/EMA, RSI, ATR, volatility, relative volume;
- rasio fundamental dan growth;
- owner earnings, FCFF/DCF, WACC, margin of safety;
- TWR/XIRR;
- Markowitz, CAPM, risk parity;
- Monte Carlo, VaR/CVaR, drawdown, Sharpe/Sortino/Calmar;
- stress test dan factor exposure;
- capital-flow aggregation;
- cross-asset correlation.

## 11. Keamanan dan Operasi

- `.env` ignored; secrets tidak masuk laporan/log.
- Server default `127.0.0.1`; tidak ada public auth/multi-user.
- CORS tidak diaktifkan; akses same-origin.
- Security headers diterapkan.
- Error API generik; detail ada di server log.
- Validasi input ticker/portfolio/risk.
- Audit log opsional `FINSENTIMIND_AUDIT_LOG=1`.
- Backup checksummed: `python -m ml.ops.backup`.
- `pip check` bersih; `pip-audit` belum terpasang.

## 12. Batasan dan Risiko

- Dataset historis berhenti di 2026-10-09.
- Sentimen sparse (8 baris legacy).
- Tidak ada provider fundamental/flow/macro otomatis yang berlisensi.
- Coal, nickel, dan CPO tetap `UNAVAILABLE`.
- Metadata company/subsector/listing date parsial.
- Full provider refresh belum dieksekusi; publication pending.
- ML belum tervalidasi.
- Belum ada deployment security, autentikasi, lisensi data, atau SLA.
- Public launch **NOT APPROVED**; live trading **NOT IMPLEMENTED**.
- Mapping hari libur calendar adalah curated dan harus diverifikasi ke decree
  IDX tahunan.

## 13. Verifikasi

- **501 unit/integration tests**, 5 pre-existing Pydantic warnings.
- `node --check frontend/app.js`: pass.
- Browser acceptance: desktop, tablet, mobile; command palette; semua view;
  tidak ada console error; tidak ada horizontal overflow.
- Local dry-run refresh: derived stages ok, quant stage ok, validation
  PASS 36 / WARN 1 / FAIL 0, no publication.
- Security scan: no `.env`, credentials, or Parquet staged.
- Screenshots: `docs/screenshots/final-product-acceptance/`.

## 14. Penyelesaian Platform Data (2026-11-16)

Bagian ini memperbarui arsitektur data setelah remediasi kalender P0.

### Generasi aktif

- `active.json` menunjuk ke `generations/idx30_calendar_v2_20261116`.
- Kalender: `IDX-2024-2026-v2`; scope: IDX30 (30 ticker).
- Rentang: 2024-10-07 → 2026-11-13; 15.018 baris; 502 sesi.
- Checksum SHA-256 per artefak diverifikasi oleh reader.
- Data Health: PASS 37 / WARN 1 / FAIL 0.

### Lapisan baru

- `ml/validation/generation_audit.py`: audit penerimaan generasi dan provenance.
- `ml/ops/publish_dataset.py`: staging + publikasi atomik lewat `dataset_store`.
- `ml/ops/revalidate_downstream.py`: rekonsiliasi quant, market, dan subsistem.
- `ml/universe/`: inventaris instrumen, ingestion EOD yfinance inkremental,
  matriks cakupan, dan orkestrasi refresh universe.

### Status hilir

- Quant features: MATCH dengan hasil hitung ulang.
- Market intelligence: OK (indeks ekuivalen-berbobot, bukan IHSG).
- Fundamental, capital flow, macro, portfolio: UNAVAILABLE (manual/belum ada).
- ML research: NOT_VALIDATED (ROC-AUC 0.5543; Sharpe strategi -1.4099).

### Ekspansi universe

- Inventaris terdokumentasi: 45 ticker (IDX30 + LQ45).
- Daftar resmi seluruh IDX: **terblokir HTTP 403**; `complete=false`.
- yfinance EOD: 45/45 berhasil, 22.593 baris s.d. 2026-11-16.
- Re-run inkremental: 0 baris baru (idempotent).
- Universe lebih luas **belum dipublikasikan** sebagai generasi.

### Verifikasi

- Full test suite: **544 passed**, 5 warning Pydantic lama.
- `node --check frontend/app.js`: pass.
- Tidak ada write Supabase, publikasi eksternal, atau merge ke `main`.
