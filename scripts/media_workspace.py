#!/usr/bin/env python3
"""Owner-invoked bounded full-media workspace backup and deletion tools.

This CLI is a thin policy-gated wrapper around ``app.media_exchange``. It does
not schedule work, discover credentials, or activate media retention. Deletion
is a separate, explicit command bound to a read-only preview hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import dump_json, now_iso
from app.image_attachments import AttachmentError, AttachmentPolicy, delete_preview, stage_backup
from app.interchange import ExchangeError
from app.json_input import decode_json_strict
from app.media_exchange import export_media_workspace, stage_media_workspace


MAX_POLICY_BYTES = 32_000
MAX_REASON_LENGTH = 500
_IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELETION_FIELDS = {"deletion_enabled"}
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)"
)
_PRIVATE_NAMES = {"original.bin", "derived.png", "derived.jpg", "thumbnail.jpg", "receipt.json"}


class WorkspaceOperationError(ValueError):
    """A policy, filesystem, archive, or DB precondition failed safely."""


class OwnerPolicy:
    """Explicit local JSON policy. Deletion is opt-in separately from storage."""

    def __init__(self, attachment: AttachmentPolicy, deletion_enabled: bool):
        self.attachment = attachment
        self.deletion_enabled = deletion_enabled


def load_owner_policy(path: str | os.PathLike[str]) -> OwnerPolicy:
    """Load a small strict owner file; reject unknown keys and secret fields."""

    policy_path = Path(path)
    try:
        info = policy_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise WorkspaceOperationError("owner policy must be a regular non-symlink file")
        if info.st_size > MAX_POLICY_BYTES:
            raise WorkspaceOperationError("owner policy exceeds the 32 KiB limit")
        value = decode_json_strict(policy_path.read_bytes(), max_bytes=MAX_POLICY_BYTES)
        if not isinstance(value, dict):
            raise WorkspaceOperationError("owner policy must be a JSON object")
        from dataclasses import fields

        attachment_names = {field.name for field in fields(AttachmentPolicy)}
        unknown = set(value) - attachment_names - _DELETION_FIELDS
        if unknown:
            raise WorkspaceOperationError("owner policy contains unsupported fields")
        deletion_enabled = value.pop("deletion_enabled", False)
        if not isinstance(deletion_enabled, bool):
            raise WorkspaceOperationError("deletion_enabled must be boolean")
        return OwnerPolicy(AttachmentPolicy(**value), deletion_enabled)
    except WorkspaceOperationError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as error:
        raise WorkspaceOperationError("owner policy could not be loaded") from error


def _require_attachment_policy(policy: OwnerPolicy) -> AttachmentPolicy:
    if not isinstance(policy, OwnerPolicy):
        raise WorkspaceOperationError("an explicit owner policy file is required")
    return policy.attachment


def _require_delete_policy(policy: OwnerPolicy) -> AttachmentPolicy:
    attachment = _require_attachment_policy(policy)
    if policy.deletion_enabled is not True:
        raise WorkspaceOperationError("owner deletion policy is disabled")
    if not attachment.enabled:
        raise WorkspaceOperationError("image storage policy is disabled")
    return attachment


def _checked_paths(database: str | os.PathLike[str], attachments: str | os.PathLike[str]) -> tuple[Path, Path]:
    database_path = Path(database)
    attachment_path = Path(attachments)
    try:
        database_info = database_path.lstat()
        attachment_info = attachment_path.lstat()
    except OSError as error:
        raise WorkspaceOperationError("database or attachment directory is unavailable") from error
    if stat.S_ISLNK(database_info.st_mode) or not stat.S_ISREG(database_info.st_mode):
        raise WorkspaceOperationError("database must be a regular non-symlink file")
    if stat.S_ISLNK(attachment_info.st_mode) or not stat.S_ISDIR(attachment_info.st_mode):
        raise WorkspaceOperationError("attachment path must be a real directory")
    if database_path.name != "bunkerkartet.sqlite3" or attachment_path.name != "attachments":
        raise WorkspaceOperationError("database and attachment paths must be the owned workspace pair")
    if database_path.parent.resolve() != attachment_path.parent.resolve():
        raise WorkspaceOperationError("database and attachment paths must share their data directory")
    if stat.S_IMODE(attachment_info.st_mode) != 0o700:
        raise WorkspaceOperationError("attachment directory permissions must be 0700")
    return database_path, attachment_path


def _connect_readonly(database: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as error:
        raise WorkspaceOperationError("workspace database could not be opened read-only") from error


def _verify_db_media_receipts(
    database: str | os.PathLike[str],
    attachments: str | os.PathLike[str],
    policy: AttachmentPolicy,
) -> None:
    """Bind the active DB receipt objects to the parent-verified file snapshot."""

    database_path, attachment_path = _checked_paths(database, attachments)
    try:
        snapshot = stage_backup(attachment_path, policy)
        stored_receipts = {
            item.path: item.data
            for item in snapshot.files
            if item.path.endswith("/receipt.json")
        }
        connection = _connect_readonly(database_path)
        try:
            rows = connection.execute(
                "SELECT image_id,receipt_json FROM image_references WHERE state='active' ORDER BY image_id"
            ).fetchall()
        finally:
            connection.close()
        active_ids = {row["image_id"] for row in rows}
        stored_ids = {reference.removeprefix("bunkerkartet-image:") for reference in snapshot.immutable_references}
        if active_ids != stored_ids:
            raise WorkspaceOperationError("active database references and private image files differ")
        if len(rows) != len(active_ids):
            raise WorkspaceOperationError("database contains duplicate active image identifiers")
        for row in rows:
            file_receipt = stored_receipts.get(f"{row['image_id']}/receipt.json")
            if file_receipt is None:
                raise WorkspaceOperationError("an active database image receipt file is missing")
            database_receipt = decode_json_strict(row["receipt_json"].encode("utf-8"), max_bytes=64_000)
            filesystem_receipt = decode_json_strict(file_receipt, max_bytes=64_000)
            if not isinstance(database_receipt, dict) or database_receipt != filesystem_receipt:
                raise WorkspaceOperationError("active database and private image receipts differ")
    except WorkspaceOperationError:
        raise
    except (AttachmentError, sqlite3.Error, OSError, UnicodeDecodeError, ValueError, TypeError) as error:
        raise WorkspaceOperationError("media workspace receipt qualification failed") from error


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _check_reason(reason: str) -> str:
    if not isinstance(reason, str):
        raise WorkspaceOperationError("an explicit owner reason is required")
    cleaned = reason.strip()
    if not cleaned or len(cleaned) > MAX_REASON_LENGTH or "\n" in cleaned or "\r" in cleaned:
        raise WorkspaceOperationError("owner reason must be one nonempty line of at most 500 characters")
    if _CREDENTIAL_TEXT.search(cleaned):
        raise WorkspaceOperationError("owner reason must not contain credential-like text")
    return cleaned


def _image_row(connection: sqlite3.Connection, image_id: str) -> tuple[sqlite3.Row, sqlite3.Row, dict[str, Any]]:
    if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
        raise WorkspaceOperationError("attachment identifier is not server-generated format")
    try:
        row = connection.execute(
            "SELECT image_id,site_id,receipt_json,state FROM image_references WHERE image_id=?",
            (image_id,),
        ).fetchone()
        if row is None or row["state"] != "active":
            raise WorkspaceOperationError("active image reference is required")
        site = connection.execute("SELECT id,revision FROM sites WHERE id=?", (row["site_id"],)).fetchone()
        if site is None:
            raise WorkspaceOperationError("image site reference is missing")
        receipt = decode_json_strict(row["receipt_json"].encode("utf-8"), max_bytes=64_000)
        if not isinstance(receipt, dict) or receipt.get("attachment_id") != image_id:
            raise WorkspaceOperationError("database image receipt is invalid")
        return row, site, receipt
    except WorkspaceOperationError:
        raise
    except (sqlite3.Error, UnicodeEncodeError, ValueError, TypeError) as error:
        raise WorkspaceOperationError("database image reference failed qualification") from error


def _file_manifest(
    attachment_path: Path,
    image_id: str,
    policy: AttachmentPolicy,
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        verified_preview = delete_preview(attachment_path, image_id, policy)
        if verified_preview.get("deletion_performed") is not False:
            raise WorkspaceOperationError("parent image verifier returned an unexpected deletion result")
        stored = receipt.get("stored_files")
        if not isinstance(stored, dict) or set(stored) - {"original", "derived", "thumbnail", "receipt"}:
            raise WorkspaceOperationError("stored image file list is invalid")
        if stored.get("receipt") != "receipt.json" or stored.get("derived") not in {"derived.png", "derived.jpg"} or stored.get("thumbnail") != "thumbnail.jpg":
            raise WorkspaceOperationError("stored image file names are outside the server allowlist")
        if receipt.get("original_retained") is True:
            if stored.get("original") != "original.bin":
                raise WorkspaceOperationError("retained original file is not in the server allowlist")
        elif receipt.get("original_retained") is False:
            if "original" in stored:
                raise WorkspaceOperationError("unretained original unexpectedly has a stored file")
        else:
            raise WorkspaceOperationError("image receipt original-retention value is invalid")

        item_dir = attachment_path / image_id
        disk_receipt = (item_dir / "receipt.json").read_bytes()
        parsed_receipt = decode_json_strict(disk_receipt, max_bytes=64_000)
        if not isinstance(parsed_receipt, dict) or parsed_receipt != receipt:
            raise WorkspaceOperationError("database and filesystem image receipts differ")
        names = set(stored.values())
        if names != {"receipt.json", "thumbnail.jpg", stored["derived"]} | ({"original.bin"} if "original" in stored else set()):
            raise WorkspaceOperationError("stored image file inventory is inconsistent")
        actual = {entry.name for entry in item_dir.iterdir()}
        if actual != names:
            raise WorkspaceOperationError("image directory has missing or unexpected files")

        files: list[dict[str, Any]] = []
        for name in sorted(names):
            path = item_dir / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise WorkspaceOperationError("image deletion target must be a regular file")
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > policy.max_store_bytes:
                raise WorkspaceOperationError("image deletion target permissions or size failed policy")
            content = path.read_bytes()
            if name != "receipt.json":
                variant = next(key for key, filename in stored.items() if filename == name)
                hash_key = "original_sha256" if variant == "original" else f"{variant}_sha256"
                if hashlib.sha256(content).hexdigest() != receipt.get(hash_key):
                    raise WorkspaceOperationError("image deletion target digest differs from its receipt")
            files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)})
        # Cross-check the parent's public preview lists every image variant.
        parent_variants = {item["variant"] for item in verified_preview.get("files", [])}
        expected_variants = set(stored) - {"receipt"}
        if parent_variants != expected_variants:
            raise WorkspaceOperationError("parent image verifier and deletion manifest disagree")
        return files
    except WorkspaceOperationError:
        raise
    except (AttachmentError, OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, StopIteration) as error:
        raise WorkspaceOperationError("image bytes failed parent verification") from error


def _preview_on_connection(
    connection: sqlite3.Connection,
    database: Path,
    attachments: Path,
    image_id: str,
    reason: str,
    policy: OwnerPolicy,
) -> dict[str, Any]:
    attachment_policy = _require_delete_policy(policy)
    cleaned_reason = _check_reason(reason)
    row, site, receipt = _image_row(connection, image_id)
    _checked_paths(database, attachments)
    files = _file_manifest(attachments, image_id, attachment_policy, receipt)
    manifest = {
        "format": "bunkerkartet-media-deletion-preview",
        "version": 1,
        "attachment_id": image_id,
        "owner_policy_id": attachment_policy.owner_policy_id,
        "reason": cleaned_reason,
        "database_reference": {
            "site_id": int(row["site_id"]),
            "site_revision": int(site["revision"]),
            "receipt_sha256": hashlib.sha256(row["receipt_json"].encode("utf-8")).hexdigest(),
        },
        "immutable_references": [dict(receipt["immutable_reference"])],
        "files": files,
    }
    return {
        "preview_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "manifest": manifest,
        "deletion_performed": False,
        "requires_explicit_owner_command": True,
    }


def build_deletion_preview(
    database: str | os.PathLike[str],
    attachments: str | os.PathLike[str],
    image_id: str,
    reason: str,
    policy: OwnerPolicy,
) -> dict[str, Any]:
    """Return a read-only preview bound to active DB/site state and all bytes."""

    database_path, attachment_path = _checked_paths(database, attachments)
    connection = _connect_readonly(database_path)
    try:
        return _preview_on_connection(
            connection, database_path, attachment_path, image_id, reason, policy
        )
    finally:
        connection.close()


def _recovery_directory(database: Path) -> Path:
    directory = database.parent / "media-deletion-receipts"
    try:
        if not os.path.lexists(directory):
            directory.mkdir(mode=0o700)
        info = directory.lstat()
    except OSError as error:
        raise WorkspaceOperationError("private recovery receipt directory is unavailable") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise WorkspaceOperationError("recovery receipt directory must be a private 0700 directory")
    return directory


def _write_receipt_new(path: Path, value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise WorkspaceOperationError("could not create the private recovery receipt") from error


def _replace_receipt(path: Path, value: dict[str, Any]) -> None:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise WorkspaceOperationError("recovery receipt path changed unsafely")
        fd, temporary_name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(_canonical(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
    except WorkspaceOperationError:
        raise
    except OSError as error:
        raise WorkspaceOperationError("could not update the private recovery receipt") from error


def _existing_paths(directory: Path, image_id: str, names: list[str]) -> list[str]:
    item_dir = directory / image_id
    remaining = []
    for name in names:
        if (item_dir / name).exists() or (item_dir / name).is_symlink():
            remaining.append(name)
    return remaining


def _record_deletion_event(
    connection: sqlite3.Connection,
    site_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    timestamp = now_iso()
    connection.execute(
        "UPDATE sites SET revision=revision+1,updated_at=? WHERE id=?",
        (timestamp, site_id),
    )
    connection.execute(
        "INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (site_id, event_type, dump_json(payload), timestamp),
    )


def execute_deletion(
    database: str | os.PathLike[str],
    attachments: str | os.PathLike[str],
    image_id: str,
    reason: str,
    expected_preview_sha256: str,
    policy: OwnerPolicy,
    *,
    fault_hook: Callable[[str, Path], None] | None = None,
) -> dict[str, Any]:
    """Execute one explicit hash-confirmed deletion with durable recovery proof.

    The active DB reference is tombstoned under ``BEGIN IMMEDIATE`` before any
    bytes are unlinked, so concurrent API readers fail closed. A private
    recovery receipt is created first. If byte removal fails, the DB tombstone
    remains and the receipt lists deleted and remaining file digests.
    """

    if not isinstance(expected_preview_sha256, str) or not _SHA256.fullmatch(expected_preview_sha256):
        raise WorkspaceOperationError("an explicit 64-character preview hash is required")
    database_path, attachment_path = _checked_paths(database, attachments)
    attachment_policy = _require_delete_policy(policy)
    cleaned_reason = _check_reason(reason)
    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    receipt_path: Path | None = None
    recovery: dict[str, Any] | None = None
    receipt_created = False
    preview: dict[str, Any] | None = None
    row_site_id: int | None = None
    tombstoned = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        preview = _preview_on_connection(
            connection, database_path, attachment_path, image_id, cleaned_reason, policy
        )
        if preview["preview_sha256"] != expected_preview_sha256:
            raise WorkspaceOperationError("owner preview is stale or does not match current bytes and references")
        row, site, _receipt = _image_row(connection, image_id)
        row_site_id = int(row["site_id"])
        file_names = [entry["path"] for entry in preview["manifest"]["files"]]
        manifest_digest = hashlib.sha256(_canonical(preview["manifest"])).hexdigest()
        recovery_dir = _recovery_directory(database_path)
        receipt_path = recovery_dir / f"{image_id}-{expected_preview_sha256}.json"
        recovery = {
            "format": "bunkerkartet-media-deletion-recovery",
            "version": 1,
            "status": "prepared",
            "created_at": now_iso(),
            "attachment_id": image_id,
            "site_id": row_site_id,
            "reason": cleaned_reason,
            "preview_sha256": expected_preview_sha256,
            "manifest_sha256": manifest_digest,
            "manifest": preview["manifest"],
            "deleted_files": [],
            "remaining_files": file_names,
        }
        _write_receipt_new(receipt_path, recovery)
        receipt_created = True

        timestamp = now_iso()
        changed = connection.execute(
            "UPDATE image_references SET state='deleted',deleted_at=?,deletion_reason=? WHERE image_id=? AND state='active'",
            (timestamp, cleaned_reason, image_id),
        ).rowcount
        if changed != 1:
            raise WorkspaceOperationError("active image reference changed before owner deletion")
        started_payload = {
            "reason": cleaned_reason,
            "preview_sha256": expected_preview_sha256,
            "manifest_sha256": manifest_digest,
            "status": "bytes_pending_explicit_owner_delete",
            "recovery_receipt": receipt_path.name,
        }
        _record_deletion_event(connection, row_site_id, "image_deletion_started", started_payload)
        connection.commit()
        tombstoned = True

        recovery["status"] = "in_progress"
        _replace_receipt(receipt_path, recovery)
        item_dir = attachment_path / image_id
        deleted: list[dict[str, Any]] = []
        for item in preview["manifest"]["files"]:
            path = item_dir / item["path"]
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise WorkspaceOperationError("private image file changed before deletion")
            contents = path.read_bytes()
            if len(contents) != item["size_bytes"] or hashlib.sha256(contents).hexdigest() != item["sha256"]:
                raise WorkspaceOperationError("private image bytes changed after the deletion preview")
            check_info = path.lstat()
            if (check_info.st_dev, check_info.st_ino) != (info.st_dev, info.st_ino):
                raise WorkspaceOperationError("private image file identity changed during deletion")
            path.unlink()
            deleted.append(item)
            recovery["deleted_files"] = [entry["path"] for entry in deleted]
            recovery["remaining_files"] = _existing_paths(attachment_path, image_id, file_names)
            _replace_receipt(receipt_path, recovery)
            if fault_hook is not None:
                fault_hook("after_unlink", path)
        item_dir.rmdir()

        connection.execute("BEGIN IMMEDIATE")
        _record_deletion_event(
            connection,
            row_site_id,
            "image_deleted",
            {
                "reason": cleaned_reason,
                "preview_sha256": expected_preview_sha256,
                "manifest_sha256": manifest_digest,
                "byte_deletion_manifest": preview["manifest"]["files"],
                "recovery_receipt": receipt_path.name,
            },
        )
        connection.commit()
        recovery["status"] = "complete"
        recovery["deleted_files"] = file_names
        recovery["remaining_files"] = []
        recovery["completed_at"] = now_iso()
        _replace_receipt(receipt_path, recovery)
        return {"status": "complete", "preview_sha256": expected_preview_sha256, "recovery_receipt": str(receipt_path), "manifest": preview["manifest"]}
    except Exception as error:
        if connection.in_transaction:
            connection.rollback()
        if tombstoned and receipt_path is not None and recovery is not None and preview is not None and row_site_id is not None:
            names = [entry["path"] for entry in preview["manifest"]["files"]]
            deleted_names = recovery.get("deleted_files", [])
            remaining = _existing_paths(attachment_path, image_id, names)
            recovery["status"] = "partial_failure_unavailable"
            recovery["deleted_files"] = deleted_names
            recovery["remaining_files"] = remaining
            recovery["failure_class"] = type(error).__name__
            recovery["recovery_action"] = "image reference is tombstoned; reconcile only this server-owned directory using the manifest"
            try:
                _replace_receipt(receipt_path, recovery)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE image_references SET deletion_reason=? WHERE image_id=? AND state='deleted'",
                    (f"partial deletion unavailable: {cleaned_reason}", image_id),
                )
                _record_deletion_event(
                    connection,
                    row_site_id,
                    "image_deletion_partial_failure",
                    {
                        "preview_sha256": expected_preview_sha256,
                        "manifest_sha256": recovery["manifest_sha256"],
                        "reason": cleaned_reason,
                        "deleted_files": deleted_names,
                        "remaining_files": remaining,
                        "recovery_receipt": receipt_path.name,
                    },
                )
                connection.commit()
            except Exception as recovery_error:
                if connection.in_transaction:
                    connection.rollback()
                recovery["status"] = "recovery_required_db_or_receipt_update"
                recovery["recovery_error_class"] = type(recovery_error).__name__
                try:
                    _replace_receipt(receipt_path, recovery)
                except Exception:
                    pass
                raise WorkspaceOperationError("deletion stopped after tombstoning; inspect the private recovery receipt") from error
        elif receipt_created and receipt_path is not None and recovery is not None:
            if not tombstoned:
                recovery["status"] = "aborted_before_tombstone"
                try:
                    _replace_receipt(receipt_path, recovery)
                except Exception:
                    pass
        if isinstance(error, WorkspaceOperationError):
            raise
        if isinstance(error, (OSError, sqlite3.Error, AttachmentError, ExchangeError, ValueError, TypeError)):
            raise WorkspaceOperationError("owner deletion failed safely; check its recovery receipt if created") from error
        raise
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="create a bounded complete database/attachment archive")
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--seed", type=Path, required=True)
    export.add_argument("--attachments", type=Path, required=True)
    export.add_argument("--archive", type=Path, required=True)
    export.add_argument("--project-root", type=Path, required=True)
    export.add_argument("--policy", type=Path, required=True, help="explicit owner media policy JSON")

    stage = commands.add_parser("stage", help="verify and restore a complete archive into a new directory")
    stage.add_argument("--archive", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--policy", type=Path, required=True, help="explicit owner media policy JSON")

    for name, help_text in (
        ("delete-preview", "show a read-only, hash-bound owner deletion proposal"),
        ("delete-execute", "tombstone and delete only after explicit preview hash and reason"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--attachments", type=Path, required=True)
        command.add_argument("--attachment-id", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--policy", type=Path, required=True, help="explicit owner media policy JSON")
        if name == "delete-execute":
            command.add_argument("--preview-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = load_owner_policy(args.policy)
        if args.command == "export":
            attachment_policy = _require_attachment_policy(policy)
            _verify_db_media_receipts(args.database, args.attachments, attachment_policy)
            result = export_media_workspace(
                args.database,
                args.seed,
                args.attachments,
                args.archive,
                attachment_policy,
                project_root=args.project_root,
            )
        elif args.command == "stage":
            attachment_policy = _require_attachment_policy(policy)
            result = stage_media_workspace(args.archive, args.destination, attachment_policy)
            staged_data = args.destination / "data"
            _verify_db_media_receipts(
                staged_data / "bunkerkartet.sqlite3",
                staged_data / "attachments",
                attachment_policy,
            )
        elif args.command == "delete-preview":
            result = build_deletion_preview(
                args.database, args.attachments, args.attachment_id, args.reason, policy
            )
        else:
            result = execute_deletion(
                args.database,
                args.attachments,
                args.attachment_id,
                args.reason,
                args.preview_sha256,
                policy,
            )
    except (WorkspaceOperationError, AttachmentError, ExchangeError, OSError, sqlite3.Error, ValueError, TypeError) as error:
        # The error text is intentionally generic; do not spill policy paths,
        # database contents, image bytes, or credential-like owner text.
        print(f"media workspace {args.command} failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
