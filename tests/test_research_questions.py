from test_import_api import client,auth,package,commit_previewed


def test_questions_keep_independent_identity_and_require_explicit_resolution(tmp_path):
    api=client(tmp_path)
    body={'external_key':'not-imported:one','kind':'identity','wording':'Original wording retained','uncertainty':'No definite match','source_urls':['https://example.com/evidence']}
    first=api.post('/api/research/questions',headers=auth(),json=body)
    assert first.status_code==201,first.text
    second=api.post('/api/research/questions',headers=auth(),json={**body,'kind':'location'})
    assert second.status_code==201
    question=first.json()
    deferred=api.patch(f"/api/research/questions/{question['id']}",headers=auth(),json={'expected_revision':question['revision'],'state':'deferred','reason':'Await archive'})
    assert deferred.status_code==200
    assert deferred.json()['wording']==body['wording'] and deferred.json()['source_urls']==body['source_urls']
    missing=api.patch(f"/api/research/questions/{question['id']}",headers=auth(),json={'expected_revision':2,'state':'resolved','reason':' '})
    assert missing.status_code==422
    stale=api.patch(f"/api/research/questions/{question['id']}",headers=auth(),json={'expected_revision':1,'state':'resolved','reason':'Reviewed source'})
    assert stale.status_code==409
    history=api.get(f"/api/research/questions/{question['id']}/events",headers=auth()).json()
    assert len(history)==2
    assert api.get('/api/research/questions').status_code==401
    assert api.get('/api/sites',headers=auth()).json()==[]


def test_hypotheses_remain_alternatives_without_mutating_current_feature(tmp_path):
    api=client(tmp_path);assert commit_previewed(api,package()).status_code==200
    created=api.post('/api/research/questions',headers=auth(),json={'external_key':'forum:1','kind':'identity','wording':'Which structure?'})
    assert created.status_code==201,created.text
    question=created.json()
    before=api.get('/api/sites/1',headers=auth()).json()
    for title in ('Structure A','Structure B'):
        result=api.post(f"/api/research/questions/{question['id']}/hypotheses",headers=auth(),json={'expected_question_revision':question['revision'],'label':title,'original_wording':'Source says bunker','objections':'Identity unresolved','candidate_point':{'lat':63.41,'lon':10.42}})
        assert result.status_code==201,result.text
        question['revision']=result.json()['question_revision']
    detail=api.get(f"/api/research/questions/{question['id']}",headers=auth()).json()
    assert len(detail['hypotheses'])==2
    after=api.get('/api/sites/1',headers=auth()).json()
    for key in ('latitude','longitude','status','revision','access'):assert before[key]==after[key]
    export=api.get('/api/sites.geojson',headers=auth()).json()
    assert 'Structure A' not in str(export) and 'Which structure?' not in str(export)


def test_conflicting_claim_links_coexist_and_resolution_does_not_promote(tmp_path):
    from test_content_editor import document
    api=client(tmp_path);content=document()
    assert commit_previewed(api,package(external_key=content['external_key'])).status_code==200
    before=api.get('/api/sites/1',headers=auth()).json()
    body={'external_key':content['external_key'],'target_external_key':content['external_key'],'claim_id':content['claims'][0]['id'],'target_claim_id':content['claims'][1]['id'],'relation':'contradicts','reason':'Different dated context'}
    first=api.post('/api/research/assertions',headers=auth(),json=body)
    assert first.status_code==201,first.text
    derived=api.post('/api/research/assertions',headers=auth(),json={**body,'relation':'derived_from'})
    assert derived.status_code==201
    decision=api.patch(f"/api/research/assertions/{first.json()['id']}",headers=auth(),json={'expected_revision':1,'state':'resolved','reason':'Both retained under distinct dates'})
    assert decision.status_code==200
    reopened=api.patch(f"/api/research/assertions/{first.json()['id']}",headers=auth(),json={'expected_revision':2,'state':'reopened','reason':'Additional evidence'})
    assert reopened.status_code==200 and len(reopened.json()['decision_history'])==2
    after=api.get('/api/sites/1',headers=auth()).json()
    assert after['status']==before['status'] and after['revision']==before['revision']
    assert len(api.get('/api/research/assertions',headers=auth(),params={'external_key':content['external_key']}).json())==2
