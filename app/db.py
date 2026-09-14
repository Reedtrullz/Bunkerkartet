from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    external_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    site_kind TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    precision TEXT NOT NULL,
    uncertainty_m REAL,
    location_basis TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    access TEXT NOT NULL DEFAULT 'unknown',
    confidence TEXT,
    condition TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    short_rationale TEXT,
    observed_location_text TEXT,
    merged_into_id INTEGER REFERENCES sites(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status IN ('candidate', 'approximate', 'likely', 'trusted', 'field-verified', 'destroyed-or-filled', 'rejected')),
    CHECK (access IN ('public', 'private', 'restricted', 'unknown', 'permission_required', 'dangerous', 'unsafe'))
);

CREATE INDEX IF NOT EXISTS idx_sites_status ON sites(status);
CREATE INDEX IF NOT EXISTS idx_sites_kind ON sites(site_kind);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    source_type TEXT,
    excerpt TEXT,
    published_at TEXT,
    accessed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'source',
    created_at TEXT NOT NULL,
    UNIQUE (site_id, source_id, role)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS import_records (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    external_key TEXT NOT NULL,
    site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (batch_id, external_key)
);

CREATE TABLE IF NOT EXISTS site_events (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS field_observations (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    note TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    observed_location_text TEXT,
    access_notes TEXT,
    photo_urls_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    CHECK (outcome IN ('found', 'not_found', 'inaccessible', 'needs_follow_up')),
    CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE INDEX IF NOT EXISTS idx_field_observations_site
    ON field_observations(site_id, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS route_plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start_json TEXT NOT NULL,
    waypoints_json TEXT NOT NULL,
    distance_m REAL NOT NULL,
    duration_s REAL NOT NULL,
    geometry_json TEXT NOT NULL,
    gpx_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

BASE_REQUIRED_SCHEMA = {
    "sites": {
        "id", "external_key", "name", "site_kind", "latitude", "longitude",
        "precision", "uncertainty_m", "location_basis", "status", "access",
        "confidence", "condition", "warnings_json", "short_rationale", "observed_location_text",
        "merged_into_id", "created_at", "updated_at",
    },
    "sources": {
        "id", "url", "title", "source_type", "excerpt", "published_at",
        "accessed_at", "created_at", "updated_at",
    },
    "evidence": {"id", "site_id", "source_id", "role", "created_at"},
    "import_batches": {
        "id", "batch_id", "schema_version", "generated_at", "created_at", "committed_at",
    },
    "import_records": {
        "id", "batch_id", "external_key", "site_id", "action", "payload_json", "created_at",
    },
    "site_events": {"id", "site_id", "event_type", "payload_json", "created_at"},
    "field_observations": {
        "id", "site_id", "observed_at", "outcome", "note", "latitude", "longitude",
        "observed_location_text", "access_notes", "photo_urls_json", "created_at",
    },
    "route_plans": {
        "id", "name", "start_json", "waypoints_json", "distance_m", "duration_s",
        "geometry_json", "gpx_text", "created_at",
    },
}
V2_REQUIRED_SCHEMA = {table: set(columns) for table, columns in BASE_REQUIRED_SCHEMA.items()}
V2_REQUIRED_SCHEMA["import_batches"].add("payload_hash")
V2_REQUIRED_SCHEMA["evidence_items"] = {
    "id", "site_id", "source_id", "import_record_id", "source_index", "legacy_evidence_id",
    "excerpt", "content_kind", "role", "published_at", "accessed_at", "provenance_status",
    "created_at",
}
V3_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V2_REQUIRED_SCHEMA.items()}
V3_REQUIRED_SCHEMA["field_observations"].update({"point_role", "uncertainty_m"})
V4_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V3_REQUIRED_SCHEMA.items()}
V4_REQUIRED_SCHEMA["sites"].update({
    "approach_latitude", "approach_longitude", "approach_access", "approach_note", "approach_reviewed_at",
})
V4_REQUIRED_SCHEMA["route_plans"].add("stops_json")
V5_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V4_REQUIRED_SCHEMA.items()}
V5_REQUIRED_SCHEMA["sites"].add("location_review_required")
V5_REQUIRED_SCHEMA["route_plans"].add("route_warnings_json")
V6_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V5_REQUIRED_SCHEMA.items()}
V7_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V6_REQUIRED_SCHEMA.items()}
V7_REQUIRED_SCHEMA["site_relations"] = {
    "id", "site_id", "related_external_key", "relation_kind", "created_at",
}
CURRENT_SCHEMA_VERSION = 7
REQUIRED_SCHEMA = V7_REQUIRED_SCHEMA


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_payload_hash(payload: dict[str, object]) -> str:
    canonical_payload = dict(payload)
    canonical_payload["records"] = sorted(
        payload.get("records", []), key=lambda record: record["external_key"]
    )
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            try:
                with self.connect() as connection:
                    connection.executescript(SCHEMA)
                    connection.execute("PRAGMA user_version = 1")
                    _run_migration(connection, _migrate_v2)
                    _run_migration(connection, _migrate_v3)
                    _run_migration(connection, _migrate_v4)
                    _run_migration(connection, _migrate_v5)
                    _run_migration(connection, _migrate_v6)
                    _run_migration(connection, _migrate_v7)
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("database initialization failed") from exc
            return

        try:
            with sqlite3.connect(self.path, timeout=10) as connection:
                connection.row_factory = sqlite3.Row
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > CURRENT_SCHEMA_VERSION:
                    raise RuntimeError("future schema version is not supported")
                required = (
                    BASE_REQUIRED_SCHEMA if version < 2
                    else V2_REQUIRED_SCHEMA if version == 2
                    else V3_REQUIRED_SCHEMA if version == 3
                    else V4_REQUIRED_SCHEMA if version == 4
                    else V5_REQUIRED_SCHEMA if version == 5
                    else V6_REQUIRED_SCHEMA if version == 6
                    else REQUIRED_SCHEMA
                )
                errors = required_schema_errors(
                    connection,
                    required_schema=required,
                    allow_legacy_confidence=version == 0,
                )
                if errors:
                    raise RuntimeError("database is missing required schema")
                while version < CURRENT_SCHEMA_VERSION:
                    migration = {
                        1: _migrate_v1, 2: _migrate_v2, 3: _migrate_v3, 4: _migrate_v4,
                        5: _migrate_v5, 6: _migrate_v6, 7: _migrate_v7,
                    }.get(version + 1)
                    if migration is None:
                        raise RuntimeError("database migration is not available")
                    _run_migration(connection, migration)
                    version += 1
        except RuntimeError:
            raise
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("database is not a readable SQLite database") from exc


def required_schema_errors(
    connection: sqlite3.Connection,
    *,
    required_schema: dict[str, set[str]] | None = None,
    allow_legacy_confidence: bool = False,
) -> list[str]:
    required_schema = required_schema or REQUIRED_SCHEMA
    errors: list[str] = []
    for table, required_columns in required_schema.items():
        object_type = connection.execute(
            "SELECT type FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()
        if object_type is None or object_type[0] != "table":
            errors.append(table)
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = required_columns - columns
        if table == "sites" and allow_legacy_confidence:
            missing.discard("confidence")
        if missing:
            errors.append(table)
    return errors


def _migrate_v1(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "confidence" not in columns:
        connection.execute("ALTER TABLE sites ADD COLUMN confidence TEXT")
    connection.execute(
        """
        UPDATE sites
        SET confidence = (
            SELECT json_extract(import_records.payload_json, '$.confidence')
            FROM import_records
            WHERE import_records.site_id = sites.id
              AND json_valid(import_records.payload_json) = 1
              AND json_extract(import_records.payload_json, '$.confidence')
                  IN ('high', 'medium', 'low', 'unknown')
            ORDER BY import_records.id DESC
            LIMIT 1
        )
        WHERE sites.confidence IS NULL
          AND EXISTS (
            SELECT 1
            FROM import_records
            WHERE import_records.site_id = sites.id
              AND json_valid(import_records.payload_json) = 1
              AND json_extract(import_records.payload_json, '$.confidence')
                  IN ('high', 'medium', 'low', 'unknown')
          )
        """
    )
    connection.execute("PRAGMA user_version = 1")


def _migrate_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE import_batches ADD COLUMN payload_hash TEXT")
    connection.execute(
        """
        CREATE TABLE evidence_items (
            id INTEGER PRIMARY KEY,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            import_record_id INTEGER REFERENCES import_records(id),
            source_index INTEGER,
            legacy_evidence_id INTEGER UNIQUE REFERENCES evidence(id) ON DELETE SET NULL,
            excerpt TEXT NOT NULL,
            content_kind TEXT NOT NULL DEFAULT 'unknown'
                CHECK(content_kind IN ('quote', 'summary', 'unknown')),
            role TEXT NOT NULL DEFAULT 'context'
                CHECK(role IN ('identity', 'location', 'access', 'context')),
            published_at TEXT,
            accessed_at TEXT,
            provenance_status TEXT NOT NULL
                CHECK(provenance_status IN ('import_record', 'legacy_unresolved')),
            created_at TEXT NOT NULL,
            UNIQUE(import_record_id, source_index)
        )
        """
    )
    _repair_v2_history(connection)
    connection.execute("PRAGMA user_version = 2")


def _repair_v2_history(connection: sqlite3.Connection) -> None:
    evidence_rows = connection.execute(
        """
        SELECT evidence.id, evidence.site_id, evidence.source_id, evidence.created_at,
               sources.url, sources.excerpt, sources.published_at, sources.accessed_at
        FROM evidence
        JOIN sources ON sources.id = evidence.source_id
        """
    ).fetchall()
    for evidence in evidence_rows:
        reconstructed: dict[tuple[int, int], dict[str, object]] = {}
        records = connection.execute(
            "SELECT id, payload_json FROM import_records WHERE site_id = ? ORDER BY id",
            (evidence["site_id"],),
        ).fetchall()
        for record in records:
            try:
                payload = json.loads(record["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            for source_index, source in enumerate(payload.get("sources", [])):
                if (
                    isinstance(source, dict)
                    and source.get("url") == evidence["url"]
                    and isinstance(source.get("excerpt"), str)
                ):
                    reconstructed[(record["id"], source_index)] = source
        if reconstructed:
            for (import_record_id, source_index), source in reconstructed.items():
                if connection.execute(
                    "SELECT 1 FROM evidence_items WHERE import_record_id = ? AND source_index = ?",
                    (import_record_id, source_index),
                ).fetchone():
                    continue
                connection.execute(
                    """
                    INSERT INTO evidence_items
                        (site_id, source_id, import_record_id, source_index, excerpt,
                         content_kind, role, published_at, accessed_at, provenance_status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'unknown', 'context', ?, ?, 'import_record', ?)
                    """,
                    (
                        evidence["site_id"], evidence["source_id"], import_record_id, source_index,
                        source["excerpt"], source.get("publication_date"), source.get("access_date"),
                        evidence["created_at"],
                    ),
                )
        else:
            if connection.execute(
                "SELECT 1 FROM evidence_items WHERE legacy_evidence_id = ?",
                (evidence["id"],),
            ).fetchone() is None:
                connection.execute(
                    """
                    INSERT INTO evidence_items
                        (site_id, source_id, legacy_evidence_id, excerpt, content_kind, role,
                         published_at, accessed_at, provenance_status, created_at)
                    VALUES (?, ?, ?, ?, 'unknown', 'context', ?, ?, 'legacy_unresolved', ?)
                    """,
                    (
                        evidence["site_id"], evidence["source_id"], evidence["id"],
                        evidence["excerpt"] or "", evidence["published_at"], evidence["accessed_at"],
                        evidence["created_at"],
                    ),
                )
    for batch in connection.execute(
        "SELECT id, batch_id, schema_version, generated_at FROM import_batches WHERE payload_hash IS NULL"
    ).fetchall():
        records = []
        reconstructable = True
        for record in connection.execute(
            "SELECT payload_json FROM import_records WHERE batch_id = ? ORDER BY external_key, id",
            (batch["batch_id"],),
        ).fetchall():
            try:
                payload = json.loads(record["payload_json"])
            except (TypeError, json.JSONDecodeError):
                reconstructable = False
                break
            if not isinstance(payload, dict) or not payload.get("external_key"):
                reconstructable = False
                break
            records.append(payload)
        if not records:
            reconstructable = False
        generated_at = batch["generated_at"]
        if generated_at.endswith("+00:00"):
            generated_at = generated_at[:-6] + "Z"
        if reconstructable:
            payload = {
                "schema_version": batch["schema_version"],
                "batch_id": batch["batch_id"],
                "generated_at": generated_at,
                "records": records,
            }
            connection.execute(
                "UPDATE import_batches SET payload_hash = ? WHERE id = ?",
                (canonical_payload_hash(payload), batch["id"]),
            )
def _migrate_v3(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE field_observations ADD COLUMN point_role TEXT NOT NULL DEFAULT 'unknown'")
    connection.execute("ALTER TABLE field_observations ADD COLUMN uncertainty_m REAL")
    connection.execute("PRAGMA user_version = 3")


def _migrate_v4(connection: sqlite3.Connection) -> None:
    _repair_v2_history(connection)
    connection.execute("ALTER TABLE sites ADD COLUMN approach_latitude REAL")
    connection.execute("ALTER TABLE sites ADD COLUMN approach_longitude REAL")
    connection.execute("ALTER TABLE sites ADD COLUMN approach_access TEXT NOT NULL DEFAULT 'unknown'")
    connection.execute("ALTER TABLE sites ADD COLUMN approach_note TEXT")
    connection.execute("ALTER TABLE sites ADD COLUMN approach_reviewed_at TEXT")
    connection.execute("ALTER TABLE sites ADD COLUMN location_review_required INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE route_plans ADD COLUMN stops_json TEXT")
    connection.execute("ALTER TABLE route_plans ADD COLUMN route_warnings_json TEXT")
    connection.execute("PRAGMA user_version = 4")


def _migrate_v5(connection: sqlite3.Connection) -> None:
    _repair_evidence_item_fk(connection)
    _repair_v2_history(connection)
    site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "location_review_required" not in site_columns:
        connection.execute("ALTER TABLE sites ADD COLUMN location_review_required INTEGER NOT NULL DEFAULT 0")
    route_columns = {row[1] for row in connection.execute("PRAGMA table_info(route_plans)")}
    if "route_warnings_json" not in route_columns:
        connection.execute("ALTER TABLE route_plans ADD COLUMN route_warnings_json TEXT")
    connection.execute("PRAGMA user_version = 5")


def _migrate_v6(connection: sqlite3.Connection) -> None:
    """Repair databases that already recorded schema version 5."""
    _repair_evidence_item_fk(connection)
    _repair_v2_history(connection)
    connection.execute("PRAGMA user_version = 6")


def _migrate_v7(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS site_relations (
            id INTEGER PRIMARY KEY,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            related_external_key TEXT NOT NULL,
            relation_kind TEXT NOT NULL DEFAULT 'related'
                CHECK(relation_kind IN ('related')),
            created_at TEXT NOT NULL,
            UNIQUE(site_id, related_external_key, relation_kind)
        )
        """
    )
    for record in connection.execute(
        "SELECT site_id, payload_json FROM import_records WHERE site_id IS NOT NULL"
    ).fetchall():
        try:
            payload = json.loads(record["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        related_keys = payload.get("related_site_keys", []) if isinstance(payload, dict) else []
        if not isinstance(related_keys, list):
            continue
        for related_external_key in related_keys:
            if not isinstance(related_external_key, str) or not related_external_key:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO site_relations (site_id, related_external_key, relation_kind, created_at) VALUES (?, ?, 'related', ?)",
                (record["site_id"], related_external_key, now_iso()),
            )
    connection.execute("PRAGMA user_version = 7")


def _repair_evidence_item_fk(connection: sqlite3.Connection) -> None:
    foreign_keys = connection.execute("PRAGMA foreign_key_list(evidence_items)").fetchall()
    if any(
        row["from"] == "legacy_evidence_id" and row["on_delete"].upper() == "SET NULL"
        for row in foreign_keys
    ):
        return
    connection.execute(
        """
        CREATE TABLE evidence_items_repaired (
            id INTEGER PRIMARY KEY,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            import_record_id INTEGER REFERENCES import_records(id),
            source_index INTEGER,
            legacy_evidence_id INTEGER UNIQUE REFERENCES evidence(id) ON DELETE SET NULL,
            excerpt TEXT NOT NULL,
            content_kind TEXT NOT NULL DEFAULT 'unknown'
                CHECK(content_kind IN ('quote', 'summary', 'unknown')),
            role TEXT NOT NULL DEFAULT 'context'
                CHECK(role IN ('identity', 'location', 'access', 'context')),
            published_at TEXT,
            accessed_at TEXT,
            provenance_status TEXT NOT NULL
                CHECK(provenance_status IN ('import_record', 'legacy_unresolved')),
            created_at TEXT NOT NULL,
            UNIQUE(import_record_id, source_index)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO evidence_items_repaired
            (id, site_id, source_id, import_record_id, source_index, legacy_evidence_id,
             excerpt, content_kind, role, published_at, accessed_at, provenance_status, created_at)
        SELECT id, site_id, source_id, import_record_id, source_index, legacy_evidence_id,
               excerpt, content_kind, role, published_at, accessed_at, provenance_status, created_at
        FROM evidence_items
        """
    )
    connection.execute("DROP TABLE evidence_items")
    connection.execute("ALTER TABLE evidence_items_repaired RENAME TO evidence_items")


def _run_migration(connection: sqlite3.Connection, migration: Callable[[sqlite3.Connection], None]) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        migration(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def load_json(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def site_from_row(row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    result["warnings"] = load_json(result.pop("warnings_json", None), [])
    return result


def observation_from_row(row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    result["photo_urls"] = load_json(result.pop("photo_urls_json", None), [])
    return result
