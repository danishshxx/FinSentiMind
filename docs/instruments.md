# FinSentiMind — Instrument Master Data (TASK 108)

## Tujuan

Menyediakan identitas instrumen IDX yang auditabel untuk dipakai oleh Stock
Explorer, Stock Detail, market intelligence, dan riset kuantitatif.

Prinsip:

- Metadata yang tidak terverifikasi tetap `null`.
- Company name, sector, subsector, listing date, dan listing status tidak
  difabrikasi.
- Setiap field yang terisi memiliki provenance sumber.
- Perubahan metadata dicatat sebagai revision.

## Lokasi dan Format

Master data:

```text
ml/metadata/idx30_instruments.json
```

Validator/loader:

```text
ml/metadata/master.py
```

Validasi manual:

```powershell
python -m ml.metadata.master
```

Field level-atas:

- `schema_version`
- `metadata_version`
- `effective_from`
- `updated_at`
- `sources` — daftar sumber dan field yang didukung.
- `revisions` — riwayat perubahan metadata.
- `instruments` — daftar instrumen.

Field per instrumen:

| Field | Arti |
|---|---|
| `ticker` | Kode 4 huruf IDX, uppercase |
| `exchange` | Saat ini `IDX` |
| `company_name` | Nama tampilan yang didukung source; boleh null |
| `sector` | Sektor broad; boleh null |
| `subsector` | Boleh null |
| `listing_status` | `active`, `inactive`, atau `unknown` |
| `listing_date` | ISO date atau null |
| `previous_tickers` | Riwayat kode lama |
| `successor_ticker` | Kode pengganti jika ada |
| `provenance` | Field → source id |
| `notes` | Catatan bila ada |

Field hasil komputasi API:

- `metadata_status`: `complete`, `partial`, atau `unavailable`.
- `completeness_pct`: persentase field metadata yang terisi.
- `missing_fields`: daftar field yang belum tersedia.

## Sumber Saat Ini

| Source | Dipakai untuk | Batasan |
|---|---|---|
| `docs/universe.md` | ticker, exchange, sector | Snapshot IDX30 2026-08-03 |
| `app/core/aliases.py` | company display name | Alias proyek, bukan verifikasi legal |
| `prices_clean.parquet` | listing_status active | Terbukti punya data sampai 2026-10-09 |
| `unavailable` | subsector, listing date | Tidak ada sumber terverifikasi |

Artinya:

- 30/30 ticker punya sector broad.
- 28/30 punya company display name; AADI dan DEWA tetap null.
- 0/30 punya subsector terverifikasi.
- 0/30 punya listing date terverifikasi.
- Semua 30 berstatus `active` karena ada data harga lokal terbaru.

## Membedakan Unknown, Unavailable, dan Inactive

- `null` — nilai tidak tersedia atau belum terverifikasi.
- `listing_status=unknown` — status listing belum diketahui.
- `listing_status=inactive` — instrumen tidak aktif.
- `metadata_status=unavailable` — metadata inti tidak tersedia.
- `metadata_status=partial` — ticker/exchange/listing status ada, tetapi field
  lain belum lengkap.
- `metadata_status=complete` — semua field metadata terisi.

## Update Metadata

1. Tambah atau koreksi data di JSON hanya jika ada source yang jelas.
2. Update `metadata_version` dan `updated_at`.
3. Tambahkan entry baru ke `revisions`.
4. Jalankan `python -m ml.metadata.master`.
5. Jalankan test.
6. Commit JSON tanpa Parquet atau secret.

Jika ticker berubah:

- isi `previous_tickers` pada instrumen baru;
- isi `successor_ticker` pada instrumen lama;
- jangan membuat ticker lama dan baru sebagai dua instrumen aktif sekaligus;
- validator menolak previous ticker yang bertabrakan dengan ticker aktif lain.

## API dan Integrasi

Endpoint read-only tambahan:

```text
GET /api/instruments
GET /api/instruments/{ticker}
```

Integrasi backward-compatible:

- `GET /api/stocks` menambahkan `company_name`, `sector`, `listing_status`,
  `metadata_status`, dan `completeness_pct`.
- `GET /api/stocks/{ticker}` menambahkan objek `instrument`.
- Stock Explorer menampilkan kolom Sector.
- Stock Detail menampilkan company name dan metadata di panel Data Quality.

Nilai yang tidak tersedia ditampilkan sebagai “Tidak tersedia”, bukan dikarang.

## Batasan

- Belum ada integrasi otomatis ke vendor data komersial.
- Company name adalah display name proyek, bukan verifikasi legal name.
- Sector broad berasal dari snapshot universe, bukan taksonomi IDX resmi
  terbaru per emiten.
- Subsector dan listing date belum tersedia.
- Metadata tidak menentukan kelayakan investasi.
