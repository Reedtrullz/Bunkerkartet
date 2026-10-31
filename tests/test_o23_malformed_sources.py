"""Malformed-source behaviour of GET /api/sites?q=: nine JSON and six SQLite BLOB payloads.

Functional cases carried over from the historical normalization test; no timing or profiling.
"""
import json

import pytest
from started_client import StartedClient as TestClient

from app.config import Settings
from app.main import create_app
from scripts.benchmark_catalogue import _seed

TOKEN = {"Authorization": "Bearer synthetic-normalization-only"}

# (label, params, status, expected ids, stored payload_json, authorized)
CASES = tuple(
    (f"source-{name}", {"q": " STRASSE "}, 200, [63, 64], payload, True)
    for name, payload in (
        ("malformed", "{"), ("duplicate-key", '{"sources":[],"sources":[]}'),
        ("trailing", '{"sources":[]} trailing'), ("wrong-root", "[]"),
        ("missing", "{}"), ("wrong-sources", '{"sources":3}'),
        ("empty-sources", '{"sources":[]}'), ("nonobject-reading", '{"sources":[3,null]}'),
        ("json-null", "null"),
    )
) + (
    ("source-blob-own-match", {"q": " STRASSE "}, 500, None, b"{}", True),
    ("source-blob-absent", {"q": " impossible needle "}, 500, None, b"{}", True),
    ("source-blob-blank-q", {"q": " "}, 200, None, b"{}", True),
    ("source-blob-kind-excluded", {"q": " STRASSE ", "site_kind": " ShElTeR "}, 200, [], b"{}", True),
    ("source-blob-invalid", {"q": " STRASSE ", "status": "bad"}, 400, None, b"{}", True),
    ("source-blob-no-auth", {"q": " STRASSE "}, 401, None, b"{}", False),
)


def _seed_sources(database):
    """Authored 64-site catalogue; sites 63 and 64 match STRASSE and 63 has a stored import record."""
    _seed(database, 64)
    record = {"sources": [
        {"title": "Synthetic primary", "source_type": "archive"},
        {"title": "Forensic later \u00c6\u00d8\u00c5", "source_type": "archive"},
    ]}
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with database.connect() as c:
        c.execute("UPDATE sites SET status='rejected' WHERE id=1")
        c.execute("UPDATE sites SET merged_into_id=3 WHERE id=2")
        c.execute("UPDATE sites SET name='Stra\u00dfe evidence',site_kind='bunker' WHERE id IN (63,64)")
        c.execute("UPDATE sites SET access='public',confidence='high',short_rationale='Rationale only',observed_location_text='Location only',condition='Condition only' WHERE id=63")
        c.execute("INSERT INTO import_batches(batch_id,schema_version,generated_at,created_at,committed_at) VALUES('normalization','1.0','2026-01-01','2026-01-01','2026-01-01')")
        c.execute("INSERT INTO import_records(id,batch_id,external_key,site_id,action,payload_json,created_at) VALUES(1,'normalization','benchmark:0063',63,'created',?,'2026-01-01')", (raw,))
        for source_id, site_id in ((1, 63), (2, 63), (3, 64)):
            c.execute("INSERT INTO sources(id,url,title,source_type,excerpt,created_at,updated_at) VALUES(?,?,?,'archive',?,'2026-01-01','2026-01-01')", (source_id, f"https://example.test/normalization/{source_id}", "Archive registry" if source_id != 3 else "Legacy synthetic", "Evidence only" if source_id == 3 else "Synthetic excerpt"))
            c.execute("INSERT INTO evidence(site_id,source_id,role,created_at) VALUES(?,?,'source','2026-01-01')", (site_id, source_id))
            if site_id == 63:
                c.execute("INSERT INTO evidence_items(site_id,source_id,import_record_id,source_index,excerpt,content_kind,role,provenance_status,created_at) VALUES(63,?,1,?,'Synthetic excerpt','summary','context','import_record','2026-01-01')", (source_id, source_id - 1))


@pytest.mark.parametrize("params,status,expected_ids,payload,authorized", [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_malformed_source_rows(tmp_path, params, status, expected_ids, payload, authorized):
    app = create_app(Settings(data_dir=tmp_path, admin_token="synthetic-normalization-only", app_version="normalization-fixture"))
    # A real unhandled exception must surface as HTTP 500, not be re-raised into the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        _seed_sources(app.state.database)
        with app.state.database.connect() as c:
            c.execute("UPDATE import_records SET payload_json=? WHERE id=1", (payload,))
        response = client.get("/api/sites", params=params, headers=TOKEN if authorized else {}, timeout=10)
    assert response.status_code == status, response.text[:500]
    if status == 200:
        ids = [item["id"] for item in response.json()]
        if expected_ids is not None:
            assert ids == expected_ids
        else:  # blank q reads no sources: rejected id 1 and merged id 2 stay hidden
            assert len(ids) == 62 and 1 not in ids and 2 not in ids
    if status == 400:
        assert response.json() == {"detail": "invalid site status"}
