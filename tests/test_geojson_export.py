from test_import_api import client,auth,package,commit_previewed
from test_catalogue_quality import _insert_amendment
from contextlib import contextmanager
import math
import sqlite3

import pytest


def _amendment_error_fixture(api):
    with getattr(api.app, 'state').database.connect() as connection:
        connection.execute(
            """INSERT INTO sites (id,external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (1,'geo:error','Error site','shelter','unknown','map_reference','2026-01-01','2026-01-01')"""
        )
        connection.execute("UPDATE sites SET approach_latitude=63.5,approach_longitude=10.5 WHERE id=1")
        connection.executemany(
            """INSERT INTO field_observations
               (id,site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
                photo_urls_json,context_json,request_id,payload_hash,created_at)
               VALUES (?,1,?,'found','Synthetic private note',63.4,10.4,'entrance',25,'[]','{}',?,?,'2026-01-01')""",
            [(i, f'2026-01-0{6-i}', f'error:{i}', f'{i:064x}') for i in (1, 2, 3)],
        )
        _insert_amendment(
            connection, 1, 2,
            {'request_id': 'error-amended', 'expected_observation_revision': 1, 'reason': 'Synthetic private reason'},
            {'assessment': {'latitude': 63.7, 'longitude': 10.5, 'point_role': 'viewpoint'}, 'context': {}, 'status': None},
        )
        _insert_amendment(
            connection, 2, 2,
            {'request_id': 'error-withdrawn', 'expected_observation_revision': 1, 'reason': 'Synthetic private withdrawal', 'status': 'withdrawn'},
            {'assessment': {}, 'context': {}, 'status': 'withdrawn'},
        )


def _fail_amendment_batches(database, error_type, attempts, fail_at=None, statements=None):
    original_connect = database.connect

    @contextmanager
    def failing_connect():
        with original_connect() as connection:
            if statements is not None:
                connection.set_trace_callback(statements.append)

            class FailingConnection:
                def execute(self, sql, parameters=()):
                    if 'FROM observation_amendments' in sql and ' IN (' in sql:
                        attempts.append(sql)
                        if fail_at is None or len(attempts) - 1 == fail_at:
                            raise error_type('synthetic amendment read failure')
                    return connection.execute(sql, parameters)

                def __getattr__(self, name):
                    return getattr(connection, name)

            yield FailingConnection()

    return failing_connect


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
    amendment_reads = [sql for sql in statements if 'FROM observation_amendments' in sql]
    assert len(amendment_reads) == math.ceil(size / 500)
    assert all(len(sql.partition(' WHERE observation_id IN (')[2].partition(')')[0].split(',')) <= 500 for sql in amendment_reads)
    statements.clear()
    assert api.get('/api/sites.geojson').status_code == 401
    assert not any('FROM field_observations' in sql for sql in statements)


def test_geojson_amendment_reads_are_fresh_and_preserve_latest_withdrawn_and_corrupt_rows(tmp_path, monkeypatch):
    api = client(tmp_path)
    database = getattr(api.app, 'state').database
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO sites (id,external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (1,'geo:fresh','Fresh site','shelter','unknown','map_reference','2026-01-01','2026-01-01')"""
        )
        connection.executemany(
            """INSERT INTO field_observations
               (site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
                photo_urls_json,context_json,request_id,payload_hash,created_at)
               VALUES (1,?,'found','Private fixture note',63.4,10.4,'entrance',25,
                       '[]','{}',?,?,'2026-01-01')""",
            [(f'2026-01-0{i}', f'fresh:{i}', f'{i:064x}') for i in range(1, 4)],
        )
    statements = []
    original_connect = database.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, 'connect', traced_connect)
    first = api.get('/api/sites.geojson', headers=auth())
    assert first.status_code == 200
    first_features = first.json()['features']
    assert [feature['id'] for feature in first_features] == [1, 'observation-3', 'observation-2', 'observation-1']
    assert all(feature['properties']['uncertainty_m'] == 25 for feature in first_features[1:])

    for revision, uncertainty in ((1, 77), (2, 88)):
        response = api.post(
            '/api/sites/1/observations/1/amendments',
            headers=auth(),
            json={
                'request_id': f'fresh-amendment-{revision}',
                'expected_observation_revision': revision,
                'reason': 'Keep the effective export request-scoped.',
                'uncertainty_m': uncertainty,
            },
        )
        assert response.status_code == 201, response.text
    assert api.post(
        '/api/sites/1/observations/2/amendments',
        headers=auth(),
        json={
            'request_id': 'fresh-withdrawal',
            'expected_observation_revision': 1,
            'reason': 'Withdraw the second fixture row.',
            'status': 'withdrawn',
        },
    ).status_code == 201
    with original_connect() as connection:
        connection.execute(
            """INSERT INTO observation_amendments
               (observation_id,site_id,revision,request_id,payload_hash,request_json,changes_json,reason,created_at)
               VALUES (3,1,2,'corrupt-row',? ,?,'{}','Malformed fixture row','2026-01-02')""",
            ('0' * 64, b'\xff'),
        )

    statements.clear()
    second = api.get('/api/sites.geojson', headers=auth())
    assert second.status_code == 200
    assert [feature['id'] for feature in second.json()['features']] == [1, 'observation-1']
    assert second.json()['features'][1]['properties']['uncertainty_m'] == 88
    amendment_reads = [sql for sql in statements if 'FROM observation_amendments' in sql]
    assert len(amendment_reads) == 1
    assert all(len(sql.partition(' WHERE observation_id IN (')[2].partition(')')[0].split(',')) <= 500 for sql in amendment_reads)


def test_geojson_amendment_batch_failure_falls_back_to_per_row_reads(tmp_path, monkeypatch):
    api = client(tmp_path)
    _amendment_error_fixture(api)
    attempts = []
    database = getattr(api.app, 'state').database
    monkeypatch.setattr(
        database, 'connect',
        _fail_amendment_batches(database, sqlite3.OperationalError, attempts),
    )
    response = api.get('/api/sites.geojson', headers=auth())
    assert response.status_code == 200, response.text
    assert len(attempts) == 1
    features = response.json()['features']
    assert [feature['id'] for feature in features] == [1, 'approach-1', 'observation-1', 'observation-3']
    assert features[2]['geometry']['coordinates'] == [10.5, 63.7]
    assert features[2]['properties']['point_role'] == 'viewpoint'
    assert 'Synthetic private' not in response.text


def test_geojson_missing_amendment_history_keeps_sites_and_approaches(tmp_path):
    api = client(tmp_path)
    _amendment_error_fixture(api)
    with getattr(api.app, 'state').database.connect() as connection:
        connection.execute('DROP TABLE observation_amendments')
    response = api.get('/api/sites.geojson', headers=auth())
    assert response.status_code == 200, response.text
    features = response.json()['features']
    assert [feature['id'] for feature in features] == [1, 'approach-1']
    assert features[0]['properties']['feature_type'] == 'site'
    assert features[1]['properties']['point_role'] == 'approach'
    assert 'Synthetic private' not in response.text


def test_geojson_amendment_unrelated_failure_is_not_swallowed(tmp_path, monkeypatch):
    api = client(tmp_path)
    _amendment_error_fixture(api)
    attempts = []
    database = getattr(api.app, 'state').database
    monkeypatch.setattr(
        database, 'connect',
        _fail_amendment_batches(database, RuntimeError, attempts),
    )
    with pytest.raises(RuntimeError, match='synthetic amendment read failure'):
        api.get('/api/sites.geojson', headers=auth())
    assert len(attempts) == 1


def test_geojson_second_amendment_chunk_failure_keeps_earlier_chunk(tmp_path, monkeypatch):
    api = client(tmp_path)
    with getattr(api.app, 'state').database.connect() as connection:
        connection.execute(
            """INSERT INTO sites (id,external_key,name,site_kind,precision,location_basis,created_at,updated_at)
               VALUES (1,'geo:chunk','Chunk site','shelter','unknown','map_reference','2026-01-01','2026-01-01')"""
        )
        connection.executemany(
            """INSERT INTO field_observations
               (id,site_id,observed_at,outcome,note,latitude,longitude,point_role,uncertainty_m,
                photo_urls_json,context_json,request_id,payload_hash,created_at)
               VALUES (?,1,'2026-01-03','found','Synthetic multi private note',63.4,10.4,'entrance',25,'[]','{}',?,?,'2026-01-03')""",
            [(i, f'chunk:{i}', f'{i:064x}') for i in range(1, 502)],
        )
    attempts, statements = [], []
    database = getattr(api.app, 'state').database
    monkeypatch.setattr(
        database, 'connect',
        _fail_amendment_batches(database, sqlite3.OperationalError, attempts, fail_at=1, statements=statements),
    )
    response = api.get('/api/sites.geojson', headers=auth())
    assert response.status_code == 200, response.text
    assert len(attempts) == 2
    amendment_sql = [sql for sql in statements if 'observation_amendments' in sql]
    assert sum('FROM observation_amendments WHERE observation_id=' in sql for sql in statements) == 1, amendment_sql
    features = response.json()['features']
    assert len(features) == 502
    assert {feature['id'] for feature in features[1:]} == {f'observation-{i}' for i in range(1, 502)}
    assert 'Synthetic multi private note' not in response.text
