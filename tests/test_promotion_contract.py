from test_import_api import client,auth,package,commit_previewed


def setup(api):
    assert commit_previewed(api,package()).status_code==200
    return api.get('/api/sites/1',headers=auth()).json()


def test_promotions_require_reason_and_selected_owned_evidence(tmp_path):
    api=client(tmp_path);site=setup(api)
    for body in ({'action':'accept','expected_revision':1}, {'action':'accept','expected_revision':1,'reason':'Reviewed identity','evidence_ids':[9999]}):
        assert api.post('/api/sites/1/review',headers=auth(),json=body).status_code in (409,422)
    ids=[s['evidence_id'] for s in site['sources'] if s.get('evidence_id')]
    result=api.post('/api/sites/1/review',headers=auth(),json={'action':'accept','expected_revision':1,'reason':'Source supports identity, access remains unknown','evidence_ids':ids})
    assert result.status_code==200,result.text
    assert result.json()['site']['access']=='unknown'
    event=api.get('/api/sites/1/events',headers=auth()).json()[0]
    assert event['payload']['reason'] and event['payload']['evidence_ids']==ids


def test_merge_without_preview_is_rejected(tmp_path):
    api=client(tmp_path)
    for key in ('a','b'):assert commit_previewed(api,package(key,external_key=key)).status_code==200
    result=api.post('/api/sites/1/review',headers=auth(),json={'action':'merge','expected_revision':1,'target_site_id':2,'target_expected_revision':1,'reason':'Duplicate'})
    assert result.status_code==422
