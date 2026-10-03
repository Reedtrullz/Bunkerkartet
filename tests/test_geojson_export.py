from test_import_api import client,auth,package,commit_previewed


def test_geojson_keeps_null_geometry_ids_axis_order_and_explicit_point_roles(tmp_path):
    api=client(tmp_path);body=package()
    body['records'][0]['geometry']['longitude']=180
    assert commit_previewed(api,body).status_code==200
    assert api.post('/api/sites/1/approach',headers=auth(),json={'expected_revision':1,'latitude':63.41,'longitude':10.41,'access':'public','note':'Synthetic public path'}).status_code==200
    for role in ('entrance','viewpoint'):
        response=api.post('/api/sites/1/observations',headers=auth(),json={'observed_at':'2026-10-03','outcome':'found','note':'Synthetic scoped evidence','latitude':63.42,'longitude':10.42,'point_role':role,'uncertainty_m':25})
        assert response.status_code==201,response.text
    features=api.get('/api/sites.geojson',headers=auth()).json()['features']
    assert features[0]['id']==1 and features[0]['geometry']['coordinates']==[-180.0,63.4]
    assert {feature['properties']['point_role'] for feature in features}=={'feature','approach','entrance','viewpoint'}
    approach=next(feature for feature in features if feature['properties']['point_role']=='approach')
    assert approach['properties']['uncertainty_m'] is None
    assert approach['geometry']['coordinates']==[10.41,63.41]
    assert all(feature['id'] is not None for feature in features)
    assert api.get('/api/sites/1',headers=auth()).json()['longitude']==180
    second=package('no-point',external_key='unknown:two')
    second['records'][0].update(geometry=None,precision='unknown',uncertainty_m=None)
    assert commit_previewed(api,second).status_code==200
    assert next(feature for feature in api.get('/api/sites.geojson',headers=auth()).json()['features'] if feature['id']==2)['geometry'] is None
