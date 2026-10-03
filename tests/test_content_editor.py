from copy import deepcopy
import json

from app.enrichment import ENRICHMENT_PATH
from test_import_api import client,auth,commit_previewed,package


def document():
    return deepcopy(json.loads(ENRICHMENT_PATH.read_text())['sites'][0])


def seed(api):
    payload=package(external_key=document()['external_key'])
    assert commit_previewed(api,payload).status_code==200
    return api.get('/api/sites/1',headers=auth()).json()


def test_content_override_survives_restart_and_preserves_sources_ids_states_and_site_facts(tmp_path):
    api=client(tmp_path);before=seed(api);content=document()
    content['display_name']='ÆØÅ edited';content['research_state']='identity_review'
    content['sources'].append({'id':'unused','title':'Unused reference','url':'https://example.com/unused'})
    response=api.patch('/api/sites/1/content',headers=auth(),json={'expected_revision':before['revision'],**{k:v for k,v in content.items() if k!='external_key'}})
    assert response.status_code==200,response.text
    after=response.json()
    assert after['content_document']==content
    assert after['revision']==before['revision']+1
    for key in ('name','status','access','latitude','longitude'):assert after[key]==before[key]
    restarted=client(tmp_path)
    assert restarted.get('/api/sites/1',headers=auth()).json()['content_document']==content
    stale=api.patch('/api/sites/1/content',headers=auth(),json={'expected_revision':before['revision'],**{k:v for k,v in content.items() if k!='external_key'}})
    assert stale.status_code==409
    assert stale.json()['detail']['code']=='REVISION_MISMATCH'


def test_corrupt_override_is_per_record_unavailable_and_cannot_route(tmp_path):
    api=client(tmp_path);seed(api)
    with api.app.state.database.connect() as connection:
        connection.execute("UPDATE sites SET content_json='{\"broken\":true}' WHERE id=1")
    detail=api.get('/api/sites/1',headers=auth()).json()
    assert detail['data_status']=='unavailable' and detail['enrichment'] is None
    assert detail['content_status']=='unavailable' and not detail['route_eligible']
    assert len(api.get('/api/sites',headers=auth()).json())==1


def test_search_uses_casefold_effective_content_and_each_evidence_reading(tmp_path):
    api=client(tmp_path);seed(api)
    second=package('second',external_key=document()['external_key'])
    second['records'][0]['sources'][0]['excerpt']='ÆØÅ unique later reading % underscore_'
    assert commit_previewed(api,second).status_code==200
    for term in ('æøå','%','underscore_'):
        result=api.get('/api/sites',headers=auth(),params={'q':term})
        assert len(result.json())==1
    assert api.get('/api/sites',headers=auth(),params={'q':'unmatched_'}).json()==[]
