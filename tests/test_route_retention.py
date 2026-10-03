from started_client import StartedClient
from app.main import create_app
from app.config import Settings
from app.routes import RouteResult
from test_route_api import seed_site


def test_exact_route_erasure_removes_private_content_retains_evidence_and_retry_tombstone(tmp_path,monkeypatch):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',ors_api_key='synthetic')))
    site_id=seed_site(api);auth={'Authorization':'Bearer secret'};calls=[]
    def provider(key,coordinates):
        calls.append(coordinates);return RouteResult(200,120,coordinates,[0,1])
    monkeypatch.setattr('app.main.fetch_openrouteservice',provider)
    inputs={'request_id':'erasure-original','name':'Private route canary','start':{'lat':63.4,'lon':10.4},'site_ids':[site_id]}
    route=api.post('/api/routes',headers=auth,json=inputs).json()
    body={'route_ids':[route['id']],'reason':'Synthetic owner selection'}
    assert api.post('/api/admin/retention/routes/preview',json=body).status_code==401
    preview=api.post('/api/admin/retention/routes/preview',headers=auth,json=body).json()
    before=api.get(f'/api/sites/{site_id}',headers=auth).json()
    assert preview['items'][0]['name']=='Private route canary'
    assert api.post('/api/admin/retention/routes/commit',headers=auth,json={**body,'preview_hash':'0'*64}).status_code==409
    commit={**body,'preview_hash':preview['preview_hash']}
    assert api.post('/api/admin/retention/routes/commit',headers=auth,json=commit).status_code==200
    assert api.post('/api/admin/retention/routes/commit',headers=auth,json=commit).json()['idempotent'] is True
    assert api.get('/api/routes',headers=auth).json()==[]
    assert api.get(f"/api/routes/{route['id']}",headers=auth).status_code==410
    assert api.post('/api/routes',headers=auth,json=inputs).status_code==410 and len(calls)==1
    assert api.get(f'/api/sites/{site_id}',headers=auth).json()==before
    with api.app.state.database.connect() as c:
        stored=dict(c.execute('SELECT * FROM route_plans WHERE id=?',(route['id'],)).fetchone())
        assert stored['start_json']=='{}' and stored['geometry_json']=='[]' and stored['gpx_text']==''
        assert stored['details_json'] is None
    receipts=api.get('/api/admin/retention/routes/receipts',headers=auth)
    assert 'Private route canary' not in receipts.text and '63.4' not in receipts.text
    assert receipts.json()['items'][0]['reason']==body['reason']
