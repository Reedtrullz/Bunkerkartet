from copy import deepcopy
from app.seed_reconciliation import reconciliation_preview,resolve_reconciliation
from app.enrichment import load_site_documents
import pytest


def test_disjoint_claims_proposed_conflicts_explicit_and_missing_differs_empty():
    base=next(iter(load_site_documents().values())).model_dump(mode='json',exclude_none=True)
    current=deepcopy(base);local=deepcopy(base)
    current['claims'].append({'id':'new-seed','section':'about','text':'New evidence claim','certainty':'unknown','source_ids':[]})
    local['claims'][0]['text']='Local review wording'
    current['claims'][0]['text']='Changed seed wording'
    effect=reconciliation_preview(base,current,local,7)
    assert any(item['key']=='claims:new-seed' and not item['local']['present'] for item in effect['items'])
    assert next(item for item in effect['items'] if item['key']=='claims:'+base['claims'][0]['id'])['conflict']
    with pytest.raises(ValueError):resolve_reconciliation(effect,{})
    choices={item['key']:'local' for item in effect['items']};choices['claims:new-seed']='current'
    result=resolve_reconciliation(effect,choices)
    assert result['claims'][0]['text']=='Local review wording'
    assert any(claim['id']=='new-seed' for claim in result['claims'])


def test_removed_source_requires_valid_result_and_original_is_preserved():
    base=next(iter(load_site_documents().values())).model_dump(mode='json',exclude_none=True)
    current=deepcopy(base);current['sources']=current['sources'][1:]
    effect=reconciliation_preview(base,current,base,1)
    choices={item['key']:'current' for item in effect['items']}
    with pytest.raises(ValueError):resolve_reconciliation(effect,choices)
    assert len(base['sources'])>len(current['sources'])
