from datetime import datetime,timedelta,timezone
from app.main import create_app
from app.config import Settings
from started_client import StartedClient
from test_import_api import auth,package,commit_previewed


def test_reader_is_scoped_revocable_and_fails_every_installed_mutation(tmp_path):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',pilot_readers_enabled=True)))
    assert commit_previewed(api,package()).status_code==200
    issued=api.post('/api/pilots/readers',headers=auth(),json={'scopes':['sites:read'],'ttl_seconds':60})
    assert issued.status_code==200,issued.text
    token=issued.json()['token'];headers={'Authorization':'Bearer '+token}
    assert api.get('/api/sites',headers=headers).status_code==200
    assert api.get('/api/sites/1',headers=headers).status_code==200
    assert api.get('/api/session',headers=headers).json()['role']=='reader'
    assert api.get('/api/sites/1/events',headers=headers).status_code==403
    assert api.get('/api/sites/1/history',headers=headers).status_code==403
    history_grant=api.post('/api/pilots/readers',headers=auth(),json={'scopes':['sites:read','history:read'],'ttl_seconds':60}).json()
    assert api.get('/api/sites/1/history',headers={'Authorization':'Bearer '+history_grant['token']}).status_code==200
    for route in api.app.routes:
        for method in getattr(route,'methods',set()) & {'POST','PATCH','PUT','DELETE'}:
            url=route.path
            if not url.startswith('/api/'):continue
            replacements={'site_id':'1','route_id':'1','observation_id':'1','visit_id':'1','id':'1','question_id':'1','image_id':'0'*32,'credential_id':'missing','pack_id':'missing','request_id':'missing','batch_id':'missing'}
            for key,value in replacements.items():url=url.replace('{'+key+'}',value)
            result=api.request(method,url,headers=headers,json={})
            assert result.status_code in (401,403),(method,url,result.status_code,result.text)
    assert api.delete('/api/pilots/readers/'+issued.json()['credential_id'],headers=auth()).status_code==200
    assert api.get('/api/sites',headers=headers).status_code==403
    assert api.get('/api/sites/1',headers=auth()).json()['revision']==1


def test_expired_reader_cannot_read_or_mutate(tmp_path):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',pilot_readers_enabled=True)))
    issued=api.post('/api/pilots/readers',headers=auth(),json={'scopes':['sites:read'],'ttl_seconds':60}).json()
    with api.app.state.database.connect() as c:c.execute("UPDATE pilot_reader_credentials SET expires_at='2000-01-01T00:00:00Z'")
    assert api.get('/api/sites',headers={'Authorization':'Bearer '+issued['token']}).status_code==403
