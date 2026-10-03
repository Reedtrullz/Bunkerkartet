import json
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.enrichment import ResearchSite, load_site_enrichment


def test_claim_ids_are_unique_across_sections_and_nonblank():
    raw = json.loads(Path('app/content/site_enrichment.json').read_text())['sites'][0]
    ResearchSite.model_validate_json(json.dumps(raw))
    duplicate = deepcopy(raw)
    duplicate['claims'][1]['id'] = duplicate['claims'][0]['id']
    duplicate['claims'][1]['section'] = 'uncertainty'
    with pytest.raises(ValidationError):
        ResearchSite.model_validate_json(json.dumps(duplicate))
    blank = deepcopy(raw)
    blank['claims'][0]['id'] = '  '
    with pytest.raises(ValidationError):
        ResearchSite.model_validate_json(json.dumps(blank))


def test_claim_and_unused_source_identity_survive_projection():
    raw = json.loads(Path('app/content/site_enrichment.json').read_text())['sites'][0]
    projected = load_site_enrichment()[raw['external_key']]
    claims = projected['claims']
    assert [c['id'] for c in claims] == [c['id'] for c in raw['claims']]
    assert [s['id'] for s in projected['sources']] == [s['id'] for s in raw['sources']]
    assert claims[0]['source_ids'] == raw['claims'][0]['source_ids']
