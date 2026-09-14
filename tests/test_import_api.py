import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import SCHEMA
from app.imports import validate_import_package
from app.main import create_app


def package(batch_id="batch-1", *, name="Leira bunker", external_key="forum:1"):
    return {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "generated_at": "2026-09-13T12:00:00Z",
        "records": [
            {
                "external_key": external_key,
                "name": name,
                "site_kind": "bunker",
                "geometry": {"latitude": 63.4, "longitude": 10.4},
                "precision": "approximate",
                "uncertainty_m": 80,
                "location_basis": "llm_inference",
                "status": "candidate",
                "access": "unknown",
                "sources": [
                    {
                        "url": "https://example.com/forum/1",
                        "title": "Forum finding",
                        "source_type": "forum",
                        "excerpt": "A bunker is described near the ridge.",
                        "access_date": "2026-09-13",
                    }
                ],
                "confidence": "medium",
                "short_rationale": "The landmark description points to the ridge.",
            }
        ],
    }


def client(tmp_path, *, token="secret"):
    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                admin_token=token,
                app_version="test-sha",
            )
        )
    )


def auth(token="secret"):
    return {"Authorization": f"Bearer {token}"}


def commit_previewed(api, payload, *, token="secret"):
    preview = api.post("/api/admin/imports/preview", headers=auth(token), json=payload)
    assert preview.status_code == 200, preview.text
    return api.post(
        "/api/admin/imports/commit",
        headers={**auth(token), "X-Import-Preview": preview.json()["preview_hash"]},
        json=payload,
    )


def test_import_preview_and_idempotent_commit(tmp_path):
    api = client(tmp_path)
    payload = package()

    preview = api.post(
        "/api/admin/imports/preview", headers=auth(), json=payload
    )
    assert preview.status_code == 200
    assert preview.json()["summary"] == {
        "total": 1,
        "new": 1,
        "update_candidate": 0,
        "preserve_trusted": 0,
        "warnings": 0,
    }

    committed = commit_previewed(api, payload)
    assert committed.status_code == 200
    assert committed.json()["created"] == 1
    assert committed.json()["idempotent"] is False

    repeated = api.post(
        "/api/admin/imports/commit", headers=auth(), json=payload
    )
    assert repeated.status_code == 200
    assert repeated.json() == {
        "batch_id": "batch-1",
        "created": 0,
        "updated": 0,
        "preserved": 0,
        "evidence_attached": 0,
        "idempotent": True,
    }

    sites = api.get("/api/sites", headers=auth())
    assert sites.status_code == 200
    assert len(sites.json()) == 1
    assert sites.json()[0]["status"] == "candidate"


def test_related_site_keys_are_visible_and_resolve_when_target_arrives_later(tmp_path):
    api = client(tmp_path)
    first = package()
    first["records"][0]["related_site_keys"] = ["forum:later"]

    assert commit_previewed(api, first).status_code == 200
    relation = api.get("/api/sites/1", headers=auth()).json()["relations"][0]
    assert relation == {
        "related_external_key": "forum:later",
        "relation_kind": "related",
        "related_site_id": None,
        "related_name": None,
        "related_status": "not_imported",
    }

    later = package("batch-later", external_key="forum:later", name="Later site")
    assert commit_previewed(api, later).status_code == 200
    relation = api.get("/api/sites/1", headers=auth()).json()["relations"][0]
    assert relation["related_site_id"] == 2
    assert relation["related_name"] == "Later site"
    assert relation["related_status"] == "candidate"


def test_related_site_key_cannot_point_to_itself(tmp_path):
    api = client(tmp_path)
    payload = package()
    payload["records"][0]["related_site_keys"] = [payload["records"][0]["external_key"]]

    response = api.post("/api/admin/imports/preview", headers=auth(), json=payload)

    assert response.status_code == 422
    assert "itself" in response.text


def test_duplicate_warning_normalizes_name_and_marks_overlap_as_possible_relation(tmp_path):
    api = client(tmp_path)
    first = package(name="Same  bunker")
    assert commit_previewed(api, first).status_code == 200
    second = package("batch-overlap", external_key="forum:2", name="Nearby bunker")
    second["records"][0]["geometry"] = {"latitude": 63.4015, "longitude": 10.4002}
    second["records"][0]["uncertainty_m"] = 100

    preview = api.post("/api/admin/imports/preview", headers=auth(), json=second)

    assert preview.status_code == 200
    warnings = preview.json()["records"][0]["warnings"]
    assert any("possible relation" in warning and "not proof" in warning for warning in warnings)


def test_new_commit_requires_a_preview_header(tmp_path):
    api = client(tmp_path)

    response = api.post("/api/admin/imports/commit", headers=auth(), json=package())

    assert response.status_code == 409
    assert "fresh preview" in response.text


def test_commit_rejects_stale_preview_after_site_change(tmp_path):
    api = client(tmp_path)
    first = package()
    assert commit_previewed(api, first).status_code == 200
    candidate_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    second = package("batch-2")
    preview = api.post("/api/admin/imports/preview", headers=auth(), json=second).json()
    assert api.patch(
        f"/api/sites/{candidate_id}", headers=auth(), json={"name": "Curator name"}
    ).status_code == 200

    response = api.post(
        "/api/admin/imports/commit",
        headers={**auth(), "X-Import-Preview": preview["preview_hash"]},
        json=second,
    )

    assert response.status_code == 409
    assert api.get(f"/api/sites/{candidate_id}", headers=auth()).json()["name"] == "Curator name"


def test_preview_exposes_stable_payload_hash(tmp_path):
    api = client(tmp_path)
    first = package()
    second = json.loads(json.dumps(first))
    second["records"][0]["short_rationale"] = "Changed rationale."

    first_preview = api.post("/api/admin/imports/preview", headers=auth(), json=first)
    second_preview = api.post("/api/admin/imports/preview", headers=auth(), json=second)

    assert first_preview.status_code == second_preview.status_code == 200
    assert len(first_preview.json()["payload_hash"]) == 64
    assert first_preview.json()["payload_hash"] != second_preview.json()["payload_hash"]


def test_preview_effect_is_deterministic_when_record_order_changes(tmp_path):
    api = client(tmp_path)
    first = package()
    second = package("batch-2", external_key="forum:2", name="Second bunker")
    payload = {**first, "records": [first["records"][0], second["records"][0]]}
    reversed_payload = {**payload, "records": list(reversed(payload["records"]))}

    forward = api.post("/api/admin/imports/preview", headers=auth(), json=payload).json()
    reverse = api.post("/api/admin/imports/preview", headers=auth(), json=reversed_payload).json()

    assert forward["payload_hash"] == reverse["payload_hash"]
    assert forward["preview_hash"] == reverse["preview_hash"]
    assert [record["external_key"] for record in forward["records"]] == ["forum:1", "forum:2"]


def test_peer_duplicate_warnings_match_preview_and_persisted_sites(tmp_path):
    api = client(tmp_path)
    first = package("batch-peer-1", external_key="peer:1", name="Same bunker")
    second = package("batch-peer-2", external_key="peer:2", name="Same bunker")
    second["records"][0]["geometry"] = {"latitude": 63.4005, "longitude": 10.4005}
    payload = {**first, "records": [first["records"][0], second["records"][0]]}

    preview = api.post("/api/admin/imports/preview", headers=auth(), json=payload)
    assert preview.status_code == 200
    preview_warnings = {
        record["external_key"]: record["warnings"] for record in preview.json()["records"]
    }
    assert all(warnings for warnings in preview_warnings.values())

    assert api.post(
        "/api/admin/imports/commit",
        headers={**auth(), "X-Import-Preview": preview.json()["preview_hash"]},
        json=payload,
    ).status_code == 200
    persisted = {
        site["external_key"]: site["warnings"]
        for site in api.get("/api/sites", headers=auth()).json()
    }
    assert persisted == preview_warnings


def test_same_batch_with_different_payload_is_rejected(tmp_path):
    api = client(tmp_path)
    first = package()
    assert commit_previewed(api, first).status_code == 200
    changed = json.loads(json.dumps(first))
    changed["records"][0]["sources"][0]["excerpt"] = "Different excerpt."

    response = commit_previewed(api, changed)

    assert response.status_code == 409
    assert api.get("/api/sites", headers=auth()).json()[0]["name"] == "Leira bunker"


def test_same_source_url_keeps_each_import_excerpt(tmp_path):
    api = client(tmp_path)
    first = package()
    first["records"][0]["sources"][0]["excerpt"] = "First site-specific excerpt."
    second = package("batch-2")
    second["records"][0]["sources"][0]["excerpt"] = "Second site-specific excerpt."

    assert commit_previewed(api, first).status_code == 200
    assert commit_previewed(api, second).status_code == 200

    sources = api.get("/api/sites/1", headers=auth()).json()["sources"]
    assert [source["excerpt"] for source in sources] == [
        "First site-specific excerpt.",
        "Second site-specific excerpt.",
    ]


def test_legacy_credential_url_is_withheld_from_detail(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    sentinel = "synthetic-legacy-secret"
    with sqlite3.connect(tmp_path / "bunkerkartet.sqlite3") as connection:
        connection.execute(
            "UPDATE sources SET url = ? WHERE url = ?",
            (f"https://example.com/map?token={sentinel}", "https://example.com/forum/1"),
        )

    response = api.get("/api/sites/1", headers=auth())

    assert response.status_code == 200
    source = response.json()["sources"][0]
    assert source["url"] is None
    assert source["url_status"] == "legacy reference withheld; review required"
    assert sentinel not in response.text


def test_legacy_credential_photo_url_is_withheld_from_detail(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    sentinel = "synthetic-legacy-photo-secret"
    observation = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={"observed_at": "2026-09-14", "outcome": "found", "note": "Observed."},
    )
    observation_id = observation.json()["observation"]["id"]
    with sqlite3.connect(tmp_path / "bunkerkartet.sqlite3") as connection:
        connection.execute(
            "UPDATE field_observations SET photo_urls_json = ? WHERE id = ?",
            (json.dumps([f"https://example.com/photo?token={sentinel}"]), observation_id),
        )

    response = api.get(f"/api/sites/{site_id}", headers=auth())

    assert response.status_code == 200
    observation = response.json()["field_observations"][0]
    assert observation["photo_urls"] == []
    assert observation["photo_urls_status"] == "legacy reference withheld; review required"
    assert sentinel not in response.text


@pytest.mark.parametrize("shared_url", [True, False])
def test_merge_preserves_evidence_after_v1_migration(tmp_path, shared_url):
    path = tmp_path / "bunkerkartet.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        for site_id in (1, 2):
            connection.execute(
                "INSERT INTO sites (external_key, name, site_kind, precision, location_basis, created_at, updated_at) VALUES (?, ?, 'bunker', 'unknown', 'landmark_description', '2026-09-14', '2026-09-14')",
                (f"legacy:{site_id}", f"Legacy {site_id}"),
            )
            url = "https://example.com/shared" if shared_url else f"https://example.com/source-{site_id}"
            source_id = connection.execute(
                "SELECT id FROM sources WHERE url = ?", (url,)
            ).fetchone()
            if source_id is None:
                source_id = connection.execute(
                    "INSERT INTO sources (url, title, source_type, excerpt, created_at, updated_at) VALUES (?, 'Legacy source', 'test', 'Overwritten', '2026-09-14', '2026-09-14')",
                    (url,),
                ).lastrowid
            else:
                source_id = source_id[0]
            batch_id = f"legacy-batch-{site_id}"
            connection.execute(
                "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES (?, '1.0', '2026-09-14T00:00:00+00:00', '2026-09-14', '2026-09-14')",
                (batch_id,),
            )
            connection.execute(
                "INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at) VALUES (?, ?, ?, 'created', ?, '2026-09-14')",
                (
                    batch_id,
                    f"legacy:{site_id}",
                    site_id,
                    json.dumps({
                        "external_key": f"legacy:{site_id}",
                        "sources": [{
                            "url": url,
                            "title": "Legacy source",
                            "source_type": "test",
                            "excerpt": f"Site {site_id} evidence",
                            "publication_date": None,
                            "access_date": None,
                        }],
                    }),
                ),
            )
            connection.execute(
                "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (?, ?, 'source', '2026-09-14')",
                (site_id, source_id),
            )

    api = client(tmp_path)
    sites = api.get("/api/sites", headers=auth()).json()
    response = api.post(
        f"/api/sites/{sites[0]['id']}/review",
        headers=auth(),
        json={"action": "merge", "target_site_id": sites[1]["id"]},
    )

    assert response.status_code == 200
    target = api.get(f"/api/sites/{sites[1]['id']}", headers=auth()).json()
    assert len(target["sources"]) == 2
    with api.app.state.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence_items WHERE site_id = ?", (sites[1]["id"],)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence_items WHERE legacy_evidence_id IS NOT NULL"
        ).fetchone()[0] == 0


def test_legacy_batch_hash_is_reconstructed_for_exact_retry(tmp_path):
    path = tmp_path / "bunkerkartet.sqlite3"
    payload = package()
    record = validate_import_package(payload).records[0].model_dump(mode="json")
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES ('batch-1', '1.0', '2026-09-13T12:00:00+00:00', '2026-09-14', '2026-09-14')"
        )
        connection.execute(
            "INSERT INTO import_records (batch_id, external_key, action, payload_json, created_at) VALUES ('batch-1', 'forum:1', 'created', ?, '2026-09-14')",
            (json.dumps(record),),
        )

    api = client(tmp_path)
    response = commit_previewed(api, payload)

    assert response.status_code == 200
    assert response.json()["idempotent"] is True


def test_legacy_batch_without_reconstructable_records_is_explicitly_unverifiable(tmp_path):
    path = tmp_path / "bunkerkartet.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at) VALUES ('batch-1', '1.0', '2026-09-13T12:00:00+00:00', '2026-09-14', '2026-09-14')"
        )

    api = client(tmp_path)
    response = commit_previewed(api, package())

    assert response.status_code == 409
    assert "cannot be verified" in response.text


def test_import_rejects_duplicate_external_keys_before_commit(tmp_path):
    api = client(tmp_path)
    payload = package()
    payload["records"].append({**payload["records"][0], "name": "Second bunker"})

    for endpoint in ("preview", "commit"):
        response = api.post(
            f"/api/admin/imports/{endpoint}", headers=auth(), json=payload
        )
        assert response.status_code == 422
        assert "duplicate external_key" in str(response.json()["detail"])

    assert api.get("/api/sites", headers=auth()).json() == []


def test_import_preserves_trusted_fields_and_attaches_new_evidence(tmp_path):
    api = client(tmp_path)
    first = package()
    assert commit_previewed(api, first).status_code == 200

    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    assert api.post(
        f"/api/sites/{site_id}/review", headers=auth(), json={"action": "research"}
    ).status_code == 200
    assert api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Feature observed from the public path.",
        },
    ).status_code == 201
    assert api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "field_verify"},
    ).status_code == 200
    assert api.post(
        f"/api/sites/{site_id}/review", headers=auth(), json={"action": "confirm"}
    ).status_code == 200

    edited = api.patch(
        f"/api/sites/{site_id}",
        headers=auth(),
        json={
            "name": "Curated bunker",
            "latitude": 63.401,
            "longitude": 10.401,
            "precision": "exact",
            "uncertainty_m": 5,
            "location_basis": "explicit_coordinate",
        },
    )
    assert edited.status_code == 200

    second = package("batch-2", name="Imported replacement")
    second["records"][0]["sources"][0]["url"] = "https://example.com/forum/2"
    committed = commit_previewed(api, second)
    assert committed.status_code == 200
    assert committed.json()["preserved"] == 1
    assert committed.json()["evidence_attached"] == 1

    site = api.get(f"/api/sites/{site_id}", headers=auth()).json()
    assert site["name"] == "Curated bunker"
    assert site["status"] == "trusted"
    assert site["latitude"] == 63.401
    assert site["location_review_required"] == 1
    assert len(site["sources"]) == 2


def test_import_does_not_erase_candidate_location_or_cautions_when_new_source_has_no_point(tmp_path):
    api = client(tmp_path)
    first = package()
    first["records"][0].update(
        warnings=["Do not enter"],
        condition="Current condition unknown",
        confidence="medium",
    )
    assert commit_previewed(api, first).status_code == 200

    second = package("batch-2")
    second["records"][0].update(
        geometry=None,
        precision="unknown",
        uncertainty_m=None,
        location_basis="landmark_description",
        confidence=None,
        condition=None,
        short_rationale=None,
        warnings=[],
        access="unknown",
    )
    second["records"][0]["sources"][0]["url"] = "https://example.com/forum/no-point"

    response = commit_previewed(api, second)

    assert response.status_code == 200
    site = api.get("/api/sites/1", headers=auth()).json()
    assert site["latitude"] == 63.4
    assert site["longitude"] == 10.4
    assert site["precision"] == "approximate"
    assert site["uncertainty_m"] == 80
    assert site["confidence"] == "medium"
    assert site["condition"] == "Current condition unknown"
    assert "Do not enter" in site["warnings"]
    assert len(site["sources"]) == 2


def test_blank_candidate_text_from_new_source_is_preserved(tmp_path):
    api = client(tmp_path)
    first = package()
    first["records"][0].update(
        condition="Existing condition note",
        short_rationale="Existing coordinate rationale",
        observed_location_text="Existing landmark clue",
    )
    assert commit_previewed(api, first).status_code == 200

    second = package("batch-2")
    second["records"][0].update(
        geometry=None,
        precision="unknown",
        uncertainty_m=None,
        location_basis="landmark_description",
        confidence="unknown",
        condition=" ",
        short_rationale="",
        observed_location_text="   ",
    )
    second["records"][0]["sources"][0]["url"] = "https://example.com/forum/blank-text"

    assert commit_previewed(api, second).status_code == 200
    site = api.get("/api/sites/1", headers=auth()).json()
    assert site["condition"] == "Existing condition note"
    assert site["confidence"] == "medium"
    assert site["short_rationale"] == "Existing coordinate rationale"
    assert site["observed_location_text"] == "Existing landmark clue"


def test_site_status_changes_use_review_workflow(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.patch(
        f"/api/sites/{site_id}", headers=auth(), json={"status": "trusted"}
    )

    assert response.status_code == 409
    assert api.get(f"/api/sites/{site_id}", headers=auth()).json()["status"] == "candidate"


def test_import_rejects_excluded_snublestein_record(tmp_path):
    api = client(tmp_path)
    payload = package(name="Snublestein at Ila")

    response = api.post(
        "/api/admin/imports/preview", headers=auth(), json=payload
    )
    assert response.status_code == 422


def test_import_url_validation_does_not_echo_nested_secret_value(tmp_path):
    api = client(tmp_path)
    payload = package()
    sentinel = "synthetic-private-value"
    payload["records"][0]["sources"][0]["url"] = (
        "https://example.com/map?layer=https%3A%2F%2Fexample.org%2Fwms"
        "%3Fapi_key%3D" + sentinel
    )

    response = api.post("/api/admin/imports/preview", headers=auth(), json=payload)

    assert response.status_code == 422
    assert sentinel not in response.text


def test_observation_photo_url_validation_does_not_echo_secret_value(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    sentinel = "synthetic-photo-secret"

    response = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Observed from the public path.",
            "photo_urls": [f"https://example.com/photo?token={sentinel}"],
        },
    )

    assert response.status_code == 422
    assert sentinel not in response.text


@pytest.mark.parametrize("photo_urls", [123, {"url": "https://example.com/photo.jpg"}, "https://example.com/photo.jpg"])
def test_observation_photo_urls_wrong_container_is_validation_error(tmp_path, photo_urls):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Observed from the public path.",
            "photo_urls": photo_urls,
        },
    )

    assert response.status_code == 422


def test_candidate_review_accept_reject_restore_and_duplicate_warning(tmp_path):
    api = client(tmp_path)
    first = package()
    assert commit_previewed(api, first).status_code == 200
    first_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    second = package("batch-2", external_key="forum:2")
    assert commit_previewed(api, second).status_code == 200
    preview = api.post(
        "/api/admin/imports/preview", headers=auth(), json=package("batch-3", external_key="forum:3")
    )
    assert preview.json()["summary"]["warnings"] == 2

    assert api.post(
        f"/api/sites/{first_id}/review", headers=auth(), json={"action": "accept"}
    ).json()["site"]["status"] == "likely"
    assert api.post(
        f"/api/sites/{first_id}/review", headers=auth(), json={"action": "reject"}
    ).json()["site"]["status"] == "rejected"
    assert api.post(
        f"/api/sites/{first_id}/review", headers=auth(), json={"action": "restore"}
    ).json()["site"]["status"] == "candidate"


def test_review_lifecycle_and_field_observation_are_recorded(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    researched = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "research"},
    )
    assert researched.status_code == 200
    assert researched.json()["site"]["status"] == "likely"

    observed = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Concrete entrance and a partly buried ventilation shaft observed.",
            "latitude": 63.4001,
            "longitude": 10.4001,
            "observed_location_text": "On the east side of the ridge.",
            "access_notes": "Stay on the public path; entrance is not entered.",
            "photo_urls": ["https://example.com/field-photo.jpg"],
        },
    )
    assert observed.status_code == 201
    assert observed.json()["site"]["status"] == "likely"
    assert observed.json()["site"]["field_observations"][0]["outcome"] == "found"
    assert observed.json()["site"]["field_observations"][0]["photo_urls"] == [
        "https://example.com/field-photo.jpg"
    ]

    field_verified = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "field_verify"},
    )
    assert field_verified.status_code == 200
    assert field_verified.json()["site"]["status"] == "field-verified"

    confirmed = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "confirm"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["site"]["status"] == "trusted"

    downgrade = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "research"},
    )
    assert downgrade.status_code == 409


def test_review_exposes_explicit_approximate_and_destroyed_transitions(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    approximate = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "mark_approximate"},
    )
    assert approximate.status_code == 200
    assert approximate.json()["site"]["status"] == "approximate"

    researched = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "research"},
    )
    assert researched.status_code == 200

    destroyed = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "mark_destroyed"},
    )
    assert destroyed.status_code == 200
    assert destroyed.json()["site"]["status"] == "destroyed-or-filled"


def test_field_verification_requires_an_observation(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    assert api.post(
        f"/api/sites/{site_id}/review", headers=auth(), json={"action": "research"}
    ).status_code == 200
    response = api.post(
        f"/api/sites/{site_id}/review",
        headers=auth(),
        json={"action": "field_verify"},
    )

    assert response.status_code == 409
    assert "field observation" in response.json()["detail"]


def test_field_verification_requires_a_found_observation(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    assert api.post(
        f"/api/sites/{site_id}/review", headers=auth(), json={"action": "research"}
    ).status_code == 200
    assert api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={"observed_at": "2026-09-14", "outcome": "not_found", "note": "No feature visible."},
    ).status_code == 201

    response = api.post(
        f"/api/sites/{site_id}/review", headers=auth(), json={"action": "field_verify"}
    )

    assert response.status_code == 409
    assert "found field observation" in response.json()["detail"]


def test_observations_are_rejected_for_rejected_or_merged_sites(tmp_path):
    api = client(tmp_path)
    first = package()
    second = package("batch-2", external_key="forum:2", name="Second bunker")
    assert commit_previewed(api, first).status_code == 200
    assert commit_previewed(api, second).status_code == 200
    sites = api.get("/api/sites", headers=auth()).json()
    first_id = next(site["id"] for site in sites if site["external_key"] == "forum:1")
    second_id = next(site["id"] for site in sites if site["external_key"] == "forum:2")

    assert api.post(
        f"/api/sites/{first_id}/review", headers=auth(), json={"action": "reject"}
    ).status_code == 200
    observation = {"observed_at": "2026-09-14", "outcome": "found", "note": "A feature was seen."}
    assert api.post(f"/api/sites/{first_id}/observations", headers=auth(), json=observation).status_code == 409

    assert api.post(
        f"/api/sites/{first_id}/review", headers=auth(), json={"action": "restore"}
    ).status_code == 200
    assert api.post(
        f"/api/sites/{second_id}/review", headers=auth(), json={"action": "merge", "target_site_id": first_id}
    ).status_code == 200
    assert api.post(f"/api/sites/{second_id}/observations", headers=auth(), json=observation).status_code == 409


def test_site_edit_requires_consistent_coordinates_and_precision(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200

    clear_without_precision = api.patch(
        "/api/sites/1", headers=auth(), json={"latitude": None, "longitude": None}
    )
    assert clear_without_precision.status_code == 422

    missing_uncertainty = api.patch(
        "/api/sites/1",
        headers=auth(),
        json={"latitude": 63.4, "longitude": 10.4, "uncertainty_m": None},
    )
    assert missing_uncertainty.status_code == 422

    exact_inferred = api.patch(
        "/api/sites/1",
        headers=auth(),
        json={
            "latitude": 63.4,
            "longitude": 10.4,
            "precision": "exact",
            "uncertainty_m": 1,
            "location_basis": "llm_inference",
        },
    )
    assert exact_inferred.status_code == 422

    cleared = api.patch(
        "/api/sites/1",
        headers=auth(),
        json={"latitude": None, "longitude": None, "precision": "unknown"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["latitude"] is None
    assert cleared.json()["uncertainty_m"] is None


def test_site_list_exposes_observation_points(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    observed = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Observed from the public path.",
            "latitude": 63.401,
            "longitude": 10.401,
        },
    )
    assert observed.status_code == 201
    observation_id = observed.json()["observation"]["id"]

    sites = api.get("/api/sites", headers=auth())

    assert sites.status_code == 200
    assert sites.json()[0]["observation_points"] == [
        {
            "id": observation_id,
            "observed_at": "2026-09-14",
            "outcome": "found",
            "latitude": 63.401,
            "longitude": 10.401,
        }
    ]


def test_found_observation_coordinate_can_be_adopted_with_audit_event(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    observed = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Entrance found beside the marked path.",
            "latitude": 63.401,
            "longitude": 10.401,
            "point_role": "feature",
            "uncertainty_m": 8,
        },
    )
    observation_id = observed.json()["observation"]["id"]

    adopted = api.post(
        f"/api/sites/{site_id}/observations/{observation_id}/adopt-location",
        headers=auth(),
    )

    assert adopted.status_code == 200
    assert adopted.json()["site"]["latitude"] == 63.401
    assert adopted.json()["site"]["longitude"] == 10.401
    assert adopted.json()["site"]["precision"] == "approximate"
    assert adopted.json()["site"]["location_basis"] == "explicit_coordinate"
    assert f"Field observation #{observation_id}" in adopted.json()["site"]["short_rationale"]
    with api.app.state.database.connect() as connection:
        event = connection.execute(
            "SELECT event_type, payload_json FROM site_events WHERE site_id = ? ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()
    assert event["event_type"] == "coordinate_adopted"
    assert json.loads(event["payload_json"]) == {
        "observation_id": observation_id,
        "old_latitude": 63.4,
        "old_longitude": 10.4,
        "new_latitude": 63.401,
        "new_longitude": 10.401,
    }


def test_adopting_new_feature_coordinate_on_trusted_site_requires_location_review(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    assert api.post(f"/api/sites/{site_id}/review", headers=auth(), json={"action": "research"}).status_code == 200
    baseline = api.post(
        f"/api/sites/{site_id}/observations", headers=auth(), json={
            "observed_at": "2026-09-14", "outcome": "found", "note": "Feature confirmed."
        }
    )
    assert baseline.status_code == 201
    assert api.post(f"/api/sites/{site_id}/review", headers=auth(), json={"action": "field_verify"}).status_code == 200
    assert api.post(f"/api/sites/{site_id}/review", headers=auth(), json={"action": "confirm"}).status_code == 200
    observation_id = api.post(
        f"/api/sites/{site_id}/observations", headers=auth(), json={
            "observed_at": "2026-09-14", "outcome": "found", "note": "New feature point.",
            "latitude": 63.401, "longitude": 10.401, "point_role": "feature", "uncertainty_m": 8,
        }
    ).json()["observation"]["id"]

    adopted = api.post(
        f"/api/sites/{site_id}/observations/{observation_id}/adopt-location", headers=auth()
    )

    assert adopted.status_code == 200
    assert adopted.json()["site"]["location_review_required"] == 1
    repeated = api.post(
        f"/api/sites/{site_id}/observations/{observation_id}/adopt-location", headers=auth()
    )
    assert repeated.status_code == 200
    assert repeated.json()["site"]["location_review_required"] == 1


def test_only_found_observation_with_coordinates_can_be_adopted(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    not_found = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "not_found",
            "note": "No structure at the candidate point.",
            "latitude": 63.401,
            "longitude": 10.401,
        },
    ).json()["observation"]["id"]
    no_coordinate = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "A feature was seen, but no GPS point was recorded.",
        },
    ).json()["observation"]["id"]

    assert api.post(
        f"/api/sites/{site_id}/observations/{not_found}/adopt-location", headers=auth()
    ).status_code == 409
    assert api.post(
        f"/api/sites/{site_id}/observations/{no_coordinate}/adopt-location", headers=auth()
    ).status_code == 409


def test_adoption_requires_feature_role_and_explicit_radius(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    missing_radius = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14", "outcome": "found", "note": "Feature.",
            "latitude": 63.401, "longitude": 10.401, "point_role": "feature",
        },
    ).json()["observation"]["id"]
    entrance = api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14", "outcome": "found", "note": "Entrance.",
            "latitude": 63.401, "longitude": 10.401, "point_role": "entrance", "uncertainty_m": 8,
        },
    ).json()["observation"]["id"]

    assert api.post(
        f"/api/sites/{site_id}/observations/{missing_radius}/adopt-location", headers=auth()
    ).status_code == 409
    assert api.post(
        f"/api/sites/{site_id}/observations/{entrance}/adopt-location", headers=auth()
    ).status_code == 409


def test_field_priority_returns_only_public_sites_in_research_order(tmp_path):
    api = client(tmp_path)
    records = [
        ("public-medium", "Medium public", "public", "medium", 250),
        ("public-low", "Low public", "public", "low", 50),
        ("private-high", "Private high", "private", "high", 10),
        ("unknown-high", "Unknown high", "unknown", "high", 10),
    ]
    for key, name, access, confidence, uncertainty in records:
        payload = package(f"batch-{key}", name=name, external_key=f"field:{key}")
        payload["records"][0]["sources"][0]["url"] = f"https://example.com/field/{key}"
        payload["records"][0].update(access=access, confidence=confidence, uncertainty_m=uncertainty)
        assert commit_previewed(api, payload).status_code == 200

    response = api.get("/api/field-priority?limit=10", headers=auth())

    assert response.status_code == 200
    assert [site["external_key"] for site in response.json()] == [
        "field:public-medium",
        "field:public-low",
    ]


def test_candidate_review_filters_combine_curator_fields(tmp_path):
    api = client(tmp_path)
    broad = package("batch-broad", name="Broad lead", external_key="forum:broad")
    broad["records"][0]["sources"][0]["url"] = "https://example.com/forum/broad"
    broad["records"][0].update(
        confidence="low",
        access="permission_required",
        uncertainty_m=800,
        sources=[{**broad["records"][0]["sources"][0], "source_type": "forum"}],
    )
    narrow = package("batch-narrow", name="Narrow lead", external_key="web:narrow")
    narrow["records"][0]["sources"][0]["url"] = "https://example.com/website/narrow"
    narrow["records"][0].update(
        confidence="medium",
        access="public",
        uncertainty_m=80,
        sources=[{**narrow["records"][0]["sources"][0], "source_type": "website"}],
    )
    assert commit_previewed(api, broad).status_code == 200
    assert commit_previewed(api, narrow).status_code == 200

    response = api.get(
        "/api/review/candidates?confidence=low&access=permission_required"
        "&uncertainty_band=over-500&source_type=forum",
        headers=auth(),
    )

    assert response.status_code == 200
    assert [site["external_key"] for site in response.json()] == ["forum:broad"]
    assert response.json()[0]["confidence"] == "low"


def test_site_kind_filter_matches_case_insensitive_substrings(tmp_path):
    api = client(tmp_path)
    bunker = package()
    cave = package("batch-cave", name="Ridge cave", external_key="forum:cave")
    cave["records"][0]["site_kind"] = "cave"
    assert commit_previewed(api, bunker).status_code == 200
    assert commit_previewed(api, cave).status_code == 200

    response = api.get("/api/sites?site_kind=BUNK", headers=auth())

    assert response.status_code == 200
    assert [site["site_kind"] for site in response.json()] == ["bunker"]


def test_site_list_filters_access_and_confidence(tmp_path):
    api = client(tmp_path)
    public = package("batch-public", name="Public lead", external_key="site:public")
    public["records"][0]["sources"][0]["url"] = "https://example.com/site/public"
    public["records"][0].update(access="public", confidence="high")
    unknown = package("batch-unknown", name="Unknown lead", external_key="site:unknown")
    unknown["records"][0]["sources"][0]["url"] = "https://example.com/site/unknown"
    unknown["records"][0].update(access="unknown", confidence="low")
    assert commit_previewed(api, public).status_code == 200
    assert commit_previewed(api, unknown).status_code == 200

    response = api.get("/api/sites?access=public&confidence=high", headers=auth())

    assert response.status_code == 200
    assert [site["external_key"] for site in response.json()] == ["site:public"]
    assert api.get("/api/sites?access=not-an-access", headers=auth()).status_code == 400
    assert api.get("/api/sites?confidence=not-confidence", headers=auth()).status_code == 400


def test_site_search_matches_name_and_rationale(tmp_path):
    api = client(tmp_path)
    first = package(name="Kuhaugen command bunker")
    second = package(
        "batch-second", name="Harbour feature", external_key="forum:second"
    )
    second["records"][0]["short_rationale"] = "Near the Kuhaugen ridge."
    assert commit_previewed(api, first).status_code == 200
    assert commit_previewed(api, second).status_code == 200

    response = api.get("/api/sites?q=KUHAUGEN", headers=auth())

    assert response.status_code == 200
    assert {site["name"] for site in response.json()} == {
        "Kuhaugen command bunker",
        "Harbour feature",
    }


def test_geojson_export_contains_sites_and_observations(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    assert api.post(
        f"/api/sites/{site_id}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Observed from the path.",
            "latitude": 63.401,
            "longitude": 10.401,
        },
    ).status_code == 201

    response = api.get("/api/sites.geojson", headers=auth())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    features = response.json()["features"]
    assert len(features) == 2
    assert {feature["properties"]["feature_type"] for feature in features} == {
        "site",
        "field_observation",
    }


def test_editing_warnings_updates_the_json_column(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.patch(
        f"/api/sites/{site_id}", headers=auth(), json={"warnings": ["Check gate"]}
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == ["Check gate"]


def test_edit_rejects_null_for_required_site_fields(tmp_path):
    api = client(tmp_path)
    assert commit_previewed(api, package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.patch(
        f"/api/sites/{site_id}", headers=auth(), json={"name": None}
    )

    assert response.status_code == 422


def test_merge_transfers_evidence_without_duplicate_failure(tmp_path):
    api = client(tmp_path)
    first = package()
    second = package("batch-2", external_key="forum:2")
    assert commit_previewed(api, first).status_code == 200
    assert commit_previewed(api, second).status_code == 200
    sites = api.get("/api/sites", headers=auth()).json()
    observation = api.post(
        f"/api/sites/{sites[0]['id']}/observations",
        headers=auth(),
        json={
            "observed_at": "2026-09-14",
            "outcome": "found",
            "note": "Observed from the public path.",
        },
    )
    assert observation.status_code == 201

    response = api.post(
        f"/api/sites/{sites[0]['id']}/review",
        headers=auth(),
        json={"action": "merge", "target_site_id": sites[1]["id"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "merged"
    target = api.get(f"/api/sites/{sites[1]['id']}", headers=auth()).json()
    assert len(target["sources"]) == 2
    assert len(target["field_observations"]) == 1


def test_merged_sites_cannot_be_reviewed_or_used_as_merge_targets(tmp_path):
    api = client(tmp_path)
    first = package()
    second = package("batch-2", external_key="forum:2")
    assert commit_previewed(api, first).status_code == 200
    assert commit_previewed(api, second).status_code == 200
    sites = api.get("/api/sites", headers=auth()).json()

    merged = api.post(
        f"/api/sites/{sites[0]['id']}/review",
        headers=auth(),
        json={"action": "merge", "target_site_id": sites[1]["id"]},
    )
    assert merged.status_code == 200

    cannot_review = api.post(
        f"/api/sites/{sites[0]['id']}/review",
        headers=auth(),
        json={"action": "restore"},
    )
    assert cannot_review.status_code == 409

    target_rejected = api.post(
        f"/api/sites/{sites[1]['id']}/review", headers=auth(), json={"action": "reject"}
    )
    assert target_rejected.status_code == 200

    third = package("batch-3", external_key="forum:3")
    assert commit_previewed(api, third).status_code == 200
    third_id = next(
        site["id"]
        for site in api.get("/api/sites", headers=auth()).json()
        if site["external_key"] == "forum:3"
    )
    cannot_merge = api.post(
        f"/api/sites/{third_id}/review",
        headers=auth(),
        json={"action": "merge", "target_site_id": sites[1]["id"]},
    )
    assert cannot_merge.status_code == 404
