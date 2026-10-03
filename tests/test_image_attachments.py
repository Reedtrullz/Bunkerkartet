from __future__ import annotations

import io
import stat
import struct
import zlib
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from app.image_attachments import (
    AttachmentError,
    AttachmentPolicy,
    list_images,
    delete_preview,
    read_image,
    restore_staged_backup,
    stage_backup,
    store_image,
)


def enabled_policy(**overrides: object) -> AttachmentPolicy:
    values: dict[str, object] = {
        "enabled": True,
        "owner_policy_id": "synthetic-owner-policy",
        "rights_status": "permission_recorded",
        "rights_reference": "synthetic-rights-record",
        "max_bytes": 2_000_000,
        "max_pixels": 1_000_000,
        "max_dimension": 2000,
        "thumbnail_max_edge": 64,
        "retain_original": False,
        "retention_days": 30,
    }
    values.update(overrides)
    return AttachmentPolicy(**values)


def image_bytes(*, fmt: str = "PNG", size: tuple[int, int] = (40, 30), exif_canary: bool = False) -> bytes:
    image = Image.new("RGB", size, (40, 90, 160))
    output = io.BytesIO()
    kwargs: dict[str, object] = {}
    if exif_canary:
        exif = Image.Exif()
        exif[270] = "PRIVATE-EXIF-CANARY"
        kwargs["exif"] = exif
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def highly_compressed_png(width: int, height: int) -> bytes:
    compressor = zlib.compressobj(level=9)
    compressed_parts = []
    row = b"\x00" + bytes(width * 3)
    for _ in range(height):
        part = compressor.compress(row)
        if part:
            compressed_parts.append(part)
    compressed_parts.append(compressor.flush())
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", b"".join(compressed_parts))
        + _png_chunk(b"IEND", b"")
    )


def test_attachments_are_disabled_without_owner_policy_and_create_no_files(tmp_path: Path) -> None:
    destination = tmp_path / "attachments"

    with pytest.raises(AttachmentError, match="disabled"):
        store_image(destination, image_bytes(), AttachmentPolicy())

    assert not destination.exists()


def test_read_and_preview_do_not_create_an_absent_store(tmp_path: Path) -> None:
    destination = tmp_path / "absent"
    policy = enabled_policy()
    assert list_images(destination, policy) == []
    with pytest.raises(AttachmentError):
        read_image(destination, "0" * 32, policy)
    with pytest.raises(AttachmentError):
        delete_preview(destination, "0" * 32, policy)
    assert not destination.exists()


def test_store_reencodes_metadata_free_derived_and_thumbnail_with_private_modes(tmp_path: Path) -> None:
    source = image_bytes(exif_canary=True)
    directory = tmp_path / "attachments"
    receipt = store_image(directory, source, enabled_policy(), declared_mime="image/png")

    assert receipt["original_sha256"]
    assert receipt["derived_sha256"] != receipt["original_sha256"]
    assert receipt["thumbnail_sha256"]
    assert receipt["original_retained"] is False
    assert receipt["immutable_reference"]["id"] == receipt["attachment_id"]
    assert receipt["metadata_policy"] == "derived_pixels_reencoded_without_metadata"

    derived = read_image(directory, receipt["attachment_id"], enabled_policy())
    thumb = read_image(directory, receipt["attachment_id"], enabled_policy(), variant="thumbnail")
    assert Image.open(io.BytesIO(derived)).getexif().get(270) is None
    assert Image.open(io.BytesIO(thumb)).getexif().get(270) is None
    assert b"PRIVATE-EXIF-CANARY" not in derived
    assert b"PRIVATE-EXIF-CANARY" not in thumb
    assert not (directory / receipt["attachment_id"] / "original.bin").exists()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (directory / receipt["attachment_id"]).iterdir())


@pytest.mark.parametrize(
    ("payload", "declared_mime"),
    [
        (image_bytes(fmt="JPEG"), "image/png"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
        (b"<html>not an image</html>", "image/jpeg"),
    ],
)
def test_rejects_mime_spoof_svg_and_html_without_persisting(
    tmp_path: Path, payload: bytes, declared_mime: str
) -> None:
    directory = tmp_path / "attachments"

    with pytest.raises(AttachmentError):
        store_image(directory, payload, enabled_policy(), declared_mime=declared_mime)

    assert not directory.exists()


def test_compressed_pixel_bomb_is_rejected_before_decode_or_persist(tmp_path: Path) -> None:
    payload = highly_compressed_png(1000, 1000)
    assert len(payload) < 100_000
    directory = tmp_path / "attachments"

    with pytest.raises(AttachmentError, match="pixel"):
        store_image(directory, payload, enabled_policy(max_pixels=100_000))

    assert not directory.exists()


def test_maximum_encoded_bytes_is_enforced_before_persist(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError, match="byte"):
        store_image(tmp_path / "attachments", image_bytes(), enabled_policy(max_bytes=8))


def test_original_retention_is_explicit_and_hashes_distinguish_original_and_derived(tmp_path: Path) -> None:
    source = image_bytes(fmt="JPEG", exif_canary=True)
    policy = enabled_policy(retain_original=True)
    receipt = store_image(tmp_path / "attachments", source, policy, declared_mime="image/jpeg")

    assert receipt["original_retained"] is True
    assert receipt["original_sha256"] != receipt["derived_sha256"]
    assert read_image(tmp_path / "attachments", receipt["attachment_id"], policy, variant="original") == source
    assert b"PRIVATE-EXIF-CANARY" not in read_image(
        tmp_path / "attachments", receipt["attachment_id"], policy
    )


def test_list_and_delete_preview_return_immutable_references_without_deleting(tmp_path: Path) -> None:
    directory = tmp_path / "attachments"
    policy = enabled_policy()
    receipt = store_image(directory, image_bytes(), policy)
    before = stage_backup(directory, policy)

    listed = list_images(directory, policy)
    preview = delete_preview(directory, receipt["attachment_id"], policy)
    after = stage_backup(directory, policy)

    assert listed[0]["immutable_reference"] == receipt["immutable_reference"]
    assert preview["deletion_performed"] is False
    assert preview["requires_explicit_owner_command"] is True
    assert preview["immutable_references"] == [receipt["immutable_reference"]]
    assert before.manifest_sha256 == after.manifest_sha256
    assert read_image(directory, receipt["attachment_id"], policy)


def test_staged_backup_restore_preserves_bytes_thumbnail_and_reference(tmp_path: Path) -> None:
    policy = enabled_policy(retain_original=True)
    source_dir = tmp_path / "attachments"
    receipt = store_image(source_dir, image_bytes(exif_canary=True), policy)
    snapshot = stage_backup(source_dir, policy)
    restored_dir = tmp_path / "restored"

    restored = restore_staged_backup(snapshot, restored_dir, policy)

    assert restored[0]["immutable_reference"] == receipt["immutable_reference"]
    assert read_image(restored_dir, receipt["attachment_id"], policy) == read_image(
        source_dir, receipt["attachment_id"], policy
    )
    assert read_image(restored_dir, receipt["attachment_id"], policy, variant="thumbnail") == read_image(
        source_dir, receipt["attachment_id"], policy, variant="thumbnail"
    )
    assert read_image(restored_dir, receipt["attachment_id"], policy, variant="original") == read_image(
        source_dir, receipt["attachment_id"], policy, variant="original"
    )


def test_staged_restore_rejects_tampered_member_and_does_not_replace_existing_store(tmp_path: Path) -> None:
    policy = enabled_policy()
    source_dir = tmp_path / "attachments"
    store_image(source_dir, image_bytes(), policy)
    snapshot = stage_backup(source_dir, policy)
    mutated_file = snapshot.files[0]
    tampered_files = tuple(
        replace(item, data=item.data + b"tamper") if item.path == mutated_file.path else item
        for item in snapshot.files
    )
    tampered = replace(snapshot, files=tampered_files)

    with pytest.raises(AttachmentError, match="digest"):
        restore_staged_backup(tampered, tmp_path / "bad-restore", policy)
    assert not (tmp_path / "bad-restore").exists()

    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "preserve.txt"
    sentinel.write_text("keep")
    with pytest.raises(AttachmentError, match="exists"):
        restore_staged_backup(snapshot, existing, policy)
    assert sentinel.read_text() == "keep"


def test_preview_and_reads_reject_traversal_and_detect_changed_content(tmp_path: Path) -> None:
    directory = tmp_path / "attachments"
    policy = enabled_policy()
    receipt = store_image(directory, image_bytes(), policy)

    with pytest.raises(AttachmentError):
        read_image(directory, "../outside", policy)
    derived_path = directory / receipt["attachment_id"] / "derived.png"
    derived_path.write_bytes(b"changed")
    with pytest.raises(AttachmentError, match="digest"):
        read_image(directory, receipt["attachment_id"], policy)


def test_owner_rights_and_metadata_policy_are_required_before_writing(tmp_path: Path) -> None:
    directory = tmp_path / "attachments"
    invalid_policies = [
        enabled_policy(rights_status="unknown"),
        enabled_policy(rights_reference=None),
        enabled_policy(owner_policy_id=None),
        enabled_policy(retention_days=None),
    ]

    for policy in invalid_policies:
        with pytest.raises(AttachmentError):
            store_image(directory, image_bytes(), policy)
        assert not directory.exists()
