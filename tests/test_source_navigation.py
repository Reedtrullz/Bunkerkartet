from test_content_editor import document
from test_import_api import client,auth,package,commit_previewed


def test_sources_preserve_readings_unused_overlay_and_claim_namespaces(tmp_path):
    api=client(tmp_path);content=document()
    payload=package(external_key=content['external_key'])
    payload['records'][0]['sources'][0]['url']=content['sources'][0]['url']
    assert commit_previewed(api,payload).status_code==200
    second=package('later',external_key=content['external_key'])
    second['records'][0]['sources'][0]['url']=content['sources'][0]['url']
    second['records'][0]['sources'][0]['excerpt']='Later distinct reading'
    assert commit_previewed(api,second).status_code==200
    rows=api.get('/api/research/sources',headers=auth())
    assert rows.status_code==200,rows.text
    matching=[row for row in rows.json()['items'] if row['url']==content['sources'][0]['url']][0]
    assert matching['reading_count']==2
    assert matching['registry_ids'] and matching['overlay_references'][0]['source_id']==content['sources'][0]['id']
    detail=api.get('/api/research/source',headers=auth(),params={'url':matching['url']}).json()
    assert len(detail['readings'])==2 and detail['claim_references']
    review=api.post('/api/research/source-reviews',headers=auth(),json={'url':matching['url'],'expected_revision':detail['review_revision'],'reviewed_at':'2026-10-03','note':'Access details may have changed'})
    assert review.status_code==200,review.text
    assert review.json()['follow_up_suggestions']
    assert api.get('/api/sites/1',headers=auth()).json()['status']=='candidate'
    assert api.get('/api/research/source',params={'url':matching['url']}).status_code==401
