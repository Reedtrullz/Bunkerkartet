#!/usr/bin/env python3
"""Create and verify bounded, checksummed SQLite backup archives."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import CURRENT_SCHEMA_VERSION
from scripts.verify_database import verify as verify_database
from scripts.verify_restore_archive import verify_archive


DEFAULT_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_UNPACKED_BYTES = 500 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def _receipt_path(archive: Path) -> Path:
    return Path(str(archive) + ".receipt.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_archive(archive_path: Path, max_archive_bytes: int, max_unpacked_bytes: int) -> int:
    if not archive_path.is_file():
        raise BackupError("backup archive is missing")
    if archive_path.stat().st_size > max_archive_bytes:
        raise BackupError("backup archive size exceeds the configured limit")
    try:
        file_count = verify_archive(archive_path)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            total_size = sum(member.size for member in members if member.isfile())
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise BackupError("backup archive failed structural verification") from exc
    if file_count > 3 or total_size > max_unpacked_bytes:
        raise BackupError("backup archive expanded size exceeds the configured limit")
    return total_size


def create_backup(
    database_path: Path,
    archive_path: Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    created_at: datetime | None = None,
) -> dict[str, object]:
    """Take a transactionally consistent SQLite snapshot and retain its receipt."""
    database_path = Path(database_path)
    archive_path = Path(archive_path)
    receipt_path = _receipt_path(archive_path)
    if not database_path.is_file():
        raise BackupError("database file is missing")
    if archive_path.exists() or receipt_path.exists():
        raise BackupError("backup destination already exists")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise BackupError("backup timestamp must include a timezone")

    with tempfile.TemporaryDirectory(prefix="bunkerkartet-backup-", dir=archive_path.parent) as temporary:
        temporary_path = Path(temporary)
        snapshot = temporary_path / "bunkerkartet.sqlite3"
        candidate = temporary_path / "backup.tar.gz"
        try:
            with closing(sqlite3.connect(database_path)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            with tarfile.open(candidate, "w:gz") as archive:
                archive.add(snapshot, arcname="bunkerkartet.sqlite3", recursive=False)
            expanded_size = _inspect_archive(candidate, max_archive_bytes, max_unpacked_bytes)
            with tarfile.open(candidate, "r:gz") as archive:
                member = archive.extractfile("bunkerkartet.sqlite3")
                if member is None:
                    raise BackupError("backup database is missing")
                extracted = temporary_path / "verified.sqlite3"
                with extracted.open("xb") as output:
                    shutil.copyfileobj(member, output, length=1024 * 1024)
            with closing(sqlite3.connect(extracted.resolve().as_uri()+"?mode=ro",uri=True)) as check:
                source_version = check.execute("PRAGMA user_version").fetchone()[0]
                if source_version >= 14 and check.execute("SELECT 1 FROM image_references WHERE state='active' LIMIT 1").fetchone():
                    raise BackupError("active images require a complete media workspace backup")
            verify_database(extracted, source_version)
            os.chmod(candidate, 0o600)
            os.replace(candidate, archive_path)
        except BackupError:
            raise
        except (OSError, sqlite3.Error, tarfile.TarError, RuntimeError, ValueError) as exc:
            raise BackupError("backup creation or database verification failed") from exc

    receipt: dict[str, object] = {
        "receipt_version": 1,
        "archive": archive_path.name,
        "sha256": _sha256(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "expanded_size_bytes": expanded_size,
        "database_schema_version": source_version,
        "created_at": timestamp.astimezone(timezone.utc).isoformat(),
        "verified": True,
    }
    temporary_receipt = receipt_path.with_name(receipt_path.name + ".tmp")
    try:
        with temporary_receipt.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.chmod(temporary_receipt, 0o600)
        os.replace(temporary_receipt, receipt_path)
    except OSError as exc:
        temporary_receipt.unlink(missing_ok=True)
        raise BackupError("backup receipt could not be retained") from exc
    _failure_path(archive_path.parent).unlink(missing_ok=True)
    return receipt


def _failure_path(directory: Path) -> Path:
    return Path(directory) / "backup-failure.json"


def record_failure(directory: Path, *, at: datetime | None = None) -> None:
    """Write a secret-free failure marker for the operator's freshness check."""
    marker = _failure_path(Path(directory))
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "failed", "at": (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()}
    temporary = marker.with_name(marker.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, marker)


def verify_backup(
    archive_path: Path,
    *,
    expected_schema_version: int | None = None,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    receipt_path: Path | None = None,
) -> dict[str, object]:
    archive_path = Path(archive_path)
    receipt_path = Path(receipt_path) if receipt_path else _receipt_path(archive_path)
    expanded_size = _inspect_archive(archive_path, max_archive_bytes, max_unpacked_bytes)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup receipt is missing or invalid") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("receipt_version") != 1
        or receipt.get("archive") != archive_path.name
        or receipt.get("verified") is not True
        or receipt.get("sha256") != _sha256(archive_path)
        or receipt.get("archive_size_bytes") != archive_path.stat().st_size
        or receipt.get("expanded_size_bytes") != expanded_size
    ):
        raise BackupError("backup checksum or receipt does not match the archive")
    schema_version = receipt.get("database_schema_version")
    expected = expected_schema_version if expected_schema_version is not None else schema_version
    if not isinstance(expected, int) or schema_version != expected:
        raise BackupError("backup database schema does not match the expected version")
    with tempfile.TemporaryDirectory(prefix="bunkerkartet-backup-verify-") as temporary:
        extracted = Path(temporary) / "bunkerkartet.sqlite3"
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                member = archive.extractfile("bunkerkartet.sqlite3")
                if member is None:
                    raise BackupError("backup database is missing")
                with extracted.open("xb") as output:
                    shutil.copyfileobj(member, output, length=1024 * 1024)
            verify_database(extracted, expected)
        except (OSError, tarfile.TarError, RuntimeError, ValueError, sqlite3.Error) as exc:
            raise BackupError("backup database failed schema or integrity verification") from exc
    return receipt


def check_latest_backup(
    directory: Path,
    *,
    maximum_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, object]:
    directory = Path(directory)
    receipts = sorted(directory.glob("*.tar.gz.receipt.json"))
    if not receipts:
        raise BackupError("backup receipt is missing")
    try:
        parsed = [(datetime.fromisoformat(json.loads(path.read_text())["created_at"]), path) for path in receipts]
        created_at, receipt_path = max(parsed, key=lambda item: item[0])
        current_time = now or datetime.now(timezone.utc)
        age = (current_time.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise BackupError("backup receipt timestamp is invalid") from exc
    if age < 0 or age > maximum_age_seconds:
        raise BackupError("latest backup receipt is stale")
    marker = _failure_path(directory)
    if marker.exists():
        try:
            failure_at = datetime.fromisoformat(json.loads(marker.read_text())["at"])
            if failure_at > created_at:
                raise BackupError("a backup run failed after the latest verified backup")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError("backup failure marker is invalid") from exc
    archive_path = directory / str(json.loads(receipt_path.read_text())["archive"])
    return verify_backup(archive_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="create a local consistent backup")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    create.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    verify = subparsers.add_parser("verify", help="verify archive, checksum receipt and schema")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--receipt", type=Path)
    verify.add_argument("--expected-schema-version", type=int, required=True)
    verify.add_argument("--max-archive-bytes", type=int, default=DEFAULT_MAX_ARCHIVE_BYTES)
    status = subparsers.add_parser("status", help="fail if the latest backup is missing, failed or stale")
    status.add_argument("--directory", type=Path, required=True)
    status.add_argument("--max-age-seconds", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(args.database, args.archive, max_archive_bytes=args.max_archive_bytes)
        elif args.command == "verify":
            result = verify_backup(
                args.archive,
                expected_schema_version=args.expected_schema_version,
                max_archive_bytes=args.max_archive_bytes,
                receipt_path=args.receipt,
            )
        else:
            result = check_latest_backup(args.directory, maximum_age_seconds=args.max_age_seconds)
    except (BackupError, OSError):
        if args.command == "create":
            try:
                record_failure(args.archive.parent)
            except OSError:
                pass
        print("backup operation failed", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
