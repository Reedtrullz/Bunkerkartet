const state = {
  token: "",
  authEpoch: 0,
  sites: [],
  routes: [],
  pendingImport: null,
  previewHash: null,
  importGeneration: 0,
  importRequestInFlight: false,
  routeSiteIds: [],
  prioritySites: [],
  siteCache: new Map(),
  start: null,
  pickingStart: false,
  routeRequestInFlight: false,
  gpxObjectUrls: new Set(),
};

const map = L.map("map").setView([63.4305, 10.3951], 12);
const topoLayer = L.tileLayer(
  "https://cache.kartverket.no/v1/wmts/1.0.0/topo/default/webmercator/{z}/{y}/{x}.png",
  { maxZoom: 18, attribution: "&copy; Kartverket" }
).addTo(map);
const imageryLayer = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, attribution: "Tiles &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community" }
);
L.control.layers({ "Topo (Kartverket)": topoLayer, "Aerial imagery": imageryLayer }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
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
const ACCESS_LABELS = {
  public: "Public approach",
  private: "Private",
  restricted: "Restricted",
  unknown: "Access unknown",
  permission_required: "Permission required",
  dangerous: "Dangerous",
  unsafe: "Unsafe",
};

const statusLabel = (status) => STATUS_LABELS[status] || status;
const accessLabel = (access) => ACCESS_LABELS[access] || access;
const outcomeLabel = (outcome) => outcome.replaceAll("_", " ");

function locationCategory(siteKind) {
  const kind = String(siteKind || "").toLowerCase();
  if (kind.includes("pow") || kind.includes("fangeleir") || kind.includes("prisoner")) {
    return { key: "pow", label: "POW camp", glyph: "P" };
  }
  if (kind.includes("war grave") || kind.includes("grave")) {
    return { key: "grave", label: "War grave", glyph: "G" };
  }
  if (kind.includes("war memorial") || kind.includes("memorial") || kind.includes("monument")) {
    return { key: "memorial", label: "War memorial", glyph: "M" };
  }
  if (kind.includes("cave") || kind.includes("tunnel")) {
    return { key: "cave", label: "Cave / tunnel", glyph: "C" };
  }
  if (["bunker", "fort", "battery", "searchlight", "observation", "communications"].some((term) => kind.includes(term))) {
    return { key: "bunker", label: "Bunker / military position", glyph: "B" };
  }
  return { key: "other", label: "Other wartime site", glyph: "O" };
}

function statusClass(status) {
  return {
    candidate: "candidate",
    approximate: "approximate",
    likely: "likely",
    trusted: "trusted",
    "field-verified": "field-verified",
    "destroyed-or-filled": "destroyed-or-filled",
    rejected: "rejected",
  }[status] || "candidate";
}

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

function revokeGpxObjectUrls() {
  state.gpxObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.gpxObjectUrls.clear();
}

function cacheSites(sites) { sites.forEach((site) => state.siteCache.set(site.id, site)); }

function isStaleRequest(error) { return error?.name === "AbortError"; }

function clearAuthenticatedData({ resetToken = true } = {}) {
  state.authEpoch += 1;
  state.sites = [];
  state.routes = [];
  state.prioritySites = [];
  state.routeSiteIds = [];
  state.siteCache.clear();
  state.pendingImport = null;
  state.previewHash = null;
  state.importGeneration += 1;
  state.importRequestInFlight = false;
  state.start = null;
  state.pickingStart = false;
  state.routeRequestInFlight = false;
  revokeGpxObjectUrls();
  markerLayer.clearLayers();
  uncertaintyLayer.clearLayers();
  observationLayer.clearLayers();
  if (routeLayer) { routeLayer.remove(); routeLayer = null; }
  if (startMarker) { startMarker.remove(); startMarker = null; }
  $("download-geojson").disabled = true;
  renderSiteList();
  renderMap();
  renderFieldPriority();
  renderRouteHistory();
  renderRouteStops();
  $("candidate-list").replaceChildren();
  text($("candidate-summary"), "");
  $("detail-panel").classList.remove("is-selected");
  text($("site-detail-heading"), "Site detail");
  text($("site-detail"), "Select a marker or site.");
  $("route-result").replaceChildren();
  text($("route-start"), "Start: no route start selected");
  $("import-file").value = "";
  $("import-file").disabled = false;
  $("preview-import").disabled = true;
  $("commit-import").disabled = true;
  text($("preview-import"), "Preview");
  text($("commit-import"), "Commit");
  text($("import-result"), "");
  $("load-sites").disabled = false;
  text($("load-sites"), "Load map");
  text($("create-route"), "Create route");
  if (resetToken) {
    state.token = "";
    $("admin-token").value = "";
  }
}

function renderImportPreview(result) {
  const root = $("import-result");
  root.replaceChildren();
  const summary = document.createElement("p");
  text(summary, `${result.summary.total} record${result.summary.total === 1 ? "" : "s"}; ${result.summary.warnings} warning${result.summary.warnings === 1 ? "" : "s"}. Review preview; it is not human approval.`);
  root.append(summary);
  result.records.forEach((record) => {
    const item = document.createElement("article");
    const heading = document.createElement("strong"); text(heading, `${record.name} — ${record.action}`); item.append(heading);
    record.changes.forEach((change) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `${change.field}: ${JSON.stringify(change.before)} → ${JSON.stringify(change.after)}`); item.append(line); });
    record.preserved_fields.forEach((field) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `Preserved: ${field}`); item.append(line); });
    record.evidence.forEach((evidence) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `Evidence: ${evidence.title} — ${evidence.excerpt}`); item.append(line); });
    record.warnings.forEach((warning) => { const line = document.createElement("div"); line.className = "warning"; text(line, warning); item.append(line); });
    root.append(item);
  });
  const technical = document.createElement("details");
  const label = document.createElement("summary"); text(label, "Show technical preview"); technical.append(label);
  const raw = document.createElement("pre"); text(raw, JSON.stringify(result, null, 2)); technical.append(raw); root.append(technical);
}

async function api(path, options = {}) {
  const requestEpoch = state.authEpoch;
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (requestEpoch !== state.authEpoch) throw new DOMException("Utdatert forespørsel", "AbortError");
  if (!response.ok) {
    if (response.status === 401) {
      clearAuthenticatedData();
      setStatus("Authentication failed; private workspace cleared.");
      throw new DOMException("Utdatert forespørsel", "AbortError");
    }
    const error = new Error(body.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
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
  return state.siteCache.get(id) || state.sites.find((site) => site.id === id) || state.prioritySites.find((site) => site.id === id);
}

function renderMap(fit = false) {
  markerLayer.clearLayers();
  uncertaintyLayer.clearLayers();
  observationLayer.clearLayers();
  const bounds = [];
  state.sites.forEach((site) => {
    if (site.latitude == null || site.longitude == null) return;
    const point = [site.latitude, site.longitude];
    bounds.push(point);
    const category = locationCategory(site.site_kind);
    const marker = L.marker(point, {
      alt: `${category.label}: ${site.name}`,
      icon: L.divIcon({
        className: "site-marker-icon",
        html: `<span class="site-marker site-marker-${category.key} status-${statusClass(site.status)}" aria-label="${category.label}">${category.glyph}</span>`,
        iconAnchor: [14, 14],
        iconSize: [28, 28],
        popupAnchor: [0, -14],
      }),
      title: `${category.label}: ${site.name}`,
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
    const uncertainty = site.uncertainty_m == null ? "uncertainty unknown" : `${Math.round(site.uncertainty_m)} m`;
    text(meta, `${site.site_kind} | ${statusLabel(site.status)} | ${site.confidence || "unknown"} | ${uncertainty} | ${accessLabel(site.access)}`);
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
  if (fit && bounds.length) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
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
    text(meta, `${site.site_kind} | ${site.precision} | ${accessLabel(site.access)}`);
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
  const token = $("admin-token").value.trim();
  if (token !== state.token) clearAuthenticatedData({ resetToken: false });
  state.token = token;
  if (!state.token) { setStatus("Enter the admin token to load sites."); return; }
  const requestEpoch = state.authEpoch;
  const loadButton = $("load-sites"); loadButton.disabled = true; text(loadButton, "Loading...");
  const params = new URLSearchParams();
  const status = $("status-filter").value;
  const kind = $("kind-filter").value.trim();
  const query = $("site-search").value.trim();
  const access = $("access-filter").value;
  const confidence = $("confidence-filter").value;
  const hasFilters = [status, kind, query, access, confidence].some(Boolean);
  if (status) params.set("status", status);
  if (kind) params.set("site_kind", kind);
  if (query) params.set("q", query);
  if (access) params.set("access", access);
  if (confidence) params.set("confidence", confidence);
  try {
    state.sites = await api(`/api/sites?${params}`);
    if (requestEpoch !== state.authEpoch) return;
    cacheSites(state.sites);
    $("download-geojson").disabled = false;
    renderSiteList();
    renderMap(true);
    setStatus(state.sites.length
      ? `${state.sites.length} site${state.sites.length === 1 ? "" : "s"} loaded.`
      : hasFilters
        ? "No sites match the current filters."
        : "Authenticated. No site records imported yet.");
    await loadCandidates();
    await loadFieldPriority();
    await loadRoutes();
  } catch (error) {
    if (isStaleRequest(error)) return;
    if (error.status === 401 || error.status === 503) clearAuthenticatedData();
    setStatus(error.message);
  } finally {
    if (requestEpoch === state.authEpoch) { loadButton.disabled = false; text(loadButton, "Load map"); }
  }
}

async function runReviewAction(id, action, targetSiteId = null) {
  if (action === "reject" && !window.confirm("Reject this candidate?")) return;
  if (action === "mark_destroyed" && !window.confirm("Mark this site as destroyed or filled?")) return;
  if (action === "merge" && !window.confirm("Merge this record into the selected surviving site?")) return;
  try {
    const payload = { action };
    if (targetSiteId) payload.target_site_id = targetSiteId;
    const result = await api(`/api/sites/${id}/review`, { method: "POST", body: JSON.stringify(payload) });
    if (result.site) state.siteCache.set(result.site.id, result.site);
    await loadSites();
    await loadDetail(targetSiteId || id);
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

function renderLifecycleActions(site, root) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("h3"); text(heading, "Lifecycle"); section.append(heading);
  const actions = document.createElement("div"); actions.className = "candidate-actions";
  const transitions = {
    candidate: [["Mark researched", "research", ""], ["Mark approximate", "mark_approximate", ""], ["Mark destroyed or filled", "mark_destroyed", "danger"]],
    approximate: [["Mark researched", "research", ""], ["Mark destroyed or filled", "mark_destroyed", "danger"]],
    likely: [["Mark field verified", "field_verify", ""], ["Mark destroyed or filled", "mark_destroyed", "danger"]],
    "field-verified": [["Confirm", "confirm", ""], ["Mark destroyed or filled", "mark_destroyed", "danger"]],
    trusted: [["Mark destroyed or filled", "mark_destroyed", "danger"]],
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
  if (site.status === "candidate") {
    const targets = [...state.siteCache.values()]
      .filter((candidate) => candidate.id !== site.id && candidate.status === "candidate" && candidate.merged_into_id == null)
      .sort((first, second) => first.name.localeCompare(second.name));
    if (targets.length) {
      const target = document.createElement("select");
      const placeholder = document.createElement("option"); placeholder.value = ""; text(placeholder, "Choose surviving site"); target.append(placeholder);
      targets.forEach((candidate) => { const option = document.createElement("option"); option.value = candidate.id; text(option, `${candidate.name} (#${candidate.id})`); target.append(option); });
      const merge = document.createElement("button"); merge.className = "small danger"; merge.type = "button"; merge.disabled = true; text(merge, "Merge into selected");
      target.addEventListener("change", () => { merge.disabled = !target.value; });
      merge.addEventListener("click", () => runReviewAction(site.id, "merge", Number(target.value)));
      section.append(labeledControl("Merge duplicate into", target), merge);
    }
  }
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
    } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
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
    if (observation.photo_urls_status) { const withheld = document.createElement("div"); withheld.className = "site-meta"; text(withheld, observation.photo_urls_status); item.append(withheld); }
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
    } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
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
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

async function loadDetail(id) {
  const requestEpoch = state.authEpoch;
  const panel = $("detail-panel");
  const root = $("site-detail");
  panel.classList.add("is-selected");
  text(root, "Loading site details...");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  $("site-detail-heading").focus({ preventScroll: true });
  try {
    const site = await api(`/api/sites/${id}`);
    state.siteCache.set(site.id, site);
    text($("site-detail-heading"), `Site detail: ${site.name}`);
    root.replaceChildren();
    const coordinates = site.latitude == null ? "Unknown" : `${site.latitude.toFixed(5)}, ${site.longitude.toFixed(5)}`;
    const copy = document.createElement("dl"); copy.className = "detail-copy";
    [["Name", site.name], ["External key", site.external_key], ["Type", site.site_kind], ["Status", statusLabel(site.status)],
      ["Confidence", site.confidence || "Unknown"],
      ["Coordinates", coordinates],
      ["Precision", `${site.precision}${site.uncertainty_m == null ? "" : ` (${site.uncertainty_m} m)`}`],
      ["Access", accessLabel(site.access)], ["Basis", site.location_basis], ["Condition", site.condition || "Unknown"],
      ["Observed location", site.observed_location_text || "Unknown"]]
      .forEach(([label, value]) => { const dt = document.createElement("dt"); text(dt, label); const dd = document.createElement("dd"); text(dd, value); copy.append(dt, dd); });
    if (site.short_rationale) { const rationale = document.createElement("p"); text(rationale, site.short_rationale); copy.append(rationale); }
    if (site.warnings?.length) { const warning = document.createElement("p"); warning.className = "warning"; text(warning, site.warnings.join(" | ")); copy.append(warning); }
    const sourcesTitle = document.createElement("dt"); text(sourcesTitle, "Sources"); copy.append(sourcesTitle);
    const sources = document.createElement("dd"); const sourceList = document.createElement("ul"); sourceList.className = "source-list";
    (site.sources || []).forEach((source) => { const li = document.createElement("li"); if (source.url) { const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; text(link, source.title || source.url); li.append(link); } else { const withheld = document.createElement("span"); text(withheld, source.title || "Reference withheld"); li.append(withheld); } const sourceMeta = document.createElement("div"); sourceMeta.className = "site-meta"; text(sourceMeta, [source.source_type || "source", source.published_at && `published ${source.published_at}`, source.accessed_at && `accessed ${source.accessed_at}`, source.url_status].filter(Boolean).join(" | ")); li.append(sourceMeta); const excerpt = document.createElement("div"); excerpt.className = "site-meta"; text(excerpt, source.excerpt); li.append(excerpt); sourceList.append(li); });
    sources.append(sourceList); copy.append(sources); root.append(copy);
    if (site.latitude != null && site.longitude != null) {
      const copyCoordinates = document.createElement("button"); copyCoordinates.className = "small"; copyCoordinates.type = "button"; text(copyCoordinates, "Copy coordinates");
      copyCoordinates.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(`${site.latitude}, ${site.longitude}`); setStatus("Coordinates copied."); }
        catch { setStatus("Clipboard is unavailable; use the coordinates shown above."); }
      });
      root.append(copyCoordinates);
    }
    renderLifecycleActions(site, root);
    renderSiteEditor(site, root);
    renderObservations(site, root);
  } catch (error) { if (!isStaleRequest(error)) text($("site-detail"), error.message); }
}

async function readImport(file) {
  const requestEpoch = state.authEpoch;
  const generation = state.importGeneration;
  try {
    const content = await file.text();
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration) return;
    state.pendingImport = JSON.parse(content);
    state.previewHash = null;
    $("preview-import").disabled = false;
    $("commit-import").disabled = true;
    text($("import-result"), "JSON loaded. Preview before commit.");
  } catch (error) {
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration) return;
    state.pendingImport = null;
    $("preview-import").disabled = true;
    $("commit-import").disabled = true;
    text($("import-result"), `Invalid JSON: ${error.message}`);
  }
}

async function previewImport() {
  if (!state.pendingImport || state.importRequestInFlight) return;
  const requestEpoch = state.authEpoch;
  const generation = state.importGeneration;
  const pendingImport = state.pendingImport;
  state.importRequestInFlight = true;
  const button = $("preview-import"); button.disabled = true; text(button, "Previewing...");
  try {
    const result = await api("/api/admin/imports/preview", { method: "POST", body: JSON.stringify(pendingImport) });
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration || pendingImport !== state.pendingImport) return;
    state.previewHash = result.preview_hash;
    renderImportPreview(result);
    $("commit-import").disabled = false;
  } catch (error) {
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration || pendingImport !== state.pendingImport) return;
    if (!isStaleRequest(error)) { text($("import-result"), error.message); $("commit-import").disabled = true; }
  }
  finally {
    if (requestEpoch === state.authEpoch && generation === state.importGeneration) {
      state.importRequestInFlight = false;
      text(button, "Preview");
      button.disabled = !state.pendingImport;
    }
  }
}

async function commitImport() {
  if (!state.pendingImport || !state.previewHash || state.importRequestInFlight) return;
  const requestEpoch = state.authEpoch;
  const generation = state.importGeneration;
  const pendingImport = state.pendingImport;
  const previewHash = state.previewHash;
  state.importRequestInFlight = true;
  const button = $("commit-import"); button.disabled = true; text(button, "Committing...");
  $("import-file").disabled = true;
  try {
    const result = await api("/api/admin/imports/commit", { method: "POST", headers: { "X-Import-Preview": previewHash }, body: JSON.stringify(pendingImport) });
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration || pendingImport !== state.pendingImport) return;
    text($("import-result"), JSON.stringify(result, null, 2));
    state.pendingImport = null;
    state.previewHash = null;
    await loadSites();
  } catch (error) { if (requestEpoch === state.authEpoch && generation === state.importGeneration && !isStaleRequest(error)) text($("import-result"), error.message); }
  finally {
    if (requestEpoch === state.authEpoch && generation === state.importGeneration) {
      state.importRequestInFlight = false;
      $("import-file").disabled = false;
      text(button, "Commit");
      button.disabled = !state.pendingImport || !state.previewHash;
    }
  }
}

async function loadCandidates() {
  if (!state.token) return;
  const requestEpoch = state.authEpoch;
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
    if (requestEpoch !== state.authEpoch) return;
    cacheSites(candidates);
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
      text(meta, `${site.site_kind} | ${site.confidence || "unknown"} | ${uncertainty} | ${accessLabel(site.access)} | ${sourceCount} source${sourceCount === 1 ? "" : "s"}`); item.append(meta);
      if (site.warnings?.length) {
        const warnings = document.createElement("div"); warnings.className = "warning";
        text(warnings, `${site.warnings.length} warning${site.warnings.length === 1 ? "" : "s"}`); item.append(warnings);
      }
      const actions = document.createElement("div"); actions.className = "candidate-actions";
      const details = document.createElement("button"); details.className = "small"; text(details, "Details");
      details.addEventListener("click", () => loadDetail(site.id)); actions.append(details);
      [["Mark researched", "research", ""], ["Reject", "reject", "danger"]].forEach(([label, action, style]) => {
        const button = document.createElement("button"); button.className = `small ${style}`; text(button, label);
        button.addEventListener("click", () => runReviewAction(site.id, action)); actions.append(button);
      });
      item.append(actions); list.append(item);
    });
  } catch (error) { if (!isStaleRequest(error)) text($("candidate-list"), error.message); }
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
    const meta = document.createElement("div"); meta.className = "site-meta"; text(meta, `${site.confidence || "unknown"} | ${uncertainty} | ${accessLabel(site.access)}`); item.append(meta);
    const actions = document.createElement("div"); actions.className = "site-actions";
    const details = document.createElement("button"); details.className = "small"; details.type = "button"; text(details, "Details"); details.addEventListener("click", () => loadDetail(site.id));
    const add = document.createElement("button"); add.className = "small"; add.type = "button"; text(add, state.routeSiteIds.includes(site.id) ? "Added" : "Add route"); add.disabled = state.routeSiteIds.includes(site.id); add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add); item.append(actions); list.append(item);
  });
}

async function loadFieldPriority() {
  if (!state.token) return;
  const requestEpoch = state.authEpoch;
  try {
    state.prioritySites = await api("/api/field-priority?limit=12");
    if (requestEpoch !== state.authEpoch) return;
    cacheSites(state.prioritySites);
    renderFieldPriority();
  } catch (error) { if (!isStaleRequest(error)) text($("field-priority-list"), error.message); }
}

function formatRouteDistance(distance) {
  return distance >= 1000 ? `${(distance / 1000).toFixed(1)} km` : `${Math.round(distance)} m`;
}

function formatRouteDuration(duration) {
  return `${Math.round(duration / 60)} min`;
}

function renderRouteHistory() {
  const list = $("route-history-list"); list.replaceChildren();
  text($("route-history-summary"), `${state.routes.length} saved route${state.routes.length === 1 ? "" : "s"}.`);
  if (!state.routes.length) {
    const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "No saved routes."); list.append(empty); return;
  }
  state.routes.forEach((route) => {
    const item = document.createElement("article"); item.className = "route-item";
    const name = document.createElement("h3"); text(name, route.name); item.append(name);
    const meta = document.createElement("div"); meta.className = "site-meta";
    text(meta, `${new Date(route.created_at).toLocaleString()} | ${formatRouteDistance(route.distance_m)} | ${formatRouteDuration(route.duration_s)}`); item.append(meta);
    const load = document.createElement("button"); load.className = "small"; load.type = "button"; text(load, "Load route"); load.addEventListener("click", () => loadRoute(route.id)); item.append(load);
    list.append(item);
  });
}

async function loadRoutes() {
  if (!state.token) return;
  const requestEpoch = state.authEpoch;
  try {
    state.routes = await api("/api/routes?limit=20");
    if (requestEpoch !== state.authEpoch) return;
    renderRouteHistory();
  } catch (error) { if (!isStaleRequest(error)) text($("route-history-list"), error.message); }
}

function renderRouteResult(result) {
  revokeGpxObjectUrls();
  const root = $("route-result"); root.replaceChildren();
  const summary = document.createElement("div"); text(summary, `${formatRouteDistance(result.distance_m)} | ${formatRouteDuration(result.duration_s)}`); root.append(summary);
  (result.warnings || []).forEach((warning) => { const p = document.createElement("p"); p.className = "warning"; text(p, warning); root.append(p); });
  const cautionSites = state.routeSiteIds.map(siteById).filter((site) => site && site.access !== "public");
  if (cautionSites.length) {
    const warning = document.createElement("p"); warning.className = "warning";
    text(warning, `Access is not established for: ${cautionSites.map((site) => site.name).join(", ")}. Use public approaches only.`); root.append(warning);
  }
  const href = URL.createObjectURL(new Blob([result.gpx], { type: "application/gpx+xml" }));
  state.gpxObjectUrls.add(href);
  const download = document.createElement("a");
  download.href = href;
  download.download = "bunkerkartet-route.gpx";
  text(download, "Download GPX"); root.append(download);
}

async function loadRoute(id) {
  const requestEpoch = state.authEpoch;
  try {
    const result = await api(`/api/routes/${id}`);
    if (requestEpoch !== state.authEpoch) return;
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    updateRouteStart(result.start, "saved route start");
    state.routeSiteIds = result.waypoints.map((point) => [...state.siteCache.values()].find((site) =>
      site.latitude != null && Math.abs(site.latitude - point.lat) < 0.000001 &&
      site.longitude != null && Math.abs(site.longitude - point.lon) < 0.000001
    )?.id).filter((siteId) => siteId != null);
    renderRouteStops();
    renderRouteResult(result);
    setStatus(`Loaded ${result.name}.`);
  } catch (error) { if (!isStaleRequest(error)) text($("route-result"), error.message); }
}

async function downloadGeoJSON() {
  const requestEpoch = state.authEpoch;
  try {
    const response = await fetch("/api/sites.geojson", { headers: { Authorization: `Bearer ${state.token}` } });
    if (requestEpoch !== state.authEpoch) throw new DOMException("Utdatert forespørsel", "AbortError");
    if (!response.ok) {
      if (response.status === 401) {
        clearAuthenticatedData();
        setStatus("Authentication failed; private workspace cleared.");
        return;
      }
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${response.status})`);
    }
    const blob = await response.blob();
    if (requestEpoch !== state.authEpoch) throw new DOMException("Utdatert forespørsel", "AbortError");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    state.gpxObjectUrls.add(link.href);
    link.download = "bunkerkartet-sites.geojson";
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); state.gpxObjectUrls.delete(link.href); }, 1000);
    setStatus("GeoJSON downloaded.");
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

function updateRouteStart(point, label) {
  state.start = point;
  if (startMarker) startMarker.remove();
  startMarker = L.circleMarker([point.lat, point.lon], { color: "#c65d2e", fillColor: "#fff", fillOpacity: 1, radius: 8, weight: 3 }).addTo(map).bindTooltip("Route start");
  text($("route-start"), `Start: ${label || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`}`);
}

function addRouteSite(id) {
  if (!state.routeSiteIds.includes(id)) state.routeSiteIds.push(id);
  renderRouteStops(); renderSiteList(); renderMap(false);
}

function renderRouteStops() {
  const list = $("route-stops"); list.replaceChildren();
  state.routeSiteIds.forEach((id, index) => {
    const site = siteById(id); if (!site) return;
    const item = document.createElement("li"); item.className = "route-stop";
    const name = document.createElement("span"); name.className = "route-stop-name"; text(name, site.name);
    const access = document.createElement("span"); access.className = "route-stop-meta"; text(access, accessLabel(site.access));
    const up = document.createElement("button"); up.className = "small"; up.title = "Move earlier"; text(up, "Up"); up.disabled = index === 0;
    up.addEventListener("click", () => { [state.routeSiteIds[index - 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index - 1]]; renderRouteStops(); });
    const down = document.createElement("button"); down.className = "small"; down.title = "Move later"; text(down, "Down"); down.disabled = index === state.routeSiteIds.length - 1;
    down.addEventListener("click", () => { [state.routeSiteIds[index + 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index + 1]]; renderRouteStops(); });
    const remove = document.createElement("button"); remove.className = "small"; remove.title = "Remove stop"; text(remove, "Remove");
    remove.addEventListener("click", () => { state.routeSiteIds.splice(index, 1); renderRouteStops(); renderSiteList(); renderMap(); });
    item.append(name, access, up, down, remove); list.append(item);
  });
  $("create-route").disabled = !state.start || state.routeSiteIds.length === 0;
}

async function createRoute() {
  if (!state.start || state.routeSiteIds.length === 0 || state.routeRequestInFlight) return;
  const routeSites = state.routeSiteIds.map(siteById).filter((site) => site && site.latitude != null && site.longitude != null);
  if (routeSites.length !== state.routeSiteIds.length) {
    setStatus("Some selected stops are no longer available with coordinates.");
    return;
  }
  const waypoints = routeSites.map((site) => ({ lat: site.latitude, lon: site.longitude }));
  const requestEpoch = state.authEpoch;
  state.routeRequestInFlight = true;
  const button = $("create-route"); button.disabled = true; text(button, "Creating route...");
  try {
    const name = $("route-name").value.trim() || "Trondheim field route";
    const result = await api("/api/routes", { method: "POST", body: JSON.stringify({ name, start: state.start, waypoints, waypoint_names: routeSites.map((site) => site.name) }) });
    if (requestEpoch !== state.authEpoch) return;
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    renderRouteResult(result);
    await loadRoutes();
    setStatus(`Created ${result.name}.`);
  } catch (error) { if (!isStaleRequest(error)) text($("route-result"), error.message); }
  finally {
    if (requestEpoch === state.authEpoch) {
      state.routeRequestInFlight = false;
      text(button, "Create route");
      renderRouteStops();
    }
  }
}

$("auth-form").addEventListener("submit", (event) => { event.preventDefault(); loadSites(); });
$("lock-app").addEventListener("click", () => { clearAuthenticatedData(); setStatus("Arbeidsflate låst."); });
$("refresh-sites").addEventListener("click", loadSites);
$("status-filter").addEventListener("change", loadSites);
$("kind-filter").addEventListener("change", loadSites);
$("access-filter").addEventListener("change", loadSites);
$("confidence-filter").addEventListener("change", loadSites);
$("site-search").addEventListener("change", loadSites);
$("download-geojson").addEventListener("click", downloadGeoJSON);
$("import-file").addEventListener("change", (event) => {
  state.importGeneration += 1;
  state.previewHash = null;
  state.importRequestInFlight = false;
  $("commit-import").disabled = true;
  text($("preview-import"), "Preview");
  if (event.target.files[0]) readImport(event.target.files[0]);
});
$("preview-import").addEventListener("click", previewImport);
$("commit-import").addEventListener("click", commitImport);
$("refresh-candidates").addEventListener("click", loadCandidates);
$("refresh-field-priority").addEventListener("click", loadFieldPriority);
$("refresh-routes").addEventListener("click", loadRoutes);
$("review-confidence-filter").addEventListener("change", loadCandidates);
$("review-access-filter").addEventListener("change", loadCandidates);
$("review-uncertainty-filter").addEventListener("change", loadCandidates);
$("review-source-filter").addEventListener("change", loadCandidates);
$("pick-start").addEventListener("click", () => { state.pickingStart = true; setStatus("Click the map to set the route start."); });
$("use-location").addEventListener("click", () => {
  if (!navigator.geolocation) {
    text($("location-status"), "Location is not available in this browser.");
    setStatus("Location is not available in this browser.");
    return;
  }
  text($("location-status"), "Requesting current location...");
  navigator.geolocation.getCurrentPosition((position) => {
    const point = { lat: position.coords.latitude, lon: position.coords.longitude };
    updateRouteStart(point, "current location"); map.setView([point.lat, point.lon], 15);
    text($("location-status"), "Current location set as route start.");
    setStatus("Current location set as route start.");
  }, () => {
    text($("location-status"), "Could not read current location.");
    setStatus("Could not read current location.");
  });
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
