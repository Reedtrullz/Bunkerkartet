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


def test_import_preserves_trusted_fields_and_attaches_new_evidence(tmp_path):
    api = client(tmp_path)
    first = package()
    assert api.post("/api/admin/imports/commit", headers=auth(), json=first).status_code == 200

    site_id = api.get("/api/sites", headers=auth()).json()[0]["id"]
    edited = api.patch(
        f"/api/sites/{site_id}",
        headers=auth(),
        json={
            "name": "Curated bunker",
            "status": "trusted",
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
