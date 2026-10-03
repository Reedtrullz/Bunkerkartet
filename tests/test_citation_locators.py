from copy import deepcopy
import pytest
from app.enrichment import load_research_site, research_site_payload, load_site_documents


def test_claim_specific_archive_locators_roundtrip_without_guessing():
    original=next(iter(load_site_documents().values())).model_dump(mode='json',exclude_none=True)
    first=original['claims'][0]
    first['citations']=[{'source_id':first['source_ids'][0],'page':'folio 12 verso','archive_reference':'Privat: A/17','quotation':'Exact original spelling','rights':'unknown'}]
    second=deepcopy(first);second['id']='different-page';second['citations'][0]['page']='map sheet IV';original['claims'].append(second)
    parsed=load_research_site(original)
    assert parsed.model_dump(mode='json',exclude_none=True)['claims'][0]['citations'][0]['page']=='folio 12 verso'
    payload=research_site_payload(parsed)
    assert payload['claims'][0]['citations']!=payload['claims'][-1]['citations']
    assert payload['claims'][0]['citations'][0]['rights']=='unknown'


def test_retired_identity_cannot_be_silently_reused():
    original=next(iter(load_site_documents().values())).model_dump(mode='json',exclude_none=True)
    original['retired_claim_ids']=[original['claims'][0]['id']]
    with pytest.raises(ValueError):load_research_site(original)
