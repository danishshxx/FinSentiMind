# FinSentiMind — Data Quality Policy

## 1. Exchange calendar rules

1. The exchange calendar is authoritative and versioned
   (`ml/preprocessing/idx_exchange_calendar.json`).
2. A date is one of `WEEKEND`, `OFFICIAL_CLOSURE`, `VERIFIED_OPEN`, `UNVERIFIED`.
3. Cross-sectional ticker coverage never determines whether the exchange was
   open. Missing observations are investigated, not converted into holidays.
4. Dates outside calendar coverage are `UNVERIFIED`; they are never assumed open
   or closed.
5. Future sessions are never manufactured with weekday logic.

## 2. Price integrity rules

- Official-closure rows are removed before returns and rolling features.
- Raw upstream rows are preserved outside the canonical dataset.
- Zero-volume rows are **not** removed globally; a zero-volume bar on a verified
  open session is legitimate and preserved.
- A closure bar with positive volume or non-flat OHLC is a blocking
  contradiction (`ExchangeClosureContradictionError`). It is never silently
  deleted.
- `return_1d` is computed on consecutive valid observations per ticker. It is
  NULL only at the first observation and never zero-filled or forward-filled.
- Duplicate `(ticker, trading_date)` keys are rejected.

## 3. Sentiment rules

- News maps to the next eligible verified session using the Asia/Jakarta cutoff
  and no look-ahead.
- Future news with no verified eligible session stays pending.
- Sessions without matched news keep all sentiment fields NULL. NULL is not
  neutral sentiment.
- `sentiment_std` may be NULL for a single-article session; it is required when
  at least two articles are present.

## 4. Generation acceptance gates

A generation may be published only if `ml.validation.generation_audit` reports
no FAIL:

| Gate | Rule |
|---|---|
| Required artifacts | Four mandatory Parquet artifacts present |
| Calendar version | A single calendar version across the calendar artifact |
| Closure rows | Zero price rows on official closures |
| Weekend rows | Zero price rows on weekends |
| Unverified dates | WARN, never silently accepted |
| Future sessions | No trading session later than the latest price row |
| Row reconciliation | base == prices; quant == prices when present |
| Ticker reconciliation | prices and base ticker sets match |
| Unique keys | No duplicate `(ticker, trading_date)` |
| Date range | base range equals price range |
| Sentiment keys | Every sentiment date is a price session |
| Price nulls | Core OHLCV fields non-null |
| Return nulls | NULL only at warm-up |
| Sentiment nulls | Unmatched rows fully NULL; matched rows populated |

Publication also verifies SHA-256 checksums and reader compatibility through
`resolve_active_dataset(..., verify_hashes=True)`.

## 5. Data classification

| Label | Meaning |
|---|---|
| VERIFIED | Read from a named source and checked |
| DERIVED | Computed deterministically from verified inputs |
| ESTIMATED | Not used in this platform |
| UNAVAILABLE | No verified source; never fabricated |

The Data Health view reports these labels per dataset and subsystem. EOD prices
are never labelled real-time.

## 6. Publication policy

- New generations are immutable; old generations and legacy files are preserved.
- A failed publication leaves the previous `active.json` untouched.
- Rollback requires a verified generation with matching checksums.
- No Supabase writes, no historical source mutation, no public deployment.
