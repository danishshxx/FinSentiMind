"""Macro dashboard and cross-asset context without causal claims."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from ml.macro.repository import MacroRepository
from ml.macro.schemas import CATALOG_BY_CODE, MACRO_CATALOG


def _indicator_frame(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    return {
        indicator: group.sort_values("date")
        for indicator, group in df.groupby("indicator")
    }


def _window_return(series: pd.Series, window: int) -> Optional[float]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) <= window:
        return None
    if values.iloc[-window - 1] == 0:
        return None
    return float(values.iloc[-1] / values.iloc[-window - 1] - 1.0)


def cross_asset_summary(
    repo: MacroRepository,
    as_of=None,
    windows=(1, 5, 20),
) -> Dict[str, Any]:
    status = repo.status()
    df = repo.observations(as_of=as_of)
    if df.empty:
        return {
            "status": "UNAVAILABLE",
            "provider": status,
            "reason": "No verified macro observations are available.",
            "indicators": [],
            "cross_asset_correlation": None,
            "causation": False,
        }

    frames = _indicator_frame(df)
    as_of_date = pd.Timestamp(as_of).date() if as_of else df["date"].max()
    indicators = []
    for catalog in MACRO_CATALOG:
        code = catalog["code"]
        group = frames.get(code)
        if group is None or group.empty:
            indicators.append({
                **catalog,
                "status": "unavailable",
                "reason": "No verified observation installed.",
            })
            continue
        series = group.set_index("date")["value"]
        latest_date = series.index.max()
        indicators.append({
            **catalog,
            "status": "available",
            "reason": None,
            "latest_date": str(latest_date),
            "value": float(series.loc[latest_date]),
            "freshness_days": int((as_of_date - latest_date).days),
            "returns": {
                f"{window}d": _window_return(series, window) for window in windows
            },
            "source_id": str(group.iloc[-1]["source_id"]),
            "source_document": str(group.iloc[-1]["source_document"]),
        })

    pivot = (
        df.pivot_table(index="date", columns="indicator", values="value", aggfunc="last")
        .sort_index()
    )
    returns = pivot.pct_change(fill_method=None).dropna(how="all")
    correlation = None
    if returns.shape[0] >= 2 and returns.shape[1] >= 2:
        correlation = (
            returns.corr()
            .round(6)
            .where(pd.notna(returns.corr()), None)
            .to_dict()
        )
    available = [item for item in indicators if item["status"] == "available"]
    return {
        "status": "available" if available else "unavailable",
        "provider": status,
        "coverage": repo.coverage(),
        "as_of": str(as_of_date),
        "indicators": indicators,
        "available_count": len(available),
        "total_count": len(indicators),
        "cross_asset_correlation": correlation,
        "causation": False,
        "currency_note": (
            "Values remain in their native units/currencies. Returns are "
            "unitless; no FX conversion is invented."
        ),
        "warning": (
            "Correlation is not causation. Macro relationships are context, "
            "not a trading signal."
        ),
    }


def sector_macro_correlations(
    sector_returns: pd.DataFrame,
    macro_returns: pd.DataFrame,
    min_observations: int = 10,
) -> Dict[str, Any]:
    """Correlate supplied sector and macro return series without causal claims."""
    if sector_returns.empty or macro_returns.empty:
        return {
            "status": "UNAVAILABLE",
            "reason": "Sector or macro return history is empty.",
        }
    sector = sector_returns.copy()
    macro = macro_returns.copy()
    sector["date"] = pd.to_datetime(sector["date"]).dt.date
    macro["date"] = pd.to_datetime(macro["date"]).dt.date
    merged = sector.merge(macro, on="date", how="inner", suffixes=("_sector", "_macro"))
    if merged.empty:
        return {"status": "UNAVAILABLE", "reason": "No overlapping dates."}
    results: List[Dict[str, Any]] = []
    for sector_name, group in merged.groupby("sector"):
        for macro_name in macro.columns:
            if macro_name == "date":
                continue
            return_col = "return_sector" if "return_sector" in group else "return"
            pair = group[[return_col, macro_name]].dropna()
            if len(pair) < min_observations:
                continue
            results.append({
                "sector": sector_name,
                "macro_indicator": macro_name,
                "observations": int(len(pair)),
                "correlation": float(pair.corr().iloc[0, 1]),
            })
    return {
        "status": "OK" if results else "UNAVAILABLE",
        "reason": None if results else "Insufficient overlapping observations.",
        "correlations": results,
        "causation": False,
        "warning": "Correlation is not causation.",
    }


def build_macro_dashboard(repo: MacroRepository, as_of=None) -> Dict[str, Any]:
    return {
        "summary": cross_asset_summary(repo, as_of=as_of),
        "provider_matrix": __import__(
            "ml.macro.providers", fromlist=["provider_matrix"]
        ).provider_matrix(),
    }
