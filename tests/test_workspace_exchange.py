from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import stat
import sqlite3
import zipfile

import pytest

from app.db import CURRENT_SCHEMA_VERSION, REQUIRED_SCHEMA, Database
from app.enrichment import load_site_documents, research_site_payload
from app.interchange import ExchangeError, create_workspace_archive, stage_workspace_archive


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_MEMBERS = {"manifest.json", "workspace.sqlite3", "seed/site_enrichment.json"}


def _seed_bytes() -> bytes:
    return (
        json.dumps(
            {
                "schema_version": 1,
                "sites": [
                    {
                        "external_key": "synthetic:exchange-site",
                        "display_name": "Synthetic seed name",
                        "kind_label": "test bunker",
                        "research_state": "curated",
                        "reviewed_at": "2026-10-03",
                        "sources": [
                            {
                                "id": "synthetic-source",
                                "title": "Synthetic source",
                                "url": "https://example.test/source",
                            }
                        ],
                        "claims": [
                            {
                                "id": "synthetic-claim",
                                "section": "about",
                                "text": "Synthetic claim retained in the seed.",
                                "certainty": "source_supported",
                                "source_ids": ["synthetic-source"],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _create_synthetic_workspace(root: Path) -> tuple[Path, Path, Path]:
    database_path = root / "data" / "bunkerkartet.sqlite3"
    seed_path = root / "app" / "content" / "site_enrichment.json"
    seed_path.parent.mkdir(parents=True)
    seed_path.write_bytes(_seed_bytes())
    database = Database(database_path)
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO sites (
                id, external_key, name, site_kind, latitude, longitude, precision,
                uncertainty_m, location_basis, status, access, confidence,
                created_at, updated_at, content_json
            ) VALUES (1, 'synthetic:exchange-site', 'Synthetic site', 'bunker',
                63.4, 10.4, 'approximate', 25, 'map_reference', 'candidate',
                'unknown', 'medium', '2026-10-03', '2026-10-03', ?)
            """,
            (
                json.dumps(
                    {
                        "external_key": "synthetic:exchange-site",
                        "display_name": "Synthetic effective override",
                        "kind_label": "test bunker",
                        "research_state": "identity_review",
                        "reviewed_at": "2026-10-03",
                        "sources": [
                            {
                                "id": "synthetic-source",
                                "title": "Synthetic source",
                                "url": "https://example.test/source",
                            }
                        ],
                        "claims": [
                            {
                                "id": "synthetic-claim",
                                "section": "about",
                                "text": "Synthetic claim retained in the override.",
                                "certainty": "source_supported",
                                "source_ids": ["synthetic-source"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(
            """INSERT INTO sources (id, url, title, source_type, excerpt, created_at, updated_at)
               VALUES (1, 'https://example.test/source', 'Synthetic source', 'primary',
                       'Synthetic provenance excerpt', '2026-10-03', '2026-10-03')"""
        )
        connection.execute(
            "INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, 1, 'source', '2026-10-03')"
        )
        connection.execute(
            """INSERT INTO import_batches (batch_id, schema_version, generated_at, created_at, committed_at)
               VALUES ('synthetic-batch', '1.0', '2026-10-03', '2026-10-03', '2026-10-03')"""
        )
        connection.execute(
            """INSERT INTO import_records (batch_id, external_key, site_id, action, payload_json, created_at)
               VALUES ('synthetic-batch', 'synthetic:exchange-site', 1, 'updated', ?, '2026-10-03')""",
            (json.dumps({"name": "Synthetic historical import"}),),
        )
        connection.execute(
            """INSERT INTO site_events (site_id, event_type, payload_json, created_at)
               VALUES (1, 'edit', ?, '2026-10-03')""",
            (json.dumps({"before": {"name": "Before"}, "after": {"name": "After"}}),),
        )
        connection.execute(
            """INSERT INTO field_observations (site_id, observed_at, outcome, note, created_at)
               VALUES (1, '2026-10-03', 'needs_follow_up', 'Synthetic private observation', '2026-10-03')"""
        )
        connection.execute(
            """INSERT INTO route_plans
               (id, name, start_json, waypoints_json, distance_m, duration_s, geometry_json, gpx_text, created_at)
               VALUES (1, 'Synthetic private route', '{"lat":63.4,"lon":10.4}', '[]', 100, 90,
                       '[[10.4,63.4],[10.5,63.5]]', '<gpx/>', '2026-10-03')"""
        )
        connection.execute(
            """INSERT INTO site_relations (site_id, related_external_key, relation_kind, created_at)
               VALUES (1, 'synthetic:related-site', 'related', '2026-10-03')"""
        )
    return root, database_path, seed_path


def _table_snapshot(database_path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            for table in REQUIRED_SCHEMA
        }


def _make_archive(tmp_path: Path) -> tuple[Path, Path, Path]:
    root, database_path, seed_path = _create_synthetic_workspace(tmp_path / "source")
    archive_path = tmp_path / "workspace.bkws"
    create_workspace_archive(
        database_path,
        seed_path,
        archive_path,
        project_root=PROJECT_ROOT,
    )
    return archive_path, database_path, seed_path


def test_workspace_exchange_reconstructs_seed_overrides_history_and_private_records(tmp_path):
    archive_path, database_path, seed_path = _make_archive(tmp_path)
    source_db_bytes = database_path.read_bytes()
    source_seed_bytes = seed_path.read_bytes()
    destination = tmp_path / "isolated-compatible-workspace"

    result = stage_workspace_archive(archive_path, destination)

    restored_database = destination / "data" / "bunkerkartet.sqlite3"
    restored_seed = destination / "app" / "content" / "site_enrichment.json"
    assert result["status"] == "staged-and-verified"
    assert _table_snapshot(restored_database) == _table_snapshot(database_path)
    original_seed = load_site_documents(seed_path)
    imported_seed = load_site_documents(restored_seed)
    assert {key: research_site_payload(value) for key, value in imported_seed.items()} == {
        key: research_site_payload(value) for key, value in original_seed.items()
    }
    with closing(sqlite3.connect(restored_database)) as connection:
        assert connection.execute("SELECT content_json FROM sites WHERE id=1").fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM site_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_records").fetchone()[0] == 1
        assert connection.execute("SELECT note FROM field_observations").fetchone()[0] == "Synthetic private observation"
        assert connection.execute("SELECT name FROM route_plans").fetchone()[0] == "Synthetic private route"

    assert database_path.read_bytes() == source_db_bytes
    assert seed_path.read_bytes() == source_seed_bytes
    assert stat.S_IMODE(restored_database.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored_seed.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / "app").stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / "app" / "content").stat().st_mode) == 0o700
    assert stat.S_IMODE((destination / "data").stat().st_mode) == 0o700


def test_workspace_archive_manifest_is_versioned_bounded_and_explicit_about_exclusions(tmp_path):
    archive_path, database_path, _ = _make_archive(tmp_path)

    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == ARCHIVE_MEMBERS
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest["format"] == "bunkerkartet-private-workspace"
    assert manifest["format_version"] == 1
    assert manifest["schema_version"] == CURRENT_SCHEMA_VERSION
    assert len(manifest["build_sha"]) in {40, 64}
    assert manifest["seed"]["identity"].startswith("sha256:")
    assert manifest["seed"]["identity"] == (
        "sha256:" + manifest["checksums"]["seed/site_enrichment.json"]["sha256"]
    )
    assert len(manifest["source_tree_sha256"]) == 64
    assert manifest["counts"] == {table: len(rows) for table, rows in _table_snapshot(database_path).items()}
    assert set(manifest["checksums"]) == ARCHIVE_MEMBERS - {"manifest.json"}
    assert all(len(entry["sha256"]) == 64 and entry["size_bytes"] > 0 for entry in manifest["checksums"].values())
    assert {".env", "environment files", "tokens and credentials", "files outside the exchange allowlist"} <= set(manifest["exclusions"])
    assert stat.S_IMODE(archive_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["missing", "unsafe_path", "tampered", "wrong_format", "wrong_schema"])
def test_workspace_import_rejects_untrusted_archive_before_creating_destination(tmp_path, failure):
    archive_path, database_path, seed_path = _make_archive(tmp_path)
    source_db_bytes = database_path.read_bytes()
    source_seed_bytes = seed_path.read_bytes()
    invalid_archive = tmp_path / f"{failure}.bkws"

    with zipfile.ZipFile(archive_path) as source:
        entries = {item.filename: source.read(item.filename) for item in source.infolist()}
    if failure == "missing":
        del entries["seed/site_enrichment.json"]
    elif failure == "unsafe_path":
        del entries["seed/site_enrichment.json"]
        entries["../escape"] = b"must not escape staging"
    elif failure == "tampered":
        entries["seed/site_enrichment.json"] += b" "
    elif failure in {"wrong_format", "wrong_schema"}:
        manifest = json.loads(entries["manifest.json"])
        if failure == "wrong_format":
            manifest["format_version"] = 999
        else:
            manifest["schema_version"] = CURRENT_SCHEMA_VERSION + 1
        entries["manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(invalid_archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, payload in entries.items():
            target.writestr(name, payload)

    destination = tmp_path / "must-not-be-accepted"
    with pytest.raises(ExchangeError):
        stage_workspace_archive(invalid_archive, destination)

    assert not destination.exists()
    assert database_path.read_bytes() == source_db_bytes
    assert seed_path.read_bytes() == source_seed_bytes


@pytest.mark.parametrize("limit", ["archive", "unpacked"])
def test_workspace_import_enforces_archive_and_expanded_size_bounds(tmp_path, limit):
    archive_path, _, _ = _make_archive(tmp_path)
    destination = tmp_path / f"bounded-{limit}"
    kwargs = {"max_archive_bytes": 1} if limit == "archive" else {"max_unpacked_bytes": 1}

    with pytest.raises(ExchangeError):
        stage_workspace_archive(archive_path, destination, **kwargs)

    assert not destination.exists()


def test_workspace_import_never_overwrites_an_existing_destination(tmp_path):
    archive_path, _, _ = _make_archive(tmp_path)
    destination = tmp_path / "existing"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ExchangeError):
        stage_workspace_archive(archive_path, destination)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_workspace_export_enforces_compressed_archive_bound_without_output(tmp_path):
    _, database_path, seed_path = _create_synthetic_workspace(tmp_path / "source")
    archive_path = tmp_path / "too-small-limit.bkws"
    database_bytes = database_path.read_bytes()
    seed_bytes = seed_path.read_bytes()

    with pytest.raises(ExchangeError):
        create_workspace_archive(
            database_path,
            seed_path,
            archive_path,
            project_root=PROJECT_ROOT,
            max_archive_bytes=1,
        )

    assert not archive_path.exists()
    assert database_path.read_bytes() == database_bytes
    assert seed_path.read_bytes() == seed_bytes
