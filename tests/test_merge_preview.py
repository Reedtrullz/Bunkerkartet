from test_import_api import client,auth,package,commit_previewed


def test_merge_preview_binds_both_snapshots_and_transfer_receipt(tmp_path):
    api=client(tmp_path)
    assert commit_previewed(api,package('source',external_key='source:one')).status_code==200
    assert commit_previewed(api,package('target',external_key='target:two')).status_code==200
    body={'expected_revision':1,'target_site_id':2,'target_expected_revision':1,'reason':'Reviewed duplicate identity'}
    preview=api.post('/api/sites/1/merge-preview',headers=auth(),json=body)
    assert preview.status_code==200,preview.text
    effect=preview.json()
    assert effect['source']['external_key']=='source:one' and effect['survivor']['external_key']=='target:two'
    assert effect['transfers']['evidence_ids']
    assert api.patch('/api/sites/2',headers=auth(),json={'expected_revision':1,'name':'Changed survivor'}).status_code==200
    stale=api.post('/api/sites/1/review',headers=auth(),json={'action':'merge',**body,'merge_preview_hash':effect['preview_hash']})
    assert stale.status_code==409
    assert api.get('/api/sites/1',headers=auth()).json()['merged_into_id'] is None
    body['target_expected_revision']=2
    effect=api.post('/api/sites/1/merge-preview',headers=auth(),json=body).json()
    committed=api.post('/api/sites/1/review',headers=auth(),json={'action':'merge',**body,'merge_preview_hash':effect['preview_hash']})
    assert committed.status_code==200,committed.text
    origin=api.get('/api/sites/1',headers=auth()).json()
    assert origin['merged_into_id']==2 and origin['external_key']=='source:one'
    assert committed.json()['merge_receipt']['transfers']==effect['transfers']
    receipt=api.get('/api/admin/imports/source',headers=auth()).json()['records'][0]
    assert receipt['original_site_id']==1 and receipt['merged_into_id']==2


def test_merge_preview_does_not_hide_request_id_collisions(tmp_path):
    api=client(tmp_path)
    for number in (1,2):assert commit_previewed(api,package(f'key{number}',external_key=f'key:{number}')).status_code==200
    observation={'request_id':'same-id','observed_at':'2026-10-03','outcome':'needs_follow_up','note':'Separate readings'}
    for id in (1,2):assert api.post(f'/api/sites/{id}/observations',headers=auth(),json=observation).status_code==201
    response=api.post('/api/sites/1/merge-preview',headers=auth(),json={'expected_revision':2,'target_site_id':2,'target_expected_revision':2,'reason':'Reviewed candidate'})
    assert response.status_code==200,response.text
    assert response.json()['can_commit'] is False and response.json()['conflicts']==['observation_request_id_collision']
