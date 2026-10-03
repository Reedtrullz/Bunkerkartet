from app.config import Settings
from app.main import create_app
from started_client import StartedClient
from test_import_api import package,commit_previewed


def test_restart_rotation_rejects_old_bearer_and_reconciles_ambiguous_observation(tmp_path):
    old={'Authorization':'Bearer synthetic-old-token'};new={'Authorization':'Bearer synthetic-new-token'}
    body={'request_id':'ambiguous-original-id','observed_at':'2026-10-03','outcome':'not_found','note':'Synthetic unsent receipt canary'}
    with StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='synthetic-old-token'))) as before:
        assert commit_previewed(before,package(),token='synthetic-old-token').status_code==200
        accepted=before.post('/api/sites/1/observations',headers=old,json=body)
        assert accepted.status_code==201
        observation_id=accepted.json()['observation']['id']
    with StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='synthetic-new-token'))) as after:
        assert after.get('/api/sites',headers=old).status_code==401
        assert after.post('/api/sites/1/observations',headers=old,json=body).status_code==401
        retry=after.post('/api/sites/1/observations',headers=new,json=body)
        assert retry.status_code==200 and retry.json()['idempotent'] is True
        assert retry.json()['observation']['id']==observation_id
        assert len(after.get('/api/sites/1',headers=new).json()['field_observations'])==1
