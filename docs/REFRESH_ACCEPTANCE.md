# Refresh Acceptance — TASK 107B

Status: **LOCAL/DRY-RUN ACCEPTED — EXTERNAL PUBLICATION PENDING OWNER APPROVAL**

No provider write, Supabase mutation, generation publication, or
`active.json` change was performed in this acceptance run.

## Verified run

| Field | Value |
|---|---|
| Run ID | `task107b_acceptance_20261114` |
| Mode | `--dry-run` |
| Status | `DRY_RUN` |
| Published | `false` |
| Data as of | `2026-11-13` |
| Tickers | 30 |
| Clean price rows | 15,155 |
| Daily sentiment rows | 8 |
| Base dataset rows | 15,155 |
| Quant feature rows | 15,155 |
| Validation | PASS 36 / WARN 1 / FAIL 0 |
| Pending future news | 12 |
| Quarantined news | 0 |
| Active generation pointer | absent |
| Prior failed staging | preserved |

The single WARN is sparse sentiment coverage (8 of 15,155 rows), which is a
research-readiness limitation and not a structural failure.

## Previous failure re-check

The failed run `20261114T154144Z_acedb3bb` is now explained by future-session
handling. Re-running only the sentiment classification against its verified
calendar produces:

- MAPPED: 26
- PENDING_FUTURE_SESSION: 12
- QUARANTINED_UNRESOLVABLE: 0

The pending articles are written to
`ml/reports/pending_news_20261114T154144Z_acedb3bb.json` with article IDs and
timestamps. No article is mapped to the previous session.

## Stage results

| Stage | Result |
|---|---|
| Price ingestion | skipped by dry-run (no mutation) |
| Price cleaning | ok |
| Quality gate | ok, 0 ERROR / 0 WARNING |
| Trading calendar | ok, 506 sessions through 2026-11-13 |
| News ingestion | skipped by dry-run |
| Sentiment processing | skipped by dry-run |
| Sentiment aggregation | ok with 12 pending articles |
| Base dataset | ok |
| Quant features | ok |
| Validation | ok |
| Publication | not attempted |

## Reader and rollback verification

- `--validate-only` reads the legacy flat dataset successfully:
  14,405 rows, data as of `2026-10-09`.
- `--list-generations` returns `[]`; no unpublished generation was exposed.
- Rollback semantics and checksum verification remain covered by automated
  tests; there is no generation to roll back to in this project state.

## Publication gate

Before a real run may proceed, the owner must approve:

1. Provider ingestion writes (price/news/sentiment).
2. Creation of an immutable generation under `ml/data/generations/`.
3. Atomic replacement of `ml/data/active.json`.

Safe control plan after approval:

1. Confirm no other refresh holds `ml/data/refresh.lock`.
2. Preserve the current flat dataset and all staging directories.
3. Run the full refresh once.
4. Verify quality, calendar, pending-news report, sentiment, base dataset,
   quant features, validation, manifest, and checksums.
5. Publish only if validation has zero FAIL.
6. Restart uvicorn so readers load the new generation.
7. Keep the previous dataset available for rollback.

## Evidence files

- `ml/reports/refresh_task107b_acceptance_20261114.json`
- `ml/reports/refresh_task107b_validate_20261114.json`
- `ml/reports/validation_task107b_acceptance_20261114.md`
- `ml/reports/quality_gate_task107b_acceptance_20261114.md`
- `ml/reports/pending_news_task107b_acceptance_20261114.json`
- `ml/data/staging/task107b_acceptance_20261114/`

These are generated artifacts under ignored directories; this document is the
tracked acceptance summary.
