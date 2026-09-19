from fastapi import HTTPException
from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest

from app.config import Settings
from app.main import _require_admin, create_app


def test_health_reports_version_and_database_status(tmp_path):
    app = create_app(
        Settings(data_dir=tmp_path, admin_token="admin", app_version="test-sha")
    )

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "version": "test-sha",
        "database": "ready",
    }


def test_readiness_reports_schema_auth_and_optional_routing(tmp_path):
    ready = TestClient(create_app(Settings(data_dir=tmp_path / "ready", admin_token="admin"))).get(
        "/api/ready"
    )
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "database": "ready",
        "authentication": "configured",
        "routing": "optional-unconfigured",
    }

    not_ready = TestClient(create_app(Settings(data_dir=tmp_path / "missing-auth"))).get(
        "/api/ready"
    )
    assert not_ready.status_code == 503
    assert not_ready.json()["authentication"] == "not_configured"


def test_import_limits_are_enforced_before_commit_validation(tmp_path):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="admin")))
    oversized = {"records": [], "padding": "x" * (2 * 1024 * 1024)}
    response = api.post(
        "/api/admin/imports/preview",
        headers={"Authorization": "Bearer admin"},
        json=oversized,
    )
    assert response.status_code == 413


def test_import_work_does_not_block_health_while_worker_waits(tmp_path, monkeypatch):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="admin")))
    started = Event()
    release = Event()

    def blocked_commit(*_args):
        started.set()
        assert release.wait(3)
        return {"status": "synthetic"}

    monkeypatch.setattr("app.main._commit_package", blocked_commit)
    payload = {
        "schema_version": "1.0",
        "batch_id": "worker-test",
        "generated_at": "2026-09-14T12:00:00Z",
        "records": [{
            "external_key": "worker:test",
            "name": "Worker test",
            "site_kind": "bunker",
            "geometry": {"latitude": 63.4, "longitude": 10.4},
            "precision": "approximate",
            "uncertainty_m": 100,
            "location_basis": "map_reference",
            "status": "candidate",
            "access": "unknown",
            "sources": [{
                "url": "https://example.com/worker",
                "title": "Worker source",
                "source_type": "test",
                "excerpt": "Synthetic worker source.",
            }],
        }],
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            api.post,
            "/api/admin/imports/commit",
            headers={"Authorization": "Bearer admin"},
            json=payload,
        )
        assert started.wait(2)
        assert api.get("/api/health").status_code == 200
        release.set()
        assert future.result().status_code == 200



def test_private_sites_api_requires_bearer_token(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    client = TestClient(app)

    assert client.get("/api/sites").status_code == 401
    assert client.get(
        "/api/sites", headers={"Authorization": "Bearer admin"}
    ).status_code == 200


def test_site_detail_exposes_external_enrichment_without_changing_site_facts(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    with app.state.database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sites
                (external_key, name, site_kind, precision, location_basis, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("krigskart:413", "Jonsvatnet, Trondheim, 3", "bunker", "unknown", "map_reference", "2026-09-15", "2026-09-15"),
        )

    response = TestClient(app).get("/api/sites/1", headers={"Authorization": "Bearer admin"})

    assert response.status_code == 200
    body = response.json()
    assert body["enrichment"]["display_name"] == "Junkers Ju 88 A – Jonsvatnet (markør 413)"
    assert body["enrichment"]["research_state"] == "curated"
    assert body["enrichment"]["about"][0]["certainty"] == "supported"
    assert body["enrichment"]["about"][0]["sources"][0]["url"].endswith("oldid=6380")
    assert "Dykking i Jonsvatnet er forbudt" in body["enrichment"]["visit_access"]["access_rules"][0]["text"]
    assert body["status"] == "candidate"
    assert body["latitude"] is None
    assert body["access"] == "unknown"


def test_site_search_matches_enrichment_display_name_without_rewriting_db_name(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    with app.state.database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sites
                (external_key, name, site_kind, precision, location_basis, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("krigskart:413", "Jonsvatnet, Trondheim, 3", "bunker", "unknown", "map_reference", "2026-09-15", "2026-09-15"),
        )

    client = TestClient(app)
    for query in ("Junkers", "Ju 88"):
        response = client.get("/api/sites", params={"q": query}, headers={"Authorization": "Bearer admin"})
        assert response.status_code == 200
        assert [site["external_key"] for site in response.json()] == ["krigskart:413"]
        assert response.json()[0]["name"] == "Jonsvatnet, Trondheim, 3"
        assert response.json()[0]["enrichment"]["display_name"].startswith("Junkers Ju 88 A")
        assert response.json()[0]["enrichment"]["research_state"] == "curated"


def test_unicode_bearer_token_is_rejected_without_server_error(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_token="admin")

    with pytest.raises(HTTPException) as error:
        _require_admin(settings, "Bearer café")

    assert error.value.status_code == 401


def test_responses_include_security_headers_and_api_is_not_cached(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["permissions-policy"] == "geolocation=(self)"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_root_serves_map_frontend(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="admin")))

    response = client.get("/")

    assert response.status_code == 200
    assert "Bunkerkartet" in response.text
    assert "/static/app.js?v=" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/static/app.js").headers["cache-control"] == "no-store"


def test_root_escapes_app_version_in_asset_query(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                admin_token="admin",
                app_version='sha" onerror="alert(1)',
            )
        )
    )

    response = client.get("/")

    assert response.status_code == 200
    assert '/static/app.js?v=sha&quot; onerror=&quot;alert(1)"' in response.text
    assert '/static/app.js?v=sha" onerror="alert(1)"' not in response.text
