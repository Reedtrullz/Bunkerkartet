from app.main import create_app
from app.config import Settings
from started_client import StartedClient
from test_import_api import auth,package,commit_previewed


def prepare(tmp_path):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',pilot_offline_enabled=True)))
    assert commit_previewed(api,package()).status_code==200
    result=api.post('/api/pilots/offline/packs',headers=auth(),json={'site_ids':[1],'selected_fields':['external_key','revision','name'],'ttl_seconds':600})
    assert result.status_code==200,result.text
    pack=result.json()['pack_id'];path=f'/api/pilots/offline/packs/{pack}/outbox/device-original-request'
    queued=api.post(f'/api/pilots/offline/packs/{pack}/outbox',headers=auth(),json={'original_request_id':'device-original-request','site_key':package()['records'][0]['external_key'],'cached_site_revision':1,'payload':{'observed_at':'2026-10-03','outcome':'not_found','note':'Bounded offline note','coverage_unknown':True}})
    assert queued.status_code==200,queued.text
    return api,path


def test_explicit_approved_offline_sync_keeps_original_id_and_retries_once(tmp_path):
    api,path=prepare(tmp_path)
    assert api.post(path+'/sync',headers=auth(),json={'expected_site_revision':1}).status_code==409
    approved=api.post(path+'/review',headers=auth(),json={'decision':'approve','reviewer_ref':'Synthetic owner review'})
    assert approved.status_code==200,approved.text
    sent=api.post(path+'/sync',headers=auth(),json={'expected_site_revision':1})
    assert sent.status_code==200,sent.text
    replay=api.post(path+'/sync',headers=auth(),json={'expected_site_revision':1})
    assert replay.status_code==200 and replay.json()['idempotent'] is True
    assert sent.json()['observation']['request_id']=='device-original-request'
    assert replay.json()['observation']['id']==sent.json()['observation']['id']
    detail=api.get('/api/sites/1',headers=auth()).json()
    assert len(detail['field_observations'])==1 and detail['revision']==2 and detail['status']=='candidate'
    with api.app.state.database.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM site_events WHERE event_type='offline_synced'").fetchone()[0]==1


def test_site_change_after_manual_approval_blocks_sync_without_an_observation(tmp_path):
    api,path=prepare(tmp_path)
    assert api.post(path+'/review',headers=auth(),json={'decision':'approve','reviewer_ref':'Synthetic owner review'}).status_code==200
    assert api.patch('/api/sites/1',headers=auth(),json={'expected_revision':1,'short_rationale':'Changed current context'}).status_code==200
    result=api.post(path+'/sync',headers=auth(),json={'expected_site_revision':1})
    assert result.status_code==409
    assert api.get('/api/sites/1',headers=auth()).json()['field_observations']==[]
