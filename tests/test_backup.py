"""Tests for local checksummed backup/recovery (TASK 120)."""
import json
import zipfile
from pathlib import Path

import pytest

from ml.ops.backup import create_backup, verify_backup


def test_backup_and_verify_round_trip(tmp_path: Path):
    data = tmp_path / "data"
    (data / "generations" / "run-1").mkdir(parents=True)
    (data / "base_dataset.parquet").write_bytes(b"base")
    (data / "active.json").write_text('{"generation": "run-1"}', encoding="utf-8")
    (data / "staging" / "failed").mkdir(parents=True)
    (data / "staging" / "failed" / "artifact.parquet").write_bytes(b"staging")

    output = tmp_path / "backup.zip"
    result = create_backup(data, output)
    assert result["files"] == 2
    assert output.exists()

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("BACKUP_MANIFEST.json"))
    paths = {item["path"] for item in manifest["files"]}
    assert "base_dataset.parquet" in paths
    assert "active.json" in paths
    assert not any(path.startswith("staging") for path in paths)

    verify = verify_backup(output)
    assert verify["status"] == "OK"
    assert verify["failures"] == []


def test_verify_rejects_archive_without_manifest(tmp_path: Path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("file.txt", "data")
    with pytest.raises(ValueError, match="MANIFEST"):
        verify_backup(bad)
