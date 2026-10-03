from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

from app.db import CURRENT_SCHEMA_VERSION, Database
from scripts.backup_database import (
    BackupError,
    check_latest_backup,
    create_backup,
    verify_backup,
)


def _database(path):
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) "
            "VALUES ('synthetic:backup', 'Synthetic backup site', 'bunker', 'unknown', 'explicit_coordinate', '2026-10-03', '2026-10-03')"
        )
    return database


def test_backup_receipt_checksums_a_verified_nonempty_sqlite_snapshot(tmp_path):
    database = _database(tmp_path / "bunkerkartet.sqlite3")
    archive = tmp_path / "bunkerkartet-20261003T120000Z.tar.gz"

    receipt = create_backup(database.path, archive)
    checked = verify_backup(archive)

    assert receipt["verified"] is True
    assert receipt["sha256"] == checked["sha256"]
    assert receipt["database_schema_version"] == CURRENT_SCHEMA_VERSION
    assert checked["archive_size_bytes"] == archive.stat().st_size
    assert archive.stat().st_mode & 0o077 == 0


def test_backup_status_detects_missing_stale_and_failed_runs(tmp_path):
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    with pytest.raises(BackupError, match="missing"):
        check_latest_backup(tmp_path, maximum_age_seconds=3600, now=now)

    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "old.tar.gz"
    create_backup(database.path, archive, created_at=now - timedelta(hours=3))
    with pytest.raises(BackupError, match="stale"):
        check_latest_backup(tmp_path, maximum_age_seconds=3600, now=now)

    receipt_path = tmp_path / "old.tar.gz.receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["created_at"] = now.isoformat()
    receipt_path.write_text(json.dumps(receipt))
    (tmp_path / "backup-failure.json").write_text(
        json.dumps({"status": "failed", "at": (now + timedelta(seconds=1)).isoformat()})
    )
    with pytest.raises(BackupError, match="failed"):
        check_latest_backup(tmp_path, maximum_age_seconds=3600, now=now + timedelta(seconds=2))


def test_backup_verification_rejects_changed_archive_and_size_over_limit(tmp_path):
    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "backup.tar.gz"
    create_backup(database.path, archive)
    archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(BackupError, match="checksum"):
        verify_backup(archive)

    fresh = tmp_path / "fresh.tar.gz"
    create_backup(database.path, fresh)
    with pytest.raises(BackupError, match="size"):
        verify_backup(fresh, max_archive_bytes=1)


def test_backup_creation_enforces_archive_limit_without_leaving_partial_output(tmp_path):
    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "too-small-limit.tar.gz"

    with pytest.raises(BackupError, match="size"):
        create_backup(database.path, archive, max_archive_bytes=1)

    assert not archive.exists()
    assert not Path(str(archive) + ".receipt.json").exists()


def test_backup_api_captures_committed_wal_rows_before_writer_closes(tmp_path):
    database = Database(tmp_path / "wal.sqlite3")
    database.initialize()
    writer = database.connect()
    writer.execute(
        "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) "
        "VALUES ('synthetic:wal', 'WAL site', 'bunker', 'unknown', 'explicit_coordinate', '2026-10-03', '2026-10-03')"
    )
    writer.commit()
    wal_path = Path(str(database.path) + "-wal")
    assert wal_path.is_file() and wal_path.stat().st_size > 0
    archive = tmp_path / "wal.tar.gz"

    create_backup(database.path, archive)
    writer.close()
    with tarfile.open(archive, "r:gz") as backup_archive:
        database_bytes = backup_archive.extractfile("bunkerkartet.sqlite3").read()
    restored_path = tmp_path / "restored.sqlite3"
    restored_path.write_bytes(database_bytes)
    with sqlite3.connect(restored_path) as restored:
        assert restored.execute("SELECT name FROM sites WHERE external_key='synthetic:wal'").fetchone()[0] == "WAL site"


def test_owner_policy_is_explicitly_disabled_without_deleting_any_backup():
    policy_path = Path(__file__).resolve().parents[1] / "docs/operations/backup-policy.json"
    policy = json.loads(policy_path.read_text())

    assert policy["enabled"] is False
    assert policy["schedule"] is None
    assert policy["offsite"]["enabled"] is False
    assert policy["rpo_hours"] is None
    assert policy["rto_hours"] is None
    assert policy["retention_days"] is None
    assert policy["owner_decision"] == "pending"
