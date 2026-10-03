"""Bounded local image attachment storage for an owner-gated pilot.

The module never fetches URLs and accepts only decoded PNG/JPEG bytes. It
provides private-file storage, metadata-free derivatives, immutable references,
read/list/delete-preview operations, and in-memory staging qualification. API
authentication, database references, and production archive integration belong
to the parent application.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError


class AttachmentError(ValueError):
    """The owner policy, image bytes, or private store failed validation."""


_MIME_BY_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg"}
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SNAPSHOT_PATH_RE = re.compile(
    r"^(?P<id>[0-9a-f]{32})/(?P<name>original\.bin|derived\.(?:png|jpg)|thumbnail\.jpg|receipt\.json)$"
)
_HARD_MAX_BYTES = 25_000_000
_HARD_MAX_PIXELS = 20_000_000
_HARD_MAX_DIMENSION = 20_000
_HARD_MAX_STORE_BYTES = 500_000_000
_HARD_MAX_ATTACHMENTS = 10_000
_RECEIPT_NAME = "receipt.json"


@dataclass(frozen=True)
class AttachmentPolicy:
    """Explicit owner decisions and conservative technical limits.

    The default cannot read or write attachments. `retain_original` and
    `retention_days` must be chosen explicitly before enabling storage.
    """

    enabled: bool = False
    owner_policy_id: str | None = None
    rights_status: str = "unknown"
    rights_reference: str | None = None
    max_bytes: int = 8_000_000
    max_pixels: int = 8_000_000
    max_dimension: int = 7_000
    thumbnail_max_edge: int = 512
    retain_original: bool | None = None
    retention_days: int | None = None
    max_store_bytes: int = 100_000_000
    max_attachments: int = 1_000


@dataclass(frozen=True)
class StagedFile:
    path: str
    sha256: str
    data: bytes


@dataclass(frozen=True)
class StagedAttachmentBackup:
    version: int
    owner_policy_id: str
    files: tuple[StagedFile, ...]
    immutable_references: tuple[str, ...]
    manifest_sha256: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    value = dict(receipt)
    value.pop("receipt_sha256", None)
    return _sha256(_canonical_bytes(value))


def _require_policy(policy: AttachmentPolicy) -> None:
    if not isinstance(policy, AttachmentPolicy):
        raise AttachmentError("an explicit AttachmentPolicy is required")
    if not isinstance(policy.enabled, bool):
        raise AttachmentError("owner policy enabled must be boolean")
    if not policy.enabled:
        raise AttachmentError("image attachments are disabled by owner policy")
    if not isinstance(policy.owner_policy_id, str) or not policy.owner_policy_id.strip():
        raise AttachmentError("an owner policy identifier is required")
    if len(policy.owner_policy_id) > 120:
        raise AttachmentError("owner policy identifier is too long")
    if policy.rights_status != "permission_recorded" or not isinstance(policy.rights_reference, str) or not policy.rights_reference.strip():
        raise AttachmentError("recorded image rights are required by the owner policy")
    if policy.retain_original not in (True, False):
        raise AttachmentError("original retention must be an explicit owner decision")
    if not isinstance(policy.retention_days, int) or isinstance(policy.retention_days, bool) or not 1 <= policy.retention_days <= 3650:
        raise AttachmentError("a bounded retention period is required")
    limits = (
        (policy.max_bytes, 1, _HARD_MAX_BYTES, "max_bytes"),
        (policy.max_pixels, 1, _HARD_MAX_PIXELS, "max_pixels"),
        (policy.max_dimension, 1, _HARD_MAX_DIMENSION, "max_dimension"),
        (policy.thumbnail_max_edge, 1, 4096, "thumbnail_max_edge"),
        (policy.max_store_bytes, 1, _HARD_MAX_STORE_BYTES, "max_store_bytes"),
        (policy.max_attachments, 1, _HARD_MAX_ATTACHMENTS, "max_attachments"),
    )
    for value, minimum, maximum, field in limits:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise AttachmentError(f"{field} is outside the hard safety bound")


def _private_directory(path: Path) -> None:
    if os.path.lexists(path):
        try:
            info = path.lstat()
        except OSError as exc:
            raise AttachmentError("cannot inspect private attachment directory") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AttachmentError("attachment store must be a real directory")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise AttachmentError("attachment store permissions must be 0700")
    else:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            _private_directory(path)
        except OSError as exc:
            raise AttachmentError("cannot create private attachment directory") from exc
        _private_directory(path)


def _require_existing_private_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AttachmentError("attachment store does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AttachmentError("attachment store must be a real directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise AttachmentError("attachment store permissions must be 0700")


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise AttachmentError("could not write a private attachment file") from exc


def _check_encoded_image(data: bytes, policy: AttachmentPolicy, declared_mime: str | None) -> tuple[str, int, int, bytes, bytes]:
    if not isinstance(data, bytes):
        raise AttachmentError("image input must be immutable bytes")
    if not data or len(data) > policy.max_bytes:
        raise AttachmentError("image byte length is empty or exceeds the policy bound")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        expected_format = "PNG"
    elif data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        expected_format = "JPEG"
    else:
        raise AttachmentError("only PNG and JPEG magic signatures are accepted")
    expected_mime = _MIME_BY_FORMAT[expected_format]
    if declared_mime is not None:
        if not isinstance(declared_mime, str):
            raise AttachmentError("declared MIME type must be a string")
        normalized_mime = declared_mime.split(";", 1)[0].strip().lower()
        if normalized_mime != expected_mime:
            raise AttachmentError("declared MIME type does not match image magic")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format != expected_format:
                    raise AttachmentError("image magic does not match decoder format")
                width, height = probe.size
                if width <= 0 or height <= 0 or width > policy.max_dimension or height > policy.max_dimension:
                    raise AttachmentError("image dimensions exceed the policy bound")
                if width * height > policy.max_pixels:
                    raise AttachmentError("image pixel count exceeds the policy bound")
                if getattr(probe, "n_frames", 1) != 1:
                    raise AttachmentError("animated or multi-frame images are not accepted")
                probe.verify()
            with Image.open(io.BytesIO(data)) as image:
                if image.format != expected_format or image.size != (width, height):
                    raise AttachmentError("image header changed during decode")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                if expected_format == "JPEG":
                    pixels = oriented.convert("RGB")
                    clean = Image.new("RGB", pixels.size)
                    clean.paste(pixels)
                else:
                    has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                    mode = "RGBA" if has_alpha else "RGB"
                    pixels = oriented.convert(mode)
                    clean = Image.new(mode, pixels.size)
                    clean.paste(pixels)
                derived_stream = io.BytesIO()
                if expected_format == "JPEG":
                    clean.save(derived_stream, format="JPEG", quality=90, optimize=False, progressive=False)
                else:
                    clean.save(derived_stream, format="PNG", optimize=False)
                clean.thumbnail((policy.thumbnail_max_edge, policy.thumbnail_max_edge), Image.Resampling.LANCZOS)
                if clean.mode == "RGBA":
                    background = Image.new("RGBA", clean.size, (255, 255, 255, 255))
                    thumbnail_pixels = Image.alpha_composite(background, clean).convert("RGB")
                else:
                    thumbnail_pixels = clean.convert("RGB")
                thumbnail = Image.new("RGB", thumbnail_pixels.size)
                thumbnail.paste(thumbnail_pixels)
                thumbnail_stream = io.BytesIO()
                thumbnail.save(thumbnail_stream, format="JPEG", quality=85, optimize=False, progressive=False)
                return expected_format, width, height, derived_stream.getvalue(), thumbnail_stream.getvalue()
    except AttachmentError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise AttachmentError("image decode failed within the configured limits") from exc


def _expected_names(receipt: Mapping[str, Any]) -> dict[str, str]:
    attachment_id = receipt.get("attachment_id")
    image_format = receipt.get("format")
    if not isinstance(attachment_id, str) or not _ID_RE.fullmatch(attachment_id):
        raise AttachmentError("stored receipt has an invalid attachment id")
    if image_format not in {"PNG", "JPEG"}:
        raise AttachmentError("stored receipt has an invalid image format")
    result = {
        "derived": "derived.png" if image_format == "PNG" else "derived.jpg",
        "thumbnail": "thumbnail.jpg",
        "receipt": _RECEIPT_NAME,
    }
    if receipt.get("original_retained") is True:
        result["original"] = "original.bin"
    elif receipt.get("original_retained") is not False:
        raise AttachmentError("stored receipt has an invalid original retention flag")
    if receipt.get("stored_files") != result:
        raise AttachmentError("stored receipt contains unexpected filenames")
    return result


def _load_verified_receipt(directory: Path, attachment_id: str, policy: AttachmentPolicy) -> dict[str, Any]:
    if not isinstance(attachment_id, str) or not _ID_RE.fullmatch(attachment_id):
        raise AttachmentError("attachment id is invalid")
    item_dir = directory / attachment_id
    try:
        info = item_dir.lstat()
    except OSError as exc:
        raise AttachmentError("attachment does not exist") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise AttachmentError("attachment directory is not private")
    receipt_path = item_dir / _RECEIPT_NAME
    try:
        receipt_info = receipt_path.lstat()
        if not stat.S_ISREG(receipt_info.st_mode) or stat.S_ISLNK(receipt_info.st_mode):
            raise AttachmentError("attachment receipt is not a regular file")
        if stat.S_IMODE(receipt_info.st_mode) != 0o600:
            raise AttachmentError("attachment receipt permissions must be 0600")
        if receipt_info.st_size > 64_000:
            raise AttachmentError("attachment receipt exceeds its size bound")
        raw_receipt = receipt_path.read_bytes()
        receipt = json.loads(raw_receipt)
    except AttachmentError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise AttachmentError("attachment receipt cannot be read") from exc
    if not isinstance(receipt, dict) or receipt.get("attachment_id") != attachment_id:
        raise AttachmentError("attachment receipt identity does not match")
    if receipt.get("owner_policy_id") != policy.owner_policy_id:
        raise AttachmentError("attachment receipt belongs to another owner policy")
    if receipt.get("rights_status") != policy.rights_status or receipt.get("rights_reference") != policy.rights_reference:
        raise AttachmentError("attachment receipt rights differ from the active owner policy")
    if receipt.get("retention_days") != policy.retention_days:
        raise AttachmentError("attachment receipt retention differs from the active owner policy")
    if receipt.get("original_retained") is not policy.retain_original:
        raise AttachmentError("attachment receipt original-retention choice differs from owner policy")
    width = receipt.get("width_px")
    height = receipt.get("height_px")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or width > policy.max_dimension
        or height > policy.max_dimension
        or width * height > policy.max_pixels
    ):
        raise AttachmentError("attachment receipt image dimensions exceed the active owner policy")
    if receipt.get("immutable_reference") != {"scheme": "bunkerkartet-image", "id": attachment_id}:
        raise AttachmentError("attachment receipt immutable reference is invalid")
    if _canonical_bytes(receipt) != raw_receipt or receipt.get("receipt_sha256") != _receipt_digest(receipt):
        raise AttachmentError("attachment receipt digest is invalid")
    names = _expected_names(receipt)
    try:
        actual_names = {entry.name for entry in item_dir.iterdir()}
    except OSError as exc:
        raise AttachmentError("attachment files cannot be listed") from exc
    if actual_names != set(names.values()):
        raise AttachmentError("attachment store has missing or unexpected files")
    for variant, name in names.items():
        if variant == "receipt":
            continue
        path = item_dir / name
        try:
            file_info = path.lstat()
            if not stat.S_ISREG(file_info.st_mode) or stat.S_ISLNK(file_info.st_mode):
                raise AttachmentError("stored attachment variant is not a regular file")
            if stat.S_IMODE(file_info.st_mode) != 0o600:
                raise AttachmentError("stored attachment file permissions must be 0600")
            if file_info.st_size > policy.max_store_bytes:
                raise AttachmentError("stored attachment variant exceeds the store bound")
            contents = path.read_bytes()
        except AttachmentError:
            raise
        except OSError as exc:
            raise AttachmentError("stored attachment variant cannot be read") from exc
        digest_key = "original_sha256" if variant == "original" else f"{variant}_sha256"
        expected = receipt.get(digest_key)
        if not isinstance(expected, str) or _sha256(contents) != expected:
            raise AttachmentError(f"stored attachment {variant} digest mismatch")
    return receipt


def _directory_size(directory: Path) -> tuple[int, int]:
    if not directory.exists():
        return 0, 0
    total = 0
    count = 0
    for entry in directory.iterdir():
        if not _ID_RE.fullmatch(entry.name):
            raise AttachmentError("attachment store contains an unexpected entry")
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AttachmentError("attachment store contains a non-directory entry")
        count += 1
        if count > _HARD_MAX_ATTACHMENTS:
            raise AttachmentError("attachment count exceeds the hard safety bound")
        for child in entry.iterdir():
            child_info = child.lstat()
            if stat.S_ISLNK(child_info.st_mode) or not stat.S_ISREG(child_info.st_mode):
                raise AttachmentError("attachment store contains a non-regular file")
            total += child_info.st_size
            if total > _HARD_MAX_STORE_BYTES:
                raise AttachmentError("attachment store exceeds the hard safety bound")
    return total, count


def _enforce_store_limits(directory: Path, policy: AttachmentPolicy) -> tuple[int, int]:
    total, count = _directory_size(directory)
    if total > policy.max_store_bytes:
        raise AttachmentError("attachment store exceeds the owner policy byte limit")
    if count > policy.max_attachments:
        raise AttachmentError("attachment count exceeds the owner policy bound")
    return total, count


def store_image(
    directory: str | os.PathLike[str],
    data: bytes,
    policy: AttachmentPolicy,
    *,
    declared_mime: str | None = None,
) -> dict[str, Any]:
    """Decode, sanitize, and store one owner-authorized PNG/JPEG image."""

    _require_policy(policy)
    image_format, width, height, derived, thumbnail = _check_encoded_image(data, policy, declared_mime)
    root = Path(directory)
    if os.path.lexists(root):
        _private_directory(root)
        current_size, current_count = _enforce_store_limits(root, policy)
        for entry in root.iterdir():
            _load_verified_receipt(root, entry.name, policy)
    else:
        current_size, current_count = 0, 0
    if current_count >= policy.max_attachments:
        raise AttachmentError("attachment count exceeds the owner policy bound")
    if current_size + len(data if policy.retain_original else b"") + len(derived) + len(thumbnail) + 64_000 > policy.max_store_bytes:
        raise AttachmentError("attachment would exceed the owner store byte limit")

    _private_directory(root)
    attachment_id = uuid4().hex
    stage = root / f".{attachment_id}.{uuid4().hex}.stage"
    try:
        stage.mkdir(mode=0o700)
        stored_files = {
            "derived": "derived.png" if image_format == "PNG" else "derived.jpg",
            "thumbnail": "thumbnail.jpg",
            "receipt": _RECEIPT_NAME,
        }
        if policy.retain_original:
            stored_files["original"] = "original.bin"
        receipt: dict[str, Any] = {
            "receipt_version": 1,
            "attachment_id": attachment_id,
            "immutable_reference": {"scheme": "bunkerkartet-image", "id": attachment_id},
            "owner_policy_id": policy.owner_policy_id,
            "rights_status": policy.rights_status,
            "rights_reference": policy.rights_reference,
            "original_sha256": _sha256(data),
            "original_retained": policy.retain_original,
            "original_may_contain_metadata": policy.retain_original,
            "derived_sha256": _sha256(derived),
            "thumbnail_sha256": _sha256(thumbnail),
            "format": image_format,
            "mime_type": _MIME_BY_FORMAT[image_format],
            "width_px": width,
            "height_px": height,
            "max_pixels": policy.max_pixels,
            "retention_days": policy.retention_days,
            "metadata_policy": "derived_pixels_reencoded_without_metadata",
            "stored_files": stored_files,
        }
        if policy.retain_original:
            _write_private(stage / stored_files["original"], data)
        _write_private(stage / stored_files["derived"], derived)
        _write_private(stage / stored_files["thumbnail"], thumbnail)
        receipt["receipt_sha256"] = _receipt_digest(receipt)
        _write_private(stage / _RECEIPT_NAME, _canonical_bytes(receipt))
        final = root / attachment_id
        if os.path.lexists(final):
            raise AttachmentError("generated attachment identifier already exists")
        os.rename(stage, final)
        return receipt
    except AttachmentError:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    except OSError as exc:
        if stage.exists():
            shutil.rmtree(stage)
        raise AttachmentError("attachment write did not complete") from exc


def read_image(
    directory: str | os.PathLike[str],
    attachment_id: str,
    policy: AttachmentPolicy,
    *,
    variant: str = "derived",
) -> bytes:
    """Read and checksum-verify a stored derivative, thumbnail, or retained original."""

    _require_policy(policy)
    if variant not in {"derived", "thumbnail", "original"}:
        raise AttachmentError("variant must be derived, thumbnail, or original")
    root = Path(directory)
    _require_existing_private_directory(root)
    _enforce_store_limits(root, policy)
    receipt = _load_verified_receipt(root, attachment_id, policy)
    names = _expected_names(receipt)
    if variant not in names:
        raise AttachmentError("original bytes were not retained by policy")
    path = root / attachment_id / names[variant]
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > policy.max_store_bytes:
            raise AttachmentError("stored attachment variant failed file bounds")
        data = path.read_bytes()
        digest_key = "original_sha256" if variant == "original" else f"{variant}_sha256"
        if _sha256(data) != receipt.get(digest_key):
            raise AttachmentError(f"stored attachment {variant} digest mismatch")
        return data
    except AttachmentError:
        raise
    except OSError as exc:
        raise AttachmentError("stored attachment variant cannot be read") from exc


def list_images(directory: str | os.PathLike[str], policy: AttachmentPolicy) -> list[dict[str, Any]]:
    """Return checksum-verified immutable attachment references in stable order."""

    _require_policy(policy)
    root = Path(directory)
    if not os.path.lexists(root):
        return []
    _require_existing_private_directory(root)
    _enforce_store_limits(root, policy)
    result: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        receipt = _load_verified_receipt(root, entry.name, policy)
        result.append(
            {
                "attachment_id": receipt["attachment_id"],
                "immutable_reference": dict(receipt["immutable_reference"]),
                "original_sha256": receipt["original_sha256"],
                "derived_sha256": receipt["derived_sha256"],
                "thumbnail_sha256": receipt["thumbnail_sha256"],
                "mime_type": receipt["mime_type"],
                "width_px": receipt["width_px"],
                "height_px": receipt["height_px"],
                "original_retained": receipt["original_retained"],
            }
        )
    return result


def delete_preview(
    directory: str | os.PathLike[str], attachment_id: str, policy: AttachmentPolicy
) -> dict[str, Any]:
    """Describe deletion impact without removing bytes or mutable references."""

    _require_policy(policy)
    root = Path(directory)
    _require_existing_private_directory(root)
    _enforce_store_limits(root, policy)
    receipt = _load_verified_receipt(root, attachment_id, policy)
    names = _expected_names(receipt)
    files = []
    for variant, name in sorted(names.items()):
        if variant == "receipt":
            continue
        path = root / attachment_id / name
        data = path.read_bytes()
        digest_key = "original_sha256" if variant == "original" else f"{variant}_sha256"
        if _sha256(data) != receipt.get(digest_key):
            raise AttachmentError(f"stored attachment {variant} digest mismatch")
        files.append({"variant": variant, "sha256": _sha256(data), "bytes": len(data)})
    return {
        "attachment_id": attachment_id,
        "immutable_references": [dict(receipt["immutable_reference"])],
        "files": files,
        "deletion_performed": False,
        "requires_explicit_owner_command": True,
    }


def _snapshot_digest(owner_policy_id: str, files: tuple[StagedFile, ...], references: tuple[str, ...]) -> str:
    manifest = {
        "version": 1,
        "owner_policy_id": owner_policy_id,
        "files": [{"path": item.path, "sha256": item.sha256, "bytes": len(item.data)} for item in files],
        "immutable_references": list(references),
    }
    return _sha256(_canonical_bytes(manifest))


def stage_backup(
    directory: str | os.PathLike[str], policy: AttachmentPolicy
) -> StagedAttachmentBackup:
    """Create a bounded in-memory fixture snapshot of bytes and references.

    This is for local backup/restore qualification, not a production archive
    format or a replacement for the parent-owned backup pipeline.
    """

    _require_policy(policy)
    root = Path(directory)
    if not os.path.lexists(root):
        return StagedAttachmentBackup(1, policy.owner_policy_id or "", (), (), _snapshot_digest(policy.owner_policy_id or "", (), ()))
    _require_existing_private_directory(root)
    _enforce_store_limits(root, policy)
    files: list[StagedFile] = []
    references: list[str] = []
    total = 0
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        receipt = _load_verified_receipt(root, entry.name, policy)
        references.append(f"bunkerkartet-image:{entry.name}")
        for child in sorted((root / entry.name).iterdir(), key=lambda item: item.name):
            data = child.read_bytes()
            total += len(data)
            if total > policy.max_store_bytes:
                raise AttachmentError("staged attachment backup exceeds the owner store byte limit")
            files.append(StagedFile(f"{entry.name}/{child.name}", _sha256(data), data))
    ordered_files = tuple(sorted(files, key=lambda item: item.path))
    ordered_references = tuple(sorted(references))
    return StagedAttachmentBackup(
        1,
        policy.owner_policy_id or "",
        ordered_files,
        ordered_references,
        _snapshot_digest(policy.owner_policy_id or "", ordered_files, ordered_references),
    )


def restore_staged_backup(
    snapshot: StagedAttachmentBackup,
    destination: str | os.PathLike[str],
    policy: AttachmentPolicy,
) -> list[dict[str, Any]]:
    """Restore a fixture snapshot into a new private directory without overwrite."""

    _require_policy(policy)
    if not isinstance(snapshot, StagedAttachmentBackup) or snapshot.version != 1:
        raise AttachmentError("unsupported staged attachment backup")
    if snapshot.owner_policy_id != policy.owner_policy_id:
        raise AttachmentError("staged backup belongs to another owner policy")
    if len(snapshot.files) > policy.max_attachments * 4 + 1:
        raise AttachmentError("staged backup contains too many members")
    total = 0
    seen_paths: set[str] = set()
    members: dict[str, dict[str, bytes]] = {}
    for item in snapshot.files:
        if not isinstance(item, StagedFile) or not isinstance(item.data, bytes):
            raise AttachmentError("staged backup member is invalid")
        if item.path in seen_paths:
            raise AttachmentError("staged backup contains duplicate members")
        seen_paths.add(item.path)
        match = _SNAPSHOT_PATH_RE.fullmatch(item.path)
        if not match:
            raise AttachmentError("staged backup member path is not allowlisted")
        if _sha256(item.data) != item.sha256:
            raise AttachmentError("staged backup member digest mismatch")
        total += len(item.data)
        if total > policy.max_store_bytes:
            raise AttachmentError("staged backup exceeds the owner store byte limit")
        members.setdefault(match.group("id"), {})[match.group("name")] = item.data
    ordered = tuple(sorted(snapshot.files, key=lambda item: item.path))
    references = tuple(sorted(snapshot.immutable_references))
    if _snapshot_digest(snapshot.owner_policy_id, ordered, references) != snapshot.manifest_sha256:
        raise AttachmentError("staged backup manifest digest mismatch")
    if len(members) > policy.max_attachments:
        raise AttachmentError("staged backup attachment count exceeds the owner policy")

    destination_path = Path(destination)
    if os.path.lexists(destination_path):
        raise AttachmentError("restore destination already exists")
    parent = destination_path.parent
    if not parent.exists() or not parent.is_dir():
        raise AttachmentError("restore destination parent must already exist")
    stage = Path(tempfile.mkdtemp(prefix=".image-restore-", dir=parent))
    os.chmod(stage, 0o700)
    receipts: list[dict[str, Any]] = []
    try:
        for attachment_id, contents in sorted(members.items()):
            if _RECEIPT_NAME not in contents:
                raise AttachmentError("staged backup is missing an attachment receipt")
            receipt_data = contents[_RECEIPT_NAME]
            if len(receipt_data) > 64_000:
                raise AttachmentError("staged receipt exceeds its size bound")
            receipt = json.loads(receipt_data)
            if not isinstance(receipt, dict) or receipt.get("attachment_id") != attachment_id:
                raise AttachmentError("staged receipt identity does not match its directory")
            if receipt.get("owner_policy_id") != policy.owner_policy_id:
                raise AttachmentError("staged receipt belongs to another owner policy")
            names = _expected_names(receipt)
            expected_files = set(names.values())
            if set(contents) != expected_files:
                raise AttachmentError("staged backup has missing or unexpected attachment variants")
            if _canonical_bytes(receipt) != receipt_data or receipt.get("receipt_sha256") != _receipt_digest(receipt):
                raise AttachmentError("staged attachment receipt digest mismatch")
            item_dir = stage / attachment_id
            item_dir.mkdir(mode=0o700)
            for name, data in sorted(contents.items()):
                _write_private(item_dir / name, data)
        actual_references = tuple(f"bunkerkartet-image:{key}" for key in sorted(members))
        if actual_references != references:
            raise AttachmentError("staged backup immutable references do not match its members")
        for attachment_id in sorted(members):
            receipts.append(_load_verified_receipt(stage, attachment_id, policy))
        if os.path.lexists(destination_path):
            raise AttachmentError("restore destination already exists")
        os.rename(stage, destination_path)
        return receipts
    except AttachmentError:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if stage.exists():
            shutil.rmtree(stage)
        raise AttachmentError("staged attachment restore failed validation") from exc
