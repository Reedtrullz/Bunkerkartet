import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

AUTH = {"Authorization": "Bearer synthetic"}


def payload(batch='projection-1'):
    raw = json.loads(Path('research/example-import.json').read_text())
    raw['batch_id'] = batch
    raw['records'] = [raw['records'][0]]
    raw['records'][0]['external_key'] = 'synthetic:projection'
    return raw


def commit(api, raw):
    p = api.post('/api/admin/imports/preview', json=raw, headers=AUTH)
    assert p.status_code == 200, p.text
    r = api.post('/api/admin/imports/commit', json=raw, headers={**AUTH, 'X-Import-Preview': p.json()['preview_hash']})
    assert r.status_code == 200, r.text
    return p.json()


def test_preview_uses_exact_persisted_candidate_text_and_warning_union(tmp_path):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token='synthetic')))
    first = payload()
    first['records'][0]['warnings'] = ['Keep previous warning']
    commit(api, first)
    second = payload('projection-2')
    second['records'][0].update(condition='  normalized condition  ', short_rationale='  reviewed rationale  ', warnings=['New warning'])
    p = commit(api, second)
    detail = api.get('/api/sites/1', headers=AUTH).json()
    for change in p['records'][0]['changes']:
        assert detail[change['field']] == change['after']
    assert p['records'][0]['effective_warnings'] == detail['warnings']
    assert 'Keep previous warning' in detail['warnings']


def test_readings_have_unique_identity_and_original_citation_titles(tmp_path):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token='synthetic')))
    raw = payload()
    raw['records'][0]['sources'][0]['title'] = 'First reading title'
    commit(api, raw)
    later = payload('projection-2')
    later['records'][0]['sources'][0]['title'] = 'Later reading title'
    commit(api, later)
    with api.app.state.database.connect() as connection:
        source_id = connection.execute('SELECT source_id FROM evidence LIMIT 1').fetchone()[0]
        connection.execute("INSERT INTO evidence (site_id, source_id, role, created_at) VALUES (1, ?, 'identity', '2026-10-03')", (source_id,))
    rows = api.get('/api/sites/1', headers=AUTH).json()['sources']
    assert len(rows) == len({s['evidence_id'] for s in rows}) == 2
    assert {s['title'] for s in rows} == {'First reading title', 'Later reading title'}
    assert all(set(s['source_roles']) == {'source', 'identity'} for s in rows)
