PROJECT: FinSentiMind
POSITIONING: AI-Powered Equity Research & Quantitative Intelligence Terminal
             for the Indonesian Market

WORKFLOW:
Agentic engineering = implementasi repository.
ChatGPT = architecture reviewer, quant mentor, final reviewer.
Owner = product decision-maker.

BRANCH:
feat/finsentimind-completion
Based on verified TASK 107 branch (683ca7b) and TASK 112 (0ccaaf2).

COMMITS:
a5834db fix(data): harden refresh audit findings (TASK 107 audit)
cee7f9a feat(data): add auditable instrument master layer (TASK 108)
5a0846b feat(quant): add leakage-safe quant feature engine (TASK 109)
5be5600 feat(market): add market intelligence and regime context (TASK 110)
dea96bf feat(research): add leakage-safe ML research pipeline (TASK 111)
Final integration commit: see Git log / final report.

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
TASK 107: Safe versioned dataset refresh + publishing
TASK 108: Auditable IDX instrument master data
TASK 109: Deterministic quant feature engine v1
TASK 110: Market intelligence, regime, sector context, macro adapters
TASK 111: Leakage-safe ML research pipeline (NOT VALIDATED)

LAST REPORTED TEST RESULT:
398 passed, 5 pre-existing Pydantic deprecation warnings.

DATASET SNAPSHOT:
data_as_of = 2026-10-09
30 tickers, 14,405 base rows, 8 sentiment rows.
Full external provider refresh was NOT executed in this sprint.
Active dataset is still the legacy flat dataset; no active generation pointer
was published.

SAFE REFRESH:
python -m ml.preprocessing.refresh_pipeline
python -m ml.preprocessing.refresh_pipeline --dry-run
python -m ml.preprocessing.refresh_pipeline --validate-only
python -m ml.preprocessing.refresh_pipeline --list-generations
python -m ml.preprocessing.refresh_pipeline --rollback <run_id>

Real dry-run verified:
- all derived stages ok,
- quant_features stage ok,
- validation PASS 35 / WARN 2 / FAIL 0,
- no provider mutation and no publication.

DERIVED COMMANDS:
python -m ml.metadata.master
python -m ml.features.quant
python -m ml.research.ml_pipeline

ML RESEARCH:
Status: NOT_VALIDATED
ROC-AUC: 0.5208 vs dummy 0.5000
Strategy Sharpe: -0.887 vs universe -1.045
Cumulative strategy return: -32.60%
Do not present as a trusted signal. No live trading.

NEXT:
Final acceptance review by owner/reviewer.
Do not start additional modules until TASK 107-111 are accepted.

PRINCIPLES:
- No look-ahead bias.
- No fabricated OHLCV, sentiment, metadata, macro, or predictions.
- Missing values remain missing; no NULL-to-zero conversion.
- Last known good dataset must survive failed refresh.
- refreshed_at != data_as_of.
- Every financial calculation and model has documented methodology.
- All new functionality has tests.
- No Parquet artifacts, .env, or secrets in Git.
