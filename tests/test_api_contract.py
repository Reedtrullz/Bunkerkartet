from app.config import Settings
from app.main import create_app
from started_client import StartedClient
from test_import_api import client,auth,package,commit_previewed


def test_contract_exposes_bearer_and_required_revision_independent_of_build(tmp_path):
    app=create_app(Settings(data_dir=tmp_path/'unused',admin_token='credential-canary',app_version='build-one'))
    schema=app.openapi()
    assert schema['components']['securitySchemes']['BearerAuth']['scheme']=='bearer'
    assert schema['paths']['/api/sites']['get']['security']==[{'BearerAuth':[]}]
    assert schema['info']['version']=='1.0'
    required=schema['components']['schemas']['SitePatch']['required']
    assert 'expected_revision' in required
    assert 'credential-canary' not in str(schema)
    assert not app.state.database.path.exists()
    assert schema['paths']['/api/sites']['get']['responses']['200']['content']['application/json']['schema']['items']['$ref'].endswith('/SiteSummaryResponse')


def test_authenticated_client_preserves_unknown_and_conflict_semantics(tmp_path):
    api=client(tmp_path)
    payload=package();payload['records'][0].update(geometry=None,precision='unknown',uncertainty_m=None)
    assert commit_previewed(api,payload).status_code==200
    row=api.get('/api/sites/1',headers=auth()).json()
    assert row['latitude'] is None and row['longitude'] is None and row['access']=='unknown'
    assert api.patch('/api/sites/1',headers=auth(),json={'name':'Edited'}).status_code==422
    response=api.patch('/api/sites/1',headers=auth(),json={'expected_revision':row['revision'],'name':'Edited'})
    assert response.status_code==200
    stale=api.patch('/api/sites/1',headers=auth(),json={'expected_revision':row['revision'],'name':'Other'})
    assert stale.status_code==409 and stale.json()['detail']['code']=='REVISION_MISMATCH'
    assert api.get('/api/sites').status_code==401
