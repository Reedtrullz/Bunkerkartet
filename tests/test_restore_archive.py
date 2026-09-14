from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from app.db import Database


ROOT = Path(__file__).resolve().parents[1]


def _run(archive: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify_restore_archive.py", "--archive", str(archive)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_documented_tar_backup_with_nonempty_synthetic_database_is_accepted(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    database = Database(data / "bunkerkartet.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sites
                (external_key, name, site_kind, precision, location_basis, created_at, updated_at)
            VALUES ('synthetic:1', 'Synthetic site', 'bunker', 'unknown',
                    'landmark_description', '2026-09-14', '2026-09-14')
            """
        )
    archive = tmp_path / "backup.tar.gz"
    subprocess.run(
        ["tar", "-czf", str(archive), "-C", str(data), "."],
        check=True,
        env={**os.environ, "COPYFILE_DISABLE": "1"},
    )

    result = _run(archive)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("archive=ok")


def test_restore_archive_rejects_traversal_links_and_duplicates(tmp_path):
    database = tmp_path / "bunkerkartet.sqlite3"
    Database(database).initialize()
    cases = {
        "traversal": [("../outside", b"x")],
        "symlink": [("bunkerkartet.sqlite3", "../../etc/passwd")],
        "duplicate": [("bunkerkartet.sqlite3", b"first"), ("./bunkerkartet.sqlite3", b"second")],
    }

    for name, members in cases.items():
        archive = tmp_path / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for member_name, value in members:
                if isinstance(value, str):
                    info = tarfile.TarInfo(member_name)
                    info.type = tarfile.SYMTYPE
                    info.linkname = value
                    tar.addfile(info)
                else:
                    info = tarfile.TarInfo(member_name)
                    info.size = len(value)
                    tar.addfile(info, io.BytesIO(value))
        result = _run(archive)
        assert result.returncode != 0
        assert "outside" not in result.stderr
