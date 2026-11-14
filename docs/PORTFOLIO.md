# Portfolio Management

Status: **ENGINE IMPLEMENTED — MANUAL LEDGER ONLY**

Both personal and model portfolios use the same auditable transaction ledger.
There is no broker connection, order routing, or custody.

## Transaction types

`BUY`, `SELL`, `DIVIDEND`, `SPLIT`, `REVERSE_SPLIT`, `BONUS`, `RIGHTS`,
`DEPOSIT`, `WITHDRAWAL`, `FEE`, `TAX`.

Required semantics:

- `BUY` / `RIGHTS`: quantity and price required.
- `SELL`: cannot exceed held quantity; short selling is rejected.
- `DIVIDEND`: cash amount gross; taxes reduce cash.
- `SPLIT` / `REVERSE_SPLIT`: `ratio_to / ratio_from` is the quantity factor.
- `BONUS`: adds `held * ratio_to / ratio_from` shares at zero incremental cost.
- `DEPOSIT` / `WITHDRAWAL`: external capital flows.
- `FEE` / `TAX`: cash outflows.

## Cost basis

Policy: **FIFO**.

- Purchase fees and taxes are capitalized into the lot cost.
- Sale fees and taxes reduce proceeds.
- Partial sales consume the oldest lots first.
- Acquisition lots remain visible in the report.
- Dividends are tracked separately from realized P&L.
- Split/reverse-split preserve total cost basis and adjust lot quantity/cost per
  share.
- Bonus shares increase quantity and reduce average cost per share.

## Performance

Time-weighted return:

```text
TWR = product(1 + subperiod_return) - 1
subperiod_return = end_value / (start_value + start_of_day_external_flow) - 1
```

Money-weighted return:

```text
0 = sum(CF_t / (1 + MWR)^(days/365))
```

TWR removes the effect of external cash-flow timing. MWR/XIRR reflects the
investor's actual flow timing and size. Both are reported; neither is a future
return forecast.

## Data and limitations

- Prices come from the local dataset; a missing price makes total value
  unavailable rather than zero.
- TWR uses daily valuation history.
- XIRR needs at least one negative and one positive dated cash flow.
- Corporate-action tax treatment is not automatic.
- CSV import templates are in `ml/portfolio/templates/`.
- This is a research/tracking tool, not an order-management system.
