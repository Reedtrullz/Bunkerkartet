from test_import_api import client,auth,package,commit_previewed
from datetime import date
from app.curator_history import freshness_state


def test_freshness_unknown_disabled_due_and_overdue_are_distinct():
    today=date(2026,10,3)
    assert freshness_state(None,30,today)=='unknown'
    assert freshness_state('2026-09-03',0,today)=='policy_disabled'
    assert freshness_state('2026-09-03',30,today)=='due'
    assert freshness_state('2026-09-02',30,today)=='overdue'
    assert freshness_state('2026-09-04',30,today)=='current'


def test_history_cursor_and_compensating_text_never_restore_protected_fields(tmp_path):
    api=client(tmp_path);assert commit_previewed(api,package()).status_code==200
    for revision,name in [(1,'Second name'),(2,'Third name')]:assert api.patch('/api/sites/1',headers=auth(),json={'expected_revision':revision,'name':name}).status_code==200
    first=api.get('/api/sites/1/history',headers=auth(),params={'limit':1}).json()
    second=api.get('/api/sites/1/history',headers=auth(),params={'limit':1,'before':first['next_cursor']}).json()
    assert first['items'][0]['id']>second['items'][0]['id']
    body={'expected_revision':3,'source_event_id':first['items'][0]['id'],'side':'before','fields':['name'],'reason':'Undo ordinary title only'}
    preview=api.post('/api/sites/1/history/compensation-preview',headers=auth(),json=body)
    assert preview.status_code==200,preview.text
    assert api.patch('/api/sites/1',headers=auth(),json={'expected_revision':3,'name':'New concurrent edit'}).status_code==200
    assert api.post('/api/sites/1/history/compensate',headers=auth(),json={**body,'preview_hash':preview.json()['preview_hash']}).status_code==409
    unsafe={**body,'expected_revision':4,'fields':['access']}
    assert api.post('/api/sites/1/history/compensation-preview',headers=auth(),json=unsafe).status_code==422


def test_access_suspension_blocks_routes_but_keeps_original_point(tmp_path):
    api=client(tmp_path);assert commit_previewed(api,package()).status_code==200
    result=api.post('/api/sites/1/freshness-review',headers=auth(),json={'expected_revision':1,'reviewed_at':'2026-10-03','reason':'Public access is uncertain','suspend_approach':True})
    assert result.status_code==200,result.text
    site=result.json()['site'];assert not site['route_eligible'] and site['latitude']==63.4


def test_owner_configured_freshness_policy_blocks_list_detail_and_new_routes_without_demotion(tmp_path):
    from started_client import StartedClient
    from app.main import create_app
    from app.config import Settings
    from test_route_api import seed_site
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',ors_api_key='synthetic',freshness_policy_days=30)))
    id=seed_site(api)
    with api.app.state.database.connect() as c:c.execute("UPDATE sites SET approach_reviewed_at='2020-01-01' WHERE id=?",(id,))
    detail=api.get(f'/api/sites/{id}',headers=auth()).json()
    assert detail['route_eligible'] is False and 'overdue' in detail['route_blocking_reason']
    listing=api.get('/api/sites',headers=auth()).json();assert listing[0]['route_eligible'] is False
    assert api.post('/api/routes',headers=auth(),json={'start':{'lat':63.4,'lon':10.4},'site_ids':[id]}).status_code==409
    assert api.get(f'/api/sites/{id}',headers=auth()).json()['status']==detail['status']
