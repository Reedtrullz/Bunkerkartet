from concurrent.futures import ThreadPoolExecutor
import threading

from app.routes import RouteResult
from test_import_api import client,auth
from test_route_api import seed_site


def test_request_id_preserves_one_route_and_conflicts_with_changed_inputs(tmp_path,monkeypatch):
    api=client(tmp_path)
    object.__setattr__(api.app.state.settings,'ors_api_key','synthetic')
    calls=[]
    def route(key,coordinates):
        calls.append(coordinates)
        return RouteResult(100,90,coordinates,[0,1])
    monkeypatch.setattr('app.main.fetch_openrouteservice',route)
    site_id=seed_site(api)
    body={'request_id':'route-one','start':{'lat':63.4,'lon':10.4},'site_ids':[site_id]}
    first=api.post('/api/routes',headers=auth(),json=body)
    assert first.status_code==200,first.text
    second=api.post('/api/routes',headers=auth(),json=body)
    assert second.status_code==200 and second.json()['id']==first.json()['id']
    assert len(calls)==1
    changed=api.post('/api/routes',headers=auth(),json={**body,'name':'Changed'})
    assert changed.status_code==409
    assert changed.json()['detail']['code']=='IDEMPOTENCY_CONFLICT'
    assert len(api.get('/api/routes',headers=auth()).json())==1


def test_pending_route_reservation_does_not_hold_write_transaction(tmp_path,monkeypatch):
    api=client(tmp_path);object.__setattr__(api.app.state.settings,'ors_api_key','synthetic')
    site_id=seed_site(api);entered=threading.Event();release=threading.Event()
    def route(key,coordinates):
        entered.set();assert release.wait(5)
        return RouteResult(100,90,coordinates,[0,1])
    monkeypatch.setattr('app.main.fetch_openrouteservice',route)
    body={'request_id':'pending-one','start':{'lat':63.4,'lon':10.4},'site_ids':[site_id]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(api.post,'/api/routes',headers=auth(),json=body)
        try:
            assert entered.wait(2)
            duplicate=api.post('/api/routes',headers=auth(),json=body)
            assert duplicate.status_code==409 and duplicate.json()['detail']['code']=='ROUTE_PENDING'
            with api.app.state.database.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('UPDATE sites SET updated_at=updated_at WHERE id=?',(site_id,))
        finally: release.set()
        assert future.result().status_code==200


def test_failure_releases_reservation_and_allows_explicit_retry(tmp_path,monkeypatch):
    api=client(tmp_path);object.__setattr__(api.app.state.settings,'ors_api_key','synthetic')
    site_id=seed_site(api)
    def failed(key,coordinates):raise ValueError('synthetic provider failure')
    monkeypatch.setattr('app.main.fetch_openrouteservice',failed)
    body={'request_id':'failed-one','start':{'lat':63.4,'lon':10.4},'site_ids':[site_id]}
    assert api.post('/api/routes',headers=auth(),json=body).status_code==502
    monkeypatch.setattr('app.main.fetch_openrouteservice',lambda key,coordinates:RouteResult(100,90,coordinates,[0,1]))
    assert api.post('/api/routes',headers=auth(),json=body).status_code==200
