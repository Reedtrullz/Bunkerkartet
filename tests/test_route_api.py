from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routes import RouteResult


def test_route_requires_openrouteservice_key(tmp_path):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="secret")))

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Trondheim walk",
            "start": {"lat": 63.4, "lon": 10.4},
            "waypoints": [{"lat": 63.41, "lon": 10.41}],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "routing is not configured"


def test_route_returns_gpx_and_persists_plan(tmp_path, monkeypatch):
    api = TestClient(
        create_app(
            Settings(
                data_dir=tmp_path,
                admin_token="secret",
                ors_api_key="ors-key",
            )
        )
    )
    monkeypatch.setattr(
        "app.main.fetch_openrouteservice",
        lambda api_key, coordinates: RouteResult(
            distance_m=1200,
            duration_s=900,
            coordinates=[(10.4, 63.4), (10.41, 63.41)],
        ),
    )

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Trondheim walk",
            "start": {"lat": 63.4, "lon": 10.4},
            "waypoints": [{"lat": 63.41, "lon": 10.41}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["distance_m"] == 1200
    assert body["duration_s"] == 900
    assert body["gpx"].count("<trkpt") == 2

