// Additive, bounded workflows mounted into the existing authenticated detail panel.
// The parent page owns auth and navigation; every async detail action is generation-bound.
(function () {
  "use strict";

  const MAX_IMPORT_BYTES = 2 * 1024 * 1024;
  const MAX_IMPORT_RECORDS = 500;
  const PILOT_STATUS_PATH = "/api/pilots/status";
  const EXCHANGE = "/api/admin/exchanges";
  let capabilitiesPromise = null;
  let notebookModulePromise = null;

  function loadNotebookModule() {
    if (window.BKOfflineNotebook?.storePack) return Promise.resolve(window.BKOfflineNotebook);
    if (!notebookModulePromise) notebookModulePromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/offline-notebook.js";
      script.onload = () => window.BKOfflineNotebook?.storePack ? resolve(window.BKOfflineNotebook) : reject(new Error("Lokal notatbokmodul startet ikke."));
      script.onerror = () => { notebookModulePromise = null; reject(new Error("Lokal notatbokmodul kunne ikke lastes.")); };
      document.head.append(script);
    });
    return notebookModulePromise;
  }

  function makeButton(label, action) {
    return researchButton(label, action);
  }

  function makeStatus(parent) {
    const node = document.createElement("p");
    node.className = "workflow-status";
    node.setAttribute("role", "status");
    parent.append(node);
    return node;
  }

  function showJSON(parent, value, className = "workflow-json") {
    let pre = parent.querySelector(`pre.${className}`);
    if (!pre) {
      pre = document.createElement("pre");
      pre.className = className;
      pre.tabIndex = 0;
      parent.append(pre);
    }
    pre.textContent = JSON.stringify(value, null, 2);
    return pre;
  }

  function downloadText(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function parseOriginalStrictJSON(raw) {
    if (typeof raw !== "string" || new TextEncoder().encode(raw).byteLength > MAX_IMPORT_BYTES) {
      throw new Error("JSON-filen overskrider grensen på 2 MiB.");
    }
    if (typeof parseStrictJson !== "function") {
      throw new Error("Appens strenge JSON-leser er ikke tilgjengelig; filen ble ikke lest.");
    }
    return parseStrictJson(raw);
  }

  function parsePositiveIds(value, limit, label) {
    const tokens = String(value).split(/[\s,]+/).map(item => item.trim()).filter(Boolean);
    if (!tokens.length || tokens.length > limit || tokens.some(item => !/^[1-9]\d*$/.test(item))) {
      throw new Error(`${label} må være ${limit === 20 ? "1–20" : "1–25"} unike positive ID-er.`);
    }
    const ids = tokens.map(Number);
    if (new Set(ids).size !== ids.length) throw new Error(`${label} inneholder dupliserte ID-er.`);
    return ids;
  }

  function currentSite(site, root, epoch, generation) {
    return Boolean(state.token && state.authEpoch === epoch && state.detailGeneration === generation && root.isConnected && site);
  }

  function invalidate(button, callback) {
    button.disabled = true;
    if (callback) callback();
  }

  async function attempt(status, current, action) {
    try {
      const result = await action();
      if (current()) status.textContent = "Klart.";
      return result;
    } catch (error) {
      if (current() && error?.name !== "AbortError" && !isStaleRequest(error)) status.textContent = error.message || "Arbeidsflyten feilet.";
      return null;
    }
  }

  async function capabilities(current) {
    if (!capabilitiesPromise) {
      capabilitiesPromise = api(PILOT_STATUS_PATH).catch(error => {
        capabilitiesPromise = null;
        throw error;
      });
    }
    const response = await capabilitiesPromise;
    if (!current()) return new Set();
    const enabled = Array.isArray(response?.enabled) ? response.enabled : [];
    return new Set(enabled.filter(item => typeof item === "string"));
  }

  function addExchanges(site, root, current) {
    const section = researchSection("Valgte utvekslinger og godkjente endringer");
    section.dataset.detailSection = "exchange-workflows";
    const intro = document.createElement("p");
    intro.textContent = "Private forhåndsvisninger bruker eksplisitt utvalg og kvitteringshash. Følsomme spørsmål, observasjoner og privat start er utelatt med mindre de velges her.";
    section.append(intro);
    const status = makeStatus(section);

    // Selected dossier: response body is re-fetched at its exact generated time and hash before download.
    const dossier = researchSection("Valgt dossier: JSON, GPX og utskriftsvisning");
    dossier.dataset.workflow = "dossier";
    const dossierIds = researchControl("Steds-ID-er, kommaseparert (maks 20)", "input");
    dossierIds.control.value = String(site.id);
    dossierIds.control.inputMode = "numeric";
    const dossierTitle = researchControl("Dossiertittel", "input");
    dossierTitle.control.value = "Selected field dossier";
    const includeStart = researchControl("Ta med privat start (følsomt; separat opt-in)");
    includeStart.control.type = "checkbox";
    const startLat = researchControl("Privat start breddegrad", "input"); startLat.control.type = "number"; startLat.control.min = "-90"; startLat.control.max = "90"; startLat.control.step = "any";
    const startLon = researchControl("Privat start lengdegrad", "input"); startLon.control.type = "number"; startLon.control.min = "-180"; startLon.control.max = "180"; startLon.control.step = "any";
    const startLabel = researchControl("Privat startnavn", "input"); startLabel.control.maxLength = 120; startLabel.control.value = "Private start";
    const includeQuestions = researchControl("Ta med private forskningsspørsmål (separat opt-in)"); includeQuestions.control.type = "checkbox";
    const questionIds = researchControl("Spørsmåls-ID-er, kommaseparert"); questionIds.control.placeholder = "Bare ved uttrykkelig opt-in";
    const includeObservations = researchControl("Ta med private observasjoner (separat opt-in)"); includeObservations.control.type = "checkbox";
    const observationIds = researchControl("Observasjons-ID-er, kommaseparert"); observationIds.control.placeholder = "Bare ved uttrykkelig opt-in";
    let dossierGeneration = 0;
    const dossierPreview = makeButton("Forhåndsvis valgt dossier", async () => {
      let body;
      try {
        const siteIds = parsePositiveIds(dossierIds.control.value, 20, "Stedsutvalget");
        body = {
          site_ids: siteIds,
          title: dossierTitle.control.value.trim(),
          include_private_start: includeStart.control.checked,
          private_start: includeStart.control.checked ? {
            latitude: Number(startLat.control.value), longitude: Number(startLon.control.value), label: startLabel.control.value.trim(),
          } : null,
          include_private_questions: includeQuestions.control.checked,
          question_ids: includeQuestions.control.checked ? questionIds.control.value.split(/[\s,]+/).map(item => item.trim()).filter(Boolean) : [],
          include_private_observations: includeObservations.control.checked,
          observation_ids: includeObservations.control.checked ? parsePositiveIds(observationIds.control.value, 25, "Observasjonsutvalget") : [],
        };
        if (!body.title || body.title.length > 200) throw new Error("Tittel må være 1–200 tegn.");
        if (includeStart.control.checked && (!startLat.control.value || !startLon.control.value || !Number.isFinite(body.private_start.latitude) || !Number.isFinite(body.private_start.longitude) || !body.private_start.label)) throw new Error("Privat start krever endelige koordinater og navn.");
        if (includeQuestions.control.checked && (!body.question_ids.length || body.question_ids.length > 25 || body.question_ids.some(item => item.length > 200))) throw new Error("Velg 1–25 gyldige spørsmåls-ID-er.");
      } catch (error) { status.textContent = error.message; return; }
      const requestGeneration = dossierGeneration;
      dossierPreview.disabled = true; dossierResult = null; dossierJSON.disabled = true; dossierHTML.disabled = true;
      const response = await attempt(status, current, async () => api(`${EXCHANGE}/dossiers/preview`, { method: "POST", body: JSON.stringify(body) }));
      if (!response || !current() || requestGeneration !== dossierGeneration) { if (current()) dossierPreview.disabled = false; return; }
      const generatedAt = response.dossier?.generated_at;
      if (!/^[0-9a-f]{64}$/.test(response.preview_hash || "") || typeof generatedAt !== "string") {
        status.textContent = "Serverkvitteringen mangler hash eller genereringstid; ingen fil kan lastes ned."; dossierPreview.disabled = false; return;
      }
      dossierResult = { body, response, generatedAt };
      showJSON(dossier, { dossier: response.dossier, geojson: response.geojson, gpx_status: response.gpx_status, source_hash: response.source_hash, preview_hash: response.preview_hash });
      const frame = document.createElement("iframe"); frame.title = "Isolert privat HTML-forhåndsvisning"; frame.setAttribute("sandbox", ""); frame.referrerPolicy = "no-referrer"; frame.srcdoc = typeof response.html === "string" ? response.html : "<!doctype html><p>HTML-visning er utilgjengelig.</p>";
      frame.className = "workflow-print-preview"; frame.style.width = "100%"; frame.style.minHeight = "16rem"; frame.style.border = "1px solid #bbb";
      dossier.querySelector("iframe")?.remove(); dossier.append(frame);
      dossierJSON.disabled = false; dossierHTML.disabled = false;
      dossierPreview.disabled = false;
      status.textContent = `Dossier forhåndsvist · SHA-256 ${response.preview_hash}. Ingen publisering er utført.`;
    });
    let dossierResult = null;
    const dossierJSON = makeButton("Last ned dossier-JSON", async () => {
      if (!dossierResult || !current()) return;
      const result = await attempt(status, current, () => api(`${EXCHANGE}/dossiers/download`, { method: "POST", body: JSON.stringify({ ...dossierResult.body, preview_hash: dossierResult.response.preview_hash, generated_at: dossierResult.generatedAt }) }));
      if (!result || !current()) return;
      if (result.preview_hash !== dossierResult.response.preview_hash) { status.textContent = "Kildegrunnlaget er endret; forhåndsvis dossieret på nytt."; dossierResult = null; dossierJSON.disabled = true; dossierHTML.disabled = true; return; }
      downloadText("bunkerkartet-dossier.json", JSON.stringify(result, null, 2), "application/json;charset=utf-8");
    }); dossierJSON.disabled = true;
    const dossierHTML = makeButton("Last ned dossier-HTML", async () => {
      if (!dossierResult || !current()) return;
      const result = await attempt(status, current, () => api(`${EXCHANGE}/dossiers/download`, { method: "POST", body: JSON.stringify({ ...dossierResult.body, preview_hash: dossierResult.response.preview_hash, generated_at: dossierResult.generatedAt }) }));
      if (!result || !current()) return;
      if (result.preview_hash !== dossierResult.response.preview_hash || typeof result.html !== "string") { status.textContent = "Kildegrunnlaget er endret; forhåndsvis dossieret på nytt."; dossierResult = null; dossierJSON.disabled = true; dossierHTML.disabled = true; return; }
      downloadText("bunkerkartet-dossier.html", result.html, "text/html;charset=utf-8");
    }); dossierHTML.disabled = true;
    [dossierIds, dossierTitle, includeStart, startLat, startLon, startLabel, includeQuestions, questionIds, includeObservations, observationIds].forEach(field => {
      const invalidateDossier = () => {
        dossierGeneration += 1;
        if (!dossierResult) return;
        dossierResult = null; dossierJSON.disabled = true; dossierHTML.disabled = true; dossierPreview.disabled = false;
        dossier.querySelector("iframe")?.remove(); dossier.querySelector("pre.workflow-json")?.remove();
        status.textContent = "Utvalget er endret. Lag en ny forhåndsvisning.";
      };
      field.control.addEventListener("input", invalidateDossier);
      field.control.addEventListener("change", invalidateDossier);
    });
    dossier.append(dossierIds.wrapper, dossierTitle.wrapper, includeStart.wrapper, startLat.wrapper, startLon.wrapper, startLabel.wrapper,
      includeQuestions.wrapper, questionIds.wrapper, includeObservations.wrapper, observationIds.wrapper, dossierPreview, dossierJSON, dossierHTML);
    section.append(dossier);

    // Strict raw-file import subset. The app's existing duplicate-key parser reads the original file bytes.
    const imports = researchSection("Importér et uttrykkelig valgt delutvalg");
    imports.dataset.workflow = "import-subset";
    const file = researchControl("Importpakke (JSON)", "input"); file.control.type = "file"; file.control.accept = ".json,application/json"; file.control.dataset.workflow = "import-package";
    const readFile = makeButton("Les valgt importfil", async () => {
      const selected = file.control.files?.[0];
      if (!selected) { status.textContent = "Velg én JSON-fil først."; return; }
      if (selected.size > MAX_IMPORT_BYTES) { status.textContent = "JSON-filen overskrider grensen på 2 MiB."; return; }
      try {
        const raw = await selected.text();
        const parsed = parseOriginalStrictJSON(raw);
        if (!current()) return;
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || !Array.isArray(parsed.records) || parsed.records.length < 1 || parsed.records.length > MAX_IMPORT_RECORDS) throw new Error("Importpakken må inneholde 1–500 poster.");
        const keys = parsed.records.map(record => record?.external_key);
        if (keys.some(key => typeof key !== "string" || !key.trim() || key.length > 300) || new Set(keys).size !== keys.length) throw new Error("Importpakken må ha unike stabile external_key-verdier.");
        sourcePackage = parsed; sourceRawBytes = new TextEncoder().encode(raw).byteLength; sourceName.textContent = `${selected.name} · ${sourceRawBytes} byte · ${keys.length} stabile nøkler`;
        selectedKeys.control.value = ""; importResult = null; importCommit.disabled = true; importConfirm.control.checked = false;
        showJSON(imports, { batch_id: parsed.batch_id, schema_version: parsed.schema_version, records: keys });
        status.textContent = "Originalfilen er lest strengt; velg bare postene som skal inngå.";
      } catch (error) { sourcePackage = null; sourceRawBytes = 0; status.textContent = error.message || "Importfilen kunne ikke leses."; }
    });
    const sourceName = document.createElement("p"); sourceName.textContent = "Ingen importfil lest.";
    const selectedKeys = researchControl("Velg stabile importnøkler", "textarea"); selectedKeys.control.rows = 3; selectedKeys.control.maxLength = 150_000; selectedKeys.control.placeholder = "Én external_key per linje";
    const batchId = researchControl("Ny batch-ID", "input"); batchId.control.maxLength = 200;
    const importReason = researchControl("Begrunnelse for delutvalg", "textarea"); importReason.control.maxLength = 1000;
    let sourcePackage = null, sourceRawBytes = 0, importResult = null, importSignature = null, importGeneration = 0;
    const importPreview = makeButton("Forhåndsvis delutvalg", async () => {
      try {
        if (!sourcePackage) throw new Error("Les en importfil før forhåndsvisning.");
        const keys = selectedKeys.control.value.split(/[\r\n,]+/).map(item => item.trim()).filter(Boolean);
        const sourceKeys = new Set(sourcePackage.records.map(item => item.external_key));
        if (!keys.length || keys.length > MAX_IMPORT_RECORDS || new Set(keys).size !== keys.length || keys.some(key => !sourceKeys.has(key))) throw new Error("Velg 1–500 unike stabile nøkler som finnes i originalpakken.");
        if (!batchId.control.value.trim() || !importReason.control.value.trim()) throw new Error("Ny batch-ID og begrunnelse kreves.");
        const body = { source_package: sourcePackage, selected_keys: keys, new_batch_id: batchId.control.value.trim(), reason: importReason.control.value.trim() };
        importSignature = JSON.stringify(body); importResult = null; importCommit.disabled = true; importConfirm.control.checked = false;
        const requestGeneration = ++importGeneration;
        const result = await attempt(status, current, () => api(`${EXCHANGE}/import-subsets/preview`, { method: "POST", body: JSON.stringify(body) }));
        if (!result || !current() || requestGeneration !== importGeneration || importSignature !== JSON.stringify(body)) return;
        if (!/^[0-9a-f]{64}$/.test(result.selection_hash || "") || !/^[0-9a-f]{64}$/.test(result.preview_hash || "") || !result.package || !result.manifest) throw new Error("Delutvalgets serverkvittering er ufullstendig.");
        importResult = { body, result };
        showJSON(imports, { source_raw_bytes: sourceRawBytes, manifest: result.manifest, derived_package: result.package, native_preview: result.native_preview, selection_hash: result.selection_hash, preview_hash: result.preview_hash });
        status.textContent = "Delutvalget er forhåndsvist. Import krever ny bekreftelse av akkurat denne effekten.";
      } catch (error) { status.textContent = error.message || "Delutvalgsforhåndsvisning feilet."; }
    });
    const importConfirm = researchControl("Bekreft import av akkurat forhåndsviste poster"); importConfirm.control.type = "checkbox";
    const importCommit = makeButton("Importer godkjent delutvalg", async () => {
      if (!importResult || !importConfirm.control.checked || !current() || importSignature !== JSON.stringify(importResult.body)) return;
      importCommit.disabled = true;
      const body = { ...importResult.body, package: importResult.result.package, selection_hash: importResult.result.selection_hash, preview_hash: importResult.result.preview_hash };
      const result = await attempt(status, current, () => api(`${EXCHANGE}/import-subsets/commit`, { method: "POST", body: JSON.stringify(body) }));
      if (result && current()) { showJSON(imports, result); status.textContent = "Delutvalg sendt til den transaksjonelle importfunksjonen."; }
      importResult = null; importConfirm.control.checked = false;
    }); importCommit.disabled = true;
    importConfirm.control.addEventListener("change", () => { importCommit.disabled = !(importResult && importConfirm.control.checked && current()); });
    [selectedKeys.control, batchId.control, importReason.control, file.control].forEach(control => {
      const invalidateImport = () => { importGeneration += 1; importSignature = null; importResult = null; importCommit.disabled = true; importConfirm.control.checked = false; };
      control.addEventListener("input", invalidateImport); control.addEventListener("change", invalidateImport);
    });
    imports.append(file.wrapper, readFile, sourceName, selectedKeys.wrapper, batchId.wrapper, importReason.wrapper, importPreview, importConfirm.wrapper, importCommit);
    section.append(imports);

    // GIS role/revision round trip. Downloaded source pack remains the immutable client-side baseline.
    const gis = researchSection("GIS-koordinatendring med revisjonskontroll"); gis.dataset.workflow = "gis-edit";
    const gisIntro = document.createElement("p"); gisIntro.textContent = "Rediger bare punktkoordinater i den nedlastede GeoJSON-funksjonen. Nøkkel, rolle, ID og kildeversjon må være uendret."; gis.append(gisIntro);
    const gisFile = researchControl("Redigert GeoJSON", "input"); gisFile.control.type = "file"; gisFile.control.accept = ".json,.geojson,application/geo+json,application/json"; gisFile.control.dataset.workflow = "gis-edits";
    const gisReason = researchControl("Begrunnelse for koordinatendring", "textarea"); gisReason.control.maxLength = 1000;
    const axis = researchControl("Bekreft WGS84-rekkefølgen lengdegrad, breddegrad"); axis.control.type = "checkbox";
    let gisSource = null, gisEdited = null, gisPrepared = null, gisSignature = null, gisGeneration = 0;
    const gisDownload = makeButton("Last ned GIS-redigeringspakke", async () => {
      if (!current()) return;
      gisPrepared = null; gisCommit.disabled = true;
      const pack = await attempt(status, current, () => api(`${EXCHANGE}/gis/edit-packs`, { method: "POST", body: JSON.stringify({ site_ids: [site.id] }) }));
      if (!pack || !current()) return;
      if (!Array.isArray(pack.features) || !/^[0-9a-f]{64}$/.test(pack.source_hash || "")) { status.textContent = "GIS-pakken mangler stabile funksjoner eller kildehash."; return; }
      gisSource = pack; gisEdited = null;
      downloadText("bunkerkartet-gis-edit.geojson", JSON.stringify(pack, null, 2), "application/geo+json;charset=utf-8");
      status.textContent = "GIS-kildepakke lastet ned. Behold ID, properties og feature-rekkefølge når koordinater redigeres.";
    });
    const gisRead = makeButton("Les redigert GIS-fil", async () => {
      const selected = gisFile.control.files?.[0];
      if (!selected || selected.size > MAX_IMPORT_BYTES) { status.textContent = selected ? "GIS-filen overskrider 2 MiB." : "Velg den redigerte GeoJSON-filen."; return; }
      try {
        const raw = await selected.text(); const parsed = parseOriginalStrictJSON(raw);
        if (!current()) return;
        if (!gisSource || parsed?.type !== "FeatureCollection" || !Array.isArray(parsed.features) || parsed.features.length !== gisSource.features.length) throw new Error("GeoJSON-utvalget avviker fra den nedlastede kildepakken.");
        gisEdited = parsed; gisPrepared = null; gisCommit.disabled = true;
        showJSON(gis, { source_hash: gisSource.source_hash, site_ids: parsed.site_ids, features: parsed.features });
        status.textContent = "Redigert fil lest strengt. Serveren kontrollerer hvert ID-, rolle- og revisjonsfelt.";
      } catch (error) { gisEdited = null; gisPrepared = null; gisCommit.disabled = true; status.textContent = error.message || "GeoJSON-filen kunne ikke leses."; }
    });
    const gisPreview = makeButton("Forhåndsvis GIS-endringer", async () => {
      if (!gisSource || !gisEdited || !axis.control.checked || !gisReason.control.value.trim()) { status.textContent = "Kildepakke, redigert fil, WGS84-bekreftelse og begrunnelse kreves."; return; }
      const body = { source_pack: gisSource, edited_features: gisEdited.features, reason: gisReason.control.value.trim(), axis_order_confirmation: "longitude_latitude" };
      gisSignature = JSON.stringify(body); gisPrepared = null; gisCommit.disabled = true;
      const requestGeneration = ++gisGeneration;
      const result = await attempt(status, current, () => api(`${EXCHANGE}/gis/edit-previews`, { method: "POST", body: JSON.stringify(body) }));
      if (!result || !current() || requestGeneration !== gisGeneration || gisSignature !== JSON.stringify(body)) return;
      if (!/^[0-9a-f]{64}$/.test(result.preview_hash || "") || !Array.isArray(result.edits)) { status.textContent = "GIS-kvitteringen mangler redigeringsliste eller hash."; return; }
      gisPrepared = { body, result };
      showJSON(gis, result);
      gisCommit.disabled = !gisConfirm.control.checked;
      status.textContent = "Revisjonsbundet GIS-forhåndsvisning klar. Ingen koordinater er lagret ennå.";
    });
    const gisConfirm = researchControl("Jeg har gjennomgått akkurat disse koordinatendringene"); gisConfirm.control.type = "checkbox";
    const gisCommit = makeButton("Lagre forhåndsviste koordinatendringer", async () => {
      if (!gisPrepared || !gisConfirm.control.checked || !current() || gisSignature !== JSON.stringify(gisPrepared.body)) return;
      gisCommit.disabled = true;
      const body = { ...gisPrepared.body, preview_hash: gisPrepared.result.preview_hash };
      const result = await attempt(status, current, () => api(`${EXCHANGE}/gis/edit-commits`, { method: "POST", body: JSON.stringify(body) }));
      if (result && current()) { showJSON(gis, result); status.textContent = `Serverens revisjons- og plasseringsvakt behandlet endringen (${result.updated ?? result.result?.updated ?? "kvittering mottatt"}).`; }
      gisPrepared = null; gisConfirm.control.checked = false;
    }); gisCommit.disabled = true;
    gisConfirm.control.addEventListener("change", () => { gisCommit.disabled = !(gisPrepared && gisConfirm.control.checked && current()); });
    [gisFile.control, gisReason.control, axis.control].forEach(control => {
      const invalidateGIS = () => { gisGeneration += 1; gisSignature = null; gisPrepared = null; gisCommit.disabled = true; gisConfirm.control.checked = false; };
      control.addEventListener("input", invalidateGIS); control.addEventListener("change", invalidateGIS);
    });
    gis.append(gisFile.wrapper, gisDownload, gisRead, gisReason.wrapper, axis.wrapper, gisPreview, gisConfirm.wrapper, gisCommit);
    section.append(gis);
    root.append(section);
  }

  function addVisit(site, root, current) {
    const section = researchSection("Privat feltbesøk og observasjonstilknytning"); section.dataset.detailSection = "visit-workflow";
    const intro = document.createElement("p"); intro.textContent = "Besøket bruker en revisjonsbundet serverkopi av rute eller offentlig vurdert tilnærming. Observasjoner opprettes separat og kan bare knyttes til et registrert besøk når det er markert som gjennomført.";
    const status = makeStatus(section);
    const eligible = site.route_eligible === true;
    const gate = document.createElement("p"); gate.textContent = eligible ? "Serveren har merket tilnærmingen som egnet for besøksplanlegging." : "Planlegging er deaktivert for dette stedet fordi ingen offentlig vurdert tilnærming er bekreftet.";
    const date = researchControl("Planlagt dato", "input"); date.control.type = "date"; date.control.value = new Date().toISOString().slice(0, 10);
    const request = researchControl("Opprinnelig forespørsels-ID", "input"); request.control.maxLength = 200; request.control.value = crypto.randomUUID();
    const plan = makeButton("Planlegg privat feltbesøk", async () => {
      if (!eligible || !current()) return;
      const body = { request_id: request.control.value.trim(), planned_date: date.control.value, manual_site_ids: [site.id], selected_questions: [] };
      if (!body.request_id || !body.planned_date) { status.textContent = "Dato og forespørsels-ID kreves."; return; }
      plan.disabled = true;
      const result = await attempt(status, current, () => api("/api/visits", { method: "POST", body: JSON.stringify(body) }));
      if (!result || !current()) { plan.disabled = false; return; }
      activeVisit = result; renderVisit(result);
    }); plan.disabled = !eligible;
    const visitPane = document.createElement("div"); section.append(intro, gate, date.wrapper, request.wrapper, plan, visitPane, status); root.append(section);
    let activeVisit = null;
    function renderVisit(visit) {
      if (!current()) return;
      visitPane.replaceChildren();
      showJSON(visitPane, visit, "workflow-visit-json");
      if (visit.state === "planned") {
        const occurred = researchControl("Faktisk besøksdato", "input"); occurred.control.type = "date"; occurred.control.value = new Date().toISOString().slice(0, 10);
        const outcome = researchControl("Besøksutfall"); ["completed", "partial", "aborted"].forEach(value => { const option = document.createElement("option"); option.value = value; option.textContent = value; outcome.control.append(option); });
        visitPane.append(occurred.wrapper, outcome.wrapper, makeButton("Merk som gjennomført", async () => {
          const result = await attempt(status, current, () => api(`/api/visits/${visit.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: visit.revision, state: "occurred", occurred_date: occurred.control.value, visit_outcome: outcome.control.value, outcomes: [] }) }));
          if (result && current()) { activeVisit = result; renderVisit(result); }
        }));
      }
      if (visit.state === "occurred") {
        const obs = researchControl("Eksisterende observasjons-ID", "input"); obs.control.type = "number"; obs.control.min = "1"; obs.control.step = "1";
        const question = researchControl("Tilknyttet spørsmål-ID (valgfritt)", "input"); question.control.type = "number"; question.control.min = "1"; question.control.step = "1";
        const attachmentRequest = researchControl("Vedleggsforespørsels-ID", "input"); attachmentRequest.control.maxLength = 200; attachmentRequest.control.value = crypto.randomUUID();
        visitPane.append(obs.wrapper, question.wrapper, attachmentRequest.wrapper, makeButton("Knytt eksisterende observasjon til besøket", async () => {
          const observationId = Number(obs.control.value);
          if (!Number.isSafeInteger(observationId) || observationId <= 0) { status.textContent = "Oppgi en gyldig observasjons-ID."; return; }
          const body = { request_id: attachmentRequest.control.value.trim(), site_id: site.id, observation_id: observationId };
          if (question.control.value) body.question_id = Number(question.control.value);
          const result = await attempt(status, current, () => api(`/api/visits/${visit.id}/observations`, { method: "POST", body: JSON.stringify(body) }));
          if (result && current()) { const attached = document.createElement("pre"); attached.textContent = JSON.stringify(result, null, 2); visitPane.append(attached); }
        }), makeButton("Lukk gjennomført besøk", async () => {
          const result = await attempt(status, current, () => api(`/api/visits/${visit.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: visit.revision, state: "closed" }) }));
          if (result && current()) { activeVisit = result; renderVisit(result); }
        }));
      }
      if (visit.state === "closed") { const closed = document.createElement("p"); closed.textContent = "Besøket er lukket. Historikk og vedleggsidentitet beholdes på serveren."; visitPane.append(closed); }
      void activeVisit;
    }
  }

  function addHistorical(site, root, current, enabled) {
    const section = researchSection("Kildebelagte historiske forhold og enkel tidsliste"); section.dataset.detailSection = "historical-assertions";
    const status = makeStatus(section);
    const generic = Array.isArray(site.related_site_keys) ? site.related_site_keys : [];
    const legacy = document.createElement("p"); legacy.textContent = generic.length ? `Eldre generiske relasjoner (ikke typet): ${generic.join(", ")}` : "Eldre generiske relasjoner beholdes som generiske.";
    const gate = document.createElement("p"); gate.textContent = enabled ? "Historisk pilot er aktivert av servereier. Påstander er kildetilknyttede vurderinger, ikke fastslåtte forhold." : "Historisk pilot er slått av på serveren; kontrollene forblir skrivebeskyttet.";
    const filterDate = researchControl("Vis forhold som gjelder denne datoen (tomt viser alle)", "input"); filterDate.control.type = "date";
    const list = document.createElement("div");
    const renderList = makeButton("Vis tidsrelasjoner", async () => {
      if (!enabled) { status.textContent = "Historisk pilot er deaktivert på serveren."; return; }
      const query = filterDate.control.value ? `?on_date=${encodeURIComponent(filterDate.control.value)}` : "";
      const response = await attempt(status, current, () => api(`/api/pilots/historical/assertions${query}`));
      if (!response || !current()) return;
      list.replaceChildren();
      const items = Array.isArray(response.items) ? response.items : [];
      if (!items.length) { const empty = document.createElement("p"); empty.textContent = "Ingen typede kildebelagte forhold i denne tidsvisningen."; list.append(empty); }
      for (const item of items.slice(0, 500)) {
        const article = document.createElement("article"); article.dataset.uncertainty = item.visually_distinct_uncertainty ? "uncertain" : "recorded";
        article.className = item.visually_distinct_uncertainty ? "workflow-uncertain" : "workflow-recorded";
        const heading = document.createElement("h4"); heading.textContent = `${item.subject_key} · ${item.predicate} · ${item.object_key}`;
        const summary = document.createElement("p"); summary.textContent = `${item.certainty} · ${item.date_precision} · ${item.date_match || "not_filtered"} · vurdering ${item.curator_disposition}`;
        const evidence = document.createElement("p"); evidence.textContent = `Kilde: ${item.source_label || item.source_ref} · ${item.date_context} · usikkerhet: ${item.uncertainty}`;
        const trust = document.createElement("p"); trust.textContent = "Denne historiske relasjonen endrer ikke tillit eller adgang.";
        article.append(heading, summary, evidence, trust); list.append(article);
      }
    }); renderList.disabled = !enabled;
    const add = researchSection("Legg til kildebelagt, uavklart forhold");
    const predicate = researchControl("Forholdstype", "select"); [["part_of", "Del av"], ["historical_predecessor", "Historisk forgjenger"], ["possible_same_as", "Mulig samme identitet"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; predicate.control.append(option); });
    const objectKey = researchControl("Motpartens stabile nøkkel"); objectKey.control.maxLength = 300;
    const assertionKey = researchControl("Påstandsnøkkel"); assertionKey.control.maxLength = 300; assertionKey.control.value = crypto.randomUUID();
    const source = researchControl("Kildehenvisning (ingen automatisk kildehenting)"); source.control.maxLength = 2000;
    const sourceLabel = researchControl("Kildetittel (valgfritt)"); sourceLabel.control.maxLength = 500;
    const certainty = researchControl("Kildeusikkerhet", "select"); [["possible", "Mulig"], ["uncertain", "Usikker"], ["probable", "Sannsynlig"], ["contradicted", "Motstridt"], ["certain", "Sikker (kildepåstand)"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; certainty.control.append(option); });
    const precision = researchControl("Tidsangivelsens presisjon", "select"); [["unknown", "Ukjent"], ["day", "Dag"], ["month", "Måned"], ["year", "År"], ["range", "Intervall"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; precision.control.append(option); });
    const validFrom = researchControl("Gyldig fra", "input"); validFrom.control.type = "date";
    const validTo = researchControl("Gyldig til", "input"); validTo.control.type = "date";
    const dateContext = researchControl("Kildens dato-/tidskontekst", "textarea"); dateContext.control.maxLength = 1000;
    const uncertainty = researchControl("Hva er uavklart?", "textarea"); uncertainty.control.maxLength = 2000;
    const disposition = researchControl("Kuratorens foreløpige vurdering", "select"); [["pending", "Til vurdering"], ["deferred", "Utsatt"], ["accepted", "Beholdt som hypotese"], ["rejected", "Forkastet hypotese"]].forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; disposition.control.append(option); });
    const create = makeButton("Registrer kildebelagt historisk hypotese", async () => {
      if (!enabled) { status.textContent = "Historisk pilot er deaktivert på serveren."; return; }
      const body = { assertion_key: assertionKey.control.value.trim(), subject_key: site.external_key, predicate: predicate.control.value, object_key: objectKey.control.value.trim(), source_ref: source.control.value.trim(), source_label: sourceLabel.control.value.trim() || null, certainty: certainty.control.value, date_context: dateContext.control.value.trim(), valid_from: precision.control.value === "unknown" ? null : validFrom.control.value || null, valid_to: precision.control.value === "unknown" ? null : validTo.control.value || null, date_precision: precision.control.value, curator_disposition: disposition.control.value, uncertainty: uncertainty.control.value.trim() };
      if (Object.values(body).some(value => value === "")) { status.textContent = "Stabil nøkkel, kilde, dato-/tidskontekst og usikkerhetsbeskrivelse kreves."; return; }
      const result = await attempt(status, current, () => api("/api/pilots/historical/assertions", { method: "POST", body: JSON.stringify(body) }));
      if (result && current()) { showJSON(list, result); status.textContent = "Hypotesen er registrert separat fra stedsstatus og adgang."; }
    }); create.disabled = !enabled;
    add.append(predicate.wrapper, objectKey.wrapper, assertionKey.wrapper, source.wrapper, sourceLabel.wrapper, certainty.wrapper, precision.wrapper, validFrom.wrapper, validTo.wrapper, dateContext.wrapper, uncertainty.wrapper, disposition.wrapper, create);
    section.append(legacy, gate, filterDate.wrapper, renderList, list, add, status); root.append(section);
    // A first render is intentionally user initiated; no background historical read occurs.
  }

  function addPublicationPreview(site, root, current, enabled) {
    const section = researchSection("Privat publiseringsutkast (uten vert eller publisering)"); section.dataset.detailSection = "publication-preview";
    const notice = document.createElement("p"); notice.textContent = enabled ? "Kun privat forhåndsvisning og JSON-nedlasting. Ingen konto, vert, opplasting eller publisering kobles til." : "Publiseringsforhåndsvisning er deaktivert på serveren.";
    const selection = researchControl("Valg for privat forhåndsvisning", "select"); addOptions(selection.control, [["", "Velg uttrykkelig"], ["exclude", "Utelat"], ["include", "Ta med i privat utkast"]]);
    const rights = researchControl("Rettighetsbeslutning", "select"); addOptions(rights.control, [["", "Velg uttrykkelig"], ["pending", "Uavklart"], ["denied", "Ikke godkjent"], ["approved", "Godkjent for dette private utkastet"]]);
    const coordinates = researchControl("Koordinatbeslutning", "select"); addOptions(coordinates.control, [["", "Velg uttrykkelig"], ["withheld", "Hold tilbake"], ["generalized", "Bruk generalisert punkt"], ["exact", "Bruk eksakte koordinater"]]);
    const selectionRef = researchControl("Grunnlag for utvalgsbeslutning"); selectionRef.control.maxLength = 300;
    const rightsRef = researchControl("Grunnlag for rettighetsbeslutning"); rightsRef.control.maxLength = 300;
    const coordinateRef = researchControl("Grunnlag for koordinatbeslutning"); coordinateRef.control.maxLength = 300;
    const nameField = researchControl("Ta med navn"); nameField.control.type = "checkbox";
    const kindField = researchControl("Ta med objekttype"); kindField.control.type = "checkbox";
    const precisionField = researchControl("Ta med presisjonsbeskrivelse"); precisionField.control.type = "checkbox";
    const uncertaintyField = researchControl("Ta med usikkerhet"); uncertaintyField.control.type = "checkbox";
    const coordinateFields = researchControl("Ta med breddegrad/lengdegrad"); coordinateFields.control.type = "checkbox";
    const generalizedLat = researchControl("Generalisert breddegrad", "input"); generalizedLat.control.type = "number"; generalizedLat.control.min = "-90"; generalizedLat.control.max = "90"; generalizedLat.control.step = "any";
    const generalizedLon = researchControl("Generalisert lengdegrad", "input"); generalizedLon.control.type = "number"; generalizedLon.control.min = "-180"; generalizedLon.control.max = "180"; generalizedLon.control.step = "any";
    const status = makeStatus(section); const output = document.createElement("div"); let preview = null;
    const build = makeButton("Bygg privat publiseringsforhåndsvisning", async () => {
      if (!enabled) { status.textContent = "Publiseringspilot er deaktivert på serveren."; return; }
      if (!selection.control.value || !rights.control.value || !coordinates.control.value || !selectionRef.control.value.trim() || !rightsRef.control.value.trim() || !coordinateRef.control.value.trim()) { status.textContent = "Utvalg, rettighet, koordinatvalg og hvert beslutningsgrunnlag kreves."; return; }
      const fields = ["external_key"];
      if (nameField.control.checked) fields.push("name"); if (kindField.control.checked) fields.push("site_kind");
      if (precisionField.control.checked) fields.push("precision"); if (uncertaintyField.control.checked) fields.push("uncertainty_m");
      if (coordinateFields.control.checked) fields.push("latitude", "longitude");
      if (coordinates.control.value === "withheld" && coordinateFields.control.checked) { status.textContent = "Koordinater kan ikke velges når koordinatbeslutningen er tilbakeholdelse."; return; }
      const decision = { selection: selection.control.value, selection_ref: selectionRef.control.value.trim(), rights: rights.control.value, rights_ref: rightsRef.control.value.trim(), coordinate_mode: coordinates.control.value, coordinate_ref: coordinateRef.control.value.trim() };
      if (coordinates.control.value === "generalized") {
        if (!Number.isFinite(Number(generalizedLat.control.value)) || !Number.isFinite(Number(generalizedLon.control.value))) { status.textContent = "Generalisert koordinat krever begge akser."; return; }
        decision.generalized_latitude = Number(generalizedLat.control.value); decision.generalized_longitude = Number(generalizedLon.control.value);
      }
      if (coordinates.control.value === "exact" && !coordinateFields.control.checked) { status.textContent = "Eksakt koordinatbeslutning krever at begge koordinatfeltene er valgt."; return; }
      const body = { site_keys: [site.external_key], selected_fields: fields, decisions: { [site.external_key]: decision } };
      const result = await attempt(status, current, () => api("/api/pilots/publication/preview", { method: "POST", body: JSON.stringify(body) }));
      if (!result || !current()) return;
      preview = result; showJSON(output, preview);
      output.querySelector("button[data-download-publication]")?.remove();
      const save = makeButton("Last ned privat forhåndsvisning som JSON", () => downloadText("bunkerkartet-private-publication-preview.json", JSON.stringify(preview, null, 2), "application/json;charset=utf-8"));
      save.dataset.downloadPublication = "true"; output.append(save);
      status.textContent = `Privat forhåndsvisning ${result.preview_sha256 || ""}. Pakken er ikke vert eller publisert.`;
    });
    build.disabled = !enabled;
    section.append(notice, selection.wrapper, selectionRef.wrapper, rights.wrapper, rightsRef.wrapper, coordinates.wrapper, coordinateRef.wrapper,
      nameField.wrapper, kindField.wrapper, precisionField.wrapper, uncertaintyField.wrapper, coordinateFields.wrapper, generalizedLat.wrapper, generalizedLon.wrapper, build, output, status);
    root.append(section);
  }

  function addOptions(select, options) { options.forEach(([value, label]) => { const option = document.createElement("option"); option.value = value; option.textContent = label; select.append(option); }); }

  async function addOffline(site, root, current, enabled) {
    const section = researchSection("Valgt frakoblet notatbok (pilot)"); section.dataset.detailSection = "offline-notebook";
    const status = makeStatus(section);
    const explanation = document.createElement("p"); explanation.textContent = enabled
      ? "Pilot på serveren er aktiv. Bare dette eksplisitte stedsutvalget kan lagres. Ingen automatisk synkronisering eller godkjenning skjer. Token lagres aldri; notater på enheten kan leses av andre med tilgang til enheten."
      : "Frakoblet pilot er deaktivert på serveren. En tom offentlig notatbok-shell er tilgjengelig uten privat data.";
    const shell = document.createElement("a"); shell.href = "/static/offline-notebook.html"; shell.textContent = "Åpne offentlig, tom notatbok-shell"; shell.rel = "noopener";
    const shellDownload = makeButton("Last ned tom, selvstendig notatbok-shell", async () => {
      try {
        const response = await fetch("/static/offline-notebook.js", { cache: "no-store" });
        if (!response.ok) throw new Error("Notatbokskallet kunne ikke hentes.");
        let source = await response.text(); source = source.replace(/<\/script/gi, "<\\/script");
        const html = `<!doctype html><html lang="nb"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Frakoblet feltnotatbok</title><style>body{font:16px system-ui;max-width:54rem;margin:2rem auto;padding:0 1rem;line-height:1.5}label{display:block;margin:.7rem 0}button,input,select,textarea{font:inherit;padding:.45rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{padding:.8rem;background:#fff4d6}</style><body data-offline-notebook><h1>Frakoblet feltnotatbok</h1><p class="notice">Tom offentlig shell. Ingen private data, token, kartfliser eller arbeidsområde følger med.</p><div id="offline-notebook"></div><script>${source}</script></body></html>`;
        downloadText("bunkerkartet-empty-offline-notebook.html", html, "text/html;charset=utf-8");
        status.textContent = "Tom shell lastet ned uten arbeidsområde, token eller kartfliser.";
      } catch (error) { status.textContent = error.message; }
    });
    const consent = researchControl("Jeg godkjenner lagring av akkurat dette utvalget på denne enheten til utløp eller sletting"); consent.control.type = "checkbox";
    const includeCoordinates = researchControl("Ta også med stedets eksakte koordinater (følsomme)"); includeCoordinates.control.type = "checkbox";
    const save = makeButton("Lagre valgt pakke på denne enheten", async () => {
      if (!enabled || !consent.control.checked || !current()) { status.textContent = "Serverpilot og eksplisitt enhetsgodkjenning kreves."; return; }
      if (!navigator.storage?.persist || !globalThis.indexedDB) { status.textContent = "Denne nettleseren tilbyr ikke IndexedDB og eksplisitt vedvarende lagring."; return; }
      save.disabled = true;
      try {
        const persisted = await navigator.storage.persist();
        if (!persisted) throw new Error("Nettleseren ga ikke eksplisitt tillatelse til vedvarende enhetslagring.");
        const selectedFields = ["external_key", "name", "site_kind", "revision"];
        if (includeCoordinates.control.checked) selectedFields.push("latitude", "longitude");
        const response = await api("/api/pilots/offline/packs", { method: "POST", body: JSON.stringify({ site_ids: [site.id], selected_fields: selectedFields }) });
        if (!current()) return;
        if (typeof response.payload_json !== "string" || new TextEncoder().encode(response.payload_json).byteLength > 256_000 || typeof response.expires_at !== "string") throw new Error("Serverens offlinepakke mangler gyldig avgrensning eller utløp.");
        const payload = parseOriginalStrictJSON(response.payload_json);
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(response.payload_json));
        const localHash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
        if (localHash !== response.sha256 || payload.pack_id !== response.pack_id || payload.stale === true) throw new Error("Pakkehash eller identitet stemmer ikke; ingenting ble lagret på enheten.");
        const notebook = await loadNotebookModule();
        if (!current()) return;
        await notebook.storePack({ pack_id: response.pack_id, payload, sha256: response.sha256, expires_at: response.expires_at, site_id: site.id, site_key: site.external_key });
        status.textContent = `Utvalg lagret på denne enheten til ${response.expires_at}. Token, fliser og andre steder er ikke lagret.`;
        const open = document.createElement("a"); open.href = `/static/offline-notebook.html?pack=${encodeURIComponent(response.pack_id)}`; open.textContent = "Åpne denne lokale pakken"; open.rel = "noopener"; section.append(open);
      } catch (error) { if (current()) status.textContent = error.message || "Lokal lagring feilet."; }
      finally { if (current()) save.disabled = !enabled || !consent.control.checked; }
    }); save.disabled = true;
    consent.control.addEventListener("change", () => { save.disabled = !enabled || !consent.control.checked; });
    const generationNotice = document.createElement("p"); generationNotice.textContent = "Låsing sletter arbeidsområdets skjermminne. Enhetslagring er adskilt og forblir til utløp eller eksplisitt sletting.";
    section.append(explanation, shell, shellDownload, consent.wrapper, includeCoordinates.wrapper, save, generationNotice, status); root.append(section);
  }

  document.addEventListener("bunkerkartet:detail", event => {
    const site = event.detail?.site, root = event.detail?.root;
    if (!site || !root || root.dataset.workflowSectionsAdded === "true") return;
    root.dataset.workflowSectionsAdded = "true";
    const epoch = state.authEpoch, generation = state.detailGeneration;
    const current = () => currentSite(site, root, epoch, generation);
    addExchanges(site, root, current);
    addVisit(site, root, current);
    const pilotStatus = makeStatus(root);
    pilotStatus.textContent = "Henter serverens pilotflagg…";
    capabilities(current).then(enabled => {
      if (!current()) return;
      pilotStatus.remove();
      addHistorical(site, root, current, enabled.has("historical"));
      addPublicationPreview(site, root, current, enabled.has("publication-preview"));
      void addOffline(site, root, current, enabled.has("offline"));
    }).catch(error => {
      if (!current()) return;
      pilotStatus.textContent = `Pilotstatus utilgjengelig; pilotkontroller er slått av (${error.message}).`;
      addHistorical(site, root, current, false);
      addPublicationPreview(site, root, current, false);
      void addOffline(site, root, current, false);
    });
  });
}());
