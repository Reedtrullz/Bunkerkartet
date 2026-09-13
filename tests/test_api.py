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


def test_root_serves_map_frontend(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="admin")))

    response = client.get("/")

    assert response.status_code == 200
    assert "Bunkerkartet" in response.text
