from app.main import create_app
from app.config import Settings
from app.pilots import SYNTHETIC_GEOGRAPHIES
from started_client import StartedClient
from test_import_api import auth,package,commit_previewed


def test_two_descriptors_change_defaults_scope_receipt_without_coordinate_coercion(tmp_path):
    configs=[]
    for index,descriptor in enumerate(SYNTHETIC_GEOGRAPHIES):
        api=StartedClient(create_app(Settings(data_dir=tmp_path/str(index),admin_token='secret',pilot_geography_enabled=True,pilot_geography_descriptors=(descriptor,))))
        configs.append(api.get('/api/config').json()['geography'])
        payload=package();preview=api.post('/api/admin/imports/preview',headers=auth(),json=payload).json()
        assert preview['workspace_scope']['descriptor_id']==descriptor.descriptor_id
        assert any('geographic review' in warning for warning in preview['records'][0]['warnings'])
        assert commit_previewed(api,payload).status_code==200
        site=api.get('/api/sites/1',headers=auth()).json()
        assert (site['latitude'],site['longitude'])==(63.4,10.4) and site['status']=='candidate'
    assert configs[0]['map_defaults']!=configs[1]['map_defaults']
