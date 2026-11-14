"""Time-weighted and money-weighted portfolio performance."""

from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


def compute_twr(value_history: pd.DataFrame) -> Dict[str, object]:
    """Compute TWR with external contributions treated as start-of-day flows."""
    if value_history.empty:
        return {"status": "UNAVAILABLE", "reason": "No valuation history."}
    frame = value_history.sort_values("date").reset_index(drop=True)
    if frame["total_value"].isna().any():
        return {
            "status": "UNAVAILABLE",
            "reason": "Valuation history has missing prices.",
        }
    growth = 1.0
    periods = 0
    previous: Optional[float] = None
    for _, row in frame.iterrows():
        total = float(row["total_value"])
        flow = float(row.get("external_flow") or 0.0)
        if previous is None:
            previous = total
            continue
        base = previous + flow
        if base > 0:
            growth *= total / base
            periods += 1
        previous = total
    return {
        "status": "OK",
        "twr": growth - 1.0,
        "periods": periods,
        "formula": (
            "TWR = product(1 + subperiod_return) - 1; "
            "subperiod_return = end_value/(start_value + start_of_day_flow) - 1"
        ),
    }


def _xnpv(rate: float, flows: List[Tuple[date, float]]) -> float:
    start = min(day for day, _ in flows)
    return sum(
        amount / ((1.0 + rate) ** ((day - start).days / 365.0))
        for day, amount in flows
    )


def xirr(flows: Iterable[Tuple[date, float]]) -> Optional[float]:
    """Annualized money-weighted return using dated external cash flows."""
    items = sorted((day, float(amount)) for day, amount in flows)
    if not items or not any(amount < 0 for _, amount in items) or not any(
        amount > 0 for _, amount in items
    ):
        return None

    low, high = -0.9999, 10.0
    low_value = _xnpv(low, items)
    high_value = _xnpv(high, items)
    while low_value * high_value > 0 and high < 1_000:
        high *= 2.0
        high_value = _xnpv(high, items)
    if low_value * high_value > 0:
        return None

    for _ in range(200):
        mid = (low + high) / 2.0
        value = _xnpv(mid, items)
        if abs(value) < 1e-9:
            return mid
        if low_value * value <= 0:
            high = mid
            high_value = value
        else:
            low = mid
            low_value = value
    return (low + high) / 2.0


def build_money_weighted_flows(
    transactions: pd.DataFrame, ending_date: date, ending_value: float
) -> List[Tuple[date, float]]:
    flows: List[Tuple[date, float]] = []
    for _, row in transactions.iterrows():
        if row["side"] == "DEPOSIT":
            flows.append((row["trade_date"], -float(row["cash_amount"])))
        elif row["side"] == "WITHDRAWAL":
            flows.append((row["trade_date"], float(row["cash_amount"])))
    flows.append((ending_date, float(ending_value)))
    return flows


def performance_report(
    value_history: pd.DataFrame,
    transactions: pd.DataFrame,
    ending_value: Optional[float],
    ending_date: Optional[date] = None,
) -> Dict[str, object]:
    twr = compute_twr(value_history)
    if ending_value is None or ending_date is None:
        mwr = {
            "status": "UNAVAILABLE",
            "reason": "Ending value/date is unavailable.",
            "mwr": None,
        }
    else:
        flows = build_money_weighted_flows(transactions, ending_date, ending_value)
        rate = xirr(flows)
        mwr = {
            "status": "OK" if rate is not None else "UNAVAILABLE",
            "mwr": rate,
            "reason": None if rate is not None else "Cash flows do not bracket a root.",
            "formula": "XIRR: 0 = sum(CF_t / (1 + MWR)^(days/365))",
            "flows": [
                {"date": str(day), "amount": amount} for day, amount in flows
            ],
        }
    return {
        "twr": twr,
        "mwr": mwr,
        "difference_note": (
            "TWR removes the effect of external cash-flow timing; MWR (XIRR) "
            "reflects the investor's actual cash-flow timing and size."
        ),
    }
