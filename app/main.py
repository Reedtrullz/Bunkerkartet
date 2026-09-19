from __future__ import annotations

from datetime import date, datetime
from html import escape as escape_html
import hashlib
import hmac
import json
import math
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from urllib.error import HTTPError, URLError

from app.config import Settings
from app.enrichment import load_site_enrichment
from app.db import (
    CURRENT_SCHEMA_VERSION,
    REQUIRED_SCHEMA,
    Database,
    canonical_payload_hash,
    dump_json,
    load_json,
    now_iso,
    observation_from_row,
    required_schema_errors,
    site_from_row,
)
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
MAX_IMPORT_BODY_BYTES = 2 * 1024 * 1024
# ponytail: one process-wide semaphore is enough for the bounded provider quota; use a distributed limiter if scaling out.
ORS_SEMAPHORE = threading.BoundedSemaphore(2)
SITE_ENRICHMENT = load_site_enrichment()


async def _read_import_json(request: Request) -> object:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_IMPORT_BODY_BYTES:
        raise HTTPException(413, "request body too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_IMPORT_BODY_BYTES:
            raise HTTPException(413, "request body too large")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(422, "request body must be valid JSON") from error


class SitePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(gt=0)
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

    request_id: str | None = Field(default=None, min_length=1, max_length=200)
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
    expected_revision: int | None = Field(default=None, gt=0)


class LocationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(gt=0)

    @field_validator("reason")
    @classmethod
    def require_nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("location review reason cannot be blank")
        return value


class ExpectedRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, gt=0)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ApproachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, gt=0)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    access: Literal["unknown", "public"]
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def require_nonblank_note(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approach note cannot be blank")
        return value


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Trondheim field route", min_length=1, max_length=200)
    start: RoutePoint
    site_ids: list[int] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_site_ids(self) -> "RouteRequest":
        if any(site_id <= 0 for site_id in self.site_ids):
            raise ValueError("site_ids must be positive")
        if len(set(self.site_ids)) != len(self.site_ids):
            raise ValueError("site_ids must be unique")
        return self


def _validation_detail(error: ValidationError) -> list[dict[str, object]]:
    return safe_validation_errors(error.errors())


def _payload_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(first[1]), math.radians(first[0])
    lat2, lon2 = math.radians(second[1]), math.radians(second[0])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _route_warnings(
    requested_coordinates: list[tuple[float, float]],
    route: RouteResult,
    labels: list[str],
) -> list[str]:
    warnings = ["A route does not grant permission to enter land or structures."]
    if route.waypoint_indices is None:
        warnings.append("Provider waypoint snapping was not returned; intermediate stop snapping was not controlled.")
        if _distance_m(requested_coordinates[0], route.coordinates[0]) > 50 or _distance_m(
            requested_coordinates[-1], route.coordinates[-1]
        ) > 50:
            warnings.append("The provider snapped a route endpoint; verify the approach on site.")
        return warnings
    if len(route.waypoint_indices) != len(requested_coordinates):
        raise ValueError("routing provider returned an unexpected waypoint count")
    for requested, index, label in zip(requested_coordinates, route.waypoint_indices, labels):
        distance = _distance_m(requested, route.coordinates[index])
        if distance > 50:
            warnings.append(f"{label}: provider snapped this stop by {round(distance)} m; verify the approach.")
    return warnings


def _route_site_error(row: sqlite3.Row, site_id: int) -> str | None:
    if row["merged_into_id"] is not None:
        return f"site {site_id} is merged"
    if row["status"] == "rejected":
        return f"site {site_id} is rejected"
    if row["location_review_required"]:
        return f"site {site_id} requires a location review before routing"
    if (
        row["approach_latitude"] is None
        or row["approach_longitude"] is None
        or row["approach_access"] != "public"
        or row["approach_reviewed_at"] is None
    ):
        return f"site {site_id} has no reviewed public approach"
    return None


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
    enrichment = SITE_ENRICHMENT.get(row["external_key"])
    if enrichment:
        site["enrichment"] = {
            key: enrichment[key]
            for key in ("display_name", "kind_label", "research_state", "reviewed_at")
        }
    site["observation_points"] = _observation_points(connection, int(row["id"]))
    return site


def _site_relations(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT site_relations.related_external_key, site_relations.relation_kind,
               related.id AS related_site_id, related.name AS related_name,
               related.status AS related_status
        FROM site_relations
        LEFT JOIN sites AS related ON related.external_key = site_relations.related_external_key
        WHERE site_relations.site_id = ?
        ORDER BY site_relations.related_external_key
        """,
        (site_id,),
    ).fetchall()
    relations: list[dict[str, object]] = []
    for row in rows:
        relation = dict(row)
        if relation["related_site_id"] is None:
            relation["related_status"] = "not_imported"
        relations.append(relation)
    return relations


def _site_detail(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    site = site_from_row(row)
    site["enrichment"] = SITE_ENRICHMENT.get(row["external_key"])
    site["relations"] = _site_relations(connection, int(row["id"]))
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


def _normalized_label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _possible_relation_warning(external_key: str) -> str:
    return f"possible relation of {external_key}: overlapping uncertainty areas; not proof of the same object"


def _duplicate_warnings(
    connection: sqlite3.Connection, record: ImportRecord
) -> list[str]:
    geometry = record.geometry
    warnings = list(record.warnings)
    rows = connection.execute(
        """
        SELECT external_key, name, site_kind, latitude, longitude, uncertainty_m
        FROM sites
        WHERE merged_into_id IS NULL AND external_key <> ?
        """,
        (record.external_key,),
    ).fetchall()
    # ponytail: bounded O(n^2) scan is enough for the private v1 catalogue; add a spatial index at scale.
    for row in rows:
        if _normalized_label(row["name"]) != _normalized_label(record.name):
            continue
        if _normalized_label(row["site_kind"]) != _normalized_label(record.site_kind):
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
        elif (
            record.uncertainty_m is not None
            and row["uncertainty_m"] is not None
            and distance <= record.uncertainty_m + float(row["uncertainty_m"])
        ):
            warnings.append(_possible_relation_warning(row["external_key"]))
    for row in rows:
        if (
            _normalized_label(row["site_kind"]) != _normalized_label(record.site_kind)
            or _normalized_label(row["name"]) == _normalized_label(record.name)
        ):
            continue
        if (
            geometry is None
            or row["latitude"] is None
            or row["longitude"] is None
            or record.uncertainty_m is None
            or row["uncertainty_m"] is None
        ):
            continue
        distance = _distance_m(
            (float(geometry.longitude), float(geometry.latitude)),
            (float(row["longitude"]), float(row["latitude"])),
        )
        if distance <= record.uncertainty_m + float(row["uncertainty_m"]):
            warnings.append(_possible_relation_warning(row["external_key"]))
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
        if _normalized_label(other.name) != _normalized_label(record.name) or _normalized_label(other.site_kind) != _normalized_label(record.site_kind):
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
        elif (
            record.uncertainty_m is not None
            and other.uncertainty_m is not None
            and distance <= record.uncertainty_m + other.uncertainty_m
        ):
            warnings.append(_possible_relation_warning(other.external_key))
    for other in sorted(records, key=lambda item: item.external_key):
        if (
            other.external_key == record.external_key
            or _normalized_label(other.site_kind) != _normalized_label(record.site_kind)
            or _normalized_label(other.name) == _normalized_label(record.name)
        ):
            continue
        if (
            record.geometry is None
            or other.geometry is None
            or record.uncertainty_m is None
            or other.uncertainty_m is None
        ):
            continue
        distance = _distance_m(
            (record.geometry.longitude, record.geometry.latitude),
            (other.geometry.longitude, other.geometry.latitude),
        )
        if distance <= record.uncertainty_m + other.uncertainty_m:
            warnings.append(_possible_relation_warning(other.external_key))
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
        "related_site_keys": list(record.related_site_keys),
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
                        observed_location_text = ?, updated_at = ?, revision = revision + 1
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
                reviewed_update = connection.execute(
                    "UPDATE sites SET revision = revision + 1, updated_at = ? WHERE id = ?",
                    (timestamp, site_id),
                )
                if reviewed_update.rowcount != 1:
                    raise HTTPException(409, "site changed while importing")
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
            for related_external_key in record.related_site_keys:
                connection.execute(
                    "INSERT OR IGNORE INTO site_relations (site_id, related_external_key, relation_kind, created_at) VALUES (?, ?, 'related', ?)",
                    (site_id, related_external_key, timestamp),
                )
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
    route = {
        "id": row["id"],
        "name": row["name"],
        "start": json.loads(row["start_json"]),
        "waypoints": json.loads(row["waypoints_json"]),
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "created_at": row["created_at"],
    }
    stops = load_json(row["stops_json"], None) if "stops_json" in row.keys() else None
    stored_warnings = load_json(row["route_warnings_json"], None) if "route_warnings_json" in row.keys() else None
    route["warnings"] = stored_warnings if isinstance(stored_warnings, list) else []
    if isinstance(stops, list):
        route["stops"] = stops
        route["waypoints"] = [{"lat": stop["lat"], "lon": stop["lon"]} for stop in stops]
        route["legacy_route"] = False
    else:
        route["stops"] = []
        route["legacy_route"] = True
    return route


def _safe_event_payload(event_type: str, payload_json: str) -> dict[str, object]:
    payload = load_json(payload_json, {})
    if not isinstance(payload, dict):
        return {}
    if event_type == "import":
        return {key: payload[key] for key in ("external_key", "action") if key in payload}
    allowed = {
        "edit": {"before", "after"},
        "review": {"action", "target_site_id"},
        "merge_received": {"merged_from_site_id"},
        "approach_review": {"latitude", "longitude", "access", "note", "reviewed_at"},
        "location_review": {"reason"},
        "observation": {
            "observed_at", "outcome", "note", "latitude", "longitude", "point_role",
            "uncertainty_m", "observed_location_text", "access_notes",
        },
        "coordinate_adopted": {
            "observation_id", "old_latitude", "old_longitude", "new_latitude", "new_longitude",
        },
    }.get(event_type, set())
    if event_type == "edit":
        return {
            side: {
                key: value for key, value in payload.get(side, {}).items()
                if key in _PREVIEW_FIELDS
            }
            for side in ("before", "after")
            if isinstance(payload.get(side), dict)
        }
    return {key: payload[key] for key in allowed if key in payload}


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

    @app.get("/api/ready")
    def readiness() -> JSONResponse:
        database_status = "ready"
        try:
            if not database.path.exists():
                database_status = "unavailable"
            else:
                with sqlite3.connect(f"{database.path.resolve().as_uri()}?mode=ro", uri=True) as connection:
                    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        database_status = "unavailable"
                    elif required_schema_errors(connection, required_schema=REQUIRED_SCHEMA):
                        database_status = "schema_not_ready"
                    elif connection.execute("PRAGMA user_version").fetchone()[0] != CURRENT_SCHEMA_VERSION:
                        database_status = "schema_not_ready"
        except sqlite3.Error:
            database_status = "unavailable"
        authentication = "configured" if settings.admin_token else "not_configured"
        ready = database_status == "ready" and authentication == "configured"
        payload = {
            "status": "ready" if ready else "not_ready",
            "database": database_status,
            "authentication": authentication,
            "routing": "configured" if settings.ors_api_key else "optional-unconfigured",
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": settings.app_version}

    @app.get("/api/imports/schema", dependencies=[Depends(admin_guard)])
    def import_schema() -> dict[str, object]:
        return ImportPackage.model_json_schema()

    @app.post("/api/admin/imports/preview", dependencies=[Depends(admin_guard)])
    async def preview_import(request: Request) -> dict[str, object]:
        payload = await _read_import_json(request)

        def work() -> dict[str, object]:
            try:
                package = validate_import_package(payload)
            except ValidationError as error:
                raise HTTPException(422, detail=_validation_detail(error))
            with database.connect() as connection:
                return _preview_package(connection, package)

        return await run_in_threadpool(work)

    @app.post("/api/admin/imports/commit", dependencies=[Depends(admin_guard)])
    async def commit_import(
        request: Request,
        x_import_preview: str | None = Header(default=None),
    ) -> dict[str, object]:
        payload = await _read_import_json(request)

        def work() -> dict[str, object]:
            try:
                package = validate_import_package(payload)
            except ValidationError as error:
                raise HTTPException(422, detail=_validation_detail(error))
            return _commit_package(database, package, x_import_preview)

        return await run_in_threadpool(work)

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
            database_search = (
                "(" + " OR ".join(
                    f"lower(COALESCE({field}, '')) LIKE lower(?)" for field in searchable_fields
                ) + " OR EXISTS ("
                "SELECT 1 FROM evidence JOIN sources ON sources.id = evidence.source_id "
                "WHERE evidence.site_id = sites.id AND ("
                "lower(COALESCE(sources.title, '')) LIKE lower(?) OR "
                "lower(COALESCE(sources.excerpt, '')) LIKE lower(?)"
                ")))"
            )
            enrichment_keys = [
                external_key
                for external_key, enrichment in SITE_ENRICHMENT.items()
                if q.strip().casefold() in " ".join(
                    str(enrichment.get(field, ""))
                    for field in ("display_name", "kind_label")
                ).casefold()
            ]
            if enrichment_keys:
                placeholders = ",".join("?" for _ in enrichment_keys)
                conditions.append(f"({database_search} OR external_key IN ({placeholders}))")
                values.extend([term] * (len(searchable_fields) + 2))
                values.extend(enrichment_keys)
            else:
                conditions.append(database_search)
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

    @app.get("/api/sites/{site_id}/events", dependencies=[Depends(admin_guard)])
    def get_site_events(site_id: int, limit: int = Query(default=50, ge=1, le=100)) -> list[dict[str, object]]:
        with database.connect() as connection:
            if connection.execute("SELECT 1 FROM sites WHERE id = ?", (site_id,)).fetchone() is None:
                raise HTTPException(404, "site not found")
            rows = connection.execute(
                "SELECT id, event_type, payload_json, created_at FROM site_events WHERE site_id = ? ORDER BY id DESC LIMIT ?",
                (site_id, limit),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "created_at": row["created_at"],
                    "payload": _safe_event_payload(row["event_type"], row["payload_json"]),
                }
                for row in rows
            ]

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
        expected_revision = values.pop("expected_revision")
        if "name" in values and values["name"] is not None and "snublestein" in values["name"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if "site_kind" in values and values["site_kind"] is not None and "snublestein" in values["site_kind"].casefold():
            raise HTTPException(422, "snublestein records are excluded")
        if "warnings" in values:
            values["warnings_json"] = dump_json(values.pop("warnings"))
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["revision"] != expected_revision:
                raise HTTPException(409, "site revision is stale; reload before editing")
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
            if row["status"] in {"trusted", "field-verified"} and (
                new_latitude != row["latitude"] or new_longitude != row["longitude"]
            ):
                values["location_review_required"] = 1
            if not values:
                raise HTTPException(400, "no site fields supplied")
            event_before = {}
            event_after = {}
            for field, value in values.items():
                event_field = "warnings" if field == "warnings_json" else field
                old_value = row[field]
                if field == "warnings_json":
                    old_value = load_json(old_value, [])
                    value = load_json(value, [])
                event_before[event_field] = old_value
                event_after[event_field] = value
            assignments = ", ".join(f"{field} = ?" for field in values)
            timestamp = now_iso()
            cursor = connection.execute(
                f"UPDATE sites SET {assignments}, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                [*values.values(), timestamp, site_id, expected_revision],
            )
            if cursor.rowcount != 1:
                raise HTTPException(409, "site revision is stale; reload before editing")
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'edit', ?, ?)",
                (site_id, dump_json({"before": event_before, "after": event_after}), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return _site_detail(connection, updated)

    @app.post("/api/sites/{site_id}/approach", dependencies=[Depends(admin_guard)])
    def set_site_approach(site_id: int, request: ApproachRequest) -> dict[str, object]:
        timestamp = now_iso()
        reviewed_at = timestamp if request.access == "public" else None
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot set an approach")
            expected_revision = request.expected_revision or row["revision"]
            if expected_revision != row["revision"]:
                raise HTTPException(409, "site revision is stale; reload before editing")
            timestamp = now_iso()
            updated = connection.execute(
                """
                UPDATE sites
                SET approach_latitude = ?, approach_longitude = ?, approach_access = ?,
                    approach_note = ?, approach_reviewed_at = ?, updated_at = ?, revision = revision + 1
                WHERE id = ? AND revision = ?
                """,
                (
                    request.latitude, request.longitude, request.access, request.note.strip(),
                    reviewed_at, timestamp, site_id, expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, "site revision is stale; reload before editing")
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'approach_review', ?, ?)",
                (site_id, dump_json({**request.model_dump(exclude={"expected_revision"}), "reviewed_at": reviewed_at}), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return _site_detail(connection, updated)

    @app.post("/api/sites/{site_id}/location-review", dependencies=[Depends(admin_guard)])
    def review_site_location(site_id: int, request: LocationReviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot review location")
            if row["revision"] != request.expected_revision:
                raise HTTPException(409, "site revision is stale; reload before reviewing location")
            if not row["location_review_required"]:
                raise HTTPException(409, "site does not require a location review")
            timestamp = now_iso()
            updated = connection.execute(
                "UPDATE sites SET location_review_required = 0, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                (timestamp, site_id, request.expected_revision),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, "site revision is stale; reload before reviewing location")
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'location_review', ?, ?)",
                (site_id, dump_json({"reason": request.reason.strip()}), timestamp),
            )
            current = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": "location_reviewed", "site": _site_detail(connection, current)}

    @app.post("/api/sites/{site_id}/observations", dependencies=[Depends(admin_guard)])
    def add_field_observation(site_id: int, observation: FieldObservation) -> dict[str, object]:
        timestamp = now_iso()
        request_id = observation.request_id or str(uuid.uuid4())
        payload = observation.model_dump(mode="json", exclude={"request_id"})
        payload_hash = _payload_digest(payload)
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            existing = connection.execute(
                "SELECT * FROM field_observations WHERE site_id = ? AND request_id = ?",
                (site_id, request_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, "observation request_id already has a different payload")
                return {
                    "observation": _safe_observation(existing),
                    "site": _site_detail(connection, row),
                    "idempotent": True,
                }
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot receive observations")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot receive observations")
            cursor = connection.execute(
                """
                INSERT INTO field_observations
                    (site_id, observed_at, outcome, note, latitude, longitude,
                     point_role, uncertainty_m, observed_location_text, access_notes,
                     photo_urls_json, request_id, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    request_id,
                    payload_hash,
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
            updated_result = connection.execute(
                "UPDATE sites SET revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?",
                (timestamp, site_id, row["revision"]),
            )
            if updated_result.rowcount != 1:
                raise HTTPException(409, "site revision is stale; reload before recording observation")
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return JSONResponse(
                status_code=201,
                content={
                    "observation": _safe_observation(observation_row),
                    "site": _site_detail(connection, updated),
                    "idempotent": False,
                },
            )

    @app.post(
        "/api/sites/{site_id}/observations/{observation_id}/adopt-location",
        dependencies=[Depends(admin_guard)],
    )
    def adopt_observation_location(
        site_id: int, observation_id: int, request: ExpectedRevisionRequest | None = None
    ) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot adopt an observation coordinate")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot adopt an observation coordinate")
            expected_revision = (request.expected_revision if request else None) or row["revision"]
            if expected_revision != row["revision"]:
                raise HTTPException(409, "site revision is stale; reload before editing")
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
            location_review_required = int(
                bool(row["location_review_required"])
                or row["status"] in {"trusted", "field-verified"}
                and (
                    observation["latitude"] != row["latitude"]
                    or observation["longitude"] != row["longitude"]
                )
            )
            updated_result = connection.execute(
                """
                UPDATE sites
                SET latitude = ?, longitude = ?, precision = 'approximate', uncertainty_m = ?,
                    location_basis = ?, location_review_required = ?, short_rationale = ?, updated_at = ?,
                    revision = revision + 1
                WHERE id = ? AND revision = ?
                """,
                (
                    observation["latitude"],
                    observation["longitude"],
                    observation["uncertainty_m"],
                    "explicit_coordinate",
                    location_review_required,
                    rationale,
                    timestamp,
                    site_id,
                    expected_revision,
                ),
            )
            if updated_result.rowcount != 1:
                raise HTTPException(409, "site revision is stale; reload before editing")
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
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot be reviewed")
            expected_revision = request.expected_revision or row["revision"]
            if expected_revision != row["revision"]:
                raise HTTPException(409, "site revision is stale; reload before reviewing")
            if request.action == "merge":
                if request.target_site_id is None or request.target_site_id == site_id:
                    raise HTTPException(400, "merge requires a different target site")
                target = connection.execute(
                    "SELECT id, revision FROM sites WHERE id = ? AND id <> ? "
                    "AND merged_into_id IS NULL AND status <> 'rejected'",
                    (request.target_site_id, site_id),
                ).fetchone()
                if target is None:
                    raise HTTPException(404, "merge target not found")
                collision = connection.execute(
                    """
                    SELECT 1 FROM field_observations source
                    JOIN field_observations target
                      ON target.site_id = ? AND target.request_id = source.request_id
                    WHERE source.site_id = ? AND source.request_id IS NOT NULL
                    LIMIT 1
                    """,
                    (request.target_site_id, site_id),
                ).fetchone()
                if collision is not None:
                    raise HTTPException(409, "merge would collide with an existing observation request_id")
                merged = connection.execute(
                    "UPDATE sites SET merged_into_id = ?, status = 'rejected', updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                    (request.target_site_id, now_iso(), site_id, expected_revision),
                )
                if merged.rowcount != 1:
                    raise HTTPException(409, "site revision is stale; reload before reviewing")
                target_updated = connection.execute(
                    "UPDATE sites SET revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?",
                    (now_iso(), request.target_site_id, target["revision"]),
                )
                if target_updated.rowcount != 1:
                    raise HTTPException(409, "merge target revision is stale; reload before reviewing")
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
                connection.execute(
                    "INSERT OR IGNORE INTO site_relations (site_id, related_external_key, relation_kind, created_at) SELECT ?, related_external_key, relation_kind, created_at FROM site_relations WHERE site_id = ?",
                    (request.target_site_id, site_id),
                )
                connection.execute("DELETE FROM site_relations WHERE site_id = ?", (site_id,))
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
                updated_result = connection.execute(
                    "UPDATE sites SET status = ?, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                    (status, now_iso(), site_id, expected_revision),
                )
                if updated_result.rowcount != 1:
                    raise HTTPException(409, "site revision is stale; reload before reviewing")
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'review', ?, ?)",
                (site_id, dump_json(request.model_dump(exclude={"expected_revision"})), now_iso()),
            )
            if request.action == "merge":
                connection.execute(
                    "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'merge_received', ?, ?)",
                    (request.target_site_id, dump_json({"merged_from_site_id": site_id}), now_iso()),
                )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": status, "site": _site_detail(connection, updated)}

    @app.post("/api/routes", dependencies=[Depends(admin_guard)])
    def create_route(request: RouteRequest) -> dict[str, object]:
        if not settings.ors_api_key:
            raise HTTPException(503, "routing is not configured")
        with database.connect() as connection:
            placeholders = ",".join("?" for _ in request.site_ids)
            rows = connection.execute(
                f"SELECT * FROM sites WHERE id IN ({placeholders})",
                request.site_ids,
            ).fetchall()
            sites = {int(row["id"]): row for row in rows}
            if len(sites) != len(request.site_ids):
                raise HTTPException(404, "one or more route sites were not found")
            approach_rows = []
            for site_id in request.site_ids:
                row = sites[site_id]
                if error := _route_site_error(row, site_id):
                    raise HTTPException(409, error)
                approach_rows.append(row)
        coordinates = [(request.start.lon, request.start.lat)] + [
            (row["approach_longitude"], row["approach_latitude"]) for row in approach_rows
        ]
        labels = ["Route start"] + [f"{row['name']} approach" for row in approach_rows]
        if not ORS_SEMAPHORE.acquire(blocking=False):
            raise HTTPException(429, "routing provider is busy", headers={"Retry-After": "1"})
        try:
            route: RouteResult = fetch_openrouteservice(settings.ors_api_key, coordinates)
            warnings = _route_warnings(coordinates, route, labels)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(502, "routing provider error") from error
        finally:
            ORS_SEMAPHORE.release()
        stop_warnings: list[list[str]] = [[] for _ in approach_rows]
        if route.waypoint_indices is None:
            stop_warnings = [["Provider waypoint snapping was not returned; stop snapping was not controlled."] for _ in approach_rows]
        else:
            for index, (row, waypoint_index) in enumerate(zip(approach_rows, route.waypoint_indices[1:]), start=1):
                distance = _distance_m(coordinates[index], route.coordinates[waypoint_index])
                if distance > 50:
                    stop_warnings[index - 1].append(
                        f"Provider snapped this stop by {round(distance)} m; verify the approach."
                    )
        stops = [
            {
                "site_id": int(row["id"]),
                "name": row["name"],
                "lat": row["approach_latitude"],
                "lon": row["approach_longitude"],
                "point_role": "approach",
                "approach_reviewed_at": row["approach_reviewed_at"],
                "access_note": row["approach_note"],
                "warnings": stop_warnings[index],
            }
            for index, row in enumerate(approach_rows)
        ]
        named_waypoints = [(request.start.lon, request.start.lat, "Route start")]
        named_waypoints.extend(
            (stop["lon"], stop["lat"], f"{stop['name']} — approach; public. {stop['access_note']}")
            for stop in stops
        )
        gpx = build_gpx(request.name, route.coordinates, named_waypoints)
        timestamp = now_iso()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in request.site_ids)
            current_rows = connection.execute(
                f"SELECT * FROM sites WHERE id IN ({placeholders})", request.site_ids
            ).fetchall()
            current_by_id = {int(row["id"]): row for row in current_rows}
            snapshot_fields = (
                "merged_into_id", "status", "approach_latitude", "approach_longitude",
                "approach_access", "approach_note", "approach_reviewed_at", "location_review_required",
            )
            for snapshot in approach_rows:
                current = current_by_id.get(int(snapshot["id"]))
                if current is None or _route_site_error(current, int(snapshot["id"])) or any(
                    current[field] != snapshot[field] for field in snapshot_fields
                ):
                    raise HTTPException(409, "a route site changed; calculate the route again")
            cursor = connection.execute(
                """
                INSERT INTO route_plans
                    (name, start_json, waypoints_json, stops_json, route_warnings_json, distance_m, duration_s,
                     geometry_json, gpx_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.name,
                    dump_json(request.start.model_dump()),
                    dump_json([{"lat": stop["lat"], "lon": stop["lon"]} for stop in stops]),
                    dump_json(stops),
                    dump_json(warnings),
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
            "stops": stops,
            "waypoints": [{"lat": stop["lat"], "lon": stop["lon"]} for stop in stops],
            "legacy_route": False,
            "warnings": warnings,
            "created_at": timestamp,
        }

    @app.get("/api/routes", dependencies=[Depends(admin_guard)])
    def list_routes(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, start_json, waypoints_json, stops_json, route_warnings_json, distance_m, duration_s, created_at "
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
            if route["legacy_route"]:
                requested_coordinates = [(route["start"]["lon"], route["start"]["lat"])] + [
                    (point["lon"], point["lat"]) for point in route["waypoints"]
                ]
                route["warnings"] = [
                    "A route does not grant permission to enter land or structures.",
                    "Legacy route stops were not reviewed as public approaches.",
                ]
                if _distance_m(requested_coordinates[0], route_coordinates[0]) > 50 or _distance_m(
                    requested_coordinates[-1], route_coordinates[-1]
                ) > 50:
                    route["warnings"].append("The provider snapped a route endpoint; verify the approach on site.")
            else:
                route["warnings"] = list(dict.fromkeys(route["warnings"] or [
                    "A route does not grant permission to enter land or structures.",
                    *(warning for stop in route["stops"] for warning in stop.get("warnings", [])),
                ]))
                placeholders = ",".join("?" for _ in route["stops"])
                current = connection.execute(
                    f"SELECT id, merged_into_id, status, approach_latitude, approach_longitude, approach_access, approach_note, approach_reviewed_at, location_review_required FROM sites WHERE id IN ({placeholders})",
                    [stop["site_id"] for stop in route["stops"]],
                ).fetchall() if route["stops"] else []
                current_by_id = {int(item["id"]): item for item in current}
                route["current_site_changed"] = False
                for stop in route["stops"]:
                    current = current_by_id.get(int(stop["site_id"]))
                    if current is None or current["merged_into_id"] is not None or current["status"] == "rejected" or current["location_review_required"] or any(
                        current[current_key] != stop[stop_key]
                        for current_key, stop_key in (
                            ("approach_latitude", "lat"), ("approach_longitude", "lon"),
                            ("approach_note", "access_note"), ("approach_reviewed_at", "approach_reviewed_at"),
                        )
                    ) or current["approach_access"] != "public":
                        route["current_site_changed"] = True
                        break
                if route["current_site_changed"]:
                    route["warnings"].append("A saved route stop's reviewed approach has changed; calculate the route again.")
            return route

    return app


app = create_app()
