from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pilots import (
    GeographyPolicy,
    HistoricalAssertion,
    HistoricalPolicy,
    OfflinePolicy,
    PilotDisabled,
    PilotPolicies,
    PublicationDecision,
    PublicationPolicy,
    ReaderPolicy,
    SYNTHETIC_GEOGRAPHIES,
    add_historical_assertion,
    assess_offline_item,
    authorize_reader,
    build_offline_pack,
    build_publication_preview,
    clear_offline_pack,
    geography_key,
    inspect_offline_pack,
    issue_reader_credential,
    list_time_layer,
    out_of_scope_review,
    persist_publication_preview,
    queue_offline_observation,
    record_offline_review,
    revoke_reader_credential,
    verify_publication_preview,
    install_pilot_routes,
    PILOT_SCHEMA,
    PILOT_REQUIRED,
)


NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(PILOT_SCHEMA)
    yield db
    db.close()


def enabled_policies() -> PilotPolicies:
    return PilotPolicies(
        offline=OfflinePolicy(enabled=True),
        historical=HistoricalPolicy(enabled=True),
        readers=ReaderPolicy(enabled=True),
        publication=PublicationPolicy(enabled=True),
        geography=GeographyPolicy(enabled=True, descriptors=SYNTHETIC_GEOGRAPHIES),
    )


def test_pilot_schema_is_exported_and_default_policy_is_disabled():
    assert set(PILOT_REQUIRED) == {
        "pilot_offline_packs",
        "pilot_offline_outbox",
        "pilot_historical_assertions",
        "pilot_reader_credentials",
        "pilot_publication_receipts",
    }
    db = sqlite3.connect(":memory:")
    db.executescript(PILOT_SCHEMA)
    for table, columns in PILOT_REQUIRED.items():
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        assert set(columns) <= present
    db.close()
    policy = PilotPolicies()
    assert not any((policy.offline.enabled, policy.historical.enabled, policy.readers.enabled,
                    policy.publication.enabled, policy.geography.enabled))


def test_disabled_features_fail_closed(connection):
    with pytest.raises(PilotDisabled):
        build_offline_pack(connection, [{"external_key": "synthetic:1", "name": "Test"}],
                           ["external_key", "name"], OfflinePolicy(), now=NOW)
    with pytest.raises(PilotDisabled):
        issue_reader_credential(connection, ReaderPolicy(), now=NOW, ttl_seconds=60,
                                 scopes=["sites:read"])
    with pytest.raises(PilotDisabled):
        build_publication_preview([], {}, ["name"], PublicationPolicy())


def test_offline_pack_checksum_expiry_label_and_explicit_clear_cascade(connection):
    policy = OfflinePolicy(enabled=True, max_ttl_seconds=3600)
    receipt = build_offline_pack(
        connection,
        [{"external_key": "synthetic:a", "name": "Alpha", "revision": 4,
          "access": "private", "field_observations": [{"note": "must not enter pack"}]}],
        ["external_key", "name", "revision"], policy, now=NOW, ttl_seconds=60,
    )
    assert receipt["sha256"] == hashlib.sha256(receipt["payload_json"].encode()).hexdigest()
    payload = json.loads(receipt["payload_json"])
    assert payload["records"] == [{"external_key": "synthetic:a", "name": "Alpha", "revision": 4}]
    assert "private" not in receipt["payload_json"]
    assert "must not enter" not in receipt["payload_json"]
    assert inspect_offline_pack(connection, receipt["pack_id"], policy, now=NOW + timedelta(seconds=30))["stale"] is False
    assert inspect_offline_pack(connection, receipt["pack_id"], policy, now=NOW + timedelta(seconds=60))["stale"] is True
    with pytest.raises(ValueError, match="explicit"):
        clear_offline_pack(connection, receipt["pack_id"], policy, explicit=False)
    queue_offline_observation(connection, receipt["pack_id"], "request-1", "synthetic:a", 4,
                              {"outcome": "found", "note": "synthetic"}, policy,
                              now=NOW + timedelta(seconds=10))
    assert clear_offline_pack(connection, receipt["pack_id"], policy, explicit=True)["cleared"] is True
    assert connection.execute("SELECT count(*) FROM pilot_offline_packs").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM pilot_offline_outbox").fetchone()[0] == 0


def test_offline_retry_is_idempotent_and_site_changes_require_manual_review(connection):
    policy = OfflinePolicy(enabled=True)
    pack = build_offline_pack(connection, [{"external_key": "synthetic:a", "revision": 7}],
                              ["external_key", "revision"], policy, now=NOW)
    first = queue_offline_observation(connection, pack["pack_id"], "device-request-1",
                                      "synthetic:a", 7, {"outcome": "found"}, policy, now=NOW)
    retry = queue_offline_observation(connection, pack["pack_id"], "device-request-1",
                                      "synthetic:a", 7, {"outcome": "found"}, policy, now=NOW)
    assert retry["duplicate_retry"] is True
    assert retry["outbox_id"] == first["outbox_id"]
    assert connection.execute("SELECT count(*) FROM pilot_offline_outbox").fetchone()[0] == 1
    with pytest.raises(ValueError, match="request ID"):
        queue_offline_observation(connection, pack["pack_id"], "device-request-1",
                                  "synthetic:a", 7, {"outcome": "different"}, policy, now=NOW)

    changed = assess_offline_item(connection, pack["pack_id"], "device-request-1",
                                  current_site_revision=8, policy=policy, now=NOW)
    assert changed["state"] == "needs_review"
    assert changed["reason"] == "site_changed"
    with pytest.raises(ValueError, match="changed"):
        record_offline_review(connection, pack["pack_id"], "device-request-1", decision="approve",
                              reviewer_ref="owner-review-1", current_site_revision=8,
                              policy=policy, now=NOW)
    same = assess_offline_item(connection, pack["pack_id"], "device-request-1",
                               current_site_revision=7, policy=policy, now=NOW)
    assert same["state"] == "pending_review"
    approved = record_offline_review(connection, pack["pack_id"], "device-request-1",
                                     decision="approve", reviewer_ref="owner-review-2",
                                     current_site_revision=7, policy=policy, now=NOW)
    assert approved["state"] == "approved_for_sync"
    assert approved["synced"] is False
    approved_retry = queue_offline_observation(connection, pack["pack_id"], "device-request-1",
                                               "synthetic:a", 7, {"outcome": "found"}, policy, now=NOW)
    assert approved_retry["state"] == "approved_for_sync"
    invalidated = assess_offline_item(connection, pack["pack_id"], "device-request-1",
                                      current_site_revision=9, policy=policy, now=NOW)
    assert invalidated["state"] == "needs_review"
    stored = connection.execute("SELECT reviewer_ref,reviewed_at FROM pilot_offline_outbox").fetchone()
    assert stored["reviewer_ref"] is None and stored["reviewed_at"] is None


def test_offline_limits_reject_unbounded_or_unapproved_fields(connection):
    policy = OfflinePolicy(enabled=True, max_sites=1, allowed_fields=frozenset({"external_key", "name"}))
    with pytest.raises(ValueError, match="site limit"):
        build_offline_pack(connection, [{"external_key": "a"}, {"external_key": "b"}],
                           ["external_key"], policy, now=NOW)
    with pytest.raises(ValueError, match="field"):
        build_offline_pack(connection, [{"external_key": "a", "access": "private"}],
                           ["external_key", "access"], policy, now=NOW)


def test_expired_records_remain_explicitly_bounded_until_owner_clears_them(connection):
    offline = OfflinePolicy(enabled=True, max_stored_packs=1)
    build_offline_pack(connection, [{"external_key": "synthetic:a", "revision": 1}],
                        ["external_key", "revision"], offline, now=NOW)
    with pytest.raises(ValueError, match="explicitly clear"):
        build_offline_pack(connection, [{"external_key": "synthetic:b", "revision": 1}],
                            ["external_key", "revision"], offline, now=NOW)

    reader = ReaderPolicy(enabled=True, max_stored_credentials=1)
    issue_reader_credential(connection, reader, now=NOW, ttl_seconds=60, scopes=["sites:read"])
    with pytest.raises(ValueError, match="stored reader credential"):
        issue_reader_credential(connection, reader, now=NOW, ttl_seconds=60, scopes=["sites:read"])

    publication = PublicationPolicy(enabled=True, max_receipts=1)
    record = {"external_key": "synthetic:public", "name": "Test"}
    decision = PublicationDecision("include", "selection", "approved", "rights", "withheld", "coordinate")
    preview = build_publication_preview([record], {"synthetic:public": decision},
                                        ["external_key", "name"], publication)
    persist_publication_preview(connection, preview, publication, now=NOW)
    another = build_publication_preview([record], {"synthetic:public": decision},
                                        ["external_key", "name"], publication)
    with pytest.raises(ValueError, match="receipt limit"):
        persist_publication_preview(connection, another, publication, now=NOW)


def test_historical_assertions_keep_contradictions_sources_and_time_context_separate(connection):
    policy = HistoricalPolicy(enabled=True)
    connection.execute("CREATE TABLE sites (external_key TEXT, status TEXT, access TEXT)")
    connection.execute("INSERT INTO sites VALUES ('synthetic:site-1','candidate','unknown')")
    records = [
        HistoricalAssertion(
            assertion_key="assertion:a", subject_key="synthetic:site-1", predicate="connected_to",
            object_key="synthetic:site-2", source_ref="https://example.test/archive/a",
            certainty="uncertain", date_context="circa 1942, exact date unknown",
            valid_from="1942-01-01", valid_to="1942-12-31", date_precision="year",
            curator_disposition="pending", uncertainty="The source gives only a year.",
        ),
        HistoricalAssertion(
            assertion_key="assertion:b", subject_key="synthetic:site-1", predicate="not_connected_to",
            object_key="synthetic:site-2", source_ref="archive-id:synthetic-02",
            certainty="uncertain", date_context="1942 assessment; source conflicts with A",
            valid_from="1942-01-01", valid_to="1942-12-31", date_precision="year",
            curator_disposition="deferred", uncertainty="Conflicting source retained.",
        ),
    ]
    for record in records:
        add_historical_assertion(connection, record, policy, now=NOW)
    layer = list_time_layer(connection, "1942-06-01", policy)
    assert [item["assertion_key"] for item in layer] == ["assertion:a", "assertion:b"]
    assert all(item["date_match"] == "within_interval" for item in layer)
    assert {item["source_ref"] for item in layer} == {
        "https://example.test/archive/a", "archive-id:synthetic-02"
    }
    assert tuple(connection.execute("SELECT status,access FROM sites WHERE external_key='synthetic:site-1'").fetchone()) == (
        "candidate", "unknown",
    )
    with pytest.raises(ValueError, match="source"):
        add_historical_assertion(
            connection,
            HistoricalAssertion("assertion:no-source", "a", "related_to", "b", "", "uncertain",
                                "undated", None, None, "unknown", "pending", "unknown"),
            policy, now=NOW,
        )


def test_historical_date_intervals_are_validated_and_unknowns_remain_visible(connection):
    policy = HistoricalPolicy(enabled=True)
    invalid = HistoricalAssertion("bad", "a", "related_to", "b", "archive:1", "uncertain",
                                  "range disputed", "1943-01-01", "1942-01-01", "range", "pending", "dates conflict")
    with pytest.raises(ValueError, match="interval"):
        add_historical_assertion(connection, invalid, policy, now=NOW)
    unknown = HistoricalAssertion("unknown", "a", "related_to", "b", "archive:2", "possible",
                                  "date not recorded", None, None, "unknown", "deferred", "undated")
    add_historical_assertion(connection, unknown, policy, now=NOW)
    assert list_time_layer(connection, "1942-01-01", policy)[0]["date_match"] == "unknown"


def test_reader_credentials_store_only_salted_hash_and_expire_revoke(connection):
    policy = ReaderPolicy(enabled=True, max_ttl_seconds=600)
    issued = issue_reader_credential(connection, policy, now=NOW, ttl_seconds=120,
                                     scopes=["sites:read", "history:read"])
    assert issued["token"]
    stored = dict(connection.execute("SELECT * FROM pilot_reader_credentials").fetchone())
    assert "token" not in stored
    assert issued["token"] not in repr(stored)
    assert stored["token_hash"] != issued["token"]
    grant = authorize_reader(connection, issued["token"], NOW + timedelta(seconds=1))
    assert grant.scopes == frozenset({"sites:read", "history:read"})
    assert grant.read_only is True
    with pytest.raises(PermissionError, match="read-only"):
        grant.require_mutation()
    with pytest.raises(PermissionError):
        authorize_reader(connection, issued["token"], NOW + timedelta(seconds=120))
    revoke_reader_credential(connection, issued["credential_id"], policy, now=NOW + timedelta(seconds=5))
    with pytest.raises(PermissionError):
        authorize_reader(connection, issued["token"], NOW + timedelta(seconds=6))


def test_reader_rejects_mutation_scopes_and_invalid_expiry(connection):
    policy = ReaderPolicy(enabled=True, max_ttl_seconds=60)
    with pytest.raises(ValueError, match="read-only"):
        issue_reader_credential(connection, policy, now=NOW, ttl_seconds=30, scopes=["sites:write"])
    with pytest.raises(ValueError, match="TTL"):
        issue_reader_credential(connection, policy, now=NOW, ttl_seconds=61, scopes=["sites:read"])


def test_publication_preview_binds_membership_and_decisions_and_excludes_canaries(connection):
    policy = PublicationPolicy(enabled=True)
    records = [
        {"external_key": "synthetic:approved", "name": "Public sample", "latitude": 4.0,
         "longitude": 12.0, "private_note": "CANARY-PRIVATE", "field_observations": ["CANARY-OBS"],
         "route_start": "CANARY-START", "raw_import_payload": "CANARY-RAW"},
        {"external_key": "synthetic:withheld", "name": "Withheld sample", "latitude": 4.1,
         "longitude": 12.1},
    ]
    decisions = {
        "synthetic:approved": PublicationDecision(
            selection="include", selection_ref="selection-review-7", rights="approved",
            rights_ref="rights-record-4", coordinate_mode="exact", coordinate_ref="coordinate-review-3",
        ),
        "synthetic:withheld": PublicationDecision(
            selection="exclude", selection_ref="selection-review-8", rights="pending",
            rights_ref="rights-pending-9", coordinate_mode="withheld", coordinate_ref="location-withheld-1",
        ),
    }
    preview = build_publication_preview(records, decisions, ["external_key", "name", "latitude", "longitude"], policy)
    assert [item["external_key"] for item in preview["entries"]] == ["synthetic:approved"]
    serialized = json.dumps(preview, sort_keys=True)
    for canary in ("CANARY-PRIVATE", "CANARY-OBS", "CANARY-START", "CANARY-RAW"):
        assert canary not in serialized
    assert verify_publication_preview(preview) is True
    receipt = persist_publication_preview(connection, preview, policy, now=NOW)
    stored = connection.execute("SELECT * FROM pilot_publication_receipts").fetchone()
    assert receipt["hosted"] is False and receipt["deployed"] is False
    assert stored["preview_sha256"] == preview["preview_sha256"]
    assert json.loads(stored["package_json"])["entries"] == preview["entries"]
    changed = json.loads(json.dumps(preview))
    changed["entries"].append({"external_key": "synthetic:extra"})
    assert verify_publication_preview(changed) is False


def test_publication_requires_explicit_decisions_and_coordinate_approval():
    policy = PublicationPolicy(enabled=True)
    record = {"external_key": "synthetic:1", "name": "Example", "latitude": 5.0, "longitude": 13.0}
    incomplete = PublicationDecision("include", "selection-1", "approved", "rights-1", "withheld", "location-1")
    with pytest.raises(ValueError, match="coordinate"):
        build_publication_preview([record], {"synthetic:1": incomplete},
                                  ["external_key", "latitude", "longitude"], policy)
    with pytest.raises(ValueError, match="explicit"):
        build_publication_preview([record], {}, ["external_key", "name"], policy)


def test_synthetic_geographies_use_consistent_defaults_and_collision_safe_keys():
    assert len(SYNTHETIC_GEOGRAPHIES) == 2
    alpha, beta = SYNTHETIC_GEOGRAPHIES
    assert alpha.key_namespace != beta.key_namespace
    assert alpha.map_defaults["center"] != beta.map_defaults["center"]
    assert alpha.bounding_box != beta.bounding_box
    left = geography_key(alpha, "source-alpha", "id:b")
    right = geography_key(alpha, "source-alpha:sub", "id:b")
    cross_scope = geography_key(beta, "source-beta", "id:b")
    assert len({left, right, cross_scope}) == 3
    with pytest.raises(ValueError, match="not approved"):
        geography_key(alpha, "unapproved-source", "id:b")
    assert "SYNTHETIC" in " ".join(alpha.warnings).upper()


def test_out_of_scope_geography_requests_review_without_coordinate_coercion():
    alpha = SYNTHETIC_GEOGRAPHIES[0]
    lat, lon = 40.25, 12.5
    result = out_of_scope_review(alpha, lat, lon)
    assert result["review_required"] is True
    assert result["coordinates"] == {"latitude": lat, "longitude": lon}
    assert result["coerced"] is False


def test_installer_registers_routes_without_implicitly_enabling_features(tmp_path):
    app = FastAPI()
    from app.db import Database

    database = Database(tmp_path / "pilots.sqlite3")
    with database.connect() as connection:
        connection.executescript(PILOT_SCHEMA)

    def admin_guard():
        return None

    def site_detail(connection, row):
        return dict(row)

    install_pilot_routes(app, database, admin_guard, site_detail)
    paths = {route.path for route in app.routes}
    assert "/api/pilots/status" in paths
    assert "/api/pilots/offline/packs" in paths
    assert "/api/pilots/historical/assertions" in paths
    with TestClient(app) as client:
        response = client.get("/api/pilots/status")
        geography = client.get("/api/pilots/geographies")
        offline = client.post("/api/pilots/offline/packs", json={
            "site_ids": [1], "selected_fields": ["external_key", "revision"], "ttl_seconds": 60,
        })
    assert response.status_code == 200
    assert response.json()["enabled"] == []
    assert geography.status_code == 404
    assert offline.status_code == 404
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/pilots/") and any(
            method in getattr(route, "methods", set()) for method in {"POST", "PATCH", "PUT", "DELETE"}
        ):
            assert admin_guard in [dependency.call for dependency in route.dependant.dependencies]


def test_installer_can_take_explicit_policies_without_settings(tmp_path):
    app = FastAPI()
    from app.db import Database

    database = Database(tmp_path / "pilots.sqlite3")
    with database.connect() as connection:
        connection.executescript(PILOT_SCHEMA)
    install_pilot_routes(app, database, lambda: None, lambda connection, row: dict(row),
                         policies=enabled_policies())
    assert any(route.path == "/api/pilots/publication/preview" for route in app.routes)
