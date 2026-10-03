"""Focused regressions from the bounded trust/privacy integration review."""

import sqlite3

from started_client import StartedClient

from app.config import Settings
from app.db import CURRENT_SCHEMA_VERSION, Database
from app.main import create_app
from app.routes import RouteResult
from scripts.backup_database import create_backup
from scripts.restore_database import restore_archive
from test_route_api import seed_site


ADMIN = {"Authorization": "Bearer review-admin"}


def test_sites_reader_scope_does_not_expose_private_saved_routes(tmp_path, monkeypatch):
    api = StartedClient(create_app(Settings(
        data_dir=tmp_path,
        admin_token="review-admin",
        ors_api_key="synthetic",
        pilot_readers_enabled=True,
    )))
    site_id = seed_site(api)
    monkeypatch.setattr(
        "app.main.fetch_openrouteservice",
        lambda _key, coordinates: RouteResult(1200, 900, coordinates, [0, 1]),
    )
    created = api.post("/api/routes", headers=ADMIN, json={
        "name": "Private trip canary",
        "start": {"lat": 63.4, "lon": 10.4},
        "site_ids": [site_id],
    })
    assert created.status_code == 200, created.text

    grant = api.post("/api/pilots/readers", headers=ADMIN, json={
        "scopes": ["sites:read"], "ttl_seconds": 60,
    })
    assert grant.status_code == 200, grant.text
    response = api.get(
        f"/api/routes/{created.json()['id']}",
        headers={"Authorization": f"Bearer {grant.json()['token']}"},
    )

    assert response.status_code == 403


def _restore_source(path, external_key, name):
    database = Database(path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO sites
               (external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (?,?,'bunker','unknown','map_reference','2026-10-03','2026-10-03')""",
            (external_key, name),
        )
    return database


def test_restore_uses_the_same_archive_bytes_that_passed_receipt_verification(tmp_path, monkeypatch):
    source_a = _restore_source(tmp_path / "source-a.sqlite3", "restore:a", "Expected source")
    source_b = _restore_source(tmp_path / "source-b.sqlite3", "restore:b", "Swapped source")
    archive_a = tmp_path / "selected.tar.gz"
    archive_b = tmp_path / "other.tar.gz"
    original_receipt = create_backup(source_a.path, archive_a)
    create_backup(source_b.path, archive_b)

    verify = __import__("scripts.restore_database", fromlist=["verify_backup"]).verify_backup

    def verify_then_replace(path, **kwargs):
        receipt = verify(path, **kwargs)
        # Simulate the source path changing after successful checksum validation.
        archive_a.write_bytes(archive_b.read_bytes())
        return receipt

    monkeypatch.setattr("scripts.restore_database.verify_backup", verify_then_replace)
    destination = tmp_path / "restore-candidate"
    result = restore_archive(archive_a, destination, expected_version=CURRENT_SCHEMA_VERSION)

    assert result["archive_sha256"] == original_receipt["sha256"]
    with sqlite3.connect(destination / "bunkerkartet.sqlite3") as connection:
        restored = connection.execute(
            "SELECT external_key,name FROM sites WHERE external_key LIKE 'restore:%'"
        ).fetchall()
    assert restored == [("restore:a", "Expected source")]
