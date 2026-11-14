"""Tests for versioned dataset generations and the active manifest pointer."""

from __future__ import annotations

from pathlib import Path

import pytest

from ml.preprocessing.dataset_store import (
    DatasetStoreError,
    REQUIRED_ARTIFACTS,
    list_generations,
    publish_generation,
    resolve_active_dataset,
    rollback_to_generation,
    staging_dir,
    verify_generation,
    write_json_atomic,
)


def _staging(root: Path, run_id: str, marker: str) -> Path:
    directory = staging_dir(root, run_id)
    directory.mkdir(parents=True)
    for index, name in enumerate(REQUIRED_ARTIFACTS):
        (directory / name).write_bytes(f"{marker}-{name}-{index}".encode("utf-8"))
    return directory


def test_legacy_fallback_when_no_manifest(tmp_path: Path):
    (tmp_path / "base_dataset.parquet").write_bytes(b"legacy")
    active = resolve_active_dataset(tmp_path)
    assert active.source == "legacy"
    assert active.active_dir == tmp_path
    assert active.run_id is None


def test_publish_switches_active_pointer(tmp_path: Path):
    staging = _staging(tmp_path, "run-a", "a")
    published = publish_generation(
        staging, tmp_path, "run-a", {"data_as_of": "2025-01-10", "refreshed_at": "2025-01-11T00:00:00+00:00"}
    )

    assert published.path == tmp_path / "generations" / "run-a"
    assert not staging.exists()
    active = resolve_active_dataset(tmp_path)
    assert active.source == "manifest"
    assert active.run_id == "run-a"
    assert active.active_dir == published.path
    assert (published.path / "generation.json").exists()
    verification = verify_generation(tmp_path, "run-a")
    assert verification["valid"] is True


def test_previous_generation_survives_new_publication(tmp_path: Path):
    publish_generation(_staging(tmp_path, "run-a", "a"), tmp_path, "run-a", {})
    publish_generation(_staging(tmp_path, "run-b", "b"), tmp_path, "run-b", {})

    active = resolve_active_dataset(tmp_path)
    assert active.run_id == "run-b"
    assert (tmp_path / "generations" / "run-a" / "base_dataset.parquet").exists()

    rows = list_generations(tmp_path)
    assert [row["run_id"] for row in rows] == ["run-a", "run-b"]
    assert {row["run_id"] for row in rows if row["active"]} == {"run-b"}


def test_rollback_restores_verified_generation(tmp_path: Path):
    publish_generation(_staging(tmp_path, "run-a", "a"), tmp_path, "run-a", {"data_as_of": "2025-01-10"})
    publish_generation(_staging(tmp_path, "run-b", "b"), tmp_path, "run-b", {"data_as_of": "2025-01-11"})

    rolled = rollback_to_generation(tmp_path, "run-a")
    active = resolve_active_dataset(tmp_path)
    assert rolled.manifest["run_id"] == "run-a"
    assert active.run_id == "run-a"
    assert active.manifest.get("rolled_back_at")


def test_tampered_generation_is_not_valid(tmp_path: Path):
    published = publish_generation(_staging(tmp_path, "run-a", "a"), tmp_path, "run-a", {})
    (published.path / "base_dataset.parquet").write_bytes(b"tampered")

    verification = verify_generation(tmp_path, "run-a")
    assert verification["valid"] is False
    assert "base_dataset.parquet" in verification["hash_mismatches"]

    with pytest.raises(DatasetStoreError, match="not safe"):
        rollback_to_generation(tmp_path, "run-a")


def test_invalid_pointer_falls_back_to_legacy(tmp_path: Path):
    (tmp_path / "base_dataset.parquet").write_bytes(b"legacy")
    write_json_atomic(
        tmp_path / "active.json",
        {"generation": "generations/missing-run"},
    )
    active = resolve_active_dataset(tmp_path)
    assert active.source == "legacy"
    assert active.active_dir == tmp_path


def test_publication_failure_keeps_previous_active(tmp_path: Path):
    first = publish_generation(_staging(tmp_path, "run-a", "a"), tmp_path, "run-a", {})
    pointer_before = (tmp_path / "active.json").read_bytes()

    with pytest.raises(DatasetStoreError, match="already exists"):
        publish_generation(_staging(tmp_path, "run-a", "again"), tmp_path, "run-a", {})

    assert (tmp_path / "active.json").read_bytes() == pointer_before
    assert resolve_active_dataset(tmp_path).active_dir == first.path


def test_tampered_active_generation_falls_back_to_legacy(tmp_path: Path):
    published = publish_generation(_staging(tmp_path, "run-a", "a"), tmp_path, "run-a", {})
    (tmp_path / "base_dataset.parquet").write_bytes(b"legacy")
    (published.path / "base_dataset.parquet").write_bytes(b"tampered")

    active = resolve_active_dataset(tmp_path)
    assert active.source == "legacy"
    assert active.active_dir == tmp_path
