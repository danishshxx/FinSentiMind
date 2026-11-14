"""Manual import for verified capital-flow observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.capital_flow.repository import DEFAULT_FLOW_PATH
from ml.capital_flow.schemas import (
    CapitalFlowSchemaError,
    FLOW_KEYS,
    FLOW_OBSERVATION_COLUMNS,
    validate_flow_observations,
)
from ml.fundamentals.ingestion import write_parquet_atomic


logger = get_logger(__name__)


def load_import(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Capital-flow import not found: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in (".json", ".jsonl"):
        return pd.read_json(path, lines=path.suffix.lower() == ".jsonl")
    raise CapitalFlowSchemaError("Use CSV or JSON for capital-flow imports.")


def ingest_flow_observations(
    input_path: Path,
    output_path: Path = DEFAULT_FLOW_PATH,
    append: bool = True,
) -> pd.DataFrame:
    incoming = validate_flow_observations(load_import(input_path))
    existing = (
        pd.read_parquet(output_path)
        if append and output_path.exists()
        else pd.DataFrame(columns=FLOW_OBSERVATION_COLUMNS)
    )
    combined = (
        pd.concat([existing, incoming], ignore_index=True)
        if not existing.empty else incoming
    )
    duplicated = combined.duplicated(subset=FLOW_KEYS, keep=False)
    if duplicated.any():
        conflict = combined.loc[duplicated].groupby(
            FLOW_KEYS, dropna=False
        )["net_value"].nunique(dropna=False)
        if (conflict > 1).any():
            raise CapitalFlowSchemaError(
                "Conflicting import for the same flow key/revision; bump revision."
            )
        combined = combined.drop_duplicates(subset=FLOW_KEYS, keep="last")
    combined = combined.sort_values(
        ["date", "scope", "ticker", "investor_class", "broker_code", "revision"]
    ).reset_index(drop=True)
    write_parquet_atomic(combined, output_path)
    logger.info("Ingested %d flow row(s); dataset now %d.", len(incoming), len(combined))
    return combined


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import verified capital-flow data.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_FLOW_PATH)
    parser.add_argument("--no-append", action="store_true")
    args = parser.parse_args(argv)
    setup_logging()
    result = ingest_flow_observations(
        args.input, output_path=args.output, append=not args.no_append
    )
    print(json.dumps({"rows": int(len(result))}, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
