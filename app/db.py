from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


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

CURRENT_SCHEMA_VERSION = 1
REQUIRED_SCHEMA = {
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


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
                    connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("database initialization failed") from exc
            return

        try:
            with sqlite3.connect(self.path, timeout=10) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > CURRENT_SCHEMA_VERSION:
                    raise RuntimeError("future schema version is not supported")
                errors = required_schema_errors(connection, allow_legacy_confidence=version == 0)
                if errors:
                    raise RuntimeError("database is missing required schema")
                if version == 0:
                    _migrate_v1(connection)
        except RuntimeError:
            raise
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("database is not a readable SQLite database") from exc


def required_schema_errors(
    connection: sqlite3.Connection, *, allow_legacy_confidence: bool = False
) -> list[str]:
    errors: list[str] = []
    for table, required_columns in REQUIRED_SCHEMA.items():
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
    with connection:
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
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")


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
