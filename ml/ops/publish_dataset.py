"""Stage and publish an immutable dataset generation safely.

This module is a thin orchestration layer over the existing ``dataset_store``
publisher. It never invents a new publication mechanism: it copies validated
artifacts into a fresh staging directory, writes a provenance sidecar, runs the
generation acceptance audit, then delegates the atomic move and ``active.json``
switch to :func:`ml.preprocessing.dataset_store.publish_generation`.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger, setup_logging
from ml.preprocessing.dataset_store import (
    OPTIONAL_ARTIFACTS,
    REQUIRED_ARTIFACTS,
    DatasetStoreError,
    publish_generation,
    resolve_active_dataset,
    verify_generation,
    write_json_atomic,
)
from ml.validation.generation_audit import audit_generation, write_provenance


logger = get_logger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage_from_source(
    source_dir: Path | str,
    data_root: Path | str,
    run_id: str,
    *,
    expected_calendar_version: Optional[str] = None,
    universe_scope: Optional[str] = None,
    feature_version: Optional[str] = None,
    source_generation: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Copy a validated source directory into a fresh staging generation."""
    source_dir = Path(source_dir)
    data_root = Path(data_root)
    staging = data_root / "staging" / run_id
    if staging.exists():
        raise DatasetStoreError(f"Staging directory already exists: {staging}")

    source_audit = audit_generation(
        source_dir, expected_calendar_version=expected_calendar_version
    )
    if source_audit["status"] == "FAIL":
        raise DatasetStoreError(
            f"Source generation failed acceptance: {source_audit['failures']}"
        )

    staging.mkdir(parents=True)
    for name in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
        candidate = source_dir / name
        if candidate.is_file():
            shutil.copy2(candidate, staging / name)

    provenance = write_provenance(
        staging,
        source_generation=source_generation or source_dir.name,
        calendar_version=source_audit["calendar_version"],
        data_as_of=source_audit["data_as_of"],
        feature_version=feature_version,
        universe_scope=universe_scope,
        notes=notes,
    )

    staged_audit = audit_generation(
        staging, expected_calendar_version=expected_calendar_version
    )
    if staged_audit["status"] == "FAIL":
        raise DatasetStoreError(
            f"Staged generation failed acceptance: {staged_audit['failures']}"
        )
    logger.info(
        "Staged generation %s from %s (%d rows)",
        run_id, source_dir, staged_audit["price_rows"],
    )
    return {
        "run_id": run_id,
        "staging": str(staging),
        "provenance": provenance,
        "audit": staged_audit,
    }


def publish_and_verify(
    staging: Path | str,
    data_root: Path | str,
    run_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Publish a staged generation, then verify pointer and checksums."""
    published = publish_generation(staging, data_root, run_id, metadata or {})
    verification = verify_generation(data_root, run_id)
    if not verification["valid"]:
        raise DatasetStoreError(
            f"Published generation failed verification: {verification}"
        )
    active = resolve_active_dataset(data_root, verify_hashes=True)
    if active.run_id != run_id:
        raise DatasetStoreError(
            f"Active pointer did not switch to {run_id} (found {active.run_id})."
        )
    logger.info("Published and activated generation %s", run_id)
    return {
        "run_id": run_id,
        "path": str(published.path),
        "manifest": published.manifest,
        "verification": {
            "valid": verification["valid"],
            "missing": verification["missing"],
            "hash_mismatches": verification["hash_mismatches"],
        },
        "active": {
            "source": active.source,
            "run_id": active.run_id,
            "path": str(active.active_dir),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage and publish a validated dataset generation."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("ml/data"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-calendar-version", default=None)
    parser.add_argument("--universe-scope", default=None)
    parser.add_argument("--feature-version", default=None)
    parser.add_argument("--refreshed-at", default=None)
    parser.add_argument("--notes", nargs="*", default=None)
    args = parser.parse_args(argv)

    setup_logging()
    staged = stage_from_source(
        args.source,
        args.data_root,
        args.run_id,
        expected_calendar_version=args.expected_calendar_version,
        universe_scope=args.universe_scope,
        feature_version=args.feature_version,
        notes=args.notes,
    )
    provenance = staged["provenance"]
    metadata = {
        "data_as_of": provenance["data_as_of"],
        "refreshed_at": args.refreshed_at or provenance["refreshed_at"],
        "notes": [
            f"calendar_version={provenance['calendar_version']}",
            f"source_generation={provenance['source_generation']}",
            f"universe_scope={provenance['universe_scope']}",
            f"feature_version={provenance['feature_version']}",
        ],
    }
    result = publish_and_verify(
        staged["staging"], args.data_root, args.run_id, metadata=metadata
    )
    report_path = Path("ml/reports") / f"publication_{args.run_id}.json"
    write_json_atomic(report_path, {
        "published_at": _utc_now(),
        "staging_audit": staged["audit"],
        "publication": result,
    })
    print(json.dumps({
        "status": "PUBLISHED",
        "run_id": result["run_id"],
        "path": result["path"],
        "active": result["active"],
        "report": str(report_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
