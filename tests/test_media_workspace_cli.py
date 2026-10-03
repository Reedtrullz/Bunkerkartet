"""Synthetic acceptance tests for explicit full-media backup and owner deletion."""

from __future__ import annotations

import base64
from dataclasses import asdict
import hashlib
import io
import json
import sqlite3
import stat
from pathlib import Path

from PIL import Image
import pytest

from app.config import Settings
from app.enrichment import ENRICHMENT_PATH
from app.image_attachments import AttachmentPolicy
from app.main import create_app
from scripts.media_workspace import (
    WorkspaceOperationError,
    build_deletion_preview,
    execute_deletion,
    load_owner_policy,
    main,
)
from started_client import StartedClient
from test_import_api import auth, commit_previewed, package


REPO = Path(__file__).resolve().parents[1]
TEST_TOKEN = "synthetic-cli-test-token"


def attachment_policy(*, enabled: bool = True, retain_original: bool = True) -> AttachmentPolicy:
    return AttachmentPolicy(
        enabled=enabled,
        owner_policy_id="synthetic-owner-policy",
        rights_status="permission_recorded",
        rights_reference="synthetic-fixture-only",
        retain_original=retain_original,
        retention_days=14,
    )


def policy_json(path: Path, *, enabled: bool = True, deletion: bool = True, retain_original: bool = True) -> Path:
    policy = attachment_policy(enabled=enabled, retain_original=retain_original)
    path.write_text(json.dumps({**asdict(policy), "deletion_enabled": deletion}))
    path.chmod(0o600)
    return path


def workspace(
    tmp_path: Path,
    *,
    policy_enabled: bool = True,
    deletion_enabled: bool = True,
    retain_original: bool = True,
):
    data_dir = tmp_path / "source"
    api_policy = attachment_policy(enabled=policy_enabled, retain_original=retain_original)
    api_policy_path = tmp_path / "api-owner-policy.json"
    api_policy_path.write_text(json.dumps(asdict(api_policy)))
    api_policy_path.chmod(0o600)
    owner_policy = policy_json(
        tmp_path / "owner-policy.json",
        enabled=policy_enabled,
        deletion=deletion_enabled,
        retain_original=retain_original,
    )
    api = StartedClient(
        create_app(
            Settings(
                data_dir=data_dir,
                admin_token=TEST_TOKEN,
                attachment_policy_path=api_policy_path,
            )
        )
    )
    assert commit_previewed(api, package(), token=TEST_TOKEN).status_code == 200
    stream = io.BytesIO()
    Image.new("RGB", (8, 6), (15, 90, 180)).save(stream, format="PNG")
    image_bytes = stream.getvalue()
    uploaded = api.post(
        "/api/sites/1/images",
        headers=auth(TEST_TOKEN),
        json={
            "expected_revision": 1,
            "mime": "image/png",
            "bytes_base64": base64.b64encode(image_bytes).decode("ascii"),
            "reason": "Synthetic attachment fixture",
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    image_id = uploaded.json()["receipt"]["attachment_id"]
    return {
        "api": api,
        "data_dir": data_dir,
        "database": data_dir / "bunkerkartet.sqlite3",
        "attachments": data_dir / "attachments",
        "image_id": image_id,
        "image_bytes": image_bytes,
        "policy": owner_policy,
        "api_policy": api_policy_path,
    }


def cli_policy(policy_path: Path) -> list[str]:
    return ["--policy", str(policy_path)]


def preview_cli(paths: dict, capsys, reason: str = "Owner-approved synthetic fixture deletion"):
    result = main(
        [
            "delete-preview",
            "--database",
            str(paths["database"]),
            "--attachments",
            str(paths["attachments"]),
            "--attachment-id",
            paths["image_id"],
            "--reason",
            reason,
            *cli_policy(paths["policy"]),
        ]
    )
    assert result == 0
    return json.loads(capsys.readouterr().out)


def test_policy_is_required_disabled_by_default_and_strictly_bounded(tmp_path):
    with pytest.raises(SystemExit) as missing_policy:
        main(["stage", "--archive", str(tmp_path / "missing.zip"), "--destination", str(tmp_path / "new")])
    assert missing_policy.value.code == 2

    disabled = policy_json(tmp_path / "disabled.json", enabled=False)
    loaded = load_owner_policy(disabled)
    assert loaded.attachment.enabled is False
    assert loaded.deletion_enabled is True
    assert main(
        [
            "stage",
            "--archive",
            str(tmp_path / "absent.bkmw"),
            "--destination",
            str(tmp_path / "restore"),
            *cli_policy(disabled),
        ]
    ) == 1
    assert not (tmp_path / "restore").exists()

    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"enabled":false,"token":"must-not-be-accepted"}')
    with pytest.raises(WorkspaceOperationError):
        load_owner_policy(unknown)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 32_001)
    with pytest.raises(WorkspaceOperationError):
        load_owner_policy(oversized)


@pytest.mark.parametrize("retain_original", [True, False])
def test_cli_export_stage_restores_archive_bytes_and_api_equivalent_content(tmp_path, capsys, retain_original):
    paths = workspace(tmp_path, retain_original=retain_original)
    archive = tmp_path / "backup.bkmw"
    assert main(
        [
            "export",
            "--database",
            str(paths["database"]),
            "--seed",
            str(ENRICHMENT_PATH),
            "--attachments",
            str(paths["attachments"]),
            "--archive",
            str(archive),
            "--project-root",
            str(REPO),
            *cli_policy(paths["policy"]),
        ]
    ) == 0
    export_receipt = json.loads(capsys.readouterr().out)
    assert export_receipt["format"] == "bunkerkartet-media-workspace"
    assert export_receipt["references"] == [f"bunkerkartet-image:{paths['image_id']}"]

    destination = tmp_path / "restored"
    assert main(
        [
            "stage",
            "--archive",
            str(archive),
            "--destination",
            str(destination),
            *cli_policy(paths["policy"]),
        ]
    ) == 0
    stage_receipt = json.loads(capsys.readouterr().out)
    assert stage_receipt["status"] == "staged-and-verified"
    staged_item = destination / "data/attachments" / paths["image_id"]
    source_item = paths["attachments"] / paths["image_id"]
    expected_names = {"derived.png", "thumbnail.jpg", "receipt.json"}
    if retain_original:
        expected_names.add("original.bin")
    assert {p.name for p in staged_item.iterdir()} == expected_names
    assert {p.name for p in staged_item.iterdir()} == {p.name for p in source_item.iterdir()}
    for source in source_item.iterdir():
        assert hashlib.sha256((staged_item / source.name).read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()

    restored = StartedClient(
        create_app(
            Settings(
                data_dir=destination / "data",
                enrichment_path=destination / "app/content/site_enrichment.json",
                admin_token=TEST_TOKEN,
                attachment_policy_path=paths["api_policy"],
            )
        )
    )
    headers = auth(TEST_TOKEN)
    original_response = restored.get(f"/api/images/{paths['image_id']}?variant=original", headers=headers)
    if retain_original:
        assert original_response.content == paths["image_bytes"]
    else:
        assert original_response.status_code == 409
    assert restored.get("/api/sites/1", headers=headers).json() == paths["api"].get("/api/sites/1", headers=headers).json()

    before_archive = archive.read_bytes()
    assert main(
        [
            "export",
            "--database",
            str(paths["database"]),
            "--seed",
            str(ENRICHMENT_PATH),
            "--attachments",
            str(paths["attachments"]),
            "--archive",
            str(archive),
            "--project-root",
            str(REPO),
            *cli_policy(paths["policy"]),
        ]
    ) == 1
    capsys.readouterr()
    assert archive.read_bytes() == before_archive
    before_database = (destination / "data/bunkerkartet.sqlite3").read_bytes()
    assert main(
        ["stage", "--archive", str(archive), "--destination", str(destination), *cli_policy(paths["policy"])]
    ) == 1
    capsys.readouterr()
    assert (destination / "data/bunkerkartet.sqlite3").read_bytes() == before_database


def test_preview_binds_reason_reference_site_revision_and_every_private_file(tmp_path, capsys):
    paths = workspace(tmp_path)
    preview = preview_cli(paths, capsys)
    assert preview["deletion_performed"] is False
    assert preview["requires_explicit_owner_command"] is True
    assert preview["preview_sha256"] == hashlib.sha256(
        json.dumps(preview["manifest"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    files = preview["manifest"]["files"]
    assert {item["path"] for item in files} == {
        "original.bin",
        "derived.png",
        "thumbnail.jpg",
        "receipt.json",
    }
    assert not (paths["data_dir"] / "media-deletion-receipts").exists()
    assert preview["manifest"]["immutable_references"] == [
        {"scheme": "bunkerkartet-image", "id": paths["image_id"]}
    ]
    assert all(
        item["sha256"] == hashlib.sha256((paths["attachments"] / paths["image_id"] / item["path"]).read_bytes()).hexdigest()
        for item in files
    )
    second_reason = preview_cli(paths, capsys, "Different explicit synthetic reason")
    assert second_reason["preview_sha256"] != preview["preview_sha256"]


def test_export_rejects_active_reference_receipt_that_differs_from_verified_bytes(tmp_path, capsys):
    paths = workspace(tmp_path)
    with sqlite3.connect(paths["database"]) as connection:
        row = connection.execute(
            "SELECT receipt_json FROM image_references WHERE image_id=?", (paths["image_id"],)
        ).fetchone()
        changed = json.loads(row[0])
        changed["rights_reference"] = "different-synthetic-receipt"
        connection.execute(
            "UPDATE image_references SET receipt_json=? WHERE image_id=?",
            (json.dumps(changed, sort_keys=True, separators=(",", ":")), paths["image_id"]),
        )
    archive = tmp_path / "mismatched-receipt.bkmw"
    result = main(
        [
            "export",
            "--database",
            str(paths["database"]),
            "--seed",
            str(ENRICHMENT_PATH),
            "--attachments",
            str(paths["attachments"]),
            "--archive",
            str(archive),
            "--project-root",
            str(REPO),
            *cli_policy(paths["policy"]),
        ]
    )
    assert result == 1
    capsys.readouterr()
    assert not archive.exists()


def test_stale_or_mismatched_preview_refuses_deletion_without_mutation(tmp_path, capsys):
    paths = workspace(tmp_path)
    preview = preview_cli(paths, capsys)
    item = paths["attachments"] / paths["image_id"]
    files_before = {p.name: p.read_bytes() for p in item.iterdir()}
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute("UPDATE sites SET revision=revision+1 WHERE id=1")
    with pytest.raises(WorkspaceOperationError):
        execute_deletion(
            paths["database"], paths["attachments"], paths["image_id"],
            preview["manifest"]["reason"], "0" * 64, load_owner_policy(paths["policy"]),
        )
    with pytest.raises(WorkspaceOperationError):
        execute_deletion(
            paths["database"], paths["attachments"], paths["image_id"],
            preview["manifest"]["reason"], preview["preview_sha256"], load_owner_policy(paths["policy"]),
        )
    assert {p.name: p.read_bytes() for p in item.iterdir()} == files_before
    with sqlite3.connect(paths["database"]) as connection:
        assert connection.execute("SELECT state FROM image_references WHERE image_id=?", (paths["image_id"],)).fetchone() == ("active",)
    assert not (paths["data_dir"] / "media-deletion-receipts").exists()


def test_explicit_owner_delete_tombstones_reference_and_records_byte_manifest(tmp_path, capsys):
    paths = workspace(tmp_path)
    preview = preview_cli(paths, capsys)
    reference_before = sqlite3.connect(paths["database"]).execute(
        "SELECT receipt_json FROM image_references WHERE image_id=?", (paths["image_id"],)
    ).fetchone()[0]
    reason = preview["manifest"]["reason"]
    result = main(
        [
            "delete-execute",
            "--database",
            str(paths["database"]),
            "--attachments",
            str(paths["attachments"]),
            "--attachment-id",
            paths["image_id"],
            "--reason",
            reason,
            "--preview-sha256",
            preview["preview_sha256"],
            *cli_policy(paths["policy"]),
        ]
    )
    assert result == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "complete"
    assert receipt["manifest"]["files"] == preview["manifest"]["files"]
    assert not (paths["attachments"] / paths["image_id"]).exists()
    assert (paths["attachments"]).stat().st_mode & 0o777 == 0o700
    recovery = Path(receipt["recovery_receipt"])
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
    row = sqlite3.connect(paths["database"]).execute(
        "SELECT receipt_json,state,deletion_reason FROM image_references WHERE image_id=?", (paths["image_id"],)
    ).fetchone()
    assert row == (reference_before, "deleted", reason)
    event = sqlite3.connect(paths["database"]).execute(
        "SELECT event_type,payload_json FROM site_events WHERE site_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event[0] == "image_deleted"
    event_payload = json.loads(event[1])
    assert event_payload["preview_sha256"] == preview["preview_sha256"]
    assert len(event_payload["manifest_sha256"]) == 64
    assert paths["api"].get(f"/api/images/{paths['image_id']}", headers=auth(TEST_TOKEN)).status_code == 410


def test_partial_byte_failure_tombstones_and_preserves_recovery_manifest(tmp_path, capsys):
    paths = workspace(tmp_path)
    preview = preview_cli(paths, capsys)
    calls = 0

    def fail_after_first_unlink(phase: str, _path: Path):
        nonlocal calls
        if phase == "after_unlink":
            calls += 1
            if calls == 1:
                raise OSError("synthetic interruption")

    with pytest.raises(WorkspaceOperationError):
        execute_deletion(
            paths["database"], paths["attachments"], paths["image_id"],
            preview["manifest"]["reason"], preview["preview_sha256"],
            load_owner_policy(paths["policy"]), fault_hook=fail_after_first_unlink,
        )
    row = sqlite3.connect(paths["database"]).execute(
        "SELECT state,receipt_json,deletion_reason FROM image_references WHERE image_id=?", (paths["image_id"],)
    ).fetchone()
    assert row[0] == "deleted"
    assert json.loads(row[1])["attachment_id"] == paths["image_id"]
    assert row[2].startswith("partial deletion unavailable:")
    event = sqlite3.connect(paths["database"]).execute(
        "SELECT event_type,payload_json FROM site_events WHERE site_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event[0] == "image_deletion_partial_failure"
    recovery_files = list((paths["data_dir"] / "media-deletion-receipts").glob("*.json"))
    assert len(recovery_files) == 1
    recovery = json.loads(recovery_files[0].read_text())
    assert recovery["status"] == "partial_failure_unavailable"
    assert recovery["deleted_files"] and recovery["remaining_files"]
    assert paths["api"].get(f"/api/images/{paths['image_id']}", headers=auth(TEST_TOKEN)).status_code == 410


def test_deletion_requires_disabled_by_default_owner_flag_and_exact_database_pair(tmp_path, capsys):
    paths = workspace(tmp_path, deletion_enabled=False)
    with pytest.raises(WorkspaceOperationError):
        build_deletion_preview(
            paths["database"], paths["attachments"], paths["image_id"],
            "Synthetic explicit reason", load_owner_policy(paths["policy"]),
        )
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(WorkspaceOperationError):
        build_deletion_preview(
            paths["database"], outside, paths["image_id"],
            "Synthetic explicit reason", load_owner_policy(policy_json(tmp_path / "enabled.json")),
        )
    assert (paths["attachments"] / paths["image_id"]).is_dir()


def test_corrupt_attachment_is_rejected_before_preview_and_cli_requires_explicit_delete_hash(tmp_path, capsys):
    paths = workspace(tmp_path)
    original = paths["attachments"] / paths["image_id"] / "original.bin"
    original.write_bytes(b"tampered synthetic bytes")
    with pytest.raises(WorkspaceOperationError):
        build_deletion_preview(
            paths["database"], paths["attachments"], paths["image_id"],
            "Synthetic explicit reason", load_owner_policy(paths["policy"]),
        )
    with pytest.raises(SystemExit) as missing_hash:
        main(
            [
                "delete-execute",
                "--database",
                str(paths["database"]),
                "--attachments",
                str(paths["attachments"]),
                "--attachment-id",
                paths["image_id"],
                "--reason",
                "Synthetic explicit reason",
                *cli_policy(paths["policy"]),
            ]
        )
    assert missing_hash.value.code == 2
    assert original.read_bytes() == b"tampered synthetic bytes"
