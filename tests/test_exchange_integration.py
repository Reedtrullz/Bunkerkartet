from copy import deepcopy
from test_import_api import client,auth,package,commit_previewed
from test_content_editor import document


def test_selected_subset_retains_original_parent_hash_and_unresolved_links(tmp_path):
    api=client(tmp_path);source=package('source-package');second=deepcopy(source['records'][0]);second['external_key']='forum:2';source['records'].append(second)
    source['records'][0]['related_site_keys']=['forum:2']
    body={'source_package':source,'selected_keys':['forum:1'],'new_batch_id':'selected-batch','reason':'Reviewed explicit selected record'}
    preview=api.post('/api/admin/exchanges/import-subsets/preview',headers=auth(),json=body)
    assert preview.status_code==200,preview.text
    selection=preview.json()
    submitted={**body,**{key:selection[key] for key in ('package','selection_hash','preview_hash')}}
    committed=api.post('/api/admin/exchanges/import-subsets/commit',headers=auth(),json=submitted)
    assert committed.status_code==200,committed.text
    sites=api.get('/api/sites',headers=auth()).json();assert len(sites)==1
    detail=api.get('/api/sites/1',headers=auth()).json();assert detail['relations'][0]['related_status']=='not_imported'
    receipt=api.get('/api/admin/imports/selected-batch',headers=auth()).json()
    assert receipt['selection_provenance']==selection['manifest']


def test_gis_native_guard_is_atomic_stale_safe_and_unchanged_rows_do_not_write(tmp_path):
    api=client(tmp_path);assert commit_previewed(api,package()).status_code==200
    path='/api/admin/exchanges/gis/'
    pack=api.post(path+'edit-packs',headers=auth(),json={'site_ids':[1]}).json()
    body={'source_pack':pack,'edited_features':deepcopy(pack['features']),'reason':'Reviewed coordinate correction','axis_order_confirmation':'longitude_latitude'}
    unchanged=api.post(path+'edit-previews',headers=auth(),json=body).json()
    assert api.post(path+'edit-commits',headers=auth(),json={**body,'preview_hash':unchanged['preview_hash']}).json()['updated']==0
    assert api.get('/api/sites/1',headers=auth()).json()['revision']==1
    swapped=deepcopy(body);swapped['edited_features'][0]['geometry']['coordinates']=list(reversed(pack['features'][0]['geometry']['coordinates']))
    rejected=api.post(path+'edit-previews',headers=auth(),json=swapped)
    assert rejected.status_code==422 and 'swapped' in rejected.text
    assert api.get('/api/sites/1',headers=auth()).json()['revision']==1
    body['edited_features'][0]['geometry']['coordinates']=[10.41,63.41]
    effect=api.post(path+'edit-previews',headers=auth(),json=body).json()
    applied=api.post(path+'edit-commits',headers=auth(),json={**body,'preview_hash':effect['preview_hash']})
    assert applied.status_code==200,applied.text
    detail=api.get('/api/sites/1',headers=auth()).json()
    assert detail['revision']==2 and detail['location_review_required'] and detail['access']=='unknown' and detail['status']=='candidate'
    assert api.post(path+'edit-commits',headers=auth(),json={**body,'preview_hash':effect['preview_hash']}).status_code==409


def test_dossier_preview_matches_download_with_exact_citations_and_sensitive_exclusions(tmp_path):
    api=client(tmp_path);content=document();assert commit_previewed(api,package(external_key=content['external_key'])).status_code==200
    content['claims'][0]['citations']=[{'source_id':content['claims'][0]['source_ids'][0],'page':'folio 7 verso','archive_reference':'Private/17','rights':'unknown'}]
    assert api.patch('/api/sites/1/content',headers=auth(),json={'expected_revision':1,**{k:v for k,v in content.items() if k!='external_key'}}).status_code==200
    body={'site_ids':[1]};path='/api/admin/exchanges/dossiers/'
    preview=api.post(path+'preview',headers=auth(),json=body);assert preview.status_code==200,preview.text
    result=preview.json();download=api.post(path+'download',headers=auth(),json={**body,'preview_hash':result['preview_hash'],'generated_at':result['dossier']['generated_at']})
    assert download.status_code==200,download.text
    assert download.json()==result
    assert 'folio 7 verso' in result['html']
    assert result['dossier']['sites'][0]['claims'][0]['citations']==content['claims'][0]['citations']
    assert result['dossier']['private_start'] is None and result['dossier']['private_questions']==[] and result['dossier']['private_observations']==[]
