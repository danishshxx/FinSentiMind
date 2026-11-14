"""Versioned dataset generations and an atomic active-dataset pointer.

The refresh pipeline writes every artifact into an isolated staging directory.
Only after validation succeeds is the directory moved into ``generations/`` and
the small ``active.json`` pointer switched atomically. Readers resolve that
pointer, so the previous known-good generation remains intact when a refresh
fails.

Multi-file atomicity is not claimed. A generation directory is immutable after
publication; the active switch is a single atomic file replace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger


logger = get_logger(__name__)

SCHEMA_VERSION = 1
ACTIVE_POINTER = "active.json"
GENERATIONS_DIR = "generations"
STAGING_DIR = "staging"
GENERATION_MANIFEST = "generation.json"

REQUIRED_ARTIFACTS = (
    "prices_clean.parquet",
    "daily_sentiment.parquet",
    "trading_calendar.parquet",
    "base_dataset.parquet",
)

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DatasetStoreError(RuntimeError):
    """Raised when a generation cannot be safely read or published."""


@dataclass(frozen=True)
class ActiveDataset:
    root: Path
    active_dir: Path
    source: str
    run_id: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class PublishedGeneration:
    path: Path
    manifest: Dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        raise DatasetStoreError(
            "run_id must contain only letters, digits, dot, dash, or underscore."
        )
    return str(run_id)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DatasetStoreError(f"Cannot read JSON file {path}: {exc}") from exc


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON using a temporary file in the same directory + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def missing_artifacts(directory: Path) -> List[str]:
    return [name for name in REQUIRED_ARTIFACTS if not (directory / name).is_file()]


def require_artifacts(directory: Path) -> None:
    missing = missing_artifacts(directory)
    if missing:
        raise DatasetStoreError(
            f"Dataset directory is incomplete ({directory}): missing {missing}"
        )


def artifact_manifest(directory: Path) -> Dict[str, Dict[str, Any]]:
    require_artifacts(directory)
    artifacts: Dict[str, Dict[str, Any]] = {}
    for name in REQUIRED_ARTIFACTS:
        path = directory / name
        stat = path.stat()
        artifacts[name] = {
            "size_bytes": int(stat.st_size),
            "sha256": sha256_file(path),
        }
    return artifacts


def _safe_generation_path(root: Path, relative: str | Path) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise DatasetStoreError(
            f"Generation path escapes the dataset root: {relative!r}"
        ) from exc
    return candidate


def resolve_active_dataset(root: Path | str) -> ActiveDataset:
    """Resolve ``active.json``; fall back to flat legacy files if absent/invalid."""
    root = Path(root)
    pointer = root / ACTIVE_POINTER
    if pointer.is_file():
        try:
            payload = read_json(pointer)
            generation = payload.get("generation")
            if not generation:
                raise DatasetStoreError("active.json has no generation field")
            candidate = _safe_generation_path(root, generation)
            require_artifacts(candidate)
            return ActiveDataset(
                root=root,
                active_dir=candidate,
                source="manifest",
                run_id=payload.get("run_id"),
                manifest=payload,
            )
        except Exception as exc:
            logger.warning(
                "Ignoring invalid active manifest %s (%s); using legacy flat files.",
                pointer,
                exc,
            )
    return ActiveDataset(
        root=root,
        active_dir=root,
        source="legacy",
        run_id=None,
        manifest=None,
    )


def resolve_active_dataset_dir(root: Path | str) -> Path:
    return resolve_active_dataset(root).active_dir


def read_active_manifest(root: Path | str) -> Optional[Dict[str, Any]]:
    pointer = Path(root) / ACTIVE_POINTER
    if not pointer.is_file():
        return None
    try:
        return read_json(pointer)
    except DatasetStoreError:
        return None


def generation_dir(root: Path | str, run_id: str) -> Path:
    validate_run_id(run_id)
    return Path(root) / GENERATIONS_DIR / run_id


def staging_dir(root: Path | str, run_id: str) -> Path:
    validate_run_id(run_id)
    return Path(root) / STAGING_DIR / run_id


def publish_generation(
    staging: Path | str,
    root: Path | str,
    run_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> PublishedGeneration:
    """Publish a validated staging directory and atomically switch active.json."""
    staging = Path(staging)
    root = Path(root)
    validate_run_id(run_id)
    require_artifacts(staging)

    destination = generation_dir(root, run_id)
    if destination.exists():
        raise DatasetStoreError(f"Generation already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    artifacts = artifact_manifest(staging)
    published_at = utc_now_iso()
    relative = destination.resolve().relative_to(root.resolve()).as_posix()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "generation": relative,
        "published_at": published_at,
        "refreshed_at": (metadata or {}).get("refreshed_at"),
        "data_as_of": (metadata or {}).get("data_as_of"),
        "artifacts": artifacts,
        "notes": (metadata or {}).get("notes", []),
    }

    # Move the complete staged directory first. Until active.json changes, readers
    # continue using the previous generation.
    try:
        os.replace(staging, destination)
    except OSError:
        shutil.move(str(staging), str(destination))

    try:
        write_json_atomic(destination / GENERATION_MANIFEST, manifest)
        write_json_atomic(root / ACTIVE_POINTER, manifest)
    except Exception as exc:
        raise DatasetStoreError(
            f"Generation was moved to {destination} but activation failed; "
            f"the previous active generation is still in use: {exc}"
        ) from exc

    logger.info("Published dataset generation %s at %s", run_id, destination)
    return PublishedGeneration(path=destination, manifest=manifest)


def verify_generation(
    root: Path | str,
    run_id: str,
    check_hashes: bool = True,
) -> Dict[str, Any]:
    root = Path(root)
    directory = generation_dir(root, run_id)
    report: Dict[str, Any] = {
        "run_id": run_id,
        "path": str(directory),
        "valid": False,
        "missing": missing_artifacts(directory),
        "hash_mismatches": [],
    }
    manifest_path = directory / GENERATION_MANIFEST
    if not manifest_path.is_file():
        report["error"] = "generation.json is missing"
        return report
    try:
        manifest = read_json(manifest_path)
    except DatasetStoreError as exc:
        report["error"] = str(exc)
        return report

    if check_hashes and not report["missing"]:
        artifacts = manifest.get("artifacts", {})
        for name in REQUIRED_ARTIFACTS:
            expected = (artifacts.get(name) or {}).get("sha256")
            actual = sha256_file(directory / name)
            if expected and expected != actual:
                report["hash_mismatches"].append(name)

    report["valid"] = not report["missing"] and not report["hash_mismatches"]
    report["manifest"] = manifest
    return report


def rollback_to_generation(root: Path | str, run_id: str) -> PublishedGeneration:
    """Point active.json back to an existing, verified generation."""
    validate_run_id(run_id)
    verification = verify_generation(root, run_id)
    if not verification["valid"]:
        raise DatasetStoreError(
            f"Generation {run_id} is not safe to activate: "
            f"{verification.get('error') or verification}"
        )
    manifest = verification["manifest"]
    pointer = {
        **manifest,
        "rolled_back_at": utc_now_iso(),
    }
    write_json_atomic(Path(root) / ACTIVE_POINTER, pointer)
    logger.info("Rolled back active dataset to generation %s", run_id)
    return PublishedGeneration(path=generation_dir(root, run_id), manifest=pointer)


def list_generations(root: Path | str) -> List[Dict[str, Any]]:
    root = Path(root)
    generations_root = root / GENERATIONS_DIR
    if not generations_root.is_dir():
        return []
    active = read_active_manifest(root) or {}
    rows: List[Dict[str, Any]] = []
    for directory in sorted(generations_root.iterdir()):
        if not directory.is_dir():
            continue
        manifest_path = directory / GENERATION_MANIFEST
        entry: Dict[str, Any] = {
            "run_id": directory.name,
            "path": str(directory),
            "active": directory.name == active.get("run_id"),
            "complete": not missing_artifacts(directory),
        }
        if manifest_path.is_file():
            try:
                manifest = read_json(manifest_path)
                entry.update(
                    {
                        "published_at": manifest.get("published_at"),
                        "refreshed_at": manifest.get("refreshed_at"),
                        "data_as_of": manifest.get("data_as_of"),
                    }
                )
            except DatasetStoreError:
                entry["complete"] = False
        rows.append(entry)
    return rows
