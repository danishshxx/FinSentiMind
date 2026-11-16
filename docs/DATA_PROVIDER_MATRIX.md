# FinSentiMind — Data Provider Matrix

Status values mean: **VERIFIED** (accessed and checked in this sprint),
**DOCUMENTED** (present in code, not re-exercised), **UNAVAILABLE** (no licensed
or retrievable source), **BLOCKED** (source refused access).

| Provider | Data | Access | Status | Evidence / notes |
|---|---|---|---|---|
| Supabase `harga_saham` | EOD OHLCV | Read-only SELECT | VERIFIED (read) | 15,155 raw rows audited; 30 tickers. No writes performed. |
| Supabase `berita_saham` | News | Read-only SELECT | DOCUMENTED | Used by existing sentiment pipeline. Not mutated. |
| yfinance `.JK` | EOD OHLCV + adj close + actions | Free, unauthenticated | VERIFIED | 45/45 documented tickers returned 22,593 rows through 2026-11-16. |
| IDX official securities list | Full instrument universe | Official endpoint | BLOCKED | `GetSecuritiesStock` returned HTTP 403 / gateway timeout. Inventory completeness recorded as false. |
| IDX official holiday calendar | Exchange closures | Official decrees | VERIFIED (documented) | `IDX-2024-2026-v2` with per-closure provenance. 2024 announcement identifier not archived. |
| RSS news | News | Public feeds | DOCUMENTED | Existing adapters; not re-run in this sprint. |
| Telegram / Stockbit news | News | Project adapters | DOCUMENTED | Respect source terms; no login or paywall bypass. |
| Fundamentals | Statements, actions, dividends | Manual verified import | UNAVAILABLE | No local dataset installed. `ml/fundamentals` is manual-import only. |
| Capital flow | Market + portfolio cash flow | Manual verified import | UNAVAILABLE | No provider licensed; broker identity is never inferable from public aggregates. |
| Macro | USD/IDR, indices, gold | yfinance adapters | DISABLED | Enabled only with `FINSENTIMIND_ENABLE_MACRO_YFINANCE=1` after licensing review. |
| Macro commodities | Coal, nickel, CPO | none | UNAVAILABLE | No speculative symbol mapping is invented. |
| ML research | Model metrics | Local pipeline | VERIFIED (run) | Re-run on the corrected generation; status `NOT_VALIDATED`. |

## Licence and usage boundaries

- Personal research use only. No public redistribution of market data.
- No paid subscription, API-key purchase, or scraping behind authentication.
- yfinance is used at free, public rate limits with batching, retries and delay
  between batches.
- Provider limitations are surfaced as `UNAVAILABLE`, never fabricated.

## Known data gaps

- Full IDX instrument enumeration is blocked by the exchange endpoint.
- Fundamentals, capital flow, and macro history are not installed.
- Sentiment coverage is sparse (8 of 15,018 rows in the active generation).
- yfinance may revise historical rows; re-ingestion is idempotent but not a
  point-in-time archive.
