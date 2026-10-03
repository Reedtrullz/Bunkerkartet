import pytest

from app.db import decode_stored_json
from test_import_api import auth, client
from test_route_api import seed_site


@pytest.mark.parametrize('raw,state', [(None,'absent_legacy'),('[]','valid_empty'),('["safe"]','valid'),('{','malformed'),('{}','wrong_shape'),('[1]','wrong_shape')])
def test_stored_string_list_states(raw,state):
    value, status = decode_stored_json(raw, 'strings')
    assert status == state
    if state in ('malformed','wrong_shape'): assert value is None


def test_one_corrupt_route_does_not_hide_healthy_routes_or_export_gpx(tmp_path,monkeypatch):
    from app.routes import RouteResult
    api = client(tmp_path)
    object.__setattr__(api.app.state.settings, 'ors_api_key', 'synthetic')
    monkeypatch.setattr('app.main.fetch_openrouteservice',lambda key,coordinates:RouteResult(100,90,coordinates,[0,1]))
    site_id = seed_site(api)
    body={'start':{'lat':63.4,'lon':10.4},'site_ids':[site_id]}
    first=api.post('/api/routes',headers=auth(),json=body).json()['id']
    second=api.post('/api/routes',headers=auth(),json=body).json()['id']
    with api.app.state.database.connect() as connection:
        connection.execute("UPDATE route_plans SET geometry_json='{}' WHERE id=?",(first,))
    listed=api.get('/api/routes',headers=auth()).json()
    assert len(listed)==2
    assert next(row for row in listed if row['id']==first)['data_status']=='unavailable'
    assert next(row for row in listed if row['id']==second)['data_status']=='valid'
    detail=api.get(f'/api/routes/{first}',headers=auth()).json()
    assert detail['data_status']=='unavailable'
    assert 'gpx' not in detail and 'geometry' not in detail


def test_corrupt_site_warnings_are_visibly_unavailable(tmp_path):
    api=client(tmp_path); site_id=seed_site(api)
    with api.app.state.database.connect() as connection:
        connection.execute("UPDATE sites SET warnings_json='{}' WHERE id=?",(site_id,))
    body=api.get(f'/api/sites/{site_id}',headers=auth()).json()
    assert body['data_status']=='unavailable'
    assert body['warnings'] is None
    assert not body['route_eligible']
