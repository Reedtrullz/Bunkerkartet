from __future__ import annotations

import json
import hashlib
import math
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
V8_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V7_REQUIRED_SCHEMA.items()}
V8_REQUIRED_SCHEMA["sites"].add("revision")
V8_REQUIRED_SCHEMA["field_observations"].update({"request_id", "payload_hash"})
V9_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V8_REQUIRED_SCHEMA.items()}
V9_REQUIRED_SCHEMA["route_requests"] = {"id", "request_id", "payload_hash", "route_id", "expires_at"}
V10_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V9_REQUIRED_SCHEMA.items()}
V10_REQUIRED_SCHEMA["sites"].add("content_json")
V11_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V10_REQUIRED_SCHEMA.items()}
for _table, _columns in {
    'research_questions': {'id','external_key','kind','state','payload_json','revision','created_at','updated_at'},
    'question_events': {'id','question_id','payload_json','created_at'},
    'identity_hypotheses': {'id','question_id','state','payload_json','revision','created_at','updated_at'},
    'claim_assertions': {'id','external_key','target_external_key','claim_id','target_claim_id','relation','state','payload_json','revision','created_at','updated_at'},
}.items(): V11_REQUIRED_SCHEMA[_table]=_columns
OBSERVATION_WORKFLOW_COLUMNS = {
    'field_observations': {'context_json'},
    'observation_amendments': {'id','observation_id','site_id','revision','request_id','payload_hash','request_json','changes_json','reason','support_invalidated','created_at'},
    'observation_visits': {'id','request_id','payload_hash','request_json','state','planned_date','occurred_date','visit_outcome','selected_questions_json','site_ids_json','route_snapshot_json','manual_approaches_json','outcomes_json','retention_until','revision','created_at','updated_at','close_reason','closed_at'},
    'visit_observations': {'id','visit_id','site_id','observation_id','question_id','request_id','payload_hash','request_json','attached_at'},
}
V12_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V11_REQUIRED_SCHEMA.items()}
for _table, _columns in OBSERVATION_WORKFLOW_COLUMNS.items():
    V12_REQUIRED_SCHEMA.setdefault(_table, set()).update(_columns)
V13_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V12_REQUIRED_SCHEMA.items()}
V13_REQUIRED_SCHEMA['route_plans'].add('details_json')
V14_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V13_REQUIRED_SCHEMA.items()}
V14_REQUIRED_SCHEMA['image_references']={'id','image_id','site_id','receipt_json','state','created_at','deleted_at','deletion_reason'}
from app.pilots import PILOT_REQUIRED
V15_REQUIRED_SCHEMA = {table: set(columns) for table, columns in V14_REQUIRED_SCHEMA.items()}
V15_REQUIRED_SCHEMA.update({table:set(columns) for table,columns in PILOT_REQUIRED.items()})
V15_REQUIRED_SCHEMA['import_batches'].add('provenance_json')
V16_REQUIRED_SCHEMA = {table:set(columns) for table,columns in V15_REQUIRED_SCHEMA.items()}
V16_REQUIRED_SCHEMA['route_retention_receipts']={'id','route_id','payload_json','created_at'}
CURRENT_SCHEMA_VERSION = 16
REQUIRED_SCHEMA = V16_REQUIRED_SCHEMA


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_payload_hash(payload: dict[str, object]) -> str:
    canonical_payload = dict(payload)
    canonical_payload["records"] = sorted(
        payload.get("records", []), key=lambda record: record["external_key"]
    )
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OwnedConnection(sqlite3.Connection):
    """A transaction context owns both completion and connection lifetime."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, factory=OwnedConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def transaction(self) -> sqlite3.Connection:
        """Open a commit/rollback/close context; connect remains compatible."""
        return self.connect()

    def initialize(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            try:
                with self.connect() as connection:
                    connection.executescript(SCHEMA)
                    connection.execute("PRAGMA user_version = 1")
                    for boundary in range(2, CURRENT_SCHEMA_VERSION + 1):
                        _run_migration(connection, globals()[f"_migrate_v{boundary}"])
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("database initialization failed") from exc
            return

        try:
            with sqlite3.connect(self.path, timeout=10, factory=OwnedConnection) as connection:
                connection.row_factory = sqlite3.Row
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > CURRENT_SCHEMA_VERSION:
                    raise RuntimeError("future schema version is not supported")
                required = BASE_REQUIRED_SCHEMA if version < 2 else globals()[f"V{version}_REQUIRED_SCHEMA"]
                errors = required_schema_errors(
                    connection,
                    required_schema=required,
                    allow_legacy_confidence=version == 0,
                )
                if errors:
                    raise RuntimeError("database is missing required schema")
                while version < CURRENT_SCHEMA_VERSION:
                    migration = globals().get(f"_migrate_v{version + 1}")
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
    if errors:
        return errors
    unique_keys = {
        "sites": [("external_key",)], "sources": [("url",)],
        "evidence": [("site_id", "source_id", "role")],
        "import_batches": [("batch_id",)], "import_records": [("batch_id", "external_key")],
        "evidence_items": [("legacy_evidence_id",), ("import_record_id", "source_index")],
        "site_relations": [("site_id", "related_external_key", "relation_kind")],
        "route_requests": [("request_id",)],
        "route_retention_receipts": [("route_id",)],
        "observation_amendments": [("observation_id","revision"),("observation_id","request_id")],
        "image_references": [("image_id",)],
        "pilot_offline_outbox": [("original_request_id",)],
        "pilot_historical_assertions": [("assertion_key",)],
        "pilot_reader_credentials": [("credential_id",)],
        "pilot_publication_receipts": [("package_id",)],
        "observation_visits": [("request_id",)],
        "visit_observations": [("visit_id","request_id"),("visit_id","observation_id")],
    }
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version >= 8:
        unique_keys["field_observations"] = [("site_id", "request_id")]
    foreign_keys = {
        "sites": [("merged_into_id", "sites", "id", "NO ACTION")],
        "evidence": [("site_id", "sites", "id", "CASCADE"), ("source_id", "sources", "id", "CASCADE")],
        "import_records": [("batch_id", "import_batches", "batch_id", "CASCADE"), ("site_id", "sites", "id", "SET NULL")],
        "site_events": [("site_id", "sites", "id", "CASCADE")],
        "field_observations": [("site_id", "sites", "id", "CASCADE")],
        "evidence_items": [("site_id", "sites", "id", "CASCADE"), ("source_id", "sources", "id", "CASCADE"), ("import_record_id", "import_records", "id", "NO ACTION")],
        "site_relations": [("site_id", "sites", "id", "CASCADE")],
        "route_requests": [("route_id", "route_plans", "id", "SET NULL")],
        "route_retention_receipts": [("route_id", "route_plans", "id", "NO ACTION")],
        "pilot_offline_outbox": [("pack_id","pilot_offline_packs","pack_id","CASCADE")],
        "image_references": [("site_id","sites","id","CASCADE")],
        "question_events": [("question_id", "research_questions", "id", "CASCADE")],
        "observation_amendments": [("observation_id","field_observations","id","CASCADE"),("site_id","sites","id","CASCADE")],
        "visit_observations": [("visit_id","observation_visits","id","CASCADE"),("site_id","sites","id","CASCADE"),("observation_id","field_observations","id","CASCADE"),("question_id","research_questions","id","SET NULL")],
        "identity_hypotheses": [("question_id", "research_questions", "id", "CASCADE")],
    }
    if version >= 6:
        foreign_keys["evidence_items"].append(("legacy_evidence_id", "evidence", "id", "SET NULL"))
    for table in required_schema:
        columns = list(connection.execute(f"PRAGMA table_info({table})"))
        integer_fields = {"id", "site_id", "source_id", "import_record_id", "source_index", "legacy_evidence_id", "merged_into_id", "revision", "location_review_required", "route_id", "question_id", "observation_id", "visit_id", "support_invalidated", "cached_site_revision", "reviewed_site_revision"}
        real_fields = {"latitude", "longitude", "uncertainty_m", "approach_latitude", "approach_longitude", "distance_m", "duration_s", "expires_at"}
        for column in columns:
            if column[1] not in required_schema[table]: continue
            declaration = column[2].upper()
            affinity = "INTEGER" if "INT" in declaration else "TEXT" if any(word in declaration for word in ("CHAR", "CLOB", "TEXT")) else "REAL" if any(word in declaration for word in ("REAL", "FLOA", "DOUB")) else "BLOB" if not declaration or "BLOB" in declaration else "NUMERIC"
            expected = "INTEGER" if column[1] in integer_fields else "REAL" if column[1] in real_fields and (column[1] != "expires_at" or table == "route_requests") else "TEXT"
            if affinity != expected: errors.append(f"{table}:column_affinity")
        primary = [row for row in columns if row[5]]
        key, key_type = ("pack_id", "TEXT") if table == "pilot_offline_packs" else ("id", "INTEGER")
        if len(primary) != 1 or primary[0][1] != key or primary[0][2].upper() != key_type:
            errors.append(f"{table}:primary_key")
        indexes = set()
        for index in connection.execute(f"PRAGMA index_list({table})"):
            if index[2] and not index[4]:
                name = index[1].replace('"', '""')
                indexes.add(tuple(row[2] for row in connection.execute(f'PRAGMA index_info("{name}")')))
        for key in unique_keys.get(table, []):
            if key not in indexes: errors.append(f"{table}:unique_key")
        actual_fk = {(row[3], row[2], row[4], row[6].upper()) for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
        for key in foreign_keys.get(table, []):
            if key not in actual_fk: errors.append(f"{table}:foreign_key")
    # Old migration repair paths deliberately handle historical orphans; current
    # workspaces must satisfy these invariants before being declared ready.
    if version >= 8:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            errors.append("records:foreign_key")
        invalid_site = connection.execute("""
            SELECT id FROM sites WHERE
                (latitude IS NULL) != (longitude IS NULL) OR
                latitude NOT BETWEEN -90 AND 90 OR longitude NOT BETWEEN -180 AND 180 OR
                (approach_latitude IS NULL) != (approach_longitude IS NULL) OR
                approach_latitude NOT BETWEEN -90 AND 90 OR approach_longitude NOT BETWEEN -180 AND 180 OR
                typeof(revision) != 'integer' OR revision < 1 OR
                status NOT IN ('candidate','approximate','likely','trusted','field-verified','destroyed-or-filled','rejected') OR
                access NOT IN ('public','private','restricted','unknown','permission_required','dangerous','unsafe')
            LIMIT 1
        """).fetchone()
        if invalid_site: errors.append(f"sites:{invalid_site[0]}:semantic")
        invalid_observation = connection.execute("""SELECT id FROM field_observations WHERE
            (latitude IS NULL) != (longitude IS NULL) OR latitude NOT BETWEEN -90 AND 90 OR
            longitude NOT BETWEEN -180 AND 180 LIMIT 1""").fetchone()
        if invalid_observation: errors.append(f"field_observations:{invalid_observation[0]}:semantic")
        for table, field, kind in (("sites", "warnings_json", "strings"), ("field_observations", "photo_urls_json", "strings"), ("route_plans", "geometry_json", "geometry"), ("route_plans", "start_json", "start"), ("route_plans", "waypoints_json", "points")):
            for row in connection.execute(f"SELECT id,{field} FROM {table}"):
                if decode_stored_json(row[1], kind)[1] not in {"valid", "valid_empty"}:
                    errors.append(f"{table}:{row[0]}:stored_field"); break
    return list(dict.fromkeys(errors))


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


def _migrate_v8(connection: sqlite3.Connection) -> None:
    site_columns = {row[1] for row in connection.execute("PRAGMA table_info(sites)")}
    if "revision" not in site_columns:
        connection.execute("ALTER TABLE sites ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    observation_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(field_observations)")
    }
    if "request_id" not in observation_columns:
        connection.execute("ALTER TABLE field_observations ADD COLUMN request_id TEXT")
    if "payload_hash" not in observation_columns:
        connection.execute("ALTER TABLE field_observations ADD COLUMN payload_hash TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_field_observations_request ON field_observations(site_id, request_id)"
    )
    connection.execute("PRAGMA user_version = 8")


def _migrate_v9(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE route_requests (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        payload_hash TEXT NOT NULL,
        route_id INTEGER REFERENCES route_plans(id) ON DELETE SET NULL,
        expires_at REAL NOT NULL
    )""")
    connection.execute("PRAGMA user_version=9")


def _migrate_v10(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE sites ADD COLUMN content_json TEXT")
    connection.execute("PRAGMA user_version=10")


def _migrate_v11(connection: sqlite3.Connection) -> None:
    from app.research import RESEARCH_SCHEMA
    for statement in RESEARCH_SCHEMA: connection.execute(statement)
    connection.execute("PRAGMA user_version=11")


def _migrate_v12(connection: sqlite3.Connection) -> None:
    from app.observation_workflows import OBSERVATION_WORKFLOW_SCHEMA
    for statement in OBSERVATION_WORKFLOW_SCHEMA: connection.execute(statement)
    connection.execute("PRAGMA user_version=12")


def _migrate_v13(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE route_plans ADD COLUMN details_json TEXT")
    connection.execute("PRAGMA user_version=13")


def _migrate_v14(connection: sqlite3.Connection) -> None:
    from app.media_api import MEDIA_SCHEMA
    for statement in MEDIA_SCHEMA: connection.execute(statement)
    connection.execute("PRAGMA user_version=14")


def _migrate_v15(connection: sqlite3.Connection) -> None:
    from app.pilots import PILOT_SCHEMA
    for statement in PILOT_SCHEMA.split(';'):
        if statement.strip():connection.execute(statement)
    connection.execute('ALTER TABLE import_batches ADD COLUMN provenance_json TEXT')
    connection.execute("PRAGMA user_version=15")


def _migrate_v16(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE route_retention_receipts(id INTEGER PRIMARY KEY,route_id INTEGER NOT NULL UNIQUE REFERENCES route_plans(id),payload_json TEXT NOT NULL,created_at TEXT NOT NULL)")
    connection.execute("PRAGMA user_version=16")


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


def decode_stored_json(value: str | None, kind: str) -> tuple[object | None, str]:
    """Decode a stored field without hiding corruption behind an empty default."""
    if value is None: return None, "absent_legacy"
    from app.json_input import decode_json_strict
    try:
        decoded = decode_json_strict(value.encode(), max_bytes=2 * 1024 * 1024)
    except (UnicodeError, ValueError, AttributeError):
        return None, "malformed"
    def number(item):
        return isinstance(item, (float, int)) and not isinstance(item, bool) and math.isfinite(item)
    def point(item):
        return isinstance(item, dict) and number(item.get("lat")) and number(item.get("lon")) and -90 <= item["lat"] <= 90 and -180 <= item["lon"] <= 180
    valid = {
        "strings": lambda: isinstance(decoded, list) and all(isinstance(item, str) for item in decoded),
        "object": lambda: isinstance(decoded, dict),
        "start": lambda: point(decoded),
        "points": lambda: isinstance(decoded, list) and all(point(item) for item in decoded),
        "stops": lambda: isinstance(decoded, list) and all(point(item) and isinstance(item.get("site_id"), int) and not isinstance(item.get("site_id"), bool) and isinstance(item.get("name"), str) and isinstance(item.get("warnings", []), list) for item in decoded),
        "geometry": lambda: isinstance(decoded, list) and len(decoded) >= 2 and all(isinstance(item, list) and len(item) == 2 and number(item[0]) and number(item[1]) and -180 <= item[0] <= 180 and -90 <= item[1] <= 90 for item in decoded),
    }
    if kind not in valid: raise ValueError("unknown stored field contract")
    if not valid[kind](): return None, "wrong_shape"
    return decoded, "valid_empty" if not decoded else "valid"


def site_from_row(row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    result.pop("content_json", None)
    result["warnings"], status = decode_stored_json(result.pop("warnings_json", None), "strings")
    result["stored_field_status"] = {"warnings": status}
    result["data_status"] = "valid" if status in {"valid", "valid_empty"} else "unavailable"
    return result


def observation_from_row(row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    result["photo_urls"], status = decode_stored_json(result.pop("photo_urls_json", None), "strings")
    result["stored_field_status"] = {"photo_urls": status}
    result["data_status"] = "valid" if status in {"valid", "valid_empty"} else "unavailable"
    return result
