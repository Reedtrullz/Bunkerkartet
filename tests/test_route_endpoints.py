from started_client import StartedClient
from app.main import create_app
from app.config import Settings
from app.routes import RouteResult
from app.route_planning import ProviderLeg
from test_route_api import seed_site
import xml.etree.ElementTree as ET


def test_return_endpoint_receipt_and_budget_survive_saved_route_without_recalculation(tmp_path,monkeypatch):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',ors_api_key='synthetic')))
    id=seed_site(api);calls=[]
    def provider(key,coordinates):
        calls.append(coordinates)
        return RouteResult(200,120,coordinates,[0,1,2],(ProviderLeg(100,60),ProviderLeg(100,60)))
    monkeypatch.setattr('app.main.fetch_openrouteservice',provider)
    body={'request_id':'return-test','start':{'lat':63.4,'lon':10.4},'site_ids':[id],'mode':'return_to_start','visit_minutes':[20],'declared_budget_minutes':30}
    result=api.post('/api/routes',headers={'Authorization':'Bearer secret'},json=body)
    assert result.status_code==200,result.text
    route=result.json();assert calls[0][0]==calls[0][-1] and len(route['stops'])==1
    assert route['calculation_receipt']['profile']=='foot-hiking' and route['budget']['total_minutes']==22
    assert len(ET.fromstring(route['gpx']).findall('{http://www.topografix.com/GPX/1/1}wpt'))==3
    loaded=api.get(f"/api/routes/{route['id']}",headers={'Authorization':'Bearer secret'}).json()
    assert loaded['calculation_receipt']==route['calculation_receipt']
    assert loaded['mode']=='return_to_start' and len(calls)==1
    adjusted=api.post(f"/api/routes/{route['id']}/budget",headers={'Authorization':'Bearer secret'},json={'visit_minutes':[40],'declared_budget_minutes':30})
    assert adjusted.status_code==200 and adjusted.json()['budget']['over_budget'] is True and len(calls)==1


def test_profile_and_endpoint_defaults_require_explicit_valid_policy(tmp_path):
    api=StartedClient(create_app(Settings(data_dir=tmp_path,admin_token='secret',ors_api_key='synthetic')));id=seed_site(api)
    for extra in ({'mode':'explicit_end'},{'profile':'foot-walking'},{'mode':'one_way','end':{'lat':63,'lon':10}}):
        result=api.post('/api/routes',headers={'Authorization':'Bearer secret'},json={'start':{'lat':63.4,'lon':10.4},'site_ids':[id],**extra})
        assert result.status_code in (409,422)
