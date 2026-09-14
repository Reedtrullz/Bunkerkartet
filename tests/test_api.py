from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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


def test_private_sites_api_requires_bearer_token(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    client = TestClient(app)

    assert client.get("/api/sites").status_code == 401
    assert client.get(
        "/api/sites", headers={"Authorization": "Bearer admin"}
    ).status_code == 200


def test_responses_include_security_headers_and_api_is_not_cached(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, admin_token="admin"))
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
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
