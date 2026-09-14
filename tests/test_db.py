import json
import sqlite3

import pytest

import app.db as db_module
from app.db import CURRENT_SCHEMA_VERSION, SCHEMA, Database


def test_database_initialization_creates_v1_tables(tmp_path):
    database = Database(tmp_path / "bunkerkartet.sqlite3")
    database.initialize()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "sites",
        "sources",
        "evidence",
        "import_batches",
        "import_records",
        "route_plans",
        "field_observations",
    }.issubset(names)


def test_database_enables_foreign_keys(tmp_path):
    database = Database(tmp_path / "bunkerkartet.sqlite3")
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_database_migrates_confidence_column_for_existing_sites(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_schema = SCHEMA.replace("    confidence TEXT,\n", "")
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy_schema)
        connection.execute(
            """
            INSERT INTO sites
                (external_key, name, site_kind, latitude, longitude, precision,
                 uncertainty_m, location_basis, status, access, condition,
                 warnings_json, short_rationale, observed_location_text,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy:site",
                "Legacy site",
                "bunker",
                63.4,
                10.4,
                "approximate",
                100,
                "map_reference",
                "candidate",
                "unknown",
                None,
                "[]",
                None,
                None,
                "2026-09-13T00:00:00+00:00",
                "2026-09-13T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO import_records
                (batch_id, external_key, site_id, action, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-batch",
                "legacy:site",
                1,
                "created",
                json.dumps({"confidence": "high"}),
                "2026-09-13T00:00:00+00:00",
            ),
        )

    Database(path).initialize()

    with Database(path).connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
        confidence = connection.execute(
            "SELECT confidence FROM sites WHERE external_key = ?", ("legacy:site",)
        ).fetchone()[0]

    assert "confidence" in columns
    assert confidence == "high"


def test_database_initialization_is_idempotent_and_sets_v1(tmp_path):
    path = tmp_path / "bunkerkartet.sqlite3"
    database = Database(path)

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0


def test_database_rejects_incomplete_existing_schema_without_creating_tables(tmp_path):
    path = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(RuntimeError, match="required schema"):
        Database(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'sources'"
        ).fetchone()[0] == 0


def test_database_rejects_unknown_future_schema_without_changes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="future schema"):
        Database(path).initialize()


def test_database_rejects_non_sqlite_bytes(tmp_path):
    path = tmp_path / "not-sqlite.sqlite3"
    path.write_bytes(b"not a sqlite database")

    with pytest.raises(RuntimeError, match="database"):
        Database(path).initialize()


def test_v2_migration_rolls_back_ddl_and_retries_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")

    original = db_module._migrate_v2

    def fail_after_alter(connection):
        connection.execute("ALTER TABLE import_batches ADD COLUMN payload_hash TEXT")
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(db_module, "_migrate_v2", fail_after_alter)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        Database(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "payload_hash" not in {
            row[1] for row in connection.execute("PRAGMA table_info(import_batches)")
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'evidence_items'"
        ).fetchone()[0] == 0

    monkeypatch.setattr(db_module, "_migrate_v2", original)
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
