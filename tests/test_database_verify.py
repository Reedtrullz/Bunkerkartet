from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.db import CURRENT_SCHEMA_VERSION, Database


ROOT = Path(__file__).resolve().parents[1]


def test_verify_database_reads_valid_database_without_mutating_file(tmp_path):
    path = tmp_path / "valid.sqlite3"
    Database(path).initialize()
    before = hashlib.sha256(path.read_bytes()).digest()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_database.py",
            "--database",
            str(path),
            "--expected-version",
            str(CURRENT_SCHEMA_VERSION),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "sites=0" in result.stdout
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_verify_database_rejects_corrupt_or_wrong_schema(tmp_path):
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    result = subprocess.run(
        [sys.executable, "scripts/verify_database.py", "--database", str(corrupt), "--expected-version", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not sqlite" not in result.stderr

    wrong = tmp_path / "wrong.sqlite3"
    with sqlite3.connect(wrong) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    result = subprocess.run(
        [sys.executable, "scripts/verify_database.py", "--database", str(wrong), "--expected-version", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
