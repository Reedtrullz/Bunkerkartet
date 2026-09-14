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
    assert "waypoint_names" in javascript
    assert "L.control.layers" in javascript
    assert "siteCache" in javascript
    assert "target_site_id" in javascript
    assert "Copy coordinates" in javascript
    assert "startMarker = L.circleMarker" in javascript
    assert "External key" in javascript
