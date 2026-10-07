# FinSentiMind — Ticker Universe

## Current Snapshot

| Field | Value |
|---|---|
| **Index** | IDX30 |
| **Snapshot date** | 2026-08-03 |
| **Ticker count** | 30 |
| **Rebalance cadence** | Quarterly (Feb, May, Aug, Nov) |
| **Purpose** | Phase 1 static universe for dataset building |

## Constituents

| Ticker | Sector | Primary Aliases |
|---|---|---|
| AADI | Energy | AADI |
| ADMR | Energy | ADMR, Alamtri Minerals |
| ADRO | Energy | ADRO, Adaro, Alamtri Resources |
| AMRT | Consumer | AMRT, Alfamart |
| ANTM | Mining | ANTM, Aneka Tambang, Antam |
| ASII | Industrials | ASII, Astra International |
| BBCA | Banking | BBCA, BCA, Bank Central Asia |
| BBNI | Banking | BBNI, BNI, Bank Negara Indonesia |
| BBRI | Banking | BBRI, BRI, Bank Rakyat Indonesia |
| BMRI | Banking | BMRI, Bank Mandiri |
| BRPT | Materials | BRPT, Barito Pacific |
| BUMI | Energy | BUMI, Bumi Resources |
| CPIN | Consumer | CPIN, Charoen Pokphand |
| DEWA | Energy | DEWA |
| EMTK | Media | EMTK, Elang Mahkota |
| GOTO | Tech | GOTO, GoTo, Gojek Tokopedia |
| ICBP | Consumer | ICBP, Indofood CBP |
| INCO | Mining | INCO, Vale Indonesia |
| INDF | Consumer | INDF, Indofood Sukses Makmur |
| INKP | Materials | INKP, Indah Kiat |
| JPFA | Consumer | JPFA, Japfa Comfeed |
| KLBF | Healthcare | KLBF, Kalbe Farma |
| MBMA | Mining | MBMA, Merdeka Battery |
| MDKA | Mining | MDKA, Merdeka Copper Gold |
| MEDC | Energy | MEDC, Medco Energi |
| PGAS | Energy | PGAS, Perusahaan Gas Negara, PGN |
| PGEO | Energy | PGEO, Pertamina Geothermal |
| TLKM | Telco | TLKM, Telkom Indonesia, Telkomsel |
| UNTR | Industrials | UNTR, United Tractors |
| UNVR | Consumer | UNVR, Unilever Indonesia |

## Policy

- **Frozen for Phase 1.** No mid-experiment universe changes.
- Any change creates a new snapshot (e.g., `universe_2026-11-05.md`) and a
  corresponding `UNIVERSE_SNAPSHOT_DATE` bump in code.
- Snapshot metadata is embedded in every dataset artifact (Phase 1+).

## Source

IDX official index constituents — https://www.idx.co.id/en/market-data/indices/stock-index/

## Future Expansion

| Phase | Universe | Rationale |
|---|---|---|
| Phase 1 (now) | IDX30 (30) | Correctness first |
| Phase 3+ | LQ45 (45) | Broader coverage after pipeline stable |
| Phase 4+ | All-IDX liquid | Full scale |

## Notes

- The `WATCH_TICKERS` constant in `app/core/aliases.py` is the single source
  of truth for code.
- `IDX30_TICKERS` and `LQ45_TICKERS` are defined there; `WATCH_TICKERS`
  points to the active universe.
- Tickers with weak/unstable aliases (e.g., BUMI) should be flagged during
  news-matching quality review (Phase 3).