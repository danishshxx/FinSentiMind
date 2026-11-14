"""Capital-flow metrics and honest interpretation boundaries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from app.core.logging import get_logger
from ml.capital_flow.repository import CapitalFlowRepository


logger = get_logger(__name__)


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _sum_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[column, "buy_value", "sell_value", "net_value", "gross_value"])
    return (
        df.groupby(column, dropna=False)
        .agg(
            buy_value=("buy_value", "sum"),
            sell_value=("sell_value", "sum"),
            net_value=("net_value", "sum"),
            gross_value=("gross_value", "sum"),
        )
        .reset_index()
        .sort_values("net_value", ascending=False)
    )


def _concentration(df: pd.DataFrame) -> Optional[Dict[str, float]]:
    brokers = df[df["broker_code"].notna() & (df["gross_value"] > 0)]
    if brokers.empty:
        return None
    by_date = brokers.groupby(["date", "broker_code"])["gross_value"].sum().reset_index()
    top1 = []
    top3 = []
    for _, group in by_date.groupby("date"):
        total = group["gross_value"].sum()
        if total <= 0:
            continue
        ordered = group["gross_value"].sort_values(ascending=False)
        top1.append(float(ordered.iloc[0] / total))
        top3.append(float(ordered.head(3).sum() / total))
    if not top1:
        return None
    return {
        "average_top1_gross_share": sum(top1) / len(top1),
        "average_top3_gross_share": sum(top3) / len(top3),
        "sessions": len(top1),
    }


def _persistence(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"sessions": 0, "current_positive_streak": 0, "longest_positive_streak": 0,
                "longest_negative_streak": 0}
    daily = df.groupby("date")["net_value"].sum().sort_index()
    signs = [1 if value > 0 else -1 if value < 0 else 0 for value in daily]
    longest_pos = longest_neg = current_pos = current_neg = 0
    for sign in signs:
        if sign > 0:
            current_pos += 1
            current_neg = 0
        elif sign < 0:
            current_neg += 1
            current_pos = 0
        else:
            current_pos = current_neg = 0
        longest_pos = max(longest_pos, current_pos)
        longest_neg = max(longest_neg, current_neg)
    return {
        "sessions": int(len(daily)),
        "current_positive_streak": int(current_pos),
        "longest_positive_streak": int(longest_pos),
        "longest_negative_streak": int(longest_neg),
    }


def _price_divergence(df: pd.DataFrame, prices: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
    if prices is None or prices.empty or {"date", "close"}.difference(prices.columns):
        return None
    price = prices.copy()
    price["date"] = pd.to_datetime(price["date"]).dt.date
    price = price.sort_values("date")
    start = df["date"].min()
    end = df["date"].max()
    price = price[(price["date"] >= start) & (price["date"] <= end)]
    if len(price) < 2:
        return None
    first = float(price.iloc[0]["close"])
    last = float(price.iloc[-1]["close"])
    if first == 0:
        return None
    price_return = last / first - 1.0
    net_flow = float(df["net_value"].sum())
    if net_flow > 0 and price_return > 0:
        classification = "confirmation_accumulation"
    elif net_flow > 0 and price_return < 0:
        classification = "accumulation_divergence"
    elif net_flow < 0 and price_return > 0:
        classification = "distribution_divergence"
    elif net_flow < 0 and price_return < 0:
        classification = "confirmation_distribution"
    else:
        classification = "neutral"
    return {
        "start_date": str(start),
        "end_date": str(end),
        "price_return": price_return,
        "net_flow": net_flow,
        "classification": classification,
        "note": (
            "Divergence is an observation, not proof of intent. Exchange "
            "transactions always have both a buyer and a seller."
        ),
    }


def summarize_flows(
    df: pd.DataFrame, prices: Optional[pd.DataFrame] = None
) -> Dict[str, Any]:
    if df.empty:
        return {
            "status": "UNAVAILABLE",
            "reason": "No verified capital-flow observations are available.",
        }
    buy = float(df["buy_value"].sum())
    sell = float(df["sell_value"].sum())
    net = float(df["net_value"].sum())
    gross = float(df["gross_value"].sum())
    investors = _sum_by(df, "investor_class").to_dict(orient="records")
    brokers = _sum_by(
        df[df["broker_code"].notna()], "broker_code"
    ).to_dict(orient="records")
    return {
        "status": "OK",
        "rows": int(len(df)),
        "sessions": int(df["date"].nunique()),
        "first_date": str(df["date"].min()),
        "last_date": str(df["date"].max()),
        "total_buy_value": buy,
        "total_sell_value": sell,
        "net_value": net,
        "gross_value": gross,
        "net_to_gross": _safe_div(net, gross),
        "by_investor_class": investors,
        "by_broker": brokers,
        "broker_concentration": _concentration(df),
        "persistence": _persistence(df),
        "price_flow_divergence": _price_divergence(df, prices),
        "sources": sorted(df["source_id"].dropna().astype(str).unique()),
        "interpretation_boundary": (
            "Net flow is an analytical aggregation, not proof of investor "
            "identity or motive. Broker codes identify intermediaries, not "
            "ultimate beneficial owners. Do not label this 'smart money'."
        ),
    }


def build_market_report(
    repo: CapitalFlowRepository,
    as_of=None,
    prices: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    status = repo.status()
    df = repo.observations(scope="market", as_of=as_of)
    return {
        "scope": "market",
        "provider": status,
        "coverage": repo.coverage(),
        "summary": summarize_flows(df, prices),
    }


def build_stock_report(
    repo: CapitalFlowRepository,
    ticker: str,
    as_of=None,
    prices: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    status = repo.status()
    df = repo.observations(scope="stock", ticker=ticker, as_of=as_of)
    return {
        "scope": "stock",
        "ticker": ticker.upper(),
        "provider": status,
        "coverage": repo.coverage(),
        "summary": summarize_flows(df, prices),
    }
