from __future__ import annotations

from functools import partial
from contextlib import asynccontextmanager
from datetime import date, datetime
from html import escape as escape_html
import hashlib
import hmac
import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from urllib.error import HTTPError, URLError

from app.diagnostics import emit_operation, operational_logger
from app.config import Settings
from app.contracts import API_CONTRACT_VERSION, SiteSummaryResponse, SiteDetailResponse, ImportReceiptResponse
from app.json_input import decode_json_strict
from app.enrichment import ResearchSite, ResearchSource, ResearchClaim, load_site_documents, load_research_site, research_site_payload, load_site_enrichment
from app.db import (
    CURRENT_SCHEMA_VERSION,
    REQUIRED_SCHEMA,
    Database,
    OwnedConnection,
    canonical_payload_hash,
    dump_json,
    decode_stored_json,
    load_json,
    now_iso,
    observation_from_row,
    required_schema_errors,
    site_from_row,
)
from app.imports import (
    ImportPackage,
    import_schema as versioned_import_schema,
    ImportRecord,
    safe_validation_errors,
    validate_import_package,
    validate_location,
    validate_reference_url,
)
from app.routes import RouteResult, build_gpx, fetch_openrouteservice, validate_xml_text


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
SITE_DOCUMENTS = load_site_documents()
SITE_ENRICHMENT = {key: research_site_payload(value) for key, value in SITE_DOCUMENTS.items()}


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
        return decode_json_strict(bytes(body), max_bytes=MAX_IMPORT_BODY_BYTES)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(422, "request body must be valid JSON") from error


class SitePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

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
    warnings: list[str] | None = Field(default=None, max_length=30)
    short_rationale: str | None = Field(default=None, max_length=2000)
    observed_location_text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_complete_coordinate_pair(self) -> "SitePatch":
        for field in ("name", "site_kind", "condition", "short_rationale", "observed_location_text"):
            value = getattr(self, field)
            if value is not None and not value.strip():
                raise ValueError("mutation text cannot be blank")
        if self.warnings and any(not item.strip() or len(item) > 1000 for item in self.warnings):
            raise ValueError("warnings must be nonblank and at most 1000 characters")
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


class SiteContentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_revision: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=500)
    kind_label: str = Field(min_length=1, max_length=200)
    research_state: Literal["curated", "researched_pending", "identity_review"] = "curated"
    reviewed_at: date
    sources: list[ResearchSource] = Field(min_length=1, max_length=50)
    claims: list[ResearchClaim] = Field(min_length=1, max_length=30)
    retired_claim_ids: list[str] | None = Field(default=None, max_length=100)


from app.observation_workflows import OptionalObservationContext, effective_observation, install_observation_routes


class FieldObservation(OptionalObservationContext):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

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


class OfflineSyncRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_site_revision:int=Field(gt=0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

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
    expected_revision: int = Field(gt=0)
    target_expected_revision: int | None = Field(default=None, gt=0)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    merge_preview_hash: str | None = Field(default=None, min_length=64, max_length=64)

    evidence_ids: list[int] = Field(default_factory=list, max_length=50)
    observation_ids: list[int] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_merge_snapshot(self) -> "ReviewRequest":
        if self.action in {"merge", "accept", "research", "field_verify", "confirm"}:
            if not self.reason or not self.reason.strip():
                raise ValueError("consequential review requires a reason")
        if self.action == "merge" and (self.target_expected_revision is None or self.merge_preview_hash is None):
            raise ValueError("merge requires both revisions and its reviewed preview hash")
        for ids in (self.evidence_ids, self.observation_ids):
            if len(set(ids)) != len(ids) or any(value <= 0 for value in ids):
                raise ValueError("selected references must be positive and unique")
        return self


class MergePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_revision: int = Field(gt=0)
    target_site_id: int = Field(gt=0)
    target_expected_revision: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip(): raise ValueError("merge reason cannot be blank")
        return value


class LocationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(gt=0)

    @field_validator("reason")
    @classmethod
    def require_nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("location review reason cannot be blank")
        return value


class ExpectedRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    expected_revision: int = Field(gt=0)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ApproachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    expected_revision: int = Field(gt=0)
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


class RouteBudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    visit_minutes: list[float | None] = Field(max_length=20)
    declared_budget_minutes: float | None = Field(default=None, ge=0, le=10080)


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(default="Trondheim field route", min_length=1, max_length=200)
    start: RoutePoint
    mode: Literal["one_way", "return_to_start", "explicit_end"] = "one_way"
    end: RoutePoint | None = None
    profile: Literal["foot-hiking", "foot-walking"] = "foot-hiking"
    visit_minutes: list[float | None] | None = Field(default=None, max_length=20)
    declared_budget_minutes: float | None = Field(default=None, ge=0, le=10080)
    site_ids: list[int] = Field(min_length=1, max_length=20)
    request_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("name")
    @classmethod
    def exportable_name(cls, value: str) -> str:
        if not value.strip(): raise ValueError("route name cannot be blank")
        validate_xml_text(value)
        return value

    @model_validator(mode="after")
    def validate_site_ids(self) -> "RouteRequest":
        if any(site_id <= 0 for site_id in self.site_ids):
            raise ValueError("site_ids must be positive")
        if len(set(self.site_ids)) != len(self.site_ids):
            raise ValueError("site_ids must be unique")
        if (self.mode == "explicit_end") != (self.end is not None):
            raise ValueError("explicit_end requires an endpoint; other modes do not")
        if self.visit_minutes is not None and (len(self.visit_minutes) != len(self.site_ids) or any(value is not None and not 0 <= value <= 1440 for value in self.visit_minutes)):
            raise ValueError("visit durations must match the ordered stop list and be 0..1440 minutes")
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


def _route_site_error(row: sqlite3.Row, site_id: int, documents=None, freshness_days=0) -> str | None:
    if "external_key" in row.keys() and _effective_research_site(row, documents)[1] == "unavailable":
        return f"site {site_id} has unavailable stored content"
    if "warnings_json" in row.keys() and decode_stored_json(row["warnings_json"], "strings")[1] not in {"valid", "valid_empty"}:
        return f"site {site_id} has unavailable stored safety information"
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
    if freshness_days:
        from app.curator_history import freshness_state
        if freshness_state(row["approach_reviewed_at"],freshness_days) in {"overdue","unknown"}:
            return f"site {site_id} public approach review is overdue or has an invalid date"
    return None


def _source_rows(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT sources.url, sources.title AS registry_title, sources.source_type AS registry_type,
               items.excerpt, items.published_at, items.accessed_at, items.role,
               items.id AS evidence_id, items.content_kind, items.provenance_status,
               items.source_index, records.payload_json,
               (SELECT group_concat(role) FROM evidence
                WHERE site_id = items.site_id AND source_id = items.source_id) AS source_roles
        FROM evidence_items items
        JOIN sources ON sources.id = items.source_id
        LEFT JOIN import_records records ON records.id = items.import_record_id
        WHERE items.site_id = ?
        UNION ALL
        SELECT sources.url, sources.title, sources.source_type, sources.excerpt,
               sources.published_at, sources.accessed_at, evidence.role,
               NULL, 'unknown', 'legacy_registry', NULL, NULL, evidence.role
        FROM evidence JOIN sources ON sources.id = evidence.source_id
        WHERE evidence.site_id = ? AND NOT EXISTS (
            SELECT 1 FROM evidence_items items
            WHERE items.site_id = evidence.site_id AND items.source_id = evidence.source_id
        )
        ORDER BY evidence_id
        """, (site_id, site_id),
    ).fetchall()
    result = []
    for row in rows:
        source = dict(row)
        source['source_roles'] = sorted(set((source.pop('source_roles') or '').split(',')) - {''})
        raw, index = source.pop('payload_json'), source.pop('source_index')
        citation = None
        if raw is not None and isinstance(index, int):
            try:
                document = decode_json_strict(raw.encode(), max_bytes=MAX_IMPORT_BODY_BYTES)
                candidate = document['sources'][index]
                if isinstance(candidate, dict):
                    citation = candidate
            except (ValueError, KeyError, IndexError, TypeError):
                pass
        source['title'] = citation.get('title') if citation else None
        source['source_type'] = citation.get('source_type') if citation else None
        source['citation_status'] = 'recorded_reading' if citation else 'historical_metadata_unknown'
        for field, default in (("claim_ids", []), ("uncertainty_note", None), ("rights_status", "unknown"), ("rights_note", None)):
            source[field] = citation.get(field, default) if citation else default
        try:
            source['url'] = validate_reference_url(source['url'])
        except ValueError:
            source['url'] = None
            source['url_status'] = 'legacy reference withheld; review required'
        result.append(source)
    return result


def _observation_points(connection: sqlite3.Connection, site_id: int) -> list[dict[str, object]]:
    rows = connection.execute("SELECT * FROM field_observations WHERE site_id=? ORDER BY observed_at DESC,id DESC",(site_id,)).fetchall()
    result=[]
    for row in rows:
        observation=_effective_safe_observation(connection,row)
        if observation.get("data_status") == "unavailable" or observation.get("withdrawn") or observation.get("latitude") is None or observation.get("longitude") is None: continue
        result.append({key:observation[key] for key in ("id","observed_at","outcome","latitude","longitude","point_role","uncertainty_m")})
    return result


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


def _effective_safe_observation(connection, row):
    original = _safe_observation(row)
    try:
        result = effective_observation(connection,row)
        result.update(id=row["id"],request_id=row["request_id"],site_id=row["site_id"],observed_at=row["observed_at"],created_at=row["created_at"],data_status=original["data_status"])
        safe=[]
        for url in result.get("photo_urls",[]):
            try:safe.append(validate_reference_url(url))
            except ValueError:result["photo_urls_status"]="legacy reference withheld; review required"
        result["photo_urls"]=safe
        return result
    except ValueError:
        return {**original,"data_status":"unavailable","withdrawn":False,"observation_revision":None,"original_snapshot":None,"context":None,"amendments":[]}


def _effective_research_site(row: sqlite3.Row, documents=None) -> tuple[ResearchSite | None, str]:
    stored = row["content_json"] if "content_json" in row.keys() else None
    if stored is None:
        selected = SITE_DOCUMENTS if documents is None else documents
        return selected.get(row["external_key"]), "seed" if row["external_key"] in selected else "absent"
    try:
        return load_research_site(stored, row["external_key"]), "override"
    except (ValueError, TypeError):
        return None, "unavailable"


def _effective_enrichment(row: sqlite3.Row, documents=None) -> dict[str, object] | None:
    content, status = _effective_research_site(row, documents)
    return research_site_payload(content) if content is not None else None


def _site_summary(connection: sqlite3.Connection, row: sqlite3.Row, documents=None, freshness_days=0, observation_points=None) -> dict[str, object]:
    site = site_from_row(row)
    site["route_blocking_reason"] = _route_site_error(row, int(row["id"]), documents, freshness_days)
    site["route_eligible"] = site["route_blocking_reason"] is None
    content, content_status = _effective_research_site(row, documents)
    site["content_status"] = content_status
    if content_status == "unavailable": site["data_status"] = "unavailable"
    enrichment = research_site_payload(content) if content else None
    if enrichment:
        site["enrichment"] = {
            key: enrichment[key]
            for key in ("display_name", "kind_label", "research_state", "reviewed_at")
        }
    site["observation_points"] = _observation_points(connection, int(row["id"])) if observation_points is None else observation_points
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


def _site_detail(connection: sqlite3.Connection, row: sqlite3.Row, documents=None, freshness_days=0) -> dict[str, object]:
    site = site_from_row(row)
    site["route_blocking_reason"] = _route_site_error(row, int(row["id"]), documents, freshness_days)
    site["route_eligible"] = site["route_blocking_reason"] is None
    content, content_status = _effective_research_site(row, documents)
    site["content_status"] = content_status
    site["content_document"] = content.model_dump(mode="json", exclude_none=True) if content else None
    site["enrichment"] = research_site_payload(content) if content else None
    if content_status == "unavailable": site["data_status"] = "unavailable"
    site["relations"] = _site_relations(connection, int(row["id"]))
    site["sources"] = _source_rows(connection, int(row["id"]))
    observations = connection.execute(
        """
        SELECT *
        FROM field_observations
        WHERE site_id = ?
        ORDER BY observed_at DESC, id DESC
        """,
        (int(row["id"]),),
    ).fetchall()
    site["field_observations"] = [_effective_safe_observation(connection,item) for item in observations]
    site["observation_points"] = [
        {
            "id": observation["id"],
            "observed_at": observation["observed_at"],
            "outcome": observation["outcome"],
            "latitude": observation["latitude"],
            "longitude": observation["longitude"],
            "point_role": observation["point_role"],
            "uncertainty_m": observation["uncertainty_m"],
        }
        for observation in site["field_observations"]
        if observation.get("data_status") != "unavailable" and not observation.get("withdrawn") and observation["latitude"] is not None and observation["longitude"] is not None
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


def _candidate_effect(existing: sqlite3.Row | None, record: ImportRecord, warnings: list[str]) -> tuple[dict[str, object], list[str]]:
    effective = _record_values(record)
    for field in ('condition', 'short_rationale', 'observed_location_text'):
        if effective[field] is not None:
            effective[field] = effective[field].strip() or None
    effective_warnings = list(warnings)
    if existing is not None:
        old_warnings = load_json(existing['warnings_json'], [])
        if not isinstance(old_warnings, list):
            raise HTTPException(409, 'stored candidate warnings unavailable; review required')
        effective_warnings = [*old_warnings, *effective_warnings]
        if existing['status'] != 'candidate':
            return {field: existing[field] for field in _PREVIEW_FIELDS}, old_warnings
        if record.geometry is None and existing['latitude'] is not None and existing['longitude'] is not None:
            effective_warnings.append('incoming record has no geometry; existing candidate coordinate will be preserved')
            for field in ('latitude', 'longitude', 'precision', 'uncertainty_m', 'location_basis'):
                effective[field] = existing[field]
        if effective['access'] == 'unknown' and existing['access'] != 'unknown':
            effective['access'] = existing['access']
        if effective['confidence'] in (None, 'unknown') and existing['confidence'] not in (None, 'unknown'):
            effective['confidence'] = existing['confidence']
        for field in ('condition', 'short_rationale', 'observed_location_text'):
            if effective[field] is None:
                effective[field] = existing[field]
    return effective, list(dict.fromkeys(effective_warnings))


def _scope_warnings(record, geography):
    if geography is None:return []
    warnings=[]
    if not any(record.external_key.startswith(namespace+':') for namespace in geography.approved_source_namespaces):
        warnings.append('Source namespace is outside the selected workspace policy; explicit source review required. Stable key was retained.')
    if record.geometry is not None:
        lon,lat=record.geometry.longitude,record.geometry.latitude
        low_lon,low_lat,high_lon,high_lat=geography.bounding_box
        if not (low_lon<=lon<=high_lon and low_lat<=lat<=high_lat):
            warnings.append('Coordinate is outside the selected workspace scope; explicit geographic review required. Coordinate was retained.')
    return warnings


def _preview_record(
    connection: sqlite3.Connection, record: ImportRecord, records: list[ImportRecord], geography=None
) -> dict[str, object]:
    existing = _existing_site(connection, record.external_key)
    warnings = _warnings_for_record(connection, record, records) + _scope_warnings(record,geography)
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
    effective, effective_warnings = _candidate_effect(existing, record, warnings)
    if existing is None:
        action = 'new'
        changes = [{'field': field, 'before': None, 'after': effective[field]}
                   for field in _PREVIEW_FIELDS if effective[field] is not None]
    elif existing['status'] == 'candidate':
        action = 'update_candidate'
        for field in _PREVIEW_FIELDS:
            if existing[field] != effective[field]:
                changes.append({'field': field, 'before': existing[field], 'after': effective[field]})
            elif incoming[field] != effective[field]:
                preserved_fields.append(field)
    else:
        action = 'preserve_trusted'
        preserved_fields = list(_PREVIEW_FIELDS)
    evidence = [source.model_dump(mode="json") for source in record.sources]
    return {
        "external_key": record.external_key,
        "name": record.name,
        "action": action,
        "changes": changes,
        "preserved_fields": preserved_fields,
        "evidence": evidence,
        "related_site_keys": list(record.related_site_keys),
        "warnings": warnings,
        "effective_warnings": effective_warnings,
    }


def _preview_hash(preview: dict[str, object]) -> str:
    effect = {
        "payload_hash": preview["payload_hash"],
        "records": preview["records"],
        "summary": preview["summary"],
    }
    if 'workspace_scope' in preview:effect['workspace_scope']=preview['workspace_scope']
    return canonical_payload_hash(effect)


def _preview_package(
    connection: sqlite3.Connection, package: ImportPackage, geography=None
) -> dict[str, object]:
    preview_records: list[dict[str, object]] = []
    counts = {"new": 0, "update_candidate": 0, "preserve_trusted": 0, "warnings": 0}
    records = sorted(package.records, key=lambda item: item.external_key)
    for record in records:
        preview_record = _preview_record(connection, record, records, geography)
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
    if geography is not None:preview['workspace_scope']=geography.as_dict()
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
    database: Database, package: ImportPackage, preview_hash: str | None, provenance: dict | None = None, *, geography=None
) -> dict[str, object]:
    timestamp = now_iso()
    payload_hash = _payload_hash(package)
    created = updated = preserved = evidence_attached = 0
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_batch = connection.execute(
            "SELECT committed_at, payload_hash, provenance_json FROM import_batches WHERE batch_id = ?",
            (package.batch_id,),
        ).fetchone()
        if existing_batch and existing_batch["committed_at"]:
            if existing_batch["payload_hash"] is None:
                raise HTTPException(409, "legacy batch payload cannot be verified")
            if existing_batch["payload_hash"] != payload_hash:
                raise HTTPException(409, "import batch payload differs")
            if existing_batch['provenance_json'] != (dump_json(provenance) if provenance is not None else None):
                raise HTTPException(409, "import batch parent-selection provenance differs")
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
        preview = _preview_package(connection, package, geography)
        if preview_hash != preview["preview_hash"]:
            raise HTTPException(409, "fresh preview required")

        batch_insert = connection.execute(
            """
            INSERT OR IGNORE INTO import_batches
                (batch_id, schema_version, generated_at, created_at, payload_hash, provenance_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (package.batch_id, package.schema_version, package.generated_at.isoformat(), timestamp, payload_hash, dump_json(provenance) if provenance is not None else None),
        )
        if batch_insert.rowcount == 0:
            existing_batch = connection.execute(
                "SELECT committed_at, payload_hash, provenance_json FROM import_batches WHERE batch_id = ?",
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
            record.external_key: _warnings_for_record(connection, record, package.records) + _scope_warnings(record,geography)
            for record in package.records
        }
        for record in package.records:
            warnings = warnings_by_key[record.external_key]
            existing = _existing_site(connection, record.external_key)
            effective, warnings = _candidate_effect(existing, record, warnings)
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
                    (record.external_key, *(effective[field] for field in _PREVIEW_FIELDS[:7]), "candidate", *(effective[field] for field in ("access", "confidence", "condition")), dump_json(warnings), effective["short_rationale"], effective["observed_location_text"], timestamp, timestamp),
                )
                site_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                action = "created"
                created += 1
            elif existing["status"] == "candidate":
                connection.execute(
                    """
                    UPDATE sites SET name = ?, site_kind = ?, latitude = ?, longitude = ?,
                        precision = ?, uncertainty_m = ?, location_basis = ?, status = ?,
                        access = ?, confidence = ?, condition = ?, warnings_json = ?, short_rationale = ?,
                        observed_location_text = ?, updated_at = ?, revision = revision + 1
                    WHERE id = ?
                    """,
                    (
                        *(effective[field] for field in _PREVIEW_FIELDS[:7]),
                        'candidate',
                        *(effective[field] for field in ('access', 'confidence', 'condition')),
                        dump_json(warnings),
                        effective['short_rationale'],
                        effective['observed_location_text'],
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'import_record', ?)
                    """,
                    (
                        site_id,
                        source_id,
                        import_record_id,
                        source_index,
                        source.excerpt,
                        getattr(source, "content_kind", "unknown"),
                        getattr(source, "role", "context"),
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
    route = {key: row[key] for key in ("id", "name", "distance_m", "duration_s", "created_at")}
    states = {}
    for field, kind in (("start", "start"), ("waypoints", "points"), ("geometry", "geometry"), ("stops", "stops"), ("route_warnings", "strings")):
        column = field + "_json"
        value, status = decode_stored_json(row[column] if column in row.keys() else None, kind)
        states[field] = status
        route["warnings" if field == "route_warnings" else field] = value
    route["stored_field_status"] = states
    route["legacy_route"] = states["stops"] == "absent_legacy"
    bad = any(status in {"malformed", "wrong_shape"} for status in states.values()) or any(states[field] == "absent_legacy" for field in ("start", "waypoints", "geometry"))
    route["data_status"] = "unavailable" if bad else "valid"
    route.pop("geometry")
    if bad:
        route.update(start=None, waypoints=[], stops=[], warnings=["Stored route is unavailable; review required."])
        return route
    route["warnings"] = route["warnings"] or []
    details, details_status = decode_stored_json(row["details_json"] if "details_json" in row.keys() else None, "object")
    route["calculation_status"] = "missing_legacy" if details_status == "absent_legacy" else details_status
    if details_status in {"malformed","wrong_shape"}:
        route.update(data_status="unavailable",start=None,waypoints=[],stops=[],warnings=["Stored route calculation details unavailable."])
        return route
    route.update(details or {"mode":"one_way","end":None,"calculation_receipt":None,"legs":None,"budget":None})
    route["stops"] = route["stops"] or []
    if not route["legacy_route"]:
        route["waypoints"] = [{"lat": stop["lat"], "lon": stop["lon"]} for stop in route["stops"]]
    return route


def _merge_effect(connection, source, target, reason, documents=None):
    collision = connection.execute("SELECT 1 FROM field_observations source JOIN field_observations target ON target.site_id=? AND target.request_id=source.request_id WHERE source.site_id=? AND source.request_id IS NOT NULL LIMIT 1", (target["id"], source["id"])).fetchone()
    relations = [dict(row) for row in connection.execute("SELECT id,related_external_key,relation_kind FROM site_relations WHERE site_id=? ORDER BY id", (source["id"],))]
    result = {
        "source": _site_detail(connection, source, documents),
        "survivor": _site_detail(connection, target, documents),
        "reason": reason,
        "transfers": {
            "evidence_ids": [row[0] for row in connection.execute("SELECT id FROM evidence_items WHERE site_id=? ORDER BY id", (source["id"],))],
            "observation_ids": [row[0] for row in connection.execute("SELECT id FROM field_observations WHERE site_id=? ORDER BY id", (source["id"],))],
            "relations": [relation for relation in relations if relation["related_external_key"] != target["external_key"]],
            "self_relations_skipped": [relation for relation in relations if relation["related_external_key"] == target["external_key"]],
        },
        "conflicts": ["observation_request_id_collision"] if collision else [],
        "can_commit": not bool(collision),
        "survivor_fields": "All catalogue/content/approach fields remain those of the survivor; evidence and observations transfer with their original IDs.",
        "lineage": {"original_external_key": source["external_key"], "original_site_id": source["id"], "survivor_external_key": target["external_key"], "survivor_site_id": target["id"]},
    }
    result["preview_hash"] = _payload_digest(result)
    return result


def _safe_event_payload(event_type: str, payload_json: str) -> dict[str, object]:
    payload, status = decode_stored_json(payload_json, "object")
    if status not in {"valid", "valid_empty"}:
        return {"data_status": "unavailable", "stored_field_status": status}
    if event_type == "content_edit":
        return {"before": payload.get("before"), "after": payload.get("after")}
    if event_type == "import":
        return {key: payload[key] for key in ("external_key", "action") if key in payload}
    allowed = {
        "edit": {"before", "after"},
        "review": {"action", "target_site_id", "reason", "evidence_ids", "observation_ids", "merge_preview_hash"},
        "merge_received": {"merged_from_site_id", "merge_receipt"},
        "image_attached": {"receipt", "reason", "observation_id", "claim_id"},
        "image_deleted": {"image_id", "reason", "preview_sha256"},
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
    documents = load_site_documents(settings.enrichment_path) if settings.enrichment_path else SITE_DOCUMENTS
    geography = None
    if settings.pilot_geography_enabled:
        from app.pilots import GeographyDescriptor
        from dataclasses import replace
        descriptors = settings.pilot_geography_descriptors
        if settings.geography_descriptor_path is not None:
            try:
                raw=decode_json_strict(settings.geography_descriptor_path.read_bytes(),max_bytes=32000)
                descriptor=GeographyDescriptor(**{**raw,'bounding_box':tuple(raw['bounding_box']),'approved_source_namespaces':tuple(raw['approved_source_namespaces']),'warnings':tuple(raw['warnings'])})
            except (ValueError,TypeError,KeyError,OSError) as error:raise RuntimeError('workspace geography descriptor is invalid') from error
            descriptors=(descriptor,)
            settings=replace(settings,pilot_geography_descriptors=descriptors)
        if len(descriptors)!=1:raise RuntimeError('select exactly one owner workspace descriptor')
        geography=descriptors[0]
    import_preview=partial(_preview_package,geography=geography)
    import_commit=partial(_commit_package,geography=geography)
    site_summary = partial(_site_summary, documents=documents, freshness_days=settings.freshness_policy_days)
    site_detail = partial(_site_detail, documents=documents, freshness_days=settings.freshness_policy_days)
    effective_content = partial(_effective_research_site, documents=documents)
    effective_enrichment = partial(_effective_enrichment, documents=documents)
    route_site_error = partial(_route_site_error, documents=documents, freshness_days=settings.freshness_policy_days)
    database = Database(settings.db_path)
    @asynccontextmanager
    async def lifespan(app):
        app.state.operations = operational_logger(settings.operations_log_dir)
        try:
            database.initialize()
        except (OSError, RuntimeError, sqlite3.Error):
            emit_operation(app.state.operations, request_id=uuid.uuid4().hex, code="STARTUP_FAILED", elapsed_ms=0)
            raise
        try:
            yield
        finally:
            if settings.operations_log_dir is not None:
                for handler in list(app.state.operations.handlers):
                    handler.close(); app.state.operations.removeHandler(handler)

    static_dir = Path(__file__).parent / "static"
    app = FastAPI(title="Bunkerkartet", version=API_CONTRACT_VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database

    @app.exception_handler(RequestValidationError)
    async def safe_request_validation_error(request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": safe_validation_errors(exc.errors())})

    @app.middleware("http")
    async def set_response_headers(request, call_next):
        started = time.perf_counter()
        correlation = uuid.uuid4().hex
        error_code = None
        if request.method in {"POST", "PATCH", "PUT"} and request.url.path.startswith("/api/"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_IMPORT_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "request body too large"}, headers={"Cache-Control": "no-store", **SECURITY_HEADERS})
            request._body = bytes(body)
            if body:
                try:
                    decode_json_strict(bytes(body), max_bytes=MAX_IMPORT_BODY_BYTES)
                except (UnicodeDecodeError, ValueError):
                    return JSONResponse(status_code=422, content={"detail": "request body must be unambiguous bounded JSON"}, headers={"Cache-Control": "no-store", **SECURITY_HEADERS})
        try:
            response = await call_next(request)
        except sqlite3.Error:
            error_code = "DATABASE_UNAVAILABLE"
            response = JSONResponse(status_code=503, content={"detail": {"code": error_code, "message": "database operation unavailable"}})
        if response.status_code >= 500:
            code = error_code or ("PROVIDER_UNAVAILABLE" if response.status_code == 502 else "OPERATION_UNAVAILABLE")
            emit_operation(app.state.operations, request_id=correlation, code=code, elapsed_ms=(time.perf_counter()-started)*1000)
        response.headers["X-Request-ID"] = correlation
        if request.url.path in {"/", "/static/app.js", "/static/styles.css"} or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth", description="Memory-only owner bearer credential")

    def admin_guard(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if credentials and settings.admin_token and hmac.compare_digest(credentials.credentials,settings.admin_token): return
        if credentials and settings.pilot_readers_enabled:
            from app.pilots import authorize_reader
            try:
                with database.connect() as c: authorize_reader(c,credentials.credentials,None)
            except PermissionError:pass
            else:raise HTTPException(403,"reader credential cannot mutate or administer private research")
        _require_admin(settings, f"Bearer {credentials.credentials}" if credentials else None)

    def read_guard(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials and settings.admin_token and hmac.compare_digest(credentials.credentials,settings.admin_token): return None
        if credentials and settings.pilot_readers_enabled:
            from app.pilots import authorize_reader
            try:
                with database.connect() as c: grant = authorize_reader(c,credentials.credentials,None)
                scope = "history:read" if request.url.path.endswith(("/events","/history")) else "sites:read"
                if request.url.path != "/api/session":grant.require_scope(scope)
                return grant
            except PermissionError:raise HTTPException(403,"reader credential is expired, revoked, or lacks this read scope")
        _require_admin(settings, f"Bearer {credentials.credentials}" if credentials else None)

    @app.get('/api/session')
    def session(grant=Depends(read_guard)):
        return {'role':'reader' if grant else 'owner','scopes':sorted(grant.scopes) if grant else ['owner'],'expires_at':grant.expires_at if grant else None}

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
                with sqlite3.connect(f"{database.path.resolve().as_uri()}?mode=ro", uri=True, factory=OwnedConnection) as connection:
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

    @app.get("/api/config")
    def browser_config() -> dict[str, object]:
        return {"idle_lock_seconds": settings.idle_lock_seconds,
                "hidden_lock_seconds": settings.hidden_lock_seconds,
                "enabled_map_providers": list(settings.map_providers),
                "available_map_providers": ["kartverket", "esri"], "route_profiles": list(settings.route_profiles), "geography": geography.as_dict() if geography else None}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": settings.app_version}

    @app.get("/api/imports/schema", dependencies=[Depends(admin_guard)])
    def import_schema() -> dict[str, object]:
        return versioned_import_schema()

    @app.get("/api/admin/imports", dependencies=[Depends(admin_guard)])
    def list_import_receipts(limit: int = Query(default=20, ge=1, le=100), before: int | None = Query(default=None, gt=0)) -> dict[str, object]:
        with database.connect() as connection:
            rows = connection.execute("SELECT id,batch_id,schema_version,generated_at,committed_at,payload_hash FROM import_batches WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?", (before, before, limit+1)).fetchall()
            return {"items": [dict(row) for row in rows[:limit]], "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None}

    @app.get("/api/admin/imports/{batch_id}", dependencies=[Depends(admin_guard)], response_model=ImportReceiptResponse)
    def get_import_receipt(batch_id: str) -> dict[str, object]:
        with database.connect() as connection:
            batch = connection.execute("SELECT id,batch_id,schema_version,generated_at,committed_at,payload_hash,provenance_json FROM import_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None: raise HTTPException(404, "import batch not found")
            records = connection.execute("SELECT id,external_key,site_id,action,created_at FROM import_records WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
            result = dict(batch); effects = []; readings = 0; links = set()
            result['selection_provenance'],result['selection_provenance_status'] = decode_stored_json(result.pop('provenance_json'), 'object')
            for record in records:
                effect = dict(record)
                evidence = connection.execute("SELECT id,site_id,source_id FROM evidence_items WHERE import_record_id=? ORDER BY id", (record["id"],)).fetchall()
                effect["evidence_ids"] = [item["id"] for item in evidence]
                original = connection.execute("SELECT id,merged_into_id FROM sites WHERE external_key=?", (record["external_key"],)).fetchone()
                effect["original_site_id"] = original["id"] if original else None
                effect["merged_into_id"] = original["merged_into_id"] if original else None
                readings += len(evidence); links.update((record["id"], item["source_id"]) for item in evidence)
                effects.append(effect)
            result.update(records=effects, evidence_items=readings, source_links=len(links), receipt_status="recorded" if result["payload_hash"] else "legacy_hash_unavailable")
            return result

    @app.post("/api/admin/imports/preview", dependencies=[Depends(admin_guard)])
    async def preview_import(request: Request) -> dict[str, object]:
        payload = await _read_import_json(request)

        def work() -> dict[str, object]:
            try:
                package = validate_import_package(payload)
            except ValidationError as error:
                raise HTTPException(422, detail=_validation_detail(error))
            with database.connect() as connection:
                return import_preview(connection, package)

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
            return import_commit(database, package, x_import_preview)

        return await run_in_threadpool(work)

    @app.get("/api/sites", dependencies=[Depends(read_guard)], response_model=list[SiteSummaryResponse])
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
        query = f"SELECT * FROM sites WHERE {' AND '.join(conditions)} ORDER BY name, id"
        with database.connect() as connection:
            from app.catalogue_quality import batch_observation_points
            rows = connection.execute(query, values).fetchall()
            points = batch_observation_points(connection, [int(row["id"]) for row in rows])
            result = []
            for row in rows:
                if site_kind and site_kind.strip().casefold() not in row["site_kind"].casefold(): continue
                if q and q.strip():
                    enrichment = effective_enrichment(row) or {}
                    texts = [str(row[field] or "") for field in ("name", "site_kind", "short_rationale", "observed_location_text", "condition")]
                    texts.extend(str(enrichment.get(field, "")) for field in ("display_name", "kind_label"))
                    texts.extend(claim["text"] for claim in enrichment.get("claims", []))
                    for reading in _source_rows(connection, int(row["id"])):
                        texts.extend(str(reading.get(field) or "") for field in ("title", "registry_title", "excerpt"))
                    if not any(q.strip().casefold() in value.casefold() for value in texts): continue
                result.append(site_summary(connection, row, observation_points=points[int(row["id"])]))
            return result

    @app.get("/api/sites.geojson", dependencies=[Depends(read_guard)])
    def export_sites_geojson() -> JSONResponse:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sites WHERE merged_into_id IS NULL AND status <> 'rejected' ORDER BY name, id"
            ).fetchall()
            features: list[dict[str, object]] = []
            for row in rows:
                site = site_summary(connection, row)
                site_id = int(site["id"])
                geometry = None
                if site["latitude"] is not None and site["longitude"] is not None:
                    geometry = {
                        "type": "Point",
                        "coordinates": [-180.0 if site["longitude"] == 180 else site["longitude"], site["latitude"]],
                    }
                properties = {
                    key: value for key, value in site.items() if key != "observation_points"
                }
                properties["feature_type"] = "site"
                properties["point_role"] = "feature"
                features.append(
                    {"type": "Feature", "id": site_id, "geometry": geometry, "properties": properties}
                )
                if site["approach_latitude"] is not None and site["approach_longitude"] is not None:
                    features.append({"type": "Feature", "id": f"approach-{site_id}", "geometry": {"type": "Point", "coordinates": [-180.0 if site["approach_longitude"] == 180 else site["approach_longitude"], site["approach_latitude"]]}, "properties": {"feature_type": "approach", "site_id": site_id, "point_role": "approach", "uncertainty_m": None, "access": site["approach_access"], "reviewed_at": site["approach_reviewed_at"], "route_eligible": site["route_eligible"], "route_blocking_reason": site["route_blocking_reason"]}})
                for observation in site["observation_points"]:
                    features.append(
                        {
                            "type": "Feature",
                            "id": f"observation-{observation['id']}",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [-180.0 if observation["longitude"] == 180 else observation["longitude"], observation["latitude"]],
                            },
                            "properties": {
                                "feature_type": "field_observation",
                                "site_id": site_id,
                                "observed_at": observation["observed_at"],
                                "outcome": observation["outcome"],
                                "point_role": observation["point_role"],
                                "uncertainty_m": observation["uncertainty_m"],
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
            return [site_detail(connection, row) for row in rows]

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
            return [site_detail(connection, row) for row in rows]

    from app.pilots import install_pilot_routes
    install_pilot_routes(app,database,admin_guard,site_detail,settings)

    def gis_commit(db, edits, reason, preview_hash):
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=[]
            for edit in edits:
                row=c.execute('SELECT * FROM sites WHERE id=? AND external_key=?',(edit['site_id'],edit['external_key'])).fetchone()
                if row is None or row['revision'] != edit['expected_revision'] or row['merged_into_id'] is not None:raise HTTPException(409,{'code':'REVISION_MISMATCH','message':'GIS selection changed'})
                if site_from_row(row)['data_status'] != 'valid' or effective_content(row)[1]=='unavailable':raise HTTPException(409,'stored data unavailable')
                if edit['new'] is None:raise HTTPException(422,'GIS coordinate removal requires native review')
                lon,lat=edit['new']
                if row['uncertainty_m'] is None:raise HTTPException(409,'new GIS point requires an explicit native radius review first')
                try:validate_location(lat,lon,row['precision'] if row['precision']!='unknown' else 'approximate',row['uncertainty_m'],row['location_basis'])
                except ValueError as error:raise HTTPException(422,'invalid GIS coordinate or accuracy') from error
                rows.append((row,lat,lon))
            for row,lat,lon in rows:
                c.execute('UPDATE sites SET latitude=?,longitude=?,location_review_required=1,revision=revision+1,updated_at=? WHERE id=?',(lat,lon,now_iso(),row['id']))
                c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'gis_coordinate_edit',?,?)",(row['id'],dump_json({'before':{'latitude':row['latitude'],'longitude':row['longitude']},'after':{'latitude':lat,'longitude':lon},'reason':reason,'preview_hash':preview_hash}),now_iso()))
            return {'updated':len(rows),'preview_hash':preview_hash,'location_review_required':True}
    def private_questions(c,site_ids,question_ids):
        result=[]
        for id in question_ids:
            row=c.execute('SELECT q.* FROM research_questions q JOIN sites s ON s.external_key=q.external_key WHERE q.id=? AND s.id IN ('+','.join('?' for _ in site_ids)+')',(id,*site_ids)).fetchone()
            if row is None:raise HTTPException(409,'private question selection does not belong to dossier sites')
            payload,status=decode_stored_json(row['payload_json'],'object')
            if status not in {'valid','valid_empty'}:raise HTTPException(409,'private question unavailable')
            result.append({**dict(row),**payload,'id':str(row['id']),'prompt':payload.get('wording'),'revision':row['revision']})
        return result
    from app.exchange_workflows import install_exchange_routes
    install_exchange_routes(app,database,admin_guard,site_detail,import_preview,import_commit,load_private_questions=private_questions,commit_location_edits=gis_commit)

    from app.media_api import install_media_routes
    install_media_routes(app,database,admin_guard,settings,effective_content)

    def observation_support(c, site_id, observation_id):
        for event in c.execute("SELECT event_type,payload_json FROM site_events WHERE site_id=? AND event_type IN ('coordinate_adopted','review')",(site_id,)):
            payload,status = decode_stored_json(event['payload_json'],'object')
            if payload and (payload.get('observation_id') == observation_id or observation_id in payload.get('observation_ids',[])): return True
        return False
    def visit_route_snapshot(c, route_id):
        if c.execute("SELECT 1 FROM route_retention_receipts WHERE route_id=?",(route_id,)).fetchone():return None
        row = c.execute("SELECT * FROM route_plans WHERE id=?",(route_id,)).fetchone()
        if row is None:return None
        route = _route_summary(row)
        return route if route['data_status'] == 'valid' and not route['legacy_route'] else None
    def manual_visit_approaches(c, site_ids):
        result=[]
        for id in site_ids:
            row = c.execute('SELECT * FROM sites WHERE id=?',(id,)).fetchone()
            if row is None or route_site_error(row,id):return None
            result.append({'site_id':id,'lat':row['approach_latitude'],'lon':row['approach_longitude'],'point_role':'approach','site_revision':row['revision'],'approach_reviewed_at':row['approach_reviewed_at']})
        return result
    def visit_question_membership(c,question_id,site_id):
        return c.execute('SELECT 1 FROM research_questions q JOIN sites s ON s.external_key=q.external_key WHERE q.id=? AND s.id=?',(question_id,site_id)).fetchone() is not None
    install_observation_routes(app,database,admin_guard,route_snapshot=visit_route_snapshot,manual_approaches=manual_visit_approaches,question_membership=visit_question_membership,observation_support=observation_support)

    from app.seed_reconciliation import install_reconciliation_routes
    install_reconciliation_routes(app,database,admin_guard,documents,effective_content,site_detail)

    from app.curator_history import install_history_routes
    install_history_routes(app,database,admin_guard,site_detail,settings.freshness_policy_days,read_guard=read_guard)

    @app.get("/api/sites/{site_id}/events", dependencies=[Depends(read_guard)])
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

    @app.get("/api/sites/{site_id}", dependencies=[Depends(read_guard)], response_model=SiteDetailResponse)
    def get_site(site_id: int) -> dict[str, object]:
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            return site_detail(connection, row)

    @app.patch("/api/sites/{site_id}/content", dependencies=[Depends(admin_guard)])
    def edit_site_content(site_id: int, patch: SiteContentPatch) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
            if row is None: raise HTTPException(404, "site not found")
            if row["merged_into_id"] is not None: raise HTTPException(409, "merged site cannot be edited")
            if row["revision"] != patch.expected_revision:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
            before, status = effective_content(row)
            if status == "unavailable": raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "stored content requires review"})
            try:
                content = load_research_site({"external_key": row["external_key"], **patch.model_dump(mode="json", exclude={"expected_revision"})}, row["external_key"])
            except ValidationError as error: raise HTTPException(422, detail=_validation_detail(error)) from error
            except ValueError as error: raise HTTPException(422, "invalid content document") from error
            removed = {claim.id for claim in before.claims} - {claim.id for claim in content.claims} if before else set()
            if removed - set(content.retired_claim_ids or []):
                raise HTTPException(409, "removed claim IDs require explicit retirement; section moves preserve IDs")
            if before and set(before.retired_claim_ids or []) - set(content.retired_claim_ids or []):
                raise HTTPException(409, "retired claim identities cannot be forgotten")
            after = content.model_dump(mode="json", exclude_none=True)
            base_event = connection.execute("SELECT payload_json FROM site_events WHERE site_id=? AND event_type IN ('content_edit','seed_reconciliation') ORDER BY id DESC LIMIT 1",(site_id,)).fetchone()
            base_payload, _ = decode_stored_json(base_event[0],"object") if base_event else ({},"absent_legacy")
            seed_base = base_payload.get("seed_base") if base_payload else None
            if seed_base is None and row["content_json"] is None and row["external_key"] in documents:
                seed_base = documents[row["external_key"]].model_dump(mode="json", exclude_none=True)
            timestamp = now_iso()
            cursor = connection.execute("UPDATE sites SET content_json=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?", (dump_json(after), timestamp, site_id, patch.expected_revision))
            if cursor.rowcount != 1: raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site changed"})
            connection.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'content_edit',?,?)", (site_id, dump_json({"before": before.model_dump(mode="json", exclude_none=True) if before else None, "after": after, "seed_base": seed_base, "seed_hash": canonical_payload_hash(seed_base) if seed_base else None}), timestamp))
            updated = connection.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
            return site_detail(connection, updated)

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
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            if row["revision"] != expected_revision:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
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
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'edit', ?, ?)",
                (site_id, dump_json({"before": event_before, "after": event_after}), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return site_detail(connection, updated)

    @app.post("/api/sites/{site_id}/approach", dependencies=[Depends(admin_guard)])
    def set_site_approach(site_id: int, request: ApproachRequest) -> dict[str, object]:
        timestamp = now_iso()
        reviewed_at = timestamp if request.access == "public" else None
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot set an approach")
            expected_revision = request.expected_revision
            if expected_revision != row["revision"]:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
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
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'approach_review', ?, ?)",
                (site_id, dump_json({**request.model_dump(exclude={"expected_revision"}), "reviewed_at": reviewed_at}), timestamp),
            )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return site_detail(connection, updated)

    @app.post("/api/sites/{site_id}/location-review", dependencies=[Depends(admin_guard)])
    def review_site_location(site_id: int, request: LocationReviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot review location")
            if row["revision"] != request.expected_revision:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before reviewing location"})
            if not row["location_review_required"]:
                raise HTTPException(409, "site does not require a location review")
            timestamp = now_iso()
            updated = connection.execute(
                "UPDATE sites SET location_review_required = 0, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                (timestamp, site_id, request.expected_revision),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before reviewing location"})
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'location_review', ?, ?)",
                (site_id, dump_json({"reason": request.reason.strip()}), timestamp),
            )
            current = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return {"status": "location_reviewed", "site": site_detail(connection, current)}

    def record_field_observation(site_id: int, observation: FieldObservation, owned_connection=None):
        timestamp = now_iso()
        request_id = observation.request_id or str(uuid.uuid4())
        context_fields = set(OptionalObservationContext.model_fields) - {"point_role"}
        payload = observation.model_dump(mode="json", exclude={"request_id"} | {key for key in context_fields if getattr(observation,key) is None})
        context = {key:payload[key] for key in context_fields if key in payload}
        if observation.captured_at is not None and observation.observed_at > date.today():
            raise HTTPException(422, "a planned visit is not a completed observation")
        payload_hash = _payload_digest(payload)
        from contextlib import nullcontext
        with (database.connect() if owned_connection is None else nullcontext(owned_connection)) as connection:
            if owned_connection is None:connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            existing = connection.execute(
                "SELECT * FROM field_observations WHERE site_id = ? AND request_id = ?",
                (site_id, request_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, "observation request_id already has a different payload")
                return {
                    "observation": _effective_safe_observation(connection,existing),
                    "site": site_detail(connection, row),
                    "idempotent": True,
                }
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot receive observations")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot receive observations")
            if observation.linked_question_id is not None and not connection.execute("SELECT 1 FROM research_questions WHERE id=? AND external_key=?",(observation.linked_question_id,row["external_key"])).fetchone():
                raise HTTPException(409,"linked question does not belong to this site")
            cursor = connection.execute(
                """
                INSERT INTO field_observations
                    (site_id, observed_at, outcome, note, latitude, longitude,
                     point_role, uncertainty_m, observed_location_text, access_notes,
                     photo_urls_json, request_id, payload_hash, created_at, context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    dump_json(context),
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
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before recording observation"})
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            return JSONResponse(
                status_code=201,
                content={
                    "observation": _effective_safe_observation(connection,observation_row),
                    "site": site_detail(connection, updated),
                    "idempotent": False,
                },
            )

    @app.post("/api/sites/{site_id}/observations", dependencies=[Depends(admin_guard)])
    def add_field_observation(site_id:int,observation:FieldObservation):
        return record_field_observation(site_id,observation)

    @app.post('/api/pilots/offline/packs/{pack_id}/outbox/{original_request_id}/sync',dependencies=[Depends(admin_guard)])
    def sync_offline_observation(pack_id:str,original_request_id:str,body:OfflineSyncRequest):
        if not settings.pilot_offline_enabled:raise HTTPException(409,'offline notebook pilot is disabled')
        from app.pilots import OfflinePolicy,inspect_offline_pack,PilotError
        with database.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            item=c.execute('SELECT * FROM pilot_offline_outbox WHERE pack_id=? AND original_request_id=?',(pack_id,original_request_id)).fetchone()
            if item is None:raise HTTPException(404,'offline outbox item not found')
            payload,status=decode_stored_json(item['payload_json'],'object')
            if status not in {'valid','valid_empty'} or hashlib.sha256(item['payload_json'].encode()).hexdigest()!=item['payload_sha256']:raise HTTPException(409,'offline payload failed qualification')
            if 'request_id' in payload:raise HTTPException(409,'original offline request identity cannot be replaced')
            try:observation=FieldObservation.model_validate({**payload,'request_id':original_request_id})
            except ValidationError as error:raise HTTPException(422,detail=_validation_detail(error))
            if observation.observed_at>date.today():raise HTTPException(422,'a future plan cannot be synchronized as an observation')
            site=c.execute('SELECT * FROM sites WHERE external_key=?',(item['site_key'],)).fetchone()
            if site is None:raise HTTPException(409,'offline site no longer exists')
            existing=c.execute('SELECT id FROM field_observations WHERE site_id=? AND request_id=?',(site['id'],original_request_id)).fetchone()
            if existing is None:
                try:pack=inspect_offline_pack(c,pack_id,OfflinePolicy(enabled=True))
                except (PilotError,KeyError,ValueError):raise HTTPException(409,'offline pack requires review')
                if pack['stale'] or item['state']!='approved_for_sync' or not item['reviewer_ref'] or item['reviewed_site_revision']!=body.expected_site_revision or site['revision']!=body.expected_site_revision or item['cached_site_revision']!=body.expected_site_revision:raise HTTPException(409,'approve the current site and pack before explicit synchronization')
            result=record_field_observation(int(site['id']),observation,c)
            response=json.loads(result.body) if isinstance(result,JSONResponse) else result
            if not response['idempotent']:
                c.execute("INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(?,'offline_synced',?,?)",(site['id'],dump_json({'original_request_id':original_request_id,'pack_id':pack_id,'observation_id':response['observation']['id'],'reviewer_ref':item['reviewer_ref'],'reviewed_site_revision':item['reviewed_site_revision']}),now_iso()))
            return {**response,'synced':True,'original_request_id':original_request_id}

    @app.post(
        "/api/sites/{site_id}/observations/{observation_id}/adopt-location",
        dependencies=[Depends(admin_guard)],
    )
    def adopt_observation_location(
        site_id: int, observation_id: int, request: ExpectedRevisionRequest
    ) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot adopt an observation coordinate")
            if row["status"] == "rejected":
                raise HTTPException(409, "rejected site cannot adopt an observation coordinate")
            expected_revision = request.expected_revision
            if expected_revision != row["revision"]:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
            observation = connection.execute(
                "SELECT * FROM field_observations WHERE id = ? AND site_id = ?",
                (observation_id, site_id),
            ).fetchone()
            if observation is None:
                raise HTTPException(404, "observation not found")
            observation = _effective_safe_observation(connection,observation)
            if observation.get("withdrawn"):
                raise HTTPException(409, "withdrawn observation cannot support coordinate adoption")
            if observation["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "observation requires stored-data review"})
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
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before editing"})
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
            return {"status": "coordinate_adopted", "site": site_detail(connection, updated)}

    @app.post("/api/sites/{site_id}/merge-preview", dependencies=[Depends(admin_guard)])
    def preview_merge(site_id: int, request: MergePreviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            source = connection.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
            target = connection.execute("SELECT * FROM sites WHERE id=?", (request.target_site_id,)).fetchone()
            if source is None or target is None: raise HTTPException(404, "merge site not found")
            if source["id"] == target["id"] or source["merged_into_id"] is not None or target["merged_into_id"] is not None or target["status"] == "rejected": raise HTTPException(409, "merge requires distinct active identities")
            if source["revision"] != request.expected_revision or target["revision"] != request.target_expected_revision: raise HTTPException(409, {"code":"REVISION_MISMATCH", "message":"merge snapshot changed"})
            if site_from_row(source)["data_status"] != "valid" or site_from_row(target)["data_status"] != "valid" or effective_content(source)[1] == "unavailable" or effective_content(target)[1] == "unavailable": raise HTTPException(409, "merge stored data unavailable")
            return _merge_effect(connection, source, target, request.reason, documents)

    @app.post("/api/sites/{site_id}/review", dependencies=[Depends(admin_guard)])
    def review_site(site_id: int, request: ReviewRequest) -> dict[str, object]:
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "site not found")
            if site_from_row(row)["data_status"] != "valid":
                raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "site requires stored-data review"})
            if row["merged_into_id"] is not None:
                raise HTTPException(409, "merged site cannot be reviewed")
            expected_revision = request.expected_revision
            if expected_revision != row["revision"]:
                raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before reviewing"})
            if request.action in {"accept", "research", "field_verify", "confirm"}:
                owned = {item[0] for item in connection.execute("SELECT id FROM evidence_items WHERE site_id=?", (site_id,))}
                if not set(request.evidence_ids) <= owned:
                    raise HTTPException(409, "selected evidence does not belong to this site")
                found = set()
                for item in connection.execute("SELECT * FROM field_observations WHERE site_id=?",(site_id,)):
                    assessment = _effective_safe_observation(connection,item)
                    if assessment["data_status"] == "valid" and not assessment.get("withdrawn") and assessment.get("outcome") == "found": found.add(item["id"])
                if not set(request.observation_ids) <= found:
                    raise HTTPException(409, "selected observation is not a found assessment for this site")
                if request.action in {"accept", "research"} and not request.evidence_ids:
                    raise HTTPException(409, "select dated evidence for the identity review")
                if request.action in {"field_verify", "confirm"} and not request.observation_ids:
                    raise HTTPException(409, "select a found field observation; viewpoint verification does not establish feature location or permission")
                if request.action == "confirm":
                    unresolved = connection.execute("SELECT 1 FROM research_questions WHERE external_key=? AND kind IN ('identity','location') AND state<>'resolved' LIMIT 1", (row["external_key"],)).fetchone()
                    if unresolved or row["location_review_required"]:
                        raise HTTPException(409, "resolve identity/location questions and location review before confirmation")
            if request.action == "merge":
                if request.target_site_id is None or request.target_site_id == site_id:
                    raise HTTPException(400, "merge requires a different target site")
                target = connection.execute(
                    "SELECT * FROM sites WHERE id = ? AND id <> ? "
                    "AND merged_into_id IS NULL AND status <> 'rejected'",
                    (request.target_site_id, site_id),
                ).fetchone()
                if target is None:
                    raise HTTPException(404, "merge target not found")
                if site_from_row(target)["data_status"] != "valid":
                    raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": "target requires stored-data review"})
                if request.target_expected_revision != target["revision"]:
                    raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "merge target revision is stale; reload before reviewing"})
                merge_receipt = _merge_effect(connection, row, target, request.reason, documents)
                if request.merge_preview_hash is not None and request.merge_preview_hash != merge_receipt["preview_hash"]:
                    raise HTTPException(409, {"code": "MERGE_PREVIEW_MISMATCH", "message": "merge effect changed; review a fresh preview"})
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
                    raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before reviewing"})
                target_updated = connection.execute(
                    "UPDATE sites SET revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?",
                    (now_iso(), request.target_site_id, target["revision"]),
                )
                if target_updated.rowcount != 1:
                    raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "merge target revision is stale; reload before reviewing"})
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
                    "INSERT OR IGNORE INTO site_relations (site_id, related_external_key, relation_kind, created_at) SELECT ?, related_external_key, relation_kind, created_at FROM site_relations WHERE site_id = ? AND related_external_key <> ?",
                    (request.target_site_id, site_id, target["external_key"]),
                )
                connection.execute("DELETE FROM site_relations WHERE site_id = ?", (site_id,))
                connection.execute("DELETE FROM evidence WHERE site_id = ?", (site_id,))
                connection.execute("UPDATE observation_amendments SET site_id=? WHERE site_id=?",(request.target_site_id,site_id))
                connection.execute("UPDATE visit_observations SET site_id=? WHERE site_id=?",(request.target_site_id,site_id))
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
                    raise HTTPException(409, {"code": "REVISION_MISMATCH", "message": "site revision is stale; reload before reviewing"})
            connection.execute(
                "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'review', ?, ?)",
                (site_id, dump_json(request.model_dump(exclude={"expected_revision"})), now_iso()),
            )
            if request.action == "merge":
                connection.execute(
                    "INSERT INTO site_events (site_id, event_type, payload_json, created_at) VALUES (?, 'merge_received', ?, ?)",
                    (request.target_site_id, dump_json({"merged_from_site_id": site_id, "merge_receipt": merge_receipt}), now_iso()),
                )
            updated = connection.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
            result = {"status": status, "site": site_detail(connection, updated)}
            if request.action == "merge": result["merge_receipt"] = merge_receipt
            return result

    @app.post("/api/routes", dependencies=[Depends(admin_guard)])
    def create_route(request: RouteRequest) -> dict[str, object]:
        if geography is not None and 'name' not in request.model_fields_set:
            request=RouteRequest.model_validate({**request.model_dump(), 'name':geography.display_name[:170]+' field route'})
        if not request.request_id:
            return calculate_route(request)
        payload_hash = _payload_digest(request.model_dump(exclude={"request_id"}))
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM route_requests WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_CONFLICT", "message": "request ID has different route inputs"})
                if existing["route_id"] is not None:
                    return get_route(existing["route_id"])
                if existing["expires_at"] > time.time():
                    raise HTTPException(409, {"code": "ROUTE_PENDING", "message": "route calculation is pending"}, headers={"Retry-After": "1"})
                connection.execute("DELETE FROM route_requests WHERE id=?", (existing["id"],))
            # Cap pending leases rather than accumulating abandoned calculations.
            connection.execute("DELETE FROM route_requests WHERE route_id IS NULL AND expires_at < ?", (time.time(),))
            pending = connection.execute("SELECT COUNT(*) FROM route_requests WHERE route_id IS NULL").fetchone()[0]
            if pending >= 20:
                raise HTTPException(429, "route calculations are busy", headers={"Retry-After": "1"})
            reservation = connection.execute("INSERT INTO route_requests(request_id,payload_hash,expires_at) VALUES(?,?,?)", (request.request_id, payload_hash, time.time()+60)).lastrowid
        try:
            result = calculate_route(request, reservation)
            with database.connect() as connection:
                connection.execute("UPDATE route_requests SET route_id=? WHERE id=?", (result["id"], reservation))
            result["request_id"] = request.request_id
            return result
        except BaseException:
            with database.connect() as connection:
                connection.execute("DELETE FROM route_requests WHERE id=? AND route_id IS NULL", (reservation,))
            raise

    def calculate_route(request: RouteRequest, reservation_id: int | None = None) -> dict[str, object]:
        if request.profile not in settings.route_profiles:
            raise HTTPException(409, "route profile has not been enabled by the owner")
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
                if error := route_site_error(row, site_id):
                    raise HTTPException(409, error)
                approach_rows.append(row)
        from app.route_planning import routing_inputs, build_calculation_receipt, assess_route_budget
        from dataclasses import asdict
        inputs = routing_inputs(request.start.model_dump(), [{"lon":row["approach_longitude"],"lat":row["approach_latitude"],"label":f"{row['name']} approach"} for row in approach_rows],request.mode,request.end.model_dump() if request.end else None)
        coordinates = list(inputs.coordinates)
        labels = list(inputs.labels)
        if not ORS_SEMAPHORE.acquire(blocking=False):
            raise HTTPException(429, "routing provider is busy", headers={"Retry-After": "1"})
        try:
            route: RouteResult = fetch_openrouteservice(settings.ors_api_key, coordinates) if request.profile == "foot-hiking" else fetch_openrouteservice(settings.ors_api_key, coordinates, profile=request.profile)
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
        if request.mode != "one_way":
            named_waypoints.append((*coordinates[-1],labels[-1]))
        receipt = build_calculation_receipt(inputs,profile=request.profile,engine_metadata=route.engine_metadata).to_dict()
        budget = asdict(assess_route_budget(route.leg_summaries, request.visit_minutes or [None]*len(stops), declared_budget_minutes=request.declared_budget_minutes))
        details = {"mode":request.mode,"end":request.end.model_dump() if request.end else None,"profile":request.profile,"calculation_receipt":receipt,"legs":[asdict(leg) for leg in route.leg_summaries] if route.leg_summaries is not None else None,"visit_minutes":request.visit_minutes or [None]*len(stops),"declared_budget_minutes":request.declared_budget_minutes,"budget":budget}
        try:
            gpx = build_gpx(request.name, route.coordinates, named_waypoints)
        except ValueError as error:
            raise HTTPException(422, {"code": "GPX_EXPORT_UNAVAILABLE", "message": "route labels or coordinates cannot be exported as GPX"}) from error
        timestamp = now_iso()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if reservation_id is not None:
                lease = connection.execute("SELECT id FROM route_requests WHERE id=? AND request_id=? AND route_id IS NULL AND expires_at>?", (reservation_id, request.request_id, time.time())).fetchone()
                if lease is None:
                    raise HTTPException(409, {"code": "ROUTE_LEASE_EXPIRED", "message": "route calculation lease expired; reconcile before retry"})
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
                if current is None or route_site_error(current, int(snapshot["id"])) or any(
                    current[field] != snapshot[field] for field in snapshot_fields
                ):
                    raise HTTPException(409, "a route site changed; calculate the route again")
            cursor = connection.execute(
                """
                INSERT INTO route_plans
                    (name, start_json, waypoints_json, stops_json, route_warnings_json, distance_m, duration_s,
                     geometry_json, gpx_text, created_at, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    dump_json(details),
                ),
            )
            route_id = cursor.lastrowid
            if reservation_id is not None:
                connection.execute("UPDATE route_requests SET route_id=? WHERE id=?", (route_id, reservation_id))
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
            **details,
            "warnings": warnings,
            "created_at": timestamp,
        }

    @app.get("/api/routes", dependencies=[Depends(admin_guard)])
    def list_routes(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, start_json, waypoints_json, geometry_json, stops_json, route_warnings_json, distance_m, duration_s, created_at, details_json "
                "FROM route_plans WHERE NOT EXISTS(SELECT 1 FROM route_retention_receipts erased WHERE erased.route_id=route_plans.id) ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_route_summary(row) for row in rows]

    @app.post("/api/routes/{route_id}/budget", dependencies=[Depends(admin_guard)])
    def preview_budget(route_id: int, body: RouteBudgetRequest):
        from app.route_planning import assess_route_budget, ProviderLeg
        from dataclasses import asdict
        with database.connect() as c:
            if c.execute("SELECT 1 FROM route_retention_receipts WHERE route_id=?",(route_id,)).fetchone():raise HTTPException(410,"private route content was erased by its owner")
            row = c.execute("SELECT * FROM route_plans WHERE id=?", (route_id,)).fetchone()
            if row is None: raise HTTPException(404, "route not found")
            route = _route_summary(row)
            if route["data_status"] != "valid" or len(body.visit_minutes) != len(route["stops"]): raise HTTPException(409, "budget must match available saved stops")
            try:
                legs = tuple(ProviderLeg(**leg) for leg in route["legs"]) if route.get("legs") is not None else None
                budget = asdict(assess_route_budget(legs, body.visit_minutes, declared_budget_minutes=body.declared_budget_minutes))
            except (TypeError, ValueError): raise HTTPException(422, "invalid itinerary budget")
            return {"route_id":route_id,"calculation_receipt":route.get("calculation_receipt"),"budget":budget,"stored_route_unchanged":True}

    @app.get("/api/routes/{route_id}", dependencies=[Depends(admin_guard)])
    def get_route(route_id: int) -> dict[str, object]:
        with database.connect() as connection:
            if connection.execute("SELECT 1 FROM route_retention_receipts WHERE route_id=?",(route_id,)).fetchone():
                raise HTTPException(410,"private route content was erased by its owner")
            row = connection.execute("SELECT * FROM route_plans WHERE id = ?", (route_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "route not found")
            route = _route_summary(row)
            if route["data_status"] != "valid":
                return route
            route_coordinates = decode_stored_json(row["geometry_json"], "geometry")[0]
            route["geometry"] = {"type": "LineString", "coordinates": route_coordinates}
            from app.routes import qualify_stored_gpx
            if qualify_stored_gpx(row["gpx_text"]):
                route["gpx"] = row["gpx_text"]
                route["gpx_status"] = "qualified"
            else:
                route["gpx_status"] = "unavailable_legacy"
                route["warnings"].append("Stored GPX cannot be qualified; no download is offered. The original bytes are retained.")
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
                    if current is None or route_site_error(current,int(stop["site_id"])) or any(
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
            if route.get("gpx_status") == "unavailable_legacy":
                warning = "Stored GPX cannot be qualified; no download is offered. The original bytes are retained."
                if warning not in route["warnings"]: route["warnings"].append(warning)
            return route

    from app.route_retention import install_retention_routes
    install_retention_routes(app,database,admin_guard)
    from app.catalogue_quality import install_quality_routes
    install_quality_routes(app,database,admin_guard,site_detail,effective_content,settings.freshness_policy_days)
    from app.research import install_research_routes
    install_research_routes(app, database, admin_guard, effective_content)
    from app.source_navigation import install_source_routes
    install_source_routes(app, database, admin_guard, effective_content, _source_rows)
    return app


app = create_app()
