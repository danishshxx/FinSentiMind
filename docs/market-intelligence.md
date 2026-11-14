# FinSentiMind — Market Intelligence (TASK 110)

## Ringkasan

Market Intelligence memberi konteks pasar dari dataset lokal:

- breadth,
- equal-weight universe performance,
- median dan distribusi cross-sectional return,
- market volatility,
- volume activity,
- sector performance dari metadata yang tersedia,
- regime classification dengan metodologi transparan,
- macro context melalui adapter opsional.

**Equal-weight index di sini bukan IHSG.** Labelnya selalu
“Equal-weight local universe proxy (bukan IHSG)”.

## Perhitungan

Semua perhitungan memakai data sampai `as_of` saja. Implementasi:
`ml/market/intelligence.py`.

### Return universe

Untuk setiap tanggal:

```text
equal_weight_return_t = mean(return_1d ticker yang tersedia)
equal_weight_index_t  = 100 × cumulative_product(1 + equal_weight_return)
```

Ticker yang tidak punya return pada tanggal tersebut tidak diisi nol; mereka
hanya tidak masuk rata-rata.

### Breadth dan cross-section

Pada sesi terakhir:

- advancers: return > 0;
- decliners: return < 0;
- unchanged: return == 0;
- median, P10, P90 return lintas ticker.

### Volatility

```text
volatility_20d_annualized =
    std_sample(daily equal-weight return, 20 sesi) × sqrt(252)
```

### Volume activity

- total volume seluruh ticker per sesi;
- rata-rata 20 sesi;
- rasio volume terakhir terhadap rata-rata.

Rasio hanya deskriptif; bukan sinyal trading.

### Sector performance

Sector berasal dari instrument master TASK 108. Untuk setiap sector:

- coverage = ticker dengan return 20 sesi valid / total ticker sector;
- mean dan median return 20 sesi.

Sector tanpa coverage tidak ditampilkan.

### Regime

Regime dihitung dari distribusi historis, bukan threshold return arbitrary:

1. Hitung 20-session equal-weight return dan 20-session realized volatility
   untuk setiap tanggal historis.
2. Hitung percentile posisi nilai terakhir di distribusi yang tersedia.
3. Klasifikasi:
   - `high_volatility` jika percentile volatility ≥ 0.80;
   - `bullish` jika percentile return ≥ 2/3;
   - `bearish` jika percentile return ≤ 1/3;
   - `sideways` di antaranya;
   - `unknown` jika histori < 60 observasi.

Percentile memakai data sampai tanggal evaluasi, sehingga tidak ada look-ahead.
Methodology string disertakan di API.

## Macro Context

Indikator yang disiapkan:

- USD/IDR
- S&P 500
- Hang Seng
- Gold
- Coal
- Nickel
- CPO

Adapter `YFinanceMacroProvider` hanya aktif jika owner secara eksplisit
mengaktifkan:

```powershell
$env:FINSENTIMIND_ENABLE_MACRO_YFINANCE = "1"
```

Namun:

- default adalah **unavailable**;
- coal, nickel, dan CPO tidak diberi simbol spekulatif;
- provider error dikembalikan sebagai `unavailable` + alasan;
- tidak ada dummy value;
- lisensi redistribusi data harus diverifikasi sebelum produksi.

## API

```text
GET /api/market/intelligence
```

Contoh field:

```json
{
  "as_of": "2026-10-09",
  "label": "Equal-weight local universe proxy (bukan IHSG)",
  "index": {"level": 0, "return_1d": 0, "return_5d": 0, "return_20d": 0},
  "breadth": {},
  "cross_section": {},
  "volatility": {},
  "volume_activity": {},
  "regime": {},
  "sector_performance": {},
  "macro": {}
}
```

Kontrak endpoint lama tidak berubah; endpoint ini adalah tambahan.

## Integrasi Terminal

Market Monitor menampilkan panel:

- Market Regime;
- Cross-Section;
- Volatility & Volume;
- Sector Performance;
- Macro Context dengan status unavailable/available.

Panel chart Market Monitor yang sudah ada tetap memakai OHLCV lokal.

## Batasan

- Universe hanya ticker yang tersedia di dataset.
- Bukan IHSG dan bukan benchmark resmi.
- Sector coverage bergantung pada metadata TASK 108.
- Regime adalah klasifikasi statistik, bukan rekomendasi investasi.
- Macro default unavailable sampai provider resmi dan lisensi disetujui.
- Belum ada factor model, beta, atau risk decomposition.
