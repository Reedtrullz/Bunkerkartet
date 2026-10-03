from test_import_api import client, package, auth, commit_previewed


def test_all_protected_operations_require_observed_revision(tmp_path):
    api = client(tmp_path)
    commit_previewed(api, package())
    requests = [('/api/sites/1/review', {'action': 'research'}), ('/api/sites/1/approach', {'latitude': 63.4, 'longitude': 10.4, 'access': 'public', 'note': 'Synthetic public approach'}), ('/api/sites/1/observations/1/adopt-location', {})]
    for path, body in requests:
        response = api.post(path, json=body, headers=auth())
        assert response.status_code == 422, response.text
    assert api.get('/api/sites/1', headers=auth()).json()['revision'] == 1


def test_two_different_approaches_from_same_snapshot_have_one_commit(tmp_path):
    api = client(tmp_path)
    commit_previewed(api, package())
    original = api.get('/api/sites/1', headers=auth()).json()
    body = {'expected_revision': original['revision'], 'latitude': 63.4, 'longitude': 10.4, 'access': 'public', 'note': 'First reviewed viewpoint'}
    first = api.post('/api/sites/1/approach', json=body, headers=auth())
    before_events = api.get('/api/sites/1/events', headers=auth()).json()
    second = api.post('/api/sites/1/approach', json={**body, 'longitude': 10.41, 'note': 'Different viewpoint'}, headers=auth())
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()['detail']['code'] == 'REVISION_MISMATCH'
    assert api.get('/api/sites/1/events', headers=auth()).json() == before_events
    assert api.get('/api/sites/1', headers=auth()).json()['approach_longitude'] == 10.4


def test_merge_requires_target_snapshot_and_rejects_changed_target(tmp_path):
    api = client(tmp_path)
    commit_previewed(api, package())
    commit_previewed(api, package('target-batch', external_key='target:2'))
    missing = api.post('/api/sites/1/review', json={'action': 'merge', 'expected_revision': 1, 'target_site_id': 2}, headers=auth())
    assert missing.status_code == 422
    assert api.patch('/api/sites/2', json={'expected_revision': 1, 'name': 'Changed target'}, headers=auth()).status_code == 200
    events = api.get('/api/sites/1/events', headers=auth()).json()
    stale = api.post('/api/sites/1/review', json={'action': 'merge', 'expected_revision': 1, 'target_site_id': 2, 'target_expected_revision': 1, 'reason':'Previously reviewed duplicate', 'merge_preview_hash':'0'*64}, headers=auth())
    assert stale.status_code == 409
    assert api.get('/api/sites/1/events', headers=auth()).json() == events
    assert api.get('/api/sites/1', headers=auth()).json()['merged_into_id'] is None
