from dataclasses import asdict
import io,json,base64
from PIL import Image
from pathlib import Path
from app.main import create_app
from app.config import Settings
from app.image_attachments import AttachmentPolicy,read_image
from app.media_exchange import export_media_workspace,stage_media_workspace
from app.interchange import create_workspace_archive,ExchangeError
from app.enrichment import ENRICHMENT_PATH
from test_import_api import package,commit_previewed,auth
from started_client import StartedClient
import pytest


def test_private_photo_bytes_and_references_restore_with_api_equivalence(tmp_path):
    policy=AttachmentPolicy(enabled=True,owner_policy_id='synthetic-test',rights_status='permission_recorded',rights_reference='synthetic-rights',retain_original=False,retention_days=7)
    policyfile=tmp_path/'policy.json';policyfile.write_text(json.dumps(asdict(policy)))
    settings=Settings(data_dir=tmp_path/'source',admin_token='secret',attachment_policy_path=policyfile)
    api=StartedClient(create_app(settings));assert commit_previewed(api,package()).status_code==200
    data=io.BytesIO();Image.new('RGB',(4,4),'blue').save(data,format='PNG')
    observation=api.post('/api/sites/1/observations',headers=auth(),json={'request_id':'image-linked-observation','observed_at':'2026-10-03','outcome':'found','note':'Synthetic linked observation'}).json()['observation']
    with api.app.state.database.connect() as c:revision=c.execute('SELECT revision FROM sites WHERE id=1').fetchone()[0]
    uploaded=api.post('/api/sites/1/images',headers=auth(),json={'expected_revision':revision,'observation_id':observation['id'],'mime':'image/png','bytes_base64':base64.b64encode(data.getvalue()).decode(),'reason':'Synthetic owned fixture'})
    assert uploaded.status_code==200,uploaded.text
    id=uploaded.json()['receipt']['attachment_id']
    events=api.get('/api/sites/1/events',headers=auth()).json()
    assert any(item['event_type']=='image_attached' and item['payload']['observation_id']==observation['id'] for item in events)
    assert api.get('/api/images/'+id).status_code==401
    original=api.get('/api/images/'+id,headers=auth()).content
    with pytest.raises(ExchangeError):create_workspace_archive(settings.db_path,ENRICHMENT_PATH,tmp_path/'incomplete.bkws',project_root=Path.cwd())
    archive=tmp_path/'full.bkmw';export_media_workspace(settings.db_path,ENRICHMENT_PATH,settings.data_dir/'attachments',archive,policy,project_root=Path.cwd())
    destination=tmp_path/'restored';stage_media_workspace(archive,destination,policy)
    restored=StartedClient(create_app(Settings(data_dir=destination/'data',enrichment_path=destination/'app/content/site_enrichment.json',admin_token='secret',attachment_policy_path=policyfile)))
    assert restored.get('/api/images/'+id,headers=auth()).content==original
    assert restored.get('/api/sites/1',headers=auth()).json()==api.get('/api/sites/1',headers=auth()).json()
