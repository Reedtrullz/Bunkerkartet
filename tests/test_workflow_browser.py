from __future__ import annotations

import json
import hashlib
from pathlib import Path

from playwright.sync_api import Page, expect

from test_browser_contracts import base_url, page  # noqa: F401 - shared browser fixtures


def _open_detail(page: Page, base_url: str, *, pilot_status: dict[str, object] | None = None) -> None:
    if pilot_status is not None:
        page.route(
            "**/api/pilots/status",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(pilot_status)),
        )
    page.goto(base_url)
    page.get_by_label("Administratortoken", exact=True).fill("audit-only")
    page.get_by_role("button", name="Last inn kart", exact=True).click()
    page.get_by_text("Junkers Ju 88 A – Jonsvatnet (markør 413)", exact=True).first.wait_for()
    # The application index natively loads workflow-ui.js; wait for its script
    # resource instead of injecting a second copy and registering duplicate listeners.
    page.wait_for_function("performance.getEntriesByType('resource').some(entry => entry.name.endsWith('/static/workflow-ui.js'))")
    assert page.evaluate("performance.getEntriesByType('resource').filter(entry => entry.name.endsWith('/static/workflow-ui.js')).length") == 1
    page.locator("#site-list .site-item").get_by_role("button", name="Detaljer", exact=True).first.click()
    expect(page.locator('[data-detail-section="exchange-workflows"]')).to_have_count(1)


def _create_offline_test_note(page: Page, base_url: str, pack_id: str) -> tuple[dict[str, object], str]:
    _open_detail(page, base_url, pilot_status={"enabled": ["offline"], "defaults_enabled": False, "production_data_used": False})
    section = page.locator('[data-detail-section="offline-notebook"]')
    section.locator("summary").click()
    page.evaluate("navigator.storage.persist = async () => true")
    pack = {"schema": "bunkerkartet-offline-pack-v1", "pack_id": pack_id, "created_at": "2026-10-03T12:00:00Z", "expires_at": "2026-10-10T12:00:00Z", "selected_fields": ["external_key", "name", "revision"], "records": [{"external_key": "krigskart:413", "name": "Synthetic site", "revision": 3}]}
    payload_json = json.dumps(pack)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    page.route("**/api/pilots/offline/packs", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"pack_id": pack_id, "payload_json": payload_json, "sha256": payload_hash, "created_at": pack["created_at"], "expires_at": pack["expires_at"], "site_count": 1, "stale": False, "tile_cache_included": False, "bearer_token_included": False})))
    section.get_by_label("Jeg godkjenner lagring av akkurat dette utvalget på denne enheten til utløp eller sletting", exact=True).check()
    section.get_by_role("button", name="Lagre valgt pakke på denne enheten", exact=True).click()
    expect(section.get_by_role("status")).to_contain_text("lagret på denne enheten")
    page.goto(f"{base_url}/static/offline-notebook.html")
    page.get_by_label("Notat for valgt sted", exact=True).fill("Synthetic offline observation")
    page.get_by_role("button", name="Lagre notat lokalt", exact=True).click()
    request_id = page.locator("[data-outbox-request-id]").first.get_attribute("data-outbox-request-id")
    assert request_id
    return pack, request_id


def _approve_offline_test_note(page: Page, pack_id: str, request_id: str, revisions: list[int]) -> None:
    page.get_by_label("Administratortoken for manuell kontroll", exact=False).fill("sync-browser-token")
    page.route(f"**/api/pilots/offline/packs/{pack_id}", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"stale": False, "payload": {}})))

    def site_revision(route):
        revision = revisions.pop(0) if len(revisions) > 1 else revisions[0]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": 1, "external_key": "krigskart:413", "name": "Synthetic site", "revision": revision}))

    page.route("**/api/sites/1", site_revision)
    page.get_by_role("button", name="Kontroller serverrevisjon manuelt", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Serverrevisjon er hentet")
    page.route(f"**/api/pilots/offline/packs/{pack_id}/outbox", lambda route: route.fulfill(status=201, content_type="application/json", body=json.dumps({"original_request_id": request_id, "state": "pending_review"})))
    page.get_by_role("button", name="Send notat til serverens manuelle vurderingskø", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("manuelle vurderingskø")
    expect(page.get_by_role("button", name="Lever godkjent observasjon", exact=True)).to_have_count(0)
    page.get_by_label("Referanse for manuell gjennomgang (ikke en token)", exact=True).fill("synthetic-sync-review")
    page.get_by_label("Jeg har gjennomgått serverens gjeldende sted og dette notatet", exact=True).check()
    page.route(f"**/api/pilots/offline/packs/{pack_id}/outbox/{request_id}/review", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"pack_id": pack_id, "original_request_id": request_id, "state": "approved_for_sync", "reviewer_ref": "synthetic-sync-review", "reviewed_site_revision": 3, "synced": False})))
    page.get_by_role("button", name="Godkjenn for senere manuell synkronisering", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("approved_for_sync")


def test_dossier_preview_and_download_are_bound_to_same_hash_and_safe_preview(page: Page, base_url: str):
    _open_detail(page, base_url)
    section = page.locator('[data-detail-section="exchange-workflows"]')
    section.locator("summary").first.click()
    section.locator('[data-workflow="dossier"] summary').click()
    captured: list[dict[str, object]] = []
    fixture = {
        "dossier": {"generated_at": "2026-10-03T12:00:00Z", "sites": [{"name": "<script>window.dossierXss=1</script>"}]},
        "source_hash": "a" * 64,
        "geojson": {"type": "FeatureCollection", "features": []},
        "gpx": "<?xml version='1.0'?><gpx/>",
        "gpx_status": "available",
        "html": "<!doctype html><html><body><h1>Safe synthetic dossier</h1><script>parent.dossierXss=1</script></body></html>",
        "preview_hash": "b" * 64,
    }

    def dossier_api(route):
        captured.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(fixture))

    page.route("**/api/admin/exchanges/dossiers/preview", dossier_api)
    page.route("**/api/admin/exchanges/dossiers/download", dossier_api)
    section.get_by_role("button", name="Forhåndsvis valgt dossier", exact=True).click()
    expect(section.locator("pre").first).to_contain_text("b" * 64)
    expect(section.locator("pre")).to_contain_text("<script>window.dossierXss=1</script>")
    assert page.evaluate("window.dossierXss === undefined")
    expect(section.locator("iframe[sandbox]")).to_have_count(1)

    with page.expect_download() as download:
        section.get_by_role("button", name="Last ned dossier-HTML", exact=True).click()
    assert download.value.suggested_filename.endswith(".html")
    assert len(captured) == 2
    assert "preview_hash" not in captured[0]
    assert captured[1]["preview_hash"] == "b" * 64
    assert captured[1]["generated_at"] == "2026-10-03T12:00:00Z"
    assert captured[0]["include_private_start"] is False
    assert captured[0]["include_private_questions"] is False
    assert captured[0]["include_private_observations"] is False


def test_import_subset_rejects_duplicate_keys_then_previews_and_commits_explicit_selection(page: Page, base_url: str, tmp_path: Path):
    _open_detail(page, base_url)
    section = page.locator('[data-detail-section="exchange-workflows"]')
    section.locator("summary").first.click()
    section.locator('[data-workflow="import-subset"] summary').click()
    chooser = section.locator('input[type="file"][data-workflow="import-package"]')
    chooser.set_input_files({"name": "duplicate.json", "mimeType": "application/json", "buffer": b'{"records":[],"records":[]}'})
    section.get_by_role("button", name="Les valgt importfil", exact=True).click()
    expect(section.get_by_role("status")).to_contain_text("duplisert nøkkel")

    package = {
        "schema_version": "1.1",
        "batch_id": "parent-batch",
        "generated_at": "2026-10-03T10:00:00Z",
        "source": "synthetic fixture",
        "records": [
            {"external_key": "synthetic:a", "name": "A", "site_kind": "bunker", "related_site_keys": []},
            {"external_key": "synthetic:b", "name": "B", "site_kind": "bunker", "related_site_keys": ["synthetic:a"]},
        ],
    }
    chooser.set_input_files({"name": "package.json", "mimeType": "application/json", "buffer": json.dumps(package).encode()})
    section.get_by_role("button", name="Les valgt importfil", exact=True).click()
    expect(section).to_contain_text("synthetic:a")
    section.get_by_label("Velg stabile importnøkler", exact=True).fill("synthetic:b")
    section.get_by_label("Ny batch-ID", exact=True).fill("subset-batch")
    section.get_by_label("Begrunnelse for delutvalg", exact=True).fill("Synthetic subset review")
    previews: list[dict[str, object]] = []

    def preview(route):
        previews.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "package": {**package, "batch_id": "subset-batch", "records": [package["records"][1]]},
            "manifest": {"parent_package_hash": "c" * 64, "selected_keys": ["synthetic:b"]},
            "selection_hash": "d" * 64,
            "preview_hash": "e" * 64,
            "native_preview": {"preview_hash": "f" * 64, "effects": []},
        }))

    page.route("**/api/admin/exchanges/import-subsets/preview", preview)
    section.get_by_role("button", name="Forhåndsvis delutvalg", exact=True).click()
    expect(section).to_contain_text("parent_package_hash")
    expect(section.get_by_role("button", name="Importer godkjent delutvalg", exact=True)).to_be_disabled()
    section.get_by_label("Bekreft import av akkurat forhåndsviste poster", exact=True).check()

    commits: list[dict[str, object]] = []

    def commit(route):
        commits.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": {"created": 1}, "manifest": {}, "package_hash": "c" * 64}))

    page.route("**/api/admin/exchanges/import-subsets/commit", commit)
    section.get_by_role("button", name="Importer godkjent delutvalg", exact=True).click()
    expect(section.locator("pre").first).to_contain_text("created")
    assert previews[0]["selected_keys"] == ["synthetic:b"]
    assert previews[0]["source_package"] == package
    assert commits[0]["preview_hash"] == "e" * 64
    assert commits[0]["selection_hash"] == "d" * 64


def test_gis_download_requires_revision_bound_preview_and_reason_before_commit(page: Page, base_url: str):
    _open_detail(page, base_url)
    section = page.locator('[data-detail-section="exchange-workflows"]')
    section.locator("summary").first.click()
    section.locator('[data-workflow="gis-edit"] summary').click()
    pack = {
        "schema_version": "1.0", "type": "FeatureCollection", "coordinate_order": "longitude_latitude",
        "site_ids": [1],
        "features": [{"type": "Feature", "id": "krigskart:413", "geometry": {"type": "Point", "coordinates": [10.4, 63.4]},
                      "properties": {"external_key": "krigskart:413", "site_id": 1, "source_revision": 3,
                                     "feature_type": "site", "point_role": "feature"}}],
        "source_hash": "a" * 64,
    }
    page.route("**/api/admin/exchanges/gis/edit-packs", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(pack)))
    with page.expect_download():
        section.get_by_role("button", name="Last ned GIS-redigeringspakke", exact=True).click()
    edited = {**pack["features"][0], "geometry": {"type": "Point", "coordinates": [10.41, 63.41]}}
    edited_pack = {**pack, "features": [edited]}
    chooser = section.locator('input[type="file"][data-workflow="gis-edits"]')
    chooser.set_input_files({"name": "edited.geojson", "mimeType": "application/geo+json", "buffer": json.dumps(edited_pack).encode()})
    section.get_by_role("button", name="Les redigert GIS-fil", exact=True).click()
    section.get_by_label("Begrunnelse for koordinatendring", exact=True).fill("Synthetic coordinate correction review")
    page.route("**/api/admin/exchanges/gis/edit-previews", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"preview_hash": "b" * 64, "edits": [{"expected_revision": 3}]})))
    page.route("**/api/admin/exchanges/gis/edit-commits", lambda route: (commits.append(route.request.post_data_json), route.fulfill(status=200, content_type="application/json", body=json.dumps({"updated": 1, "preview_hash": "b" * 64}))))
    expect(section.get_by_role("button", name="Lagre forhåndsviste koordinatendringer", exact=True)).to_be_disabled()
    section.get_by_label("Bekreft WGS84-rekkefølgen lengdegrad, breddegrad", exact=True).check()
    section.get_by_role("button", name="Forhåndsvis GIS-endringer", exact=True).click()
    expect(section).to_contain_text("source_revision")
    expect(section.get_by_role("button", name="Lagre forhåndsviste koordinatendringer", exact=True)).to_be_disabled()

    # The commit remains disabled until this separate effect-confirmation step.
    section.get_by_label("Jeg har gjennomgått akkurat disse koordinatendringene", exact=True).check()
    commits: list[dict[str, object]] = []
    expect(section.get_by_role("button", name="Lagre forhåndsviste koordinatendringer", exact=True)).to_be_enabled()
    section.get_by_role("button", name="Lagre forhåndsviste koordinatendringer", exact=True).click()
    expect(section.locator("pre").last).to_contain_text('"updated": 1')
    assert commits[0]["preview_hash"] == "b" * 64
    assert commits[0]["reason"] == "Synthetic coordinate correction review"


def test_private_visit_uses_revision_transitions_and_existing_observation_attachment(page: Page, base_url: str):
    def reviewed_detail(route):
        response = route.fetch()
        payload = response.json()
        payload["route_eligible"] = True
        route.fulfill(response=response, json=payload)

    page.route("**/api/sites/1", reviewed_detail)
    _open_detail(page, base_url)
    section = page.locator('[data-detail-section="visit-workflow"]')
    section.locator("summary").click()
    expect(section).to_contain_text("offentlig vurdert tilnærming")
    request_ids: list[str] = []
    transitions: list[dict[str, object]] = []
    attachments: list[dict[str, object]] = []

    def create_visit(route):
        request = route.request.post_data_json
        request_ids.append(request["request_id"])
        assert request["manual_site_ids"] == [1]
        assert request["selected_questions"] == []
        route.fulfill(status=201, content_type="application/json", body=json.dumps({"id": 22, "state": "planned", "revision": 1, "site_ids": [1], "selected_questions": []}))

    def transition(route):
        body = route.request.post_data_json
        transitions.append(body)
        if body["state"] == "occurred":
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": 22, "state": "occurred", "revision": 2, "site_ids": [1], "selected_questions": []}))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": 22, "state": "closed", "revision": 3, "site_ids": [1], "selected_questions": []}))

    page.route("**/api/visits", create_visit)
    page.route("**/api/visits/22", transition)
    page.route("**/api/visits/22/observations", lambda route: (attachments.append(route.request.post_data_json), route.fulfill(status=201, content_type="application/json", body=json.dumps({"visit_id": 22, "observation_id": 9, "idempotent": False, "revision": 2}))))
    section.get_by_role("button", name="Planlegg privat feltbesøk", exact=True).click()
    expect(section).to_contain_text('"planned"')
    section.get_by_label("Faktisk besøksdato", exact=True).fill("2026-10-03")
    section.get_by_role("button", name="Merk som gjennomført", exact=True).click()
    expect(section).to_contain_text('"occurred"')
    section.get_by_label("Eksisterende observasjons-ID", exact=True).fill("9")
    section.get_by_role("button", name="Knytt eksisterende observasjon til besøket", exact=True).click()
    section.get_by_role("button", name="Lukk gjennomført besøk", exact=True).click()
    expect(section).to_contain_text("Besøket er lukket")
    assert len(request_ids[0]) >= 20
    assert transitions[0]["expected_revision"] == 1 and transitions[0]["outcomes"] == []
    assert transitions[1] == {"expected_revision": 2, "state": "closed"}
    assert attachments[0]["observation_id"] == 9 and attachments[0]["site_id"] == 1


def test_offline_notebook_requires_device_consent_uses_ephemeral_token_and_clears_outbox(page: Page, base_url: str):
    _open_detail(page, base_url, pilot_status={"enabled": ["offline"], "defaults_enabled": False, "production_data_used": False})
    section = page.locator('[data-detail-section="offline-notebook"]')
    section.locator("summary").click()
    expect(section).to_contain_text("Ingen automatisk synkronisering")
    expect(section.get_by_role("button", name="Lagre valgt pakke på denne enheten", exact=True)).to_be_disabled()
    page.evaluate("navigator.storage.persist = async () => true")
    pack = {"schema": "bunkerkartet-offline-pack-v1", "pack_id": "pack-1", "created_at": "2026-10-03T12:00:00Z", "expires_at": "2026-10-10T12:00:00Z", "selected_fields": ["external_key", "name", "revision"], "records": [{"external_key": "krigskart:413", "name": "Synthetic site", "revision": 3}]}
    payload_json = json.dumps(pack)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    page.route("**/api/pilots/offline/packs", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"pack_id": "pack-1", "payload_json": payload_json, "sha256": payload_hash, "created_at": pack["created_at"], "expires_at": pack["expires_at"], "site_count": 1, "stale": False, "tile_cache_included": False, "bearer_token_included": False})))
    section.get_by_label("Jeg godkjenner lagring av akkurat dette utvalget på denne enheten til utløp eller sletting", exact=True).check()
    section.get_by_role("button", name="Lagre valgt pakke på denne enheten", exact=True).click()
    expect(section.get_by_role("status")).to_contain_text("lagret på denne enheten")
    page.goto(f"{base_url}/static/offline-notebook.html")
    expect(page.get_by_role("heading", name="Frakoblet feltnotatbok", exact=True)).to_be_visible()
    expect(page.get_by_text("Aktiv til utløp", exact=False)).to_be_visible()
    page.get_by_label("Notat for valgt sted", exact=True).fill("Synthetic offline observation")
    page.get_by_role("button", name="Lagre notat lokalt", exact=True).click()
    request_id = page.locator("[data-outbox-request-id]").first.get_attribute("data-outbox-request-id")
    assert request_id and len(request_id) >= 20
    page.get_by_label("Administratortoken for manuell kontroll", exact=False).fill("temporary-audit-token")
    page.route("**/api/pilots/offline/packs/pack-1/outbox", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"original_request_id": request_id, "state": "pending_review"})))
    page.get_by_role("button", name="Send notat til serverens manuelle vurderingskø", exact=True).click()
    page.route("**/api/sites/1", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"id": 1, "external_key": "krigskart:413", "name": "Synthetic changed site", "revision": 4})))
    page.route("**/api/pilots/offline/packs/pack-1", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"cleared": True, "outbox_cleared": True}) if route.request.method == "DELETE" else json.dumps({"stale": False, "payload": pack})))
    page.get_by_role("button", name="Kontroller serverrevisjon manuelt", exact=True).click()
    expect(page.get_by_text("Serverrevisjon har endret seg", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Godkjenn for senere manuell synkronisering", exact=True)).to_be_disabled()
    page.get_by_label("Referanse for manuell gjennomgang (ikke en token)", exact=True).fill("synthetic-manual-review")
    page.get_by_label("Jeg har gjennomgått serverens gjeldende sted og dette notatet", exact=True).check()
    expect(page.get_by_role("button", name="Utsett etter gjennomgang", exact=True)).to_be_enabled()
    page.on("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Tøm valgt pakke og tilhørende notater", exact=True).click()
    expect(page.get_by_text("Denne nettleseren har ingen lokalt godkjente pakker.", exact=False)).to_be_visible()
    assert page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
    assert page.evaluate("!Object.keys(localStorage).some(key => key.toLowerCase().includes('token'))")


def test_offline_approved_sync_retries_same_request_id_after_lost_response(page: Page, base_url: str):
    pack_id = "pack-sync-retry"
    _pack, request_id = _create_offline_test_note(page, base_url, pack_id)
    _approve_offline_test_note(page, pack_id, request_id, [3])
    sync_requests: list[dict[str, object]] = []
    committed_before_disconnect = False

    def sync(route):
        nonlocal committed_before_disconnect
        sync_requests.append(route.request.post_data_json)
        assert route.request.url.endswith(f"/{request_id}/sync")
        if len(sync_requests) == 1:
            # Model a server commit followed by a lost response; retry must reuse the original ID.
            committed_before_disconnect = True
            route.abort(error_code="connectionreset")
            return
        assert committed_before_disconnect
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "synced": True,
            "original_request_id": request_id,
            "idempotent": True,
            "observation": {"id": 71, "request_id": request_id},
            "site": {"id": 1, "revision": 4},
        }))

    page.route(f"**/api/pilots/offline/packs/{pack_id}/outbox/{request_id}/sync", sync)
    sync_button = page.get_by_role("button", name="Lever godkjent observasjon", exact=True)
    expect(sync_button).to_be_visible()
    expect(sync_button).to_be_enabled()
    sync_button.click()
    expect(page.get_by_role("status")).to_contain_text("er beholdt; kontroller status")
    expect(sync_button).to_be_enabled()
    sync_button.click()
    expect(page.get_by_role("status")).to_contain_text("Observasjonen er levert")
    assert len(sync_requests) == 2
    assert sync_requests == [{"expected_site_revision": 3}, {"expected_site_revision": 3}]
    local_records = page.evaluate("async () => await window.BKOfflineNotebook.listOutbox('pack-sync-retry')")
    local_json = json.dumps(local_records)
    assert local_records[0]["server_review"]["sync_result"] == {
        "synced": True,
        "original_request_id": request_id,
        "idempotent": True,
        "observation_id": 71,
        "site_revision": 4,
    }
    assert "sync-browser-token" not in local_json
    assert "sync-browser-token" not in page.evaluate("JSON.stringify({local: localStorage, session: sessionStorage})")
    expect(page.get_by_role("button", name="Lever godkjent observasjon", exact=True)).to_have_count(0)


def test_offline_approved_sync_blocks_after_manual_current_revision_check(page: Page, base_url: str):
    pack_id = "pack-sync-changed"
    _pack, request_id = _create_offline_test_note(page, base_url, pack_id)
    _approve_offline_test_note(page, pack_id, request_id, [3, 4])
    attempts: list[dict[str, object]] = []
    page.route(f"**/api/pilots/offline/packs/{pack_id}/outbox/{request_id}/sync", lambda route: (attempts.append(route.request.post_data_json), route.fulfill(status=200, content_type="application/json", body="{}")))
    page.get_by_role("button", name="Kontroller serverrevisjon manuelt", exact=True).click()
    expect(page.get_by_text("Serverrevisjon har endret seg", exact=False)).to_be_visible()
    sync_button = page.get_by_role("button", name="Lever godkjent observasjon", exact=True)
    expect(sync_button).to_be_visible()
    expect(sync_button).to_be_disabled()
    expect(page.get_by_role("button", name="Godkjenn for senere manuell synkronisering", exact=True)).to_be_disabled()
    assert attempts == []


def test_standalone_notebook_strictly_parses_utf8_and_rejects_secret_canaries(page: Page, base_url: str):
    page.goto(f"{base_url}/static/offline-notebook.html")
    outcomes = page.evaluate("""async () => {
      const api = window.BKOfflineNotebook;
      const rejects = value => { try { value(); return false; } catch { return true; } };
      const parserRejects = [
        rejects(() => api.parseStrictJSON('{"site":1,"site":2}')),
        rejects(() => api.parseStrictJSON('1e400')),
        rejects(() => api.parseStrictJSON('\uFEFF{}')),
        rejects(() => api.parseStrictJSON('['.repeat(66) + '0' + ']'.repeat(66))),
        rejects(() => api.parseStrictUTF8JSON(new Uint8Array([0x7b, 0x22, 0x78, 0x22, 0x3a, 0xff, 0x7d])))
      ];
      const valid = api.parseStrictJSON('{"site":"ø","revision":1}');
      const pack = { pack_id: 'pack-canary', payload: { schema: 'bunkerkartet-offline-pack-v1', pack_id: 'pack-canary', created_at: '2026-10-03T12:00:00Z', expires_at: '2099-10-03T12:00:00Z', selected_fields: ['external_key', 'revision'], records: [{ external_key: 'synthetic:one', revision: 1 }] }, sha256: 'a'.repeat(64), expires_at: '2099-10-03T12:00:00Z', site_id: 1, site_key: 'synthetic:one', bearer_token: 'CANARY_BEARER_9f1c' };
      const outbox = { pack_id: 'pack-canary', site_key: 'synthetic:one', site_id: 1, cached_site_revision: 1, original_request_id: 'request-canary-1234567890', payload: { observed_at: '2026-10-03', outcome: 'found', note: 'safe note', token: 'CANARY_BEARER_9f1c' } };
      let packRejected = false, outboxRejected = false;
      try { await api.storePack(pack); } catch { packRejected = true; }
      try { await api.storeOutbox(outbox); } catch { outboxRejected = true; }
      const allStored = JSON.stringify({ packs: await api.listPacks(), outbox: await api.listOutbox('pack-canary') });
      return { parserRejects, valid, packRejected, outboxRejected, noCanaryPersisted: !allStored.includes('CANARY_BEARER_9f1c'), nothingStored: allStored === '{"packs":[],"outbox":[]}' };
    }""")
    assert outcomes["parserRejects"] == [True] * 5
    assert outcomes["valid"] == {"site": "ø", "revision": 1}
    assert outcomes["packRejected"] and outcomes["outboxRejected"]
    assert outcomes["noCanaryPersisted"] and outcomes["nothingStored"]


def test_historical_and_publication_panels_obey_server_pilot_flags_and_preview_only(page: Page, base_url: str):
    _open_detail(page, base_url, pilot_status={"enabled": ["historical", "publication-preview"], "defaults_enabled": False, "production_data_used": False})
    historical = page.locator('[data-detail-section="historical-assertions"]')
    publication = page.locator('[data-detail-section="publication-preview"]')
    historical.locator("summary").first.click()
    publication.locator("summary").click()
    assertions = [{"assertion_key": "synthetic-edge", "subject_key": "krigskart:413", "predicate": "part_of", "object_key": "synthetic-complex", "source_ref": "archive:item/12", "certainty": "possible", "date_context": "Synthetic 1942 source date", "date_precision": "unknown", "valid_from": None, "valid_to": None, "uncertainty": "Source relation is ambiguous", "curator_disposition": "pending", "visually_distinct_uncertainty": True, "affects_site_trust": False, "affects_site_access": False}]
    page.route("**/api/pilots/historical/assertions**", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": assertions})))
    historical.get_by_role("button", name="Vis tidsrelasjoner", exact=True).click()
    expect(historical.locator('[data-uncertainty="uncertain"]')).to_contain_text("possible")
    expect(historical).to_contain_text("endrer ikke tillit eller adgang")

    captured: list[dict[str, object]] = []
    preview = {"schema": "bunkerkartet-publication-preview-v1", "selected_fields": ["external_key", "name"], "entries": [], "excluded": [], "approvals": [], "hosted": False, "deployed": False, "preview_sha256": "f" * 64, "package_sha256": "e" * 64}

    def publication_preview(route):
        captured.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(preview))

    page.route("**/api/pilots/publication/preview", publication_preview)
    publication.locator("select").nth(0).select_option("include")
    publication.locator("select").nth(1).select_option("approved")
    publication.locator("select").nth(2).select_option("withheld")
    publication.get_by_label("Ta med navn", exact=True).check()
    publication.get_by_label("Grunnlag for utvalgsbeslutning", exact=True).fill("Synthetic selection record")
    publication.get_by_label("Grunnlag for rettighetsbeslutning", exact=True).fill("Synthetic rights record")
    publication.get_by_label("Grunnlag for koordinatbeslutning", exact=True).fill("Synthetic coordinate withholding")
    publication.get_by_role("button", name="Bygg privat publiseringsforhåndsvisning", exact=True).click()
    expect(publication).to_contain_text("ikke vert eller publisert")
    expect(publication.get_by_role("button", name="Publiser", exact=True)).to_have_count(0)
    assert captured[0]["selected_fields"] == ["external_key", "name"]
    decision = next(iter(captured[0]["decisions"].values()))
    assert decision["coordinate_mode"] == "withheld"
    assert "latitude" not in captured[0]["selected_fields"]
    assert "longitude" not in captured[0]["selected_fields"]
