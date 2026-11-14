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
TASK 105: Base Dataset Builder
TASK 106: Independent Dataset Validation
MVP 001: Read-only Local Equity Research Terminal
TASK 112: Hybrid Research Workstation UI + Market Monitor

CURRENT:
TASK 107: Data Refresh Reliability & Safe Dataset Publishing
- Staging + versioned generations
- Atomic active.json pointer
- Rollback to verified generation
- Strict stage failure handling
- Machine-readable refresh metadata
- Lock, bounded retries, dry-run, validation-only

LAST COMMIT BEFORE TASK 107:
0ccaaf2 feat(ui): redesign terminal as hybrid research workstation (TASK 112)

LAST REPORTED TEST RESULT:
360 passed, 5 pre-existing Pydantic deprecation warnings.

SAFE REFRESH:
python -m ml.preprocessing.refresh_pipeline
python -m ml.preprocessing.refresh_pipeline --dry-run
python -m ml.preprocessing.refresh_pipeline --validate-only
python -m ml.preprocessing.refresh_pipeline --list-generations
python -m ml.preprocessing.refresh_pipeline --rollback <run_id>

Legacy:
python -m ml.preprocessing.refresh_all
This is the old non-publishing path. Keep only for backward compatibility.

NEXT:
Review TASK 107. Do not start TASK 108 until accepted.

PRINCIPLES:
- No look-ahead bias.
- Preserve all valid price rows.
- No-news is not neutral sentiment.
- Detect duplicate join keys.
- Never publish a partial or failed refresh.
- Last known good generation must remain available.
- refreshed_at != data_as_of.
- No fabricated OHLCV, sentiment, or predictions.
- No Parquet artifacts or secrets in Git.
- Every task includes tests and review before commit.
