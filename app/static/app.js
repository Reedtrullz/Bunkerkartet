const state = {
  token: "",
  sites: [],
  pendingImport: null,
  routeSiteIds: [],
  start: null,
  pickingStart: false,
};

const map = L.map("map").setView([63.4305, 10.3951], 12);
L.tileLayer(
  "https://cache.kartverket.no/v1/wmts/1.0.0/topo/default/webmercator/{z}/{y}/{x}.png",
  { maxZoom: 18, attribution: "&copy; Kartverket" }
).addTo(map);
const markerLayer = L.layerGroup().addTo(map);
const uncertaintyLayer = L.layerGroup().addTo(map);
let routeLayer = null;
let startMarker = null;

const $ = (id) => document.getElementById(id);
const text = (node, value) => { node.textContent = value ?? ""; return node; };

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

function siteById(id) { return state.sites.find((site) => site.id === id); }

function renderMap() {
  markerLayer.clearLayers();
  uncertaintyLayer.clearLayers();
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
    text(meta, `${site.site_kind} | ${site.status}`);
    popup.append(meta);
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    text(details, "Details");
    details.addEventListener("click", () => { marker.closePopup(); loadDetail(site.id); });
    const add = document.createElement("button");
    add.className = "small";
    text(add, state.routeSiteIds.includes(site.id) ? "Added" : "Add route");
    add.disabled = state.routeSiteIds.includes(site.id);
    add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add);
    popup.append(actions);
    marker.bindPopup(popup);
    marker.on("click", () => loadDetail(site.id));
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
    text(badge, site.status);
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
    text(add, state.routeSiteIds.includes(site.id) ? "Added" : "Add route");
    add.disabled = state.routeSiteIds.includes(site.id);
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
    setStatus(`${state.sites.length} site${state.sites.length === 1 ? "" : "s"} loaded.`);
  } catch (error) { setStatus(error.message); }
}

async function loadDetail(id) {
  try {
    const site = await api(`/api/sites/${id}`);
    const root = $("site-detail");
    root.replaceChildren();
    const copy = document.createElement("dl");
    copy.className = "detail-copy";
    [["Name", site.name], ["Type", site.site_kind], ["Status", site.status],
      ["Precision", `${site.precision}${site.uncertainty_m == null ? "" : ` (${site.uncertainty_m} m)`}`],
      ["Access", site.access], ["Basis", site.location_basis], ["Condition", site.condition || "Unknown"]]
      .forEach(([label, value]) => {
        const dt = document.createElement("dt"); text(dt, label);
        const dd = document.createElement("dd"); text(dd, value); copy.append(dt, dd);
      });
    if (site.short_rationale) {
      const rationale = document.createElement("p"); text(rationale, site.short_rationale); copy.append(rationale);
    }
    if (site.warnings?.length) {
      const warning = document.createElement("p"); warning.className = "warning";
      text(warning, site.warnings.join(" | ")); copy.append(warning);
    }
    const sourcesTitle = document.createElement("dt"); text(sourcesTitle, "Sources"); copy.append(sourcesTitle);
    const sources = document.createElement("dd");
    const sourceList = document.createElement("ul"); sourceList.className = "source-list";
    (site.sources || []).forEach((source) => {
      const li = document.createElement("li");
      const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer";
      text(link, source.title || source.url); li.append(link);
      const excerpt = document.createElement("div"); excerpt.className = "site-meta"; text(excerpt, source.excerpt); li.append(excerpt);
      sourceList.append(li);
    });
    sources.append(sourceList); copy.append(sources); root.append(copy);
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
    const candidates = await api("/api/review/candidates");
    const list = $("candidate-list"); list.replaceChildren();
    if (!candidates.length) {
      const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "No candidates waiting for review."); list.append(empty); return;
    }
    candidates.forEach((site) => {
      const item = document.createElement("article"); item.className = "candidate-item";
      const title = document.createElement("strong"); text(title, site.name); item.append(title);
      const meta = document.createElement("div"); meta.className = "site-meta"; text(meta, `${site.site_kind} | ${site.precision}`); item.append(meta);
      const actions = document.createElement("div"); actions.className = "candidate-actions";
      [["Accept", "accept", ""], ["Reject", "reject", "danger"]].forEach(([label, action, style]) => {
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
  const waypoints = state.routeSiteIds.map(siteById).filter(Boolean).map((site) => ({ lat: site.latitude, lon: site.longitude }));
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
}

$("admin-token").addEventListener("keydown", (event) => { if (event.key === "Enter") loadSites(); });
$("load-sites").addEventListener("click", loadSites);
$("refresh-sites").addEventListener("click", loadSites);
$("status-filter").addEventListener("change", loadSites);
$("kind-filter").addEventListener("change", loadSites);
$("import-file").addEventListener("change", (event) => { if (event.target.files[0]) readImport(event.target.files[0]); });
$("preview-import").addEventListener("click", previewImport);
$("commit-import").addEventListener("click", commitImport);
$("refresh-candidates").addEventListener("click", loadCandidates);
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
