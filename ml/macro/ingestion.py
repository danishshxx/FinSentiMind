"""Manual macro history import with provenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.fundamentals.ingestion import write_parquet_atomic
from ml.macro.repository import DEFAULT_PATH, load_macro_import
from ml.macro.schemas import (
    MACRO_KEYS,
    MACRO_OBSERVATION_COLUMNS,
    MacroSchemaError,
    validate_macro_observations,
)


logger = get_logger(__name__)


def ingest_macro_observations(
    input_path: Path, output_path: Path = DEFAULT_PATH, append: bool = True
) -> pd.DataFrame:
    incoming = validate_macro_observations(load_macro_import(input_path))
    existing = (
        pd.read_parquet(output_path)
        if append and output_path.exists()
        else pd.DataFrame(columns=MACRO_OBSERVATION_COLUMNS)
    )
    combined = (
        pd.concat([existing, incoming], ignore_index=True)
        if not existing.empty else incoming
    )
    duplicate = combined.duplicated(subset=MACRO_KEYS, keep=False)
    if duplicate.any():
        conflicts = combined.loc[duplicate].groupby(
            MACRO_KEYS, dropna=False
        )["value"].nunique()
        if (conflicts > 1).any():
            raise MacroSchemaError(
                "Conflicting macro value for the same indicator/date/revision; "
                "bump revision."
            )
        combined = combined.drop_duplicates(subset=MACRO_KEYS, keep="last")
    combined = combined.sort_values(["indicator", "date", "revision"]).reset_index(drop=True)
    write_parquet_atomic(combined, output_path)
    logger.info("Ingested %d macro row(s); dataset now %d.", len(incoming), len(combined))
    return combined


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import verified macro history.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--no-append", action="store_true")
    args = parser.parse_args(argv)
    setup_logging()
    result = ingest_macro_observations(
        args.input, output_path=args.output, append=not args.no_append
    )
    print(json.dumps({"rows": int(len(result))}, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
