# FinSentiMind — Safe Dataset Refresh & Publishing (TASK 107)

Dokumen ini menggantikan workflow lama yang menulis langsung ke
`ml/data/*.parquet`. Refresh produksi sekarang memakai staging, validasi penuh,
dan publikasi generation berversi.

## 1. Prinsip

- Dataset aktif tidak disentuh selama refresh.
- Semua artefak ditulis ke `ml/data/staging/<run_id>/`.
- Publikasi hanya terjadi jika semua stage wajib berhasil dan validator TASK 106
  tidak menghasilkan FAIL.
- Generation lama tetap ada dan bisa dijadikan target rollback.
- `refreshed_at` (kapan file/generation dibuat) berbeda dari `data_as_of`
  (tanggal data pasar terakhir).
- Tidak ada data OHLCV, sentimen, atau prediksi yang difabrikasi.

## 2. Arsitektur Pipeline yang Dipakai

Sumber data eksternal tetap sama:

- **Supabase** untuk `harga_saham` dan `berita_saham`.
- **yfinance** melalui backfill untuk memperbarui harga.
- **RSS / Telegram / Stockbit** untuk berita (opsional bila tidak dikonfigurasi).
- Sentiment service lokal untuk memberi label/skor berita.

Stage TASK 107 memakai modul yang sudah ada:

| # | Stage | Modul / fungsi |
|---|---|---|
| 1 | Price ingestion | `run_backfill()` |
| 2 | Price normalization | `load_prices_clean()` |
| 3 | Quality gate | `build_report()` + `save_report()` |
| 4 | Trading calendar | `derive_trading_calendar()` + `save_calendar()` |
| 5 | News ingestion | `run_news_pipeline(strict=True)` |
| 6 | Sentiment processing | `run_sentiment_analysis(strict=True)` |
| 7 | Daily sentiment | `build_daily_sentiment_dataset(calendar_path=...)` |
| 8 | Base dataset | `run_build()` |
| 9 | Quant features | `build_feature_dataset()` (optional artifact) |
| 10 | Full validation | `run_all_checks()` TASK 106 |
| 11 | Publication | `publish_generation()` |
| 12 | Report | `refresh_<run_id>.json` |

Tidak ada provider pasar baru dan tidak ada duplikasi logika cleaning,
quality gate, kalender, sentimen, atau join base dataset.

## 3. Layout Dataset

```text
ml/data/
  active.json                         # pointer aktif, ditulis atomik
  refresh.lock                        # lock single-instance (ignored)
  generations/
    <run_id>/
      prices_clean.parquet
      daily_sentiment.parquet
      trading_calendar.parquet
      base_dataset.parquet
      quant_features.parquet          # optional derived artifact
      generation.json                 # checksum + metadata
  staging/
    <run_id>/                         # sisa run gagal / dry-run
  *.parquet                           # fallback legacy (jika belum ada manifest)
```

`active.json` berisi `run_id`, `generation`, `published_at`, `refreshed_at`,
`data_as_of`, dan checksum SHA-256 setiap artefak.

### Kenapa generation + pointer?

- Satu generation bersifat immutable setelah publish; reader tidak melihat
  campuran file dari dua refresh.
- Pergantian aktif hanya satu operasi `os.replace` pada `active.json`.
- **Multi-file atomicity tidak diklaim.** Yang atomik adalah switch pointer.
  Kalau proses mati sebelum pointer berubah, generation baru menjadi orphan dan
  generation lama tetap aktif.

## 4. Menjalankan Refresh

### Full refresh + publish

```powershell
python -m ml.preprocessing.refresh_pipeline
```

Status sukses: `SUCCESS`, exit code `0`.

### Dry-run (tanpa menulis provider, tanpa publish)

```powershell
python -m ml.preprocessing.refresh_pipeline --dry-run
```

Stage ingestion ditandai `skipped`; derived chain tetap dibangun dari kondisi
Supabase saat ini ke staging. Tidak ada perubahan `active.json`.

### Validation-only

```powershell
python -m ml.preprocessing.refresh_pipeline --validate-only
```

Tidak menjalankan refresh. Hanya memvalidasi dataset aktif dengan TASK 106.
Exit `0` jika struktur PASS; `1` jika FAIL.

### Opsi lain

```powershell
python -m ml.preprocessing.refresh_pipeline --data-root ml/data --reports-dir ml/reports
python -m ml.preprocessing.refresh_pipeline --run-id manual_20261114 --retries 2 --retry-backoff 2
python -m ml.preprocessing.refresh_pipeline --freshness-threshold-days 7
python -m ml.preprocessing.refresh_pipeline --lock-timeout 5
```

### Melihat generation

```powershell
python -m ml.preprocessing.refresh_pipeline --list-generations
```

### Rollback

```powershell
python -m ml.preprocessing.refresh_pipeline --rollback <run_id>
```

Rollback memverifikasi file wajib dan checksum generation target sebelum
mengubah `active.json`. File generation itu sendiri tidak diubah.

## 5. Exit Codes

| Code | Status | Arti |
|---|---|---|
| 0 | SUCCESS / DRY_RUN / VALIDATION_ONLY | Workflow selesai sesuai mode |
| 1 | FAILED | Stage/validasi gagal; dataset aktif tidak berubah |
| 2 | CONFIG_ERROR | Konfigurasi/run_id/staging tidak valid |
| 3 | LOCKED | Refresh lain masih berjalan |
| 4 | FAILED | Publikasi gagal; dataset aktif tetap generation sebelumnya |

## 6. Perilaku Saat Stage Gagal

Jika salah satu stage gagal:

1. Stage dicatat sebagai `failed` beserta pesan error dan attempt count.
2. Stage berikutnya dicatat `skipped`.
3. Publikasi tidak dijalankan.
4. `active.json` dan generation sebelumnya tidak disentuh.
5. `refresh_<run_id>.json` tetap ditulis ke `ml/reports/`.
6. `staging/<run_id>/` dipertahankan untuk inspeksi.

Contoh kegagalan:

- Backfill punya ticker error atau ticker kosong → `price_ingestion` FAILED.
- Provider berita error → `news_ingestion` FAILED (strict mode).
- Sebagian artikel gagal dianalisis → `sentiment_processing` FAILED.
- Quality gate menemukan duplikat/OHLC tidak valid → FAILED.
- Base dataset kosong → FAILED.
- Validator TASK 106 menghasilkan FAIL → FAILED.
- `os.replace`/pointer gagal → publication FAILED; generation lama aktif.

Freshness WARN dari validator **tidak** memblokir publikasi. WARN itu tetap
dilaporkan sebagai `age_days` dan `latest_trading_date`; data lama tidak
dianggap baru hanya karena file ditulis ulang.

## 6a. Berita Masa Depan dan Quarantine

Setiap artikel mendapat salah satu status eksplisit:

- `MAPPED` — masuk ke sesi terverifikasi.
- `PENDING_FUTURE_SESSION` — sesi berikutnya belum ada di calendar; artikel
  disimpan di `ml/reports/pending_news_<run_id>.json` dan tidak dipetakan ke
  sesi sebelumnya.
- `QUARANTINED_UNRESOLVABLE` — timestamp rusak atau tidak ada sesi
  terverifikasi dalam 30 hari; alasan dicatat eksplisit.

Aturan:

- Tidak ada pembuatan sesi dari hari kerja generik.
- Tidak ada drop artikel secara diam-diam.
- Pending tetap dapat diproses ulang saat calendar bertambah.
- Jika semua artikel pending dan tidak ada quarantine, `daily_sentiment`
  kosong dengan schema valid boleh dihasilkan dan dicatat sebagai
  `sentiment_empty_reason=all_articles_pending_future_session`.
- Quarantine tanpa artikel mapped tetap memblokir stage.

Laporan pending menyertakan `article_id`, ticker, `published_at`,
`scraped_at`, `effective_available_at`, status, reason, dan
`reprocessing_eligible`.

## 7. Metadata Refresh

Lokasi:

```text
ml/reports/refresh_<run_id>.json
ml/reports/refresh_latest.json        # run terakhir, termasuk gagal
ml/reports/refresh_last_success.json  # hanya run SUCCESS
ml/reports/quality_gate_<run_id>.md
ml/reports/validation_<run_id>.md
ml/reports/pending_news_<run_id>.json
```

Field penting:

- `run_id`
- `started_at`, `completed_at`
- `status`
- `refreshed_at` — waktu generation dipublikasikan; `null` jika gagal/dry-run
- `data_as_of` — tanggal data pasar terakhir di base dataset
- `stage_statuses` — status, attempt, durasi, error per stage
- `price_rows`, `sentiment_rows`, `base_dataset_rows`, `ticker_count`
- `latest_market_data_date`, `latest_sentiment_date`
- `validation` — ringkasan PASS/WARN/INFO/FAIL dan freshness
- `failure_reason`
- `generation`, `previous_generation`
- `published`

Jangan memakai `refreshed_at` sebagai tanggal pasar. Data dengan
`data_as_of = 2026-10-09` tetap data 9 Oktober walaupun di-refresh pada 14
November.

## 8. Reader dan Kompatibilitas Frontend

Pembaca yang sudah diperbarui:

- `app/services/market_data.py`
- `ml/validation/validate_dataset.py` (default CLI)
- `ml/preprocessing/quality_gate.py` (default input)
- `ml/preprocessing/trading_calendar.py` (default aktif)
- `ml/preprocessing/build_base_dataset.py` (default input)
- `ml/preprocessing/daily_sentiment.py` (calendar path eksplisit dari staging)

Reader memakai `active.json` jika valid. Jika belum ada manifest atau manifest
rusak, reader kembali ke file flat `ml/data/*.parquet` agar kompatibel dengan
dataset lama.

API FastAPI tetap read-only dan kontrak endpoint tidak berubah. Setelah refresh
baru, restart proses uvicorn agar `MarketDataRepository` memuat generation baru
(atau panggil `reload()` dari kode). Data Health akan menampilkan file generation
aktif dan freshness aktual.

## 9. Lock, Retry, dan Interupsi

- **Lock**: `ml/data/refresh.lock`, file lock OS + guard thread. Proses kedua
  mendapat exit code `3`.
- **Retry**: hanya stage ingestion yang retryable; jumlah dibatasi `--retries`
  (default 1 retry) dan backoff terbatas.
- **Interupsi**: staging boleh tertinggal; generation aktif tidak berubah.
  Jalankan refresh baru dengan `run_id` baru setelah inspeksi.
- Tidak ada retry tanpa batas.
- Lock otomatis dilepas oleh OS saat proses berhenti.

## 10. Recovery dan Inspeksi

1. Baca `ml/reports/refresh_latest.json` atau report run tertentu.
2. Periksa `stage_statuses` dan `failure_reason`.
3. Periksa `ml/data/staging/<run_id>/` bila run gagal.
4. Lihat generation dengan `--list-generations`.
5. Jika generation lama masih valid dan ingin diaktifkan:

```powershell
python -m ml.preprocessing.refresh_pipeline --rollback <run_id>
```

6. Jika `active.json` rusak, reader otomatis fallback ke file legacy; perbaiki
   pointer secara manual hanya setelah memverifikasi generation.

## 11. Batasan Provider Eksternal

- yfinance dapat rate-limit, mengembalikan data kosong, atau berubah format.
- Supabase membutuhkan `SUPABASE_URL` dan `SUPABASE_KEY`; tanpa kredensial stage
  ingestion gagal dengan pesan jelas.
- Telegram/Stockbit opsional; jika tidak dikonfigurasi, tidak dianggap gagal.
  Error fetch/insert RSS, Telegram, atau Stockbit yang benar-benar terjadi akan
  dipropagasikan oleh strict mode sehingga publikasi diblokir.
- Tidak ada data pengganti ketika provider gagal.
- Hari libur atau akhir pekan dapat menghasilkan `data_as_of` yang sama; ini
  bukan kegagalan dan tidak boleh diubah menjadi data baru.

## 12. Testing

```powershell
python -m pytest -q
```

Test TASK 107 mencakup:

- refresh sukses dan publikasi generation,
- kegagalan price/news/sentiment/base/validation/publication,
- dataset aktif tidak berubah saat gagal,
- rollback ke generation terverifikasi,
- idempotensi dan uniqueness,
- concurrent refresh ditolak,
- metadata `refreshed_at` vs `data_as_of`,
- kredensial/provider error,
- strict mode untuk job berita dan sentimen,
- test store generation dan resolver API.

Legacy `ml.preprocessing.refresh_all` tetap ada untuk kompatibilitas, tetapi
**bukan** workflow publishing yang aman. Gunakan `refresh_pipeline`.
