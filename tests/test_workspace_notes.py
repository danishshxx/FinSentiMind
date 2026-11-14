"""Tests for research-workspace notes (TASK 119)."""
from pathlib import Path

import pandas as pd
import pytest

from ml.fundamentals.ingestion import write_parquet_atomic
from ml.workspace.notes import (
    NoteSchemaError,
    ResearchNoteRepository,
    ingest_notes,
    validate_notes,
)


def _note(**overrides):
    row = {
        "note_id": "N1",
        "ticker": "BBCA",
        "section": "thesis",
        "content": "Fixture thesis",
        "author": "owner",
        "source_ref": "fixture.pdf",
        "created_at": "2026-01-01T09:00:00+07:00",
        "updated_at": "2026-01-01T09:00:00+07:00",
        "revision": 0,
    }
    row.update(overrides)
    return row


def _repo(tmp_path, rows):
    frame = validate_notes(pd.DataFrame(rows))
    path = tmp_path / "notes.parquet"
    write_parquet_atomic(frame, path)
    return ResearchNoteRepository(path)


def test_notes_are_point_in_time_revisions(tmp_path):
    repo = _repo(tmp_path, [
        _note(content="v0"),
        _note(content="v1", revision=1, updated_at="2026-02-01T09:00:00+07:00"),
    ])
    latest = repo.notes("BBCA")
    assert len(latest) == 1
    assert latest.iloc[0]["content"] == "v1"
    assert len(repo.notes("BBCA", include_history=True)) == 2


def test_duplicate_revision_is_rejected():
    with pytest.raises(NoteSchemaError, match="duplicate"):
        validate_notes(pd.DataFrame([_note(), _note()]))


def test_notes_import_round_trip(tmp_path):
    source = tmp_path / "notes.csv"
    pd.DataFrame([_note()]).to_csv(source, index=False)
    output = tmp_path / "notes.parquet"
    result = ingest_notes(source, output_path=output)
    assert output.exists()
    assert len(result) == 1


def test_missing_notes_repository_is_unavailable(tmp_path):
    repo = ResearchNoteRepository(tmp_path / "missing.parquet")
    assert repo.status()["status"] == "UNAVAILABLE"
    assert repo.notes("BBCA").empty
