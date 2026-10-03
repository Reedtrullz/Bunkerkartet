from __future__ import annotations

from copy import deepcopy
import json

import pytest
from fastapi import FastAPI, HTTPException
from started_client import StartedClient

from app.db import Database, canonical_payload_hash
from app.exchange_workflows import MAX_BODY_BYTES, install_exchange_routes
from app.imports import validate_import_package


def _database(tmp_path, sites=2):
    database = Database(tmp_path / "exchange.sqlite")
    database.initialize()
    with database.connect() as connection:
        for site_id in range(1, sites + 1):
            connection.execute(
                """INSERT INTO sites
                   (id, external_key, name, site_kind, latitude, longitude,
                    precision, uncertainty_m, location_basis, status, access,
                    created_at, updated_at, revision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    site_id,
                    f"catalogue:{site_id}",
                    f"Site {site_id}",
                    "bunker",
                    63.4 + site_id / 100,
                    10.4 + site_id / 100,
                    "approximate",
                    80.0,
                    "map_reference",
                    "candidate",
                    "unknown",
                    "2026-10-03T10:00:00Z",
                    "2026-10-03T10:00:00Z",
                    1,
                ),
            )
    return database


def _site_details():
    return {
        1: {
            "id": 1,
            "external_key": "catalogue:1",
            "name": "A <script>alert('x')</script>",
            "site_kind": "bunker",
            "status": "candidate",
            "access": "unknown",
            "latitude": None,
            "longitude": None,
            "precision": "unknown",
            "uncertainty_m": None,
            "location_basis": "landmark_description",
            "updated_at": "2026-10-03T10:00:00Z",
            "data_status": "valid",
            "content_status": "current",
            "warnings": ["Location needs review"],
            "sources": [
                {
                    "source_id": 11,
                    "url": "https://example.test/source?a=1&b=2",
                    "title": "Evidence <primary>",
                    "source_type": "archive",
                    "excerpt": "A bounded source excerpt.",
                    "published_at": "1950-01-01",
                    "accessed_at": "2026-10-03",
                    "rights_status": "unknown",
                    "rights_note": "Permission is not recorded.",
                }
            ],
            "enrichment": {
                "claims": [
                    {
                        "id": "claim-1",
                        "text": "The entrance may be on the north side.",
                        "section": "uncertainty",
                        "certainty": "uncertain",
                        "source_ids": ["archive-claim-source"],
                        "sources": [
                            {
                                "id": "archive-claim-source",
                                "title": "Claim <source>",
                                "url": "https://example.test/claim-source",
                                "accessed_at": "2026-10-03",
                                "unallowlisted_metadata": "must not escape",
                            }
                        ],
                        "secret": "must not escape the allowlist",
                    }
                ],
                "secret": "must not be exported",
            },
            "approach_latitude": 63.41,
            "approach_longitude": 10.41,
            "approach_access": "restricted",
            "approach_note": "Use the marked public path.",
            "approach_reviewed_at": "2026-09-30T12:00:00Z",
            "field_observations": [
                {
                    "id": 701,
                    "site_id": 1,
                    "observed_at": "2026-09-01T09:00:00Z",
                    "outcome": "not_found",
                    "note": "Private field note",
                    "latitude": 63.42,
                    "longitude": 10.42,
                    "point_role": "observation",
                    "uncertainty_m": 15.0,
                    "access_notes": "Private access note",
                    "extra": "must not escape the allowlist",
                }
            ],
            "observation_points": [
                {"id": 701, "latitude": 63.42, "longitude": 10.42, "point_role": "observation"}
            ],
        },
        2: {
            "id": 2,
            "external_key": "catalogue:2",
            "name": "Site 2",
            "site_kind": "shelter",
            "status": "trusted",
            "access": "public",
            "latitude": 64.0,
            "longitude": 11.0,
            "precision": "approximate",
            "uncertainty_m": 30.0,
            "location_basis": "explicit_coordinate",
            "updated_at": "2026-10-02T10:00:00Z",
            "data_status": "valid",
            "content_status": "historical_metadata_unknown",
            "sources": [],
            "enrichment": None,
            "field_observations": [],
            "observation_points": [],
            "approach_latitude": None,
            "approach_longitude": None,
            "approach_access": None,
            "approach_note": None,
            "approach_reviewed_at": None,
        },
    }


def _package(schema_version="1.0"):
    source = {
        "url": "https://example.test/evidence/a",
        "title": "Original evidence",
        "source_type": "archive",
        "excerpt": "The original source text remains unchanged.",
        "access_date": "2026-10-01",
    }
    if schema_version == "1.1":
        source.update(
            {
                "content_kind": "summary",
                "role": "location",
                "claim_ids": ["claim-1"],
                "uncertainty_note": "Approximate description",
                "rights_status": "unknown",
                "rights_note": None,
            }
        )
    return {
        "schema_version": schema_version,
        "batch_id": f"parent-{schema_version}",
        "generated_at": "2026-10-01T12:00:00Z",
        "records": [
            {
                "external_key": "source:a",
                "name": "Record A",
                "site_kind": "bunker",
                "geometry": {"latitude": 63.5, "longitude": 10.5},
                "precision": "approximate",
                "uncertainty_m": 50.0,
                "location_basis": "map_reference",
                "status": "candidate",
                "access": "unknown",
                "sources": [deepcopy(source)],
                "related_site_keys": ["source:b"],
            },
            {
                "external_key": "source:b",
                "name": "Record B",
                "site_kind": "shelter",
                "geometry": None,
                "precision": "unknown",
                "uncertainty_m": None,
                "location_basis": "landmark_description",
                "status": "candidate",
                "access": "unknown",
                "sources": [deepcopy(source)],
            },
        ],
    }


def _installed_app(tmp_path, *, details=None, question_loader=None, preview_package=None,
                   commit_package=None, commit_location_edits=None):
    database = _database(tmp_path)
    details = details if details is not None else _site_details()
    app = FastAPI()
    app.state.writes = []

    def site_detail(connection, row):
        return deepcopy(details[int(row["id"])])

    def default_preview(connection, package):
        package_value = package.model_dump(mode="json")
        return {
            "batch_id": package.batch_id,
            "records": [{"external_key": row.external_key} for row in package.records],
            "preview_hash": canonical_payload_hash(package_value),
        }

    def default_commit(database_arg, package, native_preview_hash, provenance):
        result = {
            "batch_id": package.batch_id,
            "preview_hash": native_preview_hash,
            "provenance": provenance,
        }
        app.state.writes.append(result)
        return result

    install_exchange_routes(
        app,
        database,
        lambda: None,
        site_detail,
        preview_package or default_preview,
        commit_package or default_commit,
        load_private_questions=question_loader,
        commit_location_edits=commit_location_edits,
    )
    return app, database, details


def test_dossier_preview_and_download_share_exact_allowlisted_private_snapshot(tmp_path):
    question_calls = []
    # Default dossier generation must not even load the private queue.
    app, database, details = _installed_app(
        tmp_path / "second", question_loader=lambda *args: question_calls.append(args) or []
    )
    request = {"site_ids": [1, 2], "title": "Field <plan>"}
    client = StartedClient(app)
    preview = client.post("/api/admin/exchanges/dossiers/preview", json=request)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["dossier"]["site_ids"] == [1, 2]
    assert result["dossier"]["sites"][0]["geometry"] is None
    assert result["geojson"]["features"][0]["geometry"] is None
    assert result["dossier"]["private_start"] is None
    assert result["dossier"]["private_questions"] == []
    assert result["dossier"]["private_observations"] == []
    assert "secret" not in json.dumps(result)
    assert "unallowlisted_metadata" not in json.dumps(result)
    assert "Private field note" not in json.dumps(result)
    assert "Field &lt;plan&gt;" in result["html"]
    assert "Evidence &lt;primary&gt;" in result["html"]
    assert "Claim &lt;source&gt;" in result["html"]
    assert "<script>alert('x')</script>" not in result["html"]
    assert result["dossier"]["currentness"] in result["html"]
    assert result["dossier"]["permission_caveat"]
    assert result["dossier"]["permission_caveat"] in result["html"]
    assert result["dossier"]["currentness"]
    assert question_calls == []

    download = client.post(
        "/api/admin/exchanges/dossiers/download",
        json={**request, "preview_hash": result["preview_hash"], "generated_at": result["dossier"]["generated_at"]},
    )
    assert download.status_code == 200, download.text
    assert download.json()["dossier"] == result["dossier"]
    assert download.json()["html"] == result["html"]
    assert download.json()["geojson"] == result["geojson"]
    assert download.json()["source_hash"] == result["source_hash"]

    details[1]["name"] = "Changed after preview"
    stale = client.post(
        "/api/admin/exchanges/dossiers/download",
        json={**request, "preview_hash": result["preview_hash"], "generated_at": result["dossier"]["generated_at"]},
    )
    assert stale.status_code == 409

    with database.connect() as connection:
        assert connection.execute("SELECT status FROM sites WHERE id=1").fetchone()[0] == "candidate"


def test_dossier_sensitive_fields_require_explicit_exact_opt_ins(tmp_path):
    question_rows = [
        {"id": "q-1", "site_id": 1, "prompt": "Confirm the east entrance", "state": "open", "revision": 2,
         "updated_at": "2026-09-28T10:00:00Z", "private_discussion": "excluded by allowlist"}
    ]
    calls = []

    def questions(connection, site_ids, question_ids):
        calls.append((site_ids, question_ids))
        return deepcopy(question_rows)

    app, _, _ = _installed_app(tmp_path, question_loader=questions)
    base = {
        "site_ids": [1],
        "include_private_start": True,
        "private_start": {"latitude": 63.0, "longitude": 10.0, "label": "Meet here"},
        "include_private_questions": True,
        "question_ids": ["q-1"],
        "include_private_observations": True,
        "observation_ids": [701],
    }
    client = StartedClient(app)
    response = client.post("/api/admin/exchanges/dossiers/preview", json=base)
    assert response.status_code == 200, response.text
    dossier = response.json()["dossier"]
    assert dossier["private_start"] == {"latitude": 63.0, "longitude": 10.0, "label": "Meet here"}
    assert [item["id"] for item in dossier["private_questions"]] == ["q-1"]
    assert dossier["private_observations"][0]["id"] == 701
    assert response.json()["gpx_status"].startswith("available: waypoints only")
    assert "<wpt" in response.json()["gpx"]
    assert "<trk" not in response.json()["gpx"]
    assert "private_discussion" not in json.dumps(dossier)
    assert calls == [((1,), ("q-1",))]

    invalid = client.post(
        "/api/admin/exchanges/dossiers/preview",
        json={"site_ids": [1], "private_start": {"latitude": 63.0, "longitude": 10.0, "label": "hidden"}},
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize("schema_version", ["1.0", "1.1"])
def test_subset_preview_and_commit_preserve_parent_hash_membership_and_references(tmp_path, schema_version):
    app, _, _ = _installed_app(tmp_path)
    original = _package(schema_version)
    original_before = deepcopy(original)
    client = StartedClient(app)
    request = {
        "source_package": original,
        "selected_keys": ["source:a"],
        "new_batch_id": f"subset-{schema_version}",
        "reason": "Keep the reviewed record; defer the linked record.",
    }
    preview = client.post("/api/admin/exchanges/import-subsets/preview", json=request)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["manifest"]["parent_package_hash"] == canonical_payload_hash(
        validate_import_package(original).model_dump(mode="json")
    )
    assert result["manifest"]["selected_keys"] == ["source:a"]
    assert result["manifest"]["unselected_keys"] == ["source:b"]
    assert result["manifest"]["unselected_referenced_keys"] == ["source:b"]
    assert result["package"]["batch_id"] == f"subset-{schema_version}"
    assert result["package"]["schema_version"] == schema_version
    assert [record["external_key"] for record in result["package"]["records"]] == ["source:a"]
    assert result["package"]["records"][0]["related_site_keys"] == ["source:b"]
    assert original == original_before

    committed = client.post(
        "/api/admin/exchanges/import-subsets/commit",
        json={
            **request,
            "package": result["package"],
            "selection_hash": result["selection_hash"],
            "preview_hash": result["preview_hash"],
        },
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["result"]["provenance"]["parent_package_hash"] == result["manifest"]["parent_package_hash"]
    assert committed.json()["result"]["provenance"]["selected_keys"] == ["source:a"]
    assert len(app.state.writes) == 1


def test_subset_selection_change_invalidates_preview_and_never_calls_commit(tmp_path):
    app, _, _ = _installed_app(tmp_path)
    package = _package()
    client = StartedClient(app)
    original_selection = {
        "source_package": package,
        "selected_keys": ["source:a"],
        "new_batch_id": "subset-change",
        "reason": "First selection",
    }
    preview = client.post("/api/admin/exchanges/import-subsets/preview", json=original_selection).json()
    changed = client.post(
        "/api/admin/exchanges/import-subsets/commit",
        json={
            **{**original_selection, "selected_keys": ["source:b"]},
            "package": preview["package"],
            "selection_hash": preview["selection_hash"],
            "preview_hash": preview["preview_hash"],
        },
    )
    assert changed.status_code == 409
    assert app.state.writes == []


def test_independent_subsets_keep_the_same_immutable_parent_receipt(tmp_path):
    app, _, _ = _installed_app(tmp_path)
    source_package = _package("1.0")
    client = StartedClient(app)
    first = client.post(
        "/api/admin/exchanges/import-subsets/preview",
        json={
            "source_package": source_package,
            "selected_keys": ["source:a"],
            "new_batch_id": "selection-a",
            "reason": "Review the first independent selection.",
        },
    )
    second = client.post(
        "/api/admin/exchanges/import-subsets/preview",
        json={
            "source_package": source_package,
            "selected_keys": ["source:b"],
            "new_batch_id": "selection-b",
            "reason": "Review the second independent selection.",
        },
    )
    assert first.status_code == second.status_code == 200
    first_manifest, second_manifest = first.json()["manifest"], second.json()["manifest"]
    assert first_manifest["parent_package_hash"] == second_manifest["parent_package_hash"]
    assert first_manifest["new_batch_id"] != second_manifest["new_batch_id"]
    assert first_manifest["selected_keys"] == ["source:a"]
    assert second_manifest["selected_keys"] == ["source:b"]
    assert first.json()["selection_hash"] != second.json()["selection_hash"]
    assert first_manifest["parent_keys"] == ["source:a", "source:b"]


def _export_gis_pack(client, site_ids=(1,)):
    response = client.post("/api/admin/exchanges/gis/edit-packs", json={"site_ids": list(site_ids)})
    assert response.status_code == 200, response.text
    return response.json()


def test_gis_preview_commit_is_revision_bound_and_delegates_only_changed_coordinates(tmp_path):
    observed = []
    app_holder = {}

    def native_guard(database, edits, reason, preview_hash):
        observed.append((deepcopy(edits), reason, preview_hash))
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for edit in edits:
                changed = connection.execute(
                    """UPDATE sites SET latitude=?, longitude=?, location_review_required=1,
                       revision=revision+1, updated_at='2026-10-03T12:00:00Z'
                       WHERE id=? AND revision=?""",
                    (edit["new"][1], edit["new"][0], edit["site_id"], edit["expected_revision"]),
                )
                if changed.rowcount != 1:
                    raise RuntimeError("native revision guard rejected stale site")
        return {"updated": len(edits), "location_review_required": True}

    app, database, _ = _installed_app(tmp_path, commit_location_edits=native_guard)
    client = StartedClient(app)
    pack = _export_gis_pack(client)
    feature = deepcopy(pack["features"][0])
    feature["geometry"] = {"type": "Point", "coordinates": [12.25, 64.25]}
    request = {
        "source_pack": pack,
        "edited_features": [feature],
        "reason": "Compare the coordinate against the cited 1950 map.",
        "axis_order_confirmation": "longitude_latitude",
    }
    preview = client.post("/api/admin/exchanges/gis/edit-previews", json=request)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["edits"] == [
        {
            "site_id": 1,
            "external_key": "catalogue:1",
            "expected_revision": 1,
            "old": [10.41, 63.41],
            "new": [12.25, 64.25],
        }
    ]
    committed = client.post(
        "/api/admin/exchanges/gis/edit-commits",
        json={**request, "preview_hash": result["preview_hash"]},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["updated"] == 1
    assert observed[0][0][0]["expected_revision"] == 1
    with database.connect() as connection:
        row = connection.execute("SELECT latitude, longitude, revision, location_review_required FROM sites WHERE id=1").fetchone()
    assert tuple(row) == (64.25, 12.25, 2, 1)


def test_gis_noop_and_tampered_role_or_out_of_range_geometry_fail_closed(tmp_path):
    writes = []
    app, _, _ = _installed_app(tmp_path, commit_location_edits=lambda *args: writes.append(args))
    client = StartedClient(app)
    pack = _export_gis_pack(client)
    unchanged = deepcopy(pack["features"])
    request = {
        "source_pack": pack,
        "edited_features": unchanged,
        "reason": "Confirm the returned feature without coordinate changes.",
        "axis_order_confirmation": "longitude_latitude",
    }
    preview = client.post("/api/admin/exchanges/gis/edit-previews", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["edits"] == []
    commit = client.post(
        "/api/admin/exchanges/gis/edit-commits",
        json={**request, "preview_hash": preview.json()["preview_hash"]},
    )
    assert commit.status_code == 200
    assert commit.json()["updated"] == 0
    assert writes == []

    role_changed = deepcopy(pack["features"])
    role_changed[0]["properties"]["point_role"] = "approach"
    bad_role = client.post(
        "/api/admin/exchanges/gis/edit-previews",
        json={**request, "edited_features": role_changed},
    )
    assert bad_role.status_code == 422

    out_of_range = deepcopy(pack["features"])
    out_of_range[0]["geometry"] = {"type": "Point", "coordinates": [10.4, 145.0]}
    bad_coordinate = client.post(
        "/api/admin/exchanges/gis/edit-previews",
        json={**request, "edited_features": out_of_range},
    )
    assert bad_coordinate.status_code == 422
    assert writes == []


def test_gis_stale_source_revision_rejects_commit_before_callback(tmp_path):
    writes = []
    app, database, _ = _installed_app(tmp_path, commit_location_edits=lambda *args: writes.append(args))
    client = StartedClient(app)
    pack = _export_gis_pack(client)
    feature = deepcopy(pack["features"][0])
    feature["geometry"] = {"type": "Point", "coordinates": [12.0, 64.0]}
    request = {
        "source_pack": pack,
        "edited_features": [feature],
        "reason": "Review map position.",
        "axis_order_confirmation": "longitude_latitude",
    }
    preview = client.post("/api/admin/exchanges/gis/edit-previews", json=request).json()
    with database.connect() as connection:
        connection.execute("UPDATE sites SET revision=revision+1 WHERE id=1")
    response = client.post(
        "/api/admin/exchanges/gis/edit-commits",
        json={**request, "preview_hash": preview["preview_hash"]},
    )
    assert response.status_code == 409
    assert writes == []


def test_gis_requires_axis_attestation_and_never_guesses_valid_ambiguous_pairs(tmp_path):
    app, _, _ = _installed_app(tmp_path)
    client = StartedClient(app)
    pack = _export_gis_pack(client)
    feature = deepcopy(pack["features"][0])
    # This is the numeric transpose of the source [lon, lat], but both values
    # remain legal in either axis. The workflow must preserve the declared
    # GeoJSON order, never guess that this edit should be reversed.
    feature["geometry"] = {"type": "Point", "coordinates": [63.41, 10.41]}
    request = {
        "source_pack": pack,
        "edited_features": [feature],
        "reason": "Explicitly review coordinate order against the map.",
    }
    missing_attestation = client.post("/api/admin/exchanges/gis/edit-previews", json=request)
    assert missing_attestation.status_code == 422

    request["axis_order_confirmation"] = "longitude_latitude"
    preview = client.post("/api/admin/exchanges/gis/edit-previews", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["edits"][0]["new"] == [63.41, 10.41]
    commit = client.post(
        "/api/admin/exchanges/gis/edit-commits",
        json={**request, "preview_hash": preview.json()["preview_hash"]},
    )
    assert commit.status_code == 501


def test_exchange_routes_are_admin_guarded_and_reject_oversized_or_extra_fields(tmp_path):
    app, _, _ = _installed_app(tmp_path)
    client = StartedClient(app)
    extra = client.post(
        "/api/admin/exchanges/dossiers/preview",
        json={"site_ids": [1], "unexpected": "field"},
    )
    assert extra.status_code == 422
    bool_id = client.post("/api/admin/exchanges/dossiers/preview", json={"site_ids": [True]})
    assert bool_id.status_code == 422
    duplicate_keys = client.post(
        "/api/admin/exchanges/dossiers/preview",
        content=b'{"site_ids":[1],"site_ids":[2]}',
        headers={"content-type": "application/json"},
    )
    assert duplicate_keys.status_code == 422
    oversized = client.post(
        "/api/admin/exchanges/dossiers/preview",
        content=b'{"site_ids":[1],"padding":"' + b"x" * MAX_BODY_BYTES + b'"}',
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413

    unauthenticated_app = FastAPI()
    database = _database(tmp_path / "unauthenticated")
    install_exchange_routes(
        unauthenticated_app,
        database,
        lambda: (_ for _ in ()).throw(HTTPException(status_code=401, detail="unauthorized")),
        lambda *_: {},
        lambda *_: {},
        lambda *_: {},
    )
    unauthenticated_client = StartedClient(unauthenticated_app)
    response = unauthenticated_client.post("/api/admin/exchanges/dossiers/preview", json={"site_ids": [1]})
    assert response.status_code == 401
