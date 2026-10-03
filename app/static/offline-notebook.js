// Explicit device-local field notes. Credentials exist only in this page's memory.
(function () {
  "use strict";

  const DB_NAME = "bunkerkartet-offline-notebook-v1";
  const DB_VERSION = 1;
  const PACK_STORE = "packs";
  const OUTBOX_STORE = "outbox";
  const MAX_PACKS = 5;
  const MAX_OUTBOX = 50;
  const MAX_PACK_BYTES = 256_000;
  const MAX_NOTE_BYTES = 16_000;
  const MAX_JSON_DEPTH = 64;
  const PACK_FIELDS = new Set(["external_key", "name", "site_kind", "latitude", "longitude", "precision", "uncertainty_m", "revision"]);
  const PACK_ENVELOPE_FIELDS = new Set(["pack_id", "payload", "sha256", "expires_at", "site_id", "site_key"]);
  const OUTBOX_FIELDS = new Set(["pack_id", "site_key", "site_id", "cached_site_revision", "original_request_id", "payload"]);
  let dbPromise = null;

  function parseStrictJSON(source) {
    if (typeof source !== "string") throw new Error("JSON-kilden må være tekst.");
    const bytes = new TextEncoder().encode(source);
    if (bytes.byteLength > MAX_PACK_BYTES || new TextDecoder("utf-8", { fatal: true }).decode(bytes) !== source) throw new Error("JSON-kilden er ugyldig UTF-8 eller overskrider 256 KiB.");
    if (source.charCodeAt(0) === 0xFEFF) throw new Error("JSON med UTF-8 BOM godtas ikke.");
    let index = 0;
    const fail = () => { throw new Error(`Ugyldig eller for dyp JSON ved posisjon ${index}.`); };
    const whitespace = () => { while (source[index] === " " || source[index] === "\t" || source[index] === "\r" || source[index] === "\n") index += 1; };
    function stringToken() {
      const start = index;
      if (source[index] !== '"') fail();
      index += 1;
      while (index < source.length) {
        const code = source.charCodeAt(index);
        if (code < 0x20) fail();
        if (source[index] === '"') {
          index += 1;
          try { return JSON.parse(source.slice(start, index)); } catch { fail(); }
        }
        if (source[index] === "\\") {
          index += 1;
          if (index >= source.length) fail();
          const escape = source[index];
          if (escape === "u") {
            if (!/^[0-9a-fA-F]{4}$/.test(source.slice(index + 1, index + 5))) fail();
            index += 5;
            continue;
          }
          if (!'"\\/bfnrt'.includes(escape)) fail();
        }
        index += 1;
      }
      fail();
    }
    function value(depth) {
      whitespace();
      const token = source[index];
      if (token === '"') { stringToken(); return; }
      if (token === "{" || token === "[") {
        if (depth >= MAX_JSON_DEPTH) fail();
        const close = token === "{" ? "}" : "]";
        index += 1; whitespace();
        if (source[index] === close) { index += 1; return; }
        if (token === "{") {
          const keys = new Set();
          while (true) {
            whitespace();
            const key = stringToken();
            if (keys.has(key)) fail();
            keys.add(key); whitespace();
            if (source[index] !== ":") fail();
            index += 1; value(depth + 1); whitespace();
            if (source[index] === close) { index += 1; return; }
            if (source[index] !== ",") fail();
            index += 1;
          }
        }
        while (true) {
          value(depth + 1); whitespace();
          if (source[index] === close) { index += 1; return; }
          if (source[index] !== ",") fail();
          index += 1;
        }
      }
      for (const literal of ["true", "false", "null"]) {
        if (source.startsWith(literal, index)) { index += literal.length; return; }
      }
      const number = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(index));
      if (number) {
        if (!Number.isFinite(Number(number[0]))) fail();
        index += number[0].length; return;
      }
      fail();
    }
    value(0); whitespace();
    if (index !== source.length) fail();
    try { return JSON.parse(source); } catch { fail(); }
  }

  function parseStrictUTF8JSON(input) {
    let bytes;
    if (input instanceof ArrayBuffer) bytes = new Uint8Array(input);
    else if (ArrayBuffer.isView(input)) bytes = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
    else throw new Error("JSON-kilden må være UTF-8-bytes.");
    if (bytes.byteLength > MAX_PACK_BYTES) throw new Error("JSON-kilden overskrider 256 KiB.");
    let source;
    try { source = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes); }
    catch { throw new Error("JSON-kilden inneholder ugyldig UTF-8."); }
    return parseStrictJSON(source);
  }

  function onlyKeys(value, allowed, required = []) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const keys = Object.keys(value);
    return keys.every(key => allowed.has(key)) && required.every(key => Object.hasOwn(value, key));
  }

  function validTimestamp(value) {
    return typeof value === "string" && value.length <= 40 && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  }

  function validatePackPayload(payload) {
    const payloadFields = new Set(["schema", "pack_id", "created_at", "expires_at", "selected_fields", "records"]);
    if (!onlyKeys(payload, payloadFields, [...payloadFields]) || payload.schema !== "bunkerkartet-offline-pack-v1" ||
        typeof payload.pack_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(payload.pack_id) ||
        !validTimestamp(payload.created_at) || !validTimestamp(payload.expires_at) ||
        Date.parse(payload.expires_at) <= Date.parse(payload.created_at) ||
        !Array.isArray(payload.selected_fields) || payload.selected_fields.length < 2 || payload.selected_fields.length > PACK_FIELDS.size ||
        new Set(payload.selected_fields).size !== payload.selected_fields.length ||
        payload.selected_fields.some(field => typeof field !== "string" || !PACK_FIELDS.has(field)) ||
        !payload.selected_fields.includes("external_key") || !payload.selected_fields.includes("revision") ||
        !Array.isArray(payload.records) || payload.records.length < 1 || payload.records.length > 50) {
      throw new Error("Offlinepakken har ugyldig skjema eller utvalg.");
    }
    const seen = new Set();
    for (const record of payload.records) {
      if (!onlyKeys(record, new Set(payload.selected_fields), ["external_key", "revision"]) ||
          typeof record.external_key !== "string" || !record.external_key || record.external_key.length > 300 || seen.has(record.external_key) ||
          !Number.isSafeInteger(record.revision) || record.revision < 0) throw new Error("Offlinepakken har ugyldige eller dupliserte stedsoppføringer.");
      seen.add(record.external_key);
      for (const field of ["name", "site_kind", "precision"]) if (Object.hasOwn(record, field) && (typeof record[field] !== "string" || record[field].length > 2_000)) throw new Error("Offlinepakken har et ugyldig tekstfelt.");
      if (Object.hasOwn(record, "latitude") && (typeof record.latitude !== "number" || !Number.isFinite(record.latitude) || record.latitude < -90 || record.latitude > 90)) throw new Error("Offlinepakken har ugyldig breddegrad.");
      if (Object.hasOwn(record, "longitude") && (typeof record.longitude !== "number" || !Number.isFinite(record.longitude) || record.longitude < -180 || record.longitude > 180)) throw new Error("Offlinepakken har ugyldig lengdegrad.");
      if (Object.hasOwn(record, "uncertainty_m") && (typeof record.uncertainty_m !== "number" || !Number.isFinite(record.uncertainty_m) || record.uncertainty_m < 0 || record.uncertainty_m > 100_000_000)) throw new Error("Offlinepakken har ugyldig koordinatusikkerhet.");
    }
    return payload;
  }

  function rejectSecretKeys(value, depth = 0) {
    if (depth > MAX_JSON_DEPTH) throw new Error("Lokal post er for dypt nestet.");
    if (!value || typeof value !== "object") return;
    for (const [key, nested] of Object.entries(value)) {
      if (/^(authorization|bearer_?token|token|access_?token|refresh_?token|password|secret|credential|photo|photos|attachment|image|route_?start|tile_?cache|tiles|raw_?import_?payload)$/i.test(key)) throw new Error("Posten inneholder et felt som ikke kan lagres lokalt.");
      rejectSecretKeys(nested, depth + 1);
    }
  }

  function openDB() {
    if (!globalThis.indexedDB) return Promise.reject(new Error("IndexedDB er ikke tilgjengelig."));
    if (!dbPromise) dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(PACK_STORE)) db.createObjectStore(PACK_STORE, { keyPath: "pack_id" });
        if (!db.objectStoreNames.contains(OUTBOX_STORE)) {
          const store = db.createObjectStore(OUTBOX_STORE, { keyPath: "original_request_id" });
          store.createIndex("pack_id", "pack_id", { unique: false });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { dbPromise = null; reject(request.error || new Error("Lokal notatbok kunne ikke åpnes.")); };
      request.onblocked = () => reject(new Error("En annen notatbokfane blokkerer lokal lagring."));
    });
    return dbPromise;
  }

  function transact(storeName, mode, operation) {
    return openDB().then(db => new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const store = tx.objectStore(storeName);
      let result;
      try { result = operation(store); } catch (error) { reject(error); return; }
      tx.oncomplete = () => resolve(result?.result);
      tx.onerror = () => reject(tx.error || new Error("Lokal notatbokskriving feilet."));
      tx.onabort = () => reject(tx.error || new Error("Lokal notatbokskriving ble avbrutt."));
    }));
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("Lokal notatbokforespørsel feilet."));
    });
  }

  async function listPacks() {
    const db = await openDB();
    return requestResult(db.transaction(PACK_STORE, "readonly").objectStore(PACK_STORE).getAll());
  }

  async function listOutbox(packId) {
    const db = await openDB();
    const index = db.transaction(OUTBOX_STORE, "readonly").objectStore(OUTBOX_STORE).index("pack_id");
    return requestResult(index.getAll(IDBKeyRange.only(packId)));
  }

  async function storePack(record) {
    if (!onlyKeys(record, PACK_ENVELOPE_FIELDS, [...PACK_ENVELOPE_FIELDS])) throw new Error("Lokal pakke inneholder ukjente felt.");
    validatePackPayload(record.payload);
    if (new TextEncoder().encode(JSON.stringify(record)).byteLength > MAX_PACK_BYTES ||
        typeof record.pack_id !== "string" || record.pack_id !== record.payload.pack_id ||
        typeof record.site_key !== "string" || record.site_key.length < 1 || record.site_key.length > 300 ||
        !record.payload.records.some(item => item.external_key === record.site_key) ||
        typeof record.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(record.sha256) ||
        !Number.isSafeInteger(record.site_id) || record.site_id < 1 ||
        record.expires_at !== record.payload.expires_at) {
      throw new Error("Lokal pakke må være komplett og høyst 256 KiB.");
    }
    if (!Number.isFinite(Date.parse(record.expires_at)) || Date.parse(record.expires_at) <= Date.now()) throw new Error("Utløpt pakke kan ikke lagres.");
    const packs = await listPacks();
    if (!packs.some(item => item.pack_id === record.pack_id) && packs.length >= MAX_PACKS) throw new Error("Lokal grense er fem pakker; slett en eksplisitt før du lagrer en ny.");
    const stored = { pack_id: record.pack_id, payload: record.payload, sha256: record.sha256,
      expires_at: record.expires_at, site_id: record.site_id, site_key: record.site_key,
      stored_at: new Date().toISOString() };
    await transact(PACK_STORE, "readwrite", store => store.put(stored));
    return { stored: true, pack_id: record.pack_id };
  }

  async function storeOutbox(record) {
    if (!onlyKeys(record, OUTBOX_FIELDS, [...OUTBOX_FIELDS]) || !onlyKeys(record.payload, new Set(["observed_at", "outcome", "note"]), ["observed_at", "outcome", "note"])) {
      throw new Error("Notatet inneholder ukjente felt.");
    }
    rejectSecretKeys(record);
    if (new TextEncoder().encode(JSON.stringify(record)).byteLength > MAX_NOTE_BYTES ||
        typeof record.pack_id !== "string" || !record.pack_id || record.pack_id.length > 128 ||
        typeof record.site_key !== "string" || !record.site_key || record.site_key.length > 300 ||
        !Number.isSafeInteger(record.site_id) || record.site_id < 1 ||
        !Number.isSafeInteger(record.cached_site_revision) || record.cached_site_revision < 0 ||
        typeof record.original_request_id !== "string" || !/^[A-Za-z0-9_-]{20,128}$/.test(record.original_request_id) ||
        typeof record.payload.observed_at !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(record.payload.observed_at) ||
        !Number.isFinite(Date.parse(`${record.payload.observed_at}T00:00:00Z`)) ||
        !["found", "not_found", "inaccessible", "needs_follow_up"].includes(record.payload.outcome) ||
        typeof record.payload.note !== "string" || record.payload.note.length > 4_000 || !record.payload.note.trim()) {
      throw new Error("Notatet mangler identitet eller overskrider 16 KiB.");
    }
    const pack = (await listPacks()).find(item => item.pack_id === record.pack_id);
    const site = pack?.payload.records.find(item => item.external_key === record.site_key);
    if (!pack || pack.site_id !== record.site_id || !site || site.revision !== record.cached_site_revision) throw new Error("Notatet må peke til nøyaktig sted og revisjon i en lagret pakke.");
    const existing = (await listOutbox(record.pack_id)).find(item => item.original_request_id === record.original_request_id);
    if (existing) {
      if (JSON.stringify(existing.payload) !== JSON.stringify(record.payload)) throw new Error("Opprinnelig forespørsels-ID er allerede brukt med annet notat.");
      return existing;
    }
    const db = await openDB();
    const all = await requestResult(db.transaction(OUTBOX_STORE, "readonly").objectStore(OUTBOX_STORE).getAll());
    if (all.length >= MAX_OUTBOX) throw new Error("Lokal utboks er full (maks 50 notater).");
    const stored = { pack_id: record.pack_id, site_key: record.site_key, site_id: record.site_id,
      cached_site_revision: record.cached_site_revision, original_request_id: record.original_request_id,
      payload: { observed_at: record.payload.observed_at, outcome: record.payload.outcome, note: record.payload.note },
      state: "local_pending", created_at: new Date().toISOString(), server_recorded: false, server_review: null };
    await transact(OUTBOX_STORE, "readwrite", store => store.add(stored));
    return stored;
  }

  async function deletePack(packId) {
    const db = await openDB();
    await new Promise((resolve, reject) => {
      const tx = db.transaction([PACK_STORE, OUTBOX_STORE], "readwrite");
      tx.objectStore(PACK_STORE).delete(packId);
      const outbox = tx.objectStore(OUTBOX_STORE);
      const request = outbox.index("pack_id").openCursor(IDBKeyRange.only(packId));
      request.onsuccess = () => { const cursor = request.result; if (cursor) { cursor.delete(); cursor.continue(); } };
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error("Lokal sletting feilet."));
      tx.onabort = () => reject(tx.error || new Error("Lokal sletting ble avbrutt."));
    });
    return { cleared: true, pack_id: packId };
  }

  function safeJSON(source) {
    return validatePackPayload(parseStrictJSON(source));
  }

  const publicAPI = { storePack, storeOutbox, listPacks, listOutbox, deletePack,
    parseStrictJSON, parseStrictUTF8JSON, parsePackJSON: safeJSON };
  globalThis.BKOfflineNotebook = publicAPI;

  function addText(parent, tag, value, className) {
    const element = document.createElement(tag);
    element.textContent = value == null ? "" : String(value);
    if (className) element.className = className;
    parent.append(element);
    return element;
  }

  function addField(parent, label, type = "text") {
    const wrapper = document.createElement("label");
    const caption = document.createTextNode(label);
    const input = type === "textarea" ? document.createElement("textarea") : document.createElement("input");
    if (type !== "textarea") input.type = type;
    wrapper.append(caption, input); parent.append(wrapper);
    return input;
  }

  function addButton(parent, label, action) {
    const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.addEventListener("click", action); parent.append(button); return button;
  }

  function jsonRequest(path, token, options = {}) {
    if (!token) return Promise.reject(new Error("Skriv inn administrator-token for denne manuelle handlingen."));
    const headers = new Headers(options.headers || {}); headers.set("Authorization", `Bearer ${token}`);
    if (options.body != null) headers.set("Content-Type", "application/json");
    return fetch(path, { ...options, headers }).then(async response => {
      const raw = await response.text();
      let payload;
      try { payload = raw ? JSON.parse(raw) : null; } catch { throw new Error("Serveren returnerte ugyldig JSON."); }
      if (!response.ok) {
        const detail = payload?.detail;
        const error = new Error(typeof detail === "string" ? detail : detail?.message || `Serverforespørselen feilet (${response.status}).`);
        error.status = response.status;
        throw error;
      }
      return payload;
    });
  }

  function initialize() {
    const host = document.getElementById("offline-notebook");
    if (!host || document.documentElement.dataset.offlineNotebookReady === "true") return;
    document.documentElement.dataset.offlineNotebookReady = "true";
    const status = addText(host, "p", "Lokal notatbok åpnes bare når nettleseren støtter IndexedDB."); status.setAttribute("role", "status");
    const token = addField(host, "Administratortoken for manuell kontroll (holdes bare i denne sidens minne)"); token.autocomplete = "off"; token.name = "ephemeral-admin-token";
    const packSelect = document.createElement("select"); packSelect.setAttribute("aria-label", "Velg lagret pakke");
    const packLabel = document.createElement("label"); packLabel.append(document.createTextNode("Lokal pakke "), packSelect); host.append(packLabel);
    const packDetails = document.createElement("section"); host.append(packDetails);
    const noteForm = document.createElement("fieldset"); const legend = document.createElement("legend"); legend.textContent = "Nytt frakoblet observasjonsnotat"; noteForm.append(legend); host.append(noteForm);
    const recordSelect = document.createElement("select"); recordSelect.setAttribute("aria-label", "Velg sted i offlinepakken");
    const recordLabel = document.createElement("label"); recordLabel.append(document.createTextNode("Sted "), recordSelect); noteForm.append(recordLabel);
    const observedAt = addField(noteForm, "Observert dato", "date"); observedAt.value = new Date().toISOString().slice(0, 10);
    const outcome = document.createElement("select"); outcome.setAttribute("aria-label", "Observasjonsutfall");
    for (const [value, label] of [["found", "Funnet"], ["not_found", "Ikke funnet"], ["inaccessible", "Utilgjengelig"], ["needs_follow_up", "Krever oppfølging"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; outcome.append(option); }
    const outcomeLabel = document.createElement("label"); outcomeLabel.append(document.createTextNode("Utfall "), outcome); noteForm.append(outcomeLabel);
    const note = addField(noteForm, "Notat for valgt sted", "textarea"); note.maxLength = 4000;
    const localSave = addButton(noteForm, "Lagre notat lokalt", saveLocalNote);
    const outboxHost = document.createElement("section"); addText(outboxHost, "h2", "Lokal utboks og manuell gjennomgang"); host.append(outboxHost);
    const reviewRef = addField(host, "Referanse for manuell gjennomgang (ikke en token)"); reviewRef.maxLength = 200;
    const ack = document.createElement("input"); ack.type = "checkbox"; ack.setAttribute("aria-label", "Jeg har gjennomgått serverens gjeldende sted og dette notatet");
    const ackLabel = document.createElement("label"); ackLabel.append(ack, document.createTextNode("Jeg har gjennomgått serverens gjeldende sted og dette notatet")); host.append(ackLabel);
    const shellDownload = addButton(host, "Last ned tom offentlig shell", async () => {
      try {
        const jsResponse = await fetch("/static/offline-notebook.js", { cache: "no-store" });
        const htmlResponse = await fetch("/static/offline-notebook.html", { cache: "no-store" });
        if (!jsResponse.ok || !htmlResponse.ok) throw new Error("Tom shell kunne ikke hentes.");
        let html = await htmlResponse.text(); const script = (await jsResponse.text()).replace(/<\/script/gi, "<\\/script");
        html = html.replace(/<script src="\/static\/offline-notebook\.js" defer><\/script>/, `<script>${script}</script>`);
        download("bunkerkartet-empty-offline-notebook.html", html, "text/html;charset=utf-8");
        status.textContent = "Tom shell lastet ned. Den inneholder ikke pakker eller notater.";
      } catch (error) { status.textContent = error.message; }
    });
    const clearButton = addButton(host, "Tøm valgt pakke og tilhørende notater", clearActivePack);
    const manualAuth = addButton(host, "Kontroller serverrevisjon manuelt", checkServerRevision);
    const packHint = addText(host, "p", "Ingen privat data lastes fra nettverket automatisk. Token og nettverkskall brukes bare etter en knapp du trykker.", "notice");
    let packs = [], activePack = null, outbox = [], manualServer = null;
    const syncingRequestIds = new Set();

    function download(name, content, type) {
      const url = URL.createObjectURL(new Blob([content], { type })); const link = document.createElement("a");
      link.href = url; link.download = name; link.hidden = true; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function refreshPacks(preferredId) {
      packs = await listPacks();
      packSelect.replaceChildren();
      for (const pack of packs) {
        const option = document.createElement("option"); option.value = pack.pack_id;
        const stale = Date.now() >= Date.parse(pack.expires_at) ? " · UTLØPT" : " · aktiv";
        option.textContent = `${pack.site_key}${stale} · utløp ${pack.expires_at}`; packSelect.append(option);
      }
      const queryId = new URL(location.href).searchParams.get("pack");
      const selectedId = preferredId || queryId;
      if (selectedId && packs.some(pack => pack.pack_id === selectedId)) packSelect.value = selectedId;
      activePack = packs.find(pack => pack.pack_id === packSelect.value) || null;
      await renderActivePack();
    }

    async function renderActivePack() {
      packDetails.replaceChildren(); outboxHost.replaceChildren(); addText(outboxHost, "h2", "Lokal utboks og manuell gjennomgang");
      localSave.disabled = !activePack;
      if (!activePack) {
        addText(packDetails, "p", "Denne nettleseren har ingen lokalt godkjente pakker. Åpne hovedappen og velg ett stedsutvalg med vedvarende enhetslagring.");
        recordSelect.replaceChildren(); outbox = []; return;
      }
      const expired = Date.now() >= Date.parse(activePack.expires_at);
      packDetails.classList.toggle("stale", expired);
      addText(packDetails, "p", `${expired ? "UTLØPT – skal ikke brukes til nye notater eller gjennomgang." : "Aktiv til utløp."} Utløp: ${activePack.expires_at}`, expired ? "notice stale" : "notice");
      addText(packDetails, "p", `Sted: ${activePack.site_key} · sist lagret ${activePack.payload?.created_at || activePack.stored_at} · SHA-256 ${activePack.sha256}`);
      const cachedRevision = activePack.payload.records.find(item => item.external_key === activePack.site_key)?.revision;
      if (manualServer) {
        const changed = manualServer.revision !== cachedRevision;
        addText(packDetails, "p", changed
          ? `Serverrevisjon har endret seg (${manualServer.revision}); gjennomgå gjeldende stedsoppføring. Den gamle versjonen godkjennes ikke for synkronisering.`
          : `Serverrevisjon er fortsatt ${manualServer.revision}; gjennomgå notatet før eventuell avgjørelse.`, changed ? "notice changed" : "notice");
        if (manualServer.name) addText(packDetails, "p", `Gjeldende servernavn: ${manualServer.name}`);
        packDetails.classList.toggle("changed", changed);
      }
      recordSelect.replaceChildren();
      for (const record of activePack.payload.records) {
        const option = document.createElement("option"); option.value = record.external_key; option.textContent = `${record.name || record.external_key} · revisjon ${record.revision}`; recordSelect.append(option);
      }
      outbox = await listOutbox(activePack.pack_id);
      for (const item of outbox) renderOutboxItem(item, expired);
      localSave.disabled = expired;
      manualAuth.disabled = expired;
      clearButton.disabled = false;
    }

    function renderOutboxItem(item, expired) {
      const card = document.createElement("article"); card.dataset.outboxRequestId = item.original_request_id;
      card.className = item.state === "needs_review" ? "changed" : "";
      addText(card, "h3", `${item.payload.outcome} · ${item.payload.observed_at}`);
      addText(card, "p", item.payload.note || "Uten fritekstnotat.");
      const id = addText(card, "p", `Opprinnelig forespørsels-ID: ${item.original_request_id}`); id.dataset.outboxRequestId = item.original_request_id;
      const syncResult = item.server_review?.sync_result;
      const state = item.state === "needs_review" ? "needs_review" : item.server_review?.state || (item.server_recorded ? "sendt til serverens manuelle kø" : "bare lagret lokalt");
      addText(card, "p", syncResult?.synced === true
        ? `Tilstand: observasjon levert med original-ID ${item.original_request_id}${syncResult.idempotent ? " (idempotent gjentakelse)" : ""}.`
        : `Tilstand: ${state}. Servergodkjenning er ikke observasjonssynkronisering.`);
      if (item.server_revision != null) addText(card, "p", `Serverrevisjon ${item.server_revision}; pakket revisjon ${item.cached_site_revision}.`);
      const push = addButton(card, "Send notat til serverens manuelle vurderingskø", () => queueOnServer(item));
      push.disabled = expired || item.server_recorded;
      const approve = addButton(card, "Godkjenn for senere manuell synkronisering", () => recordReview(item, "approve"));
      approve.disabled = expired || !item.server_recorded || item.server_revision !== item.cached_site_revision || !ack.checked || !reviewRef.value.trim() || syncResult?.synced === true;
      const defer = addButton(card, "Utsett etter gjennomgang", () => recordReview(item, "defer"));
      defer.disabled = expired || !item.server_recorded || !ack.checked || !reviewRef.value.trim() || syncResult?.synced === true;
      if (item.server_review?.state === "approved_for_sync" && syncResult?.synced !== true) {
        const sync = addButton(card, "Lever godkjent observasjon", () => syncOnServer(item, sync));
        sync.disabled = !canSyncItem(item) || syncingRequestIds.has(item.original_request_id);
        if (item.server_revision !== item.cached_site_revision || manualServer?.revision !== item.cached_site_revision) {
          addText(card, "p", "Synkronisering er blokkert til en eksplisitt revisjonskontroll bekrefter at stedet fortsatt samsvarer med den godkjente pakken.", "notice changed");
        }
      }
      outboxHost.append(card);
    }

    function canSyncItem(item) {
      return Boolean(activePack && Date.now() < Date.parse(activePack.expires_at) && item.server_recorded &&
        item.server_review?.state === "approved_for_sync" && item.server_review?.sync_result?.synced !== true &&
        item.server_revision === item.cached_site_revision && manualServer?.revision === item.cached_site_revision);
    }

    function localOutboxRecord(item) {
      const validStates = new Set(["local_pending", "review_current", "needs_review", "synced"]);
      const reviewStates = new Set(["pending_review", "needs_review", "approved_for_sync", "deferred", "stale_pack"]);
      const review = reviewStates.has(item.server_review?.state) ? { state: item.server_review.state } : null;
      const result = item.server_review?.sync_result;
      if (review && result?.synced === true && result.original_request_id === item.original_request_id &&
          typeof result.idempotent === "boolean" && Number.isSafeInteger(result.observation_id) &&
          Number.isSafeInteger(result.site_revision) && result.site_revision >= 0) {
        review.sync_result = { synced: true, original_request_id: item.original_request_id,
          idempotent: result.idempotent, observation_id: result.observation_id, site_revision: result.site_revision };
      }
      return {
        pack_id: item.pack_id, site_key: item.site_key, site_id: item.site_id,
        cached_site_revision: item.cached_site_revision, original_request_id: item.original_request_id,
        payload: { observed_at: item.payload.observed_at, outcome: item.payload.outcome, note: item.payload.note },
        state: validStates.has(item.state) ? item.state : "local_pending",
        created_at: typeof item.created_at === "string" ? item.created_at : new Date().toISOString(),
        server_recorded: item.server_recorded === true, server_review: review,
        server_revision: Number.isSafeInteger(item.server_revision) && item.server_revision >= 0 ? item.server_revision : null,
      };
    }

    async function persistItem(item) {
      await transact(OUTBOX_STORE, "readwrite", store => store.put(localOutboxRecord(item)));
      await renderActivePack();
    }

    async function syncOnServer(item, button) {
      if (syncingRequestIds.has(item.original_request_id)) return;
      if (Date.now() >= Date.parse(activePack?.expires_at || "")) { status.textContent = "Utgått pakke kan ikke synkroniseres; opprett et nytt utvalg etter gjennomgang."; return; }
      if (item.server_review?.state !== "approved_for_sync") { status.textContent = "Serverens godkjenning kreves før observasjonen kan leveres."; return; }
      if (!canSyncItem(item)) { status.textContent = "Synkronisering er blokkert. Kontroller serverrevisjonen manuelt; endret sted krever ny pakke og ny gjennomgang."; return; }
      if (!token.value.trim()) { status.textContent = "Skriv inn administratortoken for denne eksplisitte serverleveringen. Token blir bare brukt i sidens minne."; return; }
      const requestId = item.original_request_id;
      syncingRequestIds.add(requestId); button.disabled = true;
      try {
        const response = await jsonRequest(`/api/pilots/offline/packs/${encodeURIComponent(activePack.pack_id)}/outbox/${encodeURIComponent(requestId)}/sync`, token.value.trim(), {
          method: "POST", body: JSON.stringify({ expected_site_revision: item.cached_site_revision }),
        });
        if (response?.synced !== true || response.original_request_id !== requestId || typeof response.idempotent !== "boolean" ||
            !Number.isSafeInteger(response.observation?.id) || !Number.isSafeInteger(response.site?.revision) || response.site.revision < 0) {
          throw new Error("Serverkvitteringen for observasjonssynkronisering mangler gyldig identitet eller revisjon.");
        }
        item.server_review = { ...item.server_review, sync_result: {
          synced: true, original_request_id: requestId, idempotent: response.idempotent,
          observation_id: response.observation.id, site_revision: response.site.revision,
        } };
        item.server_revision = response.site.revision;
        item.state = "synced";
        manualServer = { revision: response.site.revision, name: response.site.name || manualServer?.name };
        await persistItem(item);
        status.textContent = `Observasjonen er levert med original-ID ${requestId}${response.idempotent ? "; serveren bekreftet en idempotent gjentakelse" : ""}.`;
      } catch (error) {
        if (error.status === 409) {
          item.state = "needs_review"; item.server_revision = null; manualServer = null;
          await persistItem(item);
          status.textContent = `Serveren avviste den godkjente revisjonen. Kontroller gjeldende sted og opprett ny pakke før ny gjennomgang. Original-ID ${requestId} er beholdt; ingen lokal synkroniseringskvittering er registrert.`;
        } else if (Number.isSafeInteger(error.status)) {
          status.textContent = `Serveren svarte ${error.status}; levering er ikke bekreftet. Original-ID ${requestId} er beholdt. Kontroller vurdering og revisjon før ny handling.`;
        } else {
          status.textContent = `Levering ble ikke bekreftet. Original-ID ${requestId} er beholdt; kontroller status og prøv samme handling igjen.`;
        }
      } finally {
        syncingRequestIds.delete(requestId);
        if (button.isConnected) button.disabled = !canSyncItem(item);
      }
    }

    async function saveLocalNote() {
      if (!activePack) { status.textContent = "Velg en aktiv, lagret pakke."; return; }
      if (Date.now() >= Date.parse(activePack.expires_at)) { status.textContent = "Pakken er utløpt; fjern den og opprett et nytt utvalg online."; return; }
      if (!observedAt.value || !note.value.trim()) { status.textContent = "Dato og et kort notat kreves."; return; }
      const externalKey = recordSelect.value;
      const record = activePack.payload.records.find(item => item.external_key === externalKey);
      if (!record) { status.textContent = "Velg et sted fra den lagrede pakken."; return; }
      const originalId = crypto.randomUUID();
      const payload = { observed_at: observedAt.value, outcome: outcome.value, note: note.value.trim() };
      try {
        const stored = await storeOutbox({ pack_id: activePack.pack_id, site_key: externalKey, site_id: activePack.site_id,
          cached_site_revision: record.revision, original_request_id: originalId, payload });
        note.value = ""; status.textContent = `Notat lagret lokalt med forespørsels-ID ${stored.original_request_id}. Ingen automatisk opplasting skjedde.`;
        await renderActivePack();
      } catch (error) { status.textContent = error.message; }
    }

    async function checkServerRevision() {
      if (!activePack) { status.textContent = "Velg en lokal pakke."; return; }
      if (Date.now() >= Date.parse(activePack.expires_at)) { status.textContent = "Pakken er utløpt; tøm den før ny registrering."; return; }
      try {
        const authToken = token.value.trim();
        const [remotePack, serverSite] = await Promise.all([
          jsonRequest(`/api/pilots/offline/packs/${encodeURIComponent(activePack.pack_id)}`, authToken),
          jsonRequest(`/api/sites/${activePack.site_id}`, authToken),
        ]);
        if (remotePack.stale) throw new Error("Serverpakken er utløpt; slett lokal kopi og opprett nytt utvalg.");
        const revision = serverSite?.revision;
        if (!Number.isSafeInteger(revision) || revision < 0) throw new Error("Serveren ga ingen gyldig revisjon; godkjenning er blokkert.");
        manualServer = { revision, name: serverSite.name };
        for (const item of outbox) {
          if (item.site_id === activePack.site_id) { item.server_revision = revision; item.state = revision === item.cached_site_revision ? "review_current" : "needs_review"; await transact(OUTBOX_STORE, "readwrite", store => store.put(localOutboxRecord(item))); }
        }
        packDetails.classList.toggle("changed", outbox.some(item => item.server_revision !== item.cached_site_revision));
        status.textContent = "Serverrevisjon er hentet etter et eksplisitt knappetrykk; denne kontrollen sendte ingen notater.";
        await renderActivePack();
      } catch (error) { status.textContent = error.message || "Serverrevisjon kunne ikke kontrolleres."; }
    }

    async function queueOnServer(item) {
      if (!activePack || Date.now() >= Date.parse(activePack.expires_at)) { status.textContent = "Utgått pakke kan ikke sendes til vurdering."; return; }
      try {
        const response = await jsonRequest(`/api/pilots/offline/packs/${encodeURIComponent(activePack.pack_id)}/outbox`, token.value.trim(), {
          method: "POST", body: JSON.stringify({ original_request_id: item.original_request_id, site_key: item.site_key,
            cached_site_revision: item.cached_site_revision, payload: item.payload }),
        });
        item.server_recorded = true; item.server_review = { state: response.state || "pending_review" };
        await persistItem(item); status.textContent = `Notatet er registrert i serverens manuelle vurderingskø (${item.original_request_id}); det er ikke synkronisert som observasjon.`;
      } catch (error) { status.textContent = error.message || "Serverkøen svarte ikke. Opprinnelig ID er beholdt for et identisk nytt forsøk."; }
    }

    async function recordReview(item, decision) {
      if (!ack.checked || !reviewRef.value.trim()) { status.textContent = "Gjennomgå serverstedet og fyll inn referanse før avgjørelse."; return; }
      if (decision === "approve" && (item.server_revision !== item.cached_site_revision || !manualServer || manualServer.revision !== item.cached_site_revision)) { status.textContent = "Stedet er endret siden pakken ble laget; godkjenning er sperret. Utsett eller lag et nytt utvalg."; return; }
      try {
        const result = await jsonRequest(`/api/pilots/offline/packs/${encodeURIComponent(activePack.pack_id)}/outbox/${encodeURIComponent(item.original_request_id)}/review`, token.value.trim(), {
          method: "POST", body: JSON.stringify({ decision, reviewer_ref: reviewRef.value.trim() }),
        });
        item.server_review = result; await persistItem(item);
        status.textContent = result.synced === false ? `Vurdering registrert: ${result.state}. Notatet er ikke synkronisert; senere levering er separat.` : `Vurdering registrert: ${result.state}.`;
      } catch (error) { status.textContent = error.message || "Servervurderingen feilet."; }
    }

    async function clearActivePack() {
      if (!activePack) { status.textContent = "Ingen lokal pakke å slette."; return; }
      const packId = activePack.pack_id;
      if (!globalThis.confirm || !confirm("Slette den valgte lokale pakken og alle tilhørende notater fra denne enheten?")) return;
      try {
        await deletePack(packId);
        status.textContent = "Lokal pakke og tilhørende notater er slettet.";
        const remoteToken = token.value.trim();
        if (remoteToken && navigator.onLine) {
          try {
            const result = await jsonRequest(`/api/pilots/offline/packs/${encodeURIComponent(packId)}`, remoteToken, { method: "DELETE" });
            if (result?.cleared !== true || result?.outbox_cleared !== true) throw new Error("server removal receipt incomplete");
            status.textContent += " Serverens tilsvarende pilotpakke ble også slettet.";
          }
          catch { status.textContent += " Serverkopi er ikke bekreftet slettet; den lokale kopien er fjernet."; }
        }
        await refreshPacks("");
      } catch (error) { status.textContent = error.message || "Lokal sletting feilet."; }
    }

    packSelect.addEventListener("change", () => { manualServer = null; ack.checked = false; refreshPacks(packSelect.value).catch(error => { status.textContent = error.message; }); });
    ack.addEventListener("change", () => renderActivePack().catch(error => { status.textContent = error.message; }));
    reviewRef.addEventListener("input", () => renderActivePack().catch(error => { status.textContent = error.message; }));
    token.addEventListener("input", () => { /* Deliberately memory-only; never mirrored to a storage API. */ });
    if (shellDownload.disabled) shellDownload.disabled = false;
    void packHint;
    refreshPacks().then(() => { status.textContent = packs.length ? "Lokal pakke lastet fra IndexedDB. Ingen automatisk nettverksforespørsel er sendt." : "Ingen privat pakke på denne enheten."; }).catch(error => { status.textContent = error.message; });
  }

  if (document.body?.hasAttribute("data-offline-notebook")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
    else initialize();
  }
}());
