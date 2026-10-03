from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_exposes_operational_controls_without_duplicate_auth_binding():
    html = (ROOT / "app/static/index.html").read_text()
    javascript = (ROOT / "app/static/app.js").read_text()

    for element_id in (
        "admin-token",
        "site-list",
        "kind-filter",
        "access-filter",
        "confidence-filter",
        "route-name",
        "route-history-list",
        "download-geojson",
    ):
        assert f'id="{element_id}"' in html
    assert javascript.count('$("auth-form").addEventListener("submit"') == 1
    assert '"/api/routes"' in javascript
    assert "`/api/routes/${id}`" in javascript
    assert "site_ids" in javascript
    assert "hasReviewedPublicApproach" in javascript
    assert "timeout: 10000" in javascript
    assert "accuracy" in javascript
    assert 'id="map-provider"' in html
    assert "function configureMapChoices(config)" in javascript
    assert "function selectMapProvider(provider)" in javascript
    assert "state.config.enabled_map_providers" in javascript
    assert "Kartforespørsler er slått av" in javascript
    assert "L.control.layers" not in javascript
    assert html.index('/static/research-ui.js') < html.index('/static/workflow-ui.js')
    assert 'id="session-role-status"' in html
    assert "function hasSessionRole(role)" in javascript
    assert "function hasSessionScope(scope)" in javascript
    assert "function applyGeographyDefaults({ force = false } = {})" in javascript
    assert 'data-session-role="anonymous"' in html
    assert 'id="geography-status"' in html
    assert "enabled_map_providers: Array.isArray(config.enabled_map_providers)" in javascript
    assert "siteCache" in javascript
    assert "target_site_id" in javascript
    assert "Kopier koordinater" in javascript
    assert "L.circleMarker([point.lat, point.lon]" in javascript
    assert "Importnøkkel" in javascript
    assert "function locationCategory(siteKind)" in javascript
    assert "L.divIcon" in javascript
    assert "site-marker-${category.key}" in javascript
    assert "map-key-marker" in html
    assert '<html lang="nb">' in html
    assert 'id="surface-map"' in html
    assert 'id="surface-review"' in html
    assert 'id="surface-route"' in html


def test_sidebar_surfaces_location_and_selected_site_details():
    html = (ROOT / "app/static/index.html").read_text()
    javascript = (ROOT / "app/static/app.js").read_text()

    for element_id in ("map-tools-panel", "location-status", "detail-panel", "site-detail-heading"):
        assert f'id="{element_id}"' in html
    assert 'id="use-location"' in html
    assert 'id="pick-start"' in html
    assert 'scrollIntoView({ behavior: "smooth", block: "start" })' in javascript
    assert 'focus({ preventScroll: true })' in javascript
    assert 'text(root, "Laster stedsdetaljer ...")' in javascript
    assert 'classList.add("is-selected")' in javascript


def test_enrichment_contract_exposes_truthful_state_and_source_freshness():
    javascript = (ROOT / "app/static/app.js").read_text()

    for label in (
        "Kildeunderlag kuratert",
        "Research funnet – venter på kuratering",
        "Identitet må avklares",
        "Ikke kuratert i kartet",
        "Ingen kildebasert opplysning er kuratert for dette feltet.",
    ):
        assert label in javascript
    assert "research_state" in javascript
    assert "source.accessed_at" in javascript
    assert "Ikke beriket i kildeunderlaget." not in javascript
