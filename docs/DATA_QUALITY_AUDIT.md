# Data Quality Audit — TASK 107A

This document records the evidence gathered for the two unresolved data
anomalies. It does not change historical values.

## 1. GOTO volume on 2026-10-09

### Observed values

| Source | Volume |
|---|---:|
| Legacy local snapshot `ml/data/prices_clean.parquet` | 32,994,912 |
| Failed-run staging `20261114T154144Z_acedb3bb` | 12,917,896,800 |
| Current yfinance raw response (`GOTO.JK`, read-only fetch) | 12,917,896,800 |
| Current Supabase `harga_saham` row | 12,917,896,800 |

### Cross-check

- Overlapping rows: 14,405.
- Rows with a volume change greater than 50%: **1** (GOTO, 2026-10-09).
- Ratio: **391.51x**.
- Neighboring GOTO sessions ranged from roughly **7.7 billion** to
  **97.5 billion** shares. The legacy value of 32.99 million is a one-row
  outlier; the refreshed value is consistent with adjacent sessions.

### Conclusion

The evidence supports a provider/snapshot correction in the legacy local row,
not a unit conversion introduced by the new pipeline. The current yfinance
response and the current Supabase record agree. No normalization, clipping, or
historical rewrite was applied.

### Residual risk

The original provider response behind the legacy row was not archived, so the
exact historical reason for the old value cannot be proven. The audit records
this limitation instead of guessing.

## 2. Calendar weekday gaps

The quality gate reports missing weekday **groups per ticker**, not missing
exchange dates. The failed-run report contains **822 per-ticker groups**.

| Metric | Value |
|---|---:|
| Per-ticker missing weekday groups | 822 |
| Unique missing date ranges | 28 |
| Weekdays with zero coverage | 43 |
| Weekdays with partial coverage | 1 |
| Official holiday groups | 389 |
| Collective leave groups | 120 |
| Mixed official/collective/exchange groups | 300 |
| Partial-observation groups | 13 |
| Unexplained groups | 0 |

### Interpretation

- The 822 groups collapse to 28 date ranges; the same exchange closure is
  counted once per affected ticker.
- 809 groups align with the curated IDX/government closure map.
- `2026-08-25` is not classified as a holiday: 17 of 30 tickers have data and
  13 are missing. It is a **partial cross-sectional observation** and requires
  provider-level verification.
- AADI starts on `2024-12-05`, so `2024-11-27` is not counted as a gap for AADI
  before its first observation.
- No group defaults to "holiday" when evidence is absent.

### Limitations

The closure map is curated for this audit, not fetched from an authoritative
API. Revalidate it against the annual IDX exchange decree before using the audit
as a compliance or licensing statement. The trading calendar itself was not
rewritten.

## Evidence files

- `ml/reports/goto_volume_audit.md`
- `ml/reports/calendar_gap_audit.md`
- `ml/reports/calendar_gap_audit.json`
- `ml/reports/pending_news_20261114T154144Z_acedb3bb.json`

These reports are generated artifacts under the ignored `ml/reports/` directory;
this document is the tracked summary.
