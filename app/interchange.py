"""Bounded private workspace export and verified staging primitives."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile

from app.db import CURRENT_SCHEMA_VERSION, required_schema_errors
from app.enrichment import ResearchDocument
from app.json_input import decode_json_strict


FORMAT_NAME = "bunkerkartet-private-workspace"
FORMAT_VERSION = 1
DEFAULT_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_UNPACKED_BYTES = 500 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SEED_BYTES = 2 * 1024 * 1024
DATABASE_MEMBER = "workspace.sqlite3"
SEED_MEMBER = "seed/site_enrichment.json"
ARCHIVE_MEMBERS = frozenset({"manifest.json", DATABASE_MEMBER, SEED_MEMBER})
CHECKSUM_MEMBERS = ARCHIVE_MEMBERS - {"manifest.json"}
EXCLUSIONS = (
    ".env",
    "environment files",
    "tokens and credentials",
    "files outside the exchange allowlist",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ExchangeError(ValueError):
    """Raised when an exchange archive cannot be safely created or staged."""


def _hash_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ExchangeError("workspace member exceeds the configured size limit")
            digest.update(chunk)
    return digest.hexdigest(), size


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_seed(path: Path) -> tuple[bytes, int]:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SEED_BYTES:
            raise ExchangeError("workspace seed is missing or too large")
        data = path.read_bytes()
        if len(data) > MAX_SEED_BYTES:
            raise ExchangeError("workspace seed is missing or too large")
        document = ResearchDocument.model_validate(
            decode_json_strict(data, max_bytes=MAX_SEED_BYTES)
        )
        if len({site.external_key for site in document.sites}) != len(document.sites):
            raise ExchangeError("workspace seed identities are not unique")
    except ExchangeError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ExchangeError("workspace seed is invalid") from exc
    return data, len(document.sites)


def _git_build_sha(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExchangeError("workspace build identity is unavailable") from exc
    value = result.stdout.strip()
    if result.returncode != 0 or not _GIT_SHA_RE.fullmatch(value):
        raise ExchangeError("workspace build identity is unavailable")
    return value


def _source_tree_sha256(project_root: Path) -> str:
    """Fingerprint app and script source bytes without including the private data directory."""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(project_root), "ls-files", "-z", "--cached", "--others",
                "--exclude-standard", "--", "app", "scripts",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExchangeError("workspace source identity is unavailable") from exc
    if result.returncode != 0:
        raise ExchangeError("workspace source identity is unavailable")

    digest = hashlib.sha256()
    total_bytes = 0
    try:
        source_names = sorted(set(result.stdout.split(b"\0")) - {b""})
        for raw_name in source_names:
            relative = Path(os.fsdecode(raw_name))
            if relative.is_absolute() or ".." in relative.parts:
                raise ExchangeError("workspace source path is invalid")
            path = project_root / relative
            if path.is_symlink():
                size = 0
                content = os.readlink(path).encode("utf-8", "surrogateescape")
                marker = b"symlink\0"
                size = len(marker) + len(content)
            elif path.is_file():
                size = path.stat().st_size
                content = None
                marker = b""
            else:
                continue
            total_bytes += size
            if total_bytes > DEFAULT_MAX_UNPACKED_BYTES:
                raise ExchangeError("workspace source tree exceeds the configured size limit")
            name = relative.as_posix().encode("utf-8", "surrogateescape")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(size.to_bytes(8, "big"))
            if content is not None:
                digest.update(marker)
                digest.update(content)
            else:
                observed = 0
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        observed += len(chunk)
                        if (
                            observed > size
                            or total_bytes - size + observed > DEFAULT_MAX_UNPACKED_BYTES
                        ):
                            raise ExchangeError("workspace source tree changed during export")
                        digest.update(chunk)
                if observed != size:
                    raise ExchangeError("workspace source tree changed during export")
    except ExchangeError:
        raise
    except OSError as exc:
        raise ExchangeError("workspace source identity is unavailable") from exc
    return digest.hexdigest()


def _database_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        )
    ]
    counts: dict[str, int] = {}
    for name in names:
        escaped = name.replace('"', '""')
        counts[name] = int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])
    return counts


def _verify_database(
    path: Path,
    expected_counts: dict[str, int] | None = None,
) -> tuple[int, dict[str, int]]:
    if not path.is_file() or path.is_symlink():
        raise ExchangeError("workspace database is missing")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != CURRENT_SCHEMA_VERSION:
                raise ExchangeError("workspace database schema version is incompatible")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ExchangeError("workspace database integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ExchangeError("workspace database foreign key check failed")
            if required_schema_errors(connection):
                raise ExchangeError("workspace database is missing required schema")
            counts = _database_counts(connection)
    except ExchangeError:
        raise
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise ExchangeError("workspace database is invalid") from exc
    if expected_counts is not None and counts != expected_counts:
        raise ExchangeError("workspace database row counts do not match the manifest")
    return version, counts


def _validate_limits(max_archive_bytes: int, max_unpacked_bytes: int) -> None:
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in (max_archive_bytes, max_unpacked_bytes)
    ):
        raise ExchangeError("workspace size limits must be positive integers")


def _copy_database_snapshot(
    source_path: Path,
    destination_path: Path,
    max_unpacked_bytes: int,
) -> None:
    if not source_path.is_file() or source_path.is_symlink():
        raise ExchangeError("workspace database is missing")
    uri = source_path.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as source:
            if int(source.execute("PRAGMA user_version").fetchone()[0]) != CURRENT_SCHEMA_VERSION:
                raise ExchangeError("workspace database schema version is incompatible")
            page_size = int(source.execute("PRAGMA page_size").fetchone()[0])

            def check_size(_status: int, _remaining: int, total_pages: int) -> None:
                if total_pages * page_size > max_unpacked_bytes:
                    raise ExchangeError("workspace database exceeds the configured size limit")

            with closing(sqlite3.connect(destination_path)) as target:
                source.backup(target, pages=256, progress=check_size, sleep=0.01)
    except ExchangeError:
        raise
    except (OSError, sqlite3.DatabaseError) as exc:
        raise ExchangeError("workspace database snapshot failed") from exc
    if destination_path.stat().st_size > max_unpacked_bytes:
        raise ExchangeError("workspace database exceeds the configured size limit")


def create_workspace_archive(
    database_path: Path,
    seed_path: Path,
    archive_path: Path,
    *,
    project_root: Path,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    _media_bundle: bool = False,
) -> dict[str, object]:
    """Write a checksummed exchange archive without changing the source workspace."""
    _validate_limits(max_archive_bytes, max_unpacked_bytes)
    database_path, seed_path = Path(database_path), Path(seed_path)
    archive_path, project_root = Path(archive_path), Path(project_root).resolve()
    if archive_path.exists() or archive_path.is_symlink():
        raise ExchangeError("workspace archive destination already exists")
    try:
        seed_before, seed_count = _read_seed(seed_path)
        build_sha = _git_build_sha(project_root)
        source_tree_sha256 = _source_tree_sha256(project_root)
    except OSError as exc:
        raise ExchangeError("workspace export inputs are unavailable") from exc

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    created_archive = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=".bunkerkartet-workspace-export-",
            dir=archive_path.parent,
        ) as temporary:
            temporary_path = Path(temporary)
            snapshot_path = temporary_path / DATABASE_MEMBER
            _copy_database_snapshot(database_path, snapshot_path, max_unpacked_bytes)
            version, counts = _verify_database(snapshot_path)
            if not _media_bundle:
                with closing(sqlite3.connect(snapshot_path)) as check:
                    if check.execute("SELECT 1 FROM image_references WHERE state='active' LIMIT 1").fetchone():
                        raise ExchangeError("active image bytes require the complete media workspace format")
            seed_after, seed_count_after = _read_seed(seed_path)
            if seed_before != seed_after or seed_count != seed_count_after:
                raise ExchangeError("workspace seed changed during export")
            seed_snapshot = temporary_path / "site_enrichment.json"
            seed_snapshot.write_bytes(seed_before)

            checksums: dict[str, dict[str, object]] = {}
            for member, path in ((DATABASE_MEMBER, snapshot_path), (SEED_MEMBER, seed_snapshot)):
                sha256, size_bytes = _hash_file(path, max_bytes=max_unpacked_bytes)
                checksums[member] = {"sha256": sha256, "size_bytes": size_bytes}
            expanded_size = sum(int(value["size_bytes"]) for value in checksums.values())
            if expanded_size > max_unpacked_bytes:
                raise ExchangeError("workspace export exceeds the configured size limit")

            manifest: dict[str, object] = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "schema_version": version,
                "build_sha": build_sha,
                "source_tree_sha256": source_tree_sha256,
                "created_at": (
                    datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z")
                ),
                "seed": {
                    "member": SEED_MEMBER,
                    "identity": f"sha256:{_hash_bytes(seed_before)}",
                    "site_count": seed_count,
                },
                "counts": counts,
                "checksums": checksums,
                "exclusions": list(EXCLUSIONS),
            }
            manifest_bytes = (
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise ExchangeError("workspace manifest exceeds the configured size limit")

            temp_archive = temporary_path / "workspace.bkws"
            with zipfile.ZipFile(
                temp_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                archive.write(snapshot_path, arcname=DATABASE_MEMBER)
                archive.write(seed_snapshot, arcname=SEED_MEMBER)
            if temp_archive.stat().st_size > max_archive_bytes:
                raise ExchangeError("workspace archive exceeds the configured size limit")
            os.chmod(temp_archive, 0o600)
            with temp_archive.open("rb") as stream:
                os.fsync(stream.fileno())
            try:
                os.link(temp_archive, archive_path)
                created_archive = True
            except FileExistsError as exc:
                raise ExchangeError("workspace archive destination already exists") from exc
            except OSError as exc:
                raise ExchangeError("workspace archive could not be written") from exc
            temp_archive.unlink()
    except ExchangeError:
        if created_archive:
            archive_path.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.DatabaseError, zipfile.BadZipFile, ValueError) as exc:
        if created_archive:
            archive_path.unlink(missing_ok=True)
        raise ExchangeError("workspace export failed") from exc

    return {
        "status": "exported",
        "archive": str(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "member_count": len(ARCHIVE_MEMBERS),
    }


def _validate_member_name(name: str) -> None:
    if (
        not name
        or "\0" in name
        or "\\" in name
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or PurePosixPath(name).is_absolute()
    ):
        raise ExchangeError("workspace archive contains an unsafe member path")


def _archive_members(
    archive: zipfile.ZipFile,
    max_unpacked_bytes: int,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) != len(ARCHIVE_MEMBERS):
        raise ExchangeError("workspace archive member set is incomplete or contains extras")
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        original_name = getattr(info, "orig_filename", info.filename)
        if original_name != info.filename:
            raise ExchangeError("workspace archive contains an unsafe member path")
        _validate_member_name(original_name)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if (
            info.filename not in ARCHIVE_MEMBERS
            or info.filename in members
            or info.is_dir()
            or stat.S_ISLNK(mode)
            or file_type not in {0, stat.S_IFREG}
            or info.flag_bits & 0x1
            or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or info.file_size < 0
        ):
            raise ExchangeError("workspace archive contains an unsupported member")
        if info.filename == "manifest.json" and info.file_size > MAX_MANIFEST_BYTES:
            raise ExchangeError("workspace manifest exceeds the configured size limit")
        if info.filename == SEED_MEMBER and info.file_size > MAX_SEED_BYTES:
            raise ExchangeError("workspace seed exceeds the configured size limit")
        total += info.file_size
        if total > max_unpacked_bytes:
            raise ExchangeError("workspace archive exceeds the configured expanded size limit")
        members[info.filename] = info
    if set(members) != ARCHIVE_MEMBERS:
        raise ExchangeError("workspace archive member set is incomplete")
    return members


def _validate_manifest(data: bytes) -> dict[str, object]:
    try:
        manifest = decode_json_strict(data, max_bytes=MAX_MANIFEST_BYTES)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExchangeError("workspace manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ExchangeError("workspace manifest is invalid")
    expected_keys = {
        "format", "format_version", "schema_version", "build_sha", "source_tree_sha256",
        "created_at", "seed", "counts", "checksums", "exclusions",
    }
    if set(manifest) != expected_keys:
        raise ExchangeError("workspace manifest fields are unsupported")
    if manifest.get("format") != FORMAT_NAME:
        raise ExchangeError("workspace format is unsupported")
    version = manifest.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != FORMAT_VERSION:
        raise ExchangeError("workspace format version is unsupported")
    schema_version = manifest.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != CURRENT_SCHEMA_VERSION
    ):
        raise ExchangeError("workspace database schema version is incompatible")
    if (
        not isinstance(manifest.get("build_sha"), str)
        or not _GIT_SHA_RE.fullmatch(manifest["build_sha"])
    ):
        raise ExchangeError("workspace build identity is invalid")
    if (
        not isinstance(manifest.get("source_tree_sha256"), str)
        or not _SHA256_RE.fullmatch(manifest["source_tree_sha256"])
    ):
        raise ExchangeError("workspace source identity is invalid")
    if not isinstance(manifest.get("created_at"), str):
        raise ExchangeError("workspace creation time is invalid")
    try:
        created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        if created_at.utcoffset() is None:
            raise ValueError("timezone is missing")
    except ValueError as exc:
        raise ExchangeError("workspace creation time is invalid") from exc

    seed = manifest.get("seed")
    if (
        not isinstance(seed, dict)
        or set(seed) != {"member", "identity", "site_count"}
        or seed.get("member") != SEED_MEMBER
        or not isinstance(seed.get("identity"), str)
        or not seed["identity"].startswith("sha256:")
        or not _SHA256_RE.fullmatch(seed["identity"][7:])
        or isinstance(seed.get("site_count"), bool)
        or not isinstance(seed.get("site_count"), int)
        or seed["site_count"] < 0
    ):
        raise ExchangeError("workspace seed identity is invalid")

    counts = manifest.get("counts")
    if not isinstance(counts, dict) or any(
        not isinstance(name, str)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        for name, count in counts.items()
    ):
        raise ExchangeError("workspace table counts are invalid")
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != CHECKSUM_MEMBERS:
        raise ExchangeError("workspace checksums are incomplete")
    for entry in checksums.values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"sha256", "size_bytes"}
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256_RE.fullmatch(entry["sha256"])
            or isinstance(entry.get("size_bytes"), bool)
            or not isinstance(entry.get("size_bytes"), int)
            or entry["size_bytes"] < 0
        ):
            raise ExchangeError("workspace checksum entry is invalid")
    exclusions = manifest.get("exclusions")
    if (
        not isinstance(exclusions, list)
        or any(not isinstance(item, str) for item in exclusions)
        or not set(EXCLUSIONS) <= set(exclusions)
    ):
        raise ExchangeError("workspace exclusions are missing")
    return manifest


def _extract_verified_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    expected: dict[str, object],
    max_unpacked_bytes: int,
) -> None:
    digest = hashlib.sha256()
    written = 0
    try:
        with archive.open(info, "r") as source, destination.open("xb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_unpacked_bytes or written > int(expected["size_bytes"]):
                    raise ExchangeError("workspace member exceeds its verified size")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if written != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
            raise ExchangeError("workspace member checksum does not match")
        os.chmod(destination, 0o600)
    except ExchangeError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        destination.unlink(missing_ok=True)
        raise ExchangeError("workspace member could not be verified") from exc


def stage_workspace_archive(
    archive_path: Path,
    destination: Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    _media_bundle: bool = False,
) -> dict[str, object]:
    """Verify an exchange fully, then stage it as a new, isolated workspace payload."""
    _validate_limits(max_archive_bytes, max_unpacked_bytes)
    archive_path, destination = Path(archive_path), Path(destination)
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ExchangeError("workspace archive is missing")
    if archive_path.stat().st_size > max_archive_bytes:
        raise ExchangeError("workspace archive exceeds the configured size limit")
    if destination.exists() or destination.is_symlink():
        raise ExchangeError("workspace staging destination already exists")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = _archive_members(archive, max_unpacked_bytes)
            manifest_info = members["manifest.json"]
            manifest_bytes = archive.read(manifest_info)
            manifest = _validate_manifest(manifest_bytes)
            for member_name in CHECKSUM_MEMBERS:
                if (
                    manifest["checksums"][member_name]["size_bytes"]
                    != members[member_name].file_size
                ):
                    raise ExchangeError("workspace member size does not match the manifest")

            with tempfile.TemporaryDirectory(prefix="bunkerkartet-workspace-verify-") as temporary:
                verified_root = Path(temporary)
                database_copy = verified_root / DATABASE_MEMBER
                seed_copy = verified_root / "site_enrichment.json"
                _extract_verified_member(
                    archive, members[DATABASE_MEMBER], database_copy,
                    manifest["checksums"][DATABASE_MEMBER], max_unpacked_bytes,
                )
                _extract_verified_member(
                    archive, members[SEED_MEMBER], seed_copy,
                    manifest["checksums"][SEED_MEMBER], max_unpacked_bytes,
                )
                if f"sha256:{_hash_file(seed_copy)[0]}" != manifest["seed"]["identity"]:
                    raise ExchangeError("workspace seed identity does not match")
                try:
                    seed_document = ResearchDocument.model_validate(
                        decode_json_strict(seed_copy.read_bytes(), max_bytes=MAX_SEED_BYTES)
                    )
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    raise ExchangeError("workspace seed is invalid") from exc
                if (
                    len({site.external_key for site in seed_document.sites})
                    != len(seed_document.sites)
                    or len(seed_document.sites) != manifest["seed"]["site_count"]
                ):
                    raise ExchangeError("workspace seed does not match its manifest")
                _verify_database(database_copy, manifest["counts"])
                if not _media_bundle:
                    with closing(sqlite3.connect(database_copy)) as check:
                        if check.execute("SELECT 1 FROM image_references WHERE state='active' LIMIT 1").fetchone():
                            raise ExchangeError("active image bytes require the complete media workspace format")

                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    raise ExchangeError("workspace staging destination already exists")
                stage = Path(
                    tempfile.mkdtemp(
                        prefix=".bunkerkartet-workspace-stage-",
                        dir=destination.parent,
                    )
                )
                os.chmod(stage, 0o700)
                try:
                    data_dir = stage / "data"
                    app_dir = stage / "app"
                    seed_dir = app_dir / "content"
                    data_dir.mkdir(parents=True, mode=0o700)
                    app_dir.mkdir(mode=0o700)
                    seed_dir.mkdir(mode=0o700)
                    shutil.copyfile(database_copy, data_dir / "bunkerkartet.sqlite3")
                    shutil.copyfile(seed_copy, seed_dir / "site_enrichment.json")
                    (stage / "manifest.json").write_bytes(manifest_bytes)
                    for path in (
                        data_dir / "bunkerkartet.sqlite3",
                        seed_dir / "site_enrichment.json",
                        stage / "manifest.json",
                    ):
                        os.chmod(path, 0o600)
                    if destination.exists() or destination.is_symlink():
                        raise ExchangeError("workspace staging destination already exists")
                    os.rename(stage, destination)
                except Exception:
                    shutil.rmtree(stage, ignore_errors=True)
                    raise
    except ExchangeError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, sqlite3.DatabaseError, ValueError) as exc:
        raise ExchangeError("workspace archive failed verification or staging") from exc

    return {
        "status": "staged-and-verified",
        "destination": str(destination),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "automatic_merge": False,
        "automatic_live_overwrite": False,
    }
