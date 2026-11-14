"""Capital-flow provider interfaces and explicit unavailable adapters.

No provider is called in this build. The manual adapter reads verified local
imports. A provider is never replaced with fabricated values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

import pandas as pd

from ml.capital_flow.repository import CapitalFlowRepository
from ml.capital_flow.schemas import FLOW_OBSERVATION_COLUMNS


def _empty() -> pd.DataFrame:
    return pd.DataFrame({
        column: pd.Series(dtype="object") for column in FLOW_OBSERVATION_COLUMNS
    })


class CapitalFlowProvider(ABC):
    @abstractmethod
    def capabilities(self) -> Dict[str, object]:
        raise NotImplementedError

    @abstractmethod
    def market_flow(self, as_of=None) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def stock_flow(self, ticker: str, as_of=None) -> pd.DataFrame:
        raise NotImplementedError


class UnavailableCapitalFlowProvider(CapitalFlowProvider):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def capabilities(self) -> Dict[str, object]:
        return {
            "status": "UNAVAILABLE",
            "reason": self.reason,
            "market_flow": False,
            "stock_flow": False,
        }

    def market_flow(self, as_of=None) -> pd.DataFrame:
        return _empty()

    def stock_flow(self, ticker: str, as_of=None) -> pd.DataFrame:
        return _empty()


class ManualCapitalFlowProvider(CapitalFlowProvider):
    def __init__(self, repository: CapitalFlowRepository) -> None:
        self.repository = repository

    def capabilities(self) -> Dict[str, object]:
        status = self.repository.status()
        return {
            **status,
            "market_flow": status["status"] == "AVAILABLE",
            "stock_flow": status["status"] == "AVAILABLE",
        }

    def market_flow(self, as_of=None) -> pd.DataFrame:
        return self.repository.observations(scope="market", as_of=as_of)

    def stock_flow(self, ticker: str, as_of=None) -> pd.DataFrame:
        return self.repository.observations(scope="stock", ticker=ticker, as_of=as_of)


def provider_matrix() -> List[Dict[str, object]]:
    """Document source capabilities without implying licensed access."""
    return [
        {
            "provider": "IDX official trading summaries",
            "status": "UNAVAILABLE",
            "reason": "Licensing/terms and redistribution rights not established.",
            "manual_import_supported": True,
        },
        {
            "provider": "Broker/foreign-flow redistributor",
            "status": "UNAVAILABLE",
            "reason": "No subscription or redistribution agreement.",
            "manual_import_supported": True,
        },
        {
            "provider": "Verified owner-provided file",
            "status": "AVAILABLE_IF_INSTALLED",
            "reason": "Manual import with source provenance.",
            "manual_import_supported": True,
        },
    ]
