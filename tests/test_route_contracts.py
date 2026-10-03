from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routes import normalize_ors_response
from test_route_api import seed_site


@pytest.mark.parametrize('blocked', ['missing', 'location', 'rejected', 'merged'])
def test_eligibility_is_shared_by_list_detail_and_route(tmp_path, blocked):
    api = TestClient(create_app(Settings(data_dir=tmp_path, admin_token='secret', ors_api_key='key')))
    site_id = seed_site(api, approach=blocked != 'missing', location_review_required=int(blocked == 'location'))
    with api.app.state.database.connect() as connection:
        if blocked == 'rejected':
            connection.execute("UPDATE sites SET status='rejected' WHERE id=?", (site_id,))
        if blocked == 'merged':
            connection.execute('UPDATE sites SET merged_into_id=id WHERE id=?', (site_id,))
    headers = {'Authorization': 'Bearer secret'}
    detail = api.get(f'/api/sites/{site_id}', headers=headers).json()
    assert detail['route_eligible'] is False
    assert detail['route_blocking_reason']
    response = api.post('/api/routes', headers=headers, json={'start': {'lat':63.4,'lon':10.4},'site_ids':[site_id]})
    assert response.status_code == 409
    assert detail['route_blocking_reason'] in response.json()['detail']


def provider():
    return {'features':[{'geometry':{'type':'LineString','coordinates':[[10,63],[11,64],[12,65]]},'properties':{'summary':{'distance':100,'duration':90},'way_points':[0,1,2]}}]}


@pytest.mark.parametrize('change', ['bool_coordinate','bool_distance','bool_duration','backward','duplicate','wrong_count','nonfinite'])
def test_provider_rejects_ambiguous_numbers_and_waypoints(change):
    payload = deepcopy(provider())
    feature = payload['features'][0]
    if change == 'bool_coordinate': feature['geometry']['coordinates'][0][0] = True
    if change == 'bool_distance': feature['properties']['summary']['distance'] = True
    if change == 'bool_duration': feature['properties']['summary']['duration'] = False
    if change == 'backward': feature['properties']['way_points'] = [0,2,1]
    if change == 'duplicate': feature['properties']['way_points'] = [0,1,1]
    if change == 'wrong_count': feature['properties']['way_points'] = [0,2]
    if change == 'nonfinite': feature['properties']['summary']['distance'] = float('inf')
    with pytest.raises(ValueError):
        normalize_ors_response(payload, requested_waypoint_count=3)
