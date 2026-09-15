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
  detailOrigin: null,
  detailGeneration: 0,
  navigationGeneration: 0,
  surface: "map",
};

const DEFAULT_ROUTE_START = { lat: 63.4305, lon: 10.3951 };

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
  candidate: "Kandidat",
  approximate: "Omtrentlig",
  likely: "Kildegjennomgått",
  trusted: "Bekreftet",
  "field-verified": "Feltverifisert",
  "destroyed-or-filled": "Ødelagt eller fylt igjen",
  rejected: "Avvist",
};
const ACCESS_LABELS = {
  public: "Offentlig tilnærming",
  private: "Privat",
  restricted: "Begrenset",
  unknown: "Tilgang ukjent",
  permission_required: "Tillatelse kreves",
  dangerous: "Farlig",
  unsafe: "Utrygt",
};
const VALUE_LABELS = {
  unknown: "Ukjent", low: "Lav", medium: "Middels", high: "Høy",
  exact: "Nøyaktig", approximate: "Omtrentlig",
  explicit_coordinate: "Eksplisitt koordinat", address: "Adresse", map_reference: "Kartreferanse",
  landmark_description: "Landemerke", llm_inference: "Modellbasert slutning",
  feature: "Objekt", entrance: "Inngang", viewpoint: "Utsiktspunkt", public: "Offentlig",
};
const ACTION_LABELS = {
  new: "Ny", update_candidate: "Oppdater kandidat", preserve_trusted: "Bevar vurdert",
  created: "Opprettet", updated_candidate: "Kandidat oppdatert", preserved_reviewed: "Vurdert bevart",
};

const statusLabel = (status) => STATUS_LABELS[status] || status;
const accessLabel = (access) => ACCESS_LABELS[access] || access;
const outcomeLabel = (outcome) => ({
  found: "Funnet", not_found: "Ikke funnet", inaccessible: "Utilgjengelig", needs_follow_up: "Må følges opp",
}[outcome] || outcome);
const siteDisplayName = (site) => site.enrichment?.display_name || site.name;
const siteDisplayKind = (site) => site.enrichment?.kind_label || site.site_kind;
const enrichmentCertaintyLabel = (certainty) => ({
  supported: "Kildestøttet", uncertain: "Uavklart", unknown: "Ikke dokumentert", registered: "Registrert",
}[certainty] || certainty);

const importedCoordinateRationale = (site) => {
  const match = /^krigskart:(\d+)$/.exec(site.external_key || "");
  const expected = match && `Coordinate copied from KrigsKart map marker #${match[1]}; the source point is a starting area for review, not a field-verified entrance or footprint.`;
  return site.enrichment && expected === site.short_rationale ? site.short_rationale : null;
};
const importedCoordinateWarning = "Candidate point transcribed from a public map/source; coordinate, identity, condition, and access require independent verification.";

function appendEnrichmentClaims(parent, claims = []) {
  if (!claims.length) {
    const empty = document.createElement("p"); empty.className = "site-meta"; text(empty, "Ikke beriket i kildeunderlaget."); parent.append(empty);
    return;
  }
  const list = document.createElement("ul"); list.className = "enrichment-claims";
  claims.forEach((claim) => {
    const item = document.createElement("li"); item.className = `enrichment-claim certainty-${claim.certainty}`;
    const label = document.createElement("span"); label.className = "badge"; text(label, enrichmentCertaintyLabel(claim.certainty)); item.append(label);
    const statement = document.createElement("p"); text(statement, claim.text); item.append(statement);
    if (claim.sources?.length) {
      const sourceList = document.createElement("ul"); sourceList.className = "claim-sources";
      claim.sources.forEach((source) => {
        const sourceItem = document.createElement("li");
        const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; text(link, source.title); sourceItem.append(link);
        if (source.accessed_at) { const accessed = document.createElement("span"); accessed.className = "site-meta"; text(accessed, ` lest ${source.accessed_at}`); sourceItem.append(accessed); }
        sourceList.append(sourceItem);
      });
      item.append(sourceList);
    }
    list.append(item);
  });
  parent.append(list);
}

function appendEnrichmentSection(parent, title, claims) {
  const section = document.createElement("section"); section.className = "detail-section enrichment-section";
  const heading = document.createElement("h3"); text(heading, title); section.append(heading);
  appendEnrichmentClaims(section, claims);
  parent.append(section);
}

function renderEnrichment(site, root) {
  const enrichment = site.enrichment;
  if (enrichment) {
    const identity = document.createElement("p"); identity.className = "site-meta enrichment-identity";
    text(identity, `${enrichment.kind_label} | kildeunderlag kontrollert ${enrichment.reviewed_at}`); root.append(identity);
  }
  appendEnrichmentSection(root, "Om stedet", enrichment?.about);
  if (site.short_rationale && !importedCoordinateRationale(site)) {
    const registered = document.createElement("p"); registered.className = "site-meta"; text(registered, `Registrert beskrivelse: ${site.short_rationale}`); root.append(registered);
  }
  const currentClaims = enrichment?.present_day || (site.condition ? [{ certainty: "registered", text: `Registrert tilstand: ${site.condition}` }] : undefined);
  appendEnrichmentSection(root, "Hva finnes her i dag", currentClaims);
  const access = document.createElement("section"); access.className = "detail-section enrichment-section";
  const accessHeading = document.createElement("h3"); text(accessHeading, "Besøk og tilgang"); access.append(accessHeading);
  const physicalHeading = document.createElement("h4"); text(physicalHeading, "Fysisk tilgjengelighet"); access.append(physicalHeading);
  appendEnrichmentClaims(access, enrichment?.visit_access?.physical_access);
  const rulesHeading = document.createElement("h4"); text(rulesHeading, "Adgangsregler"); access.append(rulesHeading);
  appendEnrichmentClaims(access, enrichment?.visit_access?.access_rules);
  if (!enrichment) {
    const registeredAccess = document.createElement("p"); registeredAccess.className = "site-meta"; text(registeredAccess, `Registrert tilgangsfelt: ${accessLabel(site.access)}. Dette er ikke en kildebasert adgangstillatelse.`); access.append(registeredAccess);
  }
  root.append(access);
  appendEnrichmentSection(root, "Usikkerhet", enrichment?.uncertainty);
}

function locationCategory(siteKind) {
  const kind = String(siteKind || "").toLowerCase();
  if (kind.includes("pow") || kind.includes("fangeleir") || kind.includes("prisoner")) {
    return { key: "pow", label: "Krigsfangeleir", glyph: "P" };
  }
  if (kind.includes("war grave") || kind.includes("grave")) {
    return { key: "grave", label: "Krigsgrav", glyph: "G" };
  }
  if (kind.includes("war memorial") || kind.includes("memorial") || kind.includes("monument")) {
    return { key: "memorial", label: "Krigsminnesmerke", glyph: "M" };
  }
  if (kind.includes("cave") || kind.includes("tunnel")) {
    return { key: "cave", label: "Hule / tunnel", glyph: "C" };
  }
  if (["bunker", "fort", "battery", "searchlight", "observation", "communications"].some((term) => kind.includes(term))) {
    return { key: "bunker", label: "Bunker / militærstilling", glyph: "B" };
  }
  return { key: "other", label: "Annet krigsminne", glyph: "O" };
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
    text(option, labels[value] || VALUE_LABELS[value] || statusLabel(value));
    option.selected = value === selected;
    select.append(option);
  });
  return select;
}

const FIELD_LABELS = {
  name: "Navn", site_kind: "Type", latitude: "Breddegrad", longitude: "Lengdegrad",
  precision: "Presisjon", uncertainty_m: "Usikkerhet", location_basis: "Stedsgrunnlag",
  note: "Observasjonsnotat", reason: "Begrunnelse", access_notes: "Tilgangsnotat",
};

function validationMessage(detail) {
  if (!Array.isArray(detail)) return typeof detail === "string" ? detail : "Forespørselen kunne ikke behandles.";
  return detail.map((item) => {
    const path = Array.isArray(item.loc) ? item.loc.at(-1) : null;
    return `${FIELD_LABELS[path] || path || "Felt"}: ${item.msg || "Ugyldig verdi"}`;
  }).join(" ");
}

function clearFieldErrors(form) {
  form.querySelectorAll(".field-error").forEach((node) => node.remove());
  form.querySelectorAll("[aria-invalid=\"true\"]").forEach((node) => node.removeAttribute("aria-invalid"));
}

function showFieldErrors(form, error) {
  clearFieldErrors(form);
  (error.details || []).forEach((item) => {
    const field = Array.isArray(item.loc) ? item.loc.at(-1) : null;
    const control = [...form.elements].find((element) => element.name === field);
    if (!control) return;
    control.setAttribute("aria-invalid", "true");
    const message = document.createElement("div");
    message.className = "field-error";
    message.setAttribute("role", "alert");
    text(message, `${FIELD_LABELS[field] || field || "Felt"}: ${item.msg || "Ugyldig verdi"}`);
    control.closest("label")?.append(message);
  });
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

function setSurface(surface, { scroll = true } = {}) {
  state.surface = surface;
  state.navigationGeneration += 1;
  document.querySelectorAll(".surface-panel").forEach((panel) => {
    panel.hidden = panel.dataset.surface !== surface;
  });
  if (typeof renderOverlapChoices === "function") renderOverlapChoices();
  document.querySelectorAll(".surface-nav button").forEach((item) => {
    item.setAttribute("aria-pressed", String(item.dataset.target === ({ map: "map-tools-panel", review: "review-panel", route: "route-panel" }[surface])));
  });
  if (scroll) document.getElementById(({ map: "map-tools-panel", review: "review-panel", route: "route-panel" }[surface]))?.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.querySelectorAll(".surface-nav button").forEach((button) => {
  button.addEventListener("click", () => setSurface(button.dataset.target === "map-tools-panel" ? "map" : button.dataset.target === "review-panel" ? "review" : "route"));
});

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
  state.detailOrigin = null;
  state.detailGeneration += 1;
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
  text($("site-detail-heading"), "Stedsdetaljer");
  text($("site-detail"), "Velg en markør eller et sted.");
  $("close-detail").hidden = true;
  $("route-result").replaceChildren();
  text($("route-start"), "Start: ingen start valgt");
  setSurface("map", { scroll: false });
  text($("location-status"), "Posisjon brukes bare når du velger «Bruk min posisjon».");
  $("import-file").value = "";
  $("import-file").disabled = false;
  $("preview-import").disabled = true;
  $("commit-import").disabled = true;
  text($("preview-import"), "Forhåndsvis");
  text($("commit-import"), "Importer");
  text($("import-result"), "");
  $("load-sites").disabled = false;
  text($("load-sites"), "Last inn kart");
  text($("create-route"), "Beregn rute");
  if (resetToken) {
    state.token = "";
    $("admin-token").value = "";
  }
}

function renderImportPreview(result) {
  const root = $("import-result");
  root.replaceChildren();
  const summary = document.createElement("p");
  text(summary, `${result.summary.total} ${result.summary.total === 1 ? "post" : "poster"}; ${result.summary.warnings} ${result.summary.warnings === 1 ? "varsel" : "varsler"}. Forhåndsvisningen er ikke en godkjenning.`);
  root.append(summary);
  result.records.forEach((record) => {
    const item = document.createElement("article");
    const heading = document.createElement("strong"); text(heading, `${record.name} — ${ACTION_LABELS[record.action] || record.action}`); item.append(heading);
    record.changes.forEach((change) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `${change.field}: ${JSON.stringify(change.before)} → ${JSON.stringify(change.after)}`); item.append(line); });
    record.preserved_fields.forEach((field) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `Bevart: ${field}`); item.append(line); });
    record.evidence.forEach((evidence) => { const line = document.createElement("div"); line.className = "site-meta"; text(line, `Kilde: ${evidence.title} — ${evidence.excerpt}`); item.append(line); });
    record.warnings.forEach((warning) => { const line = document.createElement("div"); line.className = "warning"; text(line, warning); item.append(line); });
    root.append(item);
  });
  const technical = document.createElement("details");
  const label = document.createElement("summary"); text(label, "Vis teknisk forhåndsvisning"); technical.append(label);
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
      setStatus("Autentisering mislyktes; privat arbeidsområde er tømt.");
      throw new DOMException("Utdatert forespørsel", "AbortError");
    }
    const error = new Error(validationMessage(body.detail || `Forespørselen feilet (${response.status})`));
    error.status = response.status;
    error.details = Array.isArray(body.detail) ? body.detail : [];
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

function hasReviewedPublicApproach(site) {
  return site.approach_latitude != null && site.approach_longitude != null &&
    site.approach_access === "public" && site.approach_reviewed_at != null;
}

function markDetailControl(control, siteId, origin) {
  control.dataset.detailSiteId = String(siteId);
  control.dataset.detailOrigin = origin;
  return control;
}

function openDetail(siteId, surface = state.surface, origin = "list") {
  state.detailOrigin = { siteId, surface, origin };
  loadDetail(siteId);
}

function focusDetailOrigin(origin) {
  const controls = [...document.querySelectorAll("[data-detail-site-id]")].filter((control) => {
    return control.dataset.detailSiteId === String(origin.siteId) && control.offsetParent !== null;
  });
  const control = controls.find((candidate) => candidate.dataset.detailOrigin === origin.origin) || controls[0];
  if (control) control.focus({ preventScroll: true });
}

function closeDetail() {
  const origin = state.detailOrigin || { siteId: null, surface: "map", origin: "list" };
  state.detailGeneration += 1;
  state.detailOrigin = null;
  $("detail-panel").classList.remove("is-selected");
  text($("site-detail-heading"), "Stedsdetaljer");
  text($("site-detail"), "Velg en markør eller et sted.");
  $("close-detail").hidden = true;
  setSurface(origin.surface, { scroll: false });
  if (origin.siteId != null) focusDetailOrigin(origin);
}

function renderOverlapChoices() {
  const groups = new Map();
  state.sites.filter((site) => site.latitude != null && site.longitude != null).forEach((site) => {
    const key = `${site.latitude.toFixed(6)},${site.longitude.toFixed(6)}`;
    const group = groups.get(key) || [];
    group.push(site);
    groups.set(key, group);
  });
  const overlapGroups = [...groups.values()].filter((group) => group.length > 1);
  const panel = $("overlap-panel");
  const list = $("overlap-list");
  list.replaceChildren();
  panel.hidden = state.surface !== "map" || overlapGroups.length === 0;
  overlapGroups.forEach((group) => {
    const groupBox = document.createElement("div");
    groupBox.className = "overlap-group";
    group.forEach((site) => {
      const button = document.createElement("button");
      button.type = "button";
      markDetailControl(button, site.id, "overlap");
      text(button, `${siteDisplayName(site)} — ${siteDisplayKind(site)} — ${statusLabel(site.status)}`);
      button.addEventListener("click", () => {
        openDetail(site.id, "map", "overlap");
      });
      groupBox.append(button);
    });
    list.append(groupBox);
  });
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
      alt: `${category.label}: ${siteDisplayName(site)}`,
      icon: L.divIcon({
        className: "site-marker-icon",
        html: `<span class="site-marker site-marker-${category.key} status-${statusClass(site.status)}" aria-label="${category.label}">${category.glyph}</span>`,
        iconAnchor: [14, 14],
        iconSize: [28, 28],
        popupAnchor: [0, -14],
      }),
      title: `${category.label}: ${siteDisplayName(site)}`,
    }).addTo(markerLayer);
    const markerElement = marker.getElement();
    if (markerElement) {
      markDetailControl(markerElement, site.id, "marker");
      markerElement.tabIndex = 0;
      markerElement.setAttribute("aria-label", `${category.label}: ${siteDisplayName(site)}`);
    }
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
    text(heading, siteDisplayName(site));
    popup.append(heading);
    const meta = document.createElement("div");
    meta.className = "site-meta";
    const uncertainty = site.uncertainty_m == null ? "usikkerhet ukjent" : `${Math.round(site.uncertainty_m)} m`;
    text(meta, `${siteDisplayKind(site)} | ${statusLabel(site.status)} | ${VALUE_LABELS[site.confidence] || "Ukjent"} | ${uncertainty} | ${accessLabel(site.access)}`);
    popup.append(meta);
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    markDetailControl(details, site.id, "marker");
    text(details, "Detaljer");
    details.addEventListener("click", () => { marker.closePopup(); openDetail(site.id, "map", "marker"); });
    const add = document.createElement("button");
    add.className = "small";
    const routeReady = hasReviewedPublicApproach(site);
    text(add, !routeReady ? "Ingen gjennomgått tilnærming" : state.routeSiteIds.includes(site.id) ? "Lagt til" : "Legg til rute");
    add.disabled = !routeReady || state.routeSiteIds.includes(site.id);
    add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add);
    popup.append(actions);
    marker.bindPopup(popup);
    marker.on("click", () => openDetail(site.id, "map", "marker"));
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
      const heading = document.createElement("strong"); text(heading, "Feltobservasjon"); popup.append(heading);
      const meta = document.createElement("div"); meta.className = "site-meta";
      text(meta, `${observation.observed_at} | ${outcomeLabel(observation.outcome)}`); popup.append(meta);
      const details = document.createElement("button"); details.className = "small"; details.type = "button"; text(details, "Åpne sted");
      markDetailControl(details, site.id, "marker");
      details.addEventListener("click", () => { observationMarker.closePopup(); openDetail(site.id, "map", "marker"); });
      popup.append(details);
      observationMarker.bindPopup(popup);
    });
  });
  renderOverlapChoices();
  if (fit && bounds.length) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
}

function renderSiteList() {
  const list = $("site-list");
  list.replaceChildren();
  if (!state.sites.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    text(empty, "Ingen steder passer filtrene.");
    list.append(empty);
    return;
  }
  state.sites.forEach((site) => {
    const item = document.createElement("article");
    item.className = `site-item status-${site.status}`;
    const head = document.createElement("div");
    head.className = "site-item-head";
    const name = document.createElement("h3");
    text(name, siteDisplayName(site));
    const badge = document.createElement("span");
    badge.className = "badge";
    text(badge, statusLabel(site.status));
    head.append(name, badge);
    item.append(head);
    const meta = document.createElement("div");
    meta.className = "site-meta";
    text(meta, `${siteDisplayKind(site)} | ${site.precision} | ${accessLabel(site.access)}`);
    item.append(meta);
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    markDetailControl(details, site.id, "list");
    text(details, "Detaljer");
    details.addEventListener("click", () => openDetail(site.id, "map", "list"));
    const add = document.createElement("button");
    add.className = "small";
    const routeReady = hasReviewedPublicApproach(site);
    text(add, !routeReady ? "Ingen gjennomgått tilnærming" : state.routeSiteIds.includes(site.id) ? "Lagt til" : "Legg til rute");
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
  if (!state.token) { setStatus("Skriv inn administratortokenet for å laste inn steder."); return; }
  const requestEpoch = state.authEpoch;
  const loadButton = $("load-sites"); loadButton.disabled = true; text(loadButton, "Laster inn ...");
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
    if (!state.start) updateRouteStart(DEFAULT_ROUTE_START, "Standardstart i Trondheim");
    cacheSites(state.sites);
    $("download-geojson").disabled = false;
    renderSiteList();
    renderMap(true);
    setStatus(state.sites.length
      ? `${state.sites.length} ${state.sites.length === 1 ? "sted" : "steder"} lastet inn.`
      : hasFilters
        ? "Ingen steder passer filtrene."
        : "Innlogget. Ingen steder er importert ennå.");
    await loadCandidates();
    await loadFieldPriority();
    await loadRoutes();
  } catch (error) {
    if (isStaleRequest(error)) return;
    if (error.status === 401 || error.status === 503) clearAuthenticatedData();
    setStatus(error.message);
  } finally {
    if (requestEpoch === state.authEpoch) { loadButton.disabled = false; text(loadButton, "Last inn kart"); }
  }
}

async function refreshSitesAndDetail(siteId) {
  const navigationGeneration = state.navigationGeneration;
  await loadSites();
  if (navigationGeneration !== state.navigationGeneration) return;
  await loadDetail(siteId);
}

async function runReviewAction(id, action, targetSiteId = null) {
  if (action === "reject" && !window.confirm("Avvise denne kandidaten?")) return;
  if (action === "mark_destroyed" && !window.confirm("Markere stedet som ødelagt eller fylt igjen?")) return;
  if (action === "merge" && !window.confirm("Slå sammen posten med det valgte stedet som skal bestå?")) return;
  try {
    const payload = { action };
    if (targetSiteId) payload.target_site_id = targetSiteId;
    const currentSite = state.siteCache.get(id);
    if (currentSite?.revision) payload.expected_revision = currentSite.revision;
    const result = await api(`/api/sites/${id}/review`, { method: "POST", body: JSON.stringify(payload) });
    if (result.site) state.siteCache.set(result.site.id, result.site);
    await refreshSitesAndDetail(targetSiteId || id);
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

function renderLivssyklusActions(site, root) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("h3"); text(heading, "Livssyklus"); section.append(heading);
  const actions = document.createElement("div"); actions.className = "candidate-actions";
  const transitions = {
    candidate: [["Marker som kildegjennomgått", "research", ""], ["Marker som omtrentlig", "mark_approximate", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    approximate: [["Marker som kildegjennomgått", "research", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    likely: [["Marker som feltverifisert", "field_verify", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    "field-verified": [["Bekreft", "confirm", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    trusted: [["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
  };
  (transitions[site.status] || []).forEach(([label, action, style]) => {
    const button = document.createElement("button"); button.className = `small ${style}`; button.type = "button"; text(button, label);
    button.addEventListener("click", () => runReviewAction(site.id, action)); actions.append(button);
  });
  if (site.status !== "rejected") {
    const reject = document.createElement("button"); reject.className = "small danger"; reject.type = "button"; text(reject, "Avvis");
    reject.addEventListener("click", () => runReviewAction(site.id, "reject")); actions.append(reject);
  } else {
    const restore = document.createElement("button"); restore.className = "small"; restore.type = "button"; text(restore, "Gjenopprett kandidat");
    restore.addEventListener("click", () => runReviewAction(site.id, "restore")); actions.append(restore);
  }
  section.append(actions); root.append(section);
  if (site.status === "candidate") {
    const targets = [...state.siteCache.values()]
      .filter((candidate) => candidate.id !== site.id && candidate.status !== "rejected" && candidate.merged_into_id == null)
      .sort((first, second) => first.name.localeCompare(second.name));
    if (targets.length) {
      const target = document.createElement("select");
      const placeholder = document.createElement("option"); placeholder.value = ""; text(placeholder, "Velg sted som skal bestå"); target.append(placeholder);
      targets.forEach((candidate) => { const option = document.createElement("option"); option.value = candidate.id; text(option, `${candidate.name} — ${statusLabel(candidate.status)} (#${candidate.id})`); target.append(option); });
      const merge = document.createElement("button"); merge.className = "small danger"; merge.type = "button"; merge.disabled = true; text(merge, "Slå sammen med valgt");
      target.addEventListener("change", () => { merge.disabled = !target.value; });
      merge.addEventListener("click", () => runReviewAction(site.id, "merge", Number(target.value)));
      section.append(labeledControl("Slå sammen duplikat med", target), merge);
    }
  }
}

function compactSection(title, className = "") {
  const section = document.createElement("details");
  section.className = `detail-section compact-section ${className}`;
  const summary = document.createElement("summary");
  text(summary, title);
  section.append(summary);
  section.addEventListener("toggle", () => {
    if (!section.open && section.contains(document.activeElement)) summary.focus({ preventScroll: true });
  });
  return section;
}

function renderSiteEditor(site, root) {
  const section = compactSection("Rediger sted", "site-editor");
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
  [["Navn", name], ["Type", kind], ["Sikkerhet", confidence], ["Tilgang", access], ["Presisjon", precision], ["Usikkerhet (m)", uncertainty], ["Stedsgrunnlag", basis], ["Breddegrad", latitude], ["Lengdegrad", longitude]]
    .forEach(([label, control]) => grid.append(labeledControl(label, control)));
  form.append(grid);
  const condition = inputControl("text", site.condition || ""); condition.name = "condition";
  const rationale = textareaControl(site.short_rationale || ""); rationale.name = "short_rationale";
  const observedText = textareaControl(site.observed_location_text || ""); observedText.name = "observed_location_text";
  const warnings = textareaControl((site.warnings || []).join("\n")); warnings.name = "warnings";
  form.append(labeledControl("Tilstand", condition));
  form.append(labeledControl("Begrunnelse for koordinat", rationale));
  form.append(labeledControl("Observert sted", observedText));
  form.append(labeledControl("Varsler (ett per linje)", warnings));
  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Lagre stedsendringer"); form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFieldErrors(form);
    const numberOrNull = (value) => value === "" ? null : Number(value);
    try {
      await api(`/api/sites/${site.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.value.trim(), site_kind: kind.value.trim(),
          confidence: confidence.value, access: access.value, precision: precision.value,
          uncertainty_m: numberOrNull(uncertainty.value), location_basis: basis.value,
          latitude: numberOrNull(latitude.value), longitude: numberOrNull(longitude.value),
          expected_revision: site.revision,
          condition: condition.value.trim(), short_rationale: rationale.value.trim(),
          observed_location_text: observedText.value.trim(),
          warnings: warnings.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        }),
      });
      setStatus("Stedsendringer lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) { if (!isStaleRequest(error)) { showFieldErrors(form, error); setStatus(error.message); } }
  });
  section.append(form); root.append(section);
  const approachForm = document.createElement("form"); approachForm.className = "observation-form";
  const approachBreddegrad = inputControl("number", site.approach_latitude); approachBreddegrad.step = "0.000001"; approachBreddegrad.min = "-90"; approachBreddegrad.max = "90";
  const approachLengdegrad = inputControl("number", site.approach_longitude); approachLengdegrad.step = "0.000001"; approachLengdegrad.min = "-180"; approachLengdegrad.max = "180";
  const approachTilgang = selectControl(["unknown", "public"], site.approach_access || "unknown");
  const approachNote = textareaControl(site.approach_note || ""); approachNote.required = true;
  const approachGrid = document.createElement("div"); approachGrid.className = "detail-grid";
  approachGrid.append(labeledControl("Tilnærming breddegrad", approachBreddegrad), labeledControl("Tilnærming lengdegrad", approachLengdegrad), labeledControl("Tilgang ved tilnærming", approachTilgang));
  approachForm.append(approachGrid, labeledControl("Notat om tilnærmingsvurdering", approachNote));
  const approachSave = document.createElement("button"); approachSave.className = "primary"; approachSave.type = "submit"; text(approachSave, "Lagre vurdering av offentlig tilnærming"); approachForm.append(approachSave);
  approachForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFieldErrors(approachForm);
    const numberOrNull = (value) => value === "" ? null : Number(value);
    try {
      await api(`/api/sites/${site.id}/approach`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: site.revision, latitude: numberOrNull(approachBreddegrad.value), longitude: numberOrNull(approachLengdegrad.value), access: approachTilgang.value, note: approachNote.value.trim() }),
      });
      setStatus("Tilnærmingsvurdering lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) { if (!isStaleRequest(error)) { showFieldErrors(approachForm, error); setStatus(error.message); } }
  });
  const approachHeading = document.createElement("h3"); text(approachHeading, "Vurdering av offentlig tilnærming"); section.append(approachHeading, approachForm);
  if (site.location_review_required) {
    const reviewForm = document.createElement("form"); reviewForm.className = "observation-form";
    const reason = textareaControl(); reason.required = true; reason.placeholder = "Hvorfor er den nye plasseringen vurdert?";
    const saveReview = document.createElement("button"); saveReview.className = "primary"; saveReview.type = "submit"; text(saveReview, "Marker plassering som vurdert");
    reviewForm.append(labeledControl("Begrunnelse for lokaliseringsreview", reason), saveReview);
    reviewForm.addEventListener("submit", async (event) => {
      event.preventDefault(); saveReview.disabled = true;
      clearFieldErrors(reviewForm);
      try {
        await api(`/api/sites/${site.id}/location-review`, { method: "POST", body: JSON.stringify({ reason: reason.value.trim(), expected_revision: site.revision }) });
        setStatus("Lokaliseringsreview lagret."); await refreshSitesAndDetail(site.id);
      } catch (error) { if (!isStaleRequest(error)) { showFieldErrors(reviewForm, error); setStatus(error.message); } saveReview.disabled = false; }
    });
    section.append(reviewForm);
  }
}

function renderObservations(site, root) {
  const section = compactSection("Feltobservasjoner", "observations-editor");
  const observations = document.createElement("ul"); observations.className = "observation-list";
  (site.field_observations || []).forEach((observation) => {
    const item = document.createElement("li");
    const title = document.createElement("strong"); text(title, `${observation.observed_at} | ${outcomeLabel(observation.outcome)}`); item.append(title);
    const note = document.createElement("p"); text(note, observation.note); item.append(note);
    if (observation.observed_location_text) { const location = document.createElement("div"); location.className = "site-meta"; text(location, observation.observed_location_text); item.append(location); }
    if (observation.access_notes) { const access = document.createElement("div"); access.className = "site-meta"; text(access, `Tilgang: ${observation.access_notes}`); item.append(access); }
    const observationMeta = document.createElement("div"); observationMeta.className = "site-meta";
    text(observationMeta, `Punktrolle: ${VALUE_LABELS[observation.point_role] || "Ukjent"}${observation.uncertainty_m == null ? " | radius ukjent" : ` | ${observation.uncertainty_m} m radius`}`); item.append(observationMeta);
    if (observation.latitude != null && observation.longitude != null) {
      const coordinates = document.createElement("div"); coordinates.className = "site-meta";
      text(coordinates, `Koordinater: ${observation.latitude.toFixed(5)}, ${observation.longitude.toFixed(5)}`); item.append(coordinates);
      if (observation.outcome === "found") {
        const actions = document.createElement("div"); actions.className = "candidate-actions";
        const adopt = document.createElement("button"); adopt.className = "small"; adopt.type = "button"; adopt.disabled = observation.point_role !== "feature" || observation.uncertainty_m == null; adopt.title = "Bare et objektpunkt med eksplisitt radius kan oppdatere stedets markør";
        text(adopt, "Bruk koordinat"); adopt.addEventListener("click", () => adoptObservationLocation(site.id, observation.id));
        actions.append(adopt); item.append(actions);
      }
    }
    if (observation.photo_urls?.length) {
      const photos = document.createElement("div"); photos.className = "observation-links";
      observation.photo_urls.forEach((url, index) => { const link = document.createElement("a"); link.href = url; link.target = "_blank"; link.rel = "noreferrer"; text(link, `Bilde ${index + 1}`); photos.append(link); });
      item.append(photos);
    }
    if (observation.photo_urls_status) { const withheld = document.createElement("div"); withheld.className = "site-meta"; text(withheld, observation.photo_urls_status); item.append(withheld); }
    observations.append(item);
  });
  if (!observations.children.length) { const empty = document.createElement("p"); empty.className = "empty-state"; text(empty, "Ingen feltobservasjoner er registrert."); section.append(empty); }
  else section.append(observations);

  const form = document.createElement("form"); form.className = "observation-form";
  const requestId = crypto.randomUUID();
  const localToday = new Date(); localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
  const observedAt = inputControl("date", localToday.toISOString().slice(0, 10)); observedAt.name = "observed_at"; observedAt.required = true;
  const outcome = selectControl(["found", "not_found", "inaccessible", "needs_follow_up"], "found", { found: "Funnet", not_found: "Ikke funnet", inaccessible: "Utilgjengelig", needs_follow_up: "Må følges opp" }); outcome.name = "outcome";
  const note = textareaControl(); note.name = "note"; note.required = true; note.placeholder = "Hva ble observert?";
  const latitude = inputControl("number"); latitude.name = "latitude"; latitude.step = "0.000001"; latitude.min = "-90"; latitude.max = "90";
  const longitude = inputControl("number"); longitude.name = "longitude"; longitude.step = "0.000001"; longitude.min = "-180"; longitude.max = "180";
  const pointRole = selectControl(["feature", "entrance", "viewpoint", "unknown"], "unknown"); pointRole.name = "point_role";
  const uncertainty = inputControl("number"); uncertainty.name = "uncertainty_m"; uncertainty.min = "0"; uncertainty.step = "1";
  const location = textareaControl(); location.name = "observed_location_text";
  const access = textareaControl(); access.name = "access_notes";
  const photos = textareaControl(); photos.name = "photo_urls"; photos.placeholder = "Én bilde-URL per linje";
  const grid = document.createElement("div"); grid.className = "detail-grid";
  grid.append(labeledControl("Dato", observedAt), labeledControl("Utfall", outcome), labeledControl("Punktrolle", pointRole), labeledControl("Radius (m)", uncertainty), labeledControl("Observert breddegrad", latitude), labeledControl("Observert lengdegrad", longitude));
  form.append(grid, labeledControl("Observasjonsnotat", note), labeledControl("Observert sted", location), labeledControl("Tilgangsnotat", access), labeledControl("Bilde-URL-er", photos));
  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Lagre observasjon"); form.append(save);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFieldErrors(form);
    const numberOrUndefined = (value) => value === "" ? undefined : Number(value);
    try {
      await api(`/api/sites/${site.id}/observations`, {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId,
          observed_at: observedAt.value, outcome: outcome.value, note: note.value.trim(),
          latitude: numberOrUndefined(latitude.value), longitude: numberOrUndefined(longitude.value),
          point_role: pointRole.value, uncertainty_m: numberOrUndefined(uncertainty.value),
          observed_location_text: location.value.trim() || undefined, access_notes: access.value.trim() || undefined,
          photo_urls: photos.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        }),
      });
      setStatus("Feltobservasjon lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) { if (!isStaleRequest(error)) { showFieldErrors(form, error); setStatus(error.message); } }
  });
  section.append(form); root.append(section);
}

async function adoptObservationLocation(siteId, observationId) {
  if (!window.confirm("Bruke denne feltobservasjonens koordinat for stedet?")) return;
  try {
    const site = state.siteCache.get(siteId);
    await api(`/api/sites/${siteId}/observations/${observationId}/adopt-location`, { method: "POST", body: JSON.stringify({ expected_revision: site?.revision }) });
    setStatus("Observasjonskoordinat tatt i bruk.");
    await refreshSitesAndDetail(siteId);
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

async function loadDetail(id) {
  const requestEpoch = state.authEpoch;
  const detailGeneration = ++state.detailGeneration;
  if (!state.detailOrigin || state.detailOrigin.siteId !== id) {
    state.detailOrigin = { siteId: id, surface: state.surface, origin: state.surface === "review" ? "candidate" : "list" };
  }
  setSurface("review", { scroll: false });
  const navigationGeneration = state.navigationGeneration;
  const panel = $("detail-panel");
  const root = $("site-detail");
  panel.classList.add("is-selected");
  $("close-detail").hidden = false;
  text($("close-detail"), state.detailOrigin.surface === "map" ? "Tilbake til kart" : "Tilbake til liste");
  text(root, "Laster stedsdetaljer ...");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  $("site-detail-heading").focus({ preventScroll: true });
  try {
    const [site, events] = await Promise.all([api(`/api/sites/${id}`), api(`/api/sites/${id}/events?limit=50`)]);
    if (requestEpoch !== state.authEpoch || detailGeneration !== state.detailGeneration || navigationGeneration !== state.navigationGeneration) return;
    state.siteCache.set(site.id, site);
    text($("site-detail-heading"), `Stedsdetaljer: ${siteDisplayName(site)}`);
    root.replaceChildren();
    renderEnrichment(site, root);
    const coordinates = site.latitude == null ? "Ukjent" : `${site.latitude.toFixed(5)}, ${site.longitude.toFixed(5)}`;
    const dataBasis = document.createElement("details"); dataBasis.dataset.detailSection = "data-basis";
    const dataSummary = document.createElement("summary"); text(dataSummary, "Datagrunnlag"); dataBasis.append(dataSummary);
    const copy = document.createElement("dl"); copy.className = "detail-copy";
    [["Importnøkkel", site.external_key], ["Rånavn", site.name], ["Type", site.site_kind], ["Status", statusLabel(site.status)],
      ["Sikkerhet", VALUE_LABELS[site.confidence] || site.confidence || "Ukjent"],
      ["Koordinater", coordinates],
      ["Presisjon", `${VALUE_LABELS[site.precision] || site.precision}${site.uncertainty_m == null ? "" : ` (${site.uncertainty_m} m)`}`],
      ["Tilgang", accessLabel(site.access)], ["Grunnlag", VALUE_LABELS[site.location_basis] || site.location_basis], ["Tilstand", site.condition || "Ukjent"],
      ["Observert sted", site.observed_location_text || "Ukjent"]]
      .forEach(([label, value]) => {
        const field = document.createElement("div"); field.className = "detail-fact";
        if (["Navn", "Observert sted"].includes(label)) field.classList.add("detail-fact-wide");
        const dt = document.createElement("dt"); text(dt, label);
        const dd = document.createElement("dd"); text(dd, value);
        field.append(dt, dd); copy.append(field);
      });
    dataBasis.append(copy);
    const foldedImportMetadata = [importedCoordinateRationale(site), ...(site.enrichment ? (site.warnings || []).filter((warning) => warning === importedCoordinateWarning) : [])].filter(Boolean);
    const visibleWarnings = (site.warnings || []).filter((warning) => !site.enrichment || warning !== importedCoordinateWarning);
    if (visibleWarnings.length) { const warningTitle = document.createElement("h3"); text(warningTitle, "Registrerte varsler"); const warning = document.createElement("p"); warning.className = "warning"; text(warning, visibleWarnings.join(" | ")); root.append(warningTitle, warning); }
    if (foldedImportMetadata.length) {
      const importTitle = document.createElement("h3"); text(importTitle, "Importinformasjon");
      const importMetadata = document.createElement("section"); importMetadata.className = "detail-section"; importMetadata.append(importTitle);
      foldedImportMetadata.forEach((entry) => { const paragraph = document.createElement("p"); text(paragraph, entry); importMetadata.append(paragraph); });
      dataBasis.append(importMetadata);
    }
    const sourcesTitle = document.createElement("h3"); text(sourcesTitle, "Kilder");
    const sources = document.createElement("section"); sources.className = "detail-section"; sources.append(sourcesTitle); const sourceList = document.createElement("ul"); sourceList.className = "source-list";
    (site.sources || []).forEach((source) => { const li = document.createElement("li"); if (source.url) { const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; text(link, source.title || source.url); li.append(link); } else { const withheld = document.createElement("span"); text(withheld, source.title || "Referanse holdt tilbake"); li.append(withheld); } const sourceMeta = document.createElement("div"); sourceMeta.className = "site-meta"; text(sourceMeta, [source.source_type || "kilde", source.published_at && `publisert ${source.published_at}`, source.accessed_at && `lest ${source.accessed_at}`, source.url_status].filter(Boolean).join(" | ")); li.append(sourceMeta); const excerpt = document.createElement("div"); excerpt.className = "site-meta"; text(excerpt, source.excerpt); li.append(excerpt); sourceList.append(li); });
    sources.append(sourceList); dataBasis.append(sources); root.append(dataBasis);
    if (site.relations?.length) {
      const relationsTitle = document.createElement("h3"); text(relationsTitle, "Relaterte steder"); root.append(relationsTitle);
      const relationList = document.createElement("ul"); relationList.className = "source-list";
      site.relations.forEach((relation) => {
        const item = document.createElement("li");
        const target = relation.related_name || "Ikke importert";
        text(item, `${target} (${relation.related_external_key}) — ${relation.related_status === "not_imported" ? "ikke importert" : statusLabel(relation.related_status)}`);
        relationList.append(item);
      });
      root.append(relationList);
    }
    if (events.length) {
      const history = document.createElement("details");
      const historySummary = document.createElement("summary"); text(historySummary, "Historikk"); history.append(historySummary);
      const historyList = document.createElement("ul"); historyList.className = "source-list";
      events.forEach((event) => { const item = document.createElement("li"); text(item, `${event.created_at} — ${event.event_type}: ${JSON.stringify(event.payload)}`); historyList.append(item); });
      history.append(historyList); root.append(history);
    }
    if (site.latitude != null && site.longitude != null) {
      const copyKoordinater = document.createElement("button"); copyKoordinater.className = "small"; copyKoordinater.type = "button"; text(copyKoordinater, "Kopier koordinater");
      copyKoordinater.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(`${site.latitude}, ${site.longitude}`); setStatus("Koordinater kopiert."); }
        catch { setStatus("Utklippstavlen er ikke tilgjengelig; bruk koordinatene som vises over."); }
      });
      root.append(copyKoordinater);
    }
    renderLivssyklusActions(site, root);
    renderSiteEditor(site, root);
    renderObservations(site, root);
  } catch (error) {
    if (!isStaleRequest(error) && requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && navigationGeneration === state.navigationGeneration) {
      text($("site-detail"), error.message);
    }
  }
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
    text($("import-result"), "JSON lastet. Forhåndsvis før import.");
  } catch (error) {
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration) return;
    state.pendingImport = null;
    $("preview-import").disabled = true;
    $("commit-import").disabled = true;
    text($("import-result"), `Ugyldig JSON: ${error.message}`);
  }
}

async function previewImport() {
  if (!state.pendingImport || state.importRequestInFlight) return;
  const requestEpoch = state.authEpoch;
  const generation = state.importGeneration;
  const pendingImport = state.pendingImport;
  state.importRequestInFlight = true;
  const button = $("preview-import"); button.disabled = true; text(button, "Forhåndsviser ...");
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
      text(button, "Forhåndsvis");
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
  const button = $("commit-import"); button.disabled = true; text(button, "Importerer ...");
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
      text(button, "Importer");
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
    text($("candidate-summary"), `${candidates.length} ${candidates.length === 1 ? "kandidat" : "kandidater"} i dette utvalget.`);
    if (!candidates.length) {
      const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "Ingen kandidater venter på vurdering."); list.append(empty); return;
    }
    candidates.forEach((site) => {
      const item = document.createElement("article"); item.className = "candidate-item";
      const title = document.createElement("strong"); text(title, site.name); item.append(title);
      const uncertainty = site.uncertainty_m == null ? "usikkerhet ukjent" : `${Math.round(site.uncertainty_m)} m`;
      const sourceCount = (site.sources || []).length;
      const meta = document.createElement("div"); meta.className = "site-meta";
      text(meta, `${site.site_kind} | ${VALUE_LABELS[site.confidence] || "Ukjent"} | ${uncertainty} | ${accessLabel(site.access)} | ${sourceCount} ${sourceCount === 1 ? "kilde" : "kilder"}`); item.append(meta);
      if (site.warnings?.length) {
        const warnings = document.createElement("div"); warnings.className = "warning";
        text(warnings, `${site.warnings.length} ${site.warnings.length === 1 ? "varsel" : "varsler"}`); item.append(warnings);
      }
      const actions = document.createElement("div"); actions.className = "candidate-actions";
      const details = document.createElement("button"); details.className = "small"; markDetailControl(details, site.id, "candidate"); text(details, "Detaljer");
      details.addEventListener("click", () => openDetail(site.id, "review", "candidate")); actions.append(details);
      [["Marker som kildegjennomgått", "research", ""], ["Avvis", "reject", "danger"]].forEach(([label, action, style]) => {
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
  text($("field-priority-summary"), `${sites.length} ${sites.length === 1 ? "offentlig sted" : "offentlige steder"} i feltutvalget.`);
  if (!sites.length) {
    const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "Ingen steder oppfyller feltlisten."); list.append(empty); return;
  }
  sites.forEach((site) => {
    const item = document.createElement("article"); item.className = `site-item status-${site.status}`;
    const head = document.createElement("div"); head.className = "site-item-head";
    const name = document.createElement("h3"); text(name, site.name);
    const badge = document.createElement("span"); badge.className = "badge"; text(badge, statusLabel(site.status)); head.append(name, badge); item.append(head);
    const uncertainty = site.uncertainty_m == null ? "usikkerhet ukjent" : `${Math.round(site.uncertainty_m)} m`;
    const meta = document.createElement("div"); meta.className = "site-meta"; text(meta, `${VALUE_LABELS[site.confidence] || "Ukjent"} | ${uncertainty} | ${accessLabel(site.access)}`); item.append(meta);
    const actions = document.createElement("div"); actions.className = "site-actions";
    const details = document.createElement("button"); details.className = "small"; details.type = "button"; markDetailControl(details, site.id, "field"); text(details, "Detaljer"); details.addEventListener("click", () => openDetail(site.id, "review", "field"));
    const add = document.createElement("button"); add.className = "small"; add.type = "button"; text(add, state.routeSiteIds.includes(site.id) ? "Lagt til" : "Legg til rute"); add.disabled = state.routeSiteIds.includes(site.id); add.addEventListener("click", () => addRouteSite(site.id));
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
  text($("route-history-summary"), `${state.routes.length} ${state.routes.length === 1 ? "lagret rute" : "lagrede ruter"}.`);
  if (!state.routes.length) {
    const empty = document.createElement("div"); empty.className = "empty-state"; text(empty, "Ingen lagrede ruter."); list.append(empty); return;
  }
  state.routes.forEach((route) => {
    const item = document.createElement("article"); item.className = "route-item";
    const name = document.createElement("h3"); text(name, route.name); item.append(name);
    const meta = document.createElement("div"); meta.className = "site-meta";
    text(meta, `${new Date(route.created_at).toLocaleString()} | ${formatRouteDistance(route.distance_m)} | ${formatRouteDuration(route.duration_s)}`); item.append(meta);
    const load = document.createElement("button"); load.className = "small"; load.type = "button"; text(load, "Last inn rute"); load.addEventListener("click", () => loadRoute(route.id)); item.append(load);
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
    text(warning, `Tilgang er ikke avklart for: ${cautionSites.map((site) => site.name).join(", ")}. Bruk bare offentlige tilnærminger.`); root.append(warning);
  }
  const href = URL.createObjectURL(new Blob([result.gpx], { type: "application/gpx+xml" }));
  state.gpxObjectUrls.add(href);
  const download = document.createElement("a");
  download.href = href;
  download.download = "bunkerkartet-route.gpx";
  text(download, "Last ned GPX"); root.append(download);
}

async function loadRoute(id) {
  const requestEpoch = state.authEpoch;
  try {
    const result = await api(`/api/routes/${id}`);
    if (requestEpoch !== state.authEpoch) return;
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    updateRouteStart(result.start, "lagret rutestart");
    state.routeSiteIds = (result.stops || []).map((stop) => stop.site_id).filter((siteId) => siteId != null);
    renderRouteStops();
    renderRouteResult(result);
    setStatus(`Lastet inn ${result.name}.`);
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
        setStatus("Autentisering mislyktes; privat arbeidsområde er tømt.");
        return;
      }
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Forespørselen feilet (${response.status})`);
    }
    const blob = await response.blob();
    if (requestEpoch !== state.authEpoch) throw new DOMException("Utdatert forespørsel", "AbortError");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    state.gpxObjectUrls.add(link.href);
    link.download = "bunkerkartet-sites.geojson";
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); state.gpxObjectUrls.delete(link.href); }, 1000);
    setStatus("GeoJSON lastet ned.");
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

function updateRouteStart(point, label) {
  state.start = point;
  if (startMarker) startMarker.remove();
  startMarker = L.circleMarker([point.lat, point.lon], { color: "#c65d2e", fillColor: "#fff", fillOpacity: 1, radius: 8, weight: 3 }).addTo(map).bindTooltip("Rutestart");
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
      const up = document.createElement("button"); up.className = "small"; up.title = "Flytt opp"; text(up, "Opp"); up.disabled = index === 0;
    up.addEventListener("click", () => { [state.routeSiteIds[index - 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index - 1]]; renderRouteStops(); });
      const down = document.createElement("button"); down.className = "small"; down.title = "Flytt ned"; text(down, "Ned"); down.disabled = index === state.routeSiteIds.length - 1;
    down.addEventListener("click", () => { [state.routeSiteIds[index + 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index + 1]]; renderRouteStops(); });
      const remove = document.createElement("button"); remove.className = "small"; remove.title = "Fjern stopp"; text(remove, "Fjern");
    remove.addEventListener("click", () => { state.routeSiteIds.splice(index, 1); renderRouteStops(); renderSiteList(); renderMap(); });
    item.append(name, access, up, down, remove); list.append(item);
  });
  $("create-route").disabled = !state.start || state.routeSiteIds.length === 0;
}

async function createRoute() {
  if (!state.start || state.routeSiteIds.length === 0 || state.routeRequestInFlight) return;
  const routeSites = state.routeSiteIds.map(siteById).filter((site) => site && hasReviewedPublicApproach(site));
  if (routeSites.length !== state.routeSiteIds.length) {
    setStatus("Noen valgte stopp er ikke lenger tilgjengelige med koordinater.");
    return;
  }
  const requestEpoch = state.authEpoch;
  state.routeRequestInFlight = true;
  const button = $("create-route"); button.disabled = true; text(button, "Beregner rute ...");
  try {
    const name = $("route-name").value.trim() || "Feltur i Trondheim";
    const result = await api("/api/routes", { method: "POST", body: JSON.stringify({ name, start: state.start, site_ids: routeSites.map((site) => site.id) }) });
    if (requestEpoch !== state.authEpoch) return;
    if (routeLayer) routeLayer.remove();
    routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
    map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    renderRouteResult(result);
    await loadRoutes();
    setStatus(`Opprettet ${result.name}.`);
  } catch (error) { if (!isStaleRequest(error)) text($("route-result"), error.message); }
  finally {
    if (requestEpoch === state.authEpoch) {
      state.routeRequestInFlight = false;
      text(button, "Beregn rute");
      renderRouteStops();
    }
  }
}

$("auth-form").addEventListener("submit", (event) => { event.preventDefault(); loadSites(); });
$("close-detail").addEventListener("click", closeDetail);
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
  state.pendingImport = null;
  state.previewHash = null;
  state.importRequestInFlight = false;
  $("preview-import").disabled = true;
  $("commit-import").disabled = true;
  text($("preview-import"), "Forhåndsvis");
  text($("import-result"), "");
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
$("pick-start").addEventListener("click", () => { state.pickingStart = true; setStatus("Klikk på kartet for å sette rutestart."); });
$("use-location").addEventListener("click", () => {
  const requestEpoch = state.authEpoch;
  if (!navigator.geolocation) {
    if (requestEpoch !== state.authEpoch) return;
    text($("location-status"), "Posisjon er ikke tilgjengelig i denne nettleseren.");
    setStatus("Posisjon er ikke tilgjengelig i denne nettleseren.");
    return;
  }
  text($("location-status"), "Ber om nåværende posisjon ...");
  navigator.geolocation.getCurrentPosition((position) => {
    if (requestEpoch !== state.authEpoch) return;
    const point = { lat: position.coords.latitude, lon: position.coords.longitude };
    updateRouteStart(point, "nåværende posisjon"); map.setView([point.lat, point.lon], 15);
    text($("location-status"), `Nåværende posisjon er satt som rutestart (nøyaktighet ${Math.round(position.coords.accuracy)} m).`);
    setStatus("Nåværende posisjon er satt som rutestart.");
  }, () => {
    if (requestEpoch !== state.authEpoch) return;
    text($("location-status"), "Kunne ikke lese nåværende posisjon.");
    setStatus("Kunne ikke lese nåværende posisjon.");
  }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
});
$("create-route").addEventListener("click", createRoute);
map.on("click", (event) => {
  if (!state.pickingStart) return;
  state.pickingStart = false;
  updateRouteStart({ lat: event.latlng.lat, lon: event.latlng.lng });
  setStatus("Rutestart satt.");
  renderRouteStops();
});

updateRouteStart(DEFAULT_ROUTE_START, "Standardstart i Trondheim");
setSurface("map", { scroll: false });
