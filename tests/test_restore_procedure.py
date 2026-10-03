import tarfile

import pytest

from app.db import CURRENT_SCHEMA_VERSION, Database
from scripts.backup_database import create_backup
from scripts.restore_database import RestoreError, restore_archive
from scripts.verify_database import verify


def _database(path):
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) "
            "VALUES ('synthetic:restore', 'Synthetic restore site', 'bunker', 'unknown', 'explicit_coordinate', '2026-10-03', '2026-10-03')"
        )
    return database


def test_restore_stages_a_verified_database_without_consuming_rollback_archive(tmp_path):
    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "rollback.tar.gz"
    create_backup(database.path, archive)
    destination = tmp_path / "staged-volume"

    result = restore_archive(archive, destination, expected_version=CURRENT_SCHEMA_VERSION)

    assert result["status"] == "staged-and-verified"
    assert destination.is_dir()
    assert archive.is_file()
    verify(destination / "bunkerkartet.sqlite3", CURRENT_SCHEMA_VERSION)


def test_restore_rejects_wrong_schema_and_archive_limits_before_staging(tmp_path):
    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "rollback.tar.gz"
    create_backup(database.path, archive)

    with pytest.raises(RestoreError, match="schema"):
        restore_archive(archive, tmp_path / "wrong-schema", expected_version=CURRENT_SCHEMA_VERSION + 1)
    with pytest.raises(RestoreError, match="size"):
        restore_archive(archive, tmp_path / "oversized", expected_version=CURRENT_SCHEMA_VERSION, max_archive_bytes=1)
    assert archive.is_file()
    assert not (tmp_path / "wrong-schema").exists()
    assert not (tmp_path / "oversized").exists()


@pytest.mark.parametrize("phase", ["extraction", "copy", "start", "readiness"])
def test_restore_injected_candidate_failure_keeps_archive_and_existing_volume(tmp_path, phase):
    database = _database(tmp_path / "source.sqlite3")
    archive = tmp_path / "rollback.tar.gz"
    create_backup(database.path, archive)
    existing = tmp_path / "active-volume"
    existing.mkdir()
    sentinel = existing / "must-survive"
    sentinel.write_text("previous generation")
    staging_target = tmp_path / "candidate-volume"

    def fail(stage, _staging_path):
        if stage == phase:
            raise OSError(f"injected {phase} failure")

    with pytest.raises(OSError, match=f"injected {phase} failure"):
        restore_archive(
            archive,
            staging_target,
            expected_version=CURRENT_SCHEMA_VERSION,
            stage_hook=fail,
        )

    assert archive.is_file()
    assert sentinel.read_text() == "previous generation"
    assert not staging_target.exists()
    assert not list(tmp_path.glob(".bunkerkartet-restore-*"))


def test_restore_rejects_malformed_archive_and_retains_the_archive_file(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"untrusted"
        import io
        member = tarfile.TarInfo("../outside")
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    destination = tmp_path / "staged"

    with pytest.raises(RestoreError):
        restore_archive(archive, destination, expected_version=CURRENT_SCHEMA_VERSION)

    assert archive.is_file()
    assert not destination.exists()
