"""Explainable fundamental ratios and growth metrics.

Every metric returns a formula, the exact inputs used, a status, and a
limitation note. Missing inputs produce ``UNAVAILABLE``; they are never
silently replaced with zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd


OK = "OK"
UNAVAILABLE = "UNAVAILABLE"

LINE_ITEM_ALIASES: Dict[str, set[str]] = {
    "revenue": {
        "revenue", "total_revenue", "net_revenue", "sales", "net_sales",
        "pendapatan", "pendapatan_usaha",
    },
    "gross_profit": {"gross_profit", "laba_kotor"},
    "operating_income": {
        "operating_income", "operating_profit", "ebit", "laba_usaha",
    },
    "net_income": {
        "net_income", "net_profit", "profit_for_the_period", "laba_bersih",
    },
    "pretax_income": {"pretax_income", "income_before_tax", "profit_before_tax"},
    "income_tax": {"income_tax", "tax_expense", "beban_pajak"},
    "interest_expense": {
        "interest_expense", "finance_costs", "finance_cost", "beban_bunga",
    },
    "depreciation_amortization": {
        "depreciation_amortization", "depreciation_and_amortization", "da",
        "depreciation", "amortization",
    },
    "ebitda": {"ebitda"},
    "operating_cash_flow": {
        "operating_cash_flow", "cfo", "cash_from_operations",
        "net_cash_from_operating_activities", "arus_kas_operasi",
    },
    "capex": {
        "capex", "capital_expenditure", "capital_expenditures",
        "purchase_of_property_plant_and_equipment", "belanja_modal",
    },
    "free_cash_flow": {"free_cash_flow", "fcf"},
    "total_assets": {"total_assets", "assets", "total_aset"},
    "total_equity": {
        "total_equity", "shareholders_equity", "equity", "total_ekuitas",
    },
    "total_debt": {
        "total_debt", "interest_bearing_debt", "total_liabilities_debt",
    },
    "cash": {
        "cash", "cash_and_equivalents", "cash_and_cash_equivalents",
        "kas_dan_setara_kas",
    },
    "current_assets": {"current_assets", "aset_lancar"},
    "current_liabilities": {"current_liabilities", "liabilitas_jangka_pendek"},
    "inventory": {"inventory", "inventories", "persediaan"},
    "receivables": {"receivables", "accounts_receivable", "piutang"},
    "payables": {"payables", "accounts_payable", "utang_usaha"},
    "shares_outstanding": {
        "shares_outstanding", "diluted_shares", "diluted_shares_outstanding",
        "weighted_average_shares", "jumlah_saham",
    },
    "eps": {"eps", "earnings_per_share", "diluted_eps", "laba_per_saham"},
    "book_value_per_share": {"book_value_per_share", "bvps"},
    "dividend_per_share": {"dividend_per_share", "dps"},
}


def _normalize_name(value: Any) -> str:
    return (
        str(value).strip().lower()
        .replace(" ", "_").replace("-", "_").replace("/", "_")
        .replace("(", "").replace(")", "").replace(".", "")
    )


def canonical_line_item(value: Any) -> str:
    normalized = _normalize_name(value)
    for canonical, aliases in LINE_ITEM_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def map_line_items(df: pd.DataFrame) -> Dict[str, float]:
    if df.empty or "line_item" not in df.columns:
        return {}
    values: Dict[str, float] = {}
    for _, row in df.iterrows():
        key = canonical_line_item(row["line_item"])
        value = row.get("value")
        if value is None or pd.isna(value):
            continue
        values[key] = float(value)
    return values


@dataclass
class MetricResult:
    name: str
    value: Optional[float]
    unit: str
    formula: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    status: str = OK
    limitation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "formula": self.formula,
            "inputs": self.inputs,
            "status": self.status,
            "limitation": self.limitation,
        }


def _unavailable(name: str, unit: str, formula: str, reason: str) -> MetricResult:
    return MetricResult(
        name=name,
        value=None,
        unit=unit,
        formula=formula,
        status=UNAVAILABLE,
        limitation=reason,
    )


def _ratio(
    name: str,
    numerator: Optional[float],
    denominator: Optional[float],
    unit: str,
    formula: str,
    inputs: Dict[str, Any],
    limitation: str,
) -> MetricResult:
    if numerator is None or denominator is None:
        return _unavailable(name, unit, formula, "Missing numerator or denominator.")
    if denominator == 0:
        return _unavailable(name, unit, formula, "Denominator is zero.")
    return MetricResult(
        name=name, value=numerator / denominator, unit=unit,
        formula=formula, inputs=inputs, limitation=limitation,
    )


def _growth(
    name: str,
    current: Optional[float],
    previous: Optional[float],
    formula: str,
    inputs: Dict[str, Any],
) -> MetricResult:
    if current is None or previous is None:
        return _unavailable(name, "ratio", formula, "Missing current or prior value.")
    if previous == 0:
        return _unavailable(name, "ratio", formula, "Prior value is zero.")
    return MetricResult(
        name=name, value=(current - previous) / abs(previous), unit="ratio",
        formula=formula, inputs=inputs,
        limitation="Growth from a small or negative base can be unstable.",
    )


def _average(current: Optional[float], previous: Optional[float]) -> tuple[Optional[float], str]:
    if current is None:
        return None, "unavailable"
    if previous is None:
        return current, "ending balance (prior period unavailable)"
    return (current + previous) / 2.0, "average of current and prior period"


def _fcf(values: Dict[str, float]) -> Optional[float]:
    if "free_cash_flow" in values:
        return values["free_cash_flow"]
    if "operating_cash_flow" in values and "capex" in values:
        return values["operating_cash_flow"] - abs(values["capex"])
    return None


def _ebitda(values: Dict[str, float]) -> Optional[float]:
    if "ebitda" in values:
        return values["ebitda"]
    if "operating_income" in values and "depreciation_amortization" in values:
        return values["operating_income"] + values["depreciation_amortization"]
    return None


def compute_metrics(
    current: Dict[str, float],
    previous: Optional[Dict[str, float]] = None,
    market_price: Optional[float] = None,
    shares_outstanding: Optional[float] = None,
    dividend_per_share: Optional[float] = None,
    tax_rate: Optional[float] = None,
) -> Dict[str, MetricResult]:
    """Compute the TASK 114 ratio set from canonical line-item values."""
    previous = previous or {}
    shares = shares_outstanding or current.get("shares_outstanding")
    metrics: Dict[str, MetricResult] = {}

    revenue = current.get("revenue")
    gross_profit = current.get("gross_profit")
    operating_income = current.get("operating_income")
    net_income = current.get("net_income")
    total_assets = current.get("total_assets")
    total_equity = current.get("total_equity")
    total_debt = current.get("total_debt")
    cash = current.get("cash")
    interest = current.get("interest_expense")
    cfo = current.get("operating_cash_flow")
    fcf = _fcf(current)
    ebitda = _ebitda(current)

    metrics["gross_margin"] = _ratio(
        "gross_margin", gross_profit, revenue, "ratio",
        "gross_profit / revenue",
        {"gross_profit": gross_profit, "revenue": revenue},
        "Depends on how the issuer classifies cost of revenue.",
    )
    metrics["operating_margin"] = _ratio(
        "operating_margin", operating_income, revenue, "ratio",
        "operating_income / revenue",
        {"operating_income": operating_income, "revenue": revenue},
        "Operating income definitions can differ across issuers.",
    )
    metrics["net_margin"] = _ratio(
        "net_margin", net_income, revenue, "ratio",
        "net_income / revenue",
        {"net_income": net_income, "revenue": revenue},
        "Net income includes non-operating items.",
    )

    avg_equity, equity_basis = _average(total_equity, previous.get("total_equity"))
    metrics["return_on_equity"] = _ratio(
        "return_on_equity", net_income, avg_equity, "ratio",
        "net_income / average_total_equity",
        {"net_income": net_income, "average_total_equity": avg_equity,
         "basis": equity_basis},
        f"Equity basis: {equity_basis}.",
    )
    avg_assets, asset_basis = _average(total_assets, previous.get("total_assets"))
    metrics["return_on_assets"] = _ratio(
        "return_on_assets", net_income, avg_assets, "ratio",
        "net_income / average_total_assets",
        {"net_income": net_income, "average_total_assets": avg_assets,
         "basis": asset_basis},
        f"Asset basis: {asset_basis}.",
    )

    nopat = None
    invested_capital = None
    if operating_income is not None and tax_rate is not None:
        nopat = operating_income * (1.0 - tax_rate)
    if total_debt is not None and total_equity is not None and cash is not None:
        invested_capital = total_debt + total_equity - cash
    metrics["return_on_invested_capital"] = _ratio(
        "return_on_invested_capital", nopat, invested_capital, "ratio",
        "NOPAT / invested_capital; NOPAT = operating_income * (1 - tax_rate); "
        "invested_capital = total_debt + total_equity - cash",
        {"nopat": nopat, "invested_capital": invested_capital,
         "tax_rate": tax_rate},
        "Requires an explicit tax-rate assumption and debt/equity/cash inputs.",
    )

    for name, current_key in (
        ("revenue_growth", "revenue"),
        ("earnings_growth", "net_income"),
    ):
        metrics[name] = _growth(
            name, current.get(current_key), previous.get(current_key),
            f"({current_key}_current - {current_key}_prior) / abs({current_key}_prior)",
            {f"{current_key}_current": current.get(current_key),
             f"{current_key}_prior": previous.get(current_key)},
        )
    eps = current.get("eps")
    if eps is None and net_income is not None and shares:
        eps = net_income / shares
    metrics["eps_growth"] = _growth(
        "eps_growth", eps, previous.get("eps"),
        "(EPS_current - EPS_prior) / abs(EPS_prior)",
        {"eps_current": eps, "eps_prior": previous.get("eps")},
    )
    metrics["fcf_growth"] = _growth(
        "fcf_growth", fcf, _fcf(previous),
        "(FCF_current - FCF_prior) / abs(FCF_prior)",
        {"fcf_current": fcf, "fcf_prior": _fcf(previous)},
    )

    metrics["debt_to_equity"] = _ratio(
        "debt_to_equity", total_debt, total_equity, "ratio",
        "total_debt / total_equity",
        {"total_debt": total_debt, "total_equity": total_equity},
        "Debt definition must match the source statement.",
    )
    net_debt = (
        total_debt - cash
        if total_debt is not None and cash is not None else None
    )
    metrics["net_debt"] = MetricResult(
        name="net_debt", value=net_debt, unit="currency",
        formula="total_debt - cash",
        inputs={"total_debt": total_debt, "cash": cash},
        status=OK if net_debt is not None else UNAVAILABLE,
        limitation="" if net_debt is not None else "Missing debt or cash.",
    )
    metrics["net_debt_to_ebitda"] = _ratio(
        "net_debt_to_ebitda", net_debt, ebitda, "ratio",
        "net_debt / EBITDA; EBITDA = operating_income + D&A when not reported",
        {"net_debt": net_debt, "ebitda": ebitda},
        "EBITDA is not a cash-flow measure.",
    )
    metrics["interest_coverage"] = _ratio(
        "interest_coverage", operating_income, interest, "ratio",
        "operating_income / interest_expense",
        {"operating_income": operating_income, "interest_expense": interest},
        "A missing interest expense makes coverage unavailable.",
    )

    metrics["operating_cash_flow"] = MetricResult(
        name="operating_cash_flow", value=cfo, unit="currency",
        formula="reported cash flow from operations",
        inputs={"operating_cash_flow": cfo},
        status=OK if cfo is not None else UNAVAILABLE,
        limitation="" if cfo is not None else "CFO is not reported.",
    )
    metrics["free_cash_flow"] = MetricResult(
        name="free_cash_flow", value=fcf, unit="currency",
        formula="CFO - absolute capex, unless FCF is directly reported",
        inputs={"operating_cash_flow": cfo, "capex": current.get("capex"),
                "reported_fcf": current.get("free_cash_flow")},
        status=OK if fcf is not None else UNAVAILABLE,
        limitation="" if fcf is not None else "CFO or capex is missing.",
    )
    metrics["cash_conversion"] = _ratio(
        "cash_conversion", cfo, net_income, "ratio",
        "operating_cash_flow / net_income",
        {"operating_cash_flow": cfo, "net_income": net_income},
        "Unstable when net income is near zero or negative.",
    )

    market_cap = market_price * shares if market_price and shares else None
    metrics["earnings_per_share"] = MetricResult(
        name="earnings_per_share", value=eps, unit="currency_per_share",
        formula="reported EPS, or net_income / shares_outstanding",
        inputs={"eps": current.get("eps"), "net_income": net_income,
                "shares_outstanding": shares},
        status=OK if eps is not None else UNAVAILABLE,
        limitation="" if eps is not None else "EPS and shares are missing.",
    )
    metrics["price_to_earnings"] = _ratio(
        "price_to_earnings", market_price, eps, "ratio",
        "market_price / EPS",
        {"market_price": market_price, "eps": eps},
        "Negative earnings make P/E not meaningful.",
    )
    book_value_per_share = current.get("book_value_per_share")
    if book_value_per_share is None and total_equity is not None and shares:
        book_value_per_share = total_equity / shares
    metrics["price_to_book"] = _ratio(
        "price_to_book", market_price, book_value_per_share, "ratio",
        "market_price / book_value_per_share",
        {"market_price": market_price, "book_value_per_share": book_value_per_share},
        "Book value is an accounting value, not a market value.",
    )
    enterprise_value = (
        market_cap + net_debt
        if market_cap is not None and net_debt is not None else None
    )
    metrics["enterprise_value"] = MetricResult(
        name="enterprise_value", value=enterprise_value, unit="currency",
        formula="market_cap + net_debt",
        inputs={"market_cap": market_cap, "net_debt": net_debt},
        status=OK if enterprise_value is not None else UNAVAILABLE,
        limitation="" if enterprise_value is not None else "Missing market cap or net debt.",
    )
    metrics["ev_to_ebitda"] = _ratio(
        "ev_to_ebitda", enterprise_value, ebitda, "ratio",
        "enterprise_value / EBITDA",
        {"enterprise_value": enterprise_value, "ebitda": ebitda},
        "Sensitive to EBITDA definition and lease treatment.",
    )
    metrics["price_to_sales"] = _ratio(
        "price_to_sales", market_cap, revenue, "ratio",
        "market_cap / revenue",
        {"market_cap": market_cap, "revenue": revenue},
        "Revenue is not profit.",
    )
    metrics["fcf_yield"] = _ratio(
        "fcf_yield", fcf, market_cap, "ratio",
        "free_cash_flow / market_cap",
        {"free_cash_flow": fcf, "market_cap": market_cap},
        "One-year FCF can be distorted by working capital.",
    )
    metrics["dividend_yield"] = _ratio(
        "dividend_yield", dividend_per_share, market_price, "ratio",
        "dividend_per_share / market_price",
        {"dividend_per_share": dividend_per_share, "market_price": market_price},
        "Trailing dividend yield is not a forecast.",
    )
    return metrics


def metrics_to_dict(metrics: Dict[str, MetricResult]) -> Dict[str, Any]:
    return {name: metric.to_dict() for name, metric in metrics.items()}


def summarize_available(metrics: Dict[str, MetricResult]) -> Dict[str, Any]:
    return {
        "available": sorted(name for name, metric in metrics.items() if metric.status == OK),
        "unavailable": sorted(
            name for name, metric in metrics.items() if metric.status != OK
        ),
    }
