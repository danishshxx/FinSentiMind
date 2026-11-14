"""Manual and file-based ingestion for fundamental data.

No network provider is called. The owner can import verified source documents
(CSV/JSON) and retain provenance. This is deliberate: unlicensed scraping of
restricted financial sources is not acceptable.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from app.core.logging import get_logger, setup_logging
from ml.fundamentals.normalization import normalize_financial_statements
from ml.fundamentals.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DIVIDEND_COLUMNS,
    FINANCIAL_STATEMENT_COLUMNS,
    FundamentalSchemaError,
    LOGICAL_STATEMENT_KEYS,
    validate_corporate_actions,
    validate_dividends,
    validate_financial_statements,
)


logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _PROJECT_ROOT / "ml" / "data" / "fundamentals"
DEFAULT_STATEMENTS = DEFAULT_DATA_DIR / "financial_statements.parquet"
DEFAULT_ACTIONS = DEFAULT_DATA_DIR / "corporate_actions.parquet"
DEFAULT_DIVIDENDS = DEFAULT_DATA_DIR / "dividend_history.parquet"


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_manual_file(path: Path) -> pd.DataFrame:
    """Load a CSV or JSON manual import."""
    if not path.exists():
        raise FileNotFoundError(f"Fundamental import not found: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in (".json", ".jsonl"):
        if path.suffix.lower() == ".jsonl":
            return pd.read_json(path, lines=True)
        return pd.read_json(path)
    raise FundamentalSchemaError(
        f"Unsupported import extension {path.suffix!r}; use CSV or JSON."
    )


def _merge_statements(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new
    combined = pd.concat([existing, new], ignore_index=True)
    keys = LOGICAL_STATEMENT_KEYS + ["revision"]
    duplicated = combined.duplicated(subset=keys, keep=False)
    if duplicated.any():
        group = combined.loc[duplicated, keys + ["value"]]
        conflicts = group.groupby(keys, dropna=False)["value"].nunique(dropna=False)
        if (conflicts > 1).any():
            raise FundamentalSchemaError(
                "Conflicting import for the same statement key and revision; "
                "bump revision and set restatement_of for a correction."
            )
        combined = combined.drop_duplicates(subset=keys, keep="last")
    return combined.reset_index(drop=True)


def ingest_financial_statements(
    input_path: Path,
    output_path: Path = DEFAULT_STATEMENTS,
    append: bool = True,
) -> pd.DataFrame:
    raw = load_manual_file(input_path)
    validated = validate_financial_statements(raw)
    normalized = normalize_financial_statements(validated)
    existing = (
        pd.read_parquet(output_path) if append and output_path.exists()
        else pd.DataFrame(columns=FINANCIAL_STATEMENT_COLUMNS)
    )
    merged = _merge_statements(existing, normalized)
    merged = merged.sort_values(
        ["ticker", "period_end", "statement_type", "line_item", "revision"]
    ).reset_index(drop=True)
    write_parquet_atomic(merged, output_path)
    logger.info(
        "Ingested %d statement row(s); dataset now %d row(s).",
        len(normalized), len(merged),
    )
    return merged


def ingest_corporate_actions(
    input_path: Path, output_path: Path = DEFAULT_ACTIONS, append: bool = True
) -> pd.DataFrame:
    validated = validate_corporate_actions(load_manual_file(input_path))
    existing = (
        pd.read_parquet(output_path) if append and output_path.exists()
        else pd.DataFrame(columns=CORPORATE_ACTION_COLUMNS)
    )
    merged = (
        pd.concat([existing, validated], ignore_index=True)
        if not existing.empty else validated
    )
    write_parquet_atomic(merged, output_path)
    return merged


def ingest_dividends(
    input_path: Path, output_path: Path = DEFAULT_DIVIDENDS, append: bool = True
) -> pd.DataFrame:
    validated = validate_dividends(load_manual_file(input_path))
    existing = (
        pd.read_parquet(output_path) if append and output_path.exists()
        else pd.DataFrame(columns=DIVIDEND_COLUMNS)
    )
    merged = (
        pd.concat([existing, validated], ignore_index=True)
        if not existing.empty else validated
    )
    write_parquet_atomic(merged, output_path)
    return merged


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import verified fundamental data (no network scraping)."
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=("statements", "actions", "dividends"),
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-append", action="store_true")
    args = parser.parse_args(argv)

    setup_logging()
    if args.type == "statements":
        result = ingest_financial_statements(
            args.input,
            output_path=args.output or DEFAULT_STATEMENTS,
            append=not args.no_append,
        )
    elif args.type == "actions":
        result = ingest_corporate_actions(
            args.input,
            output_path=args.output or DEFAULT_ACTIONS,
            append=not args.no_append,
        )
    else:
        result = ingest_dividends(
            args.input,
            output_path=args.output or DEFAULT_DIVIDENDS,
            append=not args.no_append,
        )
    print(json.dumps({
        "rows": int(len(result)),
        "columns": list(result.columns),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
