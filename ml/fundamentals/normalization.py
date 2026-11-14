"""Deterministic unit normalization for fundamental data.

Currency conversion is deliberately out of scope. If an issuer reports in a
non-IDR currency, the scale factor is still applied but the row is marked
``CURRENCY_PRESERVED_NOT_CONVERTED``. Metrics must not silently compare that
value with IDR values.
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from ml.fundamentals.schemas import (
    FINANCIAL_STATEMENT_COLUMNS,
    FundamentalSchemaError,
)


UNIT_MULTIPLIERS: Dict[str, float] = {
    "ones": 1.0,
    "thousands": 1_000.0,
    "millions": 1_000_000.0,
    "billions": 1_000_000_000.0,
    "trillions": 1_000_000_000_000.0,
    "juta": 1_000_000.0,
    "miliar": 1_000_000_000.0,
    "triliun": 1_000_000_000_000.0,
}

STATUS_IDR = "IDR_NORMALIZED"
STATUS_NON_IDR = "CURRENCY_PRESERVED_NOT_CONVERTED"


def normalize_financial_statements(df: pd.DataFrame) -> pd.DataFrame:
    """Apply unit scales without changing the reported currency."""
    out = df.copy()
    unknown = sorted(set(out["unit_scale"]) - set(UNIT_MULTIPLIERS))
    if unknown:
        raise FundamentalSchemaError(f"unsupported unit_scale values: {unknown}")

    out["original_value"] = out["value"]
    out["original_unit_scale"] = out["unit_scale"]
    out["value"] = (
        pd.to_numeric(out["value"], errors="coerce")
        * out["unit_scale"].map(UNIT_MULTIPLIERS)
    )
    out["normalization_status"] = STATUS_IDR
    out.loc[
        out["currency"] != "IDR", "normalization_status"
    ] = STATUS_NON_IDR

    for column in FINANCIAL_STATEMENT_COLUMNS:
        if column not in out.columns:
            out[column] = None
    return out[FINANCIAL_STATEMENT_COLUMNS].reset_index(drop=True)
