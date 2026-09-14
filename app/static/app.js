const state = {
  token: "",
  sites: [],
  pendingImport: null,
  routeSiteIds: [],
  prioritySites: [],
  start: null,
  pickingStart: false,
  routeRequestInFlight: false,
};

const map = L.map("map").setView([63.4305, 10.3951], 12);
L.tileLayer(
  "https://cache.kartverket.no/v1/wmts/1.0.0/topo/default/webmercator/{z}/{y}/{x}.png",
  { maxZoom: 18, attribution: "&copy; Kartverket" }
).addTo(map);
const markerLayer = L.layerGroup().addTo(map);
const uncertaintyLayer = L.layerGroup().addTo(map);
const observationLayer = L.layerGroup().addTo(map);
let routeLayer = null;
let startMarker = null;

const $ = (id) => document.getElementById(id);
const text = (node, value) => { node.textContent = value ?? ""; return node; };

const STATUS_LABELS = {
  candidate: "Candidate",
  approximate: "Approximate",
  likely: "Researched",
  trusted: "Confirmed",
  "field-verified": "Field verified",
  "destroyed-or-filled": "Destroyed or filled",
  rejected: "Rejected",
};

const statusLabel = (status) => STATUS_LABELS[status] || status;
const outcomeLabel = (outcome) => outcome.replaceAll("_", " ");

function labeledControl(label, control, className = "") {
  const wrapper = document.createElement("label");
  if (className) wrapper.className = className;
  text(wrapper, label);
  wrapper.append(control);
  return wrapper;
}

function selectControl(values, selected, labels = {}) {
  const select = document.createElement("select");
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    text(option, labels[value] || statusLabel(value));
    option.selected = value === selected;
    select.append(option);
  });
  return select;
}

function inputControl(type, value = "") {
  const input = document.createElement("input");
  input.type = type;
  input.value = value ?? "";
  return input;
}

function textareaControl(value = "") {
  const textarea = document.createElement("textarea");
  text(textarea, value ?? "");
  return textarea;
}

function setStatus(message) { text($("map-status"), message); }

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function statusColor(status) {
  return {
    candidate: "#b7791f",
    likely: "#2f7456",
    trusted: "#1c3548",
    "field-verified": "#1c3548",
    "destroyed-or-filled": "#61707d",
  }[status] || "#c65d2e";
}

function siteById(id) {
  return state.sites.find((site) => site.id === id) || state.prioritySites.find((site) => site.id === id);
}

function renderMap() {
  markerLayer.clearLayers();
  uncertaintyLayer.clearLayers();
  observationLayer.clearLayers();
  const bounds = [];
  state.sites.forEach((site) => {
    if (site.latitude == null || site.longitude == null) return;
    const point = [site.latitude, site.longitude];
    bounds.push(point);
    const marker = L.circleMarker(point, {
      color: statusColor(site.status),
      fillColor: statusColor(site.status),
      fillOpacity: .88,
      radius: 7,
      weight: 2,
    }).addTo(markerLayer);
    if (site.uncertainty_m > 0) {
      L.circle(point, {
        color: statusColor(site.status),
        fill: false,
        opacity: .4,
        radius: site.uncertainty_m,
        weight: 1,
      }).addTo(uncertaintyLayer);
    }
    const popup = document.createElement("div");
    const heading = document.createElement("strong");
    text(heading, site.name);
    popup.append(heading);
    const meta = document.createElement("div");
    meta.className = "site-meta";
    text(meta, `${site.site_kind} | ${statusLabel(site.status)}`);
    popup.append(meta);
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    text(details, "Details");
    details.addEventListener("click", () => { marker.closePopup(); loadDetail(site.id); });
    const add = document.createElement("button");
    add.className = "small";
    const routeReady = site.latitude != null && site.longitude != null;
    text(add, !routeReady ? "No coordinates" : state.routeSiteIds.includes(site.id) ? "Added" : "Add route");
    add.disabled = !routeReady || state.routeSiteIds.includes(site.id);
    add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add);
    popup.append(actions);
    marker.bindPopup(popup);
    marker.on("click", () => loadDetail(site.id));
    (site.observation_points || []).forEach((observation) => {
      if (observation.latitude == null || observation.longitude == null) return;
      const observationPoint = [observation.latitude, observation.longitude];
      bounds.push(observationPoint);
      const observationMarker = L.circleMarker(observationPoint, {
        color: "#2f7456",
        fillColor: "#fff",
        fillOpacity: 1,
        radius: 5,
        weight: 2,
      }).addTo(observationLayer);
      const popup = document.createElement("div");
      const heading = document.createElement("strong"); text(heading, "Field observation"); popup.append(heading);
      const meta = document.createElement("div"); meta.className = "site-meta";
      text(meta, `${observation.observed_at} | ${outcomeLabel(observation.outcome)}`); popup.append(meta);
      const details = document.createElement("button"); details.className = "small"; details.type = "button"; text(details, "Open site");
      details.addEventListener("click", () => { observationMarker.closePopup(); loadDetail(site.id); });
      popup.append(details);
      observationMarker.bindPopup(popup);
    });
  });
  if (bounds.length) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
}

function renderSiteList() {
  const list = $("site-list");
  list.replaceChildren();
  if (!state.sites.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    text(empty, "No sites match the current filters.");
    list.append(empty);
    return;
  }
  state.sites.forEach((site) => {
    const item = document.createElement("article");
    item.className = `site-item status-${site.status}`;
    const head = document.createElement("div");
    head.className = "site-item-head";
    const name = document.createElement("h3");
    text(name, site.name);
    const badge = document.createElement("span");
    badge.className = "badge";
    text(badge, statusLabel(site.status));
    head.append(name, badge);
    item.append(head);
    const meta = document.createElement("div");
    meta.className = "site-meta";
    text(meta, `${site.site_kind} | ${site.precision} | ${site.access}`);
    item.append(meta);
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    text(details, "Details");
    details.addEventListener("click", () => loadDetail(site.id));
    const add = document.createElement("button");
    add.className = "small";
    const routeReady = site.latitude != null && site.longitude != null;
    text(add, !routeReady ? "No coordinates" : state.routeSiteIds.includes(site.id) ? "Added" : "Add route");
    add.disabled = !routeReady || state.routeSiteIds.includes(site.id);
    add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add);
    item.append(actions);
    list.append(item);
  });
}

async function loadSites() {
  state.token = $("admin-token").value.trim();
  if (!state.token) { setStatus("Enter the admin token to load sites."); return; }
  const params = new URLSearchParams();
  const status = $("status-filter").value;
  const kind = $("kind-filter").value.trim();
  if (status) params.set("status", status);
  if (kind) params.set("site_kind", kind);
  try {
    state.sites = await api(`/api/sites?${params}`);
    renderSiteList();
    renderMap();
    setStatus(state.sites.length
      ? `${state.sites.length} site${state.sites.length === 1 ? "" : "s"} loaded.`
      : status || kind
        ? "No sites match the current filters."
        : "Authenticated. No site records imported yet.");
    await loadCandidates();
    await loadFieldPriority();
  } catch (error) { setStatus(error.message); }
}

async function runReviewAction(id, action) {
  try {
    await api(`/api/sites/${id}/review`, { method: "POST", body: JSON.stringify({ action }) });
    await loadSites();
    await loadDetail(id);
  } catch (error) { setStatus(error.message); }
}

function renderLifecycleActions(site, root) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("h3"); text(heading, "Lifecycle"); section.append(heading);
  const actions = document.createElement("div"); actions.className = "candidate-actions";
  const transitions = {
    candidate: [["Mark researched", "research", ""]],
    likely: [["Mark field verified", "field_verify", ""]],
    "field-verified": [["Confirm", "confirm", ""]],
  };
  (transitions[site.status] || []).forEach(([label, action, style]) => {
    const button = document.createElement("button"); button.className = `small ${style}`; button.type = "button"; text(button, label);
    button.addEventListener("click", () => runReviewAction(site.id, action)); actions.append(button);
  });
  if (site.status !== "rejected") {
    const reject = document.createElement("button"); reject.className = "small danger"; reject.type = "button"; text(reject, "Reject");
    reject.addEventListener("click", () => runReviewAction(site.id, "reject")); actions.append(reject);
  } else {
    const restore = document.createElement("button"); restore.className = "small"; restore.type = "button"; text(restore, "Restore candidate");
    restore.addEventListener("click", () => runReviewAction(site.id, "restore")); actions.append(restore);
  }
  section.append(actions); root.append(section);
}

function renderSiteEditor(site, root) {
  const section = document.createElement("section"); section.className = "detail-section";
  const heading = document.createElement("h3"); text(heading, "Curate site"); section.append(heading);
  const form = document.createElement("form"); form.className = "detail-form";
  const grid = document.createElement("div"); grid.className = "detail-grid";
  const name = inputControl("text", site.name); name.name = "name";
  const kind = inputControl("text", site.site_kind); kind.name = "site_kind";
  const confidence = selectControl(["unknown", "low", "medium", "high"], site.confidence || "unknown"); confidence.name = "confidence";
  const access = selectControl(["unknown", "public", "private", "restricted", "permission_required", "dangerous", "unsafe"], site.access); access.name = "access";
  const precision = selectControl(["exact", "approximate", "unknown"], site.precision); precision.name = "precision";
  const uncertainty = inputControl("number", site.uncertainty_m); uncertainty.name = "uncertainty_m"; uncertainty.min = "0"; uncertainty.step = "1";
  const basis = selectControl(["explicit_coordinate", "address", "map_reference", "landmark_description", "llm_inference"], site.location_basis); basis.name = "location_basis";
  const latitude = inputControl("number", site.latitude); latitude.name = "latitude"; latitude.step = "0.000001"; latitude.min = "-90"; latitude.max = "90";
  const longitude = inputControl("number", site.longitude); longitude.name = "longitude"; longitude.step = "0.000001"; longitude.min = "-180"; longitude.max = "180";
  [["Name", name], ["Type", kind], ["Confidence", confidence], ["Access", access], ["Precision", precision], ["Uncertainty (m)", uncertainty], ["Location basis", basis], ["Latitude", latitude], ["Longitude", longitude]]
    .forEach(([label, control]) => grid.append(labeledControl(label, control)));
  form.append(grid);
  const condition = inputControl("text", site.condition || ""); condition.name = "condition";
  const rationale = textareaControl(site.short_rationale || ""); rationale.name = "short_rationale";
  const observedText = textareaControl(site.observed_location_text || ""); observedText.name = "observed_location_text";
  const warnings = textareaControl((site.warnings || []).join("\n")); warnings.name = "warnings";
  form.append(labeledControl("Condition", condition));
  form.append(labeledControl("Coordinate rationale", rationale));
  form.append(labeledControl("Observed location", observedText));
  form.append(labeledControl("Warnings (one per line)", warnings));
  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Save site changes"); form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const numberOrNull = (value) => value === "" ? null : Number(value);
    try {
      await api(`/api/sites/${site.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.value.trim(), site_kind: kind.value.trim(),
          confidence: confidence.value, access: access.value, precision: precision.value,
          uncertainty_m: numberOrNull(uncertainty.value), location_basis: basis.value,
          latitude: numberOrNull(latitude.value), longitude: numberOrNull(longitude.value),
          condition: condition.value.trim(), short_rationale: rationale.value.trim(),
          observed_location_text: observedText.value.trim(),
          warnings: warnings.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        }),
      });
      setStatus("Site changes saved.");
      await loadSites();
      await loadDetail(site.id);
    } catch (error) { setStatus(error.message); }
  });
  section.append(form); root.append(section);
}

function renderObservations(site, root) {
  const section = document.createElement("section"); section.className = "detail-section";
  const heading = document.createElement("h3"); text(heading, "Field observations"); section.append(heading);
  const observations = document.createElement("ul"); observations.className = "observation-list";
  (site.field_observations || []).forEach((observation) => {
    const item = document.createElement("li");
    const title = document.createElement("strong"); text(title, `${observation.observed_at} | ${outcomeLabel(observation.outcome)}`); item.append(title);
    const note = document.createElement("p"); text(note, observation.note); item.append(note);
    if (observation.observed_location_text) { const location = document.createElement("div"); location.className = "site-meta"; text(location, observation.observed_location_text); item.append(location); }
    if (observation.access_notes) { const access = document.createElement("div"); access.className = "site-meta"; text(access, `Access: ${observation.access_notes}`); item.append(access); }
    if (observation.latitude != null && observation.longitude != null) {
      const coordinates = document.createElement("div"); coordinates.className = "site-meta";
      text(coordinates, `Coordinate: ${observation.latitude.toFixed(5)}, ${observation.longitude.toFixed(5)}`); item.append(coordinates);
      if (observation.outcome === "found") {
        const actions = document.createElement("div"); actions.className = "candidate-actions";
        const adopt = document.createElement("button"); adopt.className = "small"; adopt.type = "button"; adopt.title = "Use this observation coordinate as the site marker";
        text(adopt, "Adopt coordinate"); adopt.addEventListener("click", () => adoptObservationLocation(site.id, observation.id));
        actions.append(adopt); item.append(actions);
      }
    }
    if (observation.photo_urls?.length) {
      const photos = document.createElement("div"); photos.className = "observation-links";
      observation.photo_urls.forEach((url, index) => { const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noreferrer"; text(link, `Photo ${index + 1}`); photos.append(link); });
      item.append(photos);
    }
    observations.append(item);
  });
  if (!observations.children.length) { const empty = document.createElement("p"); empty.className = "empty-state"; text(empty, "No field observations recorded."); section.append(empty); }
  else section.append(observations);

  const form = document.createElement("form"); form.className = "observation-form";
  const localToday = new Date(); localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
  const observedAt = inputControl("date", localToday.toISOString().slice(0, 10)); observedAt.name = "observed_at"; observedAt.required = true;
  const outcome = selectControl(["found", "not_found", "inaccessible", "needs_follow_up"], "found", { found: "Found", not_found: "Not found", inaccessible: "Inaccessible", needs_follow_up: "Needs follow-up" }); outcome.name = "outcome";
  const note = textareaControl(); note.name = "note"; note.required = true; note.placeholder = "What was observed?";
  const latitude = inputControl("number"); latitude.name = "latitude"; latitude.step = "0.000001"; latitude.min = "-90"; latitude.max = "90";
  const longitude = inputControl("number"); longitude.name = "longitude"; longitude.step = "0.000001"; longitude.min = "-180"; longitude.max = "180";
  const location = textareaControl(); location.name = "observed_location_text";
  const access = textareaControl(); access.name = "access_notes";
  const photos = textareaControl(); photos.name = "photo_urls"; photos.placeholder = "One photo URL per line";
  const grid = document.createElement("div"); grid.className = "detail-grid";
  grid.append(labeledControl("Date", observedAt), labeledControl("Outcome", outcome), labeledControl("Observed latitude", latitude), labeledControl("Observed longitude", longitude));
  form.append(grid, labeledControl("Observation note", note), labeledControl("Observed location", location), labeledControl("Access notes", access), labeledControl("Photo URLs", photos));
  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Save observation"); form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const numberOrUndefined = (value) => value === "" ? undefined : Number(value);
    try {
      await api(`/api/sites/${site.id}/observations`, {
        method: "POST",
        body: JSON.stringify({
          observed_at: observedAt.value, outcome: outcome.value, note: note.value.trim(),
          latitude: numberOrUndefined(latitude.value), longitude: numberOrUndefined(longitude.value),
          observed_location_text: location.value.trim() || undefined, access_notes: access.value.trim() || undefined,
          photo_urls: photos.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        }),
      });
      setStatus("Field observation saved.");
      await loadSites();
      await loadDetail(site.id);
    } catch (error) { setStatus(error.message); }
  });
  section.append(form); root.append(section);
}

async function adoptObservationLocation(siteId, observationId) {
  if (!window.confirm("Adopt this field observation coordinate for the site?")) return;
  try {
    await api(`/api/sites/${siteId}/observations/${observationId}/adopt-location`, { method: "POST" });
    setStatus("Observation coordinate adopted.");
    await loadSites();
    await loadDetail(siteId);
  } catch (error) { setStatus(error.message); }
}

async function loadDetail(id) {
  try {
    const site = await api(`/api/sites/${id}`);
    const root = $("site-detail");
    root.replaceChildren();
    const copy = document.createElement("dl"); copy.className = "detail-copy";
    [["Name", site.name], ["Type", site.site_kind], ["Status", statusLabel(site.status)],
      ["Confidence", site.confidence || "Unknown"],
      ["Precision", `${site.precision}${site.uncertainty_m == null ? "" : ` (${site.uncertainty_m} m)`}`],
      ["Access", site.access], ["Basis", site.location_basis], ["Condition", site.condition || "Unknown"]]
      .forEach(([label, value]) => { const dt = document.createElement("dt"); text(dt, label); const dd = document.createElement("dd"); text(dd, value); copy.append(dt, dd); });
    if (site.short_rationale) { const rationale = document.createElement("p"); text(rationale, site.short_rationale); copy.append(rationale); }
    if (site.warnings?.length) { const warning = document.createElement("p"); warning.className = "warning"; text(warning, site.warnings.join(" | ")); copy.append(warning); }
    const sourcesTitle = document.createElement("dt"); text(sourcesTitle, "Sources"); copy.append(sourcesTitle);
    const sources = document.createElement("dd"); const sourceList = document.createElement("ul"); sourceList.className = "source-list";
    (site.sources || []).forEach((source) => { const li = document.createElement("li"); const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; text(link, source.title || source.url); li.append(link); const excerpt = document.createElement("div"); excerpt.className = "site-meta"; text(excerpt, source.excerpt); li.append(excerpt); sourceList.append(li); });
    sources.append(sourceList); copy.append(sources); root.append(copy);
    renderLifecycleActions(site, root);
    renderSiteEditor(site, root);
    renderObservations(site, root);
  } catch (error) { text($("site-detail"), error.message); }
}

async function readImport(file) {
  try {
    state.pendingImport = JSON.parse(await file.text());
    $("preview-import").disabled = false;
    $("commit-import").disabled = true;
    text($("import-result"), "JSON loaded. Preview before commit.");
  } catch (error) {
    state.pendingImport = null;
    text($("import-result"), `Invalid JSON: ${error.message}`);
  }
}

async function previewImport() {
  if (!state.pendingImport) return;
  try {
    const result = await api("/api/admin/imports/preview", { method: "POST", body: JSON.stringify(state.pendingImport) });
    text($("import-result"), JSON.stringify(result, null, 2));
    $("commit-import").disabled = false;
  } catch (error) { text($("import-result"), error.message); }
}

async function commitImport() {
  if (!state.pendingImport) return;
  try {
    const result = await api("/api/admin/imports/commit", { method: "POST", body: JSON.stringify(state.pendingImport) });
    text($("import-result"), JSON.stringify(result, null, 2));
    $("commit-import").disabled = true;
    await loadSites();
    await loadCandidates();
  } catch (error) { text($("import-result"), error.message); }
}

async function loadCandidates() {
  if (!state.token) return;
  try {
    const params = new URLSearchParams();
    const confidence = $("review-confidence-filter").value;
    const access = $("review-access-filter").value;
    const uncertaintyBand = $("review-uncertainty-filter").value;
    const sourceType = $("review-source-filter").value.trim();
    if (confidence) params.set("confidence", confidence);
    if (access) params.set("access", access);
    if (uncertaintyBand) params.set("uncertainty_band", uncertaintyBand);
    if (sourceType) params.set("source_type", sourceType);
    const candidates = await api(`/api/review/candidates?${params}`);
    const list = $("candidate-list"); list.replaceChildren();
    text($("candidate-summary"), `${candidates.length} candidate${candidates.length === 1 ? "" : "s"} in this view.`);
    if (!candidates.length) {
      const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "No candidates waiting for review."); list.append(empty); return;
    }
    candidates.forEach((site) => {
      const item = document.createElement("article"); item.className = "candidate-item";
      const title = document.createElement("strong"); text(title, site.name); item.append(title);
      const uncertainty = site.uncertainty_m == null ? "uncertainty unknown" : `${Math.round(site.uncertainty_m)} m`;
      const sourceCount = (site.sources || []).length;
      const meta = document.createElement("div"); meta.className = "site-meta";
      text(meta, `${site.site_kind} | ${site.confidence || "unknown"} | ${uncertainty} | ${site.access} | ${sourceCount} source${sourceCount === 1 ? "" : "s"}`); item.append(meta);
      if (site.warnings?.length) {
        const warnings = document.createElement("div"); warnings.className = "warning";
        text(warnings, `${site.warnings.length} warning${site.warnings.length === 1 ? "" : "s"}`); item.append(warnings);
      }
      const actions = document.createElement("div"); actions.className = "candidate-actions";
      const details = document.createElement("button"); details.className = "small"; text(details, "Details");
      details.addEventListener("click", () => loadDetail(site.id)); actions.append(details);
      [["Mark researched", "research", ""], ["Reject", "reject", "danger"]].forEach(([label, action, style]) => {
        const button = document.createElement("button"); button.className = `small ${style}`; text(button, label);
        button.addEventListener("click", async () => {
          try { await api(`/api/sites/${site.id}/review`, { method: "POST", body: JSON.stringify({ action }) }); await loadSites(); await loadCandidates(); }
          catch (error) { setStatus(error.message); }
        }); actions.append(button);
      });
      item.append(actions); list.append(item);
    });
  } catch (error) { text($("candidate-list"), error.message); }
}

function renderFieldPriority() {
  const list = $("field-priority-list"); list.replaceChildren();
  const sites = state.prioritySites;
  text($("field-priority-summary"), `${sites.length} public site${sites.length === 1 ? "" : "s"} in shortlist.`);
  if (!sites.length) {
    const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "No sites meet the field shortlist."); list.append(empty); return;
  }
  sites.forEach((site) => {
    const item = document.createElement("article"); item.className = `site-item status-${site.status}`;
    const head = document.createElement("div"); head.className = "site-item-head";
    const name = document.createElement("h3"); text(name, site.name);
    const badge = document.createElement("span"); badge.className = "badge"; text(badge, statusLabel(site.status)); head.append(name, badge); item.append(head);
    const uncertainty = site.uncertainty_m == null ? "uncertainty unknown" : `${Math.round(site.uncertainty_m)} m`;
    const meta = document.createElement("div"); meta.className = "site-meta"; text(meta, `${site.confidence || "unknown"} | ${uncertainty} | ${site.access}`); item.append(meta);
    const actions = document.createElement("div"); actions.className = "site-actions";
    const details = document.createElement("button"); details.className = "small"; details.type = "button"; text(details, "Details"); details.addEventListener("click", () => loadDetail(site.id));
    const add = document.createElement("button"); add.className = "small"; add.type = "button"; text(add, state.routeSiteIds.includes(site.id) ? "Added" : "Add route"); add.disabled = state.routeSiteIds.includes(site.id); add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add); item.append(actions); list.append(item);
  });
}

async function loadFieldPriority() {
  if (!state.token) return;
  try {
    state.prioritySites = await api("/api/field-priority?limit=12");
    renderFieldPriority();
  } catch (error) { text($("field-priority-list"), error.message); }
}

function updateRouteStart(point, label) {
  state.start = point;
  if (startMarker) startMarker.remove();
  startMarker = L.marker([point.lat, point.lon]).addTo(map).bindTooltip("Route start");
  text($("route-start"), `Start: ${label || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`}`);
}

function addRouteSite(id) {
  if (!state.routeSiteIds.includes(id)) state.routeSiteIds.push(id);
  renderRouteStops(); renderSiteList(); renderMap();
}

function renderRouteStops() {
  const list = $("route-stops"); list.replaceChildren();
  state.routeSiteIds.forEach((id, index) => {
    const site = siteById(id); if (!site) return;
    const item = document.createElement("li"); item.className = "route-stop";
    const name = document.createElement("span"); name.className = "route-stop-name"; text(name, site.name);
    const up = document.createElement("button"); up.className = "small"; up.title = "Move earlier"; text(up, "Up"); up.disabled = index === 0;
    up.addEventListener("click", () => { [state.routeSiteIds[index - 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index - 1]]; renderRouteStops(); });
    const down = document.createElement("button"); down.className = "small"; down.title = "Move later"; text(down, "Down"); down.disabled = index === state.routeSiteIds.length - 1;
    down.addEventListener("click", () => { [state.routeSiteIds[index + 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index + 1]]; renderRouteStops(); });
    const remove = document.createElement("button"); remove.className = "small"; remove.title = "Remove stop"; text(remove, "Remove");
    remove.addEventListener("click", () => { state.routeSiteIds.splice(index, 1); renderRouteStops(); renderSiteList(); renderMap(); });
    item.append(name, up, down, remove); list.append(item);
  });
  $("create-route").disabled = !state.start || state.routeSiteIds.length === 0;
}

async function createRoute() {
  if (!state.start || state.routeSiteIds.length === 0 || state.routeRequestInFlight) return;
  const waypoints = state.routeSiteIds.map(siteById).filter(Boolean).map((site) => ({ lat: site.latitude, lon: site.longitude }));
  state.routeRequestInFlight = true;
  const button = $("create-route"); button.disabled = true; text(button, "Creating route...");
  try {
    const result = await api("/api/routes", { method: "POST", body: JSON.stringify({ name: "Trondheim field route", start: state.start, waypoints }) });
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    const root = $("route-result"); root.replaceChildren();
    const summary = document.createElement("div"); text(summary, `${Math.round(result.distance_m)} m | ${Math.round(result.duration_s / 60)} min`); root.append(summary);
    result.warnings.forEach((warning) => { const p = document.createElement("p"); p.className = "warning"; text(p, warning); root.append(p); });
    const download = document.createElement("a"); download.href = URL.createObjectURL(new Blob([result.gpx], { type: "application/gpx+xml" })); download.download = "bunkerkartet-route.gpx"; text(download, "Download GPX"); root.append(download);
  } catch (error) { text($("route-result"), error.message); }
  finally { state.routeRequestInFlight = false; text(button, "Create route"); renderRouteStops(); }
}

$("auth-form").addEventListener("submit", (event) => { event.preventDefault(); loadSites(); });
$("refresh-sites").addEventListener("click", loadSites);
$("status-filter").addEventListener("change", loadSites);
$("kind-filter").addEventListener("change", loadSites);
$("import-file").addEventListener("change", (event) => { if (event.target.files[0]) readImport(event.target.files[0]); });
$("preview-import").addEventListener("click", previewImport);
$("commit-import").addEventListener("click", commitImport);
$("refresh-candidates").addEventListener("click", loadCandidates);
$("refresh-field-priority").addEventListener("click", loadFieldPriority);
$("review-confidence-filter").addEventListener("change", loadCandidates);
$("review-access-filter").addEventListener("change", loadCandidates);
$("review-uncertainty-filter").addEventListener("change", loadCandidates);
$("review-source-filter").addEventListener("change", loadCandidates);
$("pick-start").addEventListener("click", () => { state.pickingStart = true; setStatus("Click the map to set the route start."); });
$("use-location").addEventListener("click", () => {
  if (!navigator.geolocation) { setStatus("Location is not available in this browser."); return; }
  navigator.geolocation.getCurrentPosition((position) => {
    const point = { lat: position.coords.latitude, lon: position.coords.longitude };
    updateRouteStart(point, "current location"); map.setView([point.lat, point.lon], 15);
  }, () => setStatus("Could not read current location."));
});
$("create-route").addEventListener("click", createRoute);
map.on("click", (event) => {
  if (!state.pickingStart) return;
  state.pickingStart = false;
  updateRouteStart({ lat: event.latlng.lat, lon: event.latlng.lng });
  setStatus("Route start set.");
  renderRouteStops();
});

updateRouteStart({ lat: 63.4305, lon: 10.3951 }, "map center");
