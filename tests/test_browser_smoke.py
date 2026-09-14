from __future__ import annotations

import socket
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
    page.get_by_label("Admin token", exact=True).fill("audit-only")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.get_by_role("button", name="Load route", exact=True).click()
    page.wait_for_timeout(150)
    with page.expect_download() as download:
        page.get_by_text("Download GPX", exact=True).click()
    root = ET.parse(download.value.path()).getroot()
    assert root.tag == "{http://www.topografix.com/GPX/1/1}gpx"
    assert root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
    page.wait_for_timeout(1100)
    with page.expect_download() as second_download:
        page.get_by_text("Download GPX", exact=True).focus()
        page.keyboard.press("Enter")
    assert ET.parse(second_download.value.path()).getroot().tag == root.tag


def test_lock_clears_private_site_and_route_dom(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Admin token", exact=True).fill("audit-only")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.click()
    page.get_by_role("button", name="Load route", exact=True).click()
    page.get_by_role("button", name="Lock", exact=True).click(timeout=1000)

    assert "Synthetic site" not in page.locator("body").inner_text()
    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert page.locator("#route-result").inner_text() == ""
    assert page.locator("#route-stops").inner_text() == ""


def test_delayed_detail_response_cannot_restore_private_dom(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Admin token", exact=True).fill("audit-only")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.wait_for_timeout(250)

    def delay_detail(route):
        time.sleep(0.5)
        route.continue_()

    page.route("**/api/sites/1", delay_detail)
    page.locator("#site-list button", has_text="Details").click()
    page.get_by_role("button", name="Lock", exact=True).click(timeout=1000)
    page.wait_for_timeout(700)

    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert "Synthetic site" not in page.locator("#site-detail").inner_text()


def test_auth_failure_outside_load_sites_clears_private_workspace(page: Page, base_url: str):
    page.goto(base_url)
    page.get_by_label("Admin token", exact=True).fill("audit-only")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.click()
    page.wait_for_timeout(150)

    page.route(
        "**/api/sites/1",
        lambda route: route.fulfill(status=401, content_type="application/json", body='{"detail":"expired"}'),
    )
    page.locator("#site-list button", has_text="Details").click()
    page.wait_for_timeout(250)

    assert "Synthetic excerpt" not in page.locator("body").inner_text()
    assert page.locator("#site-detail").inner_text() == "Select a marker or site."
    assert not page.get_by_role("button", name="Load map", exact=True).is_disabled()


def test_auth_failure_allows_retry_and_lock_resets_busy_controls(page: Page, base_url: str):
    page.goto(base_url)
    token = page.get_by_label("Admin token", exact=True)
    token.fill("wrong")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.wait_for_timeout(250)
    assert not page.get_by_role("button", name="Load map", exact=True).is_disabled()

    token.fill("audit-only")
    page.get_by_role("button", name="Load map", exact=True).click()
    page.get_by_text("Synthetic site", exact=True).first.wait_for()

    page.route("**/api/sites?*", lambda route: (time.sleep(0.5), route.continue_()))
    page.get_by_role("button", name="Load map", exact=True).click()
    page.get_by_role("button", name="Lock", exact=True).click()
    page.wait_for_timeout(700)

    assert not page.get_by_role("button", name="Load map", exact=True).is_disabled()
    assert page.get_by_role("button", name="Preview", exact=True).is_disabled()
    assert page.get_by_role("button", name="Commit", exact=True).is_disabled()


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
    page.get_by_role("button", name="Lock", exact=True).click()
    page.wait_for_timeout(700)

    assert page.locator("#import-result").inner_text() == ""
    assert page.get_by_role("button", name="Preview", exact=True).is_disabled()
