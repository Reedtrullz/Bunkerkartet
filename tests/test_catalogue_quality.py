from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta
import json
import hashlib
import re
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import _observation_points, create_app
from app.catalogue_quality import batch_observation_points, install_quality_routes, _location_state


ADMIN = {"Authorization": "Bearer quality-test"}


def _insert_sites_and_provenance(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO import_batches
               (batch_id,schema_version,generated_at,created_at,committed_at,payload_hash)
               VALUES ('quality-seed','1.0','2026-01-01','2026-01-01','2026-01-01',?)""",
            ("a" * 64,),
        )
        sites = [
            (1, "quality:identity", "Identity fixture", None, None, "unknown", "unknown", "candidate"),
            (2, "quality:location", "Location fixture", 63.4, 10.4, "map_reference", "unknown", "candidate"),
            (3, "quality:access", "Access fixture", None, None, "unknown", "public", "likely"),
            (4, "quality:unknown", "Unknown fixture", None, None, "unknown", "unknown", "candidate"),
        ]
        for site_id, external_key, name, lat, lon, basis, access, status in sites:
            connection.execute(
                """INSERT INTO sites
                   (id,external_key,name,site_kind,latitude,longitude,precision,uncertainty_m,
                    location_basis,status,access,created_at,updated_at)
                   VALUES (?,?,?,'shelter',?,?,'approximate',25,?,?,?,'2026-01-01','2026-01-01')""",
                (site_id, external_key, name, lat, lon, basis, status, access),
            )
            if site_id not in (1, 2, 3):
                continue
            sources = 2 if site_id == 2 else 1
            payload_sources = []
            for source_index in range(sources):
                source_id = site_id * 10 + source_index
                url = f"https://example.test/quality/{source_id}"
                title = f"Synthetic source {source_id}"
                payload_sources.append({"url": url, "title": title, "source_type": "archive"})
                connection.execute(
                    """INSERT INTO sources
                       (id,url,title,source_type,excerpt,published_at,accessed_at,created_at,updated_at)
                       VALUES (?,?,?,'archive','synthetic excerpt','2026-01-01','2026-01-02','2026-01-01','2026-01-01')""",
                    (source_id, url, title),
                )
            payload = json.dumps({"sources": payload_sources}, sort_keys=True)
            connection.execute(
                """INSERT INTO import_records
                   (batch_id,external_key,site_id,action,payload_json,created_at)
                   VALUES ('quality-seed',?,?,'created',?,'2026-01-01')""",
                (external_key, site_id, payload),
            )
            record_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            role = {1: "context", 2: "location", 3: "access"}[site_id]
            for source_index in range(sources):
                source_id = site_id * 10 + source_index
                connection.execute(
                    """INSERT INTO evidence_items
                       (site_id,source_id,import_record_id,source_index,excerpt,content_kind,role,
                        published_at,accessed_at,provenance_status,created_at)
                       VALUES (?,?,?,?,?,'summary',?,'2026-01-01','2026-01-02','import_record','2026-01-01')""",
                    (site_id, source_id, record_id, source_index, "Synthetic evidence", role),
                )
        connection.execute(
            "INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(2,'freshness_review',?,'2026-10-03')",
            (json.dumps({"reviewed_at": date.today().isoformat()}),),
        )
        connection.execute(
            "INSERT INTO site_events(site_id,event_type,payload_json,created_at) VALUES(3,'freshness_review',?,'2026-08-01')",
            (json.dumps({"reviewed_at": (date.today() - timedelta(days=60)).isoformat()}),),
        )
        connection.execute(
            """INSERT INTO research_questions
               (external_key,kind,state,payload_json,revision,created_at,updated_at)
               VALUES ('quality:unknown','access','open','{}',1,'2026-01-01','2026-01-01')"""
        )


def _insert_observation(connection, observation_id: int, request_id: str, *, latitude: float, longitude: float):
    connection.execute(
        """INSERT INTO field_observations
           (id,site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
            observed_location_text,access_notes,photo_urls_json,context_json,request_id,payload_hash,created_at)
           VALUES (?,1,'2026-01-03','found','Synthetic point',?,?,'feature',17,NULL,NULL,'[]','{}',?,?,
                   '2026-01-03T12:00:00+00:00')""",
        (observation_id, latitude, longitude, request_id, f"{observation_id:064x}"),
    )


def _insert_amendment(connection, observation_id: int, revision: int, request: dict, changes: dict):
    request_json = json.dumps(request, sort_keys=True, separators=(",", ":"))
    changes_json = json.dumps(changes, sort_keys=True, separators=(",", ":"))
    connection.execute(
        """INSERT INTO observation_amendments
           (observation_id,site_id,revision,request_id,payload_hash,request_json,changes_json,
            reason,support_invalidated,created_at)
           VALUES (?,1,?,?,?,?,?,?,0,'2026-01-04T12:00:00+00:00')""",
        (observation_id, revision, request["request_id"],
         hashlib.sha256(request_json.encode()).hexdigest(), request_json, changes_json,
         request["reason"]),
    )


@pytest.fixture
def quality_api(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_token="quality-test", freshness_policy_days=30)
    database = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(lifespan=lifespan)

    def guard(authorization: str | None = Header(default=None)):
        if authorization != "Bearer quality-test":
            raise HTTPException(401, "bearer token required")

    def site_detail(connection, row):
        return {"site_id": int(row["id"]), "name": row["name"], "revision": int(row["revision"])}

    about_claim = SimpleNamespace(section="about", certainty="source_supported", source_ids=["curated-source"])

    def effective_content(row):
        if row["external_key"] == "quality:identity":
            return SimpleNamespace(claims=[about_claim]), "seed"
        return None, "absent"

    install_quality_routes(app, database, guard, site_detail, effective_content, 30)
    with TestClient(app) as client:
        _insert_sites_and_provenance(database)
        yield client, database


def test_batched_observation_points_uses_prefetched_amendments_and_effective_values(quality_api):
    _, database = quality_api
    observation_id, withdrawn_id = 101, 102
    with database.connect() as connection:
        _insert_observation(connection, observation_id, "quality-point",
                            latitude=63.4, longitude=10.4)
        _insert_amendment(connection, observation_id, 2, {
            "request_id": "quality-point-amend", "expected_observation_revision": 1,
            "reason": "Correct synthetic survey point", "latitude": 63.41,
            "longitude": 10.41, "point_role": "feature", "uncertainty_m": 9,
        }, {"assessment": {"latitude": 63.41, "longitude": 10.41, "point_role": "feature",
                           "uncertainty_m": 9}, "context": {}, "status": None})
        _insert_observation(connection, withdrawn_id, "quality-withdrawn",
                            latitude=63.5, longitude=10.5)
        _insert_observation(connection, 103, "quality-corrupt-photo-json",
                            latitude=63.6, longitude=10.6)
        _insert_observation(connection, 104, "quality-corrupt-context-json",
                            latitude=63.7, longitude=10.7)
        _insert_observation(connection, 105, "quality-invalid-amendment-role",
                            latitude=63.8, longitude=10.8)
        _insert_observation(connection, 106, "quality-invalid-amendment-radius",
                            latitude=63.9, longitude=10.9)
        connection.execute("UPDATE field_observations SET photo_urls_json='{bad' WHERE id=103")
        connection.execute("UPDATE field_observations SET context_json='[]' WHERE id=104")
        _insert_amendment(connection, withdrawn_id, 2, {
            "request_id": "quality-withdraw", "expected_observation_revision": 1,
            "reason": "Withdraw synthetic point", "status": "withdrawn",
        }, {"assessment": {}, "context": {}, "status": "withdrawn"})
        for observation, request, changes in (
            (105, "quality-invalid-role", {"assessment": {"point_role": "approach"},
                                            "context": {}, "status": None}),
            (106, "quality-invalid-radius", {"assessment": {"uncertainty_m": 100001},
                                              "context": {}, "status": None}),
        ):
            _insert_amendment(connection, observation, 2, {
                "request_id": request, "expected_observation_revision": 1,
                "reason": "Corrupt synthetic stored amendment",
            }, changes)
    statements: list[str] = []
    with database.connect() as connection:
        connection.set_trace_callback(statements.append)
        original_points = _observation_points(connection, 1)
        legacy_amendment_reads = sum("FROM observation_amendments" in statement for statement in statements)
        statements.clear()
        points = batch_observation_points(connection, [1, 2, 3, 4])
    expected = [{
        "id": observation_id, "observed_at": "2026-01-03", "outcome": "found",
        "latitude": 63.41, "longitude": 10.41, "point_role": "feature", "uncertainty_m": 9.0,
    }]
    assert original_points == expected
    assert points[1] == original_points
    assert {site_id: values for site_id, values in points.items() if site_id != 1} == {
        2: [], 3: [], 4: [],
    }
    assert legacy_amendment_reads == 4
    assert not any(re.search(r"observation_id\s*=\s*\?\s*$", statement, re.I)
                   for statement in statements)
    assert sum("FROM observation_amendments" in statement for statement in statements) == 1


def test_observation_batch_bind_lists_are_capped_at_500(quality_api):
    _, database = quality_api
    with database.connect() as connection:
        connection.executemany(
            """INSERT INTO sites
               (external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (?,?,'shelter','unknown','map_reference','2026-01-01','2026-01-01')""",
            [(f"quality:batch:{i}", f"Batch fixture {i}") for i in range(1001)],
        )
        connection.executemany(
            """INSERT INTO field_observations
               (site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
                photo_urls_json,context_json,request_id,payload_hash,created_at)
               VALUES (1,'2026-01-03','found','Synthetic batch point',63.4,10.4,'feature',12,
                       '[]','{}',?,?, '2026-01-03T12:00:00+00:00')""",
            [(f"quality:batch-observation:{i}", f"{i:064x}") for i in range(1005)],
        )
    statements: list[str] = []
    with database.connect() as connection:
        connection.set_trace_callback(statements.append)
        result = batch_observation_points(connection, list(range(1, 1006)))
    assert len(result) == 1005
    assert len(result[1]) == 1005
    site_reads = [statement for statement in statements if "FROM field_observations" in statement]
    assert len(site_reads) == 3
    for statement in site_reads:
        values = statement.partition(" IN (")[2].partition(")")[0].split(",")
        assert len(values) <= 500
    amendment_reads = [statement for statement in statements if "FROM observation_amendments" in statement]
    assert len(amendment_reads) == 3
    for statement in amendment_reads:
        values = statement.partition(" IN (")[2].partition(")")[0].split(",")
        assert len(values) <= 500


@pytest.mark.parametrize(("row", "expected"), [
    ({"latitude": None, "longitude": None, "precision": "unknown",
      "location_basis": "map_reference", "uncertainty_m": None}, "unknown"),
    ({"latitude": 63.4, "longitude": 10.4, "precision": "approximate",
      "location_basis": "map_reference", "uncertainty_m": 25}, "recorded"),
    ({"latitude": 63.4, "longitude": 10.4, "precision": "unknown",
      "location_basis": "map_reference", "uncertainty_m": 25}, "unknown"),
    ({"latitude": 63.4, "longitude": 10.4, "precision": "exact",
      "location_basis": "llm_inference", "uncertainty_m": 0}, "unavailable"),
    ({"latitude": 63.4, "longitude": 10.4, "precision": "approximate",
      "location_basis": "historical_map", "uncertainty_m": 25}, "unavailable"),
    ({"latitude": 91, "longitude": 10.4, "precision": "approximate",
      "location_basis": "map_reference", "uncertainty_m": 25}, "unavailable"),
])
def test_location_dimension_distinguishes_valid_unknown_and_corrupt_storage(row, expected):
    assert _location_state(row) == expected


def test_quality_dimensions_filters_denominators_drilldown_and_read_only(quality_api):
    client, database = quality_api
    with database.connect() as connection:
        before = [tuple(row) for row in connection.execute(
            "SELECT id,status,revision FROM sites ORDER BY id"
        )]
    response = client.get("/api/quality/coverage", headers=ADMIN)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["denominators"] == {"total_catalogue": 4, "filtered_catalogue": 4}
    assert payload["dimensions"]["identity"]["filtered"]["source_backed"] == 1
    assert payload["dimensions"]["location"]["filtered"]["recorded"] == 1
    assert payload["dimensions"]["access"]["filtered"]["recorded"] == 1
    assert payload["dimensions"]["currentness"]["filtered"]["current"] == 1
    assert payload["dimensions"]["currentness"]["filtered"]["overdue"] == 1
    assert payload["dimensions"]["currentness"]["filtered"]["unknown"] == 2
    assert payload["dimensions"]["evidence"]["filtered"]["provenance_qualified"] == 3
    assert payload["source_dependence"]["filtered"] == {
        "none": 1, "single_source": 2, "multiple_sources": 1,
    }
    assert payload["questions"]["total"]["open"] == 1
    assert payload["questions"]["filtered"]["open"] == 1

    drilldown = client.get(
        "/api/quality/coverage?dimension=currentness&state=overdue", headers=ADMIN
    )
    assert drilldown.status_code == 200, drilldown.text
    details = drilldown.json()["drilldown"]
    assert details["membership_count"] == 1
    assert [item["site_id"] for item in details["items"]] == [3]
    assert details["items"][0]["detail"]["site_id"] == 3

    filtered_drilldown = client.get(
        "/api/quality/coverage?status=likely&dimension=currentness&state=overdue",
        headers=ADMIN,
    )
    filtered_payload = filtered_drilldown.json()
    assert filtered_drilldown.status_code == 200
    assert filtered_payload["dimensions"]["currentness"]["filtered"]["overdue"] == 1
    assert filtered_payload["drilldown"]["membership_count"] == 1
    assert [item["site_id"] for item in filtered_payload["drilldown"]["items"]] == [3]

    filtered = client.get("/api/quality/coverage?status=candidate", headers=ADMIN)
    assert filtered.status_code == 200
    assert filtered.json()["denominators"] == {"total_catalogue": 4, "filtered_catalogue": 3}
    assert filtered.json()["dimensions"]["identity"]["total_denominator"] == 4
    assert filtered.json()["dimensions"]["identity"]["filtered_denominator"] == 3
    with database.connect() as connection:
        after = [tuple(row) for row in connection.execute(
            "SELECT id,status,revision FROM sites ORDER BY id"
        )]
    assert after == before


def test_quality_route_requires_admin_and_rejects_unpaired_drilldown(quality_api):
    client, _ = quality_api
    assert client.get("/api/quality/coverage").status_code == 401
    assert client.get("/api/quality/coverage?dimension=identity", headers=ADMIN).status_code == 422
    assert client.get("/api/quality/coverage?dimension=not-a-dimension&state=unknown",
                      headers=ADMIN).status_code == 422


def test_500_record_import_effects_remain_idempotent_and_provenance_complete(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "bulk-import", admin_token="bulk-import"))
    with TestClient(app) as client:
        package = {
            "schema_version": "1.0", "batch_id": "catalogue-quality-500",
            "generated_at": "2026-01-01T00:00:00Z",
            "records": [{
                "external_key": f"synthetic:bulk:{i}", "name": f"Synthetic import {i}",
                "site_kind": "shelter",
                "geometry": {"latitude": 63.0 + i / 100000, "longitude": 10.0 + i / 100000},
                "precision": "approximate", "uncertainty_m": 30,
                "location_basis": "map_reference", "status": "candidate", "access": "unknown",
                "sources": [{"url": f"https://example.test/bulk/{i}", "title": f"Source {i}",
                             "source_type": "archive", "excerpt": f"Synthetic excerpt {i}"}],
                "confidence": "unknown", "short_rationale": "Synthetic 500-record effect fixture",
            } for i in range(500)],
        }
        bulk_admin = {"Authorization": "Bearer bulk-import"}
        preview = client.post("/api/admin/imports/preview", headers=bulk_admin, json=package)
        assert preview.status_code == 200, preview.text
        headers = {**bulk_admin, "X-Import-Preview": preview.json()["preview_hash"]}
        first = client.post("/api/admin/imports/commit", headers=headers, json=package)
        assert first.status_code == 200, first.text
        repeated = client.post("/api/admin/imports/commit", headers=bulk_admin, json=package)
        assert repeated.status_code == 200, repeated.text
        assert (first.json()["created"], first.json()["evidence_attached"], first.json()["idempotent"]) == (500, 500, False)
        assert repeated.json() == {
            "batch_id": "catalogue-quality-500", "created": 0, "updated": 0,
            "preserved": 0, "evidence_attached": 0, "idempotent": True,
        }
        with app.state.database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 500
            assert connection.execute("SELECT COUNT(*) FROM import_records").fetchone()[0] == 500
            assert connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 500
