"""Bounded private dossier, import-subset, and revision-bound GIS workflows.

This module deliberately owns no persistence writes.  The installer accepts the
application's database/read callbacks and delegates both commit operations to
parent-owned transactional callbacks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from html import escape
import json
import math
import xml.etree.ElementTree as ET
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.db import canonical_payload_hash
from app.imports import safe_validation_errors, validate_import_package, validate_reference_url
from app.json_input import decode_json_strict
from app.routes import GPX_NAMESPACE, validate_xml_text
from app.enrichment import CitationLocator


MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_DOSSIER_SITES = 20
MAX_SOURCES_PER_SITE = 20
MAX_CLAIMS_PER_SITE = 30
MAX_DOSSIER_QUESTIONS = 25
MAX_DOSSIER_OBSERVATIONS = 25
MAX_IMPORT_RECORDS = 500
MAX_GIS_SITES = 100
ROUTE_PREFIX = "/api/admin/exchanges"


class StrictWorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PrivateStart(StrictWorkflowModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = Field(default="Private start", min_length=1, max_length=120)

    @field_validator("label")
    @classmethod
    def nonblank_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("private start label cannot be blank")
        return value


class DossierRequest(StrictWorkflowModel):
    site_ids: list[int] = Field(min_length=1, max_length=MAX_DOSSIER_SITES)
    title: str = Field(default="Selected field dossier", min_length=1, max_length=200)
    include_private_start: bool = False
    private_start: PrivateStart | None = None
    include_private_questions: bool = False
    question_ids: list[str] = Field(default_factory=list, max_length=MAX_DOSSIER_QUESTIONS)
    include_private_observations: bool = False
    observation_ids: list[int] = Field(default_factory=list, max_length=MAX_DOSSIER_OBSERVATIONS)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title cannot be blank")
        return value

    @field_validator("site_ids")
    @classmethod
    def unique_site_ids(cls, value: list[int]) -> list[int]:
        if any(site_id <= 0 for site_id in value) or len(set(value)) != len(value):
            raise ValueError("site_ids must be unique positive integers")
        return value

    @field_validator("question_ids")
    @classmethod
    def unique_question_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 200 for item in value) or len(set(value)) != len(value):
            raise ValueError("question_ids must be unique nonblank IDs of at most 200 characters")
        return value

    @field_validator("observation_ids")
    @classmethod
    def unique_observation_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value) or len(set(value)) != len(value):
            raise ValueError("observation_ids must be unique positive integers")
        return value

    @model_validator(mode="after")
    def require_explicit_sensitive_opt_ins(self) -> "DossierRequest":
        if self.include_private_start != (self.private_start is not None):
            raise ValueError("a private start requires its explicit opt-in and the opt-in requires a start")
        if bool(self.question_ids) != self.include_private_questions:
            raise ValueError("private question IDs require their explicit opt-in")
        if bool(self.observation_ids) != self.include_private_observations:
            raise ValueError("private observation IDs require their explicit opt-in")
        return self


class DossierDownloadRequest(DossierRequest):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: str = Field(min_length=20, max_length=40)


class SubsetSelection(StrictWorkflowModel):
    source_package: dict[str, Any]
    selected_keys: list[str] = Field(min_length=1, max_length=MAX_IMPORT_RECORDS)
    new_batch_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("new_batch_id")
    @classmethod
    def nonblank_batch_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("new_batch_id cannot be blank")
        return value

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value

    @field_validator("selected_keys")
    @classmethod
    def unique_selected_keys(cls, value: list[str]) -> list[str]:
        if any(not key.strip() or len(key) > 300 for key in value) or len(set(value)) != len(value):
            raise ValueError("selected keys must be unique nonblank stable keys")
        return value


class SubsetCommitRequest(SubsetSelection):
    package: dict[str, Any]
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GISPackRequest(StrictWorkflowModel):
    site_ids: list[int] = Field(min_length=1, max_length=MAX_GIS_SITES)

    @field_validator("site_ids")
    @classmethod
    def unique_site_ids(cls, value: list[int]) -> list[int]:
        if any(site_id <= 0 for site_id in value) or len(set(value)) != len(value):
            raise ValueError("site_ids must be unique positive integers")
        return value


class GISEditRequest(StrictWorkflowModel):
    source_pack: dict[str, Any]
    edited_features: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_GIS_SITES)
    reason: str = Field(min_length=5, max_length=1000)
    axis_order_confirmation: Literal["longitude_latitude"]

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason cannot be blank")
        return value


class GISEditCommitRequest(GISEditRequest):
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def install_exchange_routes(
    app: FastAPI,
    database: object,
    admin_guard: Callable[..., object],
    site_detail: Callable[..., Mapping[str, object]],
    preview_package: Callable[..., Mapping[str, object]],
    commit_package: Callable[..., Mapping[str, object]],
    *,
    load_private_questions: Callable[..., Sequence[Mapping[str, object]]] | None = None,
    commit_location_edits: Callable[..., Mapping[str, object]] | None = None,
) -> None:
    """Install private exchange endpoints on an existing FastAPI app.

    Required callback signatures:

    * ``site_detail(connection, row) -> mapping`` (read-only catalogue detail)
    * ``preview_package(connection, package) -> mapping`` (native preview with a
      64-character ``preview_hash``)
    * ``commit_package(database, package, native_preview_hash, provenance)``
      (one atomic import operation that persists ``provenance`` with its batch)

    Optional callbacks are used only after an explicit sensitive-field opt-in:

    * ``load_private_questions(connection, site_ids, question_ids) -> rows``
    * ``commit_location_edits(database, edits, reason, preview_hash) -> result``
      (must run the same transaction/revision/location-review guard as native
      location edits; this module never issues site UPDATE statements).
    """
    router = APIRouter()

    async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request must be bounded unambiguous JSON")
                raw.extend(chunk)
            payload = decode_json_strict(bytes(raw), max_bytes=MAX_BODY_BYTES, max_depth=64)
        except (UnicodeDecodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="request must be bounded unambiguous JSON") from error
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=safe_validation_errors(error.errors())) from error

    def json_response(payload: Mapping[str, object]) -> JSONResponse:
        try:
            chunks: list[bytes] = []
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False).iterencode(payload):
                encoded_chunk = chunk.encode("utf-8")
                size += len(encoded_chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise HTTPException(status_code=413, detail="exchange output exceeds its size limit")
                chunks.append(encoded_chunk)
            encoded = b"".join(chunks)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=409, detail="exchange output is not representable") from error
        return JSONResponse(content=json.loads(encoded))

    def open_connection():
        connect = getattr(database, "connect", None)
        if not callable(connect):
            raise HTTPException(status_code=503, detail="database read callback unavailable")
        return connect()

    def require_hash(value: object, description: str) -> str:
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise HTTPException(status_code=503, detail=f"{description} did not provide a SHA-256 receipt")
        return value

    def normalized_instant(value: str | None = None) -> str:
        try:
            instant = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="timestamp must be ISO 8601 with a timezone") from error
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise HTTPException(status_code=422, detail="timestamp must include a timezone")
        return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def bounded_text(value: object, description: str, limit: int, *, optional: bool = False) -> str | None:
        if value is None and optional:
            return None
        if not isinstance(value, str) or len(value) > limit:
            raise HTTPException(status_code=409, detail=f"stored {description} is unavailable or exceeds its limit")
        return value

    def read_geometry(detail: Mapping[str, object], *, latitude_key: str = "latitude", longitude_key: str = "longitude") -> dict[str, object] | None:
        latitude, longitude = detail.get(latitude_key), detail.get(longitude_key)
        if latitude is None and longitude is None:
            return None
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (latitude, longitude)):
            raise HTTPException(status_code=409, detail="stored geometry is incomplete")
        lat, lon = float(latitude), float(longitude)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise HTTPException(status_code=409, detail="stored geometry is out of range")
        return {"type": "Point", "coordinates": [0.0 if lon == 0 else lon, 0.0 if lat == 0 else lat]}

    def safe_source(source: Mapping[str, object]) -> dict[str, object]:
        url = source.get("url")
        url_status = source.get("url_status")
        if isinstance(url, str):
            try:
                url = validate_reference_url(url)
            except ValueError:
                url = None
                url_status = "reference withheld; review required"
        else:
            url = None
        output = {
            "source_id": source.get("source_id") if isinstance(source.get("source_id"), int) and not isinstance(source.get("source_id"), bool) else None,
            "url": url,
            "url_status": bounded_text(url_status, "source URL status", 200, optional=True),
            "title": bounded_text(source.get("title"), "source title", 500, optional=True),
            "source_type": bounded_text(source.get("source_type"), "source type", 100, optional=True),
            "excerpt": bounded_text(source.get("excerpt"), "source excerpt", 1200, optional=True),
            "published_at": bounded_text(source.get("published_at"), "source publication date", 40, optional=True),
            "accessed_at": bounded_text(source.get("accessed_at"), "source access date", 40, optional=True),
            "rights_status": bounded_text(source.get("rights_status"), "source rights status", 100, optional=True),
            "rights_note": bounded_text(source.get("rights_note"), "source rights note", 1000, optional=True),
        }
        return output

    def dossier_snapshot(connection: object, request: DossierRequest, generated_at: str) -> dict[str, object]:
        placeholders = ",".join("?" for _ in request.site_ids)
        rows = connection.execute(
            f"SELECT * FROM sites WHERE id IN ({placeholders})", tuple(request.site_ids)
        ).fetchall()
        row_by_id = {int(row["id"]): row for row in rows}
        if set(row_by_id) != set(request.site_ids):
            raise HTTPException(status_code=404, detail="one or more selected sites no longer exist")

        sites: list[dict[str, object]] = []
        details_by_id: dict[int, Mapping[str, object]] = {}
        geojson_features: list[dict[str, object]] = []
        gpx_waypoints: list[tuple[float, float, str]] = []
        for site_id in request.site_ids:
            try:
                detail = site_detail(connection, row_by_id[site_id])
            except HTTPException:
                raise
            if not isinstance(detail, Mapping):
                raise HTTPException(status_code=503, detail="site detail callback returned an invalid record")
            if detail.get("id") != site_id or detail.get("external_key") != row_by_id[site_id]["external_key"]:
                raise HTTPException(status_code=409, detail="selected site identity changed during dossier creation")
            details_by_id[site_id] = detail
            geometry = read_geometry(detail)
            name = bounded_text(detail.get("name"), "site name", 500) or ""
            site = {
                "id": site_id,
                "external_key": bounded_text(detail.get("external_key"), "site key", 300) or "",
                "name": name,
                "site_kind": bounded_text(detail.get("site_kind"), "site kind", 100, optional=True),
                "status": bounded_text(detail.get("status"), "site status", 40, optional=True),
                "access": bounded_text(detail.get("access"), "site access", 40, optional=True),
                "geometry": geometry,
                "precision": bounded_text(detail.get("precision"), "location precision", 40, optional=True),
                "uncertainty_m": _finite_optional(detail.get("uncertainty_m"), "location uncertainty"),
                "location_basis": bounded_text(detail.get("location_basis"), "location basis", 60, optional=True),
                "warnings": _bounded_string_list(detail.get("warnings", []), "site warnings", 30, 1000),
                "updated_at": bounded_text(detail.get("updated_at"), "site timestamp", 40, optional=True),
                "data_status": bounded_text(detail.get("data_status"), "site currentness", 80, optional=True),
                "content_status": bounded_text(detail.get("content_status"), "content currentness", 80, optional=True),
                "sources": [],
                "claims": [],
                "reviewed_approach": None,
            }
            raw_sources = detail.get("sources", [])
            if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)) or len(raw_sources) > MAX_SOURCES_PER_SITE:
                raise HTTPException(status_code=409, detail="selected site exceeds the dossier source limit")
            site["sources"] = [safe_source(source) for source in raw_sources if isinstance(source, Mapping)]
            if len(site["sources"]) != len(raw_sources):
                raise HTTPException(status_code=409, detail="source snapshot contains an invalid record")
            enrichment = detail.get("enrichment")
            raw_claims = enrichment.get("claims", []) if isinstance(enrichment, Mapping) else []
            if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)) or len(raw_claims) > MAX_CLAIMS_PER_SITE:
                raise HTTPException(status_code=409, detail="selected site exceeds the dossier claim limit")
            claims = []
            for claim in raw_claims:
                if not isinstance(claim, Mapping):
                    raise HTTPException(status_code=409, detail="claim snapshot contains an invalid record")
                raw_claim_sources = claim.get("sources", [])
                if not isinstance(raw_claim_sources, Sequence) or isinstance(raw_claim_sources, (str, bytes)) or len(raw_claim_sources) > MAX_SOURCES_PER_SITE:
                    raise HTTPException(status_code=409, detail="claim source snapshot exceeds its limit")
                claim_sources: list[dict[str, object]] = []
                for claim_source in raw_claim_sources:
                    if not isinstance(claim_source, Mapping):
                        raise HTTPException(status_code=409, detail="claim source snapshot contains an invalid record")
                    claim_url = claim_source.get("url")
                    if isinstance(claim_url, str):
                        try:
                            claim_url = validate_reference_url(claim_url)
                        except ValueError:
                            claim_url = None
                    else:
                        claim_url = None
                    claim_sources.append(
                        {
                            "id": bounded_text(claim_source.get("id"), "claim source ID", 100, optional=True),
                            "title": bounded_text(claim_source.get("title"), "claim source title", 300, optional=True),
                            "url": claim_url,
                            "accessed_at": bounded_text(claim_source.get("accessed_at"), "claim source access date", 40, optional=True),
                        }
                    )
                raw_locators=claim.get('citations') or []
                if not isinstance(raw_locators,list) or len(raw_locators)>20:raise HTTPException(409,'claim archival locators unavailable')
                try:locators=[CitationLocator.model_validate(item).model_dump(mode='json',exclude_none=True) for item in raw_locators]
                except ValueError as error:raise HTTPException(409,'claim archival locators require review') from error
                if any(item['source_id'] not in claim.get('source_ids',[]) for item in locators):raise HTTPException(409,'locator source is not attached to this claim')
                claims.append(
                    {
                        "id": bounded_text(claim.get("id"), "claim ID", 100, optional=True),
                        "text": bounded_text(claim.get("text"), "claim text", 2000, optional=True),
                        "section": bounded_text(claim.get("section"), "claim section", 80, optional=True),
                        "certainty": bounded_text(claim.get("certainty"), "claim certainty", 80, optional=True),
                        "source_ids": _bounded_string_list(claim.get("source_ids", []), "claim source IDs", 30, 100),
                        "sources": claim_sources,
                        "citations": locators,
                    }
                )
            site["claims"] = claims
            if detail.get("approach_reviewed_at") is not None:
                approach_geometry = read_geometry(
                    detail, latitude_key="approach_latitude", longitude_key="approach_longitude"
                )
                site["reviewed_approach"] = {
                    "geometry": approach_geometry,
                    "access": bounded_text(detail.get("approach_access"), "approach access", 40, optional=True),
                    "note": bounded_text(detail.get("approach_note"), "approach note", 1000, optional=True),
                    "reviewed_at": bounded_text(detail.get("approach_reviewed_at"), "approach timestamp", 40),
                }
            sites.append(site)
            properties = {key: value for key, value in site.items() if key not in {"geometry"}}
            properties["feature_type"] = "site"
            properties["point_role"] = "feature"
            geojson_features.append({"type": "Feature", "id": site_id, "geometry": geometry, "properties": properties})
            if geometry is not None:
                lon, lat = geometry["coordinates"]
                gpx_waypoints.append((lon, lat, name))

        private_questions: list[dict[str, object]] = []
        if request.include_private_questions:
            if load_private_questions is None:
                raise HTTPException(status_code=501, detail="private question loader is not installed")
            raw_questions = load_private_questions(connection, tuple(request.site_ids), tuple(request.question_ids))
            if not isinstance(raw_questions, Sequence) or isinstance(raw_questions, (str, bytes)) or len(raw_questions) > MAX_DOSSIER_QUESTIONS:
                raise HTTPException(status_code=503, detail="private question callback exceeded its limit")
            for question in raw_questions:
                if not isinstance(question, Mapping):
                    raise HTTPException(status_code=503, detail="private question callback returned an invalid record")
                question_id = question.get("id")
                site_id = question.get("site_id")
                if not isinstance(question_id, str) or question_id not in request.question_ids or isinstance(site_id, bool) or not isinstance(site_id, int) or site_id not in request.site_ids:
                    raise HTTPException(status_code=409, detail="private question selection changed")
                private_questions.append(
                    {
                        "id": question_id,
                        "site_id": site_id,
                        "prompt": bounded_text(question.get("prompt"), "question prompt", 1000),
                        "state": bounded_text(question.get("state"), "question state", 80),
                        "revision": _positive_int(question.get("revision"), "question revision"),
                        "updated_at": bounded_text(question.get("updated_at"), "question timestamp", 40, optional=True),
                    }
                )
            if (
                len(private_questions) != len(request.question_ids)
                or {question["id"] for question in private_questions} != set(request.question_ids)
            ):
                raise HTTPException(status_code=409, detail="one or more selected private questions changed")
            private_questions.sort(key=lambda item: request.question_ids.index(str(item["id"])))

        private_observations: list[dict[str, object]] = []
        if request.include_private_observations:
            observations_by_id: dict[int, Mapping[str, object]] = {}
            for site_id in request.site_ids:
                values = details_by_id[site_id].get("field_observations", [])
                if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                    raise HTTPException(status_code=409, detail="observation snapshot is invalid")
                for observation in values:
                    if isinstance(observation, Mapping):
                        raw_id = observation.get("id")
                        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                            observations_by_id[raw_id] = observation
            for observation_id in request.observation_ids:
                observation = observations_by_id.get(observation_id)
                if observation is None:
                    raise HTTPException(status_code=409, detail="one or more selected private observations changed")
                site_id = observation.get("site_id")
                if isinstance(site_id, bool) or not isinstance(site_id, int) or site_id not in request.site_ids:
                    raise HTTPException(status_code=409, detail="selected observation is outside the selected sites")
                geometry = read_geometry(observation)
                private_observations.append(
                    {
                        "id": observation_id,
                        "site_id": site_id,
                        "observed_at": bounded_text(observation.get("observed_at"), "observation timestamp", 40, optional=True),
                        "outcome": bounded_text(observation.get("outcome"), "observation outcome", 80),
                        "geometry": geometry,
                        "point_role": bounded_text(observation.get("point_role"), "observation point role", 80, optional=True),
                        "uncertainty_m": _finite_optional(observation.get("uncertainty_m"), "observation uncertainty"),
                        "note": bounded_text(observation.get("note"), "private observation note", 2000, optional=True),
                        "observed_location_text": bounded_text(observation.get("observed_location_text"), "observation location text", 2000, optional=True),
                        "access_notes": bounded_text(observation.get("access_notes"), "private access note", 1000, optional=True),
                    }
                )
                if geometry is not None:
                    lon, lat = geometry["coordinates"]
                    geojson_features.append(
                        {
                            "type": "Feature",
                            "id": f"observation:{observation_id}",
                            "geometry": geometry,
                            "properties": {
                                "feature_type": "field_observation",
                                "point_role": private_observations[-1]["point_role"],
                                "observation_id": observation_id,
                                "site_id": site_id,
                            },
                        }
                    )
                    gpx_waypoints.append((lon, lat, f"Observation {observation_id}"))
        if request.private_start is not None:
            start = request.private_start
            geojson_features.append(
                {
                    "type": "Feature",
                    "id": "private-start",
                    "geometry": {"type": "Point", "coordinates": [start.longitude, start.latitude]},
                    "properties": {"feature_type": "private_start", "point_role": "personal_start", "label": start.label},
                }
            )
            gpx_waypoints.insert(0, (start.longitude, start.latitude, start.label))

        dossier: dict[str, object] = {
            "schema_version": "1.0",
            "title": request.title,
            "generated_at": generated_at,
            "site_ids": list(request.site_ids),
            "sites": sites,
            "private_start": request.private_start.model_dump(mode="json") if request.private_start else None,
            "private_questions": private_questions,
            "private_observations": private_observations,
            "sensitive_inclusions": {
                "private_start": request.include_private_start,
                "private_question_ids": list(request.question_ids),
                "private_observation_ids": list(request.observation_ids),
            },
            "currentness": "Snapshot reflects the selected records at generated_at; records can change after export.",
            "permission_caveat": "Private research dossier, not publication or a complete backup. Source permissions, access conditions, and route conditions must be checked independently.",
        }
        source_hash = canonical_payload_hash(dossier)
        geojson = {
            "type": "FeatureCollection",
            "schema_version": "1.0",
            "source_hash": source_hash,
            "features": geojson_features,
        }
        gpx: str | None = None
        gpx_status = "unavailable: no selected coordinate is known"
        if gpx_waypoints:
            try:
                gpx = _build_waypoint_gpx(request.title, gpx_waypoints)
                gpx_status = "available: waypoints only; no route path is represented"
            except ValueError:
                # GPX is optional when stored names contain characters illegal in XML 1.0.
                gpx_status = "unavailable: selected text cannot be represented in GPX 1.1"
        html_document = _render_dossier_html(dossier)
        return {
            "dossier": dossier,
            "source_hash": source_hash,
            "geojson": geojson,
            "gpx": gpx,
            "gpx_status": gpx_status,
            "html": html_document,
        }

    def render_and_hash_dossier(connection: object, request: DossierRequest, generated_at: str) -> dict[str, object]:
        snapshot = dossier_snapshot(connection, request, generated_at)
        effect_hash = canonical_payload_hash(
            {
                "source_hash": snapshot["source_hash"],
                "dossier": snapshot["dossier"],
                "geojson": snapshot["geojson"],
                "gpx": snapshot["gpx"],
                "gpx_status": snapshot["gpx_status"],
                "html": snapshot["html"],
            }
        )
        return {**snapshot, "preview_hash": effect_hash}

    @router.post(f"{ROUTE_PREFIX}/dossiers/preview", dependencies=[Depends(admin_guard)])
    async def dossier_preview(request: Request) -> JSONResponse:
        body = await read_body(request, DossierRequest)
        assert isinstance(body, DossierRequest)
        generated_at = normalized_instant()
        with open_connection() as connection:
            connection.execute("BEGIN")
            result = render_and_hash_dossier(connection, body, generated_at)
        return json_response(result)

    @router.post(f"{ROUTE_PREFIX}/dossiers/download", dependencies=[Depends(admin_guard)])
    async def dossier_download(request: Request) -> JSONResponse:
        body = await read_body(request, DossierDownloadRequest)
        assert isinstance(body, DossierDownloadRequest)
        selection = DossierRequest.model_validate(body.model_dump(exclude={"preview_hash", "generated_at"}))
        generated_at = normalized_instant(body.generated_at)
        with open_connection() as connection:
            connection.execute("BEGIN")
            result = render_and_hash_dossier(connection, selection, generated_at)
        if result["preview_hash"] != body.preview_hash:
            raise HTTPException(status_code=409, detail="dossier source changed; preview again")
        return json_response(result)

    def derive_subset(
        connection: object,
        source_package_value: Mapping[str, object],
        selected_keys: Sequence[str],
        new_batch_id: str,
        reason: str,
        *,
        generated_at: str | None = None,
    ) -> dict[str, object]:
        try:
            source_package = validate_import_package(dict(source_package_value))
        except (ValidationError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="source package is invalid") from error
        if len(source_package.records) > MAX_IMPORT_RECORDS:
            raise HTTPException(status_code=422, detail="source package exceeds the record limit")
        source_payload = source_package.model_dump(mode="json")
        source_keys = [record.external_key for record in source_package.records]
        if new_batch_id == source_package.batch_id:
            raise HTTPException(status_code=422, detail="a subset requires a new batch ID")
        if len(selected_keys) != len(set(selected_keys)) or not selected_keys:
            raise HTTPException(status_code=422, detail="selected keys must be unique and nonempty")
        if any(key not in source_keys for key in selected_keys):
            raise HTTPException(status_code=422, detail="selection contains an unknown source key")
        parent_hash = canonical_payload_hash(source_payload)
        selected_set = set(selected_keys)
        selected_records = [record for record in source_package.records if record.external_key in selected_set]
        # Preserve the caller's selection order in the derived package.
        by_key = {record.external_key: record for record in selected_records}
        selected_records = [by_key[key] for key in selected_keys]
        subset_payload = {
            **source_payload,
            "batch_id": new_batch_id,
            "generated_at": normalized_instant(generated_at),
            "records": [record.model_dump(mode="json") for record in selected_records],
        }
        try:
            subset_package = validate_import_package(subset_payload)
        except (ValidationError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="derived package is invalid") from error
        unselected_keys = [key for key in source_keys if key not in selected_set]
        unresolved_references = sorted(
            {
                key
                for record in selected_records
                for key in record.related_site_keys
                if key in set(unselected_keys)
            }
        )
        subset_hash = canonical_payload_hash(subset_package.model_dump(mode="json"))
        selection_hash = canonical_payload_hash(
            {
                "parent_package_hash": parent_hash,
                "new_batch_id": new_batch_id,
                "selected_keys": list(selected_keys),
                "reason": reason,
            }
        )
        manifest = {
            "schema_version": "selected-import-subset/1",
            "parent_schema_version": source_package.schema_version,
            "parent_batch_id": source_package.batch_id,
            "parent_package_hash": parent_hash,
            "parent_keys": source_keys,
            "new_batch_id": new_batch_id,
            "selected_keys": list(selected_keys),
            "unselected_keys": unselected_keys,
            "unselected_referenced_keys": unresolved_references,
            "reason": reason,
            "derived_package_hash": subset_hash,
        }
        native_preview = preview_package(connection, subset_package)
        if not isinstance(native_preview, Mapping):
            raise HTTPException(status_code=503, detail="native import preview is unavailable")
        native_hash = require_hash(native_preview.get("preview_hash"), "native import preview")
        workflow_hash = canonical_payload_hash(
            {
                "selection_hash": selection_hash,
                "derived_package_hash": subset_hash,
                "native_preview_hash": native_hash,
            }
        )
        return {
            "package": subset_package,
            "manifest": manifest,
            "selection_hash": selection_hash,
            "preview_hash": workflow_hash,
            "native_preview_hash": native_hash,
            "native_preview": dict(native_preview),
        }

    def subset_response(subset: Mapping[str, object]) -> dict[str, object]:
        package = subset["package"]
        assert hasattr(package, "model_dump")
        return {
            "schema_version": "selected-import-subset/1",
            "package": package.model_dump(mode="json"),
            "manifest": subset["manifest"],
            "selection_hash": subset["selection_hash"],
            "preview_hash": subset["preview_hash"],
            "native_preview": subset["native_preview"],
        }

    @router.post(f"{ROUTE_PREFIX}/import-subsets/preview", dependencies=[Depends(admin_guard)])
    async def subset_preview(request: Request) -> JSONResponse:
        body = await read_body(request, SubsetSelection)
        assert isinstance(body, SubsetSelection)
        with open_connection() as connection:
            connection.execute("BEGIN")
            subset = derive_subset(
                connection,
                body.source_package,
                body.selected_keys,
                body.new_batch_id,
                body.reason,
            )
        return json_response(subset_response(subset))

    @router.post(f"{ROUTE_PREFIX}/import-subsets/commit", dependencies=[Depends(admin_guard)])
    async def subset_commit(request: Request) -> JSONResponse:
        body = await read_body(request, SubsetCommitRequest)
        assert isinstance(body, SubsetCommitRequest)
        try:
            submitted_package = validate_import_package(body.package)
        except (ValidationError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="derived package is invalid") from error
        generated_at = submitted_package.generated_at.isoformat().replace("+00:00", "Z")
        with open_connection() as connection:
            connection.execute("BEGIN")
            subset = derive_subset(
                connection,
                body.source_package,
                body.selected_keys,
                body.new_batch_id,
                body.reason,
                generated_at=generated_at,
            )
        expected_package = subset["package"].model_dump(mode="json")
        submitted_payload = submitted_package.model_dump(mode="json")
        if expected_package != submitted_payload:
            raise HTTPException(status_code=409, detail="submitted subset differs from the selected source records")
        if subset["selection_hash"] != body.selection_hash or subset["preview_hash"] != body.preview_hash:
            raise HTTPException(status_code=409, detail="selection or native preview changed; preview again")
        if not callable(commit_package):
            raise HTTPException(status_code=501, detail="atomic subset commit callback is not installed")
        with open_connection() as connection:
            connection.execute("BEGIN")
            # Recompute immediately before delegating so the parent callback receives a fresh native preview.
            fresh = preview_package(connection, subset["package"])
        if not isinstance(fresh, Mapping):
            raise HTTPException(status_code=503, detail="native import preview is unavailable")
        fresh_native_hash = require_hash(fresh.get("preview_hash"), "native import preview")
        fresh_workflow_hash = canonical_payload_hash(
            {
                "selection_hash": subset["selection_hash"],
                "derived_package_hash": subset["manifest"]["derived_package_hash"],
                "native_preview_hash": fresh_native_hash,
            }
        )
        if fresh_workflow_hash != body.preview_hash:
            raise HTTPException(status_code=409, detail="native preview changed; preview again")
        result = commit_package(database, subset["package"], fresh_native_hash, subset["manifest"])
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=503, detail="atomic subset commit callback returned an invalid receipt")
        return json_response({"result": dict(result), "manifest": subset["manifest"], "package_hash": subset["manifest"]["derived_package_hash"]})

    def current_gis_pack(connection: object, site_ids: Sequence[int]) -> dict[str, object]:
        placeholders = ",".join("?" for _ in site_ids)
        rows = connection.execute(
            f"SELECT id, external_key, latitude, longitude, revision FROM sites WHERE id IN ({placeholders})",
            tuple(site_ids),
        ).fetchall()
        row_by_id = {int(row["id"]): row for row in rows}
        if set(row_by_id) != set(site_ids):
            raise HTTPException(status_code=404, detail="one or more selected GIS sites no longer exist")
        features = []
        for site_id in site_ids:
            row = row_by_id[site_id]
            external_key = row["external_key"]
            if not isinstance(external_key, str) or not external_key or len(external_key) > 300:
                raise HTTPException(status_code=409, detail="site has no stable GIS key")
            revision = _positive_int(row["revision"], "site revision")
            geometry = read_geometry({"latitude": row["latitude"], "longitude": row["longitude"]})
            features.append(
                {
                    "type": "Feature",
                    "id": external_key,
                    "geometry": geometry,
                    "properties": {
                        "external_key": external_key,
                        "site_id": site_id,
                        "source_revision": revision,
                        "feature_type": "site",
                        "point_role": "feature",
                    },
                }
            )
        body = {
            "schema_version": "1.0",
            "type": "FeatureCollection",
            "coordinate_order": "longitude_latitude",
            "site_ids": list(site_ids),
            "features": features,
        }
        return {**body, "source_hash": canonical_payload_hash(body)}

    @router.post(f"{ROUTE_PREFIX}/gis/edit-packs", dependencies=[Depends(admin_guard)])
    async def gis_edit_pack(request: Request) -> JSONResponse:
        body = await read_body(request, GISPackRequest)
        assert isinstance(body, GISPackRequest)
        with open_connection() as connection:
            result = current_gis_pack(connection, body.site_ids)
        return json_response(result)

    def prepare_gis_edit(connection: object, body: GISEditRequest) -> dict[str, object]:
        pack = body.source_pack
        required = {"schema_version", "type", "coordinate_order", "site_ids", "features", "source_hash"}
        if set(pack) != required or pack.get("schema_version") != "1.0" or pack.get("type") != "FeatureCollection" or pack.get("coordinate_order") != "longitude_latitude":
            raise HTTPException(status_code=422, detail="GIS source pack schema or coordinate order is invalid")
        site_ids = pack.get("site_ids")
        if not isinstance(site_ids, list) or not 1 <= len(site_ids) <= MAX_GIS_SITES or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in site_ids) or len(site_ids) != len(set(site_ids)):
            raise HTTPException(status_code=422, detail="GIS source membership is invalid")
        current = current_gis_pack(connection, site_ids)
        if pack != current:
            raise HTTPException(status_code=409, detail="GIS source pack is stale or has been altered; export a fresh pack")
        source_hash = require_hash(pack.get("source_hash"), "GIS source pack")
        changed_features = body.edited_features
        source_features = current["features"]
        if len(changed_features) != len(source_features):
            raise HTTPException(status_code=422, detail="GIS feature membership cannot change")
        edits: list[dict[str, object]] = []
        for original, edited in zip(source_features, changed_features, strict=True):
            if set(edited) != {"type", "id", "geometry", "properties"} or edited.get("type") != "Feature":
                raise HTTPException(status_code=422, detail="GIS feature shape contains non-allowlisted fields")
            if edited.get("id") != original["id"] or edited.get("properties") != original["properties"]:
                raise HTTPException(status_code=422, detail="GIS stable key, revision, or semantic role changed")
            proposed = _parse_geojson_point(edited.get("geometry"))
            old_geometry = original["geometry"]
            old = old_geometry["coordinates"] if isinstance(old_geometry, Mapping) else None
            if old is not None and old[0] != old[1] and proposed == [old[1],old[0]]:
                raise HTTPException(status_code=422,detail="GIS coordinate axes appear swapped; review longitude then latitude explicitly")
            if proposed is None and old is not None:
                raise HTTPException(status_code=422, detail="GIS edits cannot silently remove a stored coordinate")
            if proposed != old:
                properties = original["properties"]
                edits.append(
                    {
                        "site_id": properties["site_id"],
                        "external_key": properties["external_key"],
                        "expected_revision": properties["source_revision"],
                        "old": old,
                        "new": proposed,
                    }
                )
        preview_material = {
            "schema_version": "gis-coordinate-edit/1",
            "source_hash": source_hash,
            "axis_order_confirmation": body.axis_order_confirmation,
            "reason": body.reason,
            "edits": edits,
        }
        return {**preview_material, "preview_hash": canonical_payload_hash(preview_material)}

    @router.post(f"{ROUTE_PREFIX}/gis/edit-previews", dependencies=[Depends(admin_guard)])
    async def gis_edit_preview(request: Request) -> JSONResponse:
        body = await read_body(request, GISEditRequest)
        assert isinstance(body, GISEditRequest)
        with open_connection() as connection:
            result = prepare_gis_edit(connection, body)
        return json_response(result)

    @router.post(f"{ROUTE_PREFIX}/gis/edit-commits", dependencies=[Depends(admin_guard)])
    async def gis_edit_commit(request: Request) -> JSONResponse:
        body = await read_body(request, GISEditCommitRequest)
        assert isinstance(body, GISEditCommitRequest)
        edit_request = GISEditRequest.model_validate(body.model_dump(exclude={"preview_hash"}))
        with open_connection() as connection:
            prepared = prepare_gis_edit(connection, edit_request)
        if prepared["preview_hash"] != body.preview_hash:
            raise HTTPException(status_code=409, detail="GIS selection or revision changed; preview again")
        edits = prepared["edits"]
        if not edits:
            return json_response(
                {
                    "updated": 0,
                    "unchanged": len(edit_request.edited_features),
                    "preview_hash": body.preview_hash,
                    "status": "no_changes",
                }
            )
        if commit_location_edits is None:
            raise HTTPException(status_code=501, detail="native guarded GIS commit callback is not installed")
        result = commit_location_edits(database, edits, body.reason, body.preview_hash)
        if not isinstance(result, Mapping):
            raise HTTPException(status_code=503, detail="native guarded GIS callback returned an invalid receipt")
        return json_response({"result": dict(result), "updated": len(edits), "preview_hash": body.preview_hash})

    app.include_router(router)


def _finite_optional(value: object, description: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=409, detail=f"stored {description} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise HTTPException(status_code=409, detail=f"stored {description} is invalid")
    return number


def _bounded_string_list(value: object, description: str, count_limit: int, text_limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > count_limit:
        raise HTTPException(status_code=409, detail=f"stored {description} exceeds its limit")
    if any(not isinstance(item, str) or len(item) > text_limit for item in value):
        raise HTTPException(status_code=409, detail=f"stored {description} contains invalid text")
    return list(value)


def _positive_int(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=409, detail=f"stored {description} is invalid")
    return value


def _parse_geojson_point(value: object) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"type", "coordinates"} or value.get("type") != "Point":
        raise HTTPException(status_code=422, detail="GIS edit geometry must be a 2D WGS84 Point or null")
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise HTTPException(status_code=422, detail="GIS coordinates must use [longitude, latitude]")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in coordinates):
        raise HTTPException(status_code=422, detail="GIS coordinates must be finite numbers")
    longitude, latitude = (float(item) for item in coordinates)
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise HTTPException(status_code=422, detail="GIS coordinates must be finite numbers")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise HTTPException(status_code=422, detail="GIS coordinates are outside WGS84 longitude/latitude bounds")
    return [0.0 if longitude == 0 else longitude, 0.0 if latitude == 0 else latitude]


def _build_waypoint_gpx(title: str, waypoints: Sequence[tuple[float, float, str]]) -> str:
    """Serialize selected points without implying a routed or walked track."""
    validate_xml_text(title)
    if not waypoints or len(waypoints) > MAX_DOSSIER_SITES + MAX_DOSSIER_OBSERVATIONS + 1:
        raise ValueError("waypoint count is out of bounds")
    ET.register_namespace("", GPX_NAMESPACE)
    root = ET.Element(f"{{{GPX_NAMESPACE}}}gpx", {"version": "1.1", "creator": "Bunkerkartet"})
    metadata = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}metadata")
    ET.SubElement(metadata, f"{{{GPX_NAMESPACE}}}name").text = title
    for longitude, latitude, label in waypoints:
        validate_xml_text(label)
        if (
            any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (longitude, latitude))
            or not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("waypoint coordinate is out of range")
        # GPX 1.1 uses the half-open longitude range [-180, 180); only the
        # interchange representation is normalized here.
        longitude = -180.0 if longitude == 180.0 else longitude
        point = ET.SubElement(
            root,
            f"{{{GPX_NAMESPACE}}}wpt",
            {"lat": f"{latitude:.7f}", "lon": f"{longitude:.7f}"},
        )
        ET.SubElement(point, f"{{{GPX_NAMESPACE}}}name").text = label
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def _render_dossier_html(dossier: Mapping[str, object]) -> str:
    esc = lambda value: escape(str(value), quote=True)
    chunks = [
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>{esc(dossier['title'])}</title><main><h1>{esc(dossier['title'])}</h1>",
        f"<p>Generated: <time>{esc(dossier['generated_at'])}</time></p>",
        f"<p>{esc(dossier['currentness'])}</p><p>{esc(dossier['permission_caveat'])}</p>",
    ]
    for site in dossier["sites"]:
        chunks.extend([f"<article><h2>{esc(site['name'])}</h2>", f"<p>Key: {esc(site['external_key'])}; status: {esc(site['status'])}; access: {esc(site['access'])}</p>"])
        geometry = site["geometry"]
        if geometry is None:
            chunks.append("<p>Geometry: unknown</p>")
        else:
            chunks.append(f"<p>Geometry: {esc(geometry['coordinates'][0])}, {esc(geometry['coordinates'][1])}</p>")
        chunks.append(f"<p>Uncertainty: {esc(site['uncertainty_m'])}; location basis: {esc(site['location_basis'])}</p>")
        if site["reviewed_approach"] is not None:
            approach = site["reviewed_approach"]
            chunks.append(f"<h3>Reviewed approach snapshot</h3><p>{esc(approach['access'])}; reviewed {esc(approach['reviewed_at'])}</p>")
            if approach["geometry"] is not None:
                chunks.append(f"<p>Approach point: {esc(approach['geometry']['coordinates'])}</p>")
            if approach["note"] is not None:
                chunks.append(f"<p>{esc(approach['note'])}</p>")
        for claim in site["claims"]:
            chunks.append(f"<p>Claim: {esc(claim['text'])} ({esc(claim['certainty'])}; {esc(claim['section'])})</p>")
            for locator in claim.get('citations',[]):
                chunks.append('<p>Archive locator: '+esc(json.dumps(locator,ensure_ascii=False))+'</p>')
            for claim_source in claim["sources"]:
                claim_title = esc(claim_source["title"] or claim_source["id"] or "Claim source")
                if claim_source["url"] is not None:
                    chunks.append(f"<p>Claim evidence: <a rel=\"noreferrer noopener\" href=\"{esc(claim_source['url'])}\">{claim_title}</a></p>")
                else:
                    chunks.append(f"<p>Claim evidence: {claim_title}</p>")
        for source in site["sources"]:
            title = esc(source["title"] or "Untitled source")
            if source["url"] is not None:
                chunks.append(f"<p>Source: <a rel=\"noreferrer noopener\" href=\"{esc(source['url'])}\">{title}</a></p>")
            else:
                chunks.append(f"<p>Source: {title} ({esc(source['url_status'])})</p>")
            if source["excerpt"] is not None:
                chunks.append(f"<blockquote>{esc(source['excerpt'])}</blockquote>")
            chunks.append(f"<p>Rights: {esc(source['rights_status'])}; {esc(source['rights_note'])}</p>")
        chunks.append("</article>")
    if dossier["private_start"] is not None:
        start = dossier["private_start"]
        chunks.append(f"<section><h2>Private personal start</h2><p>{esc(start['label'])}: {esc(start['latitude'])}, {esc(start['longitude'])}</p></section>")
    if dossier["private_questions"]:
        chunks.append("<section><h2>Selected private questions</h2>")
        for question in dossier["private_questions"]:
            chunks.append(f"<p>{esc(question['id'])}: {esc(question['prompt'])} ({esc(question['state'])}, revision {esc(question['revision'])})</p>")
        chunks.append("</section>")
    if dossier["private_observations"]:
        chunks.append("<section><h2>Selected private observations</h2>")
        for observation in dossier["private_observations"]:
            chunks.append(f"<p>{esc(observation['observed_at'])}: {esc(observation['outcome'])}; {esc(observation['note'])}; {esc(observation['access_notes'])}</p>")
        chunks.append("</section>")
    chunks.append("</main></html>")
    return "".join(chunks)
