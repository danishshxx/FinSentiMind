# FinSentiMind — Quantitative Features (TASK 109)

## Tujuan

Menghasilkan fitur kuantitatif deterministik dari OHLCV historis lokal tanpa
look-ahead bias. Fitur dipakai oleh API Stock Detail dan pipeline riset ML.

- Versi: `quant_features_v1`
- Kode: `ml/features/quant.py`
- Output default:
  `ml/data/features/quant_features_v1/quant_features.parquet`
- Manifest: `feature_manifest.json`
- Quality report: `ml/reports/quant_features_v1_quality.json`

## Asumsi Harga

- Input adalah `prices_clean.parquet` dari pipeline yang sudah dibersihkan.
- Penyesuaian corporate action mengikuti asumsi upstream; modul ini **tidak**
  melakukan penyesuaian harga tambahan.
- Window memakai jumlah sesi trading per ticker, bukan hari kalender.
- Warm-up tetap `NULL`; tidak ada fill, backfill, atau interpolasi.

## Definisi Fitur

| Fitur | Definisi | Warm-up |
|---|---|---|
| `return_1d` | `close_t / close_{t-1} - 1` | 1 |
| `return_5d` | `close_t / close_{t-5} - 1` | 5 |
| `return_20d` | `close_t / close_{t-20} - 1` | 20 |
| `log_return_1d` | `ln(close_t / close_{t-1})` | 1 |
| `sma_20` | mean close 20 sesi | 20 |
| `sma_50` | mean close 50 sesi | 50 |
| `sma_200` | mean close 200 sesi | 200 |
| `ema_20` | EMA span 20, `adjust=False` | 20 |
| `ema_50` | EMA span 50, `adjust=False` | 50 |
| `rsi_14` | Wilder RSI, alpha `1/14` | 14 |
| `momentum_5` | `close_t - close_{t-5}` (poin harga) | 5 |
| `momentum_20` | `close_t - close_{t-20}` (poin harga) | 20 |
| `volatility_20` | std sample return harian, 20 sesi | 21 |
| `atr_14` | Wilder ATR dari true range | 14 |
| `volume_sma_20` | mean volume 20 sesi | 20 |
| `relative_volume_20` | `volume / volume_sma_20` | 20 |
| `volume_change_1d` | `volume_t / volume_{t-1} - 1` | 1 |

### Catatan RSI

RSI memakai Wilder smoothing:

```text
alpha = 1/14
avg_gain = ewm(gain, alpha, adjust=False, min_periods=14)
avg_loss = ewm(loss, alpha, adjust=False, min_periods=14)
RSI = 100 - 100 / (1 + avg_gain/avg_loss)
```

Kasus khusus:

- Semua perubahan naik: RSI = 100.
- Semua perubahan turun: RSI = 0.
- Harga flat: RSI = 50 (konvensi, bukan sinyal).

### ATR

True range:

```text
max(high-low, abs(high-prev_close), abs(low-prev_close))
```

ATR = Wilder EWM alpha `1/14`, `min_periods=14`.

## No Look-Ahead

- Semua rolling/shift memakai data saat ini dan sebelumnya.
- Tidak ada shift negatif.
- Test numerik membandingkan fitur pada baris historis yang sama antara dataset
  penuh dan dataset yang dipotong setelah baris tersebut; hasil harus identik.

## Menjalankan

```powershell
python -m ml.features.quant
```

Opsi:

```powershell
python -m ml.features.quant --input ml/data/prices_clean.parquet `
  --output ml/data/features/quant_features_v1/quant_features.parquet `
  --manifest ml/data/features/quant_features_v1/feature_manifest.json `
  --report ml/reports/quant_features_v1_quality.json
```

Default input mengikuti generation aktif jika `ml/data/active.json` tersedia.

## Quality Report

Berisi:

- jumlah baris, ticker, rentang tanggal;
- duplicate key count;
- per fitur: non-null, null count, null percentage, min, max;
- asumsi metodologi dan warm-up.

Report **tidak** menyatakan fitur sebagai sinyal investasi. Fitur hanya input
riset.

## Integrasi Terminal

`GET /api/stocks/{ticker}` menambahkan objek:

```json
{
  "quant_features": {
    "version": "quant_features_v1",
    "as_of": "2026-10-09",
    "values": {},
    "missing": [],
    "note": "..."
  }
}
```

Stock Detail menampilkan panel Quant Features. Nilai yang belum tersedia
ditampilkan `NULL`, bukan nol.

## Batasan

- Momentum 5/20 dinyatakan dalam poin harga, bukan persentase.
- Volatilitas belum annualized.
- Tidak ada indikator tambahan di luar daftar di atas.
- Fitur bergantung pada kualitas dan asumsi adjustment harga upstream.
- Belum ada validasi prediktif pada TASK 109; itu tugas TASK 111.
