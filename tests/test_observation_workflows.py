from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import sqlite3

import pytest
from fastapi import FastAPI, Header, HTTPException

from app.config import Settings
from app.db import Database
from app.observation_workflows import (
    OBSERVATION_WORKFLOW_REQUIRED_SCHEMA,
    OptionalObservationContext,
    effective_observation,
    install_observation_routes,
)
from tests.started_client import StartedClient, close_started_clients


ADMIN = {"Authorization": "Bearer admin"}


def _assert_workflow_schema(connection):
    for table, required in OBSERVATION_WORKFLOW_REQUIRED_SCHEMA.items():
        actual = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert required <= actual, table


@pytest.fixture
def workflow_api(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_token="admin")
    database = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(lifespan=lifespan)
    # Prepare the real migration-owned schema before synthetic fixtures are seeded;
    # StartedClient then exercises the same idempotent database startup lifespan.
    database.initialize()
    with database.connect() as connection:
        _assert_workflow_schema(connection)
        connection.execute(
            """INSERT INTO sites
               (id, external_key, name, site_kind, latitude, longitude, precision,
                uncertainty_m, location_basis, status, access, created_at, updated_at)
               VALUES (1, 'test-site', 'Synthetic site', 'shelter', 63.4, 10.4,
                       'approximate', 200, 'map_reference', 'candidate', 'unknown',
                       '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO field_observations
               (id, site_id, observed_at, outcome, note, latitude, longitude,
                point_role, uncertainty_m, observed_location_text, access_notes,
                photo_urls_json, context_json, request_id, payload_hash, created_at)
               VALUES (101, 1, '2026-01-02', 'found', 'Original field note',
                       63.401, 10.401, 'feature', 18, 'Original position', NULL,
                       '[]', '{}', 'original-request', 'original-hash',
                       '2026-01-02T12:00:00+00:00')"""
        )
        connection.execute(
            "INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES (1,'observation','{}','2026-01-02T12:00:00+00:00')"
        )
        for question_id in (201, 202):
            connection.execute(
                """INSERT INTO research_questions
                   (id, external_key, kind, state, payload_json, revision, created_at, updated_at)
                   VALUES (?, 'test-site', 'location', 'open', '{}', 1,
                           '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""",
                (question_id,),
            )
        connection.execute(
            """UPDATE sites SET approach_latitude=63.41, approach_longitude=10.41,
                       approach_access='public', approach_note='Synthetic public approach',
                       approach_reviewed_at='2026-01-01T00:00:00+00:00' WHERE id=1"""
        )

    supported_observations = {101}

    def admin_guard(authorization: str | None = Header(default=None)) -> None:
        if authorization != "Bearer admin":
            raise HTTPException(401, "bearer token required")

    def route_snapshot(connection, route_id: int):
        if route_id != 77:
            return None
        return {
            "route_id": 77,
            "stops": [{"site_id": 1, "lat": 63.41, "lon": 10.41, "point_role": "approach"}],
        }

    def manual_approaches(connection, site_ids: list[int]):
        result = []
        for site_id in site_ids:
            row = connection.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
            if row is None or row["approach_access"] != "public" or row["approach_reviewed_at"] is None:
                return None
            result.append({
                "site_id": site_id,
                "latitude": row["approach_latitude"],
                "longitude": row["approach_longitude"],
                "access": row["approach_access"],
                "reviewed_at": row["approach_reviewed_at"],
            })
        return result

    def question_membership(connection, question_id: int, site_id: int) -> bool:
        return connection.execute(
            """SELECT 1 FROM research_questions q JOIN sites s
               ON q.external_key=s.external_key WHERE q.id=? AND s.id=?""",
            (question_id, site_id),
        ).fetchone() is not None

    def observation_support(connection, site_id: int, observation_id: int) -> bool:
        return site_id == 1 and observation_id in supported_observations

    if not any(getattr(route, "path", None) == "/api/visits" for route in app.routes):
        install_observation_routes(
            app,
            database,
            admin_guard,
            route_snapshot=route_snapshot,
            manual_approaches=manual_approaches,
            question_membership=question_membership,
            observation_support=observation_support,
        )
    client = StartedClient(app)
    try:
        yield client, database, supported_observations
    finally:
        close_started_clients()


def test_amendments_are_append_only_idempotent_and_invalidate_selected_support(workflow_api):
    client, database, _ = workflow_api
    with database.connect() as connection:
        original = dict(connection.execute("SELECT * FROM field_observations WHERE id=101").fetchone())
        original_event_count = connection.execute("SELECT COUNT(*) FROM site_events").fetchone()[0]

    payload = {
        "request_id": "amend-101-1",
        "expected_observation_revision": 1,
        "reason": "The original coordinate was recorded from the wrong side of the path.",
        "latitude": 63.402,
        "longitude": 10.402,
        "note": "Corrected assessment; original note remains available.",
        "context": {"visibility_limits": "Tree cover blocks the eastern view.", "coverage_unknown": True},
    }
    response = client.post("/api/sites/1/observations/101/amendments", headers=ADMIN, json=payload)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["observation_revision"] == 2
    assert receipt["idempotent"] is False

    retry = client.post("/api/sites/1/observations/101/amendments", headers=ADMIN, json=payload)
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True
    assert retry.json()["amendment_id"] == receipt["amendment_id"]

    changed_retry = {**payload, "note": "Changed payload under the same request ID."}
    assert client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN, json=changed_retry
    ).status_code == 409
    stale = {**payload, "request_id": "amend-stale", "expected_observation_revision": 1}
    assert client.post("/api/sites/1/observations/101/amendments", headers=ADMIN, json=stale).status_code == 409

    effective = client.get("/api/sites/1/observations/101/effective", headers=ADMIN)
    assert effective.status_code == 200
    view = effective.json()
    assert view["original_snapshot"]["note"] == "Original field note"
    assert view["original_snapshot"]["latitude"] == 63.401
    assert view["note"] == "Corrected assessment; original note remains available."
    assert view["latitude"] == 63.402
    assert view["context"]["visibility_limits"] == "Tree cover blocks the eastern view."
    assert view["support_invalidated"] is True

    with database.connect() as connection:
        stored = dict(connection.execute("SELECT * FROM field_observations WHERE id=101").fetchone())
        site = dict(connection.execute("SELECT * FROM sites WHERE id=1").fetchone())
        event_count = connection.execute("SELECT COUNT(*) FROM site_events").fetchone()[0]
        amendment_count = connection.execute("SELECT COUNT(*) FROM observation_amendments").fetchone()[0]
    assert stored == original
    assert event_count == original_event_count
    assert amendment_count == 1
    assert site["location_review_required"] == 1
    assert site["revision"] == 2
    assert site["latitude"] == 63.4
    assert site["status"] == "candidate"
    assert site["access"] == "unknown"


def test_amendment_validation_rejects_unpaired_coordinates_and_unsafe_photo_urls(workflow_api):
    client, _, _ = workflow_api
    base = {
        "request_id": "bad-amendment",
        "expected_observation_revision": 1,
        "reason": "Correct an assessment.",
    }
    unpaired = client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN,
        json={**base, "latitude": 63.4},
    )
    assert unpaired.status_code == 422
    unsafe = client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN,
        json={**base, "photo_urls": ["https://user:password@example.org/photo.jpg"]},
    )
    assert unsafe.status_code == 422
    assert client.get("/api/sites/1/observations/101/effective").status_code == 401


def test_amendment_records_instrument_context_without_changing_feature_radius(workflow_api):
    client, database, _ = workflow_api
    response = client.post(
        "/api/sites/1/observations/101/amendments",
        headers=ADMIN,
        json={
            "request_id": "measurement-context",
            "expected_observation_revision": 1,
            "reason": "Add the measurement metadata reported at capture time.",
            "context": {
                "captured_at": "2026-10-02T10:00:00+02:00",
                "reported_accuracy_m": 7.5,
                "declared_uncertainty_m": 25,
                "point_role": "viewpoint",
                "linked_question_id": 201,
            },
        },
    )
    assert response.status_code == 201, response.text
    effective = response.json()["effective_observation"]
    assert effective["point_role"] == "viewpoint"
    assert effective["uncertainty_m"] == 18
    assert effective["context"]["reported_accuracy_m"] == 7.5
    assert effective["context"]["declared_uncertainty_m"] == 25
    assert effective["context"]["linked_question_id"] == 201
    with database.connect() as connection:
        original = connection.execute("SELECT point_role,uncertainty_m FROM field_observations WHERE id=101").fetchone()
    assert tuple(original) == ("feature", 18)


def test_amendment_requires_linked_question_membership_and_invalidates_only_selected_support(workflow_api):
    client, database, supported = workflow_api
    wrong_question = client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN,
        json={
            "request_id": "wrong-question",
            "expected_observation_revision": 1,
            "reason": "Attach a question to this amended assessment.",
            "context": {"linked_question_id": 9999},
        },
    )
    assert wrong_question.status_code == 422

    supported.clear()
    response = client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN,
        json={
            "request_id": "not-selected-support",
            "expected_observation_revision": 1,
            "reason": "Correct the note on a non-supporting observation.",
            "note": "Updated observation note.",
        },
    )
    assert response.status_code == 201
    assert response.json()["effective_observation"]["support_invalidated"] is False
    with database.connect() as connection:
        site = connection.execute("SELECT revision,location_review_required FROM sites WHERE id=1").fetchone()
    assert tuple(site) == (1, 0)


def test_withdrawn_observation_status_is_explicit_and_keeps_original_row(workflow_api):
    client, database, _ = workflow_api
    response = client.post(
        "/api/sites/1/observations/101/amendments", headers=ADMIN,
        json={
            "request_id": "withdraw-101",
            "expected_observation_revision": 1,
            "reason": "The observation was recorded against the wrong site.",
            "status": "withdrawn",
        },
    )
    assert response.status_code == 201, response.text
    effective = response.json()["effective_observation"]
    assert effective["status"] == "withdrawn"
    assert effective["withdrawn"] is True
    assert effective["original_snapshot"]["id"] == 101
    with database.connect() as connection:
        original = connection.execute("SELECT id,site_id,note,outcome FROM field_observations WHERE id=101").fetchone()
    assert tuple(original) == (101, 1, "Original field note", "found")


def test_concurrent_amendments_with_the_same_revision_allow_only_one_writer(workflow_api):
    client, database, _ = workflow_api
    requests = [
        {
            "request_id": f"parallel-{index}",
            "expected_observation_revision": 1,
            "reason": "Concurrent correction with an independent request identity.",
            "note": f"Concurrent note {index}.",
        }
        for index in (1, 2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda payload: client.post(
                "/api/sites/1/observations/101/amendments", headers=ADMIN, json=payload
            ),
            requests,
        ))
    assert sorted(response.status_code for response in responses) == [201, 409]
    with database.connect() as connection:
        amendments = connection.execute("SELECT revision FROM observation_amendments").fetchall()
        site = connection.execute("SELECT revision,location_review_required FROM sites WHERE id=1").fetchone()
        original = connection.execute("SELECT note FROM field_observations WHERE id=101").fetchone()
    assert [row["revision"] for row in amendments] == [2]
    assert tuple(site) == (2, 1)
    assert original["note"] == "Original field note"


def test_failed_site_review_invalidation_rolls_back_amendment_and_preserves_original(workflow_api):
    client, database, _ = workflow_api
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_location_review BEFORE UPDATE ON sites
               WHEN NEW.location_review_required=1
               BEGIN SELECT RAISE(ABORT,'injected review failure'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected review failure"):
        client.post(
            "/api/sites/1/observations/101/amendments", headers=ADMIN,
            json={
                "request_id": "rollback-amendment",
                "expected_observation_revision": 1,
                "reason": "This transaction is expected to abort after append.",
                "note": "Must not survive rollback.",
            },
        )
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM observation_amendments").fetchone()[0]
        site = connection.execute("SELECT revision,location_review_required FROM sites WHERE id=1").fetchone()
        original = connection.execute("SELECT note FROM field_observations WHERE id=101").fetchone()
        event_count = connection.execute("SELECT COUNT(*) FROM site_events").fetchone()[0]
    assert count == 0
    assert tuple(site) == (1, 0)
    assert original["note"] == "Original field note"
    assert event_count == 1


def test_legacy_negative_observation_has_unknown_scope_and_corrupt_context_fails_closed(workflow_api):
    client, database, _ = workflow_api
    with database.connect() as connection:
        connection.execute("UPDATE field_observations SET outcome='not_found',context_json='{}' WHERE id=101")
    legacy = client.get("/api/sites/1/observations/101/effective", headers=ADMIN)
    assert legacy.status_code == 200
    assert legacy.json()["negative_context_status"] == "unknown"
    assert legacy.json()["context"] == {}

    with database.connect() as connection:
        connection.execute("UPDATE field_observations SET context_json='not-json' WHERE id=101")
        row = connection.execute("SELECT * FROM field_observations WHERE id=101").fetchone()
        with pytest.raises(ValueError, match="stored observation context"):
            effective_observation(connection, row)


def test_optional_measurement_context_keeps_accuracy_distinct_and_bounded():
    context = OptionalObservationContext.model_validate({
        "captured_at": "2026-10-02T10:00:00+02:00",
        "reported_accuracy_m": 7.5,
        "declared_uncertainty_m": 25,
        "point_role": "viewpoint",
    })
    assert context.reported_accuracy_m == 7.5
    assert context.declared_uncertainty_m == 25
    assert context.point_role == "viewpoint"
    assert OptionalObservationContext().point_role == "unknown"
    with pytest.raises(ValueError):
        OptionalObservationContext.model_validate({"reported_accuracy_m": float("inf")})
    with pytest.raises(ValueError):
        OptionalObservationContext.model_validate({"captured_at": "2026-10-02T10:00:00"})


def _planned_visit(client, *, request_id="visit-1", source="route"):
    today = date.today()
    payload = {
        "request_id": request_id,
        "planned_date": today.isoformat(),
        "selected_questions": [
            {"site_id": 1, "question_id": 201},
            {"site_id": 1, "question_id": 202},
        ],
        "retention_until": (today + timedelta(days=365)).isoformat(),
    }
    if source == "route":
        payload["route_id"] = 77
    else:
        payload["manual_site_ids"] = [1]
    return client.post("/api/visits", headers=ADMIN, json=payload)


def test_visit_plan_is_not_evidence_and_skipped_question_stays_open(workflow_api):
    client, database, _ = workflow_api
    planned = _planned_visit(client)
    assert planned.status_code == 201, planned.text
    visit = planned.json()
    assert visit["state"] == "planned"
    assert visit["occurred_date"] is None
    assert visit["observation_ids"] == []
    assert visit["route_snapshot"]["route_id"] == 77
    assert visit["retention_until"] == (date.today() + timedelta(days=365)).isoformat()
    assert _planned_visit(client).json()["id"] == visit["id"]

    not_yet = client.post(
        f"/api/visits/{visit['id']}/observations", headers=ADMIN,
        json={"request_id": "attach-1", "site_id": 1, "observation_id": 101, "question_id": 202},
    )
    assert not_yet.status_code == 409

    occurred = client.patch(
        f"/api/visits/{visit['id']}", headers=ADMIN,
        json={
                "expected_revision": 1,
                "state": "occurred",
                "occurred_date": date.today().isoformat(),
                "visit_outcome": "partial",
                "outcomes": [
                {"site_id": 1, "question_id": 201, "outcome": "skipped"},
                {"site_id": 1, "question_id": 202, "outcome": "answered"},
            ],
        },
    )
    assert occurred.status_code == 200, occurred.text
    assert occurred.json()["state"] == "occurred"

    with database.connect() as connection:
        question = connection.execute("SELECT state,revision FROM research_questions WHERE id=201").fetchone()
        original = dict(connection.execute("SELECT * FROM field_observations WHERE id=101").fetchone())
    assert tuple(question) == ("open", 1)

    attach_payload = {"request_id": "attach-1", "site_id": 1, "observation_id": 101, "question_id": 202}
    attached = client.post(f"/api/visits/{visit['id']}/observations", headers=ADMIN, json=attach_payload)
    assert attached.status_code == 201, attached.text
    retry = client.post(f"/api/visits/{visit['id']}/observations", headers=ADMIN, json=attach_payload)
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True

    changed = {**attach_payload, "site_id": 2}
    assert client.post(
        f"/api/visits/{visit['id']}/observations", headers=ADMIN, json=changed
    ).status_code == 409
    stale_close = client.patch(
        f"/api/visits/{visit['id']}", headers=ADMIN,
        json={"expected_revision": 1, "state": "closed"},
    )
    assert stale_close.status_code == 409
    closed = client.patch(
        f"/api/visits/{visit['id']}", headers=ADMIN,
        json={"expected_revision": 3, "state": "closed"},
    )
    assert closed.status_code == 200
    assert closed.json()["state"] == "closed"
    assert closed.json()["observation_ids"] == [101]

    with database.connect() as connection:
        after = dict(connection.execute("SELECT * FROM field_observations WHERE id=101").fetchone())
    assert after == original


def test_manual_visits_validate_eligible_approaches_and_question_membership(workflow_api):
    client, _, _ = workflow_api
    manual = _planned_visit(client, request_id="manual-visit", source="manual")
    assert manual.status_code == 201, manual.text
    visit = manual.json()
    assert visit["route_snapshot"] is None
    assert visit["manual_approaches"] == [
        {
            "site_id": 1,
            "latitude": 63.41,
            "longitude": 10.41,
            "access": "public",
            "reviewed_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    invalid_question = client.post(
        "/api/visits", headers=ADMIN,
        json={
            "request_id": "wrong-question-site",
            "planned_date": date.today().isoformat(),
            "manual_site_ids": [1],
            "selected_questions": [{"site_id": 1, "question_id": 9999}],
        },
    )
    assert invalid_question.status_code == 422


def test_workflow_required_columns_match_sqlite_schema_introspection(workflow_api):
    _, database, _ = workflow_api
    with database.connect() as connection:
        for table, required_columns in OBSERVATION_WORKFLOW_REQUIRED_SCHEMA.items():
            actual = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert required_columns <= actual, table


def test_visit_state_transition_requires_outcomes_and_no_authless_route_access(workflow_api):
    client, _, _ = workflow_api
    assert client.post("/api/visits", json={}).status_code == 401
    planned = _planned_visit(client, request_id="visit-incomplete")
    assert planned.status_code == 201
    visit_id = planned.json()["id"]
    incomplete = client.patch(
        f"/api/visits/{visit_id}", headers=ADMIN,
        json={
            "expected_revision": 1,
            "state": "occurred",
            "occurred_date": (date.today() + timedelta(days=1)).isoformat(),
            "visit_outcome": "partial",
            "outcomes": [
                {"site_id": 1, "question_id": 201, "outcome": "skipped"},
                {"site_id": 1, "question_id": 202, "outcome": "answered"},
            ],
        },
    )
    assert incomplete.status_code == 422
