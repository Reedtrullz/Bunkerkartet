from __future__ import annotations

from datetime import date, datetime
from html import escape as escape_html
import hmac
import json
import math
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator
from urllib.error import HTTPError, URLError

from app.config import Settings
from app.db import Database, canonical_payload_hash, dump_json, load_json, now_iso, observation_from_row, site_from_row
from app.imports import (
    ImportPackage,
    ImportRecord,
    safe_validation_errors,
    validate_import_package,
    validate_location,
    validate_reference_url,
)
from app.routes import RouteResult, build_gpx, fetch_openrouteservice


SITE_STATUSES = (
    "candidate",
    "approximate",
    "likely",
    "trusted",
    "field-verified",
    "destroyed-or-filled",
    "rejected",
)
SITE_ACCESSES = (
    "public",
    "private",
    "restricted",
    "unknown",
    "permission_required",
    "dangerous",
    "unsafe",
)
CONFIDENCE_LEVELS = ("high", "medium", "low", "unknown")
LOCATION_BASES = (
    "explicit_coordinate",
    "address",
    "map_reference",
    "landmark_description",
    "llm_inference",
)
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' https://unpkg.com; style-src 'self' https://unpkg.com 'unsafe-inline'; img-src 'self' data: blob: https://cache.kartverket.no https://server.arcgisonline.com https://unpkg.com; font-src 'self' data:; connect-src 'self'",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Permissions-Policy": "geolocation=(self)",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SitePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=500)
    site_kind: str | None = Field(default=None, min_length=1, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    precision: Literal["exact", "approximate", "unknown"] | None = None
    uncertainty_m: float | None = Field(default=None, ge=0)
    location_basis: Literal[
        "explicit_coordinate",
        "address",
        "map_reference",
        "landmark_description",
        "llm_inference",
    ] | None = None
    status: Literal[
        "candidate",
        "approximate",
        "likely",
        "trusted",
        "field-verified",
        "destroyed-or-filled",
        "rejected",
    ] | None = None
    access: Literal[
        "public",
        "private",
        "restricted",
        "unknown",
        "permission_required",
        "dangerous",
        "unsafe",
    ] | None = None
    confidence: Literal["high", "medium", "low", "unknown"] | None = None
    condition: str | None = Field(default=None, max_length=1000)
    warnings: list[str] | None = None
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "SitePatch":
        for field in ("name", "site_kind", "precision", "location_basis", "status", "access", "confidence", "warnings"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if ("latitude" in self.model_fields_set) != ("longitude" in self.model_fields_set):
            raise ValueError("latitude and longitude must be edited together")
        if {"latitude", "longitude", "precision", "uncertainty_m", "location_basis"} <= self.model_fields_set:
            validate_location(
                self.latitude, self.longitude, self.precision, self.uncertainty_m, self.location_basis
            )
        return self


class FieldObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: date
    outcome: Literal["found", "not_found", "inaccessible", "needs_follow_up"]
    note: str = Field(min_length=1, max_length=4000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    point_role: Literal["feature", "entrance", "viewpoint", "unknown"] = "unknown"
    uncertainty_m: float | None = Field(default=None, ge=0)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    access_notes: str | None = Field(default=None, max_length=2000)
    photo_urls: list[HttpUrl] = Field(default_factory=list, max_length=12)

    @field_validator("photo_urls", mode="before")
    @classmethod
    def reject_credential_photo_urls(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            return value
        return [validate_reference_url(item) for item in value]

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "FieldObservation":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be recorded together")
        if self.latitude is not None and (
            not math.isfinite(self.latitude) or not math.isfinite(self.longitude or 0)
        ):
            raise ValueError("coordinates must be finite")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "accept",
        "research",
        "field_verify",
        "confirm",
        "reject",
        "restore",
        "mark_approximate",
        "mark_destroyed",
        "merge",
    ]
    target_site_id: int | None = Field(default=None, gt=0)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Trondheim field route", min_length=1, max_length=200)
    start: RoutePoint
    waypoints: list[RoutePoint] = Field(min_length=1, max_length=20)
    waypoint_names: list[str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_waypoint_names(self) -> "RouteRequest":
        if self.waypoint_names is not None:
            if len(self.waypoint_names) != len(self.waypoints):
                raise ValueError("waypoint_names must match waypoints")
            if any(not name.strip() for name in self.waypoint_names):
                raise ValueError("waypoint names cannot be empty")
        return self


def _validation_detail(error: ValidationError) -> list[dict[str, object]]:
    return safe_validation_errors(error.errors())


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(first[1]), math.radians(first[0])
    lat2, lon2 = math.radians(second[1]), math.radians(second[0])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _route_warnings(
    requested_coordinates: list[tuple[float, float]],
    routed_coordinates: list[tuple[float, float]],
) -> list[str]:
    warnings = ["A route does not grant permission to enter land or structures."]
    if _distance_m(requested_coordinates[0], routed_coordinates[0]) > 50 or _distance_m(
        requested_coordinates[-1], routed_coordinates[-1]
    ) > 50:
        warnings.append("The provider snapped a route endpoint; verify the approach on site.")
    return warnings


def _source_rows(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT sources.url, sources.title, sources.source_type,
               COALESCE(evidence_items.excerpt, sources.excerpt) AS excerpt,
               CASE WHEN evidence_items.id IS NULL THEN sources.published_at ELSE evidence_items.published_at END AS published_at,
               CASE WHEN evidence_items.id IS NULL THEN sources.accessed_at ELSE evidence_items.accessed_at END AS accessed_at,
               COALESCE(evidence_items.role, evidence.role) AS role,
               evidence_items.id AS evidence_id,
               evidence_items.content_kind, evidence_items.provenance_status
        FROM evidence
        JOIN sources ON sources.id = evidence.source_id
        LEFT JOIN evidence_items ON evidence_items.legacy_evidence_id = evidence.id
            OR (evidence_items.source_id = evidence.source_id AND evidence_items.site_id = evidence.site_id)
        WHERE evidence.site_id = ?
        ORDER BY evidence_items.id, sources.id
        """,
        (site_id,),
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        source = dict(row)
        try:
            source["url"] = validate_reference_url(source["url"])
        except ValueError:
            source["url"] = None
            source["url_status"] = "legacy reference withheld; review required"
        result.append(source)
    return result


def _observation_points(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT id, observed_at, outcome, latitude, longitude
        FROM field_observations
        WHERE site_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY observed_at DESC, id DESC
        """,
        (site_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _safe_observation(row: sqlite3.Row) -> dict[str, object]:
    observation = observation_from_row(row)
    safe_urls: list[str] = []
    withheld = False
    urls = observation.get("photo_urls", [])
    if not isinstance(urls, list):
        urls = []
        withheld = True
    for url in urls:
        try:
            safe_urls.append(validate_reference_url(url))
        except ValueError:
            withheld = True
    observation["photo_urls"] = safe_urls
    if withheld:
        observation["photo_urls_status"] = "legacy reference withheld; review required"
    return observation


def _site_summary(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["observation_points"] = _observation_points(connection, int(row["id"]))
    return site


def _site_detail(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["sources"] = _source_rows(connection, int(row["id"]))
    observations = connection.execute(
        """
        SELECT id, site_id, observed_at, outcome, note, latitude, longitude,
               point_role, uncertainty_m, observed_location_text, access_notes,
               photo_urls_json, created_at
        FROM field_observations
        WHERE site_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (int(row["id"]),),
    ).fetchall()
    site["field_observations"] = [_safe_observation(item) for item in observations]
    site["observation_points"] = [
        {
            "id": observation["id"],
            "observed_at": observation["observed_at"],
            "outcome": observation["outcome"],
            "latitude": observation["latitude"],
            "longitude": observation["longitude"],
        }
        for observation in site["field_observations"]
        if observation["latitude"] is not None and observation["longitude"] is not None
    ]
    return site


def _existing_site(connection: sqlite3.Connection, external_key: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM sites WHERE external_key = ?",
        (external_key,),
    ).fetchone()


def _duplicate_warnings(
    connection: sqlite3.Connection, record: ImportRecord
) -> list[str]:
    geometry = record.geometry
    warnings = list(record.warnings)
    rows = connection.execute(
        """
        SELECT external_key, name, site_kind, latitude, longitude
        FROM sites
        WHERE merged_into_id IS NULL AND external_key <> ?
        """,
        (record.external_key,),
    ).fetchall()
    # ponytail: bounded O(n^2) scan is enough for the private v1 catalogue; add a spatial index at scale.
    for row in rows:
        if row["name"].casefold() != record.name.casefold():
            continue
        if row["site_kind"].casefold() != record.site_kind.casefold():
            continue
        if geometry is None or row["latitude"] is None or row["longitude"] is None:
            warnings.append(f"possible duplicate of {row['external_key']}")
            continue
        distance = _distance_m(
            (float(geometry.longitude), float(geometry.latitude)),
            (float(row["longitude"]), float(row["latitude"])),
        )
        if distance <= 100:
            warnings.append(
                f"possible duplicate of {row['external_key']} ({round(distance)} m away)"
            )
    return list(dict.fromkeys(warnings))


_PREVIEW_FIELDS = (
    "name", "site_kind", "latitude", "longitude", "precision", "uncertainty_m",
    "location_basis", "access", "confidence", "condition", "short_rationale",
    "observed_location_text",
)


def _record_values(record: ImportRecord) -> dict[str, object]:
    return {
        "name": record.name,
        "site_kind": record.site_kind,
        "latitude": record.geometry.latitude if record.geometry else None,
        "longitude": record.geometry.longitude if record.geometry else None,
        "precision": record.precision,
        "uncertainty_m": record.uncertainty_m,
        "location_basis": record.location_basis,
        "access": record.access,
        "confidence": record.confidence,
        "condition": record.condition,
        "short_rationale": record.short_rationale,
        "observed_location_text": record.observed_location_text,
    }


def _peer_duplicate_warnings(record: ImportRecord, records: list[ImportRecord]) -> list[str]:
    warnings: list[str] = []
    for other in sorted(records, key=lambda item: item.external_key):
        if other.external_key == record.external_key:
            continue
        if other.name.casefold() != record.name.casefold() or other.site_kind.casefold() != record.site_kind.casefold():
            continue
        if record.geometry is None or other.geometry is None:
            warnings.append(f"possible duplicate of {other.external_key}")
            continue
        distance = _distance_m(
            (record.geometry.longitude, record.geometry.latitude),
            (other.geometry.longitude, other.geometry.latitude),
        )
        if distance <= 100:
            warnings.append(f"possible duplicate of {other.external_key} ({round(distance)} m away)")
    return warnings


def _warnings_for_record(
    connection: sqlite3.Connection, record: ImportRecord, records: list[ImportRecord]
) -> list[str]:
    return list(dict.fromkeys([
        *_duplicate_warnings(connection, record),
        *_peer_duplicate_warnings(record, records),
    ]))


def _preview_record(
    connection: sqlite3.Connection, record: ImportRecord, records: list[ImportRecord]
) -> dict[str, object]:
    existing = _existing_site(connection, record.external_key)
    warnings = _warnings_for_record(connection, record, records)
    if (
        existing is not None
        and existing["status"] == "candidate"
        and record.geometry is None
        and existing["latitude"] is not None
    ):
        warnings.append("incoming record has no geometry; existing candidate coordinate will be preserved")
    warnings = list(dict.fromkeys(warnings))
    incoming = _record_values(record)
    changes: list[dict[str, object]] = []
    preserved_fields: list[str] = []
    if existing is None:
        action = "new"
        changes = [
            {"field": field, "before": None, "after": incoming[field]}
            for field in _PREVIEW_FIELDS
            if incoming[field] is not None
        ]
    elif existing["status"] == "candidate":
        action = "update_candidate"
        effective = dict(incoming)
        preserve_candidate_point = (
            record.geometry is None
            and existing["latitude"] is not None
            and existing["longitude"] is not None
        )
        if preserve_candidate_point:
            for field in ("latitude", "longitude", "precision", "uncertainty_m", "location_basis"):
                effective[field] = existing[field]
        if effective["access"] == "unknown" and existing["access"] != "unknown":
            effective["access"] = existing["access"]
        if effective["confidence"] in (None, "unknown") and existing["confidence"] not in (None, "unknown"):
            effective["confidence"] = existing["confidence"]
        for field in ("condition", "short_rationale", "observed_location_text"):
            if not effective[field] or not str(effective[field]).strip():
                effective[field] = existing[field]
        for field in _PREVIEW_FIELDS:
            before = existing[field]
            after = effective[field]
            if before != after:
                changes.append({"field": field, "before": before, "after": after})
            elif incoming[field] != after:
                preserved_fields.append(field)
    else:
        action = "preserve_trusted"
        preserved_fields = list(_PREVIEW_FIELDS)
    evidence = [
        {
            "url": str(source.url),
            "title": source.title,
            "source_type": source.source_type,
            "excerpt": source.excerpt,
            "publication_date": source.publication_date.isoformat() if source.publication_date else None,
            "access_date": source.access_date.isoformat() if source.access_date else None,
        }
        for source in record.sources
    ]
    return {
        "external_key": record.external_key,
        "name": record.name,
        "action": action,
        "changes": changes,
        "preserved_fields": preserved_fields,
        "evidence": evidence,
        "warnings": warnings,
    }


def _preview_hash(preview: dict[str, object]) -> str:
    effect = {
        "payload_hash": preview["payload_hash"],
        "records": preview["records"],
        "summary": preview["summary"],
    }
    return canonical_payload_hash(effect)


def _preview_package(
    connection: sqlite3.Connection, package: ImportPackage
) -> dict[str, object]:
    preview_records: list[dict[str, object]] = []
    counts = {"new": 0, "update_candidate": 0, "preserve_trusted": 0, "warnings": 0}
    records = sorted(package.records, key=lambda item: item.external_key)
    for record in records:
        preview_record = _preview_record(connection, record, records)
        counts[preview_record["action"]] += 1
        counts["warnings"] += len(preview_record["warnings"])
        preview_records.append(preview_record)
    preview = {
        "batch_id": package.batch_id,
        "schema_version": package.schema_version,
        "payload_hash": _payload_hash(package),
        "records": preview_records,
        "summary": {"total": len(package.records), **counts},
    }
    preview["preview_hash"] = _preview_hash(preview)
    return preview


def _site_values(record: ImportRecord, warnings: list[str]) -> tuple[object, ...]:
    latitude = record.geometry.latitude if record.geometry else None
    longitude = record.geometry.longitude if record.geometry else None
    return (
        record.external_key,
        record.name,
        record.site_kind,
        latitude,
        longitude,
        record.precision,
        record.uncertainty_m,
        record.location_basis,
        "candidate",
        record.access,
        record.confidence,
        record.condition,
        dump_json(warnings),
        record.short_rationale,
        record.observed_location_text,
    )


def _upsert_source(
    connection: sqlite3.Connection, source: object, timestamp: str
) -> int:
    connection.execute(
        """
        INSERT INTO sources
            (url, title, source_type, excerpt, published_at, accessed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = COALESCE(sources.title, excluded.title),
            source_type = COALESCE(sources.source_type, excluded.source_type),
            excerpt = COALESCE(sources.excerpt, excluded.excerpt),
            published_at = COALESCE(sources.published_at, excluded.published_at),
            accessed_at = COALESCE(sources.accessed_at, excluded.accessed_at),
            updated_at = excluded.updated_at
        """,
        (
            str(source.url),
            source.title,
            source.source_type,
            source.excerpt,
            source.publication_date.isoformat() if source.publication_date else None,
            source.access_date.isoformat() if source.access_date else None,
            timestamp,
            timestamp,
        ),
    )
    return int(
        connection.execute("SELECT id FROM sources WHERE url = ?", (str(source.url),)).fetchone()[0]
    )


def _payload_hash(package: ImportPackage) -> str:
    return canonical_payload_hash(package.model_dump(mode="json"))


def _commit_package(
    database: Database, package: ImportPackage, preview_hash: str | None
) -> dict[str, object]:
    timestamp = now_iso()
    payload_hash = _payload_hash(package)
    created = updated = preserved = evidence_attached = 0
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_batch = connection.execute(
            "SELECT committed_at, payload_hash FROM import_batches WHERE batch_id = ?",
            (package.batch_id,),
        ).fetchone()
        if existing_batch and existing_batch["committed_at"]:
            if existing_batch["payload_hash"] is None:
                raise HTTPException(409, "legacy batch payload cannot be verified")
            if existing_batch["payload_hash"] != payload_hash:
                raise HTTPException(409, "import batch payload differs")
            return {
                "batch_id": package.batch_id,
                "created": 0,
                "updated": 0,
                "preserved": 0,
                "evidence_attached": 0,
                "idempotent": True,
            }
        if existing_batch:
            raise HTTPException(409, "import batch is already in progress")
        preview = _preview_package(connection, package)
        if preview_hash != preview["preview_hash"]:
            raise HTTPException(409, "fresh preview required")

        batch_insert = connection.execute(
            """
            INSERT OR IGNORE INTO import_batches
                (batch_id, schema_version, generated_at, created_at, payload_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (package.batch_id, package.schema_version, package.generated_at.isoformat(), timestamp, payload_hash),
        )
        if batch_insert.rowcount == 0:
            existing_batch = connection.execute(
                "SELECT committed_at, payload_hash FROM import_batches WHERE batch_id = ?",
                (package.batch_id,),
            ).fetchone()
            if existing_batch and existing_batch["committed_at"]:
                if existing_batch["payload_hash"] is None:
                    raise HTTPException(409, "legacy batch payload cannot be verified")
                if existing_batch["payload_hash"] != payload_hash:
                    raise HTTPException(409, "import batch payload differs")
                return {
                    "batch_id": package.batch_id,
                    "created": 0,
                    "updated": 0,
                    "preserved": 0,
                    "evidence_attached": 0,
                    "idempotent": True,
                }
            raise HTTPException(409, "import batch is already in progress")
        warnings_by_key = {
            record.external_key: _warnings_for_record(connection, record, package.records)
            for record in package.records
        }
        for record in package.records:
            warnings = warnings_by_key[record.external_key]
            existing = _existing_site(connection, record.external_key)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sites
                        (external_key, name, site_kind, latitude, longitude, precision,
                         uncertainty_m, location_basis, status, access, confidence, condition,
                         warnings_json, short_rationale, observed_location_text,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*_site_values(record, warnings), timestamp, timestamp),
                )
                site_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                action = "created"
                created += 1
            elif existing["status"] == "candidate":
                preserve_candidate_point = (
                    record.geometry is None
                    and existing["latitude"] is not None
                    and existing["longitude"] is not None
                )
                existing_warnings = load_json(existing["warnings_json"], [])
                if not isinstance(existing_warnings, list):
                    existing_warnings = []
                warnings = list(dict.fromkeys([*existing_warnings, *warnings]))
                confidence = record.confidence
                if confidence in (None, "unknown") and existing["confidence"] not in (None, "unknown"):
                    confidence = existing["confidence"]
                condition = record.condition.strip() if record.condition and record.condition.strip() else existing["condition"]
                rationale = record.short_rationale.strip() if record.short_rationale and record.short_rationale.strip() else existing["short_rationale"]
                observed_location = record.observed_location_text.strip() if record.observed_location_text and record.observed_location_text.strip() else existing["observed_location_text"]
                connection.execute(
                    """
                    UPDATE sites SET name = ?, site_kind = ?, latitude = ?, longitude = ?,
                        precision = ?, uncertainty_m = ?, location_basis = ?, status = ?,
                        access = ?, confidence = ?, condition = ?, warnings_json = ?, short_rationale = ?,
                        observed_location_text = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        record.name,
                        record.site_kind,
                        existing["latitude"] if preserve_candidate_point else record.geometry.latitude if record.geometry else None,
                        existing["longitude"] if preserve_candidate_point else record.geometry.longitude if record.geometry else None,
                        existing["precision"] if preserve_candidate_point else record.precision,
                        existing["uncertainty_m"] if preserve_candidate_point else record.uncertainty_m,
                        existing["location_basis"] if preserve_candidate_point else record.location_basis,
                        "candidate",
                        existing["access"] if record.access == "unknown" and existing["access"] != "unknown" else record.access,
                        confidence,
                        condition,
                        dump_json(warnings),
                        rationale,
                        observed_location,
                        timestamp,
                        existing["id"],
                    ),
                )
                site_id = int(existing["id"])
                action = "updated_candidate"
                updated += 1
            else:
                site_id = int(existing["id"])
                action = "preserved_reviewed"
                preserved += 1

            source_links: list[tuple[int, object]] = []
            for source in record.sources:
                source_id = _upsert_source(connection, source, timestamp)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence
                        (site_id, source_id, role, created_at)
                    VALUES (?, ?, 'source', ?)
                    """,
                    (site_id, source_id, timestamp),
                )
                evidence_attached += cursor.rowcount
                source_links.append((source_id, source))

            payload_json = dump_json(record.model_dump(mode="json"))
            import_record = connection.execute(
                """
                INSERT INTO import_records
                    (batch_id, external_key, site_id, action, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (package.batch_id, record.external_key, site_id, action, payload_json, timestamp),
            )
            import_record_id = int(import_record.lastrowid)
            for source_index, (source_id, source) in enumerate(source_links):
                connection.execute(
                    """
                    INSERT INTO evidence_items
                        (site_id, source_id, import_record_id, source_index, excerpt,
                         content_kind, role, published_at, accessed_at, provenance_status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'unknown', 'context', ?, ?, 'import_record', ?)
                    """,
                    (
                        site_id,
                        source_id,
                        import_record_id,
                        source_index,
                        source.excerpt,
                        source.publication_date.isoformat() if source.publication_date else None,
                        source.access_date.isoformat() if source.access_date else None,
                        timestamp,
                    ),
                )
            connection.execute(
                """
                INSERT INTO site_events (site_id, event_type, payload_json, created_at)
                VALUES (?, 'import', ?, ?)
                """,
                (site_id, payload_json, timestamp),
            )
        connection.execute(
            "UPDATE import_batches SET committed_at = ? WHERE batch_id = ?",
            (timestamp, package.batch_id),
        )
    return {
        "batch_id": package.batch_id,
        "created": created,
        "updated": updated,
        "preserved": preserved,
        "evidence_attached": evidence_attached,
        "idempotent": False,
    }


def _require_admin(settings: Settings, authorization: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(503, "admin authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(
        token.encode("utf-8"), settings.admin_token.encode("utf-8")
    ):
        raise HTTPException(401, "invalid bearer token")


def _route_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "start": json.loads(row["start_json"]),
        "waypoints": json.loads(row["waypoints_json"]),
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "created_at": row["created_at"],
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="Bunkerkartet", version=settings.app_version)
    app.state.settings = settings
    app.state.database = database

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": safe_validation_errors(exc.errors())})

    @app.middleware("http")
    async def set_response_headers(request, call_next):
        response = await call_next(request)
        if request.url.path in {"/", "/static/app.js", "/static/styles.css"} or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def admin_guard(authorization: str | None = Header(default=None)) -> None:
        _require_admin(settings, authorization)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text()
        html = html.replace(
            '/static/app.js"',
            f'/static/app.js?v={escape_html(settings.app_version, quote=True)}"',
        )
        return HTMLResponse(html)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            raise HTTPException(503, "database unavailable")
        return {"status": "healthy", "version": settings.app_version, "database": "ready"}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": settings.app_version}

    @app.get("/api/imports/schema", dependencies=[Depends(admin_guard)])
    def import_schema() -> dict[str, object]:
        return ImportPackage.model_json_schema()

    @app.post("/api/admin/imports/preview", dependencies=[Depends(admin_guard)])
    def preview_import(payload: dict[str, object]) -> dict[str, object]:
        try:
            package = validate_import_package(payload)
        except ValidationError as error:
            raise HTTPException(422, detail=_validation_detail(error))
        with database.connect() as connection:
            return _preview_package(connection, package)

    @app.post("/api/admin/imports/commit", dependencies=[Depends(admin_guard)])
    def commit_import(
        payload: dict[str, object],
        x_import_preview: str | None = Header(default=None),
    ) -> dict[str, object]:
        try:
            package = validate_import_package(payload)
        except ValidationError as error:
            raise HTTPException(422, detail=_validation_detail(error))
        return _commit_package(database, package, x_import_preview)

    @app.get("/api/sites", dependencies=[Depends(admin_guard)])
    def list_sites(
        status: str | None = Query(default=None),
        site_kind: str | None = Query(default=None, max_length=100),
        q: str | None = Query(default=None, max_length=200),
        access: str | None = Query(default=None),
        confidence: str | None = Query(default=None),
        include_rejected: bool = Query(default=False),
    ) -> list[dict[str, object]]:
        conditions = ["merged_into_id IS NULL"]
        values: list[object] = []
        if status:
            if status not in SITE_STATUSES:
                raise HTTPException(400, "invalid site status")
            conditions.append("status = ?")
            values.append(status)
        elif not include_rejected:
            conditions.append("status <> 'rejected'")
        if site_kind and site_kind.strip():
            conditions.append("lower(site_kind) LIKE lower(?)")
            values.append(f"%{site_kind.strip()}%")
        if access:
            if access not in SITE_ACCESSES:
                raise HTTPException(400, "invalid site access")
            conditions.append("access = ?")
            values.append(access)
        if confidence:
            if confidence not in CONFIDENCE_LEVELS:
                raise HTTPException(400, "invalid site confidence")
            conditions.append("COALESCE(confidence, 'unknown') = ?")
            values.append(confidence)
        if q and q.strip():
            term = f"%{q.strip()}%"
            searchable_fields = ("name", "site_kind", "short_rationale", "observed_location_text", "condition")
            conditions.append(
                "(" + " OR ".join(
                    f"lower(COALESCE({field}, '')) LIKE lower(?)" for field in searchable_fields
                ) + " OR EXISTS ("
                "SELECT 1 FROM evidence JOIN sources ON sources.id = evidence.source_id "
                "WHERE evidence.site_id = sites.id AND ("
                "lower(COALESCE(sources.title, '')) LIKE lower(?) OR "
                "lower(COALESCE(sources.excerpt, '')) LIKE lower(?)"
                ")))"
            )
            values.extend([term] * (len(searchable_fields) + 2))
        query = f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY name, id"
        with database.connect() as connection:
            return [_site_summary(connection, row) for row in connection.execute(query, values).fetchall()]

    @app.get("/api/sites.geojson", dependencies=[Depends(admin_guard)])
    def export_sites_geojson() -> JSONResponse:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sites WHERE merged_into_id IS NULL AND status <> 'rejected' ORDER BY name, id"
            ).fetchall()
            features: list[dict[str, object]] = []
            for row in rows:
                site = _site_summary(connection, row)
                site_id = int(site["id"])
                geometry = None
                if site["latitude"] is not None and site["longitude"] is not None:
                    geometry = {
                        "type": "Point",
                        "coordinates": [site["longitude"], site["latitude"]],
                    }
                properties = {
                    key: value for key, value in site.items() if key != "observation_points"
                }
                properties["feature_type"] = "site"
                features.append(
                    {"type": "Feature", "id": site_id, "geometry": geometry, "properties": properties}
                )
                for observation in site["observation_points"]:
                    features.append(
                        {
                            "type": "Feature",
                            "id": f"observation-{observation['id']}",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [observation["longitude"], observation["latitude"]],
                            },
                            "properties": {
                                "feature_type": "field_observation",
                                "site_id": site_id,
                                "observed_at": observation["observed_at"],
                                "outcome": observation["outcome"],
                            },
                        }
                    )
        return JSONResponse(
            {"type": "FeatureCollection", "features": features},
            media_type="application/geo+json",
        )

    @app.get("/api/field-priority", dependencies=[Depends(admin_guard)])
    def field_priority(limit: int = Query(default=12, ge=1, le=50)) -> list[dict[str, object]]:
        # ponytail: public-only shortlist avoids inferring permission from unknown access.
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sites
                WHERE merged_into_id IS NULL
                  AND status IN ('candidate', 'likely')
                  AND access = 'public'
                  AND latitude IS NOT NULL AND longitude IS NOT NULL
                ORDER BY
                  CASE COALESCE(confidence, 'unknown')
                    WHEN 'high' THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low' THEN 2
                    ELSE 3
                  END,
                  CASE WHEN uncertainty_m IS NULL THEN 1 ELSE 0 END,
                  uncertainty_m,
                  name,
                  id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [_site_detail(connection, row) for row in rows]

    @app.get("/api/review/candidates", dependencies=[Depends(admin_guard)])
    def candidates(
        confidence: str | None = Query(default=None),
        access: str | None = Query(default=None),
        uncertainty_band: Literal["under-100", "100-500", "over-500", "unknown"] | None = Query(default=None),
        source_type: str | None = Query(default=None, max_length=100),
    ) -> list[dict[str, object]]:
        if confidence is not None and confidence not in CONFIDENCE_LEVELS:
            raise HTTPException(400, "invalid candidate confidence")
        if access is not None and access not in SITE_ACCESSES:
            raise HTTPException(400, "invalid candidate access")
        conditions = ["sites.status = 'candidate'", "sites.merged_into_id IS NULL"]
        values: list[object] = []
        if confidence:
            conditions.append("COALESCE(sites.confidence, 'unknown') = ?")
            values.append(confidence)
        if access:
            conditions.append("sites.access = ?")
            values.append(access)
        if uncertainty_band == "under-100":
            conditions.append("sites.uncertainty_m IS NOT NULL AND sites.uncertainty_m <= 100")
        elif uncertainty_band == "100-500":
            conditions.append("sites.uncertainty_m > 100 AND sites.uncertainty_m <= 500")
        elif uncertainty_band == "over-500":
            conditions.append("sites.uncertainty_m > 500")
        elif uncertainty_band == "unknown":
            conditions.append("sites.uncertainty_m IS NULL")
        if source_type and source_type.strip():
            conditions.append(
                "EXISTS ("
                "SELECT 1 FROM evidence "
                "JOIN sources ON sources.id = evidence.source_id "
                "WHERE evidence.site_id = sites.id "
                "AND lower(sources.source_type) = lower(?)"
                ")"
            )
            values.append(source_type.strip())
        with database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY "
                "CASE COALESCE(sites.confidence, 'unknown') WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, "
                "CASE WHEN sites.uncertainty_m IS NULL THEN 1 ELSE 0 END, sites.uncertainty_m, sites.id",
                values,
            ).fetchall()
            return [_site_detail(connection, row) for row in rows]

    @app.get("/api/sites/{site_id}", dependencies=[Depends(admin_guard)])
    def get_site(site_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            return _site_detail(connection, row)

    @app.patch("/api/sites/{site_id}", dependencies=[Depends(admin_guard)])
    def edit_site(site_id: int, patch: SitePatch) -> dict[str, object]:
        values = patch.model_dump(exclude_unset=True)
        if "name" in values and values["name"] is not None and "snublestein" in values["name"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if "site_kind" in values and values["site_kind"] is not None and "snublestein" in values["site_kind"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if "warnings" in values:
            values["warnings_json"] = dump_json(values.pop("warnings"))
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            new_latitude = values.get("latitude", row["latitude"])
            new_longitude = values.get("longitude", row["longitude"])
            if (new_latitude is None) != (new_longitude is None):
                raise HTTPException(422, "latitude and longitude must be edited together")
            new_precision = values.get("precision", row["precision"])
            new_uncertainty = values.get("uncertainty_m", row["uncertainty_m"])
            new_location_basis = values.get("location_basis", row["location_basis"])
            if new_latitude is None:
                if new_precision != "unknown":
                    raise HTTPException(422, "a site without coordinates must use unknown precision")
                if new_uncertainty is not None:
                    if values.get("uncertainty_m") is not None:
                        raise HTTPException(422, "a site without coordinates cannot have uncertainty")
                    values["uncertainty_m"] = None
                    new_uncertainty = None
            elif new_uncertainty is None:
                raise HTTPException(422, "a site with coordinates requires uncertainty")
            try:
                validate_location(
                    new_latitude, new_longitude, new_precision, new_uncertainty, new_location_basis
                )
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if "status" in values and values["status"] != row["status"]:
                raise HTTPException(409, "status changes must use the review workflow")
            values.pop("status", None)
            if not values:
                raise HTTPException(400, "no site fields supplied")
            assignments = ", ".join(f"{field} = ?" for field in values)
            values["updated_at"] = now_iso()
            params = [*values.values(), site_id]
            connection.execute(
                f"UPDATE sites SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'edit', ?, ?)",
                (site_id, dump_json(values), now_iso()),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return _site_detail(connection, updated)

    @app.post(
        "/api/sites/{site_id}/observations",
        status_code=201,
        dependencies=[Depends(admin_guard)],
    )
    def add_field_observation(site_id: int, observation: FieldObservation) -> dict[str, object]:
        timestamp = now_iso()
        payload = observation.model_dump(mode="json")
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot receive observations")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot receive observations")
            cursor = connection.execute(
                """
                INSERT INTO field_observations
                    (site_id, observed_at, outcome, note, latitude, longitude,
                     point_role, uncertainty_m, observed_location_text, access_notes,
                     photo_urls_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site_id,
                    observation.observed_at.isoformat(),
                    observation.outcome,
                    observation.note,
                    observation.latitude,
                    observation.longitude,
                    observation.point_role,
                    observation.uncertainty_m,
                    observation.observed_location_text,
                    observation.access_notes,
                    dump_json(payload["photo_urls"]),
                    timestamp,
                ),
            )
            observation_row = connection.execute(
                "SELECT * FROM field_observations WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'observation', ?, ?)",
                (site_id, dump_json(payload), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {
                "observation": _safe_observation(observation_row),
                "site": _site_detail(connection, updated),
            }

    @app.post(
        "/api/sites/{site_id}/observations/{observation_id}/adopt-location",
        dependencies=[Depends(admin_guard)],
    )
    def adopt_observation_location(site_id: int, observation_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot adopt an observation coordinate")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot adopt an observation coordinate")
            observation = connection.execute(
                "SELECT * FROM field_observations WHERE id = ? AND site_id = ?",
                (observation_id, site_id),
            ).fetchone()
            if observation is None:
                raise HTTPException(404, "observation not found")
            if observation["outcome"] != "found":
                raise HTTPException(409, "only a found observation can update the site coordinate")
            if observation["latitude"] is None or observation["longitude"] is None:
                raise HTTPException(409, "observation has no coordinate")
            if observation["point_role"] != "feature":
                raise HTTPException(409, "only a feature observation can replace the site coordinate")
            if observation["uncertainty_m"] is None:
                raise HTTPException(409, "observation requires an explicit radius")
            try:
                validate_location(
                    observation["latitude"], observation["longitude"],
                    "approximate", observation["uncertainty_m"], "explicit_coordinate",
                )
            except ValueError as error:
                raise HTTPException(409, str(error)) from error
            timestamp = now_iso()
            rationale = (row["short_rationale"] or "").strip()
            update_note = (
                f"Field observation #{observation_id} recorded {observation['observed_at']} "
                "was used to update the map coordinate."
            )
            rationale = f"{rationale} {update_note}".strip()[:2000]
            connection.execute(
                """
                UPDATE sites
                SET latitude = ?, longitude = ?, precision = 'approximate', uncertainty_m = ?,
                    location_basis = ?, short_rationale = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    observation["latitude"],
                    observation["longitude"],
                    observation["uncertainty_m"],
                    "explicit_coordinate",
                    rationale,
                    timestamp,
                    site_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO site_events (site_id, event_type, payload_json, created_at)
                VALUES (?, 'coordinate_adopted', ?, ?)
                """,
                (
                    site_id,
                    dump_json(
                        {
                            "observation_id": observation_id,
                            "old_latitude": row["latitude"],
                            "old_longitude": row["longitude"],
                            "new_latitude": observation["latitude"],
                            "new_longitude": observation["longitude"],
                        }
                    ),
                    timestamp,
                ),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": "coordinate_adopted", "site": _site_detail(connection, updated)}

    @app.post("/api/sites/{site_id}/review", dependencies=[Depends(admin_guard)])
    def review_site(site_id: int, request: ReviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot be reviewed")
            if request.action == "merge":
                if request.target_site_id is None or request.target_site_id == site_id:
                    raise HTTPException(400, "merge requires a different target site")
                target = connection.execute(
                    "SELECT id FROM sites WHERE id = ? AND id <> ? "
                    "AND merged_into_id IS NULL AND status <> 'rejected'",
                    (request.target_site_id, site_id),
                ).fetchone()
                if target is None:
                    raise HTTPException(404, "merge target not found")
                connection.execute(
                    "UPDATE sites SET merged_into_id = ?, status = 'rejected', updated_at = ? WHERE id = ?",
                    (request.target_site_id, now_iso(), site_id),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence (site_id, source_id, role, created_at)
                    SELECT ?, source_id, role, created_at FROM evidence WHERE site_id = ?
                    """,
                    (request.target_site_id, site_id),
                )
                connection.execute(
                    "UPDATE evidence_items SET site_id = ? WHERE site_id = ?",
                    (request.target_site_id, site_id),
                )
                connection.execute("DELETE FROM evidence WHERE site_id = ?", (site_id,))
                connection.execute(
                    "UPDATE field_observations SET site_id = ? WHERE site_id = ?",
                    (request.target_site_id, site_id),
                )
                status = "merged"
            else:
                status = {
                    "accept": "likely",
                    "research": "likely",
                    "field_verify": "field-verified",
                    "confirm": "trusted",
                    "reject": "rejected",
                    "restore": "candidate",
                    "mark_approximate": "approximate",
                    "mark_destroyed": "destroyed-or-filled",
                }[request.action]
                allowed_statuses = {
                    "accept": {"candidate", "approximate"},
                    "research": {"candidate", "approximate"},
                    "field_verify": {"likely"},
                    "confirm": {"field-verified", "trusted"},
                    "restore": {"rejected"},
                    "mark_approximate": {"candidate"},
                    "mark_destroyed": {"candidate", "approximate", "likely", "field-verified", "trusted"},
                }
                if request.action in allowed_statuses and row["status"] not in allowed_statuses[request.action]:
                    raise HTTPException(409, "invalid lifecycle transition")
                if request.action == "field_verify":
                    if row["status"] != "likely":
                        raise HTTPException(409, "field verification requires a researched site")
                    observation = connection.execute(
                        "SELECT 1 FROM field_observations "
                        "WHERE site_id = ? AND outcome = 'found' LIMIT 1",
                        (site_id,),
                    ).fetchone()
                    if observation is None:
                        raise HTTPException(409, "record a found field observation before field verification")
                if request.action == "confirm" and row["status"] not in {
                    "field-verified",
                    "trusted",
                }:
                    raise HTTPException(409, "field verification is required before confirmation")
                connection.execute(
                    "UPDATE sites SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now_iso(), site_id),
                )
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'review', ?, ?)",
                (site_id, dump_json(request.model_dump()), now_iso()),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": status, "site": _site_detail(connection, updated)}

    @app.post("/api/routes", dependencies=[Depends(admin_guard)])
    def create_route(request: RouteRequest) -> dict[str, object]:
        if not settings.ors_api_key:
            raise HTTPException(503, "routing is not configured")
        coordinates = [(request.start.lon, request.start.lat)] + [
            (point.lon, point.lat) for point in request.waypoints
        ]
        try:
            route: RouteResult = fetch_openrouteservice(settings.ors_api_key, coordinates)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(502, f"routing provider error: {error}")
        warnings = _route_warnings(coordinates, route.coordinates)
        waypoint_names = request.waypoint_names or [f"Stop {index}" for index in range(1, len(request.waypoints) + 1)]
        named_waypoints = [(request.start.lon, request.start.lat, "Route start")]
        named_waypoints.extend(
            (point.lon, point.lat, name)
            for point, name in zip(request.waypoints, waypoint_names)
        )
        gpx = build_gpx(request.name, route.coordinates, named_waypoints)
        timestamp = now_iso()
        with database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO route_plans
                    (name, start_json, waypoints_json, distance_m, duration_s,
                     geometry_json, gpx_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.name,
                    dump_json(request.start.model_dump()),
                    dump_json([point.model_dump() for point in request.waypoints]),
                    route.distance_m,
                    route.duration_s,
                    dump_json(route.coordinates),
                    gpx,
                    timestamp,
                ),
            )
            route_id = cursor.lastrowid
        return {
            "id": route_id,
            "name": request.name,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "geometry": {"type": "LineString", "coordinates": route.coordinates},
            "gpx": gpx,
            "warnings": warnings,
            "created_at": timestamp,
        }

    @app.get("/api/routes", dependencies=[Depends(admin_guard)])
    def list_routes(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, start_json, waypoints_json, distance_m, duration_s, created_at "
                "FROM route_plans ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_route_summary(row) for row in rows]

    @app.get("/api/routes/{route_id}", dependencies=[Depends(admin_guard)])
    def get_route(route_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM route_plans WHERE id = ?", (route_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "route not found")
            route = _route_summary(row)
            route_coordinates = json.loads(row["geometry_json"])
            route["geometry"] = {"type": "LineString", "coordinates": route_coordinates}
            route["gpx"] = row["gpx_text"]
            requested_coordinates = [(route["start"]["lon"], route["start"]["lat"])] + [
                (point["lon"], point["lat"]) for point in route["waypoints"]
            ]
            route["warnings"] = _route_warnings(requested_coordinates, route_coordinates)
            return route

    return app


app = create_app()
