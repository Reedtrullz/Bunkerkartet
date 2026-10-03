from copy import deepcopy

from test_import_api import client,auth,package,commit_previewed
from app.imports import validate_import_package


def rich_package():
    body=package('rich');body['schema_version']='1.1'
    body['records'][0]['sources'][0].update(content_kind='quote',role='identity',claim_ids=['identity-one'],uncertainty_note='Source ambiguity retained',rights_status='unknown',rights_note='No reuse permission asserted')
    return body


def test_new_evidence_visible_in_preview_and_dated_reading(tmp_path):
    api=client(tmp_path);body=rich_package()
    preview=api.post('/api/admin/imports/preview',headers=auth(),json=body)
    assert preview.status_code==200,preview.text
    evidence=preview.json()['records'][0]['evidence'][0]
    assert evidence['content_kind']=='quote' and evidence['role']=='identity' and evidence['rights_status']=='unknown'
    assert commit_previewed(api,body).status_code==200
    reading=api.get('/api/sites/1',headers=auth()).json()['sources'][0]
    for key in ('content_kind','role','claim_ids','uncertainty_note','rights_status','rights_note'):
        assert reading[key]==body['records'][0]['sources'][0][key]


def test_legacy_dump_identity_is_unchanged_and_new_metadata_rejected_in_v10():
    old=package(); original=validate_import_package(old).model_dump(mode='json')
    assert 'role' not in original['records'][0]['sources'][0]
    new=rich_package();new['schema_version']='1.0'
    import pytest
    with pytest.raises(ValueError):validate_import_package(new)


def test_receipt_counts_links_and_readings_separately_and_has_stable_cursor(tmp_path):
    api=client(tmp_path)
    first=package('batch-a');second=package('batch-b')
    assert commit_previewed(api,first).status_code==200
    assert commit_previewed(api,second).status_code==200
    listed=api.get('/api/admin/imports?limit=1',headers=auth())
    assert listed.status_code==200,listed.text
    rows=listed.json();assert len(rows['items'])==1 and rows['next_cursor']
    next_page=api.get('/api/admin/imports',headers=auth(),params={'before':rows['next_cursor'],'limit':1}).json()
    assert rows['items'][0]['batch_id'] != next_page['items'][0]['batch_id']
    receipt=api.get('/api/admin/imports/batch-a',headers=auth()).json()
    assert receipt['payload_hash'] and len(receipt['records'])==1
    assert receipt['evidence_items']==1 and receipt['source_links']==1
    assert 'payload_json' not in str(receipt)
    assert api.get('/api/admin/imports/batch-a').status_code==401
