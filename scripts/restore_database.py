#!/usr/bin/env python3
"""Validate and stage a SQLite restore into a new directory only."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_database import (
    BackupError,
    DEFAULT_MAX_ARCHIVE_BYTES,
    DEFAULT_MAX_UNPACKED_BYTES,
    verify_backup,
)
from scripts.verify_database import verify as verify_database
from scripts.verify_restore_archive import ALLOWED_FILES, verify_archive


class RestoreError(RuntimeError):
    pass


def _restore_snapshot(
    archive_path: Path,
    destination: Path,
    *,
    expected_version: int,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    stage_hook: Callable[[str, Path], None] | None = None,
) -> dict[str, object]:
    """Create a verified staged copy without replacing or deleting any existing data."""
    archive_path = Path(archive_path)
    destination = Path(destination)
    if not archive_path.is_file():
        raise RestoreError("restore archive is missing")
    if archive_path.stat().st_size > max_archive_bytes:
        raise RestoreError("restore archive size exceeds the configured limit")
    if destination.exists():
        raise RestoreError("restore destination already exists; choose a new staging path")
    try:
        verified_receipt = verify_backup(
            archive_path,
            expected_schema_version=expected_version,
            max_archive_bytes=max_archive_bytes,
            max_unpacked_bytes=max_unpacked_bytes,
        )
    except BackupError as exc:
        raise RestoreError(f"restore backup checksum or schema verification failed: {exc}") from exc
    try:
        count = verify_archive(archive_path)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.name not in {".", "./"}]
            expanded_size = sum(member.size for member in members)
        if count > 3 or expanded_size > max_unpacked_bytes:
            raise RestoreError("restore archive expanded size exceeds the configured limit")
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise RestoreError("restore archive failed structural verification") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bunkerkartet-restore-", dir=destination.parent))
    os.chmod(staging, 0o700)
    reserved_identity = None
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in members:
                name = member.name[2:] if member.name.startswith("./") else member.name
                if PurePosixPath(name).name != name or name not in ALLOWED_FILES or not member.isfile():
                    raise RestoreError("restore archive member is not allowed")
                if stage_hook:
                    stage_hook("extraction", staging)
                stream = archive.extractfile(member)
                if stream is None:
                    raise RestoreError("restore archive member cannot be read")
                target = staging / name
                written = 0
                with target.open("xb") as output:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_unpacked_bytes:
                            raise RestoreError("restore archive expanded size exceeds the configured limit")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(target, 0o600)
        try:
            verify_database(staging / "bunkerkartet.sqlite3", expected_version)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RestoreError("restored database schema or integrity verification failed") from exc
        if stage_hook:
            for phase in ("copy", "start", "readiness"):
                stage_hook(phase, staging)
        if destination.exists():
            raise RestoreError("restore destination appeared during staging")
        # Reserve exclusively; rename would silently replace an existing empty directory.
        destination.mkdir(mode=0o700)
        reserved_identity=(destination.stat().st_dev,destination.stat().st_ino)
        for staged_file in staging.iterdir():
            os.link(staged_file,destination/staged_file.name)  # refuses any existing member
        shutil.rmtree(staging)
    except Exception:
        if reserved_identity is not None and destination.exists() and (destination.stat().st_dev,destination.stat().st_ino)==reserved_identity:
            shutil.rmtree(destination)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "staged-and-verified",
        "destination": str(destination),
        "database_schema_version": expected_version,
        "archive": archive_path.name,
        "archive_sha256": verified_receipt["sha256"],
    }


def restore_archive(archive_path, destination, *, expected_version, max_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES, max_unpacked_bytes=DEFAULT_MAX_UNPACKED_BYTES, stage_hook=None):
    """Snapshot the bounded archive and receipt once; verify and extract those bytes."""
    source=Path(archive_path);destination=Path(destination)
    if not source.is_file():raise RestoreError('restore archive is missing')
    if source.stat().st_size > max_archive_bytes:raise RestoreError('restore archive size exceeds the configured limit')
    if destination.exists():raise RestoreError('restore destination already exists; choose a new staging path')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bunkerkartet-restore-input-',dir=destination.parent) as folder:
        snapshot=Path(folder)/source.name
        total=0
        with source.open('rb') as incoming,snapshot.open('xb') as output:
            while chunk:=incoming.read(1024*1024):
                total+=len(chunk)
                if total>max_archive_bytes:raise RestoreError('restore archive size exceeds the configured limit')
                output.write(chunk)
        os.chmod(snapshot,0o600)
        receipt=Path(str(source)+'.receipt.json')
        try:
            with receipt.open('rb') as incoming:receipt_bytes=incoming.read(64*1024+1)
            if len(receipt_bytes)>64*1024:raise RestoreError('restore receipt size exceeds limit')
            selected_receipt=Path(str(snapshot)+'.receipt.json')
            selected_receipt.write_bytes(receipt_bytes);os.chmod(selected_receipt,0o600)
        except OSError as error:raise RestoreError('restore backup checksum receipt is missing') from error
        return _restore_snapshot(snapshot,destination,expected_version=expected_version,max_archive_bytes=max_archive_bytes,max_unpacked_bytes=max_unpacked_bytes,stage_hook=stage_hook)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-version", type=int, required=True)
    parser.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    parser.add_argument("--max-unpacked-bytes", type=int, default=DEFAULT_MAX_UNPACKED_BYTES)
    args = parser.parse_args(argv)
    try:
        result = restore_archive(
            args.archive,
            args.destination,
            expected_version=args.expected_version,
            max_archive_bytes=args.max_archive_bytes,
            max_unpacked_bytes=args.max_unpacked_bytes,
        )
    except (RestoreError, OSError, RuntimeError, ValueError):
        print("restore staging failed; source archive and existing data were retained", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
