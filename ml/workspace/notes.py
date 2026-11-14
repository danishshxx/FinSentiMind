"""Manual research notes with thesis revisions and source references.

The FastAPI layer is read-only; notes are imported from verified local CSV/JSON.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd

from ml.fundamentals.ingestion import write_parquet_atomic


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _PROJECT_ROOT / "ml" / "data" / "workspace" / "research_notes.parquet"

NOTE_COLUMNS = [
    "note_id",
    "ticker",
    "section",
    "content",
    "author",
    "source_ref",
    "created_at",
    "updated_at",
    "revision",
]

SECTIONS = {"thesis", "bull", "base", "bear", "risk", "catalyst", "source", "other"}


class NoteSchemaError(ValueError):
    pass


def validate_notes(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in NOTE_COLUMNS if column not in df.columns]
    if missing:
        raise NoteSchemaError(f"notes missing columns: {missing}")
    out = df.copy()
    out["note_id"] = out["note_id"].fillna("").astype(str).str.strip()
    out["ticker"] = out["ticker"].fillna("").astype(str).str.upper().str.strip()
    out["section"] = out["section"].fillna("other").astype(str).str.lower().str.strip()
    out["content"] = out["content"].fillna("").astype(str).str.strip()
    if (out["note_id"] == "").any() or (out["content"] == "").any():
        raise NoteSchemaError("note_id and content cannot be empty")
    if not out["section"].isin(SECTIONS).all():
        raise NoteSchemaError("invalid note section")
    out["created_at"] = pd.to_datetime(out["created_at"], errors="coerce", utc=True)
    out["updated_at"] = pd.to_datetime(out["updated_at"], errors="coerce", utc=True)
    if out["created_at"].isna().any() or out["updated_at"].isna().any():
        raise NoteSchemaError("created_at/updated_at must be valid")
    out["revision"] = pd.to_numeric(out["revision"], errors="coerce").fillna(0).astype("int64")
    if out["revision"].lt(0).any():
        raise NoteSchemaError("revision must be >= 0")
    out["author"] = out["author"].fillna("owner").astype(str)
    out["source_ref"] = out["source_ref"].fillna("").astype(str)
    duplicate = out.duplicated(subset=["note_id", "revision"], keep=False)
    if duplicate.any():
        raise NoteSchemaError("duplicate note_id/revision")
    return out[NOTE_COLUMNS].reset_index(drop=True)


class ResearchNoteRepository:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    @property
    def available(self) -> bool:
        return self.path.exists()

    def status(self) -> dict:
        return {
            "status": "AVAILABLE" if self.available else "UNAVAILABLE",
            "reason": None if self.available else "No research-notes import installed.",
        }

    def notes(self, ticker: Optional[str] = None, include_history: bool = False) -> pd.DataFrame:
        if not self.available:
            return pd.DataFrame({column: pd.Series(dtype="object") for column in NOTE_COLUMNS})
        df = pd.read_parquet(self.path)
        for column in ("created_at", "updated_at"):
            df[column] = pd.to_datetime(df[column], errors="coerce", utc=True)
        if ticker:
            df = df[df["ticker"] == ticker.upper()]
        if not include_history:
            df = df.sort_values(["updated_at", "revision"])
            df = df.drop_duplicates(subset=["note_id"], keep="last")
        return df.sort_values(["section", "updated_at"]).reset_index(drop=True)


def ingest_notes(input_path: Path, output_path: Path = DEFAULT_PATH) -> pd.DataFrame:
    raw = pd.read_csv(input_path) if input_path.suffix.lower() == ".csv" else pd.read_json(input_path)
    incoming = validate_notes(raw)
    existing = (
        pd.read_parquet(output_path) if output_path.exists()
        else pd.DataFrame(columns=NOTE_COLUMNS)
    )
    combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming
    duplicates = combined.duplicated(subset=["note_id", "revision"], keep=False)
    if duplicates.any():
        conflicts = combined.loc[duplicates].groupby(
            ["note_id", "revision"]
        )["content"].nunique()
        if (conflicts > 1).any():
            raise NoteSchemaError("Conflicting note content; bump revision.")
        combined = combined.drop_duplicates(subset=["note_id", "revision"], keep="last")
    combined = combined.sort_values(["ticker", "note_id", "revision"]).reset_index(drop=True)
    write_parquet_atomic(combined, output_path)
    return combined


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import research notes.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    result = ingest_notes(args.input, args.output)
    print(f"rows={len(result)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
