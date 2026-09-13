import json
import sqlite3

from app.db import SCHEMA, Database


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
