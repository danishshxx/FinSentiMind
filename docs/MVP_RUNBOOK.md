# FinSentiMind MVP 001 — Runbook Terminal Riset Lokal

Dokumen ini menjelaskan cara menjalankan, memakai, dan memverifikasi terminal
FinSentiMind MVP 001. Terminal ini adalah aplikasi **read-only** di atas dataset
riset lokal hasil pipeline TASK 102–106.

Prinsip utama:

- Tidak ada data pasar live.
- Tidak ada prediksi, rekomendasi trading, atau target harga.
- Tidak ada sentimen atau indikator yang dikarang.
- Watchlist disimpan di browser, bukan di server.

---

## 1. Ringkasan Produk

| Bagian | Status | Keterangan |
|---|---|---|
| Market Overview | Ada | Snapshot sesi terakhir, breadth, top gainers/losers, most active |
| Stock Explorer | Ada | 30 ticker, pencarian, sorting, watchlist |
| Historical OHLCV Chart | Ada | Candlestick + volume, rentang 1M/3M/6M/1Y/All |
| Watchlist | Ada | Disimpan di `localStorage` browser |
| Data Health | Ada | Memakai ulang validator TASK 106 |
| Read-only API | Ada | FastAPI di bawah `/api` |
| Frontend/backend integration | Ada | Frontend statis disajikan oleh FastAPI |

Dataset saat ini:

- 30 ticker (universe IDX30 pada dataset riset).
- 14.405 baris OHLCV + fitur sentimen.
- 482 sesi trading di dataset.
- Periode data: `2024-10-07` sampai `2026-10-09`.
- 8 baris memiliki berita tertaut (`news_count > 0`).
- `ml/data/` di-ignore oleh Git; artefak harus dibuat oleh pipeline lokal.

---

## 2. Keputusan Teknis (Bahasa Sederhana)

1. **Backend membaca Parquet lokal, bukan database live.**
   Tujuannya agar terminal menampilkan angka yang benar-benar ada di dataset
   riset, tanpa mengarang data baru. API hanya membaca file, tidak menulis.

2. **Satu server untuk API dan frontend.**
   FastAPI menyajikan endpoint `/api` dan file frontend dari folder `frontend/`.
   Tidak perlu Node.js, build step, atau server frontend terpisah.

3. **Frontend memakai HTML, CSS, dan JavaScript biasa.**
   Tidak ada framework berat. Chart dibuat dengan SVG sendiri agar tetap
   berjalan offline dan tidak menarik library dari CDN.

4. **Validator TASK 106 dipakai ulang.**
   Data Health tidak membuat aturan validasi baru. Endpoint memanggil
   `ml/validation/validate_dataset.py`, lalu merapikan hasilnya menjadi JSON.

5. **Watchlist disimpan di browser.**
   Backend tetap read-only. Watchlist tidak masuk server dan tidak perlu login.
   Konsekuensinya, watchlist hanya ada di browser yang dipakai.

6. **Sentimen kosong tetap NULL.**
   Hari tanpa berita tidak diisi nilai nol. Ini mengikuti prinsip pipeline:
   "no-news is not neutral sentiment". Karena itu cakupan sentimen terlihat
   sangat rendah dan ditampilkan sebagai WARN, bukan disamarkan.

7. **Tidak ada indikator teknikal tambahan di MVP.**
   Chart hanya menampilkan OHLCV dan volume aktual. SMA/RSI/EMA tidak dihitung
   karena belum ada di dataset dan tidak boleh difabrikasi.

8. **High/low memakai 252 sesi, bukan label "52 minggu" yang longgar.**
   UI menulis "High 252 sesi" dan "Low 252 sesi" agar sesuai dengan jumlah baris
   historis yang benar-benar dihitung.

---

## 3. Prasyarat

- Windows PowerShell (contoh di dokumen ini).
- Python 3.12 (sesuai environment proyek).
- File dataset lokal:
  - `ml/data/base_dataset.parquet`
  - `ml/data/prices_clean.parquet`
  - `ml/data/daily_sentiment.parquet`
  - `ml/data/trading_calendar.parquet`
- `.env` **tidak diperlukan** untuk menjalankan terminal. `.env` hanya dipakai
  oleh pipeline/scraper yang membutuhkan Supabase atau sumber berita.

---

## 4. Instalasi

Dari root repository:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Jika PowerShell menolak aktivasi virtual environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

Alternatif tanpa aktivasi:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 5. Menjalankan Aplikasi

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Buka:

- Terminal: <http://127.0.0.1:8000>
- Dokumentasi API: <http://127.0.0.1:8000/docs>
- Liveness: <http://127.0.0.1:8000/api/health>

Frontend dan backend berjalan di port yang sama, jadi tidak ada konfigurasi CORS
atau URL API terpisah.

### Memakai dataset dari folder lain

```powershell
$env:FINSENTIMIND_DATA_DIR = "D:\path\ke\dataset"
python -m uvicorn app.main:app --port 8000
```

Jika dataset tidak ditemukan, terminal menampilkan pesan error dan endpoint data
mengembalikan HTTP 503.

---

## 6. Halaman Terminal

### Market Overview

- Snapshot tanggal terakhir.
- Jumlah ticker dan sesi.
- Rata-rata return 1D equal-weight (bukan IHSG).
- Jumlah naik/turun/stagnan.
- Top gainers (hanya return positif) dan top losers (hanya return negatif).
- Most active berdasarkan volume.
- Cakupan sentimen dan peringatan bahwa NULL bukan netral.

### Stock Explorer

- Pencarian ticker.
- Sorting berdasarkan ticker, close, return 1D, volume, news, sentimen, sesi.
- Tombol Watch per ticker.
- Klik ticker untuk membuka detail.

### Detail Saham

- Close, return 1D, volume, jumlah berita.
- High/low 252 sesi, average volume 20 sesi.
- Perubahan 5 dan 20 sesi.
- Coverage sesi terhadap trading calendar.
- Chart candlestick + volume dengan rentang 1M/3M/6M/1Y/All.
- Riwayat sentimen hanya untuk hari yang punya berita.
- Lima sesi terakhir.

Deep link untuk membuka detail langsung:

```text
http://127.0.0.1:8000/?ticker=BBCA#explorer
```

### Watchlist

- Tambah/hapus ticker dari Explorer atau detail.
- Disimpan di `localStorage` dengan key `fsm.watchlist.v1`.
- Tombol "Kosongkan" menghapus semua ticker di browser tersebut.
- Tidak ada request tulis ke backend.

### Data Health

- Status struktural dari validator TASK 106.
- Jumlah PASS/WARN/INFO/FAIL.
- Umur dataset dan threshold freshness.
- Daftar file sumber, ukuran, dan waktu modifikasi.
- Tabel seluruh check, bisa difilter per severity.

---

## 7. Read-only API

Semua endpoint berada di `/api`.

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/health` | Liveness API + ketersediaan dataset |
| GET | `/api/meta` | Metadata, provenance, jumlah baris/ticker |
| GET | `/api/market/overview` | Snapshot sesi terakhir |
| GET | `/api/stocks` | Daftar saham; filter `search`, `tickers`, `sort`, `order` |
| GET | `/api/stocks/{ticker}` | Detail saham + statistik + kualitas data |
| GET | `/api/stocks/{ticker}/ohlcv` | OHLCV historis; filter `start`, `end`, `limit` |
| GET | `/api/data/health` | Laporan validator; filter `freshness_days` |

Contoh:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/market/overview"
Invoke-RestMethod "http://127.0.0.1:8000/api/stocks?sort=return_1d&order=desc"
Invoke-RestMethod "http://127.0.0.1:8000/api/stocks/BBCA"
Invoke-RestMethod "http://127.0.0.1:8000/api/stocks/BBCA/ohlcv?limit=120"
Invoke-RestMethod "http://127.0.0.1:8000/api/data/health"
```

Tidak ada endpoint POST/PUT/PATCH/DELETE untuk data pasar. Upaya menulis ke
endpoint data akan menghasilkan HTTP 405.

---

## 8. Menjalankan Validator Secara Manual

Data Health memakai validator yang sama. Untuk membuat laporan Markdown:

```powershell
python -m ml.validation.validate_dataset
```

Laporan ditulis ke `ml/reports/dataset_validation_<timestamp>.md`.

Pilihan lain:

```powershell
python -m ml.validation.validate_dataset --no-save
python -m ml.validation.validate_dataset --freshness-threshold-days 30
```

Catatan: endpoint Data Health menghitung freshness terhadap `date.today()`.
Karena itu dataset lama akan selalu memunculkan WARN freshness, walaupun status
strukturalnya PASS.

---

## 9. Testing

Jalankan seluruh test:

```powershell
python -m pytest -q
```

Hasil verifikasi MVP 001:

- `334 passed`, 5 warning Pydantic lama yang sudah ada sebelumnya.
- 14 test baru untuk repository dan API MVP.
- Test frontend statis memastikan `index.html`, `app.js`, dan `styles.css`
  disajikan dengan benar.

Verifikasi browser manual (Chrome headless + CDP) yang dilakukan:

- Market Overview terisi nilai aktual, bukan placeholder.
- Watchlist bisa menambah/menghapus dan bertahan setelah reload.
- Pencarian dan sorting Stock Explorer bekerja.
- Chart detail tampil, jumlah candle sesuai rentang, dan tombol 1M/6M berubah.
- Data Health menampilkan 35 PASS, 2 WARN, 0 FAIL dan filter WARN bekerja.
- Tidak ada error JavaScript di console browser.

---

## 10. Troubleshooting

| Gejala | Penyebab | Solusi |
|---|---|---|
| `ModuleNotFoundError: uvicorn` | Dependency belum terpasang | `pip install -r requirements.txt` |
| HTTP 503 di endpoint data | File Parquet tidak ada | Jalankan pipeline TASK 102–106 atau set `FINSENTIMIND_DATA_DIR` |
| Port 8000 dipakai | Proses lain berjalan | `python -m uvicorn app.main:app --port 8010` |
| Data Health WARN freshness | Dataset lebih tua dari threshold | Refresh pipeline; WARN bukan kegagalan struktur |
| Cakupan sentimen 0,0555% | Hanya 8 baris punya berita | Batasan dataset; jangan isi nol secara manual |
| Watchlist hilang | localStorage dibersihkan / browser lain | Tambahkan ulang; watchlist memang client-side |

---

## 11. Batasan MVP

- Universe hanya 30 ticker yang ada di dataset, bukan seluruh bursa.
- Data berhenti di `2026-10-09`; terminal tidak melakukan refresh live.
- Tidak ada profil perusahaan, sektor, fundamental, atau berita mentah di UI.
- Tidak ada prediksi ML, target harga, atau rekomendasi.
- Tidak ada indikator teknikal yang belum dihitung pipeline.
- Sentimen sangat sparse dan tidak boleh diinterpretasikan sebagai cakupan pasar.
- Watchlist tidak tersinkron antar browser/perangkat.
- Tidak ada autentikasi atau multi-user.
- Chart adalah visualisasi OHLCV, bukan alat backtest.

---

## 12. Catatan Git

- Fitur MVP berada di branch `feat/mvp-001-terminal`.
- Jangan merge ke `main` tanpa persetujuan project owner.
- `ml/data/` dan `.env` tidak boleh di-commit.
- Commit hanya file yang relevan dengan milestone MVP.
