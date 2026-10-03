from copy import deepcopy
import json
from app.main import create_app
from app.config import Settings
from started_client import StartedClient
from test_content_editor import document
from test_import_api import package,commit_previewed,auth


def test_reconciliation_preserves_old_base_across_seed_upgrade_and_conflicts(tmp_path):
    seed=document();chosen=tmp_path/'seed.json';chosen.write_text(json.dumps({'schema_version':1,'sites':[seed]}))
    settings=Settings(data_dir=tmp_path/'data',enrichment_path=chosen,admin_token='secret')
    api=StartedClient(create_app(settings));commit_previewed(api,package(external_key=seed['external_key']))
    local=deepcopy(seed);local['claims'][0]['text']='Curator override'
    response=api.patch('/api/sites/1/content',headers=auth(),json={'expected_revision':1,**{k:v for k,v in local.items() if k!='external_key'}})
    assert response.status_code==200
    newer=deepcopy(seed);newer['claims'][0]['text']='New seed wording'
    chosen.write_text(json.dumps({'schema_version':1,'sites':[newer]}));api.close()
    api=StartedClient(create_app(settings))
    # Another local edit must preserve the original seed base.
    local['display_name']='Local later title'
    assert api.patch('/api/sites/1/content',headers=auth(),json={'expected_revision':2,**{k:v for k,v in local.items() if k!='external_key'}}).status_code==200
    preview=api.get('/api/sites/1/seed-reconciliation',headers=auth()).json()
    item=next(item for item in preview['items'] if item['key']=='claims:'+seed['claims'][0]['id'])
    assert item['base']['value']['text']==seed['claims'][0]['text'] and item['conflict']
    bad=api.post('/api/sites/1/seed-reconciliation',headers=auth(),json={'expected_revision':3,'preview_hash':preview['preview_hash'],'reason':'Explicit review','choices':{}})
    assert bad.status_code==422 and api.get('/api/sites/1',headers=auth()).json()['revision']==3
    good=api.post('/api/sites/1/seed-reconciliation',headers=auth(),json={'expected_revision':3,'preview_hash':preview['preview_hash'],'reason':'Retain curator wording','choices':{i['key']:'local' for i in preview['items']}})
    assert good.status_code==200,good.text
