PROJECT: FinSentiMind
POSITIONING: AI-Powered Equity Research Terminal for Indonesian Market

WORKFLOW:
DeepSeek = implementasi kode di repository.
ChatGPT = mentor, reviewer arsitektur, validasi konsep quant,
dan penyusun task untuk DeepSeek.
User = belajar memahami keputusan engineering dan quantitative finance.

COMPLETED:
Phase 0: Production Foundation
TASK 101: Data Foundation Architecture Review
TASK 102A: Price Inventory + Historical Backfill
TASK 102B: Clean Price Dataset
TASK 102C: Price Quality Gate + Split Detection
TASK 103: Trading Calendar + Time Alignment
TASK 104: Daily Sentiment Aggregation + Refresh Workflow

LAST COMMIT MESSAGE:
feat(ml): add daily sentiment aggregation and refresh workflow (TASK 104)

LAST REPORTED TEST RESULT:
226 passed, 5 Pydantic deprecation warnings.

NEXT:
TASK 105 — Base Dataset Builder
Join prices_clean.parquet with daily_sentiment.parquet.

PRINCIPLES:
- No look-ahead bias.
- Preserve all valid price rows.
- No-news is not neutral sentiment.
- Detect duplicate join keys.
- No target generation or ML training in TASK 105.
- Every task must include tests and a review before commit.