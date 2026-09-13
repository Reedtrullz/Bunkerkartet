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
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version = 1")


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
