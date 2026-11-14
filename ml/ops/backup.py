"""Create and verify a checksummed local backup archive.

Backups are explicit local files. They are never uploaded or committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = _PROJECT_ROOT / "ml" / "data"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and "staging" not in path.parts
        and "backups" not in path.parts
    )


def create_backup(
    data_root: Path = DEFAULT_DATA_ROOT,
    output_path: Optional[Path] = None,
) -> Dict[str, object]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_path or data_root / "backups" / f"finsentimind_backup_{stamp}.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    files = _iter_files(data_root)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(data_root),
        "files": [],
        "note": "Staging directories are excluded from the archive.",
    }
    for path in files:
        relative = path.relative_to(data_root)
        manifest["files"].append({
            "path": str(relative),
            "size_bytes": int(path.stat().st_size),
            "sha256": _sha256(path),
        })
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=str(path.relative_to(data_root)))
        archive.writestr("BACKUP_MANIFEST.json", json.dumps(manifest, indent=2))
    return {
        "path": str(output_path),
        "files": len(files),
        "created_at": manifest["created_at"],
        "size_bytes": output_path.stat().st_size,
    }


def verify_backup(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Backup not found: {path}")
    with zipfile.ZipFile(path) as archive:
        if "BACKUP_MANIFEST.json" not in archive.namelist():
            raise ValueError("BACKUP_MANIFEST.json is missing")
        manifest = json.loads(archive.read("BACKUP_MANIFEST.json"))
        failures = []
        for item in manifest.get("files", []):
            data = archive.read(item["path"])
            digest = hashlib.sha256(data).hexdigest()
            if digest != item["sha256"]:
                failures.append(item["path"])
        return {
            "status": "OK" if not failures else "FAILED",
            "files": len(manifest.get("files", [])),
            "failures": failures,
            "created_at": manifest.get("created_at"),
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify a local backup.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.verify:
        print(json.dumps(verify_backup(args.verify), indent=2))
        return 0
    print(json.dumps(create_backup(args.data_root, args.output), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
