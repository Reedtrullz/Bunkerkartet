import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routes import RouteResult


def seed_site(api, *, access="unknown", approach=True, location_review_required=0):
    with api.app.state.database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO sites
                (external_key, name, site_kind, latitude, longitude, precision, uncertainty_m,
                 location_basis, status, access, warnings_json, created_at, updated_at,
                 approach_latitude, approach_longitude, approach_access, approach_note,
                 approach_reviewed_at, location_review_required)
            VALUES ('route:site', 'Approach site', 'bunker', 63.4, 10.4, 'approximate', 100,
                    'map_reference', 'candidate', ?, '[]', '2026-09-14', '2026-09-14',
                    ?, ?, ?, ?, ?, ?)
            """,
            (
                access,
                63.41 if approach else None,
                10.41 if approach else None,
                "public" if approach else "unknown",
                "Public path viewpoint" if approach else None,
                "2026-09-14" if approach else None,
                location_review_required,
            ),
        )
        return int(cursor.lastrowid)


def test_route_requires_openrouteservice_key(tmp_path):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="secret")))

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Trondheim walk",
            "start": {"lat": 63.4, "lon": 10.4},
            "site_ids": [1],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "routing is not configured"


def test_route_returns_retryable_busy_response_without_queue(tmp_path, monkeypatch):
    class BusySemaphore:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

        def release(self):
            raise AssertionError("busy route must not release an unacquired slot")

    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key")))
    site_id = seed_site(api)
    monkeypatch.setattr("app.main.ORS_SEMAPHORE", BusySemaphore())

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


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
            waypoint_indices=[0, 1],
        ),
    )
    site_id = seed_site(api)

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Trondheim walk",
            "start": {"lat": 63.4, "lon": 10.4},
            "site_ids": [site_id],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["distance_m"] == 1200
    assert body["duration_s"] == 900
    assert body["gpx"].count("<trkpt") == 2
    assert "Route start" in body["gpx"]
    assert "Approach site" in body["gpx"]
    assert body["stops"][0]["point_role"] == "approach"

    listed = api.get("/api/routes", headers={"Authorization": "Bearer secret"})
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "Trondheim walk"
    assert listed.json()[0]["created_at"]

    loaded = api.get(
        f"/api/routes/{body['id']}", headers={"Authorization": "Bearer secret"}
    )
    assert loaded.status_code == 200
    assert loaded.json()["created_at"] == listed.json()[0]["created_at"]
    assert loaded.json()["warnings"] == [
        "A route does not grant permission to enter land or structures."
    ]


def test_saved_route_keeps_endpoint_snap_warning(tmp_path, monkeypatch):
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
            coordinates=[(10.402, 63.4), (10.408, 63.41)],
            waypoint_indices=[0, 1],
        ),
    )
    site_id = seed_site(api)

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={
            "start": {"lat": 63.4, "lon": 10.4},
            "site_ids": [site_id],
        },
    )

    assert response.status_code == 200
    warning = "Route start: provider snapped this stop by"
    assert any(warning in item for item in response.json()["warnings"])
    loaded = api.get(
        f"/api/routes/{response.json()['id']}", headers={"Authorization": "Bearer secret"}
    )
    assert any(warning in item for item in loaded.json()["warnings"])


def test_route_rejects_unreviewed_approach_before_provider_call(tmp_path, monkeypatch):
    api = TestClient(
        create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key"))
    )
    site_id = seed_site(api, approach=False)
    monkeypatch.setattr("app.main.fetch_openrouteservice", lambda *_: pytest.fail("provider called"))

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 409
    assert "reviewed public approach" in response.json()["detail"]


def test_route_uses_reviewed_approach_and_stable_site_stop(tmp_path, monkeypatch):
    api = TestClient(
        create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key"))
    )
    site_id = seed_site(api, access="dangerous")
    seen = []

    def route(api_key, coordinates):
        seen.append(coordinates)
        return RouteResult(1200, 900, [coordinates[0], coordinates[-1]], [0, 1])

    monkeypatch.setattr("app.main.fetch_openrouteservice", route)
    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 200
    assert seen == [[(10.4, 63.4), (10.41, 63.41)]]
    assert response.json()["stops"][0]["site_id"] == site_id


def test_route_rejects_location_review_required(tmp_path, monkeypatch):
    api = TestClient(
        create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key"))
    )
    site_id = seed_site(api, location_review_required=1)
    monkeypatch.setattr("app.main.fetch_openrouteservice", lambda *_: pytest.fail("provider called"))

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 409
    assert "location review" in response.json()["detail"]


def test_route_rejects_rejected_site_before_provider_call(tmp_path, monkeypatch):
    api = TestClient(
        create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key"))
    )
    site_id = seed_site(api)
    with api.app.state.database.connect() as connection:
        connection.execute("UPDATE sites SET status = 'rejected' WHERE id = ?", (site_id,))
    monkeypatch.setattr("app.main.fetch_openrouteservice", lambda *_: pytest.fail("provider called"))

    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 409
    assert "rejected" in response.json()["detail"]


def test_route_revalidates_site_after_provider_returns(tmp_path, monkeypatch):
    api = TestClient(
        create_app(Settings(data_dir=tmp_path, admin_token="secret", ors_api_key="ors-key"))
    )
    site_id = seed_site(api)

    def route(api_key, coordinates):
        with api.app.state.database.connect() as connection:
            connection.execute("UPDATE sites SET status = 'rejected' WHERE id = ?", (site_id,))
        return RouteResult(1200, 900, [coordinates[0], coordinates[-1]], [0, 1])

    monkeypatch.setattr("app.main.fetch_openrouteservice", route)
    response = api.post(
        "/api/routes",
        headers={"Authorization": "Bearer secret"},
        json={"start": {"lat": 63.4, "lon": 10.4}, "site_ids": [site_id]},
    )

    assert response.status_code == 409
    assert api.get("/api/routes", headers={"Authorization": "Bearer secret"}).json() == []
