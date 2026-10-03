from test_import_api import client,auth,package,commit_previewed


def test_photo_pilot_is_private_disabled_and_creates_no_store(tmp_path):
    api=client(tmp_path);assert commit_previewed(api,package()).status_code==200
    body={'expected_revision':1,'mime':'image/png','bytes_base64':'ZmFrZQ==','reason':'Synthetic bytes'}
    assert api.post('/api/sites/1/images',json=body).status_code==401
    assert api.post('/api/sites/1/images',headers=auth(),json=body).status_code==409
    assert not (tmp_path/'attachments').exists()
    assert api.get('/api/images/123',headers=auth()).status_code==409
