"""Private, append-only observation amendments and bounded visit sessions.

The application owns migration 12 and calls ``install_observation_routes`` only
after that migration.  Keeping the SQL here lets the parent integration allocate
the schema without coupling these handlers to ``app.main``.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import sqlite3
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db import now_iso
from app.imports import validate_reference_url


OBSERVATION_WORKFLOW_SCHEMA = [
    "ALTER TABLE field_observations ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}'",
    """CREATE TABLE observation_amendments (
        id INTEGER PRIMARY KEY,
        observation_id INTEGER NOT NULL REFERENCES field_observations(id) ON DELETE CASCADE,
        site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL CHECK(revision >= 2),
        request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        changes_json TEXT NOT NULL,
        reason TEXT NOT NULL,
        support_invalidated INTEGER NOT NULL DEFAULT 0 CHECK(support_invalidated IN (0,1)),
        created_at TEXT NOT NULL,
        UNIQUE(observation_id, revision),
        UNIQUE(observation_id, request_id)
    )""",
    "CREATE INDEX idx_observation_amendments_history ON observation_amendments(observation_id, revision)",
    """CREATE TABLE observation_visits (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        payload_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('planned','occurred','closed')),
        planned_date TEXT NOT NULL,
        occurred_date TEXT,
        visit_outcome TEXT,
        selected_questions_json TEXT NOT NULL,
        site_ids_json TEXT NOT NULL,
        route_snapshot_json TEXT,
        manual_approaches_json TEXT,
        outcomes_json TEXT NOT NULL DEFAULT '{}',
        retention_until TEXT,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        close_reason TEXT,
        closed_at TEXT,
        CHECK((route_snapshot_json IS NULL) != (manual_approaches_json IS NULL)),
        CHECK((state = 'planned' AND occurred_date IS NULL AND closed_at IS NULL) OR
              (state = 'occurred' AND occurred_date IS NOT NULL AND closed_at IS NULL) OR
              (state = 'closed' AND occurred_date IS NOT NULL AND closed_at IS NOT NULL))
    )""",
    "CREATE INDEX idx_observation_visits_state_date ON observation_visits(state, planned_date, id)",
    """CREATE TABLE visit_observations (
        id INTEGER PRIMARY KEY,
        visit_id INTEGER NOT NULL REFERENCES observation_visits(id) ON DELETE CASCADE,
        site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
        observation_id INTEGER NOT NULL REFERENCES field_observations(id) ON DELETE CASCADE,
        question_id INTEGER REFERENCES research_questions(id) ON DELETE SET NULL,
        request_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        request_json TEXT NOT NULL,
        attached_at TEXT NOT NULL,
        UNIQUE(visit_id, request_id),
        UNIQUE(visit_id, observation_id)
    )""",
    "CREATE INDEX idx_visit_observations_visit ON visit_observations(visit_id, id)",
]

OBSERVATION_WORKFLOW_COLUMNS = {
    "field_observations": {"context_json"},
    "observation_amendments": {
        "id", "observation_id", "site_id", "revision", "request_id", "payload_hash",
        "request_json", "changes_json", "reason", "support_invalidated", "created_at",
    },
    "observation_visits": {
        "id", "request_id", "payload_hash", "request_json", "state", "planned_date",
        "occurred_date", "visit_outcome", "selected_questions_json", "site_ids_json",
        "route_snapshot_json", "manual_approaches_json", "outcomes_json", "retention_until",
        "revision", "created_at", "updated_at", "close_reason", "closed_at",
    },
    "visit_observations": {
        "id", "visit_id", "site_id", "observation_id", "question_id", "request_id",
        "payload_hash", "request_json", "attached_at",
    },
}
OBSERVATION_WORKFLOW_REQUIRED_SCHEMA = {
    table: set(columns) for table, columns in OBSERVATION_WORKFLOW_COLUMNS.items()
}

MAX_CONTEXT_METRES = 100_000
MAX_WORKFLOW_JSON_BYTES = 256_000


class OptionalObservationContext(BaseModel):
    """Optional submitted measurement and bounded negative-evidence context.

    ``point_role`` is shared with the native observation request model.  The
    remaining attributes belong in ``field_observations.context_json``; reported
    instrument accuracy is never copied into the feature uncertainty column.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    point_role: Literal["feature", "entrance", "viewpoint", "unknown"] = "unknown"
    captured_at: datetime | None = None
    reported_accuracy_m: float | None = Field(default=None, gt=0, le=MAX_CONTEXT_METRES)
    declared_uncertainty_m: float | None = Field(default=None, ge=0, le=MAX_CONTEXT_METRES)
    sought_target: str | None = Field(default=None, max_length=500)
    public_viewpoint_description: str | None = Field(default=None, max_length=1000)
    visibility_limits: str | None = Field(default=None, max_length=1000)
    coverage_unknown: bool | None = None
    linked_question_id: int | None = Field(default=None, gt=0)

    @field_validator(
        "sought_target", "public_viewpoint_description", "visibility_limits", mode="before"
    )
    @classmethod
    def trim_optional_text(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("optional context text must be nonblank when supplied")
        return value.strip()

    @field_validator("captured_at")
    @classmethod
    def require_timestamp_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("captured_at must include a timezone")
        return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ObservationAssessmentPatch(_StrictModel):
    outcome: Literal["found", "not_found", "inaccessible", "needs_follow_up"] | None = None
    note: str | None = Field(default=None, min_length=1, max_length=4000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    point_role: Literal["feature", "entrance", "viewpoint", "unknown"] | None = None
    uncertainty_m: float | None = Field(default=None, ge=0, le=MAX_CONTEXT_METRES)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    access_notes: str | None = Field(default=None, max_length=2000)
    photo_urls: list[str] | None = Field(default=None, max_length=12)

    @field_validator("note", "observed_location_text", "access_notes", mode="before")
    @classmethod
    def trim_assessment_text(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("assessment text must be nonblank when supplied")
        return value.strip()

    @field_validator("photo_urls")
    @classmethod
    def safe_photo_references(cls, value):
        if value is None:
            return value
        normalized = []
        for item in value:
            safe = validate_reference_url(item)
            parsed = urlsplit(safe)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("photo references must be HTTP(S) URLs")
            normalized.append(safe)
        return normalized

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be amended together")
        return self


class ObservationAmendment(_StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    expected_observation_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    outcome: Literal["found", "not_found", "inaccessible", "needs_follow_up"] | None = None
    note: str | None = Field(default=None, min_length=1, max_length=4000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    point_role: Literal["feature", "entrance", "viewpoint", "unknown"] | None = None
    uncertainty_m: float | None = Field(default=None, ge=0, le=MAX_CONTEXT_METRES)
    observed_location_text: str | None = Field(default=None, max_length=2000)
    access_notes: str | None = Field(default=None, max_length=2000)
    photo_urls: list[str] | None = Field(default=None, max_length=12)
    context: OptionalObservationContext | None = None
    status: Literal["active", "withdrawn"] | None = None

    @field_validator("request_id", "reason", "note", "observed_location_text", "access_notes", mode="before")
    @classmethod
    def trim_amendment_text(cls, value):
        if value is None:
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("text must be nonblank when supplied")
        return value.strip()

    @field_validator("photo_urls")
    @classmethod
    def safe_photo_references(cls, value):
        return ObservationAssessmentPatch.safe_photo_references(value)

    @model_validator(mode="after")
    def validate_amendment(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be amended together")
        changed = self.model_fields_set - {
            "request_id", "expected_observation_revision", "reason", "context"
        }
        if self.context is not None and self.context.model_fields_set:
            changed.add("context")
        if not changed:
            raise ValueError("amendment must change an assessment or observation status")
        return self


class VisitQuestion(_StrictModel):
    site_id: int = Field(gt=0)
    question_id: int = Field(gt=0)


class VisitCreate(_StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    planned_date: date
    route_id: int | None = Field(default=None, gt=0)
    manual_site_ids: list[int] | None = Field(default=None, min_length=1, max_length=100)
    selected_questions: list[VisitQuestion] = Field(default_factory=list, max_length=200)
    retention_until: date | None = None

    @field_validator("request_id", mode="before")
    @classmethod
    def trim_request_id(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("request_id must be nonblank")
        return value.strip()

    @field_validator("manual_site_ids")
    @classmethod
    def unique_manual_sites(cls, value):
        if value is not None and (any(site_id <= 0 for site_id in value) or len(set(value)) != len(value)):
            raise ValueError("manual site IDs must be positive and unique")
        return value

    @model_validator(mode="after")
    def exactly_one_snapshot_source(self):
        if (self.route_id is None) == (self.manual_site_ids is None):
            raise ValueError("choose one saved route or a manual approach list")
        if len({(item.site_id, item.question_id) for item in self.selected_questions}) != len(self.selected_questions):
            raise ValueError("selected questions must be unique")
        return self


class VisitQuestionOutcome(_StrictModel):
    site_id: int = Field(gt=0)
    question_id: int = Field(gt=0)
    outcome: Literal["answered", "skipped", "not_reached"]


class VisitTransition(_StrictModel):
    expected_revision: int = Field(gt=0)
    state: Literal["occurred", "closed"]
    occurred_date: date | None = None
    visit_outcome: Literal["completed", "partial", "aborted"] | None = None
    outcomes: list[VisitQuestionOutcome] | None = Field(default=None, max_length=200)
    close_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("close_reason", mode="before")
    @classmethod
    def trim_close_reason(cls, value):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("close_reason must be nonblank when supplied")
        return value.strip() if value is not None else value


class VisitObservationAttachment(_StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    site_id: int = Field(gt=0)
    observation_id: int = Field(gt=0)
    question_id: int | None = Field(default=None, gt=0)

    @field_validator("request_id", mode="before")
    @classmethod
    def trim_request_id(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("request_id must be nonblank")
        return value.strip()


def _canonical(value: object) -> str:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("workflow data is not valid bounded JSON") from error
    if len(result.encode("utf-8")) > MAX_WORKFLOW_JSON_BYTES:
        raise ValueError("workflow data exceeds the storage limit")
    return result


def _decode_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise ValueError(f"stored observation {field} is corrupt")
    try:
        decoded = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"stored observation {field} is corrupt") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"stored observation {field} is corrupt")
    return decoded


def _decode_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, str):
        raise ValueError(f"stored observation {field} is corrupt")
    try:
        decoded = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"stored observation {field} is corrupt") from error
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError(f"stored observation {field} is corrupt")
    return [ObservationAssessmentPatch.safe_photo_references([item])[0] for item in decoded]


def _finite_coordinate(value: object, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("stored observation coordinate is corrupt")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("stored observation coordinate is corrupt") from error
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError("stored observation coordinate is corrupt")
    return result


def _assessment_from_row(row: sqlite3.Row) -> dict[str, object]:
    try:
        assessment = {
            "observed_at": row["observed_at"],
            "outcome": row["outcome"],
            "note": row["note"],
            "latitude": _finite_coordinate(row["latitude"], -90, 90),
            "longitude": _finite_coordinate(row["longitude"], -180, 180),
            "point_role": row["point_role"],
            "uncertainty_m": row["uncertainty_m"],
            "observed_location_text": row["observed_location_text"],
            "access_notes": row["access_notes"],
            "photo_urls": _decode_string_list(row["photo_urls_json"], "photo URLs"),
        }
    except (IndexError, KeyError) as error:
        raise ValueError("stored observation row is incomplete") from error
    if (assessment["latitude"] is None) != (assessment["longitude"] is None):
        raise ValueError("stored observation coordinate pair is corrupt")
    if assessment["outcome"] not in {"found", "not_found", "inaccessible", "needs_follow_up"}:
        raise ValueError("stored observation outcome is corrupt")
    if assessment["point_role"] not in {"feature", "entrance", "viewpoint", "unknown"}:
        raise ValueError("stored observation role is corrupt")
    if not isinstance(assessment["note"], str) or not assessment["note"].strip():
        raise ValueError("stored observation note is corrupt")
    if not isinstance(assessment["observed_at"], str) or not assessment["observed_at"].strip():
        raise ValueError("stored observation date is corrupt")
    radius = assessment["uncertainty_m"]
    if radius is not None:
        try:
            radius_value = float(radius)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("stored observation uncertainty is corrupt") from error
        if isinstance(radius, bool) or not math.isfinite(radius_value) or radius_value < 0:
            raise ValueError("stored observation uncertainty is corrupt")
        assessment["uncertainty_m"] = radius_value
    return assessment


def _context_from_row(row: sqlite3.Row) -> dict[str, object]:
    try:
        raw = row["context_json"]
    except (IndexError, KeyError) as error:
        raise ValueError("stored observation context is missing") from error
    context = _decode_object(raw, "context")
    if "point_role" in context:
        raise ValueError("stored observation context duplicates the point role")
    try:
        validated = OptionalObservationContext.model_validate({"point_role": "unknown", **context})
    except Exception as error:
        raise ValueError("stored observation context is corrupt") from error
    return validated.model_dump(mode="json", exclude_unset=True, exclude={"point_role"})


def effective_observation(connection: sqlite3.Connection, row: sqlite3.Row, *, amendments=None) -> dict[str, object]:
    """Return immutable original evidence plus the validated effective assessment."""
    original_assessment = _assessment_from_row(row)
    original_context = _context_from_row(row)
    original = {
        "id": int(row["id"]),
        "site_id": int(row["site_id"]),
        **original_assessment,
        "context": original_context,
        "created_at": row["created_at"],
    }
    current = dict(original_assessment)
    context = dict(original_context)
    revision = 1
    status = "active"
    support_invalidated = False
    history: list[dict[str, object]] = []
    try:
        if amendments is None:
            amendments = connection.execute(
                "SELECT * FROM observation_amendments WHERE observation_id=? ORDER BY revision,id",
                (row["id"],),
            ).fetchall()
    except sqlite3.DatabaseError as error:
        raise ValueError("observation amendment history is unavailable") from error
    for amendment in amendments:
        next_revision = revision + 1
        if amendment["revision"] != next_revision:
            raise ValueError("stored observation amendment revisions are corrupt")
        request = _decode_object(amendment["request_json"], "amendment request")
        canonical = _canonical(request)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != amendment["payload_hash"]:
            raise ValueError("stored observation amendment hash is corrupt")
        changes = _decode_object(amendment["changes_json"], "amendment changes")
        assessment_patch = changes.get("assessment", {})
        context_patch = changes.get("context", {})
        next_status = changes.get("status")
        if not isinstance(assessment_patch, dict) or not isinstance(context_patch, dict):
            raise ValueError("stored observation amendment is corrupt")
        try:
            normalized_assessment = ObservationAssessmentPatch.model_validate(assessment_patch).model_dump(
                mode="json", exclude_unset=True
            )
            normalized_context = OptionalObservationContext.model_validate(
                {"point_role": "unknown", **context_patch}
            ).model_dump(mode="json", exclude_unset=True, exclude={"point_role"})
        except Exception as error:
            raise ValueError("stored observation amendment is corrupt") from error
        if normalized_assessment != assessment_patch or normalized_context != context_patch:
            raise ValueError("stored observation amendment is not canonical")
        if next_status is not None:
            if next_status not in {"active", "withdrawn"}:
                raise ValueError("stored observation status is corrupt")
            status = next_status
        current.update(normalized_assessment)
        context.update(normalized_context)
        invalidated = amendment["support_invalidated"]
        if invalidated not in (0, 1):
            raise ValueError("stored observation support marker is corrupt")
        support_invalidated = support_invalidated or bool(invalidated)
        revision = next_revision
        history.append({
            "id": int(amendment["id"]),
            "revision": revision,
            "reason": amendment["reason"],
            "changes": changes,
            "support_invalidated": bool(invalidated),
            "created_at": amendment["created_at"],
        })
    negative_fields = {
        "sought_target", "public_viewpoint_description", "visibility_limits",
        "coverage_unknown", "linked_question_id",
    }
    negative_context_status = "unknown"
    if current["outcome"] in {"not_found", "inaccessible"} and any(
        key in context and context[key] is not None for key in negative_fields
    ):
        negative_context_status = "recorded"
    return {
        "original_snapshot": original,
        **current,
        "context": context,
        "observation_revision": revision,
        "status": status,
        "withdrawn": status == "withdrawn",
        "support_invalidated": support_invalidated,
        "negative_context_status": negative_context_status,
        "amendments": history,
    }


def _snapshot_site_ids(value: object) -> list[int]:
    if not isinstance(value, dict) or not isinstance(value.get("stops"), list):
        raise HTTPException(503, "saved route snapshot is unavailable")
    site_ids = []
    for stop in value["stops"]:
        if not isinstance(stop, dict) or isinstance(stop.get("site_id"), bool):
            raise HTTPException(503, "saved route snapshot is corrupt")
        try:
            site_id = int(stop["site_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(503, "saved route snapshot is corrupt") from error
        if site_id <= 0 or site_id in site_ids:
            raise HTTPException(503, "saved route snapshot has invalid site membership")
        site_ids.append(site_id)
    if not site_ids:
        raise HTTPException(422, "a visit route must contain at least one site")
    try:
        if len(_canonical(value).encode("utf-8")) > MAX_WORKFLOW_JSON_BYTES:
            raise HTTPException(413, "saved route snapshot exceeds the visit limit")
    except ValueError as error:
        raise HTTPException(503, "saved route snapshot is corrupt") from error
    return site_ids


def _validate_manual_approaches(value: object, requested_ids: list[int]) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != len(requested_ids):
        raise HTTPException(422, "manual approach choices are unavailable or incomplete")
    by_id = {}
    for approach in value:
        if not isinstance(approach, dict) or isinstance(approach.get("site_id"), bool):
            raise HTTPException(422, "manual approach choice is invalid")
        try:
            site_id = int(approach["site_id"])
            latitude = _finite_coordinate(approach.get("latitude"), -90, 90)
            longitude = _finite_coordinate(approach.get("longitude"), -180, 180)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(422, "manual approach choice is invalid") from error
        if site_id in by_id or (latitude is None) != (longitude is None):
            raise HTTPException(422, "manual approach choice has invalid coordinates")
        if approach.get("access") != "public" or not approach.get("reviewed_at"):
            raise HTTPException(409, "manual visits require reviewed public approaches")
        if (
            approach.get("merged_into_id") is not None
            or approach.get("status") == "rejected"
            or approach.get("location_review_required") is True
        ):
            raise HTTPException(409, "manual approach is not currently eligible")
        by_id[site_id] = {
            key: approach[key]
            for key in ("site_id", "latitude", "longitude", "access", "reviewed_at", "note")
            if key in approach
        }
        by_id[site_id].update({"site_id": site_id, "latitude": latitude, "longitude": longitude})
    if set(by_id) != set(requested_ids):
        raise HTTPException(422, "manual approach choices do not match the requested sites")
    try:
        _canonical(list(by_id.values()))
    except ValueError as error:
        raise HTTPException(422, "manual approach choices are invalid") from error
    return [by_id[site_id] for site_id in requested_ids]


def _visit_record(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    try:
        selected = json.loads(row["selected_questions_json"])
        site_ids = json.loads(row["site_ids_json"])
        outcomes = json.loads(row["outcomes_json"])
        route = json.loads(row["route_snapshot_json"]) if row["route_snapshot_json"] is not None else None
        approaches = json.loads(row["manual_approaches_json"]) if row["manual_approaches_json"] is not None else None
        attachments = connection.execute(
            "SELECT observation_id,site_id,question_id,attached_at FROM visit_observations WHERE visit_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
    except (sqlite3.DatabaseError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(409, "stored visit data is unavailable") from error
    if not isinstance(selected, list) or not isinstance(site_ids, list) or not isinstance(outcomes, dict):
        raise HTTPException(409, "stored visit data is corrupt")
    return {
        "id": int(row["id"]),
        "state": row["state"],
        "planned_date": row["planned_date"],
        "occurred_date": row["occurred_date"],
        "visit_outcome": row["visit_outcome"],
        "selected_questions": selected,
        "outcomes": outcomes,
        "site_ids": site_ids,
        "route_snapshot": route,
        "manual_approaches": approaches,
        "observation_ids": [int(item["observation_id"]) for item in attachments],
        "attachments": [dict(item) for item in attachments],
        "retention_until": row["retention_until"],
        "retention_policy_status": "specified_until" if row["retention_until"] else "unspecified",
        "close_reason": row["close_reason"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "closed_at": row["closed_at"],
    }


def install_observation_routes(
    app: FastAPI,
    database,
    admin_guard,
    *,
    route_snapshot: Callable[[sqlite3.Connection, int], dict[str, object] | None] | None = None,
    manual_approaches: Callable[[sqlite3.Connection, list[int]], list[dict[str, object]] | None] | None = None,
    question_membership: Callable[[sqlite3.Connection, int, int], bool] | None = None,
    observation_support: Callable[[sqlite3.Connection, int, int], bool] | None = None,
) -> None:
    """Install all private observation and visit endpoints on an initialized app.

    Callbacks must read only local snapshots/state. ``route_snapshot`` must never
    call a routing provider; ``observation_support`` identifies the observation
    currently used by an adopted coordinate or selected review decision.
    """
    auth = [Depends(admin_guard)]

    def current_observation(connection, site_id: int, observation_id: int):
        row = connection.execute(
            "SELECT * FROM field_observations WHERE id=? AND site_id=?",
            (observation_id, site_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "observation not found")
        try:
            effective_observation(connection, row)
        except ValueError as error:
            raise HTTPException(409, {"code": "STORED_DATA_UNAVAILABLE", "message": str(error)}) from error
        return row

    @app.post("/api/sites/{site_id}/observations/{observation_id}/amendments", dependencies=auth)
    def amend_observation(site_id: int, observation_id: int, body: ObservationAmendment):
        request_payload = body.model_dump(mode="json", exclude={"request_id"})
        request_record = {"request_id": body.request_id, **request_payload}
        request_json = _canonical(request_record)
        payload_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        assessment = ObservationAssessmentPatch.model_validate(
            body.model_dump(exclude={
                "request_id", "expected_observation_revision", "reason", "context", "status"
            }, exclude_unset=True)
        ).model_dump(mode="json", exclude_unset=True)
        context = {}
        if body.context is not None:
            context = body.context.model_dump(mode="json", exclude_unset=True, exclude={"point_role"})
            if "point_role" in body.context.model_fields_set:
                if "point_role" in assessment and assessment["point_role"] != body.context.point_role:
                    raise HTTPException(422, "point_role was supplied twice with different values")
                assessment["point_role"] = body.context.point_role
        changes = {"assessment": assessment, "context": context, "status": body.status}
        changes_json = _canonical(changes)
        timestamp = now_iso()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = current_observation(connection, site_id, observation_id)
            existing = connection.execute(
                "SELECT * FROM observation_amendments WHERE observation_id=? AND request_id=?",
                (observation_id, body.request_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, "amendment request_id already has a different payload")
                effective = effective_observation(connection, row)
                return JSONResponse(status_code=200, content={
                    "amendment_id": int(existing["id"]),
                    "observation_revision": effective["observation_revision"],
                    "idempotent": True,
                    "effective_observation": effective,
                })
            effective = effective_observation(connection, row)
            if body.expected_observation_revision != effective["observation_revision"]:
                raise HTTPException(409, {
                    "code": "OBSERVATION_REVISION_MISMATCH",
                    "message": "observation changed; reload before amending",
                    "current_revision": effective["observation_revision"],
                })
            if context.get("linked_question_id") is not None:
                if question_membership is None:
                    raise HTTPException(503, "site question membership validation is not configured")
                if not question_membership(connection, int(context["linked_question_id"]), site_id):
                    raise HTTPException(422, "linked question does not belong to this observation site")
            assessment = {
                key: value for key, value in assessment.items()
                if effective[key] != value
            }
            context = {
                key: value for key, value in context.items()
                if effective["context"].get(key) != value
            }
            status_change = body.status if body.status != effective["status"] else None
            if not assessment and not context and status_change is None:
                raise HTTPException(422, "amendment does not change the effective assessment")
            changes = {"assessment": assessment, "context": context, "status": status_change}
            changes_json = _canonical(changes)
            support_invalidated = bool(
                observation_support is not None
                and observation_support(connection, site_id, observation_id)
                and (assessment or context or status_change is not None)
            )
            revision = effective["observation_revision"] + 1
            cursor = connection.execute(
                """INSERT INTO observation_amendments
                   (observation_id,site_id,revision,request_id,payload_hash,request_json,
                    changes_json,reason,support_invalidated,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (observation_id, site_id, revision, body.request_id, payload_hash, request_json,
                 changes_json, body.reason, int(support_invalidated), timestamp),
            )
            if support_invalidated:
                updated = connection.execute(
                    """UPDATE sites SET location_review_required=1,revision=revision+1,updated_at=?
                       WHERE id=?""",
                    (timestamp, site_id),
                )
                if updated.rowcount != 1:
                    raise HTTPException(409, "observation site changed during amendment")
            effective = effective_observation(connection, row)
            return JSONResponse(status_code=201, content={
                "amendment_id": int(cursor.lastrowid),
                "observation_revision": revision,
                "idempotent": False,
                "effective_observation": effective,
            })

    @app.get("/api/sites/{site_id}/observations/{observation_id}/effective", dependencies=auth)
    def get_effective_observation(site_id: int, observation_id: int):
        with database.connect() as connection:
            row = current_observation(connection, site_id, observation_id)
            return effective_observation(connection, row)

    @app.post("/api/visits", dependencies=auth)
    def create_visit(body: VisitCreate):
        request_payload = body.model_dump(mode="json", exclude={"request_id"})
        request_record = {"request_id": body.request_id, **request_payload}
        request_json = _canonical(request_record)
        payload_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM observation_visits WHERE request_id=?", (body.request_id,)
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, "visit request_id already has a different payload")
                return JSONResponse(status_code=200, content={
                    **_visit_record(connection, existing), "idempotent": True
                })
            route_value = None
            approaches_value = None
            if body.route_id is not None:
                if route_snapshot is None:
                    raise HTTPException(503, "saved route snapshots are not configured")
                route_value = route_snapshot(connection, body.route_id)
                if route_value is None:
                    raise HTTPException(404, "saved route not found")
                site_ids = _snapshot_site_ids(route_value)
            else:
                if manual_approaches is None:
                    raise HTTPException(503, "manual approach snapshots are not configured")
                site_ids = list(body.manual_site_ids or [])
                approaches_value = _validate_manual_approaches(
                    manual_approaches(connection, site_ids), site_ids
                )
            selected = [item.model_dump(mode="json") for item in body.selected_questions]
            for item in body.selected_questions:
                if item.site_id not in site_ids:
                    raise HTTPException(422, "selected question site is outside this visit")
                if question_membership is None:
                    raise HTTPException(503, "site question membership validation is not configured")
                if not question_membership(connection, item.question_id, item.site_id):
                    raise HTTPException(422, "selected question does not belong to its site")
            timestamp = now_iso()
            cursor = connection.execute(
                """INSERT INTO observation_visits
                   (request_id,payload_hash,request_json,state,planned_date,selected_questions_json,
                    site_ids_json,route_snapshot_json,manual_approaches_json,retention_until,
                    revision,created_at,updated_at)
                   VALUES (?,?,?,'planned',?,?,?,?,?,?,1,?,?)""",
                (body.request_id, payload_hash, request_json, body.planned_date.isoformat(),
                 _canonical(selected), _canonical(site_ids),
                 _canonical(route_value) if route_value is not None else None,
                 _canonical(approaches_value) if approaches_value is not None else None,
                 body.retention_until.isoformat() if body.retention_until else None,
                 timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM observation_visits WHERE id=?", (cursor.lastrowid,)).fetchone()
            return JSONResponse(status_code=201, content={**_visit_record(connection, row), "idempotent": False})

    @app.get("/api/visits/{visit_id}", dependencies=auth)
    def get_visit(visit_id: int):
        with database.connect() as connection:
            row = connection.execute("SELECT * FROM observation_visits WHERE id=?", (visit_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "visit not found")
            return _visit_record(connection, row)

    @app.patch("/api/visits/{visit_id}", dependencies=auth)
    def transition_visit(visit_id: int, body: VisitTransition):
        timestamp = now_iso()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM observation_visits WHERE id=?", (visit_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "visit not found")
            if row["revision"] != body.expected_revision:
                raise HTTPException(409, {
                    "code": "VISIT_REVISION_MISMATCH",
                    "message": "visit changed; reload before continuing",
                    "current_revision": int(row["revision"]),
                })
            if body.state == "occurred":
                if row["state"] != "planned":
                    raise HTTPException(409, "only a planned visit can transition to occurred")
                if body.occurred_date is None or body.visit_outcome is None or body.outcomes is None:
                    raise HTTPException(422, "occurrence requires an occurred date and explicit outcomes")
                if body.occurred_date > date.today():
                    raise HTTPException(422, "a future date cannot be recorded as an occurred visit")
                selected = json.loads(row["selected_questions_json"])
                expected = {(int(item["site_id"]), int(item["question_id"])) for item in selected}
                actual = [(item.site_id, item.question_id) for item in body.outcomes]
                if len(set(actual)) != len(actual) or set(actual) != expected:
                    raise HTTPException(422, "each selected question requires exactly one visit outcome")
                outcomes = [item.model_dump(mode="json") for item in body.outcomes]
                if any(not question_membership or not question_membership(connection, item.question_id, item.site_id)
                       for item in body.outcomes):
                    raise HTTPException(409, "a selected question no longer belongs to its site")
                outcomes_json = _canonical({"questions": outcomes})
                connection.execute(
                    """UPDATE observation_visits SET state='occurred',occurred_date=?,visit_outcome=?,
                       outcomes_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?""",
                    (body.occurred_date.isoformat(), body.visit_outcome, outcomes_json,
                     timestamp, visit_id, body.expected_revision),
                )
            else:
                if row["state"] != "occurred":
                    raise HTTPException(409, "only an occurred visit can be closed")
                if body.occurred_date is not None or body.visit_outcome is not None or body.outcomes is not None:
                    raise HTTPException(422, "closing a visit cannot rewrite its occurrence record")
                connection.execute(
                    """UPDATE observation_visits SET state='closed',closed_at=?,revision=revision+1,
                       updated_at=? WHERE id=? AND revision=?""",
                    (timestamp, timestamp, visit_id, body.expected_revision),
                )
            updated = connection.execute("SELECT * FROM observation_visits WHERE id=?", (visit_id,)).fetchone()
            return _visit_record(connection, updated)

    @app.post("/api/visits/{visit_id}/observations", dependencies=auth)
    def attach_visit_observation(visit_id: int, body: VisitObservationAttachment):
        request_payload = body.model_dump(mode="json", exclude={"request_id"})
        request_json = _canonical({"request_id": body.request_id, **request_payload})
        payload_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        timestamp = now_iso()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            visit = connection.execute("SELECT * FROM observation_visits WHERE id=?", (visit_id,)).fetchone()
            if visit is None:
                raise HTTPException(404, "visit not found")
            existing = connection.execute(
                "SELECT * FROM visit_observations WHERE visit_id=? AND request_id=?",
                (visit_id, body.request_id),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise HTTPException(409, "attachment request_id already has a different payload")
                return JSONResponse(status_code=200, content={
                    "visit_id": visit_id,
                    "observation_id": int(existing["observation_id"]),
                    "idempotent": True,
                    "revision": int(visit["revision"]),
                })
            if visit["state"] != "occurred":
                raise HTTPException(409, "observations can be attached only to an occurred visit")
            site_ids = json.loads(visit["site_ids_json"])
            if body.site_id not in site_ids:
                raise HTTPException(422, "observation site is outside this visit")
            questions = json.loads(visit["selected_questions_json"])
            if body.question_id is not None:
                if not any(item["site_id"] == body.site_id and item["question_id"] == body.question_id for item in questions):
                    raise HTTPException(422, "question is not selected for this site in the visit")
                if question_membership is None or not question_membership(connection, body.question_id, body.site_id):
                    raise HTTPException(409, "question no longer belongs to this site")
                outcomes = json.loads(visit["outcomes_json"]).get("questions", [])
                question_outcome = next(
                    item["outcome"] for item in outcomes
                    if item["site_id"] == body.site_id and item["question_id"] == body.question_id
                )
                if question_outcome != "answered":
                    raise HTTPException(409, "an observation cannot be attached to a skipped or unreached question")
            observation = current_observation(connection, body.site_id, body.observation_id)
            try:
                observation_date = date.fromisoformat(str(observation["observed_at"])[:10])
                occurred_date = date.fromisoformat(visit["occurred_date"])
            except (TypeError, ValueError) as error:
                raise HTTPException(409, "stored observation or visit date is corrupt") from error
            if observation_date > occurred_date:
                raise HTTPException(422, "a future-dated observation cannot be attached to an occurred visit")
            duplicate = connection.execute(
                "SELECT 1 FROM visit_observations WHERE visit_id=? AND observation_id=?",
                (visit_id, body.observation_id),
            ).fetchone()
            if duplicate is not None:
                raise HTTPException(409, "observation is already attached to this visit")
            cursor = connection.execute(
                """INSERT INTO visit_observations
                   (visit_id,site_id,observation_id,question_id,request_id,payload_hash,request_json,attached_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (visit_id, body.site_id, body.observation_id, body.question_id, body.request_id,
                 payload_hash, request_json, timestamp),
            )
            updated = connection.execute(
                "UPDATE observation_visits SET revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (timestamp, visit_id, visit["revision"]),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, "visit changed during observation attachment")
            return JSONResponse(status_code=201, content={
                "attachment_id": int(cursor.lastrowid),
                "visit_id": visit_id,
                "observation_id": body.observation_id,
                "idempotent": False,
                "revision": int(visit["revision"]) + 1,
                "observation_status": effective_observation(connection, observation)["status"],
            })
