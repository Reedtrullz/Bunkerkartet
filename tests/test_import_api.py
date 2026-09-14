import json

from fastapi.testclient import TestClient

from app.config import Settings
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

    committed = api.post(
        "/api/admin/imports/commit", headers=auth(), json=payload
    )
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
    assert api.post("/api/admin/imports/commit", headers=auth(), json=first).status_code == 200

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
        },
    )
    assert edited.status_code == 200

    second = package("batch-2", name="Imported replacement")
    second["records"][0]["sources"][0]["url"] = "https://example.com/forum/2"
    committed = api.post(
        "/api/admin/imports/commit", headers=auth(), json=second
    )
    assert committed.status_code == 200
    assert committed.json()["preserved"] == 1
    assert committed.json()["evidence_attached"] == 1

    site = api.get(f"/api/sites/{site_id}", headers=auth()).json()
    assert site["name"] == "Curated bunker"
    assert site["status"] == "trusted"
    assert site["latitude"] == 63.401
    assert len(site["sources"]) == 2


def test_site_status_changes_use_review_workflow(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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


def test_candidate_review_accept_reject_restore_and_duplicate_warning(tmp_path):
    api = client(tmp_path)
    first = package()
    assert api.post("/api/admin/imports/commit", headers=auth(), json=first).status_code == 200
    first_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    second = package("batch-2", external_key="forum:2")
    assert api.post("/api/admin/imports/commit", headers=auth(), json=second).status_code == 200
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
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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


def test_field_verification_requires_an_observation(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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


def test_site_list_exposes_observation_points(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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


def test_only_found_observation_with_coordinates_can_be_adopted(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
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
        assert api.post("/api/admin/imports/commit", headers=auth(), json=payload).status_code == 200

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
    assert api.post("/api/admin/imports/commit", headers=auth(), json=broad).status_code == 200
    assert api.post("/api/admin/imports/commit", headers=auth(), json=narrow).status_code == 200

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
    assert api.post("/api/admin/imports/commit", headers=auth(), json=bunker).status_code == 200
    assert api.post("/api/admin/imports/commit", headers=auth(), json=cave).status_code == 200

    response = api.get("/api/sites?site_kind=BUNK", headers=auth())

    assert response.status_code == 200
    assert [site["site_kind"] for site in response.json()] == ["bunker"]


def test_editing_warnings_updates_the_json_column(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.patch(
        f"/api/sites/{site_id}", headers=auth(), json={"warnings": ["Check gate"]}
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == ["Check gate"]


def test_edit_rejects_null_for_required_site_fields(tmp_path):
    api = client(tmp_path)
    assert api.post("/api/admin/imports/commit", headers=auth(), json=package()).status_code == 200
    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]

    response = api.patch(
        f"/api/sites/{site_id}", headers=auth(), json={"name": None}
    )

    assert response.status_code == 422


def test_merge_transfers_evidence_without_duplicate_failure(tmp_path):
    api = client(tmp_path)
    first = package()
    second = package("batch-2", external_key="forum:2")
    assert api.post("/api/admin/imports/commit", headers=auth(), json=first).status_code == 200
    assert api.post("/api/admin/imports/commit", headers=auth(), json=second).status_code == 200
    sites = api.get("/api/sites", headers=auth()).json()

    response = api.post(
        f"/api/sites/{sites[0]['id']}/review",
        headers=auth(),
        json={"action": "merge", "target_site_id": sites[1]["id"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "merged"
    target = api.get(f"/api/sites/{sites[1]['id']}", headers=auth()).json()
    assert len(target["sources"]) == 1
