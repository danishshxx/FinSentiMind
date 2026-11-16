# FinSentiMind — Data Refresh Runbook

All commands run locally from the repository root with the project virtualenv.
No command writes to Supabase, publishes to a remote service, or merges `main`.

## 1. Audit a staging generation

```bat
venv\Scripts\python -m ml.validation.generation_audit ^
  --dir ml\data\staging\task107b_calendar_reconciliation_20261115 ^
  --expected-calendar-version IDX-2024-2026-v2 ^
  --report-json ml\reports\generation_acceptance.json ^
  --report-md ml\reports\generation_acceptance.md
```

Exit code `0` means no FAIL. `WARN` is allowed but must be reviewed.

## 2. Stage and publish a generation

```bat
venv\Scripts\python -m ml.ops.publish_dataset ^
  --source ml\data\staging\task107b_calendar_reconciliation_20261115 ^
  --data-root ml\data ^
  --run-id idx30_calendar_v2_20261116 ^
  --expected-calendar-version IDX-2024-2026-v2 ^
  --universe-scope IDX30 ^
  --feature-version quant_features_v1
```

The source staging directory is copied, not moved. A provenance sidecar is
written into the new staging generation before publication.

## 3. Verify the active generation

```bat
venv\Scripts\python -c "from ml.preprocessing.dataset_store import resolve_active_dataset; a=resolve_active_dataset(r'ml\data', verify_hashes=True); print(a.source, a.run_id, a.active_dir)"
```

```bat
venv\Scripts\python -c "from app.services.market_data import MarketDataRepository; h=MarketDataRepository().data_health(freshness_threshold_days=7); print(h['status'], h['summary'])"
```

## 4. Rollback

Rollback only works to a generation whose checksums verify:

```bat
venv\Scripts\python -c "from ml.preprocessing.dataset_store import rollback_to_generation; rollback_to_generation(r'ml\data', 'idx30_calendar_v2_20261116')"
```

A tampered generation is refused and the current active pointer is preserved.

## 5. Downstream revalidation

```bat
venv\Scripts\python -m ml.ops.revalidate_downstream
```

Reports quant-feature equality, market-intelligence status, and the availability
of fundamentals, capital flow, macro, portfolio and ML research. Missing sources
are reported `UNAVAILABLE`; nothing is fabricated.

## 6. Universe EOD refresh (local staging only)

```bat
venv\Scripts\python -m ml.universe.refresh --end 2026-11-16 --history-start 2024-10-07
```

Stages:

1. universe inventory synchronization
2. incremental yfinance EOD ingestion (atomic writes)
3. coverage matrix against the authoritative calendar

It never touches `active.json` and never writes to Supabase. A provider failure
marks the run `PARTIAL` and preserves the previous normalized price file.

Incremental re-runs skip up-to-date tickers. Use `--full` on `yfinance_eod` only
when a deliberate full re-download is intended.

## 7. Tests

```bat
venv\Scripts\python -m pytest -q
node --check frontend/app.js
git diff --check
```

## 8. Failure recovery

| Failure | Action |
|---|---|
| Audit FAIL | Do not publish. Fix the staging generation and re-run the audit. |
| Publication raises "already exists" | Use a new `run_id`; previous active generation is unchanged. |
| Reader checksum mismatch | `active.json` is ignored; restore/rollback to a verified generation. |
| yfinance provider error | Run remains `PARTIAL`; re-run later. Existing normalized data is preserved. |
| Missing fundamentals / flow / macro | Expected in this build; reported `UNAVAILABLE`. |

## 9. Preservation rules

- Never delete `ml/data/generations/*`.
- Never delete historical staging directories.
- Never commit `ml/data/` or `ml/reports/` (both are gitignored).
- Never commit `.env` or credentials.
