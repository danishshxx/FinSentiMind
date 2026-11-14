# Fundamental Import Templates

These files contain headers only. They are intentionally empty: no financial
values are fabricated.

Import a verified source document:

```powershell
python -m ml.fundamentals.ingestion --type statements --input statements.csv
python -m ml.fundamentals.ingestion --type actions --input actions.csv
python -m ml.fundamentals.ingestion --type dividends --input dividends.csv
```

Rules:

- One row per reported line item.
- `value` is the value as printed in the source; `unit_scale` describes the
  printed unit (`ones`, `thousands`, `millions`, `billions`, `trillions`,
  `juta`, `miliar`, `triliun`).
- `available_at` is when the document first became available. It cannot precede
  `published_at`.
- For a correction, keep the prior row and import a new row with a higher
  `revision` and `restatement_of` pointing to the prior source/revision.
- Missing values stay blank/NULL; do not enter zero.
- Use `source_id`, `source_document`, and `source_page` to preserve provenance.
- Non-IDR values are scaled but **not** currency-converted; downstream metrics
  must not mix currencies.
