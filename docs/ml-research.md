# FinSentiMind — ML Research Pipeline (TASK 111)

## Status

Pipeline riset ini **RESEARCH ONLY / NOT VALIDATED** pada snapshot data saat ini.

Model tidak boleh dipakai sebagai sinyal investasi tepercaya dan tidak terhubung
ke trading execution.

## Tujuan dan Target

Target awal:

```text
signal date t
entry       = open(t+1)
exit        = close(t+5)
target      = close(t+5) / open(t+1) - 1
direction   = 1 jika target > 0, selain itu 0
```

Alasan:

- Sinyal dibuat setelah close t, jadi entry di open t+1 menghindari eksekusi pada
  harga close yang sama.
- Horizon 5 sesi cukup pendek untuk data 2 tahun tetapi cukup panjang untuk
  mengurangi noise harian.
- Ini tetap pilihan riset, bukan jaminan target terbaik.

## Dataset

Fitur:

- `quant_features_v1` dari TASK 109;
- sentimen harian dari base dataset;
- market context: return universe, breadth, dan volatility 20 sesi;
- `has_news` sebagai penanda ketersediaan sentimen.
- sector hanya disimpan sebagai konteks, bukan fitur model utama.

Nilai sentimen yang hilang **tidak** diubah menjadi netral di dataset. Di dalam
model, imputasi median hanya dilakukan pada training fold dan `has_news`
mempertahankan informasi missing.

Kolom target dan future execution columns tidak masuk `MODEL_FEATURES`.

## Model Baseline

- Dummy majority class.
- Logistic Regression dengan standardisasi dan median imputation per fold.

Tidak ada model kompleks yang ditambahkan hanya untuk terlihat canggih.

## Validasi

- Expanding walk-forward.
- Tidak ada random train/test split.
- Embargo 5 sesi untuk label yang overlap.
- Test window 40 sesi per fold.
- Semua preprocessing di-fit hanya pada training fold.
- Strategy dievaluasi pada tanggal non-overlapping setiap 5 sesi.

Fold pada run terakhir:

| Fold | Train rows | Test rows |
|---|---:|---:|
| 1 | 8.478 | 1.200 |
| 2 | 9.678 | 1.200 |
| 3 | 10.878 | 1.200 |
| 4 | 12.078 | 1.187 |
| 5 | 13.278 | 840 |

## Evaluasi

Metrik klasifikasi agregat pada run terakhir:

| Metrik | Model | Dummy |
|---|---:|---:|
| ROC-AUC | 0.5208 | 0.5000 |
| PR-AUC | 0.4437 | 0.4334 |
| Precision | 0.4478 | 0.0000 |
| Recall | 0.6302 | 0.0000 |
| F1 | 0.5236 | 0.0000 |
| Brier | 0.2577 | 0.4334 |

Strategi long-only non-overlapping:

- 38 periode.
- Cumulative return: -32.60%.
- Max drawdown: -52.03%.
- Sharpe annualized: -0.887.
- Win rate: 42.11%.
- Turnover/positions rata-rata: 17.71.
- Universe equal-weight cumulative return: -29.47%.
- Universe Sharpe: -1.045.
- Biaya awal: 10 bps per sisi; sensitivity 0 dan 25 bps dihitung.

Hasil: model tidak menunjukkan bukti out-of-sample yang cukup. Status:
**NOT_VALIDATED**.

## Acceptance Rules

Model hanya boleh diberi label `RESEARCH_ONLY` jika:

1. ROC-AUC model > dummy + 0.02;
2. Sharpe strategi > Sharpe universe + 0.10;
3. strategi memiliki cumulative return dan Sharpe positif.

Jika tidak, status `NOT_VALIDATED`. Model tetap disimpan sebagai artefak riset,
bukan sinyal.

## Explainability

Kontribusi fitur dilaporkan sebagai rata-rata koefisien logistik terstandardisasi
antar fold. Ini asosiasi model, bukan causal effect.

Pada run terakhir, kontributor positif terbesar antara lain `return_1d`,
`market_volatility_20`, dan `market_breadth`; kontributor negatif terbesar antara
lain `log_return_1d` dan `market_return_1d`. Interpretasi harus hati-hati karena
model tidak tervalidasi.

## Menjalankan

```powershell
python -m ml.research.ml_pipeline
```

Output:

```text
ml/data/research/ml_research_v1/research_dataset.parquet
ml/reports/ml_research_v1.json
ml/reports/ml_research_latest.json
```

Opsi:

```powershell
python -m ml.research.ml_pipeline --start-fraction 0.6 --test-sessions 40 `
  --embargo-sessions 5 --cost-bps 10
```

## API

```text
GET /api/research/latest
```

Jika report belum ada:

```json
{"available": false, "status": "NOT_RUN", "not_for_trading": true}
```

Jika ada, report menyertakan `status`, `not_for_trading`, target definition,
fold metadata, metrics, strategy, explainability, dan limitations.

Endpoint ini bersifat tambahan dan tidak mengubah kontrak lama.

## Batasan

- Sentimen sangat sparse (8 baris berita pada snapshot); hasil belum stabil.
- Belum ada fundamental, macro tervalidasi, atau alternative data.
- Strategy mengasumsikan eksekusi next open dan biaya sederhana; slippage nyata
  bisa lebih buruk.
- Overlapping labels dikurangi dengan embargo dan sampling non-overlapping,
  tetapi tetap ada keterbatasan statistik.
- Tidak ada live trading atau broker order routing.
- Jangan mengekspos probabilitas sebagai jaminan return.
