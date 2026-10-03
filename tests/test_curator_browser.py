from test_browser_contracts import page,base_url
from playwright.sync_api import expect


def unlock_and_open(page,base_url):
    page.goto(base_url)
    page.get_by_label('Administratortoken',exact=True).fill('audit-only')
    page.get_by_role('button',name='Last inn kart',exact=True).click()
    page.locator('#site-list .site-item',has_text='Junkers Ju 88 A – Jonsvatnet (markør 413)').get_by_role('button',name='Detaljer',exact=True).click()
    expect(page.locator('[data-detail-section="source-navigation"]')).to_have_count(1)


def test_private_history_and_source_views_survive_navigation_then_lock(page,base_url):
    unlock_and_open(page,base_url)
    sources=page.locator('[data-detail-section="source-navigation"]')
    sources.locator('summary').click()
    sources.get_by_role('button',name='https://example.com/browser-source',exact=True).click()
    expect(sources).to_contain_text('registry_ids')
    expect(sources).to_contain_text('legacy_registry')
    history=page.locator('[data-detail-section="history"]');history.locator('summary').click()
    history.get_by_role('button',name='Vis historikk',exact=True).click()
    expect(history).to_contain_text('importkvitteringen')
    page.get_by_role('button',name='Lås',exact=True).click()
    expect(page.locator('[data-detail-section="source-navigation"]')).to_have_count(0)
    assert 'browser-source' not in page.locator('body').inner_text()


def test_observation_withdrawal_retains_original_wording_and_is_idempotent(page,base_url):
    created=page.request.post(base_url+'/api/sites/1/observations',headers={'Authorization':'Bearer audit-only'},data={'request_id':'original-field','observed_at':'2026-10-03','outcome':'found','note':'Original field canary','point_role':'viewpoint','sought_target':'Visible bunker outline','visibility_limits':'Vegetation blocked the entrance','coverage_unknown':True})
    assert created.status==201
    unlock_and_open(page,base_url)
    section=page.locator('[data-detail-section="observation-amendments"]');section.locator('summary').click()
    expect(section).to_contain_text('Original field canary')
    expect(section).to_contain_text('Vegetation blocked the entrance')
    section.get_by_label('Rettelse som JSON (for eksempel outcome, note, status eller context)',exact=True).fill('{"status":"withdrawn","note":"Revised field note"}')
    section.get_by_label('Begrunnelse for rettelsen',exact=True).fill('Original assessment was mistaken')
    section.get_by_role('button',name='Lagre ny rettelse',exact=True).click()
    section=page.locator('[data-detail-section="observation-amendments"]')
    expect(section).to_contain_text('trukket tilbake')
    section.locator('summary').click()
    expect(section).to_contain_text('Original field canary')
    expect(section).to_contain_text('Revised field note')


def test_competing_hypotheses_have_reasoned_decisions_without_site_point_changes(page,base_url):
    headers={'Authorization':'Bearer audit-only'}
    site=page.request.get(base_url+'/api/sites/1',headers=headers).json()
    question=page.request.post(base_url+'/api/research/questions',headers=headers,data={'external_key':site['external_key'],'kind':'identity','wording':'Two distinct interpretations'}).json()
    revision=question['revision']
    for label in ('Alternative A','Alternative B'):
        result=page.request.post(base_url+f"/api/research/questions/{question['id']}/hypotheses",headers=headers,data={'expected_question_revision':revision,'label':label,'original_wording':'Exact synthetic archival wording','candidate_point':{'lat':63.3,'lon':10.3}})
        assert result.status==201
        revision=result.json()['question_revision']
    unlock_and_open(page,base_url)
    section=page.locator('[data-detail-section="research-questions"]');section.locator('summary').click()
    expect(section).to_contain_text('Hypotesepunkt: 63.3, 10.3')
    section.get_by_label('Vurder alternativ',exact=True).first.select_option('rejected')
    section.get_by_label('Begrunnelse for alternativet',exact=True).first.fill('Reviewed competing source explicitly')
    section.get_by_role('button',name='Registrer alternativvurdering',exact=True).first.click()
    expect(section).to_contain_text('Alternative A (rejected)')
    expect(section).to_contain_text('Alternative B (retained)')
    after=page.request.get(base_url+'/api/sites/1',headers=headers).json()
    assert (after['latitude'],after['longitude'],after['status'],after['revision']) == (site['latitude'],site['longitude'],site['status'],site['revision'])


def test_quality_drilldown_counts_remain_read_only(page,base_url):
    unlock_and_open(page,base_url)
    section=page.locator('[data-detail-section="quality-coverage"]');section.locator('summary').click()
    section.get_by_role('button',name='Vis katalogdekning',exact=True).click()
    expect(section).to_contain_text('total_catalogue')
    import json
    summary=json.loads(section.locator('pre').inner_text())
    section.get_by_label('Dimensjon',exact=True).select_option('identity')
    section.get_by_label('Dimensjonstilstand',exact=True).select_option('unknown')
    section.get_by_role('button',name='Vis stedene i valgt dimensjon',exact=True).click()
    expect(section).to_contain_text('"dimension": "identity"')
    details=json.loads(section.locator('pre').inner_text())
    assert details['drilldown']['membership_count']==summary['dimensions']['identity']['filtered']['unknown']
