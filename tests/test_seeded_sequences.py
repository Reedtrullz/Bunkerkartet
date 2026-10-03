from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


ADMIN = {"Authorization": "Bearer sequence-test"}
SEEDS = (29, 61, 62026)


def _action_plan(seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    checks = ["stale_edit", "invalid_promotion"]
    rng.shuffle(checks)
    return {
        "seed": seed,
        "token": f"sequence:{seed}",
        "northing": 63.0 + rng.randrange(100, 900) / 100_000,
        "easting": 10.0 + rng.randrange(100, 900) / 100_000,
        "pre_observation_actions": checks,
    }


def _package(plan: dict[str, object]) -> dict[str, object]:
    token = str(plan["token"])
    return {
        "schema_version": "1.0",
        "batch_id": f"sequence-batch-{plan['seed']}",
        "generated_at": "2026-01-01T00:00:00Z",
        "records": [
            {
                "external_key": f"{token}:source", "name": f"Sequence source {plan['seed']}",
                "site_kind": "shelter",
                "geometry": {"latitude": plan["northing"], "longitude": plan["easting"]},
                "precision": "approximate", "uncertainty_m": 45,
                "location_basis": "map_reference", "status": "candidate", "access": "unknown",
                "sources": [{"url": f"https://example.test/{plan['seed']}/source",
                             "title": "Synthetic source evidence", "source_type": "archive",
                             "excerpt": "Synthetic evidence for deterministic API sequences"}],
                "confidence": "medium", "short_rationale": "Synthetic source candidate",
            },
            {
                "external_key": f"{token}:survivor", "name": f"Sequence survivor {plan['seed']}",
                "site_kind": "shelter",
                "geometry": {"latitude": 64.0, "longitude": 11.0},
                "precision": "approximate", "uncertainty_m": 45,
                "location_basis": "map_reference", "status": "candidate", "access": "unknown",
                "sources": [{"url": f"https://example.test/{plan['seed']}/survivor",
                             "title": "Synthetic survivor evidence", "source_type": "archive",
                             "excerpt": "Synthetic evidence for the merge survivor"}],
                "confidence": "medium", "short_rationale": "Synthetic survivor candidate",
            },
        ],
    }


def _expect(response, status: int, trace: list[str], label: str):
    if response.status_code != status:
        pytest.fail(
            f"seeded API action {label!r} expected HTTP {status}, got "
            f"{response.status_code}: {response.text}; seed action trace={trace!r}"
        )
    return response


def _assert_independent_oracle(expected: dict[str, object], actual: dict[str, object]) -> None:
    assert actual["candidate_status"] == expected["candidate_status"]
    assert actual["import_payload_hash"] == expected["import_payload_hash"]
    evidence_ids = actual["evidence_ids"]
    assert isinstance(evidence_ids, list)
    assert len(evidence_ids) == len(set(evidence_ids))
    assert set(evidence_ids) == set(expected["evidence_ids"])


def _run_sequence(tmp_path, seed: int) -> dict[str, object]:
    plan = _action_plan(seed)
    trace: list[str] = []
    app = create_app(Settings(data_dir=tmp_path / f"seed-{seed}", admin_token="sequence-test"))
    with TestClient(app) as client:
        package = _package(plan)
        preview = _expect(
            client.post("/api/admin/imports/preview", headers=ADMIN, json=package), 200,
            trace, "preview two candidates",
        )
        trace.append("preview:two_candidates")
        commit_headers = {**ADMIN, "X-Import-Preview": preview.json()["preview_hash"]}
        committed = _expect(
            client.post("/api/admin/imports/commit", headers=commit_headers, json=package), 200,
            trace, "commit import",
        ).json()
        assert committed["created"] == 2
        assert committed["idempotent"] is False
        trace.append("commit:two_candidates")
        repeated = _expect(
            client.post("/api/admin/imports/commit", headers=ADMIN, json=package), 200,
            trace, "retry import",
        ).json()
        assert repeated == {
            "batch_id": f"sequence-batch-{seed}", "created": 0, "updated": 0,
            "preserved": 0, "evidence_attached": 0, "idempotent": True,
        }
        trace.append("retry_import:idempotent")

        with app.state.database.connect() as connection:
            site_ids = {
                row["external_key"]: int(row["id"])
                for row in connection.execute(
                    "SELECT id,external_key FROM sites WHERE external_key LIKE ?",
                    (f"sequence:{seed}:%",),
                )
            }
            initial_hash = connection.execute(
                "SELECT payload_hash FROM import_batches WHERE batch_id=?",
                (f"sequence-batch-{seed}",),
            ).fetchone()[0]
        source_id = site_ids[f"sequence:{seed}:source"]
        survivor_id = site_ids[f"sequence:{seed}:survivor"]
        initial_source = client.get(f"/api/sites/{source_id}", headers=ADMIN).json()
        source_evidence_id = initial_source["sources"][0]["evidence_id"]
        current_revision = initial_source["revision"]

        for action in plan["pre_observation_actions"]:
            if action == "stale_edit":
                accepted = _expect(
                    client.patch(f"/api/sites/{source_id}", headers=ADMIN,
                                 json={"expected_revision": current_revision,
                                       "short_rationale": "Seeded sequence edit"}),
                    200, trace, "revisioned edit",
                )
                current_revision = accepted.json()["revision"]
                stale = _expect(
                    client.patch(f"/api/sites/{source_id}", headers=ADMIN,
                                 json={"expected_revision": current_revision - 1,
                                       "short_rationale": "Stale overwrite attempt"}),
                    409, trace, "reject stale edit",
                )
                assert "REVISION_MISMATCH" in stale.text
                trace.append("stale_edit:rejected_without_overwrite")
            elif action == "invalid_promotion":
                rejected = _expect(
                    client.post(f"/api/sites/{source_id}/review", headers=ADMIN,
                                json={"action": "accept", "expected_revision": current_revision,
                                      "reason": "Synthetic invalid promotion without selected evidence"}),
                    409, trace, "reject ungrounded promotion",
                )
                assert "dated evidence" in rejected.text
                trace.append("promotion_without_evidence:rejected")

        created_observation = _expect(
            client.post(
                f"/api/sites/{source_id}/observations", headers=ADMIN,
                json={"request_id": f"observation-{seed}", "observed_at": "2026-01-03",
                      "outcome": "found", "note": "Original synthetic field note",
                      "latitude": plan["northing"], "longitude": plan["easting"],
                      "point_role": "feature", "uncertainty_m": 15},
            ), 201, trace, "create field observation",
        ).json()
        observation_id = created_observation["observation"]["id"]
        current_revision += 1
        replay_observation = _expect(
            client.post(
                f"/api/sites/{source_id}/observations", headers=ADMIN,
                json={"request_id": f"observation-{seed}", "observed_at": "2026-01-03",
                      "outcome": "found", "note": "Original synthetic field note",
                      "latitude": plan["northing"], "longitude": plan["easting"],
                      "point_role": "feature", "uncertainty_m": 15},
            ), 200, trace, "retry field observation",
        ).json()
        assert replay_observation["idempotent"] is True
        assert replay_observation["observation"]["id"] == observation_id
        trace.append("observation_retry:idempotent")
        collision = _expect(
            client.post(
                f"/api/sites/{source_id}/observations", headers=ADMIN,
                json={"request_id": f"observation-{seed}", "observed_at": "2026-01-03",
                      "outcome": "found", "note": "Changed payload with same ID",
                      "latitude": plan["northing"], "longitude": plan["easting"],
                      "point_role": "feature", "uncertainty_m": 15},
            ), 409, trace, "reject changed observation retry",
        )
        assert "different payload" in collision.text
        trace.append("observation_retry:changed_payload_rejected")
        with app.state.database.connect() as connection:
            original_observation = dict(connection.execute(
                "SELECT note,payload_hash,site_id FROM field_observations WHERE id=?",
                (observation_id,),
            ).fetchone())

        withdrawn = _expect(
            client.post(
                f"/api/sites/{source_id}/observations/{observation_id}/amendments",
                headers=ADMIN,
                json={"request_id": f"withdraw-{seed}", "expected_observation_revision": 1,
                      "reason": "Synthetic withdrawal for sequence verification",
                      "status": "withdrawn"},
            ), 201, trace, "withdraw observation",
        ).json()
        assert withdrawn["effective_observation"]["withdrawn"] is True
        trace.append("observation:withdrawn_append_only")

        question = _expect(
            client.post("/api/research/questions", headers=ADMIN,
                        json={"external_key": f"sequence:{seed}:survivor", "kind": "identity",
                              "wording": "Which synthetic identity is supported?"}),
            201, trace, "create research question",
        ).json()
        hypothesis = _expect(
            client.post(
                f"/api/research/questions/{question['id']}/hypotheses", headers=ADMIN,
                json={"expected_question_revision": 1, "label": "Alternative synthetic identity",
                      "original_wording": "A separate synthetic identity hypothesis"},
            ), 201, trace, "create identity hypothesis",
        ).json()
        decided = _expect(
            client.patch(
                f"/api/research/hypotheses/{hypothesis['id']}", headers=ADMIN,
                json={"expected_revision": 1, "expected_question_revision": 2,
                      "state": "rejected", "reason": "Synthetic oracle rejects unsupported alternative"},
            ), 200, trace, "decide identity hypothesis",
        ).json()
        assert decided["state"] == "rejected"
        assert decided["revision"] == 2
        trace.append("hypothesis:revisioned_rejection")

        with app.state.database.connect() as connection:
            connection.execute(
                "UPDATE sites SET approach_latitude=?,approach_longitude=?,approach_access='public',"
                "approach_note='Synthetic public approach',approach_reviewed_at='2026-01-01T00:00:00+00:00' WHERE id=?",
                (plan["northing"], plan["easting"], source_id),
            )
            stop_snapshot = [{
                "site_id": source_id, "name": f"Sequence source {seed}",
                "lat": plan["northing"], "lon": plan["easting"],
                "access_note": "Synthetic public approach",
                "approach_reviewed_at": "2026-01-01T00:00:00+00:00", "warnings": [],
            }]
            connection.execute(
                """INSERT INTO route_plans
                   (name,start_json,waypoints_json,stops_json,route_warnings_json,distance_m,
                    duration_s,geometry_json,gpx_text,created_at,details_json)
                   VALUES (?,?,?,?,'[]',1000,300,?,'<gpx/>','2026-01-01','{}')""",
                (f"Synthetic route {seed}", json.dumps({"lat": plan["northing"], "lon": plan["easting"]}),
                 json.dumps([]), json.dumps(stop_snapshot),
                 json.dumps([[plan["easting"], plan["northing"]],
                             [plan["easting"] + 0.01, plan["northing"] + 0.01]])),
            )
            route_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        route_before = _expect(client.get(f"/api/routes/{route_id}", headers=ADMIN), 200,
                               trace, "read saved synthetic route").json()
        assert "current_site_changed" in route_before, route_before
        assert route_before["current_site_changed"] is False
        trace.append("route:approach_snapshot_current")

        source = client.get(f"/api/sites/{source_id}", headers=ADMIN).json()
        survivor = client.get(f"/api/sites/{survivor_id}", headers=ADMIN).json()
        survivor_evidence_ids = [
            row["evidence_id"] for row in survivor["sources"] if row["evidence_id"] is not None
        ]
        merge_body = {
            "expected_revision": source["revision"], "target_site_id": survivor_id,
            "target_expected_revision": survivor["revision"],
            "reason": "Synthetic source and survivor merge",
        }
        preview = _expect(
            client.post(f"/api/sites/{source_id}/merge-preview", headers=ADMIN, json=merge_body),
            200, trace, "preview merge",
        ).json()
        merge_response = _expect(
            client.post(
                f"/api/sites/{source_id}/review", headers=ADMIN,
                json={"action": "merge", **merge_body, "merge_preview_hash": preview["preview_hash"]},
            ), 200, trace, "commit reviewed merge",
        ).json()
        assert merge_response["status"] == "merged"
        trace.append("merge:reviewed_transfer")
        route_after = _expect(client.get(f"/api/routes/{route_id}", headers=ADMIN), 200,
                              trace, "revalidate saved route after merge").json()
        assert route_after["current_site_changed"] is True
        trace.append("route:merged_stop_invalidated")

        with app.state.database.connect() as connection:
            import_hash = connection.execute(
                "SELECT payload_hash FROM import_batches WHERE batch_id=?",
                (f"sequence-batch-{seed}",),
            ).fetchone()[0]
            source_state = connection.execute(
                "SELECT status,merged_into_id FROM sites WHERE id=?", (source_id,)
            ).fetchone()
            survivor_state = connection.execute(
                "SELECT status,merged_into_id FROM sites WHERE id=?", (survivor_id,)
            ).fetchone()
            evidence_ids = [int(row[0]) for row in connection.execute(
                "SELECT id FROM evidence_items WHERE site_id=? ORDER BY id", (survivor_id,)
            )]
            observation_after_merge = dict(connection.execute(
                "SELECT note,payload_hash,site_id FROM field_observations WHERE id=?",
                (observation_id,),
            ).fetchone())
        target_detail = client.get(f"/api/sites/{survivor_id}", headers=ADMIN).json()
        effective_observation = client.get(
            f"/api/sites/{survivor_id}/observations/{observation_id}/effective", headers=ADMIN
        ).json()
        assert effective_observation["withdrawn"] is True
        assert observation_after_merge == {
            **original_observation, "site_id": survivor_id,
        }
        assert source_state["status"] == "rejected" and source_state["merged_into_id"] == survivor_id
        assert survivor_state["status"] == "candidate" and survivor_state["merged_into_id"] is None
        expected = {
            "candidate_status": "candidate", "import_payload_hash": initial_hash,
            "evidence_ids": [source_evidence_id, *survivor_evidence_ids],
        }
        actual = {
            "candidate_status": survivor_state["status"],
            "import_payload_hash": import_hash,
            "evidence_ids": evidence_ids,
        }
        _assert_independent_oracle(expected, actual)
        assert len(evidence_ids) == 2
        trace.append("oracle:status_hash_evidence_observation_route_hypothesis")
        return {"seed": seed, "actions": trace, "site_ids": [source_id, survivor_id],
                "observation_id": observation_id, "evidence_ids": evidence_ids,
                "hypothesis_state": decided["state"],
                "source_status": source_state["status"],
                "survivor_status": survivor_state["status"],
                "route_current_site_changed": route_after["current_site_changed"]}


@pytest.mark.parametrize("seed", SEEDS)
def test_fixed_seed_api_action_sequences_replay_to_the_same_oracle_state(tmp_path, seed):
    first = _run_sequence(tmp_path / "first", seed)
    second = _run_sequence(tmp_path / "second", seed)
    assert first == second


def test_independent_oracle_detects_candidate_hash_and_duplicate_evidence_regressions():
    expected = {
        "candidate_status": "candidate",
        "import_payload_hash": "a" * 64,
        "evidence_ids": [17, 23],
    }
    _assert_independent_oracle(expected, dict(expected))
    mutations = [
        {**expected, "candidate_status": "likely"},
        {**expected, "import_payload_hash": "b" * 64},
        {**expected, "evidence_ids": [17, 17, 23]},
    ]
    for corrupted in mutations:
        with pytest.raises(AssertionError):
            _assert_independent_oracle(expected, corrupted)
