import json
from copy import deepcopy

from app.config import Settings
from app.main import create_app
from started_client import StartedClient
from test_content_editor import document
from test_import_api import auth,package,commit_previewed


def test_selected_private_seed_is_isolated_between_app_instances(tmp_path):
    seed=document();seed['display_name']='Selected synthetic seed'
    chosen=tmp_path/'seed.json';chosen.write_text(json.dumps({'schema_version':1,'sites':[seed]}))
    a=StartedClient(create_app(Settings(data_dir=tmp_path/'a',admin_token='secret',enrichment_path=chosen)))
    b=StartedClient(create_app(Settings(data_dir=tmp_path/'b',admin_token='secret')))
    payload=package(external_key=seed['external_key'])
    assert commit_previewed(a,payload).status_code==200 and commit_previewed(b,payload).status_code==200
    assert a.get('/api/sites/1',headers=auth()).json()['enrichment']['display_name']=='Selected synthetic seed'
    assert b.get('/api/sites/1',headers=auth()).json()['enrichment']['display_name']!='Selected synthetic seed'


def test_ordinary_workspace_archive_restores_private_api_equivalence(tmp_path):
    from test_workspace_exchange import _make_archive
    from app.interchange import stage_workspace_archive
    archive, database, seed = _make_archive(tmp_path)
    destination = tmp_path / "equivalent"
    stage_workspace_archive(archive, destination)
    with StartedClient(create_app(Settings(data_dir=database.parent,admin_token='secret',enrichment_path=seed))) as original, StartedClient(create_app(Settings(data_dir=destination/'data',admin_token='secret',enrichment_path=destination/'app/content/site_enrichment.json'))) as restored:
        for endpoint in ('/api/sites','/api/sites/1','/api/sites/1/events','/api/routes','/api/routes/1'):
            before=original.get(endpoint,headers=auth());after=restored.get(endpoint,headers=auth())
            assert before.status_code == after.status_code == 200, (endpoint,before.text,after.text)
            assert before.json() == after.json(),endpoint
