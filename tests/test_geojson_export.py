from test_import_api import client,auth,package,commit_previewed
from contextlib import contextmanager
import math

import pytest


def test_geojson_keeps_null_geometry_ids_axis_order_and_explicit_point_roles(tmp_path):
    api=client(tmp_path);body=package()
    body['records'][0]['geometry']['longitude']=180
    assert commit_previewed(api,body).status_code==200
    assert api.post('/api/sites/1/approach',headers=auth(),json={'expected_revision':1,'latitude':63.41,'longitude':10.41,'access':'public','note':'Synthetic public path'}).status_code==200
    for role in ('entrance','viewpoint'):
        response=api.post('/api/sites/1/observations',headers=auth(),json={'observed_at':'2026-10-03','outcome':'found','note':'Synthetic scoped evidence','latitude':63.42,'longitude':10.42,'point_role':role,'uncertainty_m':25})
        assert response.status_code==201,response.text
    features=api.get('/api/sites.geojson',headers=auth()).json()['features']
    assert features[0]['id']==1 and features[0]['geometry']['coordinates']==[-180.0,63.4]
    assert {feature['properties']['point_role'] for feature in features}=={'feature','approach','entrance','viewpoint'}
    approach=next(feature for feature in features if feature['properties']['point_role']=='approach')
    assert approach['properties']['uncertainty_m'] is None
    assert approach['geometry']['coordinates']==[10.41,63.41]
    assert all(feature['id'] is not None for feature in features)
    assert api.get('/api/sites/1',headers=auth()).json()['longitude']==180
    second=package('no-point',external_key='unknown:two')
    second['records'][0].update(geometry=None,precision='unknown',uncertainty_m=None)
    assert commit_previewed(api,second).status_code==200
    assert next(feature for feature in api.get('/api/sites.geojson',headers=auth()).json()['features'] if feature['id']==2)['geometry'] is None


@pytest.mark.parametrize('size', [0, 3, 501])
def test_geojson_raw_reads_are_bounded_and_keep_site_and_observation_order(tmp_path, monkeypatch, size):
    api = client(tmp_path)
    database = getattr(api.app, 'state').database
    with database.connect() as connection:
        connection.executemany(
            """INSERT INTO sites (id,external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (?,?,?,'shelter','unknown','map_reference','2026-01-01','2026-01-01')""",
            [(i, f'geo:{i}', f'Site {i:04}') for i in range(1, size + 1)],
        )
        connection.executemany(
            """INSERT INTO field_observations
               (site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
                photo_urls_json,context_json,request_id,payload_hash,created_at)
               VALUES (?,'2026-01-03','found','Private fixture note',63.4,180,'entrance',12,
                       '[]','{}',?,?,'2026-01-03')""",
            [(i, f'geo-observation:{i}', f'{i:064x}') for i in range(1, size + 1)],
        )
    statements = []
    original_connect = database.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, 'connect', traced_connect)
    response = api.get('/api/sites.geojson', headers=auth())
    assert response.status_code == 200
    features = response.json()['features']
    assert [feature['id'] for feature in features] == [value for i in range(1, size + 1) for value in (i, f'observation-{i}')]
    assert all(features[i]['geometry'] is None for i in range(0, len(features), 2))
    assert all(features[i]['geometry']['coordinates'] == [-180.0, 63.4] for i in range(1, len(features), 2))
    assert 'Private fixture note' not in response.text
    reads = [sql for sql in statements if 'FROM field_observations' in sql]
    assert len(reads) == math.ceil(size / 500)
    assert all(len(sql.partition(' IN (')[2].partition(')')[0].split(',')) <= 500 for sql in reads)
    assert sum('FROM observation_amendments' in sql for sql in statements) == size
    statements.clear()
    assert api.get('/api/sites.geojson').status_code == 401
    assert not any('FROM field_observations' in sql for sql in statements)
