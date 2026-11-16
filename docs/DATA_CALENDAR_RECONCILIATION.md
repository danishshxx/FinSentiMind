# Data Calendar & OHLCV Integrity Remediation

Status: **LOCAL RECONCILIATION COMPLETE — PUBLICATION PENDING OWNER APPROVAL**

## Root cause

The price provider returned flat, zero-volume OHLCV bars for official IDX
exchange closures. The previous calendar treated coverage ratio as the primary
signal, so four May 2026 closures (all 30 tickers present) were classified as
trading sessions. `2026-08-25` was already non-trading only because coverage
was 17/30.

The contaminated bars entered canonical prices before returns and features were
computed. Because they were flat, returns became zero across the closure and
rolling windows were polluted.

## Authoritative calendar

Version: `IDX-2024-2026-v2`

Coverage: `2024-10-07` through `2026-12-31`.

Sources:

- 2024 IDX exchange calendar:
  `https://www.idx.co.id/id/investor/jadwal-libur-bursa`
- 2025 IDX holiday calendar:
  `https://www.idxcarbon.co.id/document/share/109/e11c312a-95d6-4525-b71a-f70377cc7777`
- 2025 amendment (adds `2025-08-18`):
  `https://www.idxcarbon.co.id/document/share/143/cf878b0d-87c1-45e2-98a5-4c54d6efc8c2`
- 2026 IDX holiday calendar, `Peng-00171/BEI.POP/09-2025`:
  `https://www.idxcarbon.co.id/document/share/158/0a901391-a0ad-4ca5-930e-2788cb7eb27f`

The calendar source is versioned in
`ml/preprocessing/idx_exchange_calendar.json` and loaded offline by
`ml/preprocessing/idx_calendar.py`.

## Original metrics (TASK 107B staging)

| Metric | Value |
|---|---:|
| Price rows | 15,155 |
| Unique price dates | 507 |
| Selected calendar sessions | 506 |
| Zero-volume rows | 137 |
| `2026-05-14` rows | 30 |
| `2026-05-15` rows | 30 |
| `2026-05-27` rows | 30 |
| `2026-05-28` rows | 30 |
| `2026-08-25` rows | 17 |

## New metrics (reconciled staging)

| Metric | Value |
|---|---:|
| Price rows | **15,018** |
| Unique price dates | **502** |
| Usable trading sessions | **502** |
| Official closure dates in range | 48 |
| Unverified dates | 0 |
| Rows on the five 2026 closures | **0** |
| Zero-volume rows after quarantine | **0** |
| Quality gate errors / warnings | 0 / 0 |
| Missing weekday groups | 0 |
| Validation | PASS 37 / WARN 1 / FAIL 0 |

The single WARN is sparse sentiment coverage, not a structural failure.

## Rows excluded

Total: **137**.

| Date | Rows | Tickers |
|---|---:|---|
| 2026-05-14 | 30 | All 30 universe tickers |
| 2026-05-15 | 30 | All 30 universe tickers |
| 2026-05-27 | 30 | All 30 universe tickers |
| 2026-05-28 | 30 | All 30 universe tickers |
| 2026-08-25 | 17 | ANTM, ASII, BBNI, BBRI, BMRI, BRPT, BUMI, INCO, INDF, INKP, JPFA, KLBF, MEDC, PGAS, TLKM, UNTR, UNVR |

All excluded rows were zero-volume and flat OHLC. No positive-volume or
non-flat closure bars were found. Any such bar would have blocked the run.

Audit file:
`ml/reports/exchange_closure_quarantine_task107b_calendar_reconciliation_20261115.json`

## Calendar amendments considered

- `2025-08-18` is an official amendment holiday and is excluded.
- The 2026 calendar includes `2026-05-14`, `2026-05-15`, `2026-05-27`,
  `2026-05-28`, and `2026-08-25`.
- Official closures and unverified dates are distinct from verified open
  sessions. Visibility into missing provider observations is preserved.

## Impact on returns and technical features

Example BBCA:

```text
2026-05-13 close 6,100
2026-05-18 close 6,125
return_1d = 6,125 / 6,100 - 1 = +0.4098%
```

The four removed May bars no longer force zero returns. A regression test
compares feature output with and without closure bars and requires equality.
Returns, SMA, EMA, RSI, ATR, volume statistics, volatility, and quant features
were rebuilt from the filtered price dataset.

Calendar behavior changed intentionally: a verified open session with partial
ticker coverage remains a trading session, while missing observations are
visible to the quality gate. Coverage ratio is descriptive, not the definition
of an exchange holiday.

## Sentiment alignment

- MAPPED: 26
- PENDING_FUTURE_SESSION: 12
- QUARANTINED_UNRESOLVABLE: 0
- Daily sentiment rows: 8
- Sentiment trading dates: `2026-09-14`, `2026-10-09`
- No sentiment row maps to an official closure.

Future news remains pending. No news is mapped to a previous session.

## Validation and staging

New staging:

`ml/data/staging/task107b_calendar_reconciliation_20261115/`

Artifacts rebuilt in order:

1. price quarantine/normalization
2. quality gate
3. authoritative trading calendar
4. sentiment alignment
5. base dataset
6. quant features
7. full validation

No provider write, Supabase mutation, publication, or `active.json` change was
performed. The original TASK 107B staging and failed TASK 107A staging remain
untouched.

## Stale artifacts and unknowns

The active legacy flat dataset still contains the old closure bars, so the
running terminal and previously generated ML/market analytics remain stale
until a corrected generation is published and those artifacts are regenerated.
Do not mix old and new calendar vintages.

Remaining unknowns:

- The exact IDX announcement identifier for the 2024 Oct–Dec schedule is not
  recorded in the repository; the official IDX calendar page is cited.
- The provider's historical raw responses were not archived.
- No full external provider refresh was run; the reconciliation uses the
  already available TASK 107B snapshot.

## Publication readiness

The corrected staging is **ready for separate owner review**. It is not
published. A separate owner-approved refresh/publication is required before
readers, screenshots, ML research, and market analytics are regenerated from
the corrected calendar.
