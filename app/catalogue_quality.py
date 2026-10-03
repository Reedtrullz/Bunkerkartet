"""Bounded observation-point reads and private provenance-qualified coverage."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import math
import sqlite3
from typing import Callable, Iterable

from fastapi import Depends, FastAPI, HTTPException, Query

from app.curator_history import freshness_state
from app.db import decode_stored_json
from app.imports import validate_location
from app.observation_workflows import effective_observation


SQL_CHUNK_SIZE = 500


def _chunks(values: list[int], size: int = SQL_CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def batch_observation_points(
    connection: sqlite3.Connection,
    site_ids: Iterable[int],
) -> dict[int, list[dict[str, object]]]:
    """Return site ID -> active effective coordinate points, using bounded reads.

    The projection preserves the list endpoint's ordering and point fields. Rows
    with withdrawn, corrupt, or incomplete effective coordinates are omitted,
    matching its current display contract. No original observation is rewritten.
    """
    normalized: list[int] = []
    seen: set[int] = set()
    for site_id in site_ids:
        if isinstance(site_id, bool) or not isinstance(site_id, int) or site_id <= 0:
            raise ValueError("site IDs must be positive integers")
        if site_id not in seen:
            normalized.append(site_id)
            seen.add(site_id)
    result: dict[int, list[dict[str, object]]] = {site_id: [] for site_id in normalized}
    if not normalized:
        return result

    observations: list[sqlite3.Row] = []
    for site_chunk in _chunks(normalized):
        placeholders = ",".join("?" for _ in site_chunk)
        observations.extend(connection.execute(
            f"""SELECT * FROM field_observations
                WHERE site_id IN ({placeholders})
                ORDER BY site_id,observed_at DESC,id DESC""",
            site_chunk,
        ).fetchall())

    if not observations:
        return result
    amendments_by_observation: dict[int, list[sqlite3.Row]] = defaultdict(list)
    observation_ids = [int(row["id"]) for row in observations]
    for observation_chunk in _chunks(observation_ids):
        placeholders = ",".join("?" for _ in observation_chunk)
        rows = connection.execute(
            f"""SELECT * FROM observation_amendments
                WHERE observation_id IN ({placeholders})
                ORDER BY observation_id,revision,id""",
            observation_chunk,
        ).fetchall()
        for amendment in rows:
            amendments_by_observation[int(amendment["observation_id"])].append(amendment)

    for original in observations:
        try:
            effective = effective_observation(
                connection,
                original,
                amendments=amendments_by_observation.get(int(original["id"]), ()),
            )
        except (ValueError, TypeError, KeyError):
            continue
        if effective.get("withdrawn") or effective.get("status") == "withdrawn":
            continue
        latitude, longitude = effective.get("latitude"), effective.get("longitude")
        if latitude is None or longitude is None:
            continue
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            continue
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            continue
        site_id = int(original["site_id"])
        if site_id not in result:
            continue
        result[site_id].append({
            "id": int(original["id"]),
            "observed_at": original["observed_at"],
            "outcome": effective.get("outcome"),
            "latitude": latitude,
            "longitude": longitude,
            "point_role": effective.get("point_role"),
            "uncertainty_m": effective.get("uncertainty_m"),
        })
    return result


DIMENSION_STATES = {
    "identity": ("source_backed", "legacy_unresolved", "unknown", "unavailable"),
    "location": ("recorded", "unknown", "unavailable"),
    "access": ("recorded", "unknown", "unavailable"),
    "currentness": ("current", "due", "overdue", "unknown", "policy_disabled", "unavailable"),
    "evidence": ("provenance_qualified", "legacy_unresolved", "unknown"),
    "source_dependence": ("none", "single_source", "multiple_sources"),
    "unresolved_questions": ("present", "none", "unavailable"),
}

_SITE_STATUSES = {
    "candidate", "approximate", "likely", "trusted", "field-verified",
    "destroyed-or-filled", "rejected",
}
_SITE_ACCESSES = {
    "public", "private", "restricted", "unknown", "permission_required",
    "dangerous", "unsafe",
}
_CONFIDENCE = {"high", "medium", "low", "unknown"}
_LOCATION_BASES = {
    "explicit_coordinate", "address", "map_reference", "landmark_description", "llm_inference",
}
_PRECISIONS = {"exact", "approximate", "unknown"}


def _value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _site_batches(rows: list[sqlite3.Row]):
    for start in range(0, len(rows), SQL_CHUNK_SIZE):
        yield rows[start:start + SQL_CHUNK_SIZE]


def _evidence_summary(connection, rows: list[sqlite3.Row]):
    summary = {
        int(row["id"]): {
            "count": 0, "qualified_count": 0, "legacy_count": 0,
            "sources": set(), "roles": Counter(),
            "qualified_roles": Counter(), "legacy_roles": Counter(),
        }
        for row in rows
    }
    for chunk in _site_batches(rows):
        site_ids = [int(row["id"]) for row in chunk]
        placeholders = ",".join("?" for _ in site_ids)
        imported = connection.execute(
            f"""SELECT site_id,source_id,role,provenance_status
                FROM evidence_items WHERE site_id IN ({placeholders})""",
            site_ids,
        ).fetchall()
        legacy = connection.execute(
            f"""SELECT evidence.site_id,evidence.source_id,evidence.role
                FROM evidence
                LEFT JOIN evidence_items items
                  ON items.site_id=evidence.site_id AND items.source_id=evidence.source_id
                WHERE evidence.site_id IN ({placeholders}) AND items.id IS NULL""",
            site_ids,
        ).fetchall()
        for record, provenance in [*((record, record["provenance_status"]) for record in imported),
                                   *((record, "legacy_unresolved") for record in legacy)]:
            entry = summary[int(record["site_id"])]
            entry["count"] += 1
            entry["sources"].add(int(record["source_id"]))
            entry["roles"][record["role"]] += 1
            if provenance == "import_record":
                entry["qualified_count"] += 1
                entry["qualified_roles"][record["role"]] += 1
            else:
                entry["legacy_count"] += 1
                entry["legacy_roles"][record["role"]] += 1
    return summary


def _question_summary(connection, rows: list[sqlite3.Row]):
    result = {str(row["external_key"]): {"open": 0, "deferred": 0, "resolved": 0,
                                         "unavailable": 0, "items": []}
              for row in rows}
    keys = list(result)
    for key_chunk_start in range(0, len(keys), SQL_CHUNK_SIZE):
        chunk = keys[key_chunk_start:key_chunk_start + SQL_CHUNK_SIZE]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        questions = connection.execute(
            f"""SELECT id,external_key,kind,state,payload_json
                FROM research_questions WHERE external_key IN ({placeholders}) ORDER BY id""",
            chunk,
        ).fetchall()
        for question in questions:
            entry = result[str(question["external_key"])]
            state = question["state"]
            payload, payload_status = decode_stored_json(question["payload_json"], "object")
            unavailable = state not in {"open", "deferred", "resolved"} or payload_status not in {"valid", "valid_empty"}
            if unavailable:
                entry["unavailable"] += 1
            else:
                entry[state] += 1
            if state != "resolved" or unavailable:
                entry["items"].append({
                    "id": int(question["id"]), "kind": question["kind"], "state": state,
                    "data_status": payload_status,
                    "wording": payload.get("wording") if payload_status in {"valid", "valid_empty"} else None,
                })
    return result


def _freshness_summary(connection, rows: list[sqlite3.Row], days: int, today: date):
    result: dict[int, dict[str, object]] = {
        int(row["id"]): {"state": "unknown", "reviewed_at": None, "review_status": "absent"}
        for row in rows
    }
    for chunk in _site_batches(rows):
        site_ids = [int(row["id"]) for row in chunk]
        if not site_ids:
            continue
        placeholders = ",".join("?" for _ in site_ids)
        latest = connection.execute(
            f"""SELECT event.site_id,event.payload_json
                FROM site_events event
                JOIN (
                    SELECT site_id,MAX(id) AS latest_id FROM site_events
                    WHERE event_type='freshness_review' AND site_id IN ({placeholders})
                    GROUP BY site_id
                ) newest ON newest.latest_id=event.id
                ORDER BY event.site_id""",
            site_ids,
        ).fetchall()
        for event in latest:
            site_id = int(event["site_id"])
            payload, status = decode_stored_json(event["payload_json"], "object")
            if status not in {"valid", "valid_empty"}:
                result[site_id] = {"state": "unavailable", "reviewed_at": None,
                                   "review_status": status}
                continue
            reviewed_at = payload.get("reviewed_at") if payload else None
            state = freshness_state(reviewed_at, days, today=today)
            result[site_id] = {"state": state, "reviewed_at": reviewed_at,
                               "review_status": "valid"}
    return result


def _identity_state(row, evidence, effective_content):
    content, content_status = None, "unavailable"
    try:
        content, content_status = effective_content(row)
    except (ValueError, TypeError, KeyError):
        pass
    sourced_about = any(
        _value(claim, "section") == "about"
        and _value(claim, "certainty") in {"source_supported", "supported"}
        and bool(_value(claim, "source_ids", []))
        for claim in (_value(content, "claims", []) or [])
    )
    if sourced_about or evidence["qualified_roles"].get("identity", 0):
        return "source_backed", content_status
    if evidence["legacy_roles"].get("identity", 0):
        return "legacy_unresolved", content_status
    return ("unavailable" if content_status == "unavailable" else "unknown"), content_status


def _location_state(row) -> str:
    latitude, longitude = row["latitude"], row["longitude"]
    precision, basis, radius = row["precision"], row["location_basis"], row["uncertainty_m"]
    if basis not in _LOCATION_BASES or precision not in _PRECISIONS:
        return "unavailable"
    if latitude is not None:
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            return "unavailable"
        try:
            if not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
                return "unavailable"
        except (TypeError, ValueError, OverflowError):
            return "unavailable"
    try:
        validate_location(latitude, longitude, precision, radius, basis)
    except (TypeError, ValueError, OverflowError):
        return "unavailable"
    if latitude is None or precision == "unknown":
        return "unknown"
    return "recorded"


def _dimension_record(row, evidence, questions, freshness, effective_content):
    identity, content_status = _identity_state(row, evidence, effective_content)
    location = _location_state(row)
    access_value = row["access"]
    access = "unknown" if access_value == "unknown" else (
        "recorded" if access_value in _SITE_ACCESSES else "unavailable"
    )
    evidence_state = (
        "provenance_qualified" if evidence["qualified_count"]
        else "legacy_unresolved" if evidence["legacy_count"]
        else "unknown"
    )
    sources = len(evidence["sources"])
    source_dependence = "none" if sources == 0 else "single_source" if sources == 1 else "multiple_sources"
    open_count = questions["open"] + questions["deferred"]
    question_state = "unavailable" if questions["unavailable"] else "present" if open_count else "none"
    return {
        "site_id": int(row["id"]),
        "external_key": row["external_key"],
        "name": row["name"],
        "status": row["status"],
        "access_value": access_value,
        "states": {
            "identity": identity,
            "location": location,
            "access": access,
            "currentness": freshness["state"],
            "evidence": evidence_state,
            "source_dependence": source_dependence,
            "unresolved_questions": question_state,
        },
        "content_status": content_status,
        "evidence_count": evidence["count"],
        "qualified_evidence_count": evidence["qualified_count"],
        "legacy_evidence_count": evidence["legacy_count"],
        "linked_source_count": sources,
        "question_counts": {key: questions[key] for key in ("open", "deferred", "resolved", "unavailable")},
        "unresolved_question_count": open_count + questions["unavailable"],
        "unresolved_questions": questions["items"],
        "freshness": freshness,
        "_row": row,
    }


def _count_states(records, dimension: str):
    return {state: sum(record["states"][dimension] == state for record in records)
            for state in DIMENSION_STATES[dimension]}


def install_quality_routes(
    app: FastAPI,
    database,
    guard,
    site_detail: Callable,
    effective_content: Callable,
    days: int,
) -> None:
    """Install private, read-only dimensional coverage and bounded drill-down."""
    auth = [Depends(guard)]

    @app.get("/api/quality/coverage", dependencies=auth)
    def get_quality_coverage(
        status: str | None = Query(default=None),
        site_kind: str | None = Query(default=None, max_length=100),
        access: str | None = Query(default=None),
        confidence: str | None = Query(default=None),
        dimension: str | None = Query(default=None),
        state: str | None = Query(default=None),
        before_id: int | None = Query(default=None, gt=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        if status is not None and status not in _SITE_STATUSES:
            raise HTTPException(422, "invalid site status filter")
        if access is not None and access not in _SITE_ACCESSES:
            raise HTTPException(422, "invalid access filter")
        if confidence is not None and confidence not in _CONFIDENCE:
            raise HTTPException(422, "invalid confidence filter")
        if dimension is None and state is not None:
            raise HTTPException(422, "dimension is required with a drill-down state")
        if dimension is not None:
            if dimension not in DIMENSION_STATES:
                raise HTTPException(422, "invalid quality dimension")
            if state is None or state not in DIMENSION_STATES[dimension]:
                raise HTTPException(422, "state is not valid for this quality dimension")

        filters = {"status": status, "site_kind": site_kind.strip() if site_kind else None,
                   "access": access, "confidence": confidence}
        with database.connect() as connection:
            all_rows = connection.execute(
                "SELECT * FROM sites WHERE merged_into_id IS NULL ORDER BY id"
            ).fetchall()
            filtered_rows = [row for row in all_rows if (
                (status is None or row["status"] == status)
                and (filters["site_kind"] is None or filters["site_kind"].casefold() in row["site_kind"].casefold())
                and (access is None or row["access"] == access)
                and (confidence is None or (row["confidence"] or "unknown") == confidence)
            )]
            evidence = _evidence_summary(connection, all_rows)
            questions = _question_summary(connection, all_rows)
            freshness = _freshness_summary(connection, all_rows, days, date.today())
            all_records = [
                _dimension_record(row, evidence[int(row["id"])],
                                  questions[str(row["external_key"])],
                                  freshness[int(row["id"])], effective_content)
                for row in all_rows
            ]
            filtered_ids = {int(row["id"]) for row in filtered_rows}
            selected_records = [record for record in all_records if record["site_id"] in filtered_ids]
            dimensions = {}
            for name in ("identity", "location", "access", "currentness", "evidence"):
                dimensions[name] = {
                    "total": _count_states(all_records, name),
                    "filtered": _count_states(selected_records, name),
                    "total_denominator": len(all_records),
                    "filtered_denominator": len(selected_records),
                }
            source_dependence = {
                "total": _count_states(all_records, "source_dependence"),
                "filtered": _count_states(selected_records, "source_dependence"),
                "total_denominator": len(all_records),
                "filtered_denominator": len(selected_records),
            }
            question_states = {
                "total": {
                    **_count_states(all_records, "unresolved_questions"),
                    "open": sum(record["question_counts"]["open"] for record in all_records),
                    "deferred": sum(record["question_counts"]["deferred"] for record in all_records),
                    "resolved": sum(record["question_counts"]["resolved"] for record in all_records),
                    "unavailable_questions": sum(record["question_counts"]["unavailable"] for record in all_records),
                },
                "filtered": {
                    **_count_states(selected_records, "unresolved_questions"),
                    "open": sum(record["question_counts"]["open"] for record in selected_records),
                    "deferred": sum(record["question_counts"]["deferred"] for record in selected_records),
                    "resolved": sum(record["question_counts"]["resolved"] for record in selected_records),
                    "unavailable_questions": sum(record["question_counts"]["unavailable"] for record in selected_records),
                },
                "total_denominator": len(all_records),
                "filtered_denominator": len(selected_records),
            }

            drilldown = {"dimension": dimension, "state": state, "membership_count": 0,
                         "items": [], "next_before_id": None}
            if dimension is not None:
                matches = [record for record in selected_records if record["states"][dimension] == state]
                drilldown["membership_count"] = len(matches)
                page_source = [record for record in matches
                               if before_id is None or record["site_id"] < before_id]
                page_source.sort(key=lambda item: item["site_id"], reverse=True)
                page = page_source[:limit]
                details = []
                for record in page:
                    row = record["_row"]
                    detail = site_detail(connection, row)
                    details.append({key: value for key, value in record.items() if key != "_row"} | {
                        "detail": detail,
                    })
                drilldown["items"] = details
                if len(page_source) > limit and page:
                    drilldown["next_before_id"] = page[-1]["site_id"]

            return {
                "denominators": {"total_catalogue": len(all_rows),
                                 "filtered_catalogue": len(selected_records)},
                "filters": filters,
                "dimensions": dimensions,
                "source_dependence": source_dependence,
                "questions": question_states,
                "drilldown": drilldown,
            }
