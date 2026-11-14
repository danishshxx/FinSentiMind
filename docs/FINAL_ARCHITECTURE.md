# FinSentiMind — Final Architecture (TASK 107–111)

## 1. Gambaran Sistem

FinSentiMind adalah terminal riset ekuitas IDX read-only berbasis:

- FastAPI untuk API,
- HTML/CSS/Vanilla JavaScript untuk frontend,
- SVG chart tanpa library eksternal,
- dataset Parquet lokal,
- generation/versioned publishing,
- scikit-learn untuk riset ML.

Tidak ada broker integration, live trading, atau data pasar real-time.

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
  7 daily sentiment
  8 base dataset
  9 quant features (optional artifact)
 10 TASK 106 validation
 11 generation publication
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

## 3. Safe Publishing

- Staging: `ml/data/staging/<run_id>/`.
- Generation immutable: `ml/data/generations/<run_id>/`.
- Publikasi hanya setelah semua stage dan validasi TASK 106 lulus.
- `active.json` diganti dengan `os.replace` atomik.
- Generation lama tetap ada.
- Checksum SHA-256 disimpan di manifest dan diverifikasi reader.
- Multi-file atomicity tidak diklaim; switch pointer yang atomik.

## 4. Modul Utama

| Modul | Fungsi |
|---|---|
| `ml/preprocessing/refresh_pipeline.py` | Orkestrasi refresh aman |
| `ml/preprocessing/dataset_store.py` | Generation, pointer, rollback, checksum |
| `ml/preprocessing/refresh_lock.py` | Single-instance lock |
| `ml/preprocessing/load_prices.py` | Cleaning harga |
| `ml/preprocessing/quality_gate.py` | Quality gate harga |
| `ml/preprocessing/trading_calendar.py` | Calendar dari coverage |
| `ml/preprocessing/daily_sentiment.py` | Agregasi sentimen harian |
| `ml/preprocessing/build_base_dataset.py` | Join price + sentiment |
| `ml/validation/validate_dataset.py` | Validator independen TASK 106 |
| `ml/metadata/master.py` | Instrument master auditabel |
| `ml/features/quant.py` | Fitur kuantitatif v1 |
| `ml/market/intelligence.py` | Breadth, regime, sector, macro adapter |
| `ml/research/ml_pipeline.py` | Target, walk-forward, baseline, evaluation |
| `app/services/market_data.py` | Reader API read-only |
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

Instrument master fields:

```text
ticker, exchange, company_name, sector, subsector, listing_status,
listing_date, previous_tickers, successor_ticker, provenance
```

Missing metadata and sentimen tetap `NULL`.

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
| `/api/data/health` | Validator TASK 106 |

Tidak ada endpoint tulis data pasar.

## 7. Frontend

- Market Overview
- Market Monitor + market intelligence + macro status
- Stock Explorer + sector
- Stock Detail + instrument metadata + quant features
- Watchlist localStorage
- Data Health
- Ctrl+K command palette
- Stale data banner

Frontend tidak menampilkan prediksi ML sebagai sinyal tepercaya karena model
berstatus `NOT_VALIDATED`.

## 8. Integritas Data

- Tidak ada OHLCV atau sentimen yang difabrikasi.
- Tidak ada NULL-to-zero.
- Target ML memakai next open dan future close hanya untuk label, bukan fitur.
- Feature window hanya memakai data sampai t.
- Walk-forward + embargo 5 sesi.
- Makro default unavailable; tidak ada dummy.
- Macro provider hanya jika owner mengaktifkan dan lisensi disetujui.

## 9. Riset ML

Target:

```text
entry = open(t+1)
exit  = close(t+5)
target_return_5d = close(t+5) / open(t+1) - 1
```

Model: DummyClassifier dan LogisticRegression.

Status saat ini: **NOT_VALIDATED**.

- ROC-AUC 0.5208 vs dummy 0.5000.
- Strategy Sharpe -0.887 vs universe -1.045.
- Cumulative strategy return -32.60%.

Model tetap disimpan sebagai artefak riset dan tidak dipakai sebagai sinyal.

## 10. Batasan dan Risiko

- Dataset historis berhenti di 2026-10-09.
- Sentimen sangat sparse (8 baris).
- Macro belum tersedia secara resmi.
- Metadata company/subsector/listing date parsial.
- Full provider refresh belum dieksekusi pada sprint ini.
- Model ML belum tervalidasi dan tidak layak dianggap trading strategy.
- Belum ada deployment security, autentikasi, lisensi data, atau operational SLA.
- Belum production-ready.

## 11. Verifikasi

- 398 unit/integration tests.
- Full pytest pass.
- Real dry-run refresh: derived stages ok, quant stage ok, validation PASS 35/2/0,
  no publication.
- Browser verification: desktop, tablet, mobile; screenshot final acceptance.
- No browser console errors.
- Security scan: no `.env`, credentials, or Parquet staged.
