from __future__ import annotations

import socket
import json
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    data_dir = tmp_path_factory.mktemp("browser-data")
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/browser_server.py")],
        cwd=ROOT,
        env={
            **__import__("os").environ,
            "BUNKERKARTET_DATA_DIR": str(data_dir),
            "BUNKERKARTET_PORT": str(port),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"browser server did not start: {stderr}")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def test_saved_route_download_survives_normal_user_delay(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_role("button", name="Tur", exact=True).click()
    page.get_by_role("button", name="Last inn rute", exact=True).click()
    page.wait_for_timeout(150)
    with page.expect_download() as download:
        page.get_by_text("Last ned GPX", exact=True).click()
    root = ET.parse(download.value.path()).getroot()
    assert root.tag == "{http://www.topografix.com/GPX/1/1}gpx"
    assert root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
    page.wait_for_timeout(1100)
    with page.expect_download() as second_download:
        page.get_by_text("Last ned GPX", exact=True).focus()
        page.keyboard.press("Enter")
    assert ET.parse(second_download.value.path()).getroot().tag == root.tag


def test_location_is_opt_in_and_only_sets_route_start(page: Page, base_url: str):
    page.context.grant_permissions(["geolocation"], origin=base_url)
    page.context.set_geolocation({"latitude": 63.44, "longitude": 10.42, "accuracy": 12})
    page.goto(base_url)
    page.get_by_role("button", name="Bruk min posisjon", exact=True).click()
    page.wait_for_function("document.getElementById('location-status').textContent.includes('nøyaktighet')")

    assert "nøyaktighet 12 m" in page.locator("#location-status").inner_text()
    assert page.locator("#route-start").inner_text() == "Start: nåværende posisjon"


def test_delayed_geolocation_cannot_restore_route_start_after_lock(page: Page, base_url: str):
    page.goto(base_url)
    page.evaluate(
        """() => {
            navigator.geolocation.getCurrentPosition = (success, error) => {
                window.__delayedGpsSuccess = success;
                window.__delayedGpsError = error;
            };
        }"""
    )
    page.get_by_role("button", name="Bruk min posisjon", exact=True).click()
    page.get_by_role("button", name="Lås", exact=True).click()
    page.evaluate(
        """() => window.__delayedGpsSuccess({
            coords: {latitude: 63.44, longitude: 10.42, accuracy: 12}
        })"""
    )
    page.wait_for_timeout(100)

    assert page.locator("#route-start").inner_text() == "Start: ingen start valgt"
    assert "Posisjon brukes bare" in page.locator("#location-status").inner_text()


def test_lock_clears_private_site_and_route_dom(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.click()
    page.get_by_role("button", name="Tur", exact=True).click()
    page.get_by_role("button", name="Last inn rute", exact=True).click()
    page.get_by_role("button", name="Lås", exact=True).click(timeout=1000)

    assert "Synthetic site" not in page.locator("body").inner_text()
    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert page.locator("#route-result").inner_text() == ""
    assert page.locator("#route-stops").inner_text() == ""


def test_delayed_detail_response_cannot_restore_private_dom(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.wait_for_timeout(250)

    def delay_detail(route):
        time.sleep(0.5)
        route.continue_()

    page.route("**/api/sites/1", delay_detail)
    page.locator("#site-list .site-item", has_text="Synthetic site").get_by_role("button", name="Detaljer", exact=True).click()
    page.get_by_role("button", name="Lås", exact=True).click(timeout=1000)
    page.wait_for_timeout(700)

    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert "Synthetic site" not in page.locator("#site-detail").inner_text()


def test_delayed_detail_response_cannot_override_newer_surface(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.wait_for()

    def delay_detail(route):
        time.sleep(0.5)
        route.continue_()

    page.route("**/api/sites/1", delay_detail)
    page.locator("#site-list .site-item", has_text="Synthetic site").get_by_role("button", name="Detaljer", exact=True).click()
    page.get_by_role("button", name="Kart", exact=True).click()
    page.wait_for_timeout(700)

    assert page.get_by_role("button", name="Kart", exact=True).get_attribute("aria-pressed") == "true"
    assert page.locator("#detail-panel").is_hidden()
    assert "Stedsdetaljer: Synthetic site" not in page.locator("#site-detail").inner_text()


def test_auth_failure_outside_load_sites_clears_private_workspace(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.click()
    page.wait_for_timeout(150)

    page.route(
        "**/api/sites/1",
        lambda route: route.fulfill(status=401, content_type="application/json", body='{"detail":"expired"}'),
    )
    page.locator("#site-list .site-item", has_text="Synthetic site").get_by_role("button", name="Detaljer", exact=True).click()
    page.wait_for_timeout(250)

    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert page.locator("#site-detail").inner_text() == "Velg en markør eller et sted."
    assert not page.get_by_role("button", name="Last inn kart", exact=True).is_disabled()


def test_auth_failure_allows_retry_and_lock_resets_busy_controls(page: Page, base_url: str):
    page.goto(base_url)
    token = page.get_by_label("Administratortoken", exact=True)
    token.fill("wrong")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.wait_for_timeout(250)
    assert not page.get_by_role("button", name="Last inn kart", exact=True).is_disabled()

    token.fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.wait_for()

    page.route("**/api/sites?*", lambda route: (time.sleep(0.5), route.continue_()))
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_role("button", name="Lås", exact=True).click()
    page.wait_for_timeout(700)

    assert not page.get_by_role("button", name="Last inn kart", exact=True).is_disabled()
    assert page.get_by_role("button", name="Forhåndsvis", exact=True).is_disabled()
    assert page.get_by_role("button", name="Importer", exact=True).is_disabled()


def test_stale_geojson_401_cannot_clear_new_session(page: Page, base_url: str):
    page.goto(base_url)
    token = page.get_by_label("Administratortoken", exact=True)
    token.fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.wait_for()

    def delayed_unauthorized(route):
        time.sleep(0.5)
        route.fulfill(status=401, content_type="application/json", body='{"detail":"expired"}')

    page.route("**/api/sites.geojson", delayed_unauthorized)
    page.get_by_role("button", name="Eksporter hele katalogen", exact=True).click()
    page.get_by_role("button", name="Lås", exact=True).click()
    token.fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.wait_for()
    page.wait_for_timeout(700)

    assert "Synthetic site" in page.locator("body").inner_text()


def test_lock_discards_delayed_import_file_read(page: Page, base_url: str):
    page.goto(base_url)
    page.evaluate(
        """() => {
            const file = new File(['{"batch_id":"stale"}'], 'stale.json', {type: 'application/json'});
            Object.defineProperty(file, 'text', {value: () => new Promise(resolve =>
                setTimeout(() => resolve('{"batch_id":"stale"}'), 500))});
            const transfer = new DataTransfer();
            transfer.items.add(file);
            const input = document.getElementById('import-file');
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    page.get_by_role("button", name="Lås", exact=True).click()
    page.wait_for_timeout(700)

    assert page.locator("#import-result").inner_text() == ""
    assert page.get_by_role("button", name="Forhåndsvis", exact=True).is_disabled()


def test_stale_preview_cannot_enable_commit_for_new_file(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    payload = {
        "schema_version": "1.0",
        "batch_id": "browser-preview",
        "generated_at": "2026-09-14T12:00:00Z",
        "records": [{
            "external_key": "browser:preview",
            "name": "Synthetic preview site",
            "site_kind": "bunker",
            "geometry": {"latitude": 63.435, "longitude": 10.4},
            "precision": "approximate",
            "uncertainty_m": 100,
            "location_basis": "map_reference",
            "status": "candidate",
            "access": "unknown",
            "sources": [{
                "url": "https://example.com/browser-preview",
                "title": "Synthetic preview source",
                "source_type": "test",
                "excerpt": "Synthetic preview excerpt",
            }],
        }],
    }
    page.route("**/api/admin/imports/preview", lambda route: (time.sleep(0.5), route.continue_()))
    page.locator("#import-file").set_input_files({"name": "a.json", "mimeType": "application/json", "buffer": json.dumps(payload).encode()})
    page.get_by_role("button", name="Forhåndsvis", exact=True).click()
    page.locator("#import-file").set_input_files({"name": "b.json", "mimeType": "application/json", "buffer": json.dumps({**payload, "batch_id": "browser-preview-b"}).encode()})
    page.wait_for_timeout(700)

    assert page.get_by_role("button", name="Importer", exact=True).is_disabled()
    page.get_by_role("button", name="Forhåndsvis", exact=True).click()
    page.wait_for_timeout(700)
    assert not page.get_by_role("button", name="Importer", exact=True).is_disabled()


def test_new_file_read_clears_old_import_before_preview(page: Page, base_url: str):
    page.goto(base_url)
    payload = {
        "schema_version": "1.0",
        "batch_id": "browser-file-a",
        "generated_at": "2026-09-14T12:00:00Z",
        "records": [],
    }
    page.locator("#import-file").set_input_files({
        "name": "a.json", "mimeType": "application/json", "buffer": json.dumps(payload).encode()
    })
    page.wait_for_function("!document.getElementById('preview-import').disabled")

    page.evaluate(
        """() => {
            const file = new File(['{"schema_version":"1.0","batch_id":"browser-file-b","generated_at":"2026-09-14T12:00:00Z","records":[]}'], 'b.json', {type: 'application/json'});
            Object.defineProperty(file, 'text', {value: () => new Promise(resolve =>
                setTimeout(() => resolve('{"schema_version":"1.0","batch_id":"browser-file-b","generated_at":"2026-09-14T12:00:00Z","records":[]}'), 500))});
            const transfer = new DataTransfer();
            transfer.items.add(file);
            const input = document.getElementById('import-file');
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )

    assert page.get_by_role("button", name="Forhåndsvis", exact=True).is_disabled()
    assert page.get_by_role("button", name="Importer", exact=True).is_disabled()
    page.locator("#preview-import").evaluate("button => button.click()")
    assert page.locator("#import-result").inner_text() == ""
    page.wait_for_timeout(700)
    assert page.locator("#import-result").inner_text() == "JSON lastet. Forhåndsvis før import."


def test_mobile_surface_navigation_is_keyboard_usable_without_overflow(page: Page, base_url: str):
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(reduced_motion="reduce")
    page.goto(base_url)

    review = page.get_by_role("button", name="Vurdering", exact=True)
    review.focus()
    page.keyboard.press("Enter")
    assert review.get_attribute("aria-pressed") == "true"
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    map_button = page.get_by_role("button", name="Kart", exact=True)
    map_button.focus()
    page.keyboard.press("Enter")
    assert map_button.get_attribute("aria-pressed") == "true"


def test_validation_errors_are_shown_next_to_the_field(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.locator("#site-list .site-item", has_text="Synthetic site").get_by_role("button", name="Detaljer", exact=True).click()
    page.wait_for_timeout(200)

    def reject_patch(route):
        if route.request.method == "PATCH":
            route.fulfill(
                status=422,
                content_type="application/json",
                body='{"detail":[{"loc":["body","name"],"msg":"Navn må fylles ut"}]}',
            )
        else:
            route.continue_()

    page.route("**/api/sites/1", reject_patch)
    page.get_by_text("Rediger sted", exact=True).wait_for()
    page.get_by_text("Rediger sted", exact=True).click()
    page.get_by_label("Navn", exact=True).fill("")
    page.get_by_role("button", name="Lagre stedsendringer", exact=True).click()

    page.locator(".field-error").filter(has_text="Navn må fylles ut").wait_for(state="visible")
    assert "[object Object]" not in page.locator("body").inner_text()


def test_identical_points_offer_a_named_choice_without_clustering(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    overlap = page.get_by_text("Sammenfallende punkter", exact=True)
    overlap.wait_for(state="visible")
    choices = page.locator("#overlap-panel button")
    assert choices.count() >= 2
    choices.filter(has_text="Synthetic site").click()
    page.get_by_role("heading", name="Stedsdetaljer: Synthetic site", exact=True).wait_for(state="visible", timeout=3000)


def test_list_detail_back_returns_focus_to_visible_site_control(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    origin = page.locator("#site-list .site-item", has_text="Synthetic site").get_by_role("button", name="Detaljer", exact=True)
    origin.focus()
    page.keyboard.press("Enter")
    page.get_by_role("heading", name="Stedsdetaljer: Synthetic site", exact=True).wait_for()
    summary = page.locator("details.site-editor > summary")
    summary.focus()
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement === document.querySelector('details.site-editor > summary')")
    close = page.get_by_role("button", name="Tilbake til kart", exact=True)
    close.focus()
    # A queued disclosure event must not steal focus after the user moves on.
    page.locator("details.site-editor").dispatch_event("toggle")
    assert close.evaluate("element => element === document.activeElement")
    page.keyboard.press("Enter")

    assert page.get_by_role("button", name="Kart", exact=True).get_attribute("aria-pressed") == "true"
    assert page.locator("#detail-panel").is_hidden()
    assert page.evaluate("""() => {
        const active = document.activeElement;
        return active?.dataset.detailSiteId === '1' && active.offsetParent !== null;
    }""")


def test_overlap_detail_back_returns_focus_to_original_choice(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    origin = page.locator("#overlap-panel button", has_text="Synthetic site")
    origin.focus()
    page.keyboard.press("Enter")
    page.get_by_role("heading", name="Stedsdetaljer: Synthetic site", exact=True).wait_for()
    page.get_by_role("button", name="Tilbake til kart", exact=True).press("Enter")

    assert page.evaluate("""() => {
        const active = document.activeElement;
        return active?.dataset.detailSiteId === '1' && active.dataset.detailOrigin === 'overlap' && active.offsetParent !== null;
    }""")


def test_marker_popup_detail_back_returns_focus_to_named_marker(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    marker = page.locator(".leaflet-marker-icon[data-detail-site-id='1']")
    marker.wait_for(state="visible")
    marker.click()
    popup = page.locator(".leaflet-popup")
    popup.get_by_role("button", name="Detaljer", exact=True).click()
    page.get_by_role("heading", name="Stedsdetaljer: Synthetic site", exact=True).wait_for()
    page.get_by_role("button", name="Tilbake til kart", exact=True).press("Enter")

    assert page.evaluate("""() => {
        const active = document.activeElement;
        return active?.dataset.detailSiteId === '1' && active.dataset.detailOrigin === 'marker' && active.getAttribute('aria-label')?.includes('Synthetic site') && active.offsetParent !== null;
    }""")


def test_complete_synthetic_operator_flow_reaches_saved_gpx(page: Page, base_url: str):
    payload = {
        "schema_version": "1.0",
        "batch_id": "browser-e2e-1",
        "generated_at": "2026-09-14T12:00:00Z",
        "records": [{
            "external_key": "browser:e2e",
            "name": "E2E testbunker med svært langt navn som skal brytes trygt",
            "site_kind": "bunker",
            "geometry": {"latitude": 63.436, "longitude": 10.401},
            "precision": "approximate",
            "uncertainty_m": 50,
            "location_basis": "map_reference",
            "status": "candidate",
            "access": "unknown",
            "sources": [{
                "url": "https://example.com/browser-e2e",
                "title": "E2E-kilde",
                "source_type": "test",
                "excerpt": "Syntetisk testkilde.",
            }],
            "confidence": "medium",
        }],
    }
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.locator("#import-file").set_input_files({
        "name": "e2e.json", "mimeType": "application/json", "buffer": json.dumps(payload).encode()
    })
    page.get_by_role("button", name="Forhåndsvis", exact=True).click()
    page.get_by_role("button", name="Importer", exact=True).click()
    page.locator("#site-list").get_by_text("E2E testbunker med svært langt navn som skal brytes trygt", exact=True).wait_for()

    page.get_by_role("button", name="Vurdering", exact=True).press("Enter")
    candidate = page.locator(".candidate-item", has_text="E2E testbunker")
    candidate.get_by_role("button", name="Detaljer", exact=True).click()
    page.locator("#detail-panel").get_by_role("button", name="Marker som kildegjennomgått", exact=True).click()
    page.locator("#detail-panel").get_by_role("button", name="Marker som feltverifisert", exact=True).wait_for()
    page.get_by_text("Feltobservasjoner", exact=True).click()
    observation = page.locator("details.observations-editor form.observation-form")
    observation.get_by_label("Observasjonsnotat", exact=True).fill("Syntetisk observasjon")
    observation.locator("select[name=point_role]").select_option("feature")
    observation.locator("input[name=uncertainty_m]").fill("20")
    observation.locator("input[name=latitude]").fill("63.436")
    observation.locator("input[name=longitude]").fill("10.401")
    observation.get_by_role("button", name="Lagre observasjon", exact=True).click()
    page.locator("#detail-panel").get_by_role("button", name="Marker som feltverifisert", exact=True).wait_for()
    page.get_by_role("button", name="Marker som feltverifisert", exact=True).click()
    page.locator("#detail-panel").get_by_role("button", name="Bekreft", exact=True).wait_for()
    page.get_by_role("button", name="Bekreft", exact=True).click()
    page.get_by_text("Rediger sted", exact=True).wait_for()
    page.get_by_text("Rediger sted", exact=True).click()
    editor = page.locator("details", has_text="Rediger sted")
    approach = editor.locator("form.observation-form")
    approach.locator("input[type=number]").nth(0).fill("63.4355")
    approach.locator("input[type=number]").nth(1).fill("10.4005")
    editor.locator("form.observation-form select").select_option("public")
    approach.locator("textarea").fill("Syntetisk offentlig vei")
    with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/approach")):
        approach.get_by_role("button", name="Lagre vurdering av offentlig tilnærming", exact=True).click()
    page.wait_for_function("""() => [...document.querySelectorAll('#site-list .site-item')].some(item => item.textContent.includes('E2E testbunker') && item.textContent.includes('Legg til rute'))""")

    page.get_by_role("button", name="Kart", exact=True).press("Enter")
    item = page.locator("#site-list .site-item", has_text="E2E testbunker")
    item.get_by_role("button", name="Legg til rute", exact=True).wait_for(state="visible")
    item.get_by_role("button", name="Legg til rute", exact=True).click()
    page.get_by_role("button", name="Tur", exact=True).press("Enter")
    page.locator("#route-stops").get_by_text("E2E testbunker med svært langt navn som skal brytes trygt", exact=True).wait_for()
    assert not page.get_by_role("button", name="Beregn rute", exact=True).is_disabled()
    page.get_by_role("button", name="Beregn rute", exact=True).click()
    page.get_by_text("Last ned GPX", exact=True).wait_for()
    with page.expect_download() as download:
        page.get_by_text("Last ned GPX", exact=True).click()
    assert ET.parse(download.value.path()).getroot().tag == "{http://www.topografix.com/GPX/1/1}gpx"
    page.get_by_role("button", name="Last inn rute", exact=True).last.click()
    assert page.get_by_text("Last ned GPX", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_two_hundred_percent_zoom_keeps_focus_and_width_bounded(page: Page, base_url: str):
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(base_url)
    page.evaluate("document.body.style.zoom = '2'")
    nav = page.get_by_role("button", name="Vurdering", exact=True)
    nav.focus()
    assert page.evaluate("document.activeElement === document.getElementById('surface-review')")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.evaluate("""() => {
        const parse = (value) => value.match(/\\d+/g).slice(0, 3).map(Number).map((channel) => channel / 255);
        const luminance = (rgb) => rgb.map((channel) => channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
        const button = document.getElementById('surface-review');
        const foreground = luminance(parse(getComputedStyle(button).color));
        const background = luminance(parse(getComputedStyle(button).backgroundColor));
        return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05) >= 4.5;
    }""")
