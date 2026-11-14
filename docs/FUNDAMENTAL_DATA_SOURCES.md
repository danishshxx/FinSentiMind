# Fundamental Data Sources and Coverage

Status: **MANUAL IMPORT ONLY — NO AUTOMATED FUNDAMENTAL PROVIDER INTEGRATED**

FinSentiMind does not fabricate fundamentals and does not scrape restricted
sources. The TASK 113 layer accepts verified owner-provided files with
provenance.

## Source inventory

| Source | Authority | Access status | Intended use |
|---|---|---|---|
| IDX official disclosures / issuer announcements | Exchange/issuer | Manual, subject to terms | Primary statements and corporate actions |
| Company annual/interim reports | Issuer | Manual, subject to terms | Statement line items, audit context |
| Authorized redistributors | Licensed vendor | Not subscribed | Licensed normalized data |
| Licensed financial data providers | Commercial vendor | Not subscribed | Licensed history and estimates |
| Owner-provided documents | Owner | Allowed when lawfully obtained | Manual import with source metadata |
| Yahoo Finance / Supabase price tables | Market data only | Existing | Prices/news only; **not** fundamentals |

## What is implemented

- Long-form financial statement schema.
- Unit-scale normalization with original values preserved.
- Provenance fields: `source_id`, `source_document`, `source_page`.
- Point-in-time fields: `published_at`, `available_at`.
- Restatement/revision history.
- Corporate-action and dividend schemas.
- Quality checks for duplicates, mixed units, mixed currency, invalid periods,
  and availability-before-publication.
- Point-in-time repository selecting the latest revision available at the query
  time.
- Manual CSV/JSON ingestion.

## What remains unavailable

- Automated official IDX fundamental ingestion.
- Licensed consensus estimates.
- Automated FX conversion between reported currencies.
- Automatic maintenance capex classification.
- Any claim of complete IDX fundamental coverage.

## Import contract

Templates:

- `ml/fundamentals/templates/financial_statements_template.csv`
- `ml/fundamentals/templates/corporate_actions_template.csv`
- `ml/fundamentals/templates/dividend_history_template.csv`

Commands:

```powershell
python -m ml.fundamentals.ingestion --type statements --input statements.csv
python -m ml.fundamentals.ingestion --type actions --input actions.csv
python -m ml.fundamentals.ingestion --type dividends --input dividends.csv
python -m ml.fundamentals.quality
```

Rules:

- One row per line item.
- Missing values remain NULL.
- Do not mix currencies in a comparison without an explicit FX source.
- Bump `revision` and use `restatement_of` for corrections.
- Preserve the source document and page.

## Legal / licensing boundary

The source matrix is an engineering inventory, not a legal opinion. Redistribution
rights, exchange licensing, and commercial terms must be reviewed before any
public or commercial use.
