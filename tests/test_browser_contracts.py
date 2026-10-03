from __future__ import annotations

import math
import re
import json
import time
from urllib.parse import urlsplit
from pathlib import Path

import pytest
from playwright.sync_api import expect
from playwright.sync_api import Page, sync_playwright

from test_browser_smoke import _start_browser_server, _stop_browser_server


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def base_url(tmp_path: Path) -> str:
    url, process = _start_browser_server(tmp_path / "browser-data")
    try:
        yield url
    finally:
        _stop_browser_server(process)


@pytest.fixture
def page(tmp_path: Path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        context.tracing.start(screenshots=True, snapshots=False, sources=False)
        page = context.new_page()
        page.set_default_timeout(7000)
        try:
            yield page
        except Exception:
            screenshot = tmp_path / "browser-contract-failure.png"
            trace = tmp_path / "browser-contract-failure.zip"
            page.screenshot(path=str(screenshot), full_page=False)
            context.tracing.stop(path=str(trace))
            if trace.stat().st_size > 4 * 1024 * 1024:
                trace.unlink()
            print(f"redacted synthetic browser artifacts: {screenshot}")
            if trace.exists():
                print(f"redacted synthetic browser trace: {trace}")
            raise
        finally:
            try:
                context.tracing.stop()
            except Exception:
                pass
            browser.close()


def _config(route, *, idle: int = 0, hidden: int = 0, enabled: list[str] | None = None, profiles: list[str] | None = None):
    import json

    route.fulfill(
        status=200,
        content_type="application/json",
        body=(
            '{"idle_lock_seconds":'
            + str(idle)
            + ',"hidden_lock_seconds":'
            + str(hidden)
            + ',"available_map_providers":["kartverket","esri"]'
            + ',"enabled_map_providers":'
            + json.dumps(enabled or [])
            + ',"route_profiles":'
            + json.dumps(profiles if profiles is not None else ["foot-hiking"])
            + '}'
        ),
    )


def test_html_loads_only_local_frontend_resources():
    html = (ROOT / "app/static/index.html").read_text()
    external = re.findall(r"(?:src|href)=[\"']https?://[^\"']+", html)
    assert external == []
    assert "/static/vendor/leaflet/leaflet.css" in html
    assert "/static/vendor/leaflet/leaflet.js" in html


def test_duplicate_json_keys_in_original_file_are_rejected(page: Page, base_url: str):
    page.goto(base_url)
    for payload in (b'{"schema_version":"1.0","schema_version":"1.0"}', b'{"records":[{"name":"a","name":"b"}]}'):
        page.locator("#import-file").set_input_files(
            {"name": "ambiguous.json", "mimeType": "application/json", "buffer": payload}
        )
        page.get_by_role("button", name="Forhåndsvis", exact=True).wait_for()
        assert page.get_by_role("button", name="Forhåndsvis", exact=True).is_disabled()
        expect(page.locator("#import-result")).to_contain_text("duplisert")
    assert "schema_version" not in page.locator("#import-result").inner_text()


def test_map_has_no_third_party_egress_until_user_opts_in(page: Page, base_url: str):
    page.route("**/api/config", lambda route: _config(route, enabled=["kartverket", "esri"]))
    external: list[str] = []
    page.on("request", lambda request: external.append(request.url) if request.url.startswith("https://") else None)
    page.route("https://**/*", lambda route: route.abort())
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#map-provider")
    assert external == []

    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    assert external == []

    expect(page.locator("#map-provider").locator('option[value="kartverket"]')).to_have_count(1)
    assert page.locator("#map-provider").is_enabled()
    with page.expect_request("https://cache.kartverket.no/**"):
        page.locator("#map-provider").select_option("kartverket")
    assert any("kartverket" in url or "kartverket.no" in url for url in external)

    page.get_by_role("button", name="Lås", exact=True).click()
    expect(page.locator("#map-provider")).to_have_value("")
    expect(page.locator("#map-provider")).to_be_disabled()
    assert page.evaluate("state.activeMapProvider") == ""


def test_reader_session_hides_mutations_and_keeps_workspace_after_forbidden_write(page: Page, base_url: str):
    page.add_init_script("window.__detailEvents = 0; document.addEventListener('bunkerkartet:detail', () => window.__detailEvents++);")
    page.route("**/api/session", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "role": "reader", "scopes": ["sites:read"], "expires_at": "2026-10-03T18:00:00Z",
    })))
    forbidden_writes = []

    def reject_reader_write(route):
        forbidden_writes.append(route.request.method)
        route.fulfill(status=403, content_type="application/json", body=json.dumps({"detail": "reader credentials cannot mutate"}))

    page.route("**/api/sites/1/content", reject_reader_write)
    route_reads = []

    def reveal_private_route_canary(route):
        route_reads.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps([
            {"id": 99, "name": "PRIVATE ROUTE CANARY", "data_status": "valid", "start": {"lat": 60.0, "lon": 7.0}, "geometry": {"type": "LineString", "coordinates": [[7.0, 60.0], [7.1, 60.1]]}},
        ]))

    page.route("**/api/routes**", reveal_private_route_canary)
    event_reads = []
    page.on("request", lambda request: event_reads.append(request.url) if request.url.endswith("/events?limit=50") else None)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    expect(page.locator("#session-role-status")).to_contain_text("Lesetilgang")
    expect(page.locator("#session-role-status")).to_contain_text("sites:read")
    expect(page.locator("#session-role-status")).to_contain_text("utløper")
    assert page.locator("body").get_attribute("data-session-role") == "reader"
    assert page.evaluate("state.session.role === 'reader' && hasSessionRole('reader') && hasSessionScope('sites:read')")
    assert not event_reads  # This reader only has sites:read, not history:read.
    assert not page.locator(".import-panel").is_visible()
    assert not page.locator("#route-draft-controls").is_visible()
    assert not page.locator("#surface-route").is_visible()
    assert not page.locator(".saved-routes-panel").is_visible()
    page.evaluate("setSurface('route', {scroll:false})")
    assert page.evaluate("state.surface") == "map"
    assert not route_reads
    assert "PRIVATE ROUTE CANARY" not in page.locator("body").inner_text()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    page.get_by_role("heading", name="Stedsdetaljer: Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).wait_for()
    assert page.locator("#site-detail form").count() > 0
    assert not page.locator("#site-detail form").first.is_visible()
    assert not page.locator("details.content-editor").is_visible()
    assert page.locator('[data-detail-section="research-questions"]').count() == 0
    assert page.locator('[data-detail-section="source-navigation"]').count() == 0
    assert page.evaluate("window.__detailEvents") == 0
    with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/api/sites/1/content")) as denied:
        page.locator("details.content-editor form").dispatch_event("submit")
    assert denied.value.status == 403
    assert forbidden_writes == ["PATCH"]
    assert page.locator("#admin-token").input_value() == "audit-only"
    assert page.locator("body").get_attribute("data-session-role") == "reader"
    assert page.evaluate("state.sites.some(site => site.id === 1) && state.siteCache.has(1)")
    assert not route_reads


def test_reader_role_transition_clears_previously_mounted_owner_research_widgets(page: Page, base_url: str):
    page.add_init_script("""
      document.addEventListener('bunkerkartet:detail', event => {
        const widget = document.createElement('section');
        widget.className = 'owner-research-canary';
        widget.textContent = 'OWNER RESEARCH CANARY';
        event.detail.root.append(widget);
      });
    """)
    calls = 0

    def change_role(route):
        nonlocal calls
        calls += 1
        role = "owner" if calls == 1 else "reader"
        scopes = ["owner"] if role == "owner" else ["sites:read"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"role": role, "scopes": scopes, "expires_at": None}))

    page.route("**/api/session", change_role)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    page.locator(".owner-research-canary").wait_for()

    page.evaluate("loadSites()")
    expect(page.locator("body")).to_have_attribute("data-session-role", "reader")
    expect(page.locator(".owner-research-canary")).to_have_count(0)
    assert "OWNER RESEARCH CANARY" not in page.locator("body").inner_text()
    assert page.locator("#admin-token").input_value() == "audit-only"


def test_geography_defaults_drive_map_start_and_route_name(page: Page, base_url: str):
    geography = {
        "descriptor_id": "synthetic-map-alpha", "display_name": "Synthetic scope Alpha",
        "key_namespace": "bk-synthetic-alpha", "bounding_box": [5.0, 58.0, 10.0, 62.0],
        "map_defaults": {"center": [60.123, 7.456], "zoom": 8},
        "approved_source_namespaces": ["synthetic-alpha:"], "warnings": ["Synthetic geography warning."],
        "synthetic": True,
    }
    config = {"idle_lock_seconds": 0, "hidden_lock_seconds": 0, "available_map_providers": ["kartverket", "esri"],
        "enabled_map_providers": ["kartverket"], "route_profiles": ["foot-hiking"], "geography": geography}
    page.route("**/api/config", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(config)))
    tile_urls = []

    def fulfill_tile(route):
        tile_urls.append(route.request.url)
        route.fulfill(status=200, content_type="image/png", body=bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"))

    page.route("https://cache.kartverket.no/**", fulfill_tile)
    page.goto(base_url)
    expect(page.locator("#geography-status")).to_contain_text("Synthetic scope Alpha")
    expect(page.locator("#geography-status")).to_contain_text("Synthetic geography warning.")
    expect(page.locator("#route-name")).to_have_value("Synthetic scope Alpha field route")
    expect(page.locator("#route-start")).to_contain_text("Start: Standardstart i Synthetic scope Alpha")
    assert page.locator("#map").get_attribute("aria-label") == "Kart over Synthetic scope Alpha"
    assert page.locator("#map-provider").input_value() == ""
    assert tile_urls == []

    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    zoom = 8
    n = 2 ** zoom
    x = math.floor((7.456 + 180) / 360 * n)
    latitude_radians = math.radians(60.123)
    y = math.floor((1 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2 * n)
    with page.expect_request("https://cache.kartverket.no/**") as tile_request:
        page.locator("#map-provider").select_option("kartverket")
    parts = urlsplit(tile_request.value.url).path.split("/")[-3:]
    tile_zoom, tile_y, tile_x = int(parts[0]), int(parts[1]), int(parts[2].removesuffix(".png"))
    assert tile_zoom == zoom
    assert abs(tile_x - x) <= 1 and abs(tile_y - y) <= 1


def test_geography_defaults_preserve_manual_route_draft_then_return_on_lock(page: Page, base_url: str):
    geography = {
        "descriptor_id": "synthetic-map-beta", "display_name": "Synthetic scope Beta",
        "key_namespace": "bk-synthetic-beta", "bounding_box": [5.0, 58.0, 10.0, 62.0],
        "map_defaults": {"center": [60.5, 7.75], "zoom": 9},
        "approved_source_namespaces": ["synthetic-beta:"], "warnings": [], "synthetic": True,
    }
    config = {"idle_lock_seconds": 0, "hidden_lock_seconds": 0, "available_map_providers": ["kartverket", "esri"],
        "enabled_map_providers": [], "route_profiles": ["foot-hiking"], "geography": geography}

    def delayed_config(route):
        time.sleep(1.5)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(config))

    page.route("**/api/config", delayed_config)
    page.goto(base_url, wait_until="domcontentloaded")
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.get_by_role("button", name="Velg start på kartet", exact=True).click()
    page.locator("#map").click(position={"x": 240, "y": 130})
    manual_start = page.locator("#route-start").inner_text()
    assert "Start: Standardstart" not in manual_start
    page.get_by_role("button", name="Tur", exact=True).click()
    page.get_by_label("Rutenavn", exact=True).fill("Manual synthetic route draft")
    expect(page.locator("#geography-status")).to_contain_text("Synthetic scope Beta")
    expect(page.locator("#route-name")).to_have_value("Manual synthetic route draft")
    expect(page.locator("#route-start")).to_have_text(manual_start)

    page.get_by_role("button", name="Lås", exact=True).click()
    expect(page.locator("#admin-token")).to_have_value("")
    expect(page.locator("#route-name")).to_have_value("Synthetic scope Beta field route")
    expect(page.locator("#route-start")).to_contain_text("Start: Standardstart i Synthetic scope Beta")
    assert page.evaluate("state.drafts.size") == 0


def test_private_list_survives_a_missing_local_leaflet_script(page: Page, base_url: str):
    page.route("**/static/vendor/leaflet/leaflet.js", lambda route: route.abort())
    page.route("https://**/*", lambda route: route.abort())
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    assert "Kartet er ikke tilgjengelig" in page.locator("#map-provider-status").inner_text()


def test_configured_idle_lock_uses_the_private_workspace_reset(page: Page, base_url: str):
    page.route("**/api/config", lambda route: _config(route, idle=1))
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    expect(page.locator("#admin-token")).to_have_value("", timeout=5000)
    assert "Junkers Ju 88 A – Jonsvatnet (markør 413)" not in page.locator("body").inner_text()


def test_default_lock_intervals_are_disabled(page: Page, base_url: str):
    response = page.request.get(f"{base_url}/api/config")
    assert response.status == 200
    assert response.json()["idle_lock_seconds"] == 0
    assert response.json()["hidden_lock_seconds"] == 0
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.wait_for_timeout(1200)
    expect(page.locator("#admin-token")).to_have_value("audit-only")
    expect(page.locator("#site-list")).to_contain_text("Junkers Ju 88 A – Jonsvatnet (markør 413)")


def test_configured_hidden_lock_uses_the_private_workspace_reset(page: Page, base_url: str):
    page.route("**/api/config", lambda route: _config(route, hidden=1))
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.evaluate("Object.defineProperty(document, 'visibilityState', {configurable:true, value:'hidden'}); document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("#admin-token")).to_have_value("", timeout=5000)
    assert "Junkers Ju 88 A – Jonsvatnet (markør 413)" not in page.locator("body").inner_text()


def test_accessible_private_navigation_and_mobile_list_fit(page: Page, base_url: str):
    page.set_viewport_size({"width": 320, "height": 700})
    page.goto(base_url)
    page.locator("#status-filter").select_option("rejected")
    assert page.locator("#category-filter").count() == 1
    assert page.get_by_role("button", name="Nullstill filtre", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.locator("#map").evaluate("element => element.getBoundingClientRect().height < 300")


def test_content_editor_keeps_stale_draft_then_roundtrips_raw_document(page: Page, base_url: str):
    original = {
        "external_key": "krigskart:413",
        "display_name": "Synthetic content title",
        "kind_label": "Bunker",
        "research_state": "identity_review",
        "reviewed_at": "2026-09-20",
        "retired_claim_ids": ["claim-retired-before"],
        "sources": [
            {"id": "source-used", "title": "Used test source", "url": "https://example.com/used", "accessed_at": "2026-09-19"},
            {"id": "source-unused", "title": "Unused but retained", "url": "https://example.com/unused"},
        ],
        "claims": [
            {"id": "claim-kept", "section": "visit_summary", "text": "A short synthetic visit note.", "certainty": "source_supported", "source_ids": ["source-used"]},
            {"id": "claim-unlinked", "section": "about", "text": "A retained claim without a source.", "certainty": "unknown", "source_ids": []},
            {"id": "claim-cited", "section": "about", "text": "A cited claim keeps its stable ID.", "certainty": "source_supported", "source_ids": ["source-used"], "citations": [{"source_id": "source-used", "archive_reference": "Synthetic Archive Box 4", "page": "12", "figure": "Fig. 3", "map_sheet": "Trondheim-7C", "quotation": "Exact synthetic quotation.", "rights": "Synthetic rights note"}]},
            {"id": "claim-retire-me", "section": "uncertainty", "text": "A claim explicitly retired by the editor.", "certainty": "unknown", "source_ids": []},
        ],
    }
    page.route(
        "**/api/sites/1",
        lambda route: _fulfill_content_document(route, original),
    )
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    editor = page.locator("details.content-editor")
    editor.locator("summary").click()
    display_name = editor.locator('[name="display_name"]')
    expect(display_name).to_have_value("Synthetic content title")
    expect(editor.locator('[name="research_state"]')).to_have_value("identity_review")
    display_name.fill("Edited synthetic content title")
    editor.locator('[data-claim-id="claim-kept"] [name="claim-text"]').fill("Edited visit summary")
    editor.locator('[data-claim-id="claim-cited"] [name="claim-section"]').select_option("current")
    editor.locator('[data-claim-id="claim-retire-me"]').get_by_role("button", name="Fjern påstand", exact=True).click()
    expect(editor).to_contain_text("claim-retire-me")

    calls = {"patch": 0}

    def reject_stale_patch(route):
        calls["patch"] += 1
        route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": {"code": "REVISION_MISMATCH", "message": "The current content changed."}}))

    page.route("**/api/sites/1/content", reject_stale_patch)
    editor.get_by_role("button", name="Lagre innhold", exact=True).click()
    expect(display_name).to_have_value("Edited synthetic content title")
    expect(editor.locator(".draft-conflict")).to_contain_text("Utkastet er bevart")
    assert calls["patch"] == 1

    page.unroute("**/api/sites/1/content", reject_stale_patch)
    saved_payloads = []

    def accept_editor_payload(route):
        saved_payloads.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/api/sites/1/content", accept_editor_payload)
    with page.expect_response(lambda response: response.request.method == "PATCH" and response.url.endswith("/api/sites/1/content")) as saved_response, page.expect_response(lambda response: response.request.method == "GET" and response.url.endswith("/api/sites/1")):
        editor.get_by_role("button", name="Lagre innhold", exact=True).click()
    assert saved_response.value.status == 200, saved_response.value.text()
    assert len(saved_payloads) == 1
    saved = saved_payloads[0]
    assert "external_key" not in saved  # The API binds identity from the stored site.
    assert saved["display_name"] == "Edited synthetic content title"
    assert [source["id"] for source in saved["sources"]] == ["source-used", "source-unused"]
    assert saved["retired_claim_ids"] == ["claim-retired-before", "claim-retire-me"]
    claims = {claim["id"]: claim for claim in saved["claims"]}
    assert claims["claim-kept"]["text"] == "Edited visit summary"
    assert claims["claim-kept"]["section"] == "visit_summary"
    assert len(claims["claim-kept"]["text"]) <= 400
    assert claims["claim-unlinked"]["source_ids"] == []
    assert claims["claim-cited"]["id"] == "claim-cited"
    assert claims["claim-cited"]["section"] == "current"
    assert claims["claim-cited"]["citations"] == original["claims"][2]["citations"]
    assert "claim-retire-me" not in claims


def test_observation_location_is_one_shot_fresh_and_draft_generation_bound(page: Page, base_url: str):
    page.add_init_script("""
      window.__geoCalls = 0; window.__geoWatchCalls = 0; window.__geoSuccess = null; window.__geoError = null; window.__geoOptions = null;
      Object.defineProperty(navigator, 'geolocation', {configurable:true, value:{
        getCurrentPosition(success, error, options) { window.__geoCalls++; window.__geoSuccess = success; window.__geoError = error; window.__geoOptions = options; },
        watchPosition() { window.__geoWatchCalls++; }
      }});
    """)
    posted: list[dict[str, object]] = []

    def capture_observation(route):
        posted.append(route.request.post_data_json)
        route.continue_()

    page.route("**/api/sites/1/observations", capture_observation)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    form = page.locator("details.observations-editor form.observation-form")
    page.locator("details.observations-editor summary").click()
    latitude = form.locator('[name="latitude"]')
    longitude = form.locator('[name="longitude"]')
    note = form.get_by_label("Observasjonsnotat", exact=True)
    latitude.fill("63.123")
    longitude.fill("10.456")
    note.fill("Manual coordinates remain until a fresh one-shot position is accepted.")
    assert page.evaluate("window.__geoCalls") == 0
    request_id = page.evaluate("state.drafts.get('1:observation').requestId")

    locate = form.get_by_role("button", name="Hent posisjon én gang fra enheten", exact=True)
    locate.click()
    assert page.evaluate("window.__geoCalls") == 1
    assert page.evaluate("window.__geoOptions.maximumAge") == 0
    page.evaluate("window.__geoSuccess({timestamp:Date.now()-600000,coords:{latitude:63.5,longitude:10.5,accuracy:8}})")
    expect(latitude).to_have_value("63.123")
    expect(longitude).to_have_value("10.456")
    expect(form.locator('[name="captured_at"]')).to_have_value("")
    expect(form.locator('p[aria-live="polite"]')).to_contain_text("fersk metadata")

    locate.click()
    page.evaluate("window.__geoError({code:1})")
    expect(latitude).to_have_value("63.123")
    expect(longitude).to_have_value("10.456")
    expect(form.locator('p[aria-live="polite"]')).to_contain_text("avslått")

    locate.click()
    note.fill("A newer manual edit wins over the pending location callback.")
    page.evaluate("window.__geoSuccess({timestamp:Date.now(),coords:{latitude:63.7,longitude:10.7,accuracy:12}})")
    expect(latitude).to_have_value("63.123")
    expect(longitude).to_have_value("10.456")
    expect(form.locator('p[aria-live="polite"]')).to_contain_text("utkastet ble endret")

    locate.click()
    page.evaluate("window.__geoSuccess({timestamp:Date.now(),coords:{latitude:63.71,longitude:10.71,accuracy:12.4}})")
    expect(latitude).to_have_value("63.71")
    expect(longitude).to_have_value("10.71")
    expect(form.locator('[name="reported_accuracy_m"]')).to_have_value("12.4")
    expect(form.locator('[name="uncertainty_m"]')).to_have_value("")
    assert page.evaluate("window.__geoCalls") == 4
    assert page.evaluate("window.__geoWatchCalls") == 0
    assert posted == []
    assert page.evaluate("state.drafts.get('1:observation').requestId") == request_id

    with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/sites/1/observations")) as saved:
        form.get_by_role("button", name="Lagre observasjon", exact=True).click()
    assert saved.value.status == 201, saved.value.text()
    assert len(posted) == 1
    assert posted[0]["request_id"] == request_id
    assert posted[0]["latitude"] == 63.71 and posted[0]["longitude"] == 10.71
    assert posted[0]["reported_accuracy_m"] == 12.4
    assert posted[0]["captured_at"].endswith("Z") or posted[0]["captured_at"].endswith("+00:00")
    assert "uncertainty_m" not in posted[0]
    assert "context" not in posted[0]


def test_current_detail_dispatches_classic_research_integration_event(page: Page, base_url: str):
    page.add_init_script("""
      window.__detailEvents = [];
      document.addEventListener('bunkerkartet:detail', event => {
        window.__detailEvents.push({siteId:event.detail.site.id, rootId:event.detail.root.id});
      });
    """)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    page.locator("#site-detail .content-editor").wait_for()
    assert page.evaluate("window.__detailEvents") == [{"siteId": 1, "rootId": "site-detail"}]


def test_merge_requires_a_fresh_preview_and_explicit_commit(page: Page, base_url: str):
    preview_bodies: list[dict[str, object]] = []
    review_bodies: list[dict[str, object]] = []

    def capture_preview(route):
        preview_bodies.append(route.request.post_data_json)
        route.continue_()

    def capture_review(route):
        review_bodies.append(route.request.post_data_json)
        route.continue_()

    page.route("**/api/sites/1/merge-preview", capture_preview)
    page.route("**/api/sites/1/review", capture_review)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    merge = page.locator("#site-detail form.merge-form")
    target = merge.locator('select[name="target_site_id"]')
    target.select_option("2")
    reason = merge.get_by_label("Begrunn sammenslåingen", exact=True)
    expect(merge.get_by_role("button", name="Forhåndsvis sammenslåing", exact=True)).to_be_disabled()
    reason.fill("Syntetisk gjennomgang fant samme kartreferanse; målpostens felt beholdes.")
    merge.get_by_role("button", name="Forhåndsvis sammenslåing", exact=True).click()
    preview = merge.locator(".merge-preview")
    expect(preview).to_contain_text("Forhåndsvisning av sammenslåing")
    expect(preview).to_contain_text("Bevis som overføres")
    commit = merge.get_by_role("button", name="Bekreft og slå sammen", exact=True)
    expect(commit).to_be_visible()
    assert len(preview_bodies) == 1
    assert review_bodies == []
    assert preview_bodies[0]["expected_revision"] == 1
    assert preview_bodies[0]["target_expected_revision"] == 1
    assert preview_bodies[0]["target_site_id"] == 2
    assert preview_bodies[0]["reason"] == reason.input_value()

    reason.fill("Endret etter forhåndsvisning; nytt snapshot kreves.")
    expect(commit).to_be_hidden()
    assert review_bodies == []
    merge.get_by_role("button", name="Forhåndsvis sammenslåing", exact=True).click()
    expect(commit).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/api/sites/1/review")) as committed:
        merge.get_by_role("button", name="Bekreft og slå sammen", exact=True).click()
    assert committed.value.status == 200, committed.value.text()
    assert len(preview_bodies) == 2
    assert len(review_bodies) == 1
    assert review_bodies[0]["action"] == "merge"
    assert review_bodies[0]["target_site_id"] == 2
    assert review_bodies[0]["target_expected_revision"] == 1
    assert review_bodies[0]["expected_revision"] == 1
    assert review_bodies[0]["reason"] == "Endret etter forhåndsvisning; nytt snapshot kreves."
    assert len(review_bodies[0]["merge_preview_hash"]) == 64


def test_navigation_url_keeps_only_allowlisted_filters(page: Page, base_url: str):
    page.goto(base_url + "/?surface=review&status=rejected&category=bunker&access=unknown&confidence=low&notes=private&token=secret&start=63.4,10.4#gps")
    expect(page.locator("#status-filter")).to_have_value("rejected")
    expect(page.locator("#category-filter")).to_have_value("bunker")
    expect(page.locator("#surface-review")).to_have_attribute("aria-pressed", "true")
    assert set(page.evaluate("[...new URLSearchParams(location.search).keys()]")) == {
        "surface", "status", "category", "access", "confidence",
    }
    assert page.evaluate("location.hash") == ""


def test_import_preview_posts_exact_original_json_bytes(page: Page, base_url: str):
    original = b'{\n  "schema_version": "1.0",\n  "batch_id": "browser-bytes",\n  "generated_at": "2026-09-14T12:00:00Z",\n  "records": []\n}\n'
    received: list[bytes] = []

    def capture_preview(route):
        received.append(route.request.post_data_buffer)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "preview_hash": "synthetic-preview", "summary": {"total": 0, "warnings": 0}, "records": [],
        }))

    page.route("**/api/admin/imports/preview", capture_preview)
    page.goto(base_url)
    page.locator("#import-file").set_input_files({"name": "bytes.json", "mimeType": "application/json", "buffer": original})
    expect(page.locator("#import-result")).to_contain_text("JSON lastet")
    page.get_by_role("button", name="Forhåndsvis", exact=True).click()
    expect(page.get_by_role("button", name="Importer", exact=True)).to_be_enabled()
    assert received == [original]


def test_import_preview_discloses_evidence_metadata_without_granting_unknown_rights(page: Page, base_url: str):
    page.route(
        "**/api/admin/imports/preview",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "preview_hash": "synthetic-evidence-preview",
                "summary": {"total": 1, "warnings": 0},
                "records": [{
                    "name": "Synthetic record",
                    "action": "create",
                    "changes": [],
                    "preserved_fields": [],
                    "evidence": [{
                        "title": "Synthetic evidence",
                        "url": "https://example.invalid/evidence",
                        "content_kind": "photograph",
                        "role": "site overview",
                        "rights_status": "unknown",
                        "claim_ids": ["claim-17"],
                    }],
                    "warnings": [],
                }],
            }),
        ),
    )
    page.goto(base_url)
    page.locator("#import-file").set_input_files({
        "name": "synthetic.json",
        "mimeType": "application/json",
        "buffer": b'{"schema_version":"1.0","batch_id":"synthetic","generated_at":"2026-09-14T12:00:00Z","records":[]}',
    })
    expect(page.locator("#import-result")).to_contain_text("JSON lastet")
    page.get_by_role("button", name="Forhåndsvis", exact=True).click()
    evidence = page.locator("#import-result .site-meta", has_text="Synthetic evidence")
    expect(evidence).to_contain_text("innhold: photograph")
    expect(evidence).to_contain_text("rolle: site overview")
    expect(evidence).to_contain_text("rettigheter ukjent; tillatelse ikke bekreftet")
    expect(evidence).to_contain_text("påstander: claim-17")
    assert "gir ingen tillatelse" in evidence.get_attribute("title")


def test_memory_observation_draft_restores_on_navigation_and_clears_on_lock(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    item = page.locator("#site-list .site-item", has_text="Junkers Ju 88 A – Jonsvatnet (markør 413)")
    item.get_by_role("button", name="Detaljer", exact=True).click()
    page.locator("details.observations-editor summary").click()
    note = page.locator("details.observations-editor [name=note]")
    note.fill("synthetic unsaved observation")
    page.get_by_role("button", name="Kart", exact=True).click()
    page.locator("#site-list .site-item", has_text="Junkers Ju 88 A – Jonsvatnet (markør 413)").get_by_role("button", name="Detaljer", exact=True).click()
    expect(page.locator("details.observations-editor [name=note]")).to_have_value("synthetic unsaved observation")

    page.get_by_role("button", name="Lås", exact=True).click()
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    expect(page.locator("#site-list")).to_contain_text("Junkers Ju 88 A – Jonsvatnet (markør 413)")
    page.get_by_role("button", name="Kart", exact=True).click()
    page.locator("#site-list .site-item", has_text="Junkers Ju 88 A – Jonsvatnet (markør 413)").get_by_role("button", name="Detaljer", exact=True).click()
    page.locator("details.observations-editor summary").click()
    expect(page.locator("details.observations-editor [name=note]")).to_have_value("")


def test_slow_superseded_list_read_cannot_replace_newer_filter(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()

    def filter_response(route):
        status = route.request.url.split("status=", 1)[-1].split("&", 1)[0]
        if status == "candidate":
            time.sleep(0.45)
            payload = [{"id": 901, "name": "Stale candidate response", "site_kind": "bunker", "status": "candidate", "precision": "unknown", "access": "unknown", "confidence": "unknown", "warnings": [], "uncertainty_m": None, "latitude": None, "longitude": None, "route_eligible": False, "route_blocking_reason": "not reviewed"}]
        else:
            payload = [{"id": 902, "name": "Latest rejected response", "site_kind": "bunker", "status": "rejected", "precision": "unknown", "access": "unknown", "confidence": "unknown", "warnings": [], "uncertainty_m": None, "latitude": None, "longitude": None, "route_eligible": False, "route_blocking_reason": "rejected"}]
        try:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
        except Exception:
            pass  # The browser may already have aborted the obsolete response.

    page.route("**/api/sites?**", filter_response)
    page.locator("#status-filter").select_option("candidate")
    page.locator("#status-filter").select_option("rejected")
    expect(page.locator("#site-list")).to_contain_text("Latest rejected response")
    page.wait_for_timeout(550)
    assert "Stale candidate response" not in page.locator("#site-list").inner_text()


def test_route_result_stays_bound_to_original_submission_snapshot(page: Page, base_url: str):
    posted: list[dict[str, object]] = []

    page.route("**/api/config", lambda route: _config(route, profiles=["foot-hiking", "foot-walking"]))

    def delayed_route(route):
        posted.append(route.request.post_data_json)
        time.sleep(0.4)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "id": 501, "name": posted[0]["name"], "distance_m": 1200, "duration_s": 900,
            "geometry": {"type": "LineString", "coordinates": [[10.3951, 63.4305], [10.4, 63.435]]},
            "gpx": "<gpx/>", "stops": [{"site_id": 1, "name": "Synthetic site", "warnings": []}],
            "warnings": [], "data_status": "valid",
        }))

    page.route("**/api/routes", lambda route: delayed_route(route) if route.request.method == "POST" else route.continue_())
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    page.evaluate("""() => {
      const site = state.sites.find(item => item.id === 1);
      site.route_eligible = true; site.route_blocking_reason = null;
      site.approach_latitude = 63.435; site.approach_longitude = 10.4;
      site.approach_access = 'public'; site.approach_reviewed_at = '2026-09-20';
      state.siteCache.set(site.id, site); addRouteSite(site.id);
    }""")
    page.get_by_role("button", name="Tur", exact=True).click()
    page.locator("#route-mode").select_option("explicit_end")
    page.locator("#route-end-lat").fill("63.44")
    page.locator("#route-end-lon").fill("10.42")
    page.locator("#route-profile").select_option("foot-walking")
    page.locator("#route-stops input[type=number]").fill("25")
    page.get_by_label("Budsjett for hele turen (minutter, valgfritt)", exact=True).fill("75")
    page.get_by_role("button", name="Beregn rute", exact=True).click()
    page.get_by_label("Rutenavn", exact=True).fill("Edited after calculation began")
    page.locator("#route-mode").select_option("return_to_start")
    expect(page.locator("#map-status")).to_contain_text("tidligere ruten")
    assert len(posted) == 1
    assert posted[0]["name"] == "Feltur i Trondheim"
    assert posted[0]["request_id"]
    assert posted[0]["mode"] == "explicit_end"
    assert posted[0]["end"] == {"lat": 63.44, "lon": 10.42}
    assert posted[0]["profile"] == "foot-walking"
    assert posted[0]["visit_minutes"] == [25]
    assert posted[0]["declared_budget_minutes"] == 75
    assert page.get_by_text("Last ned GPX", exact=True).count() == 0


def test_saved_route_budget_preview_uses_only_stored_legs(page: Page, base_url: str):
    posted: list[dict[str, object]] = []
    route_summary = {
        "id": 77, "name": "Synthetic budget route", "created_at": "2026-10-03T12:00:00Z",
        "distance_m": 3000, "duration_s": 1200, "data_status": "valid", "mode": "return_to_start",
        "profile": "foot-hiking", "stops": [{"site_id": 1, "name": "Synthetic stop", "lat": 63.4, "lon": 10.4}],
        "start": {"lat": 63.39, "lon": 10.39}, "waypoints": [{"lat": 63.4, "lon": 10.4}],
        "legs": [{"distance_m": 1500, "duration_s": 600}, {"distance_m": 1500, "duration_s": 600}],
        "visit_minutes": [20], "declared_budget_minutes": 40,
        "budget": {"travel_minutes": 20, "visit_minutes": 20, "total_minutes": 40, "declared_budget_minutes": 40, "over_budget": False, "travel_status": "available", "visit_status": "complete", "comparison_status": "within_budget"},
    }

    def routes(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps([route_summary]))

    def budget(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "route_id": 77, "stored_route_unchanged": True,
            "budget": {"travel_minutes": 20, "visit_minutes": 50, "total_minutes": 70, "declared_budget_minutes": 60, "over_budget": True, "travel_status": "available", "visit_status": "complete", "comparison_status": "over_budget"},
        }))

    page.route("**/api/routes?limit=20", routes)
    page.route("**/api/routes/77/budget", budget)
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_role("button", name="Tur", exact=True).click()
    card = page.locator("#route-history-list .route-item", has_text="Synthetic budget route")
    card.get_by_text("Forhåndsvis tidsbudsjett med lagrede rutebein", exact=True).click()
    card.get_by_label("Synthetic stop (minutter; tomt = ukjent)", exact=True).fill("50")
    card.get_by_label("Oppgitt samlet tidsbudsjett (minutter, valgfritt)", exact=True).fill("60")
    card.get_by_role("button", name="Beregn tidsbudsjett", exact=True).click()
    expect(card.locator(".route-budget-preview")).to_contain_text("over oppgitt budsjett")
    expect(card.locator(".route-budget-preview")).to_contain_text("ingen rutetjeneste kalles")
    assert posted == [{"visit_minutes": [50], "declared_budget_minutes": 60}]


def test_unavailable_saved_route_never_exposes_geometry_or_gpx(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_role("button", name="Tur", exact=True).click()
    page.route("**/api/routes/1", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "id": 1, "name": "Corrupt synthetic route", "data_status": "unavailable",
        "stored_field_status": {"geometry": "malformed"}, "geometry": None, "gpx": None,
    })))
    page.locator("#route-history-list").get_by_role("button", name="Last inn rute", exact=True).click()
    expect(page.locator("#route-result")).to_contain_text("Rutedata er utilgjengelige")
    assert page.locator("#route-result a[download]").count() == 0


def test_valid_legacy_route_keeps_geometry_but_withholds_unqualified_gpx(page: Page, base_url: str):
    route_summary = {
        "id": 88, "name": "Synthetic legacy route", "created_at": "2026-10-03T12:00:00Z",
        "distance_m": 3000, "duration_s": 1200, "data_status": "valid", "mode": "one_way",
        "profile": "foot-hiking", "stops": [], "start": {"lat": 63.39, "lon": 10.39},
        "waypoints": [], "legs": [], "warnings": [],
    }
    route_detail = {
        **route_summary,
        "geometry": {"type": "LineString", "coordinates": [[10.39, 63.39], [10.41, 63.41]]},
        "gpx_status": "unavailable_legacy",
        "warnings": ["Stored GPX cannot be qualified; no download is offered. The original bytes are retained."],
    }
    page.route("**/api/routes?limit=20", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps([route_summary])))
    page.route("**/api/routes/88", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(route_detail)))
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_role("button", name="Tur", exact=True).click()
    card = page.locator("#route-history-list .route-item", has_text="Synthetic legacy route")
    card.get_by_role("button", name="Last inn rute", exact=True).click()
    result = page.locator("#route-result")
    expect(result).to_contain_text("3.0 km")
    expect(result).to_contain_text("GPX-filen er ikke kvalifisert for nedlasting")
    assert result.locator("a[download]").count() == 0
    assert page.locator("#map .leaflet-overlay-pane path").count() >= 1


def _fulfill_content_document(route, document: dict[str, object]) -> None:
    import json

    response = route.fetch()
    body = response.json()
    body["content_document"] = document
    route.fulfill(response=response, content_type="application/json", body=json.dumps(body))
