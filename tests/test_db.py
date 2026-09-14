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
        assert {row[1] for row in connection.execute("PRAGMA table_info(field_observations)")} >= {
            "point_role", "uncertainty_m"
        }
        assert {row[1] for row in connection.execute("PRAGMA table_info(sites)")} >= {
            "approach_latitude", "approach_longitude", "approach_access", "approach_note",
            "approach_reviewed_at", "location_review_required",
        }
        assert {row[1] for row in connection.execute("PRAGMA table_info(route_plans)")} >= {
            "stops_json", "route_warnings_json"
        }


def test_v2_migration_reconstructs_site_specific_legacy_evidence(tmp_path):
    path = tmp_path / "legacy-catalog.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        for external_key in ("legacy:a", "legacy:b"):
            connection.execute(
                """
                INSERT INTO sites
                    (external_key, name, site_kind, precision, location_basis, created_at, updated_at)
                VALUES (?, ?, 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')
                """,
                (external_key, external_key),
            )
        source_id = connection.execute(
            """
            INSERT INTO sources
                (url, title, source_type, excerpt, published_at, accessed_at, created_at, updated_at)
            VALUES ('https://example.com/shared', 'Shared', 'test', 'Overwritten global excerpt',
                    '2026-09-14', '2026-09-14', '2026-09-14', '2026-09-14')
            """
        ).lastrowid
        payloads = [
            {"external_key": "legacy:a", "excerpt": "Site A excerpt", "publication_date": "2026-09-01", "access_date": None},
            {"external_key": "legacy:b", "excerpt": "Site B excerpt", "publication_date": None, "access_date": "2026-09-02"},
        ]
        for site_id, source in enumerate(payloads, start=1):
            batch_id = f"legacy-batch-{site_id}"
            connection.execute(
                "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES (?, '1.0', '2026-09-14T00:00:00+00:00', '2026-09-14', '2026-09-14')",
                (batch_id,),
            )
            payload = {
                **source,
                "name": source["external_key"],
                "site_kind": "bunker",
                "geometry": None,
                "precision": "unknown",
                "uncertainty_m": None,
                "location_basis": "landmark_description",
                "status": "candidate",
                "access": "unknown",
                "sources": [{
                    "url": "https://example.com/shared",
                    "title": "Shared",
                    "source_type": "test",
                    "excerpt": source["excerpt"],
                    "publication_date": source["publication_date"],
                    "access_date": source["access_date"],
                }],
            }
            connection.execute(
                "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES (?, ?, ?, 'created', ?, '2026-09-14')",
                (batch_id, source["external_key"], site_id, json.dumps(payload)),
            )
            connection.execute(
                "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (?, ?, 'source', '2026-09-14')",
                (site_id, source_id),
            )

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT site_id, excerpt, published_at, accessed_at, provenance_status, legacy_evidence_id FROM evidence_items ORDER BY site_id"
        ).fetchall()
    assert rows == [
        (1, "Site A excerpt", "2026-09-01", None, "import_record", None),
        (2, "Site B excerpt", None, "2026-09-02", "import_record", None),
    ]


def test_v2_migration_reconstructs_all_historical_same_site_source_items(tmp_path):
    path = tmp_path / "legacy-same-source.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:site', 'Legacy site', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        source_id = connection.execute(
            "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES ('https://example.com/shared', 'Shared', 'test', 'latest', '2026-09-14', '2026-09-14')"
        ).lastrowid
        connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'source', '2026-09-14')",
            (source_id,),
        )
        for batch_id, excerpt, published_at, accessed_at in (
            ('legacy-batch-1', 'first historical excerpt', '2026-09-01', '2026-09-02'),
            ('legacy-batch-2', 'second historical excerpt', '2026-09-03', '2026-09-04'),
        ):
            connection.execute(
                "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES (?, '1.0', '2026-09-14T00:00:00+00:00', '2026-09-14', '2026-09-14')",
                (batch_id,),
            )
            payload = {
                "external_key": "legacy:site", "name": "Legacy site", "site_kind": "bunker",
                "geometry": None, "precision": "unknown", "uncertainty_m": None,
                "location_basis": "landmark_description", "status": "candidate", "access": "unknown",
                "sources": [{"url": "https://example.com/shared", "title": "Shared", "source_type": "test", "excerpt": excerpt, "publication_date": published_at, "access_date": accessed_at}],
            }
            connection.execute(
                "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES (?, 'legacy:site', 1, 'created', ?, '2026-09-14')",
                (batch_id, json.dumps(payload)),
            )

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT excerpt, published_at, accessed_at, provenance_status FROM evidence_items ORDER BY id"
        ).fetchall()
    assert rows == [
        ("first historical excerpt", "2026-09-01", "2026-09-02", "import_record"),
        ("second historical excerpt", "2026-09-03", "2026-09-04", "import_record"),
    ]


def test_v4_repair_migrates_existing_v2_history_forward(tmp_path):
    path = tmp_path / "existing-v2.sqlite3"
    payload = {
        "external_key": "legacy:site",
        "sources": [{"url": "https://example.com/shared", "excerpt": "first"}],
    }
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:site', 'Legacy site', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        source_id = connection.execute(
            "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES ('https://example.com/shared', 'Shared', 'test', 'first', '2026-09-14', '2026-09-14')"
        ).lastrowid
        connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'source', '2026-09-14')",
            (source_id,),
        )
        connection.execute(
            "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES ('batch-1', '1.0', '2026-09-14T00:00:00Z', '2026-09-14', '2026-09-14')"
        )
        connection.execute(
            "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES ('batch-1', 'legacy:site', 1, 'created', ?, '2026-09-14')",
            (json.dumps(payload),),
        )
        db_module._migrate_v2(connection)
        connection.execute(
            "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES ('batch-2', '1.0', '2026-09-14T00:00:00Z', '2026-09-14', '2026-09-14')"
        )
        connection.execute(
            "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES ('batch-2', 'legacy:site', 1, 'updated', ?, '2026-09-14')",
            (json.dumps({**payload, "sources": [{"url": "https://example.com/shared", "excerpt": "second"}]}),),
        )
        connection.execute("PRAGMA user_version = 2")

    Database(path).initialize()

    with Database(path).connect() as connection:
        rows = connection.execute(
            "SELECT excerpt FROM evidence_items WHERE provenance_status = 'import_record' ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == ["first", "second"]


def test_legacy_evidence_without_import_records_is_safe_across_retries(tmp_path):
    path = tmp_path / "legacy-unresolved.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:site', 'Legacy site', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        source_id = connection.execute(
            "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES ('https://example.com/legacy', 'Legacy', 'test', 'unresolved excerpt', '2026-09-14', '2026-09-14')"
        ).lastrowid
        connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'source', '2026-09-14')",
            (source_id,),
        )

    Database(path).initialize()
    Database(path).initialize()

    with Database(path).connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*), MIN(provenance_status), MIN(legacy_evidence_id) FROM evidence_items"
        ).fetchone()
        foreign_key = next(
            item for item in connection.execute("PRAGMA foreign_key_list(evidence_items)")
            if item[3] == "legacy_evidence_id"
        )
    assert tuple(row) == (1, "legacy_unresolved", 1)
    assert foreign_key[6] == "SET NULL"


def test_v5_repairs_old_legacy_evidence_fk_without_losing_rows(tmp_path):
    path = tmp_path / "old-fk.sqlite3"
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:site', 'Legacy site', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        source_id = connection.execute(
            "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES ('https://example.com/legacy-fk', 'Legacy', 'test', 'kept', '2026-09-14', '2026-09-14')"
        ).lastrowid
        evidence_id = connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'source', '2026-09-14')",
            (source_id,),
        ).lastrowid
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ALTER TABLE evidence_items RENAME TO evidence_items_good")
        connection.execute(
            """
            CREATE TABLE evidence_items (
                id INTEGER PRIMARY KEY,
                site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                import_record_id INTEGER REFERENCES import_records(id),
                source_index INTEGER,
                legacy_evidence_id INTEGER UNIQUE REFERENCES evidence(id),
                excerpt TEXT NOT NULL, content_kind TEXT NOT NULL DEFAULT 'unknown',
                role TEXT NOT NULL DEFAULT 'context', published_at TEXT, accessed_at TEXT,
                provenance_status TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(import_record_id, source_index)
            )
            """
        )
        connection.execute(
            "INSERT INTO evidence_items SELECT id, site_id, source_id, import_record_id, source_index, legacy_evidence_id, excerpt, content_kind, role, published_at, accessed_at, provenance_status, created_at FROM evidence_items_good WHERE 0"
        )
        connection.execute(
            "INSERT INTO evidence_items (site_id, source_id, legacy_evidence_id, excerpt, provenance_status, created_at) VALUES (1, ?, ?, 'kept', 'legacy_unresolved', '2026-09-14')",
            (source_id, evidence_id),
        )
        connection.execute("DROP TABLE evidence_items_good")
        connection.execute("PRAGMA user_version = 4")

    database.initialize()

    with database.connect() as connection:
        row = connection.execute("SELECT excerpt FROM evidence_items WHERE legacy_evidence_id = ?", (evidence_id,)).fetchone()
        foreign_key = next(item for item in connection.execute("PRAGMA foreign_key_list(evidence_items)") if item[3] == "legacy_evidence_id")
    assert row[0] == "kept"
    assert foreign_key[6] == "SET NULL"


def test_v6_repairs_a_database_that_already_recorded_v5(tmp_path):
    path = tmp_path / "existing-v5.sqlite3"
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:v5', 'Legacy v5 site', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        source_id = connection.execute(
            "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES ('https://example.com/v5', 'v5', 'test', 'kept', '2026-09-14', '2026-09-14')"
        ).lastrowid
        connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'source', '2026-09-14')",
            (source_id,),
        )
        connection.execute("PRAGMA user_version = 5")

    database.initialize()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        row = connection.execute(
            "SELECT excerpt, provenance_status FROM evidence_items WHERE legacy_evidence_id = 1"
        ).fetchone()
    assert version == 8
    assert tuple(row) == ("kept", "legacy_unresolved")


def test_v7_creates_site_relations_and_migrates_existing_import_keys(tmp_path):
    path = tmp_path / "existing-v6.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 6")
        connection.execute(
            "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES ('legacy:one', 'Legacy one', 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')"
        )
        connection.execute(
            "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES ('legacy-relations', '1.0', '2026-09-14T00:00:00Z', '2026-09-14', '2026-09-14')"
        )
        connection.execute(
            "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES ('legacy-relations', 'legacy:one', 1, 'created', ?, '2026-09-14')",
            (json.dumps({"external_key": "legacy:one", "related_site_keys": ["legacy:missing"]}),),
        )

    Database(path).initialize()

    with Database(path).connect() as connection:
        relation = connection.execute(
            "SELECT site_id, related_external_key, relation_kind FROM site_relations"
        ).fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == 8
    assert tuple(relation) == (1, "legacy:missing", "related")


def test_v8_adds_site_revision_and_observation_request_identity(tmp_path):
    path = tmp_path / "existing-v7.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        db_module._migrate_v2(connection)
        db_module._migrate_v3(connection)
        db_module._migrate_v4(connection)
        db_module._migrate_v5(connection)
        db_module._migrate_v6(connection)
        db_module._migrate_v7(connection)
    database = Database(path)

    database.initialize()

    with database.connect() as connection:
        site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
        observation_columns = {row[1] for row in connection.execute("PRAGMA table_info(field_observations)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(field_observations)")}
    assert version == 8
    assert "revision" in site_columns
    assert {"request_id", "payload_hash"} <= observation_columns
    assert "idx_field_observations_request" in indexes
