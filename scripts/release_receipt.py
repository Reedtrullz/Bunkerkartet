#!/usr/bin/env python3
"""Build or validate a redacted Bunkerkartet release receipt."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_database import BackupError, verify_backup


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseReceiptError(ValueError):
    pass


def build_release_receipt(
    *,
    commit_sha: str,
    image_digest: str,
    database_schema_version: int,
    backup: dict[str, object],
    selected_port: int,
    readiness: dict[str, object],
    version: dict[str, object],
    rollback: dict[str, object],
    created_at: datetime | None = None,
    migration_qualification: dict | None = None,
) -> dict[str, object]:
    if not SHA_RE.fullmatch(commit_sha):
        raise ReleaseReceiptError("commit SHA must be a full 40-character lowercase SHA")
    if not DIGEST_RE.fullmatch(image_digest):
        raise ReleaseReceiptError("image digest must be a sha256 digest")
    if not 1 <= selected_port <= 65535:
        raise ReleaseReceiptError("selected port is outside the valid TCP range")
    if version.get("version") != commit_sha:
        raise ReleaseReceiptError("deployed version does not match the exact commit SHA")
    if (
        readiness.get("status") != "ready"
        or readiness.get("database") != "ready"
        or readiness.get("authentication") != "configured"
    ):
        raise ReleaseReceiptError("readiness does not prove database and private authentication")
    if backup.get("verified") is not True or not isinstance(backup.get("database_schema_version"),int):
        raise ReleaseReceiptError("verified backup schema does not match the release schema")
    if not re.fullmatch(r"[0-9a-f]{64}", str(backup.get("sha256", ""))):
        raise ReleaseReceiptError("backup receipt checksum is invalid")
    if not DIGEST_RE.fullmatch(str(rollback.get("image_digest", ""))):
        raise ReleaseReceiptError("rollback image digest is invalid")
    if rollback.get("database_schema_version") != backup.get("database_schema_version"):
        raise ReleaseReceiptError("rollback database schema is incompatible")
    if rollback.get("backup_sha256") != backup.get("sha256"):
        raise ReleaseReceiptError("rollback database checksum does not match the verified backup")
    if not isinstance(rollback.get("data_volume"), str) or not rollback["data_volume"]:
        raise ReleaseReceiptError("rollback data volume is required")
    if backup['database_schema_version'] != database_schema_version:
        verify_migration_qualification(migration_qualification,backup,rollback,database_schema_version)
    moment = created_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ReleaseReceiptError("receipt timestamp must include a timezone")
    receipt = {
        "receipt_version": 1,
        "created_at": moment.astimezone(timezone.utc).isoformat(),
        "commit_sha": commit_sha,
        "image_digest": image_digest,
        "database_schema_version": database_schema_version,
        "backup": {
            "archive": Path(str(backup.get("archive", ""))).name,
            "sha256": backup["sha256"],
            "database_schema_version": backup["database_schema_version"],
        },
        "selected_port": selected_port,
        "readiness": {
            "status": readiness["status"],
            "database": readiness["database"],
            "authentication": readiness["authentication"],
        },
        "version": {"version": version["version"]},
        "rollback": {
            "image_digest": rollback["image_digest"],
            "database_schema_version": rollback["database_schema_version"],
            "data_volume": str(rollback["data_volume"]),
            "backup_sha256": str(rollback["backup_sha256"]),
        },
    }
    if migration_qualification is not None: receipt['migration_qualification'] = migration_qualification
    validate_release_receipt(receipt)
    return receipt


def verify_migration_qualification(proof,backup,rollback,schema):
    if not isinstance(proof,dict) or not re.fullmatch(r'[0-9a-f]{64}',str(proof.get('database_sha256',''))) or isinstance(proof.get('database_size_bytes'),bool) or not isinstance(proof.get('database_size_bytes'),int) or not 0 < proof['database_size_bytes'] <= 512*1024*1024:
        raise ReleaseReceiptError('migration qualification must bind exact staged database bytes')
    if not isinstance(proof,dict) or proof.get('verified') is not True or proof.get('staged_only') is not True or proof.get('schema_before')!=backup.get('database_schema_version') or proof.get('schema_after')!=schema or proof.get('backup_sha256')!=backup.get('sha256') or not proof.get('candidate_data_volume') or proof.get('candidate_data_volume')==rollback.get('data_volume') or proof.get('rollback_data_volume')!=rollback.get('data_volume'):
        raise ReleaseReceiptError('schema-changing release requires a qualified separate candidate and preserved rollback volume')


def validate_release_receipt(receipt: object) -> None:
    if not isinstance(receipt, dict) or receipt.get("receipt_version") != 1:
        raise ReleaseReceiptError("release receipt version is invalid")
    commit_sha = receipt.get("commit_sha")
    image_digest = receipt.get("image_digest")
    if not isinstance(commit_sha, str) or not SHA_RE.fullmatch(commit_sha):
        raise ReleaseReceiptError("release receipt commit SHA is invalid")
    if not isinstance(image_digest, str) or not DIGEST_RE.fullmatch(image_digest):
        raise ReleaseReceiptError("release receipt image digest is invalid")
    if receipt.get("version") != {"version": commit_sha}:
        raise ReleaseReceiptError("release receipt version does not match its commit SHA")
    if not isinstance(receipt.get("selected_port"), int) or not 1 <= receipt["selected_port"] <= 65535:
        raise ReleaseReceiptError("release receipt selected port is invalid")
    readiness = receipt.get("readiness")
    if not isinstance(readiness, dict) or readiness != {
        "status": "ready", "database": "ready", "authentication": "configured"
    }:
        raise ReleaseReceiptError("release receipt readiness is incomplete")
    schema = receipt.get("database_schema_version")
    backup = receipt.get("backup")
    rollback = receipt.get("rollback")
    if (
        not isinstance(schema, int)
        or not isinstance(backup, dict)
        or not isinstance(backup.get("archive"), str)
        or not backup["archive"]
        or Path(backup["archive"]).name != backup["archive"]
        or not isinstance(backup.get("database_schema_version"),int)
        or not isinstance(backup.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", backup["sha256"])
    ):
        raise ReleaseReceiptError("release receipt backup schema or checksum is invalid")
    if (
        not isinstance(rollback, dict)
        or rollback.get("database_schema_version") != backup.get("database_schema_version")
        or not isinstance(rollback.get("image_digest"), str)
        or not DIGEST_RE.fullmatch(rollback["image_digest"])
        or not isinstance(rollback.get("data_volume"), str)
        or not rollback["data_volume"]
        or not isinstance(rollback.get("backup_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", rollback["backup_sha256"])
        or rollback["backup_sha256"] != backup.get("sha256")
    ):
        raise ReleaseReceiptError("release receipt rollback pair is incompatible")
    if backup['database_schema_version'] != schema:
        verify_migration_qualification(receipt.get('migration_qualification'),backup,rollback,schema)
    try:
        created_at = datetime.fromisoformat(str(receipt["created_at"]))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise ReleaseReceiptError("release receipt timestamp is invalid") from exc


def _write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            validate_release_receipt(existing)
        except (OSError, json.JSONDecodeError, ReleaseReceiptError) as exc:
            raise ReleaseReceiptError("existing release receipt is invalid") from exc
        current_without_time = {key: value for key, value in receipt.items() if key != "created_at"}
        existing_without_time = {key: value for key, value in existing.items() if key != "created_at"}
        if existing_without_time == current_without_time:
            return
        raise ReleaseReceiptError("release receipt path already contains different evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify_parser = commands.add_parser("verify", help="validate a persisted release receipt")
    verify_parser.add_argument("receipt", type=Path)
    build_parser = commands.add_parser("build", help="verify release evidence and write a redacted receipt")
    build_parser.add_argument("--commit-sha", required=True)
    build_parser.add_argument("--image-digest", required=True)
    build_parser.add_argument("--database-schema-version", required=True, type=int)
    build_parser.add_argument("--backup-archive", required=True, type=Path)
    build_parser.add_argument("--backup-receipt", required=True, type=Path)
    build_parser.add_argument("--selected-port", required=True, type=int)
    build_parser.add_argument("--readiness-json", required=True, type=Path)
    build_parser.add_argument("--version-json", required=True, type=Path)
    build_parser.add_argument("--rollback-image-digest", required=True)
    build_parser.add_argument("--rollback-schema-version", required=True, type=int)
    build_parser.add_argument("--rollback-data-volume", required=True)
    build_parser.add_argument("--rollback-backup-sha256", required=True)
    build_parser.add_argument("--output", required=True, type=Path)
    build_parser.add_argument("--migration-qualification", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            validate_release_receipt(receipt)
        else:
            backup = verify_backup(
                args.backup_archive,
                expected_schema_version=args.rollback_schema_version,
                receipt_path=args.backup_receipt,
            )
            readiness = json.loads(args.readiness_json.read_text(encoding="utf-8"))
            version = json.loads(args.version_json.read_text(encoding="utf-8"))
            receipt = build_release_receipt(
                commit_sha=args.commit_sha,
                image_digest=args.image_digest,
                database_schema_version=args.database_schema_version,
                backup=backup,
                selected_port=args.selected_port,
                readiness=readiness,
                version=version,
                migration_qualification=json.loads(args.migration_qualification.read_text()) if args.migration_qualification else None,
                rollback={
                    "image_digest": args.rollback_image_digest,
                    "database_schema_version": args.rollback_schema_version,
                    "data_volume": args.rollback_data_volume,
                    "backup_sha256": args.rollback_backup_sha256,
                },
            )
            _write_receipt(args.output, receipt)
    except (OSError, json.JSONDecodeError, BackupError, ReleaseReceiptError, KeyError, TypeError):
        print("release receipt operation failed", file=sys.stderr)
        return 1
    print("release receipt=verified" if args.command == "verify" else f"release receipt=written path={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
