"""Macro provider interfaces. Automated access is disabled by default."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import pandas as pd

from ml.macro.repository import MacroRepository
from ml.macro.schemas import CATALOG_BY_CODE, MACRO_OBSERVATION_COLUMNS


def _empty() -> pd.DataFrame:
    return pd.DataFrame({
        column: pd.Series(dtype="object") for column in MACRO_OBSERVATION_COLUMNS
    })


class MacroHistoryProvider(ABC):
    @abstractmethod
    def capabilities(self) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def history(self, indicator: str, as_of=None) -> pd.DataFrame:
        raise NotImplementedError


class UnavailableMacroHistoryProvider(MacroHistoryProvider):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def capabilities(self) -> Dict[str, object]:
        return {"status": "UNAVAILABLE", "reason": self.reason}

    def history(self, indicator: str, as_of=None) -> pd.DataFrame:
        return _empty()


class ManualMacroProvider(MacroHistoryProvider):
    def __init__(self, repository: MacroRepository) -> None:
        self.repository = repository

    def capabilities(self) -> Dict[str, object]:
        return self.repository.status()

    def history(self, indicator: str, as_of=None) -> pd.DataFrame:
        return self.repository.observations(indicator=indicator, as_of=as_of)


class YFinanceMacroProvider(MacroHistoryProvider):
    """Optional adapter for licensed use. Never enabled implicitly."""

    def __init__(self, repository: Optional[MacroRepository] = None) -> None:
        self.repository = repository
        self.source = "yfinance"

    def capabilities(self) -> Dict[str, object]:
        return {
            "status": "AVAILABLE_IF_ENABLED",
            "reason": (
                "Disabled by default. Set FINSENTIMIND_ENABLE_MACRO_YFINANCE=1 "
                "only after confirming licensing and redistribution rights."
            ),
        }

    def history(self, indicator: str, as_of=None) -> pd.DataFrame:
        catalog = CATALOG_BY_CODE.get(indicator.upper())
        if not catalog or not catalog.get("symbol"):
            return _empty()
        import yfinance as yf

        history = yf.Ticker(catalog["symbol"]).history(period="10y", auto_adjust=True)
        if history is None or history.empty:
            return _empty()
        history = history.dropna(subset=["Close"])
        rows = []
        for index, row in history.iterrows():
            stamp = pd.Timestamp(index)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            rows.append({
                "indicator": indicator.upper(),
                "date": stamp.date(),
                "value": float(row["Close"]),
                "unit": catalog["unit"],
                "currency": catalog["currency"],
                "published_at": stamp.tz_convert("UTC"),
                "available_at": stamp.tz_convert("UTC"),
                "source_id": "yfinance-read-only",
                "source_document": catalog["symbol"],
                "revision": 0,
            })
        frame = pd.DataFrame(rows)
        return frame


def build_providers(repository: Optional[MacroRepository] = None) -> List[MacroHistoryProvider]:
    repository = repository or MacroRepository()
    enabled = os.getenv("FINSENTIMIND_ENABLE_MACRO_YFINANCE", "").strip() == "1"
    providers: List[MacroHistoryProvider] = [ManualMacroProvider(repository)]
    if enabled:
        providers.append(YFinanceMacroProvider(repository))
    return providers


def provider_matrix() -> List[Dict[str, object]]:
    return [
        {
            "provider": "Verified manual import",
            "status": "AVAILABLE_IF_INSTALLED",
            "redistribution": "Owner responsibility",
            "notes": "CSV/JSON with source document and availability timestamps.",
        },
        {
            "provider": "yfinance",
            "status": "DISABLED_BY_DEFAULT",
            "redistribution": "Verify terms before production",
            "notes": "Only indices/FX/gold with configured symbols; no speculative commodity proxies.",
        },
        {
            "provider": "IDX / licensed market data",
            "status": "UNAVAILABLE",
            "redistribution": "Requires agreement",
            "notes": "Not integrated.",
        },
    ]
