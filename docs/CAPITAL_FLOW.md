# Capital Flow Intelligence

Status: **ENGINE IMPLEMENTED — LIVE PROVIDER UNAVAILABLE**

## Scope

Capital flow supports both:

- market-wide flow;
- stock-level flow.

No provider is called automatically. The current build uses verified manual
imports only.

## Provider matrix

| Provider | Status | Reason |
|---|---|---|
| IDX official trading summaries | UNAVAILABLE | Licensing/redistribution not established |
| Broker/foreign-flow redistributor | UNAVAILABLE | No subscription/agreement |
| Verified owner-provided file | AVAILABLE IF INSTALLED | Manual import with provenance |

If no dataset is installed, APIs and reports return `UNAVAILABLE` with a
reason. They do not fabricate zero flows.

## Schema

One row per date/scope/ticker/investor-class/broker/revision:

- `date`
- `scope` (`market` or `stock`)
- `ticker` (required for stock)
- `investor_class` (`foreign`, `domestic_institutional`, `retail`, `broker`,
  `unknown`)
- `broker_code`
- `buy_value`, `sell_value`
- derived `net_value = buy - sell`, `gross_value = buy + sell`
- `currency`
- `published_at`, `available_at`
- `source_id`, `source_document`, `revision`

Template: `ml/capital_flow/templates/flow_observations_template.csv`.

Import:

```powershell
python -m ml.capital_flow.ingestion --input flows.csv
```

## Metrics

- Total buy, sell, net, gross.
- Net flow / gross flow.
- Flow by investor class.
- Flow by broker.
- Broker concentration (average top-1 and top-3 gross share).
- Positive/negative flow persistence.
- Price versus flow confirmation/divergence.

## Interpretation boundary

Exchange transactions have both buyers and sellers. Net flow is an analytical
aggregation, not proof of the identity or motive of any investor. Broker codes
identify intermediaries, not ultimate beneficial owners. The product must not
label flows as "smart money" without a defensible, licensed methodology.

Price-flow divergence is an observation, not a causal claim.
