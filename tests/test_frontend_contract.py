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
    assert "L.control.layers" in javascript
    assert "siteCache" in javascript
    assert "target_site_id" in javascript
    assert "Kopier koordinater" in javascript
    assert "startMarker = L.circleMarker" in javascript
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
