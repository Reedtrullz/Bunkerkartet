const state = {
  token: "",
  session: null,
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
  reads: new Map(),
  drafts: new Map(),
  config: { idle_lock_seconds: 0, hidden_lock_seconds: 0, enabled_map_providers: [], available_map_providers: [], route_profiles: ["foot-hiking"], geography: null },
  routeDraftGeneration: 0,
  routeResultGeneration: null,
  routeSubmissions: new Map(),
  routeStopSnapshots: [],
  routeVisitMinutes: new Map(),
  loadedRouteId: null,
  routeObjectUrl: null,
  activeMapProvider: "",
  routeStartSource: null,
  routeSettingsTouched: false,
  mapViewTouched: false,
  applyingGeography: false,
};

const DEFAULT_ROUTE_START = { lat: 63.4305, lon: 10.3951 };
let map = null;
let markerLayer = null;
let uncertaintyLayer = null;
let observationLayer = null;
let activeTileLayer = null;
let routeLayer = null;
let startMarker = null;
let idleLockTimer = null;
let hiddenLockTimer = null;

const MAP_PROVIDERS = {
  kartverket: {
    url: "https://cache.kartverket.no/v1/wmts/1.0.0/topo/default/webmercator/{z}/{y}/{x}.png",
    maxZoom: 18,
    attribution: "&copy; Kartverket",
  },
  esri: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    maxZoom: 19,
    attribution: "Tiles &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
  },
};

const $ = (id) => document.getElementById(id);
const text = (node, value) => { node.textContent = value ?? ""; return node; };

function hasSessionRole(role) { return state.session?.role === role; }
function hasSessionScope(scope) { return hasSessionRole("owner") || Boolean(state.session?.scopes?.includes(scope)); }

function renderSessionRole() {
  const role = state.session?.role === "owner" ? "owner"
    : state.session?.role === "reader" ? "reader"
      : state.session?.role === "unknown" ? "unknown" : state.token ? "pending" : "anonymous";
  document.body.dataset.sessionRole = role;
  const status = $("session-role-status");
  if (!status) return;
  if (role === "owner") { text(status, "Eiertilgang"); status.title = "Privat redigering er tilgjengelig."; return; }
  if (role !== "reader") { text(status, state.token ? "Tilgang ikke bekreftet" : "Ikke innlogget"); status.title = "Endringer er skjult til rollen er bekreftet."; return; }
  const scopes = state.session.scopes.join(", ") || "ingen lesetilganger";
  const expiry = state.session.expires_at ? new Date(state.session.expires_at).toLocaleString() : "uten utløpstid";
  text(status, `Lesetilgang · ${scopes} · utløper ${expiry}`);
  status.title = `Lesetilgang utløper ${expiry}.`;
}

function geographyName() { return state.config.geography?.display_name || "Trondheim"; }

function defaultRouteName() {
  return state.config.geography ? `${state.config.geography.display_name} field route` : "Feltur i Trondheim";
}

function normalizeGeography(value) {
  if (!value || typeof value !== "object" || typeof value.display_name !== "string" || !value.display_name.trim()) return null;
  const defaults = value.map_defaults;
  const center = defaults?.center;
  const zoom = Number(defaults?.zoom);
  if (!Array.isArray(center) || center.length !== 2 || !center.every(Number.isFinite)
      || center[0] < -90 || center[0] > 90 || center[1] < -180 || center[1] > 180
      || !Number.isInteger(zoom) || zoom < 0 || zoom > 22) return null;
  return { ...value, display_name: value.display_name.trim(), map_defaults: { ...defaults, center: [...center], zoom } };
}

function applyGeographyDefaults({ force = false } = {}) {
  const geography = state.config.geography;
  const name = geographyName();
  const geographyStatus = $("geography-status");
  if (geographyStatus) {
    text(geographyStatus, geography
      ? [geography.display_name, geography.synthetic ? "syntetisk testgeografi" : "", ...(geography.warnings || [])].filter(Boolean).join(" · ")
      : "Standardområde: Trondheim.");
  }
  const mapNode = $("map");
  if (mapNode) mapNode.setAttribute("aria-label", geography ? `Kart over ${name}` : "Kart over steder i Trondheim");
  if (map && geography && (!state.mapViewTouched || force)) {
    state.applyingGeography = true;
    map.setView(geography.map_defaults.center, geography.map_defaults.zoom, { animate: false });
    state.applyingGeography = false;
    state.mapViewTouched = false;
  } else if (map && !geography && force) {
    state.applyingGeography = true;
    map.setView([DEFAULT_ROUTE_START.lat, DEFAULT_ROUTE_START.lon], 12, { animate: false });
    state.applyingGeography = false;
  }
  if (force || !state.start || !["manual", "device", "loaded"].includes(state.routeStartSource)) {
    updateRouteStart(geography ? { lat: geography.map_defaults.center[0], lon: geography.map_defaults.center[1] } : DEFAULT_ROUTE_START,
      `Standardstart i ${name}`, "default");
  }
  if (force || (!state.routeSettingsTouched && state.routeSiteIds.length === 0 && !state.loadedRouteId)) {
    $("route-name").value = defaultRouteName();
  }
}

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

function setMapMessage(message, stale = false) {
  const node = $("map-provider-status");
  if (!node) return;
  text(node, message);
  node.classList.toggle("is-stale", stale);
}

function initializeMap() {
  if (typeof L === "undefined") {
    $("map").hidden = true;
    document.querySelector(".map-shell")?.classList.add("map-unavailable");
    setMapMessage("Kartet er ikke tilgjengelig. Stedsliste og vurdering kan fortsatt brukes.", true);
    setStatus("Kartet er ikke tilgjengelig; private API- og listefunksjoner er klare.");
    return;
  }
  try {
    map = L.map("map").setView([63.4305, 10.3951], 12);
    map.on("dragstart zoomstart", () => { if (!state.applyingGeography) state.mapViewTouched = true; });
    L.control.scale({ imperial: false }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    uncertaintyLayer = L.layerGroup().addTo(map);
    observationLayer = L.layerGroup().addTo(map);
    map.on("click", (event) => {
      if (!state.pickingStart) return;
      state.pickingStart = false;
      updateRouteStart({ lat: event.latlng.lat, lon: event.latlng.lng }, "", "manual");
      setStatus("Rutestart satt.");
      renderRouteStops();
    });
    $("map").hidden = false;
    setMapMessage("Ingen kartleverandør valgt. Kartforespørsler er slått av.");
  } catch {
    map = null;
    $("map").hidden = true;
    document.querySelector(".map-shell")?.classList.add("map-unavailable");
    setMapMessage("Kartet kunne ikke startes. Stedsliste og vurdering kan fortsatt brukes.", true);
  }
}

function selectMapProvider(provider) {
  const id = MAP_PROVIDERS[provider] ? provider : "";
  if (!state.token || (id && !state.config.enabled_map_providers.includes(id))) {
    $("map-provider").value = "";
    return;
  }
  state.activeMapProvider = id;
  if (activeTileLayer && map) {
    map.removeLayer(activeTileLayer);
    activeTileLayer = null;
  }
  if (!id) {
    setMapMessage("Ingen kartleverandør valgt. Kartforespørsler er slått av.");
    return;
  }
  if (!map || typeof L === "undefined") {
    setMapMessage("Kartlaget er valgt, men kartvisningen er ikke tilgjengelig.", true);
    return;
  }
  const config = MAP_PROVIDERS[id];
  activeTileLayer = L.tileLayer(config.url, { maxZoom: config.maxZoom, attribution: config.attribution });
  activeTileLayer.on("tileerror", () => setMapMessage(`Kartlaget ${id} feilet. Stedsliste og private API fungerer fortsatt.`, true));
  activeTileLayer.on("tileload", () => {
    if (state.activeMapProvider === id) setMapMessage(`Kartlag: ${id}. Kartleverandøren mottar viste kartutsnitt.`);
  });
  activeTileLayer.addTo(map);
  setMapMessage(`Kartlag: ${id}. Kartleverandøren mottar viste kartutsnitt.`);
}

function configureMapChoices(config) {
  const select = $("map-provider");
  const providers = [...new Set(config.enabled_map_providers || [])].filter((id) => MAP_PROVIDERS[id]);
  select.replaceChildren();
  const noProvider = document.createElement("option"); noProvider.value = ""; text(noProvider, "Ingen eksterne kartlag"); select.append(noProvider);
  providers.forEach((id) => {
    const option = document.createElement("option"); option.value = id;
    text(option, id === "kartverket" ? "Kartverket topo" : "Esri flyfoto"); select.append(option);
  });
  select.disabled = !state.token || providers.length === 0 || !map;
  select.value = "";
}

function configureRouteProfiles(config) {
  const select = $("route-profile");
  const previous = select.value;
  const allowed = [...new Set(Array.isArray(config.route_profiles) ? config.route_profiles : ["foot-hiking"])]
    .filter((profile) => ["foot-hiking", "foot-walking"].includes(profile));
  select.replaceChildren();
  allowed.forEach((profile) => {
    const option = document.createElement("option"); option.value = profile;
    text(option, profile === "foot-hiking" ? "Til fots — tursti" : "Til fots — gange"); select.append(option);
  });
  select.value = allowed.includes(previous) ? previous : (allowed.includes("foot-hiking") ? "foot-hiking" : (allowed[0] || ""));
  select.disabled = allowed.length < 2;
  return previous !== select.value;
}

function updateRouteModeControls() {
  const explicitEnd = $("route-mode").value === "explicit_end";
  $("route-end-lat-label").hidden = !explicitEnd;
  $("route-end-lon-label").hidden = !explicitEnd;
  $("route-end-lat").required = explicitEnd;
  $("route-end-lon").required = explicitEnd;
}

function cancelAllReads() {
  state.reads.forEach((read) => { clearTimeout(read.timer); read.controller.abort(); });
  state.reads.clear();
}

function clearLockTimers() {
  clearTimeout(idleLockTimer); idleLockTimer = null;
  clearTimeout(hiddenLockTimer); hiddenLockTimer = null;
}

function resetIdleLockTimer() {
  clearTimeout(idleLockTimer); idleLockTimer = null;
  const seconds = Number(state.config.idle_lock_seconds) || 0;
  if (state.token && seconds > 0) idleLockTimer = setTimeout(lockWorkspace, Math.min(seconds * 1000, 2147480000));
}

function startHiddenLockTimer() {
  clearTimeout(hiddenLockTimer); hiddenLockTimer = null;
  const seconds = Number(state.config.hidden_lock_seconds) || 0;
  if (state.token && document.visibilityState === "hidden" && seconds > 0) {
    hiddenLockTimer = setTimeout(lockWorkspace, Math.min(seconds * 1000, 2147480000));
  }
}

function lockWorkspace() {
  clearAuthenticatedData();
  setStatus("Arbeidsflate låst.");
}

function loadPublicConfig() {
  fetch("/api/config", { headers: { Accept: "application/json" } })
    .then((response) => { if (!response.ok) throw new Error("Configuration unavailable"); return response.json(); })
    .then((config) => {
      if (!config || typeof config !== "object") throw new Error("Invalid config");
      state.config = {
        idle_lock_seconds: Number.isFinite(config.idle_lock_seconds) && config.idle_lock_seconds > 0 ? config.idle_lock_seconds : 0,
        hidden_lock_seconds: Number.isFinite(config.hidden_lock_seconds) && config.hidden_lock_seconds > 0 ? config.hidden_lock_seconds : 0,
        enabled_map_providers: Array.isArray(config.enabled_map_providers) ? config.enabled_map_providers : [],
        available_map_providers: Array.isArray(config.available_map_providers) ? config.available_map_providers : [],
        route_profiles: Array.isArray(config.route_profiles) ? config.route_profiles : ["foot-hiking"],
        geography: normalizeGeography(config.geography),
      };
      configureMapChoices(state.config);
      if (configureRouteProfiles(state.config) && state.routeSiteIds.length) invalidateRouteResult("Tillatte rutemodeller ble oppdatert. Kontroller rutemodellen og beregn på nytt.");
      applyGeographyDefaults();
      resetIdleLockTimer();
      if (document.visibilityState === "hidden") startHiddenLockTimer();
    })
    .catch(() => { configureMapChoices(state.config); configureRouteProfiles(state.config); applyGeographyDefaults(); });
}

const outcomeLabel = (outcome) => ({
  found: "Funnet", not_found: "Ikke funnet", inaccessible: "Utilgjengelig", needs_follow_up: "Må følges opp",
}[outcome] || outcome);
const siteDisplayName = (site) => site.enrichment?.display_name || site.name;
const siteDisplayKind = (site) => site.enrichment?.kind_label || site.site_kind;
const enrichmentCertaintyLabel = (certainty) => ({
  supported: "Kildestøttet", uncertain: "Uavklart", unknown: "Ikke dokumentert", registered: "Registrert",
}[certainty] || certainty);
const enrichmentResearchStateLabel = (state) => ({
  curated: "Kildeunderlag kuratert",
  researched_pending: "Research funnet – venter på kuratering",
  identity_review: "Identitet må avklares",
  not_curated: "Ikke kuratert i kartet",
}[state] || "Ikke kuratert i kartet");

const importedCoordinateRationale = (site) => {
  const match = /^krigskart:(\d+)$/.exec(site.external_key || "");
  const expected = match && `Coordinate copied from KrigsKart map marker #${match[1]}; the source point is a starting area for review, not a field-verified entrance or footprint.`;
  return site.enrichment && expected === site.short_rationale ? site.short_rationale : null;
};
const importedCoordinateWarning = "Candidate point transcribed from a public map/source; coordinate, identity, condition, and access require independent verification.";

function appendEnrichmentClaims(parent, claims = []) {
  if (!claims.length) {
    const empty = document.createElement("p"); empty.className = "site-meta"; text(empty, "Ingen kildebasert opplysning er kuratert for dette feltet."); parent.append(empty);
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
  const state = enrichment?.research_state || "not_curated";
  const status = document.createElement("p"); status.className = "site-meta enrichment-status";
  text(status, enrichmentResearchStateLabel(state)); root.append(status);
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
  const kind = String(siteKind || "").toLocaleLowerCase("nb");
  if (["pow", "fangeleir", "krigsfange", "prisoner", "war prisoner"].some((term) => kind.includes(term))) {
    return { key: "pow", label: "Krigsfangeleir", glyph: "P" };
  }
  if (["krigsgrav", "war grave", "grave"].some((term) => kind.includes(term))) {
    return { key: "grave", label: "Krigsgrav", glyph: "G" };
  }
  if (["krigsminnesmerke", "minnesmerke", "war memorial", "memorial", "monument"].some((term) => kind.includes(term))) {
    return { key: "memorial", label: "Krigsminnesmerke", glyph: "M" };
  }
  if (["hule", "grotte", "tunnel", "cave"].some((term) => kind.includes(term))) {
    return { key: "cave", label: "Hule / tunnel", glyph: "C" };
  }
  if (["bunker", "fort", "batteri", "battery", "stilling", "searchlight", "observation", "communications", "military position"].some((term) => kind.includes(term))) {
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
  form.querySelectorAll("[aria-invalid=\"true\"]").forEach((node) => {
    node.removeAttribute("aria-invalid");
    node.removeAttribute("aria-describedby");
  });
}

function showFieldErrors(form, error) {
  clearFieldErrors(form);
  let firstInvalid = null;
  (error.details || []).forEach((item, index) => {
    const field = Array.isArray(item.loc) ? item.loc.at(-1) : null;
    const control = [...form.elements].find((element) => element.name === field);
    if (!control) return;
    firstInvalid ||= control;
    control.setAttribute("aria-invalid", "true");
    const message = document.createElement("div");
    message.className = "field-error";
    message.id = `field-error-${crypto.randomUUID()}`;
    message.setAttribute("role", "alert");
    text(message, `${FIELD_LABELS[field] || field || "Felt"}: ${item.msg || "Ugyldig verdi"}`);
    control.setAttribute("aria-describedby", message.id);
    control.closest("label")?.append(message);
  });
  firstInvalid?.focus({ preventScroll: false });
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
  if (surface === "route" && state.session?.role === "reader") surface = "map";
  if (state.reads.has("detail")) {
    state.reads.get("detail").controller.abort();
    state.detailGeneration += 1;
  }
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
  updateNavigationUrl();
  if (state.token && surface === "review") { loadCandidates(); loadFieldPriority(); }
  if (state.token && surface === "route") loadRoutes();
}

document.querySelectorAll(".surface-nav button").forEach((button) => {
  button.addEventListener("click", () => setSurface(button.dataset.target === "map-tools-panel" ? "map" : button.dataset.target === "review-panel" ? "review" : "route"));
});

function navigationValues() {
  return {
    surface: ["map", "review", "route"].includes(new URLSearchParams(location.search).get("surface"))
      ? new URLSearchParams(location.search).get("surface") : "map",
    site: /^\d+$/.test(new URLSearchParams(location.search).get("site") || "")
      ? new URLSearchParams(location.search).get("site") : "",
    status: Object.hasOwn(STATUS_LABELS, new URLSearchParams(location.search).get("status") || "")
      ? new URLSearchParams(location.search).get("status") : "",
    category: ["bunker", "cave", "pow", "grave", "memorial", "other"].includes(new URLSearchParams(location.search).get("category"))
      ? new URLSearchParams(location.search).get("category") : "",
    access: Object.hasOwn(ACCESS_LABELS, new URLSearchParams(location.search).get("access") || "")
      ? new URLSearchParams(location.search).get("access") : "",
    confidence: ["unknown", "low", "medium", "high"].includes(new URLSearchParams(location.search).get("confidence"))
      ? new URLSearchParams(location.search).get("confidence") : "",
  };
}

function updateNavigationUrl({ replace = false, site = undefined } = {}) {
  const url = new URL(location.href);
  const values = navigationValues();
  values.surface = state.surface;
  values.status = $("status-filter").value;
  values.category = $("category-filter").value;
  values.access = $("access-filter").value;
  values.confidence = $("confidence-filter").value;
  if (site !== undefined) values.site = site == null ? "" : String(site);
  const allowed = new Set(["surface", "site", "status", "category", "access", "confidence"]);
  [...url.searchParams.keys()].forEach((key) => { if (!allowed.has(key)) url.searchParams.delete(key); });
  url.hash = "";
  Object.entries(values).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); });
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (`${location.pathname}${location.search}${location.hash}` === next) return;
  history[replace ? "replaceState" : "pushState"]({ bunkerkartet: true }, "", next);
}

function restoreNavigationFromUrl() {
  const values = navigationValues();
  $("status-filter").value = values.status;
  $("category-filter").value = values.category;
  $("access-filter").value = values.access;
  $("confidence-filter").value = values.confidence;
  setSurface(values.surface, { scroll: false });
  if (state.token) {
    loadSites().then(() => { if (values.site) loadDetail(Number(values.site)); });
  }
}

window.addEventListener("popstate", restoreNavigationFromUrl);

function revokeGpxObjectUrls() {
  state.gpxObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.gpxObjectUrls.clear();
}

function cacheSites(sites) { sites.forEach((site) => state.siteCache.set(site.id, site)); }

function saveDraft(key, draft) {
  const serialized = JSON.stringify(draft);
  if (serialized.length > 24000) return;
  state.drafts.delete(key);
  state.drafts.set(key, draft);
  while (state.drafts.size > 40) state.drafts.delete(state.drafts.keys().next().value);
}

function formSnapshot(form) {
  const values = {};
  [...form.elements].forEach((control) => {
    if (!control.name || control.disabled || control.type === "submit" || control.type === "button") return;
    values[control.name] = control.type === "checkbox" ? control.checked : control.value;
  });
  return values;
}

function showDraftNotice(form, message, conflict = false) {
  form.querySelectorAll(".draft-notice, .draft-conflict").forEach((node) => node.remove());
  const notice = document.createElement("p");
  notice.className = conflict ? "draft-conflict" : "draft-notice";
  notice.setAttribute("role", conflict ? "alert" : "status");
  text(notice, message);
  form.prepend(notice);
}

function bindFormDraft(form, key, extra = {}) {
  form.dataset.draftKey = key;
  const previous = state.drafts.get(key);
  if (previous?.fields) {
    Object.entries(previous.fields).forEach(([name, value]) => {
      const control = [...form.elements].find((candidate) => candidate.name === name);
      if (control) {
        if (control.type === "checkbox") control.checked = Boolean(value);
        else control.value = value;
      }
    });
    showDraftNotice(form, "Utkast fra denne økten er gjenopprettet. Det lagres bare i minnet og slettes når arbeidsflaten låses.");
  }
  const persist = () => saveDraft(key, { ...(state.drafts.get(key) || previous || {}), ...extra, fields: formSnapshot(form) });
  form.addEventListener("input", persist);
  form.addEventListener("change", persist);
  return { persist, clear: () => state.drafts.delete(key), previous };
}

async function showRevisionConflict(form, key, siteId, fallback = "") {
  const authEpoch = state.authEpoch;
  const draft = state.drafts.get(key);
  let latest = null;
  try { latest = await api(`/api/sites/${siteId}`); } catch { /* Keep the local draft even if readback is unavailable. */ }
  if (authEpoch !== state.authEpoch || !state.drafts.has(key)) return;
  const fields = draft?.fields ? Object.keys(draft.fields).filter((name) => {
    const current = latest?.[name];
    const value = draft.fields[name];
    if (name === "warnings" && Array.isArray(current)) return current.join("\n") !== value;
    return current !== undefined && String(current ?? "") !== String(value ?? "");
  }) : [];
  const changed = fields.length ? ` Endrede felter i utkastet: ${fields.join(", ")}.` : " Sammenlign gjeldende verdier og utkastet før du lagrer på nytt.";
  showDraftNotice(form, `Serverversjonen er nyere. Ditt utkast er bevart.${changed}${fallback ? ` ${fallback}` : ""}`, true);
}

function isStaleRequest(error) { return error?.name === "AbortError"; }

function clearAuthenticatedData({ resetToken = true } = {}) {
  cancelAllReads();
  clearLockTimers();
  state.authEpoch += 1;
  state.session = null;
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
  state.routeStartSource = null;
  state.routeSettingsTouched = false;
  state.mapViewTouched = false;
  state.pickingStart = false;
  state.routeRequestInFlight = false;
  state.routeDraftGeneration += 1;
  state.routeResultGeneration = null;
  state.routeSubmissions.clear();
  state.routeStopSnapshots = [];
  state.routeVisitMinutes.clear();
  state.loadedRouteId = null;
  state.drafts.clear();
  state.activeMapProvider = "";
  if (activeTileLayer && map) map.removeLayer(activeTileLayer);
  activeTileLayer = null;
  state.detailOrigin = null;
  state.detailGeneration += 1;
  state.reads.get("detail")?.controller.abort();
  revokeGpxObjectUrls();
  markerLayer?.clearLayers();
  uncertaintyLayer?.clearLayers();
  observationLayer?.clearLayers();
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
  $("route-name").value = defaultRouteName();
  $("route-mode").value = "one_way";
  $("route-end-lat").value = "";
  $("route-end-lon").value = "";
  $("route-declared-budget").value = "";
  configureRouteProfiles(state.config);
  updateRouteModeControls();
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
  configureMapChoices(state.config);
  updateNavigationUrl({ replace: true, site: "" });
  renderSessionRole();
  applyGeographyDefaults({ force: true });
}

function clearPrivateRouteState() {
  ["routes", "route-detail"].forEach((key) => {
    const read = state.reads.get(key);
    if (!read) return;
    clearTimeout(read.timer);
    read.controller.abort();
    state.reads.delete(key);
  });
  state.routes = [];
  state.routeDraftGeneration += 1;
  state.routeResultGeneration = null;
  state.routeRequestInFlight = false;
  state.routeSubmissions.clear();
  state.routeSiteIds = [];
  state.routeStopSnapshots = [];
  state.routeVisitMinutes.clear();
  state.loadedRouteId = null;
  state.routeSettingsTouched = false;
  state.routeStartSource = null;
  state.start = null;
  state.drafts.delete("route:settings");
  if (routeLayer) { routeLayer.remove(); routeLayer = null; }
  if (startMarker) { startMarker.remove(); startMarker = null; }
  revokeGpxObjectUrls();
  state.routeObjectUrl = null;
  $("route-result").replaceChildren();
  $("route-name").value = defaultRouteName();
  $("route-mode").value = "one_way";
  $("route-end-lat").value = "";
  $("route-end-lon").value = "";
  $("route-declared-budget").value = "";
  updateRouteModeControls();
  renderRouteStops();
  renderRouteHistory();
  applyGeographyDefaults({ force: true });
}

function appendDataStatus(parent, record, label = "Data") {
  if (record?.data_status !== "unavailable") return;
  const states = Object.entries(record.stored_field_status || {})
    .filter(([, value]) => value && value !== "valid" && value !== "valid_empty")
    .map(([field, value]) => `${field}: ${value}`);
  const notice = document.createElement("p"); notice.className = "warning"; notice.setAttribute("role", "status");
  text(notice, `${label} er utilgjengelige og er utelatt fra visningen.${states.length ? ` Feltstatus: ${states.join("; ")}.` : ""}`);
  parent.append(notice);
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
    record.evidence.forEach((evidence) => {
      const line = document.createElement("div"); line.className = "site-meta";
      const rights = evidence.rights_status === "unknown" ? "rettigheter ukjent; tillatelse ikke bekreftet" : evidence.rights_status && `rettigheter: ${evidence.rights_status}`;
      const metadata = [evidence.content_kind && `innhold: ${evidence.content_kind}`, evidence.role && `rolle: ${evidence.role}`, rights, evidence.claim_ids?.length && `påstander: ${evidence.claim_ids.join(", ")}`].filter(Boolean).join(" | ");
      text(line, `Kilde: ${evidence.title || evidence.url || "Ukjent kilde"}${metadata ? ` | ${metadata}` : ""}${evidence.excerpt ? ` — ${evidence.excerpt}` : ""}`);
      if (evidence.rights_status === "unknown") line.title = "Rettighetsstatus ukjent; dette gir ingen tillatelse.";
      item.append(line);
    });
    record.warnings.forEach((warning) => { const line = document.createElement("div"); line.className = "warning"; text(line, warning); item.append(line); });
    root.append(item);
  });
  const technical = document.createElement("details");
  const label = document.createElement("summary"); text(label, "Vis teknisk forhåndsvisning"); technical.append(label);
  const raw = document.createElement("pre"); text(raw, JSON.stringify(result, null, 2)); technical.append(raw); root.append(technical);
}

const READ_TIMEOUT_MS = 12000;

function beginRead(key) {
  const previous = state.reads.get(key);
  if (previous) { clearTimeout(previous.timer); previous.controller.abort(); }
  const read = { key, authEpoch: state.authEpoch, controller: new AbortController(), timedOut: false, timer: null };
  read.timer = setTimeout(() => { read.timedOut = true; read.controller.abort(); }, READ_TIMEOUT_MS);
  state.reads.set(key, read);
  return read;
}

function currentRead(read) { return state.reads.get(read.key) === read && state.authEpoch === read.authEpoch; }

function finishRead(read) {
  clearTimeout(read.timer);
  if (state.reads.get(read.key) === read) state.reads.delete(read.key);
}

function updateReadStatus(id, message = "", stale = false) {
  const node = $(id);
  if (!node) return;
  text(node, message);
  node.classList.toggle("is-stale", stale);
}

function readFailure(error, read, statusId, hasLastGood, noun) {
  if (!currentRead(read)) return false;
  if (error?.name === "AbortError" && !read.timedOut) return false;
  const reason = read.timedOut ? "Forespørselen tok for lang tid." : error.message;
  updateReadStatus(statusId, hasLastGood ? `Viser sist lagrede ${noun}; oppdateringen feilet: ${reason}` : reason, hasLastGood);
  return true;
}

function arrayResponse(value, surface) {
  if (!Array.isArray(value)) throw new Error(`Ugyldig svar for ${surface}; prøv å oppdatere.`);
  return value;
}

async function api(path, options = {}) {
  const requestEpoch = state.authEpoch;
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (typeof options.body === "string" || options.body instanceof Blob) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  let body = null;
  try {
    const payload = await response.text();
    body = payload ? JSON.parse(payload) : null;
  } catch {
    throw new Error("Serveren returnerte ugyldig JSON. Prøv å oppdatere.");
  }
  if (requestEpoch !== state.authEpoch) throw new DOMException("Utdatert forespørsel", "AbortError");
  if (!response.ok) {
    if (response.status === 401) {
      clearAuthenticatedData();
      setStatus("Autentisering mislyktes; privat arbeidsområde er tømt.");
      throw new DOMException("Utdatert forespørsel", "AbortError");
    }
    const detail = body?.detail;
    const message = detail && typeof detail === "object" && !Array.isArray(detail) ? detail.message : detail;
    const error = new Error(validationMessage(message || `Forespørselen feilet (${response.status})`));
    error.status = response.status;
    error.code = detail && typeof detail === "object" ? detail.code : null;
    error.details = Array.isArray(detail) ? detail : [];
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
  if (typeof site.route_eligible === "boolean") return site.route_eligible;
  return !site.merged_into_id && site.status !== "rejected" && !site.location_review_required && site.approach_latitude != null && site.approach_longitude != null &&
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
  state.reads.get("detail")?.controller.abort();
  state.detailOrigin = null;
  $("detail-panel").classList.remove("is-selected");
  text($("site-detail-heading"), "Stedsdetaljer");
  text($("site-detail"), "Velg en markør eller et sted.");
  $("close-detail").hidden = true;
  setSurface(origin.surface, { scroll: false });
  updateNavigationUrl({ site: "" });
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
  if (!map || !markerLayer || !uncertaintyLayer || !observationLayer) return;
  markerLayer.clearLayers();
  uncertaintyLayer.clearLayers();
  observationLayer.clearLayers();
  const bounds = [];
  visibleSites().forEach((site) => {
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
    add.dataset.ownerOnly = "true";
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
  if (fit && bounds.length && !state.config.geography && !state.mapViewTouched) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
}

function renderSiteList() {
  const list = $("site-list");
  list.replaceChildren();
  const sites = visibleSites();
  if (!sites.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    text(empty, "Ingen steder passer filtrene.");
    list.append(empty);
    return;
  }
  sites.forEach((site) => {
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
    appendDataStatus(item, site, "Stedsdata");
    if (site.route_eligible === false && site.route_blocking_reason) {
      const blocked = document.createElement("div"); blocked.className = "site-meta"; text(blocked, `Rute utilgjengelig: ${site.route_blocking_reason}`); item.append(blocked);
    }
    const actions = document.createElement("div");
    actions.className = "site-actions";
    const details = document.createElement("button");
    details.className = "small";
    markDetailControl(details, site.id, "list");
    text(details, "Detaljer");
    details.addEventListener("click", () => openDetail(site.id, "map", "list"));
    const add = document.createElement("button");
    add.className = "small";
    add.dataset.ownerOnly = "true";
    const routeReady = hasReviewedPublicApproach(site);
    text(add, !routeReady ? "Ingen gjennomgått tilnærming" : state.routeSiteIds.includes(site.id) ? "Lagt til" : "Legg til rute");
    add.disabled = !routeReady || state.routeSiteIds.includes(site.id);
    add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add);
    if (site.status === "rejected") {
      const restore = document.createElement("button");
      restore.className = "small"; restore.type = "button"; restore.dataset.ownerOnly = "true"; text(restore, "Gjenopprett kandidat");
      restore.addEventListener("click", () => runReviewAction(site.id, "restore"));
      actions.append(restore);
    }
    item.append(actions);
    list.append(item);
  });
}

function visibleSites() {
  const kind = $("kind-filter").value.trim().toLocaleLowerCase("nb");
  const category = $("category-filter").value;
  return state.sites.filter((site) => {
    const siteKind = String(site.site_kind || "").toLocaleLowerCase("nb");
    return (!kind || siteKind.includes(kind)) && (!category || locationCategory(site.site_kind).key === category);
  });
}

async function loadSites() {
  const token = $("admin-token").value.trim();
  if (token !== state.token) clearAuthenticatedData({ resetToken: false });
  state.token = token;
  if (!state.token) { setStatus("Skriv inn administratortokenet for å laste inn steder."); return; }
  renderSessionRole();
  configureMapChoices(state.config);
  resetIdleLockTimer();
  const read = beginRead("sites");
  const loadButton = $("load-sites"); loadButton.disabled = true; text(loadButton, "Laster inn ...");
  updateReadStatus("site-read-status", state.sites.length ? "Oppdaterer; viser sist lastede steder." : "Laster steder …", Boolean(state.sites.length));
  const params = new URLSearchParams();
  const status = $("status-filter").value;
  const kind = $("kind-filter").value.trim();
  const query = $("site-search").value.trim();
  const access = $("access-filter").value;
  const confidence = $("confidence-filter").value;
  const category = $("category-filter").value;
  const hasFilters = [status, kind, category, query, access, confidence].some(Boolean);
  if (status) params.set("status", status);
  if (query) params.set("q", query);
  if (access) params.set("access", access);
  if (confidence) params.set("confidence", confidence);
  try {
    const session = await api("/api/session", { signal: read.controller.signal });
    if (!currentRead(read)) return false;
    if (!session || !["owner", "reader"].includes(session.role) || !Array.isArray(session.scopes)) {
      state.session = { role: "unknown", scopes: [], expires_at: null };
      renderSessionRole();
      throw new Error("Serveren bekreftet ikke tilgangsrollen. Endringer er skjult.");
    }
    const previousRole = state.session?.role;
    state.session = { role: session.role, scopes: session.scopes.filter((scope) => typeof scope === "string"), expires_at: session.expires_at || null };
    renderSessionRole();
    if (previousRole === "owner" && session.role === "reader") {
      const detailRead = state.reads.get("detail");
      if (detailRead) {
        clearTimeout(detailRead.timer);
        detailRead.controller.abort();
        state.reads.delete("detail");
      }
      state.detailGeneration += 1;
      state.detailOrigin = null;
      $("detail-panel").classList.remove("is-selected");
      $("close-detail").hidden = true;
      text($("site-detail-heading"), "Stedsdetaljer");
      text($("site-detail"), "Velg en markør eller et sted.");
    }
    if (session.role === "reader") {
      clearPrivateRouteState();
      if (state.surface === "route") setSurface("map", { scroll: false });
    }
    if (!hasSessionScope("sites:read")) throw new Error("Kontoen mangler sites:read for å vise steder.");
    const sites = arrayResponse(await api(`/api/sites?${params}`, { signal: read.controller.signal }), "stedsliste");
    if (!currentRead(read)) return;
    state.sites = sites;
    if (!state.start) applyGeographyDefaults();
    cacheSites(state.sites);
    $("download-geojson").disabled = false;
    renderSiteList();
    renderMap(true);
    setStatus(state.sites.length
      ? `${state.sites.length} ${state.sites.length === 1 ? "sted" : "steder"} lastet inn.`
      : hasFilters
        ? "Ingen steder passer filtrene."
        : "Innlogget. Ingen steder er importert ennå.");
    updateReadStatus("site-read-status");
    updateNavigationUrl({ replace: true });
    const selectedSite = navigationValues().site;
    if (selectedSite) await loadDetail(Number(selectedSite));
    return true;
  } catch (error) {
    if (!readFailure(error, read, "site-read-status", state.sites.length > 0, "steder")) return false;
    if (error.status === 401 || error.status === 503) clearAuthenticatedData();
    if (!state.session && state.token) {
      state.session = { role: "unknown", scopes: [], expires_at: null };
      renderSessionRole();
    }
    setStatus(error.message);
    return false;
  } finally {
    const isLatest = currentRead(read);
    finishRead(read);
    if (isLatest) { loadButton.disabled = false; text(loadButton, "Last inn kart"); }
  }
}

async function refreshSitesAndDetail(siteId) {
  const navigationGeneration = state.navigationGeneration;
  await loadSites();
  if (navigationGeneration !== state.navigationGeneration) return;
  await loadDetail(siteId);
}

async function runReviewAction(id, action, targetSiteId = null, targetRevision = null) {
  if (action === "merge") {
    setStatus("Sammenslåing krever en gjennomgått forhåndsvisning.");
    return;
  }
  if (["accept", "research", "field_verify", "confirm"].includes(action)) {
    setStatus("Statusløft krever en eksplisitt begrunnelse og valgte kilder eller feltobservasjoner.");
    return;
  }
  if (action === "reject" && !window.confirm("Avvise denne kandidaten?")) return;
  if (action === "mark_destroyed" && !window.confirm("Markere stedet som ødelagt eller fylt igjen?")) return;
  try {
    const payload = { action };
    if (targetSiteId) { payload.target_site_id = targetSiteId; payload.target_expected_revision = targetRevision; }
    const currentSite = state.siteCache.get(id);
    if (currentSite?.revision) payload.expected_revision = currentSite.revision;
    const result = await api(`/api/sites/${id}/review`, { method: "POST", body: JSON.stringify(payload) });
    if (result.site) state.siteCache.set(result.site.id, result.site);
    await refreshSitesAndDetail(targetSiteId || id);
  } catch (error) { if (!isStaleRequest(error)) setStatus(error.message); }
}

function mergeEffectCount(value) {
  if (Array.isArray(value)) return value.length;
  if (Number.isFinite(value)) return value;
  if (value && Number.isFinite(value.count)) return value.count;
  return null;
}

function appendMergeEffect(parent, label, value) {
  const count = mergeEffectCount(value);
  if (count === null) return;
  const row = document.createElement("p"); row.className = "site-meta";
  text(row, `${label}: ${count}`); parent.append(row);
}

function renderMergePreview(root, preview) {
  root.replaceChildren();
  const source = preview.source || {};
  const target = preview.target || preview.survivor || {};
  const heading = document.createElement("h4"); text(heading, "Forhåndsvisning av sammenslåing"); root.append(heading);
  const identities = document.createElement("p"); identities.className = "site-meta";
  text(identities, `Kilde: ${source.name || source.display_name || source.external_key || "ukjent"} (#${source.id ?? "?"}) → sted som består: ${target.name || target.display_name || target.external_key || "ukjent"} (#${target.id ?? "?"})`);
  root.append(identities);

  const transfer = preview.evidence_transfer || preview.transfers || {};
  const metrics = document.createElement("div"); metrics.className = "merge-effects";
  appendMergeEffect(metrics, "Bevis som overføres", transfer.evidence_ids ?? transfer.evidence ?? preview.evidence_count);
  appendMergeEffect(metrics, "Observasjoner som overføres", transfer.observation_ids ?? transfer.observations ?? preview.observation_count);
  appendMergeEffect(metrics, "Relasjoner som overføres", transfer.relations ?? preview.relation_count);
  appendMergeEffect(metrics, "Selvreferanser som hoppes over", transfer.skipped_self_relations ?? transfer.self_relations_skipped ?? preview.skipped_self_relations);
  if (metrics.childElementCount) root.append(metrics);

  const preserved = preview.preserved_fields || preview.target_preserved_fields;
  if (Array.isArray(preserved) && preserved.length) {
    const title = document.createElement("h5"); text(title, "Felt som beholdes fra stedet som består"); root.append(title);
    const list = document.createElement("ul"); list.className = "source-list";
    preserved.forEach((field) => { const item = document.createElement("li"); text(item, typeof field === "string" ? field : field.label || field.field || JSON.stringify(field)); list.append(item); });
    root.append(list);
  } else {
    const retained = preview.survivor_fields || preview.target_fields_preserved || preview.preserved_summary;
    if (retained) { const paragraph = document.createElement("p"); paragraph.className = "site-meta"; text(paragraph, `Felt som beholdes: ${retained}`); root.append(paragraph); }
  }

  const lineage = preview.lineage;
  if (lineage) {
    const paragraph = document.createElement("p"); paragraph.className = "site-meta";
    text(paragraph, `Sporbarhet: ${lineage.original_external_key || lineage.source_external_key || "kilde"} → ${lineage.survivor_external_key || lineage.target_external_key || "mål"}.`);
    root.append(paragraph);
  }
  const conflicts = preview.conflicts || [];
  const canCommit = preview.can_commit === true && typeof preview.preview_hash === "string" && preview.preview_hash.length === 64;
  const notice = document.createElement("p");
  notice.className = canCommit ? "site-meta" : "warning";
  notice.setAttribute(canCommit ? "role" : "alert", canCommit ? "status" : "alert");
  text(notice, canCommit
    ? "Kontroller overføringer og feltene som beholdes. Sammenslåingen skjer først når du velger Bekreft og slå sammen."
    : `Denne sammenslåingen kan ikke utføres${conflicts.length ? `: ${conflicts.join(", ")}` : ". Lag en ny forhåndsvisning etter å ha kontrollert postene."}`);
  root.append(notice);
  return canCommit;
}

async function renderPromotionDraft(site, action, label, mount) {
  mount.replaceChildren();
  const form = document.createElement("form"); form.className = "detail-form promotion-form";
  form.dataset.action = action;
  const heading = document.createElement("h4"); text(heading, label); form.append(heading);
  const reason = textareaControl(""); reason.name = "reason"; reason.required = true; reason.maxLength = 2000;
  form.append(labeledControl("Begrunnelse for vurderingen", reason));

  const references = document.createElement("fieldset");
  const legend = document.createElement("legend");
  const evidenceAction = ["accept", "research"].includes(action);
  const needsObservations = ["field_verify", "confirm"].includes(action);
  text(legend, evidenceAction ? "Velg datert kildebevis" : "Velg effektive feltobservasjoner med funnet utfall");
  references.append(legend);
  const referenceInputs = document.createElement("div"); referenceInputs.className = "promotion-references";
  if (evidenceAction) {
    const readings = (site.sources || []).filter((source) => Number.isInteger(source.evidence_id) && source.evidence_id > 0 && Boolean(source.accessed_at));
    readings.forEach((source) => {
      const input = document.createElement("input"); input.type = "checkbox"; input.name = `evidence_${source.evidence_id}`; input.value = String(source.evidence_id); input.dataset.evidenceId = String(source.evidence_id);
      const labelNode = document.createElement("label");
      const title = source.title || source.registry_title || source.url || `Bevis ${source.evidence_id}`;
      const rights = source.rights_status === "unknown" ? " · rettigheter ukjent; tillatelse ikke bekreftet" : "";
      text(labelNode, `${title} · lest ${source.accessed_at}${source.role ? ` · ${source.role}` : ""}${rights}`);
      labelNode.prepend(input); referenceInputs.append(labelNode);
    });
    if (!readings.length) {
      const empty = document.createElement("p"); empty.className = "warning"; text(empty, "Ingen datert lesing med tilknyttet bevis-ID kan velges for dette stedet."); referenceInputs.append(empty);
    }
  } else if (needsObservations) {
    const found = (site.field_observations || []).filter((observation) => observation.data_status === "valid" && observation.status === "active" && !observation.withdrawn && observation.outcome === "found");
    found.forEach((observation) => {
      const input = document.createElement("input"); input.type = "checkbox"; input.name = `observation_${observation.id}`; input.value = String(observation.id); input.dataset.observationId = String(observation.id);
      const labelNode = document.createElement("label");
      text(labelNode, `${observation.observed_at} · ${observation.point_role || "rolle ukjent"} · ${observation.note || "Funnet vurdering"} (#${observation.id})`);
      labelNode.prepend(input); referenceInputs.append(labelNode);
    });
    if (!found.length) {
      const empty = document.createElement("p"); empty.className = "warning"; text(empty, "Ingen feltobservasjon er både markert effektiv og registrert med funnet utfall."); referenceInputs.append(empty);
    }
    const caveat = document.createElement("p"); caveat.className = "warning";
    text(caveat, "Kontroll fra utsiktspunkt dokumenterer ikke hvor selve anlegget ligger eller om noen har adgangstillatelse.");
    referenceInputs.append(caveat);
  }
  references.append(referenceInputs); form.append(references);

  const blockerStatus = document.createElement("div"); blockerStatus.className = "promotion-blockers"; blockerStatus.setAttribute("aria-live", "polite"); form.append(blockerStatus);
  let blockersReady = action !== "confirm";
  const unresolvedBlockers = [];
  if (action === "confirm" && site.location_review_required) unresolvedBlockers.push("Plasseringen må gjennomgås før bekreftelse.");
  const status = document.createElement("p"); status.setAttribute("role", "status"); form.append(status);
  const submit = document.createElement("button"); submit.type = "submit"; submit.className = "primary"; text(submit, `Lagre ${label.toLocaleLowerCase("nb")}`); form.append(submit);
  const reload = document.createElement("button"); reload.type = "button"; reload.className = "small"; text(reload, "Oppdater sted og sammenlign utkast"); reload.hidden = true;
  reload.addEventListener("click", () => refreshSitesAndDetail(site.id)); form.append(reload);
  const draftKey = `${site.id}:review:${action}`;
  const draft = bindFormDraft(form, draftKey, { expectedRevision: site.revision });
  if (draft.previous?.expectedRevision && draft.previous.expectedRevision !== site.revision) {
    showDraftNotice(form, `Stedsrevisjonen er nå ${site.revision}; utkastets tidligere revisjon var ${draft.previous.expectedRevision}. Kontroller alle kildevalg og begrunnelsen før innsending.`, true);
  }
  let inFlight = false;
  let staleConflict = false;
  const selectedEvidenceIds = () => [...form.querySelectorAll("input[data-evidence-id]:checked")].map((input) => Number(input.dataset.evidenceId));
  const selectedObservationIds = () => [...form.querySelectorAll("input[data-observation-id]:checked")].map((input) => Number(input.dataset.observationId));
  const updateReadiness = () => {
    const selected = evidenceAction ? selectedEvidenceIds().length > 0 : selectedObservationIds().length > 0;
    submit.disabled = inFlight || staleConflict || !reason.value.trim() || !selected || !blockersReady || unresolvedBlockers.length > 0;
  };
  form.addEventListener("input", updateReadiness);
  form.addEventListener("change", updateReadiness);
  if (action === "confirm") {
    const gateEpoch = state.authEpoch;
    const detailGeneration = state.detailGeneration;
    text(blockerStatus, "Kontrollerer åpne spørsmål om identitet og plassering …");
    try {
      const questionResult = await api(`/api/research/questions?external_key=${encodeURIComponent(site.external_key)}`);
      if (gateEpoch !== state.authEpoch || detailGeneration !== state.detailGeneration || !mount.isConnected) return;
      const questions = (questionResult.items || []).filter((question) => ["identity", "location"].includes(question.kind) && question.state !== "resolved");
      if (questions.length) unresolvedBlockers.push(`${questions.length} uavklart(e) spørsmål om identitet eller plassering må avklares.`);
      blockersReady = true;
      blockerStatus.replaceChildren();
      if (unresolvedBlockers.length) {
        unresolvedBlockers.forEach((message) => { const item = document.createElement("p"); item.className = "warning"; item.setAttribute("role", "alert"); text(item, message); blockerStatus.append(item); });
      } else {
        const clear = document.createElement("p"); clear.className = "site-meta"; text(clear, "Ingen åpne identitets-/plasseringsspørsmål eller lokaliseringsreview er registrert."); blockerStatus.append(clear);
      }
    } catch {
      if (gateEpoch !== state.authEpoch || detailGeneration !== state.detailGeneration || !mount.isConnected) return;
      unresolvedBlockers.push("Kunne ikke kontrollere åpne identitets-/plasseringsspørsmål; bekreftelse er sperret.");
      blockerStatus.replaceChildren(); const blocked = document.createElement("p"); blocked.className = "warning"; blocked.setAttribute("role", "alert"); text(blocked, unresolvedBlockers[unresolvedBlockers.length - 1]); blockerStatus.append(blocked);
      blockersReady = true;
    }
    updateReadiness();
  }
  updateReadiness();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity() || submit.disabled) return;
    const current = state.siteCache.get(site.id);
    if (current?.revision !== site.revision) {
      staleConflict = true; reload.hidden = false;
      showDraftNotice(form, "Stedsrevisjonen endret seg etter at skjemaet ble laget. Utkastet er beholdt. Oppdater stedet, sammenlign valgene og send på nytt bare hvis de fortsatt passer.", true);
      updateReadiness(); return;
    }
    inFlight = true; updateReadiness(); text(submit, "Lagrer vurdering …");
    const requestEpoch = state.authEpoch;
    try {
      await api(`/api/sites/${site.id}/review`, {
        method: "POST",
        body: JSON.stringify({ action, expected_revision: site.revision, reason: reason.value.trim(),
          evidence_ids: selectedEvidenceIds(), observation_ids: selectedObservationIds() }),
      });
      if (requestEpoch !== state.authEpoch) return;
      state.drafts.delete(draftKey);
      setStatus(`${label} lagret.`);
      await refreshSitesAndDetail(site.id);
    } catch (error) {
      if (requestEpoch !== state.authEpoch || isStaleRequest(error)) return;
      const stale = error.status === 409;
      if (stale) { staleConflict = true; reload.hidden = false; }
      showDraftNotice(form, stale
        ? `Serveren avviste vurderingen (${error.message}). Utkast og valg er beholdt; oppdater stedet og sammenlign før ny innsending.`
        : `Vurderingen ble ikke lagret: ${error.message}. Utkast og valg er beholdt.`, stale);
      text(status, error.message);
    } finally {
      if (requestEpoch === state.authEpoch) { inFlight = false; text(submit, `Lagre ${label.toLocaleLowerCase("nb")}`); updateReadiness(); }
    }
  });
  mount.append(form);
}

function renderLivssyklusActions(site, root) {
  const section = document.createElement("section");
  section.className = "detail-section";
  section.dataset.ownerOnly = "true";
  const heading = document.createElement("h3"); text(heading, "Livssyklus"); section.append(heading);
  const actions = document.createElement("div"); actions.className = "candidate-actions";
  const transitions = {
    candidate: [["Godta med kildegrunnlag", "accept", ""], ["Marker som kildegjennomgått", "research", ""], ["Marker som omtrentlig", "mark_approximate", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    approximate: [["Marker som kildegjennomgått", "research", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    likely: [["Marker som feltverifisert", "field_verify", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    "field-verified": [["Bekreft", "confirm", ""], ["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
    trusted: [["Marker som ødelagt eller fylt igjen", "mark_destroyed", "danger"]],
  };
  const promotionDraft = document.createElement("div"); promotionDraft.className = "promotion-draft";
  (transitions[site.status] || []).forEach(([label, action, style]) => {
    const button = document.createElement("button"); button.className = `small ${style}`; button.type = "button"; text(button, label);
    button.addEventListener("click", () => {
      if (["accept", "research", "field_verify", "confirm"].includes(action)) renderPromotionDraft(site, action, label, promotionDraft);
      else runReviewAction(site.id, action);
    }); actions.append(button);
  });
  if (site.status !== "rejected") {
    const reject = document.createElement("button"); reject.className = "small danger"; reject.type = "button"; text(reject, "Avvis");
    reject.addEventListener("click", () => runReviewAction(site.id, "reject")); actions.append(reject);
  } else {
    const restore = document.createElement("button"); restore.className = "small"; restore.type = "button"; text(restore, "Gjenopprett kandidat");
    restore.addEventListener("click", () => runReviewAction(site.id, "restore")); actions.append(restore);
  }
  section.append(actions, promotionDraft); root.append(section);
  if (site.status === "candidate") {
    const targets = [...state.siteCache.values()]
      .filter((candidate) => candidate.id !== site.id && candidate.status !== "rejected" && candidate.merged_into_id == null)
      .sort((first, second) => first.name.localeCompare(second.name));
    if (targets.length) {
      const mergeForm = document.createElement("form"); mergeForm.className = "detail-form merge-form";
      const target = document.createElement("select");
      target.name = "target_site_id"; target.required = true;
      const placeholder = document.createElement("option"); placeholder.value = ""; text(placeholder, "Velg sted som skal bestå"); target.append(placeholder);
      targets.forEach((candidate) => { const option = document.createElement("option"); option.value = candidate.id; option.dataset.revision = candidate.revision; text(option, `${candidate.name} — ${statusLabel(candidate.status)} (#${candidate.id})`); target.append(option); });
      const reason = textareaControl(""); reason.name = "merge_reason"; reason.required = true; reason.minLength = 1; reason.maxLength = 2000;
      reason.setAttribute("aria-describedby", `merge-reason-help-${site.id}`);
      const reasonHelp = document.createElement("small"); reasonHelp.id = `merge-reason-help-${site.id}`; text(reasonHelp, "Begrunn hvorfor dette er samme identitet. Begrunnelsen lagres i historikken.");
      const previewButton = document.createElement("button"); previewButton.className = "small"; previewButton.type = "submit"; previewButton.disabled = true; text(previewButton, "Forhåndsvis sammenslåing");
      const commitButton = document.createElement("button"); commitButton.className = "small danger"; commitButton.type = "button"; commitButton.hidden = true; commitButton.disabled = true; text(commitButton, "Bekreft og slå sammen");
      const previewRoot = document.createElement("div"); previewRoot.className = "merge-preview"; previewRoot.setAttribute("aria-live", "polite");
      let previewGeneration = 0;
      let previewReceipt = null;
      let committing = false;
      const syncPreviewButton = () => { previewButton.disabled = committing || !target.value || !reason.value.trim(); };
      const invalidatePreview = (message = "") => {
        previewGeneration += 1; previewReceipt = null;
        commitButton.hidden = true; commitButton.disabled = true;
        previewRoot.replaceChildren();
        if (message) { const notice = document.createElement("p"); notice.className = "site-meta"; text(notice, message); previewRoot.append(notice); }
        syncPreviewButton();
      };
      target.addEventListener("change", () => { invalidatePreview("Valget ble endret. Lag en ny forhåndsvisning."); });
      reason.addEventListener("input", () => { invalidatePreview("Begrunnelsen ble endret. Lag en ny forhåndsvisning."); });
      mergeForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!mergeForm.reportValidity() || committing) return;
        const sourceSnapshot = state.siteCache.get(site.id) || site;
        const targetSnapshot = state.siteCache.get(Number(target.value));
        if (!targetSnapshot || !Number.isInteger(sourceSnapshot.revision) || !Number.isInteger(targetSnapshot.revision)) {
          invalidatePreview("Kunne ikke lese begge revisjonene. Oppdater stedene før du forhåndsviser.");
          return;
        }
        const snapshot = {
          sourceId: site.id,
          sourceRevision: sourceSnapshot.revision,
          targetId: targetSnapshot.id,
          targetRevision: targetSnapshot.revision,
          reason: reason.value.trim(),
        };
        const generation = ++previewGeneration;
        const requestEpoch = state.authEpoch;
        const detailGeneration = state.detailGeneration;
        previewReceipt = null; commitButton.hidden = true; commitButton.disabled = true;
        previewButton.disabled = true; text(previewButton, "Forhåndsviser …");
        previewRoot.replaceChildren();
        try {
          const preview = await api(`/api/sites/${site.id}/merge-preview`, {
            method: "POST",
            body: JSON.stringify({ expected_revision: snapshot.sourceRevision, target_site_id: snapshot.targetId,
              target_expected_revision: snapshot.targetRevision, reason: snapshot.reason }),
          });
          if (requestEpoch !== state.authEpoch || detailGeneration !== state.detailGeneration || generation !== previewGeneration || !section.isConnected) return;
          const canCommit = renderMergePreview(previewRoot, preview);
          if (canCommit) {
            previewReceipt = { snapshot, previewHash: preview.preview_hash, generation };
            commitButton.hidden = false; commitButton.disabled = false;
          }
        } catch (error) {
          if (requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && generation === previewGeneration && section.isConnected && !isStaleRequest(error)) {
            const message = document.createElement("p"); message.className = "warning"; message.setAttribute("role", "alert"); text(message, error.status === 409 ? "Stedsrevisjonen er endret. Oppdater begge postene og lag en ny forhåndsvisning." : error.message); previewRoot.append(message);
          }
        } finally {
          if (requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && generation === previewGeneration && section.isConnected) {
            text(previewButton, "Forhåndsvis sammenslåing"); syncPreviewButton();
          }
        }
      });
      commitButton.addEventListener("click", async () => {
        const receipt = previewReceipt;
        if (!receipt || receipt.generation !== previewGeneration || committing) return;
        const currentSource = state.siteCache.get(receipt.snapshot.sourceId);
        const currentTarget = state.siteCache.get(receipt.snapshot.targetId);
        if (reason.value.trim() !== receipt.snapshot.reason || Number(target.value) !== receipt.snapshot.targetId ||
            currentSource?.revision !== receipt.snapshot.sourceRevision || currentTarget?.revision !== receipt.snapshot.targetRevision) {
          invalidatePreview("Stedsdata eller begrunnelse er endret. Oppdater postene og lag en ny forhåndsvisning før sammenslåing.");
          return;
        }
        committing = true; previewReceipt = null; commitButton.disabled = true; commitButton.hidden = true; syncPreviewButton();
        const requestEpoch = state.authEpoch;
        const detailGeneration = state.detailGeneration;
        try {
          await api(`/api/sites/${receipt.snapshot.sourceId}/review`, {
            method: "POST",
            body: JSON.stringify({ action: "merge", target_site_id: receipt.snapshot.targetId,
              expected_revision: receipt.snapshot.sourceRevision, target_expected_revision: receipt.snapshot.targetRevision,
              reason: receipt.snapshot.reason, merge_preview_hash: receipt.previewHash }),
          });
          if (requestEpoch !== state.authEpoch) return;
          updateNavigationUrl({ replace: true, site: String(receipt.snapshot.targetId) });
          await refreshSitesAndDetail(receipt.snapshot.targetId);
        } catch (error) {
          if (requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && !isStaleRequest(error)) {
            previewRoot.replaceChildren();
            const message = document.createElement("p"); message.className = "warning"; message.setAttribute("role", "alert");
            text(message, error.status === 409 ? "Sammenslåingen ble avvist fordi forhåndsvisningen eller en stedsrevisjon er utdatert. Oppdater begge postene og forhåndsvis på nytt; ingenting sendes automatisk på nytt." : `Sammenslåingen kunne ikke bekreftes: ${error.message}. Les inn status og lag en ny forhåndsvisning før du prøver igjen.`);
            previewRoot.append(message);
          }
        } finally {
          committing = false; syncPreviewButton();
        }
      });
      mergeForm.append(
        labeledControl("Slå sammen duplikat med", target),
        labeledControl("Begrunn sammenslåingen", reason), reasonHelp,
        previewButton, commitButton, previewRoot,
      );
      target.addEventListener("change", syncPreviewButton);
      reason.addEventListener("input", syncPreviewButton);
      section.append(mergeForm);
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
  section.dataset.siteId = String(site.id);
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
  const draft = bindFormDraft(form, `${site.id}:site`);
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
      draft.clear();
      setStatus("Stedsendringer lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) {
      if (!isStaleRequest(error)) {
        showFieldErrors(form, error); setStatus(error.message);
        if (error.code === "REVISION_MISMATCH") await showRevisionConflict(form, `${site.id}:site`, site.id);
      }
    }
  });
  section.append(form); root.append(section);
  const approachForm = document.createElement("form"); approachForm.className = "observation-form";
  const approachBreddegrad = inputControl("number", site.approach_latitude); approachBreddegrad.name = "latitude"; approachBreddegrad.step = "0.000001"; approachBreddegrad.min = "-90"; approachBreddegrad.max = "90";
  const approachLengdegrad = inputControl("number", site.approach_longitude); approachLengdegrad.name = "longitude"; approachLengdegrad.step = "0.000001"; approachLengdegrad.min = "-180"; approachLengdegrad.max = "180";
  const approachTilgang = selectControl(["unknown", "public"], site.approach_access || "unknown"); approachTilgang.name = "access";
  const approachNote = textareaControl(site.approach_note || ""); approachNote.name = "note"; approachNote.required = true;
  const approachGrid = document.createElement("div"); approachGrid.className = "detail-grid";
  approachGrid.append(labeledControl("Tilnærming breddegrad", approachBreddegrad), labeledControl("Tilnærming lengdegrad", approachLengdegrad), labeledControl("Tilgang ved tilnærming", approachTilgang));
  approachForm.append(approachGrid, labeledControl("Notat om tilnærmingsvurdering", approachNote));
  const approachSave = document.createElement("button"); approachSave.className = "primary"; approachSave.type = "submit"; text(approachSave, "Lagre vurdering av offentlig tilnærming"); approachForm.append(approachSave);
  const approachDraft = bindFormDraft(approachForm, `${site.id}:approach`);
  approachForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFieldErrors(approachForm);
    const numberOrNull = (value) => value === "" ? null : Number(value);
    try {
      await api(`/api/sites/${site.id}/approach`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: site.revision, latitude: numberOrNull(approachBreddegrad.value), longitude: numberOrNull(approachLengdegrad.value), access: approachTilgang.value, note: approachNote.value.trim() }),
      });
      approachDraft.clear();
      setStatus("Tilnærmingsvurdering lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) {
      if (!isStaleRequest(error)) {
        showFieldErrors(approachForm, error); setStatus(error.message);
        if (error.code === "REVISION_MISMATCH") await showRevisionConflict(approachForm, `${site.id}:approach`, site.id);
      }
    }
  });
  const approachHeading = document.createElement("h3"); text(approachHeading, "Vurdering av offentlig tilnærming"); section.append(approachHeading, approachForm);
  if (site.location_review_required) {
    const reviewForm = document.createElement("form"); reviewForm.className = "observation-form";
    const reason = textareaControl(); reason.name = "reason"; reason.required = true; reason.placeholder = "Hvorfor er den nye plasseringen vurdert?";
    const saveReview = document.createElement("button"); saveReview.className = "primary"; saveReview.type = "submit"; text(saveReview, "Marker plassering som vurdert");
    reviewForm.append(labeledControl("Begrunnelse for lokaliseringsreview", reason), saveReview);
    const reviewDraft = bindFormDraft(reviewForm, `${site.id}:location-review`);
    reviewForm.addEventListener("submit", async (event) => {
      event.preventDefault(); saveReview.disabled = true;
      clearFieldErrors(reviewForm);
      try {
        await api(`/api/sites/${site.id}/location-review`, { method: "POST", body: JSON.stringify({ reason: reason.value.trim(), expected_revision: site.revision }) });
        reviewDraft.clear();
        setStatus("Lokaliseringsreview lagret."); await refreshSitesAndDetail(site.id);
      } catch (error) {
        if (!isStaleRequest(error)) {
          showFieldErrors(reviewForm, error); setStatus(error.message); saveReview.disabled = false;
          if (error.code === "REVISION_MISMATCH") await showRevisionConflict(reviewForm, `${site.id}:location-review`, site.id);
        }
      }
    });
    section.append(reviewForm);
  }
}

const CONTENT_SECTION_LABELS = {
  about: "Om stedet", current: "Hva finnes her i dag", visit_summary: "Hva kan jeg se?",
  physical_access: "Fysisk tilgjengelighet", access_rules: "Adgangsregler", uncertainty: "Usikkerhet",
};
const CONTENT_CERTAINTY_LABELS = {
  source_supported: "Kildestøttet", supported: "Kildestøttet", uncertain: "Uavklart", unknown: "Ikke dokumentert",
};
const CONTENT_RESEARCH_STATES = {
  curated: "Kuratert", researched_pending: "Research funnet – venter på kuratering", identity_review: "Identitet må avklares",
};
const contentId = (prefix) => `${prefix}-${crypto.randomUUID()}`;

function seedContentDocument(site) {
  if (site.content_document) return structuredClone(site.content_document);
  const enrichment = site.enrichment || {};
  const sources = (enrichment.sources || []).map((source) => ({
    id: source.id, title: source.title, url: source.url,
    ...(source.accessed_at ? { accessed_at: source.accessed_at } : {}),
  }));
  const claims = [];
  const addClaims = (section, entries) => (entries || []).forEach((claim) => claims.push({
    id: claim.id || contentId("claim"), section, text: claim.text || "",
    certainty: claim.certainty === "supported" ? "source_supported" : claim.certainty || "unknown",
    source_ids: (claim.sources || []).map((source) => source.id).filter(Boolean),
    ...(claim.citations !== undefined ? { citations: structuredClone(claim.citations) } : {}),
  }));
  addClaims("about", enrichment.about);
  addClaims("current", enrichment.present_day);
  addClaims("visit_summary", enrichment.visit_summary);
  addClaims("physical_access", enrichment.visit_access?.physical_access);
  addClaims("access_rules", enrichment.visit_access?.access_rules);
  addClaims("uncertainty", enrichment.uncertainty);
  return {
    external_key: site.external_key,
    display_name: enrichment.display_name || "",
    kind_label: enrichment.kind_label || "",
    research_state: enrichment.research_state || "curated",
    reviewed_at: enrichment.reviewed_at || "",
    retired_claim_ids: structuredClone(enrichment.retired_claim_ids || []),
    sources: sources.length ? sources : [{ id: contentId("source"), title: "", url: "" }],
    claims: claims.length ? claims : [{ id: contentId("claim"), section: "about", text: "", certainty: "unknown", source_ids: [] }],
  };
}

function documentChangeLabels(before, after) {
  if (!before || !after) return [];
  const changed = [];
  for (const field of ["display_name", "kind_label", "research_state", "reviewed_at", "sources", "claims"]) {
    if (JSON.stringify(before[field]) !== JSON.stringify(after[field])) changed.push(field);
  }
  return changed;
}

function renderContentEditor(site, root) {
  const key = `${site.id}:content`;
  const section = compactSection("Rediger innhold", "content-editor");
  const savedDraft = state.drafts.get(key);
  const baseDocument = seedContentDocument(site);
  const initial = savedDraft?.document || baseDocument;
  const persistedDocument = savedDraft?.baseDocument || baseDocument;
  const persistedClaimIds = new Set((persistedDocument.claims || []).map((claim) => claim.id));
  const retiredClaimIds = new Set(initial.retired_claim_ids || persistedDocument.retired_claim_ids || []);
  const retiredNote = document.createElement("p"); retiredNote.className = "site-meta";
  const renderRetiredNote = () => {
    retiredNote.hidden = retiredClaimIds.size === 0;
    text(retiredNote, `Pensjonerte påstands-ID-er beholdes for sporbarhet: ${[...retiredClaimIds].join(", ")}.`);
  };
  renderRetiredNote();
  const initialClaimById = new Map((initial.claims || []).map((claim) => [claim.id, claim]));
  const form = document.createElement("form"); form.className = "detail-form";
  form.dataset.revision = String(savedDraft?.expectedRevision || site.revision);
  form.dataset.draftKey = key;
  const displayName = inputControl("text", initial.display_name || ""); displayName.name = "display_name"; displayName.required = true; displayName.maxLength = 500;
  const kindLabel = inputControl("text", initial.kind_label || ""); kindLabel.name = "kind_label"; kindLabel.required = true; kindLabel.maxLength = 200;
  const researchState = selectControl(Object.keys(CONTENT_RESEARCH_STATES), initial.research_state || "curated", CONTENT_RESEARCH_STATES); researchState.name = "research_state";
  const reviewedAt = inputControl("date", initial.reviewed_at || ""); reviewedAt.name = "reviewed_at"; reviewedAt.required = true;
  const identity = document.createElement("div"); identity.className = "detail-grid";
  identity.append(labeledControl("Visningsnavn", displayName), labeledControl("Stedstype", kindLabel), labeledControl("Researchtilstand", researchState), labeledControl("Kildeunderlag kontrollert", reviewedAt));
  form.append(identity);

  const sourcesFieldset = document.createElement("fieldset");
  const sourcesLegend = document.createElement("legend"); text(sourcesLegend, "Kilder"); sourcesFieldset.append(sourcesLegend);
  const sourceRows = document.createElement("div"); sourceRows.dataset.contentSources = "true"; sourcesFieldset.append(sourceRows);
  const sourceAdd = document.createElement("button"); sourceAdd.type = "button"; sourceAdd.className = "small"; text(sourceAdd, "Legg til kilde"); sourcesFieldset.append(sourceAdd); form.append(sourcesFieldset);

  const readSources = () => [...sourceRows.querySelectorAll("[data-source-id]")].map((row) => ({
    id: row.dataset.sourceId,
    title: row.querySelector('[name="source-title"]').value.trim(),
    url: row.querySelector('[name="source-url"]').value.trim(),
    ...(row.querySelector('[name="source-accessed-at"]').value ? { accessed_at: row.querySelector('[name="source-accessed-at"]').value } : {}),
  }));
  const sourceRow = (source = {}) => {
    const row = document.createElement("div"); row.dataset.sourceId = source.id || contentId("source");
    const title = inputControl("text", source.title || ""); title.name = "source-title"; title.required = true; title.maxLength = 300;
    const url = inputControl("url", source.url || ""); url.name = "source-url"; url.required = true;
    const accessed = inputControl("date", source.accessed_at || ""); accessed.name = "source-accessed-at";
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "small danger"; text(remove, "Fjern kilde");
    remove.addEventListener("click", () => { row.remove(); renderSourceChoices(); persistDraft(); });
    title.addEventListener("input", () => renderSourceChoices());
    row.append(labeledControl("Tittel", title), labeledControl("URL", url), labeledControl("Lest dato", accessed), remove); return row;
  };
  (initial.sources || []).forEach((source) => sourceRows.append(sourceRow(source)));

  const claimsFieldset = document.createElement("fieldset");
  const claimsLegend = document.createElement("legend"); text(claimsLegend, "Påstander"); claimsFieldset.append(claimsLegend);
  const claimRows = document.createElement("div"); claimRows.dataset.contentClaims = "true"; claimsFieldset.append(claimRows);
  const claimAdd = document.createElement("button"); claimAdd.type = "button"; claimAdd.className = "small"; text(claimAdd, "Legg til påstand"); claimsFieldset.append(claimAdd); form.append(claimsFieldset);

  function renderSourceChoices() {
    const sources = readSources();
    claimRows.querySelectorAll("[data-source-choices]").forEach((choices) => {
      const checked = [...choices.querySelectorAll('input[type="checkbox"]:checked')];
      const selected = new Set(choices.dataset.initialized === "true" ? checked.map((input) => input.value) : JSON.parse(choices.dataset.selected || "[]"));
      const legend = choices.querySelector("legend"); choices.replaceChildren(legend);
      sources.forEach((source) => {
        const input = document.createElement("input"); input.type = "checkbox"; input.value = source.id; input.checked = selected.has(source.id); input.name = "claim-source";
        const label = document.createElement("label"); text(label, source.title || source.id); label.prepend(input); choices.append(label);
      });
      choices.dataset.selected = JSON.stringify([...selected].filter((id) => sources.some((source) => source.id === id)));
      choices.dataset.initialized = "true";
    });
  }
  const claimRow = (claim = {}) => {
    const row = document.createElement("fieldset"); row.dataset.claimId = claim.id || contentId("claim");
    const claimSection = selectControl(Object.keys(CONTENT_SECTION_LABELS), claim.section || "about", CONTENT_SECTION_LABELS); claimSection.name = "claim-section";
    const claimText = textareaControl(claim.text || ""); claimText.name = "claim-text"; claimText.required = true; claimText.maxLength = claim.section === "visit_summary" ? 400 : 2000;
    const certaintyValue = claim.certainty === "supported" ? "source_supported" : claim.certainty || "unknown";
    const certainty = selectControl(["source_supported", "uncertain", "unknown"], certaintyValue, CONTENT_CERTAINTY_LABELS); certainty.name = "claim-certainty";
    const choices = document.createElement("fieldset"); choices.dataset.sourceChoices = "true"; choices.dataset.selected = JSON.stringify(claim.source_ids || []);
    const choicesLegend = document.createElement("legend"); text(choicesLegend, "Kildetilknytning"); choices.append(choicesLegend);
    const updateSummaryLimit = () => { claimText.maxLength = claimSection.value === "visit_summary" ? 400 : 2000; };
    claimText.addEventListener("input", updateSummaryLimit); claimSection.addEventListener("change", updateSummaryLimit);
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "small danger"; text(remove, "Fjern påstand");
    remove.addEventListener("click", () => {
      if (persistedClaimIds.has(row.dataset.claimId)) { retiredClaimIds.add(row.dataset.claimId); renderRetiredNote(); }
      row.remove(); persistDraft();
    });
    row.append(labeledControl("Seksjon", claimSection), labeledControl("Tekst", claimText), labeledControl("Sikkerhet", certainty), choices, remove);
    claimRows.append(row); renderSourceChoices();
  };
  (initial.claims || []).forEach((claim) => claimRow(claim));
  if (!claimRows.children.length) claimRow();
  sourceAdd.addEventListener("click", () => { sourceRows.append(sourceRow()); renderSourceChoices(); persistDraft(); });
  claimAdd.addEventListener("click", () => { claimRow(); persistDraft(); });

  const readClaims = () => [...claimRows.querySelectorAll("[data-claim-id]")].map((row) => ({
    ...(initialClaimById.get(row.dataset.claimId)?.citations !== undefined
      ? { citations: structuredClone(initialClaimById.get(row.dataset.claimId).citations) } : {}),
    id: row.dataset.claimId,
    section: row.querySelector('[name="claim-section"]').value,
    text: row.querySelector('[name="claim-text"]').value.trim(),
    certainty: row.querySelector('[name="claim-certainty"]').value,
    source_ids: [...row.querySelectorAll('input[name="claim-source"]:checked')].map((input) => input.value),
  }));
  const readDocument = () => ({
    external_key: initial.external_key || site.external_key,
    display_name: displayName.value.trim(), kind_label: kindLabel.value.trim(),
    research_state: researchState.value, reviewed_at: reviewedAt.value,
    sources: readSources(), claims: readClaims(), retired_claim_ids: [...retiredClaimIds],
  });
  const persistDraft = () => saveDraft(key, {
    document: readDocument(), baseDocument: persistedDocument,
    expectedRevision: Number(form.dataset.revision),
  });
  form.addEventListener("input", persistDraft);
  form.addEventListener("change", persistDraft);
  if (savedDraft) showDraftNotice(form, "Innholdsutkastet fra denne økten er gjenopprettet. Det lagres bare i minnet og slettes når arbeidsflaten låses.");

  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Lagre innhold"); form.append(save);
  form.append(retiredNote);
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "small"; text(cancel, "Lukk");
  cancel.addEventListener("click", () => { section.open = false; section.querySelector("summary").focus({ preventScroll: true }); }); form.append(cancel);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); clearFieldErrors(form);
    const documentValue = readDocument();
    const visitSummaries = documentValue.claims.filter((claim) => claim.section === "visit_summary");
    if (visitSummaries.length > 1 || visitSummaries.some((claim) => claim.text.length > 400)) {
      showDraftNotice(form, "Bruk høyst én besøksoppsummering på maksimalt 400 tegn.", true);
      if (visitSummaries.length > 1) {
        [...claimRows.querySelectorAll("[data-claim-id]")]
          .find((row) => row.dataset.claimId === visitSummaries[1].id)
          ?.querySelector('[name="claim-text"]')?.focus();
      }
      return;
    }
    try {
      await api(`/api/sites/${site.id}/content`, {
        method: "PATCH",
        body: JSON.stringify({ expected_revision: Number(form.dataset.revision), display_name: documentValue.display_name,
          kind_label: documentValue.kind_label, research_state: documentValue.research_state, reviewed_at: documentValue.reviewed_at,
          sources: documentValue.sources, claims: documentValue.claims, retired_claim_ids: documentValue.retired_claim_ids }),
      });
      state.drafts.delete(key);
      setStatus("Innhold lagret."); await refreshSitesAndDetail(site.id);
    } catch (error) {
      if (!isStaleRequest(error)) {
        showFieldErrors(form, error); setStatus(error.message);
        if (error.code === "REVISION_MISMATCH") {
          const authEpoch = state.authEpoch;
          let latest = null;
          try { latest = await api(`/api/sites/${site.id}`); } catch { /* Keep the in-memory editor draft. */ }
          if (authEpoch === state.authEpoch && state.drafts.has(key)) {
            const changed = documentChangeLabels(documentValue, latest?.content_document);
            if (latest?.revision) form.dataset.revision = String(latest.revision);
            persistDraft();
            showDraftNotice(form, `Serverversjonen er nyere${latest?.revision ? ` (revisjon ${latest.revision})` : ""}. Utkastet er bevart. Sammenlign endringene${changed.length ? ` i ${changed.join(", ")}` : ""}; lagre på nytt bare når du vil anvende hele dette dokumentet.`, true);
            section.open = true;
          }
        }
      }
    }
  });
  section.append(form); root.append(section);
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
  const draftKey = `${site.id}:observation`;
  let requestId = state.drafts.get(draftKey)?.requestId || crypto.randomUUID();
  if (!state.drafts.has(draftKey)) saveDraft(draftKey, { requestId, fields: {} });
  const localToday = new Date(); localToday.setMinutes(localToday.getMinutes() - localToday.getTimezoneOffset());
  const observedAt = inputControl("date", localToday.toISOString().slice(0, 10)); observedAt.name = "observed_at"; observedAt.required = true;
  const outcome = selectControl(["found", "not_found", "inaccessible", "needs_follow_up"], "found", { found: "Funnet", not_found: "Ikke funnet", inaccessible: "Utilgjengelig", needs_follow_up: "Må følges opp" }); outcome.name = "outcome";
  const note = textareaControl(); note.name = "note"; note.required = true; note.placeholder = "Hva ble observert?";
  const latitude = inputControl("number"); latitude.name = "latitude"; latitude.step = "0.000001"; latitude.min = "-90"; latitude.max = "90";
  const longitude = inputControl("number"); longitude.name = "longitude"; longitude.step = "0.000001"; longitude.min = "-180"; longitude.max = "180";
  const pointRole = selectControl(["feature", "entrance", "viewpoint", "unknown"], "unknown"); pointRole.name = "point_role";
  const uncertainty = inputControl("number"); uncertainty.name = "uncertainty_m"; uncertainty.min = "0"; uncertainty.step = "1";
  const capturedAt = inputControl("hidden"); capturedAt.name = "captured_at";
  const reportedAccuracy = inputControl("hidden"); reportedAccuracy.name = "reported_accuracy_m";
  const useDeviceLocation = document.createElement("button"); useDeviceLocation.type = "button"; useDeviceLocation.className = "small"; text(useDeviceLocation, "Hent posisjon én gang fra enheten");
  const geolocationStatus = document.createElement("p"); geolocationStatus.className = "site-meta"; geolocationStatus.setAttribute("role", "status"); geolocationStatus.setAttribute("aria-live", "polite");
  text(geolocationStatus, "Posisjon brukes bare når du velger knappen. Koordinatene lagres ikke før du lagrer observasjonen.");
  const location = textareaControl(); location.name = "observed_location_text";
  const access = textareaControl(); access.name = "access_notes";
  const photos = textareaControl(); photos.name = "photo_urls"; photos.placeholder = "Én bilde-URL per linje";
  const grid = document.createElement("div"); grid.className = "detail-grid";
  grid.append(labeledControl("Dato", observedAt), labeledControl("Utfall", outcome), labeledControl("Punktrolle", pointRole), labeledControl("Radius (m)", uncertainty), labeledControl("Observert breddegrad", latitude), labeledControl("Observert lengdegrad", longitude));
  form.append(grid, capturedAt, reportedAccuracy, useDeviceLocation, geolocationStatus, labeledControl("Observasjonsnotat", note), labeledControl("Observert sted", location), labeledControl("Tilgangsnotat", access), labeledControl("Bilde-URL-er", photos));
  const save = document.createElement("button"); save.className = "primary"; save.type = "submit"; text(save, "Lagre observasjon"); form.append(save);
  let observationDraftGeneration = 0;
  let hasDeviceMetadata = false;
  form.addEventListener("input", (event) => {
    observationDraftGeneration += 1;
    if (hasDeviceMetadata && (event.target === latitude || event.target === longitude)) {
      capturedAt.value = ""; reportedAccuracy.value = ""; hasDeviceMetadata = false;
      text(geolocationStatus, "Koordinatene er endret manuelt; måletid og målt nøyaktighet er fjernet.");
    }
  });
  form.addEventListener("change", () => { observationDraftGeneration += 1; });
  const draft = bindFormDraft(form, draftKey);
  hasDeviceMetadata = Boolean(capturedAt.value);
  if (hasDeviceMetadata) text(geolocationStatus, `Utkastet har enhetsmåling fra ${new Date(capturedAt.value).toLocaleString()}${reportedAccuracy.value ? ` med rapportert nøyaktighet ${Math.round(Number(reportedAccuracy.value))} m` : ""}.`);
  useDeviceLocation.addEventListener("click", () => {
    if (!navigator.geolocation?.getCurrentPosition) {
      text(geolocationStatus, "Denne nettleseren tilbyr ikke posisjon. Eventuelle manuelt angitte koordinater er beholdt."); return;
    }
    const requestEpoch = state.authEpoch;
    const detailGeneration = state.detailGeneration;
    const draftGeneration = observationDraftGeneration;
    useDeviceLocation.disabled = true; text(useDeviceLocation, "Henter én posisjonsmåling …");
    const restoreButton = () => { if (requestEpoch === state.authEpoch && form.isConnected) { useDeviceLocation.disabled = false; text(useDeviceLocation, "Hent posisjon én gang fra enheten"); } };
    const stillCurrent = () => requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && form.isConnected;
    const fail = (message) => { if (stillCurrent()) text(geolocationStatus, `${message} Eventuelle manuelt angitte koordinater er beholdt.`); restoreButton(); };
    navigator.geolocation.getCurrentPosition((position) => {
      if (!stillCurrent()) return;
      if (draftGeneration !== observationDraftGeneration) {
        fail("Observasjonsutkastet ble endret mens posisjonen ble hentet; målingen ble ikke brukt."); return;
      }
      const timestamp = Number(position.timestamp);
      const now = Date.now();
      const accuracy = Number(position.coords?.accuracy);
      const latitudeValue = Number(position.coords?.latitude);
      const longitudeValue = Number(position.coords?.longitude);
      if (!Number.isFinite(timestamp) || timestamp > now + 60_000 || now - timestamp > 300_000 ||
          !Number.isFinite(latitudeValue) || latitudeValue < -90 || latitudeValue > 90 ||
          !Number.isFinite(longitudeValue) || longitudeValue < -180 || longitudeValue > 180 ||
          (Number.isFinite(accuracy) && (accuracy <= 0 || accuracy > 100_000))) {
        fail("Posisjonsmålingen mangler gyldig, fersk metadata og ble ikke brukt."); return;
      }
      latitude.value = String(latitudeValue); longitude.value = String(longitudeValue);
      capturedAt.value = new Date(timestamp).toISOString();
      reportedAccuracy.value = Number.isFinite(accuracy) ? String(accuracy) : "";
      hasDeviceMetadata = true;
      const accuracyLabel = Number.isFinite(accuracy) ? ` med rapportert nøyaktighet ${Math.round(accuracy)} m` : " uten rapportert nøyaktighet";
      text(geolocationStatus, `En måling fra ${new Date(timestamp).toLocaleString()} ble lagt i observasjonsutkastet${accuracyLabel}. Nøyaktigheten er målemetadata, ikke stedets usikkerhetsradius.`);
      draft.persist();
      restoreButton();
    }, (error) => {
      const message = error?.code === 1 ? "Posisjonstilgang ble avslått." : error?.code === 3 ? "Posisjonsmålingen tok for lang tid." : "Posisjon kunne ikke leses.";
      fail(message);
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 10_000 });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFieldErrors(form);
    const numberOrUndefined = (value) => value === "" ? undefined : Number(value);
    const fields = formSnapshot(form);
    const previousDraft = state.drafts.get(draftKey);
    if (previousDraft?.pendingPayload && JSON.stringify(fields) !== JSON.stringify(previousDraft.fields)) {
      showDraftNotice(form, "En tidligere innsending har ukjent utfall. Les tilbake eller send samme uendrede observasjon før du endrer den.", true);
      return;
    }
    const payload = previousDraft?.pendingPayload || {
      request_id: requestId,
      observed_at: observedAt.value, outcome: outcome.value, note: note.value.trim(),
      latitude: numberOrUndefined(latitude.value), longitude: numberOrUndefined(longitude.value),
      point_role: pointRole.value, uncertainty_m: numberOrUndefined(uncertainty.value),
      captured_at: capturedAt.value || undefined,
      reported_accuracy_m: reportedAccuracy.value === "" ? undefined : Number(reportedAccuracy.value),
      observed_location_text: location.value.trim() || undefined, access_notes: access.value.trim() || undefined,
      photo_urls: photos.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
    };
    saveDraft(draftKey, { ...previousDraft, requestId, fields, pendingPayload: payload });
    try {
      await api(`/api/sites/${site.id}/observations`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      draft.clear();
      setStatus("Feltobservasjon lagret.");
      await refreshSitesAndDetail(site.id);
    } catch (error) {
      if (!isStaleRequest(error)) {
        showFieldErrors(form, error); setStatus(error.message);
        if (error.status && error.status < 500 && error.code !== "IDEMPOTENCY_CONFLICT") {
          requestId = crypto.randomUUID();
          const current = state.drafts.get(draftKey);
          if (current) saveDraft(draftKey, { ...current, requestId, pendingPayload: null, fields });
        }
        if (!error.status || error.status >= 500) {
          try {
            const latest = await api(`/api/sites/${site.id}`);
            const observed = (latest.field_observations || []).some((item) => observationMatchesPayload(item, payload));
            if (observed) {
              draft.clear(); setStatus("Tilbakelesing bekrefter at observasjonen ble lagret.");
              await refreshSitesAndDetail(site.id); return;
            }
          } catch { /* Keep the exact payload and request ID for deliberate reconciliation. */ }
          if (state.drafts.has(draftKey)) showDraftNotice(form, "Lagringsutfallet er fortsatt uklart. Utkast og forespørsels-ID beholdes; send bare samme observasjon på nytt.", true);
        }
      }
    }
  });
  section.append(form); root.append(section);
}

function observationMatchesPayload(observation, payload) {
  const normalizeNumber = (value) => value == null ? null : Number(value);
  return observation.observed_at === payload.observed_at && observation.outcome === payload.outcome &&
    observation.note === payload.note && normalizeNumber(observation.latitude) === normalizeNumber(payload.latitude) &&
    normalizeNumber(observation.longitude) === normalizeNumber(payload.longitude) && observation.point_role === payload.point_role &&
    normalizeNumber(observation.uncertainty_m) === normalizeNumber(payload.uncertainty_m) &&
    (observation.observed_location_text || undefined) === payload.observed_location_text &&
    (observation.access_notes || undefined) === payload.access_notes &&
    JSON.stringify(observation.photo_urls || []) === JSON.stringify(payload.photo_urls || []);
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
  if (!state.detailOrigin || state.detailOrigin.siteId !== id) {
    state.detailOrigin = { siteId: id, surface: state.surface, origin: state.surface === "review" ? "candidate" : "list" };
  }
  setSurface("review", { scroll: false });
  const detailGeneration = ++state.detailGeneration;
  const navigationGeneration = state.navigationGeneration;
  const detailRead = beginRead("detail");
  const panel = $("detail-panel");
  const root = $("site-detail");
  panel.classList.add("is-selected");
  $("close-detail").hidden = false;
  text($("close-detail"), state.detailOrigin.surface === "map" ? "Tilbake til kart" : "Tilbake til liste");
  text(root, "Laster stedsdetaljer ...");
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
  $("site-detail-heading").focus({ preventScroll: true });
  try {
    const eventsRequest = hasSessionScope("history:read")
      ? api(`/api/sites/${id}/events?limit=50`, { signal: detailRead.controller.signal })
      : Promise.resolve([]);
    const [site, events] = await Promise.all([
      api(`/api/sites/${id}`, { signal: detailRead.controller.signal }), eventsRequest,
    ]);
    if (!currentRead(detailRead) || requestEpoch !== state.authEpoch || detailGeneration !== state.detailGeneration || navigationGeneration !== state.navigationGeneration) return;
    state.siteCache.set(site.id, site);
    text($("site-detail-heading"), `Stedsdetaljer: ${siteDisplayName(site)}`);
    root.replaceChildren();
    appendDataStatus(root, site, "Stedsdata");
    if (site.route_eligible === false && site.route_blocking_reason) {
      const routeStatus = document.createElement("p"); routeStatus.className = "site-meta";
      text(routeStatus, `Rute utilgjengelig: ${site.route_blocking_reason}`); root.append(routeStatus);
    }
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
    (site.sources || []).forEach((source) => { const li = document.createElement("li"); if (source.url) { const link = document.createElement("a"); link.href = source.url; link.target = "_blank"; link.rel = "noreferrer"; text(link, source.title || source.url); li.append(link); } else { const withheld = document.createElement("span"); text(withheld, source.title || "Referanse holdt tilbake"); li.append(withheld); } const sourceMeta = document.createElement("div"); sourceMeta.className = "site-meta"; text(sourceMeta, [source.source_type || "kilde", source.registry_title && `registertittel: ${source.registry_title}`, source.published_at && `publisert ${source.published_at}`, source.accessed_at && `lest ${source.accessed_at}`, source.url_status, source.citation_status === "historical_metadata_unknown" && "Historisk tittel/type ukjent; registermetadata vises"].filter(Boolean).join(" | ")); li.append(sourceMeta); const excerpt = document.createElement("div"); excerpt.className = "site-meta"; text(excerpt, source.excerpt); li.append(excerpt); sourceList.append(li); });
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
    renderContentEditor(site, root);
    renderObservations(site, root);
    if (currentRead(detailRead) && requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && navigationGeneration === state.navigationGeneration && root.isConnected) {
      updateNavigationUrl({ site: id });
      if (hasSessionRole("owner")) document.dispatchEvent(new CustomEvent("bunkerkartet:detail", { detail: { site, root } }));
    }
  } catch (error) {
    if (!isStaleRequest(error) && currentRead(detailRead) && requestEpoch === state.authEpoch && detailGeneration === state.detailGeneration && navigationGeneration === state.navigationGeneration) {
      text($("site-detail"), error.message);
    }
  } finally {
    finishRead(detailRead);
  }
}

function parseStrictJson(source) {
  let index = 0;
  const whitespace = () => { while (/\s/.test(source[index] || "")) index += 1; };
  const fail = (message) => { throw new Error(message); };
  function stringToken() {
    const start = index;
    index += 1;
    while (index < source.length) {
      const character = source[index++];
      if (character === '"') return JSON.parse(source.slice(start, index));
      if (character === "\\") index += 1;
      else if (character.charCodeAt(0) < 0x20) fail("JSON-strengen er ugyldig.");
    }
    return fail("JSON-strengen er ufullstendig.");
  }
  function value(depth = 0) {
    whitespace();
    if (depth > 64) return fail("JSON-filen har for mange nøstingsnivåer.");
    if (source[index] === '"') { stringToken(); return; }
    if (source[index] === "{") {
      index += 1; whitespace();
      const keys = new Set();
      if (source[index] === "}") { index += 1; return; }
      while (index < source.length) {
        whitespace();
        if (source[index] !== '"') fail("JSON-objektet har en ugyldig nøkkel.");
        const key = stringToken();
        if (keys.has(key)) fail("JSON-filen inneholder en duplisert nøkkel.");
        keys.add(key); whitespace();
        if (source[index++] !== ":") fail("JSON-objektet mangler kolon.");
        value(depth + 1); whitespace();
        if (source[index] === "}") { index += 1; return; }
        if (source[index++] !== ",") fail("JSON-objektet mangler komma.");
      }
      return fail("JSON-objektet er ufullstendig.");
    }
    if (source[index] === "[") {
      index += 1; whitespace();
      if (source[index] === "]") { index += 1; return; }
      while (index < source.length) {
        value(depth + 1); whitespace();
        if (source[index] === "]") { index += 1; return; }
        if (source[index++] !== ",") fail("JSON-listen mangler komma.");
      }
      return fail("JSON-listen er ufullstendig.");
    }
    const token = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(source.slice(index));
    if (!token) return fail("JSON-filen inneholder en ugyldig verdi.");
    if (/^-/.test(token[0]) || /^\d/.test(token[0])) {
      if (!Number.isFinite(Number(token[0]))) return fail("JSON-filen inneholder et ugyldig tall.");
    }
    index += token[0].length;
  }
  value(); whitespace();
  if (index !== source.length) fail("JSON-filen inneholder data etter dokumentet.");
  return JSON.parse(source);
}

async function readImport(file) {
  const requestEpoch = state.authEpoch;
  const generation = state.importGeneration;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error("JSON-filen er større enn 2 MiB.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    const content = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    if (requestEpoch !== state.authEpoch || generation !== state.importGeneration) return;
    if (content.startsWith("\uFEFF")) throw new Error("JSON-filen må være UTF-8 uten BOM.");
    parseStrictJson(content);
    state.pendingImport = { bytes };
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
    const result = await api("/api/admin/imports/preview", { method: "POST", body: new Blob([pendingImport.bytes], { type: "application/json" }) });
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
    const result = await api("/api/admin/imports/commit", { method: "POST", headers: { "X-Import-Preview": previewHash }, body: new Blob([pendingImport.bytes], { type: "application/json" }) });
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
  const read = beginRead("candidates");
  const hasLastGood = $("candidate-list").children.length > 0;
  updateReadStatus("candidate-read-status", hasLastGood ? "Oppdaterer kandidater; sist lastede kandidater vises." : "Laster kandidater …", hasLastGood);
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
    const candidates = arrayResponse(await api(`/api/review/candidates?${params}`, { signal: read.controller.signal }), "kandidatlisten");
    if (!currentRead(read)) return;
    cacheSites(candidates);
    updateReadStatus("candidate-read-status");
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
  } catch (error) {
    if (readFailure(error, read, "candidate-read-status", $("candidate-list").children.length > 0, "kandidater")) {
      if (!$("candidate-list").children.length) text($("candidate-list"), error.message);
    }
  } finally { finishRead(read); }
}

function renderFieldPriority() {
  const list = $("field-priority-list"); list.replaceChildren();
  const sites = state.prioritySites;
  text($("field-priority-summary"), `${sites.length} ${sites.length === 1 ? "offentlig sted" : "offentlige steder"} i forskningsutvalget.`);
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
    appendDataStatus(item, site, "Stedsdata");
    if (site.route_eligible === false && site.route_blocking_reason) {
      const blocked = document.createElement("div"); blocked.className = "site-meta"; text(blocked, `Rute utilgjengelig: ${site.route_blocking_reason}`); item.append(blocked);
    }
    const actions = document.createElement("div"); actions.className = "site-actions";
    const details = document.createElement("button"); details.className = "small"; details.type = "button"; markDetailControl(details, site.id, "field"); text(details, "Detaljer"); details.addEventListener("click", () => openDetail(site.id, "review", "field"));
    const add = document.createElement("button"); add.className = "small"; add.type = "button"; add.dataset.ownerOnly = "true"; text(add, state.routeSiteIds.includes(site.id) ? "Lagt til" : "Legg til rute"); add.disabled = state.routeSiteIds.includes(site.id) || !hasReviewedPublicApproach(site); add.title = site.route_blocking_reason || ""; add.addEventListener("click", () => addRouteSite(site.id));
    actions.append(details, add); item.append(actions); list.append(item);
  });
}

async function loadFieldPriority() {
  if (!state.token) return;
  const read = beginRead("field-priority");
  updateReadStatus("field-priority-read-status", "Oppdaterer feltlisten …", Boolean(state.prioritySites.length));
  try {
    state.prioritySites = arrayResponse(await api("/api/field-priority?limit=12", { signal: read.controller.signal }), "feltlisten");
    if (!currentRead(read)) return;
    cacheSites(state.prioritySites);
    renderFieldPriority();
    updateReadStatus("field-priority-read-status");
  } catch (error) { readFailure(error, read, "field-priority-read-status", state.prioritySites.length > 0, "feltlisten"); }
  finally { finishRead(read); }
}

function formatRouteDistance(distance) {
  return distance >= 1000 ? `${(distance / 1000).toFixed(1)} km` : `${Math.round(distance)} m`;
}

function formatRouteDuration(duration) {
  return `${Math.round(duration / 60)} min`;
}

function routeBudgetText(budget) {
  if (!budget || typeof budget !== "object") return "Tidsbudsjett ikke tilgjengelig.";
  const minutes = (value) => value == null ? "ukjent" : `${Math.round(value)} min`;
  const comparison = {
    within_budget: "innenfor oppgitt budsjett",
    over_budget: "over oppgitt budsjett",
    unavailable: "kan ikke sammenlignes ennå",
    not_declared: "budsjett ikke oppgitt",
  }[budget.comparison_status] || "sammenligning ukjent";
  return `Reisetid ${minutes(budget.travel_minutes)} · besøkstid ${budget.visit_status === "complete" ? minutes(budget.visit_minutes) : "ufullstendig"} · total ${minutes(budget.total_minutes)} · ${comparison}.`;
}

function renderSavedRouteBudget(route, parent) {
  if (!Array.isArray(route.stops) || !route.stops.length) return;
  const details = document.createElement("details"); details.className = "route-budget-editor";
  const summary = document.createElement("summary"); text(summary, "Forhåndsvis tidsbudsjett med lagrede rutebein"); details.append(summary);
  if (!Array.isArray(route.legs) || !route.legs.length) {
    const unavailable = document.createElement("p"); unavailable.className = "site-meta";
    text(unavailable, "Lagrede kjøretider mangler; denne ruten kan ikke få en lokal budsjettforhåndsvisning."); details.append(unavailable); parent.append(details); return;
  }
  const form = document.createElement("form"); form.className = "detail-form route-budget-form";
  const fieldset = document.createElement("fieldset");
  const legend = document.createElement("legend"); text(legend, "Planlagt besøkstid per lagret stopp"); fieldset.append(legend);
  const visits = Array.isArray(route.visit_minutes) ? route.visit_minutes : [];
  route.stops.forEach((stop, index) => {
    const input = inputControl("number", visits[index] ?? ""); input.name = `visit_${index}`; input.min = "0"; input.max = "1440"; input.step = "1"; input.inputMode = "numeric";
    fieldset.append(labeledControl(`${stop.name || `Stopp ${index + 1}`} (minutter; tomt = ukjent)`, input));
  });
  const declared = inputControl("number", route.declared_budget_minutes ?? ""); declared.name = "declared_budget_minutes"; declared.min = "0"; declared.max = "10080"; declared.step = "1"; declared.inputMode = "numeric";
  const preview = document.createElement("button"); preview.type = "submit"; preview.className = "small"; text(preview, "Beregn tidsbudsjett");
  const result = document.createElement("div"); result.className = "route-budget-preview"; result.setAttribute("aria-live", "polite");
  form.append(fieldset, labeledControl("Oppgitt samlet tidsbudsjett (minutter, valgfritt)", declared), preview, result);
  const key = `${route.id}:route-budget`;
  bindFormDraft(form, key);
  let generation = 0;
  let busy = false;
  const invalidate = () => { generation += 1; result.replaceChildren(); preview.disabled = busy; };
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity() || busy) return;
    const snapshot = {
      visit_minutes: route.stops.map((_, index) => {
        const value = form.elements.namedItem(`visit_${index}`).value;
        return value === "" ? null : Number(value);
      }),
      declared_budget_minutes: declared.value === "" ? null : Number(declared.value),
    };
    const requestGeneration = ++generation;
    const requestEpoch = state.authEpoch;
    busy = true; preview.disabled = true; text(preview, "Beregner …");
    try {
      const response = await api(`/api/routes/${route.id}/budget`, { method: "POST", body: JSON.stringify(snapshot) });
      if (requestEpoch !== state.authEpoch || requestGeneration !== generation || !details.isConnected) return;
      result.replaceChildren();
      const assessment = document.createElement("p"); assessment.className = "site-meta"; text(assessment, routeBudgetText(response.budget)); result.append(assessment);
      if (response.stored_route_unchanged === true) {
        const preserved = document.createElement("p"); preserved.className = "site-meta";
        text(preserved, "Dette er en lokal forhåndsvisning med rutebeinene som allerede er lagret. Ruten og GPX endres ikke; ingen rutetjeneste kalles."); result.append(preserved);
      }
    } catch (error) {
      if (requestEpoch === state.authEpoch && requestGeneration === generation && details.isConnected && !isStaleRequest(error)) {
        result.replaceChildren(); const message = document.createElement("p"); message.className = "warning"; message.setAttribute("role", "alert"); text(message, `Budsjettet kunne ikke beregnes: ${error.message}. Utkastet er beholdt.`); result.append(message);
      }
    } finally {
      if (requestEpoch === state.authEpoch && details.isConnected) { busy = false; preview.disabled = false; text(preview, "Beregn tidsbudsjett"); }
    }
  });
  details.append(form); parent.append(details);
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
    text(meta, `${new Date(route.created_at).toLocaleString()} | ${route.data_status === "unavailable" ? "lagrede data utilgjengelige" : `${formatRouteDistance(route.distance_m)} | ${formatRouteDuration(route.duration_s)}`}`); item.append(meta);
    if (route.data_status === "valid") {
      const itinerary = document.createElement("p"); itinerary.className = "site-meta";
      const mode = route.mode === "return_to_start" ? "tur-retur start" : route.mode === "explicit_end" ? "eget sluttpunkt" : "én vei";
      text(itinerary, `${mode} · ${route.profile || "foot-hiking"}${route.budget ? ` · ${routeBudgetText(route.budget)}` : ""}`); item.append(itinerary);
    }
    appendDataStatus(item, route, "Rutedata");
    const load = document.createElement("button"); load.className = "small"; load.type = "button"; text(load, route.data_status === "unavailable" ? "Vis status" : "Last inn rute"); load.addEventListener("click", () => loadRoute(route.id)); item.append(load);
    if (route.data_status === "valid") renderSavedRouteBudget(route, item);
    list.append(item);
  });
}

async function loadRoutes() {
  if (!state.token || !hasSessionRole("owner")) return;
  const read = beginRead("routes");
  updateReadStatus("route-read-status", state.routes.length ? "Oppdaterer rutehistorikken; sist lagrede ruter vises." : "Laster rutehistorikk …", Boolean(state.routes.length));
  try {
    state.routes = arrayResponse(await api("/api/routes?limit=20", { signal: read.controller.signal }), "rutehistorikken");
    if (!currentRead(read)) return;
    renderRouteHistory();
    updateReadStatus("route-read-status");
  } catch (error) { readFailure(error, read, "route-read-status", state.routes.length > 0, "ruter"); }
  finally { finishRead(read); }
}

function renderRouteResult(result, snapshot = null) {
  revokeGpxObjectUrls();
  const root = $("route-result"); root.replaceChildren();
  if (result.data_status === "unavailable" || !result.geometry) {
    appendDataStatus(root, { ...result, data_status: "unavailable" }, "Rutedata");
    state.routeResultGeneration = null;
    return;
  }
  const summary = document.createElement("div"); text(summary, `${formatRouteDistance(result.distance_m)} | ${formatRouteDuration(result.duration_s)}`); root.append(summary);
  if (result.data_status !== "unavailable") {
    const routeMeta = document.createElement("p"); routeMeta.className = "site-meta";
    const mode = result.mode === "return_to_start" ? "tur-retur start" : result.mode === "explicit_end" ? "eget sluttpunkt" : "én vei";
    text(routeMeta, `${mode} · ${result.profile || "foot-hiking"}`); root.append(routeMeta);
    if (result.budget) { const budget = document.createElement("p"); budget.className = "site-meta"; text(budget, routeBudgetText(result.budget)); root.append(budget); }
    if (result.calculation_receipt) {
      const receipt = document.createElement("details"); receipt.className = "route-receipt";
      const receiptLabel = document.createElement("summary"); text(receiptLabel, "Rutegrunnlag og beregningskvittering");
      const receiptBody = document.createElement("pre"); text(receiptBody, JSON.stringify(result.calculation_receipt, null, 2)); receipt.append(receiptLabel, receiptBody); root.append(receipt);
    }
  }
  (result.warnings || []).forEach((warning) => { const p = document.createElement("p"); p.className = "warning"; text(p, warning); root.append(p); });
  if (result.current_site_changed) {
    const changed = document.createElement("p"); changed.className = "warning";
    text(changed, "Et eller flere gjennomgåtte stopp er endret siden ruten ble beregnet. Ruten er historisk; vurder stoppene på nytt."); root.append(changed);
  }
  const stopIds = snapshot?.site_ids || state.routeSiteIds;
  const cautionSites = stopIds.map(siteById).filter((site) => site && site.access !== "public");
  if (cautionSites.length) {
    const warning = document.createElement("p"); warning.className = "warning";
    text(warning, `Tilgang er ikke avklart for: ${cautionSites.map((site) => site.name).join(", ")}. Bruk bare offentlige tilnærminger.`); root.append(warning);
  }
  if (typeof result.gpx === "string" && result.gpx.length > 0) {
    const href = URL.createObjectURL(new Blob([result.gpx], { type: "application/gpx+xml" }));
    state.gpxObjectUrls.add(href);
    state.routeObjectUrl = href;
    const download = document.createElement("a");
    download.href = href;
    download.download = "bunkerkartet-route.gpx";
    text(download, "Last ned GPX"); root.append(download);
  } else if (result.gpx_status === "unavailable_legacy") {
    const notice = document.createElement("p"); notice.className = "warning";
    text(notice, "GPX-filen er ikke kvalifisert for nedlasting. Lagrede rutelinjer og målinger vises fortsatt."); root.append(notice);
  }
  state.routeResultGeneration = snapshot?.generation ?? state.routeDraftGeneration;
}

function routePayloadKey(payload) {
  return JSON.stringify({
    name: payload.name, start: payload.start, site_ids: payload.site_ids, mode: payload.mode,
    end: payload.end, profile: payload.profile, visit_minutes: payload.visit_minutes,
    declared_budget_minutes: payload.declared_budget_minutes,
  });
}

function invalidateRouteResult(message = "Ruteutkastet er endret. Beregn på nytt før GPX kan lastes ned.") {
  const hadResult = state.routeResultGeneration != null;
  state.routeDraftGeneration += 1;
  state.routeResultGeneration = null;
  if (routeLayer) { routeLayer.remove(); routeLayer = null; }
  if (state.routeObjectUrl) {
    URL.revokeObjectURL(state.routeObjectUrl);
    state.gpxObjectUrls.delete(state.routeObjectUrl);
    state.routeObjectUrl = null;
  }
  const root = $("route-result"); root.replaceChildren();
  if (hadResult || state.routeSiteIds.length) {
    const notice = document.createElement("p"); notice.className = "draft-notice"; text(notice, message); root.append(notice);
  }
}

async function reconcileRouteSubmission(submission) {
  const routes = arrayResponse(await api("/api/routes?limit=100"), "rutehistorikken");
  const route = routes.find((item) => item.request_id === submission.request_id || (
    item.name === submission.payload.name && item.start?.lat === submission.payload.start.lat &&
    item.start?.lon === submission.payload.start.lon &&
    JSON.stringify((item.stops || []).map((stop) => stop.site_id)) === JSON.stringify(submission.payload.site_ids)
  ));
  return route || null;
}

function renderRouteReconciliation(submission, message) {
  const root = $("route-result"); root.replaceChildren();
  const notice = document.createElement("p"); notice.className = "draft-conflict"; notice.setAttribute("role", "alert"); text(notice, message); root.append(notice);
  const check = document.createElement("button"); check.type = "button"; check.className = "small"; text(check, "Kontroller lagret rute");
  check.addEventListener("click", () => reconcileRouteDraft(submission)); root.append(check);
  const retry = document.createElement("button"); retry.type = "button"; retry.className = "small"; text(retry, "Prøv samme uendrede forespørsel igjen");
  retry.addEventListener("click", () => sendRouteSubmission(submission, { explicitRetry: true })); root.append(retry);
}

function submissionIsCurrent(submission) {
  return state.routeDraftGeneration === submission.generation && routePayloadKey(currentRoutePayload()) === submission.key;
}

async function reconcileRouteDraft(submission) {
  try {
    const route = await reconcileRouteSubmission(submission);
    if (route) {
      submission.status = "saved"; submission.routeId = route.id;
      await loadRoutes();
      if (submissionIsCurrent(submission)) await loadRoute(route.id);
      else setStatus("Den tidligere ruteforespørselen ble lagret. Det gjeldende ruteutkastet er uendret.");
      return true;
    }
    submission.status = "uncertain";
    if (submissionIsCurrent(submission)) renderRouteReconciliation(submission, "Ingen lagret rute er funnet ennå. Utkast og samme forespørsels-ID er bevart. Kontroller eller prøv den samme uendrede forespørselen på nytt.");
    return false;
  } catch (error) {
    if (!isStaleRequest(error) && submissionIsCurrent(submission)) renderRouteReconciliation(submission, `Kunne ikke lese tilbake rutehistorikken: ${error.message}. Utkast og forespørsels-ID er bevart.`);
    return false;
  }
}

function trimRouteSubmissions() {
  while (state.routeSubmissions.size > 30) {
    const oldest = state.routeSubmissions.keys().next().value;
    if (state.routeSubmissions.get(oldest)?.status === "in_flight") break;
    state.routeSubmissions.delete(oldest);
  }
}

async function sendRouteSubmission(submission, { explicitRetry = false } = {}) {
  if (state.routeRequestInFlight) return;
  if (submission.status === "saved" && submission.routeId) {
    if (submissionIsCurrent(submission)) await loadRoute(submission.routeId);
    return;
  }
  if (submission.status === "uncertain" && !explicitRetry) {
    await reconcileRouteDraft(submission);
    return;
  }
  const requestEpoch = state.authEpoch;
  state.routeRequestInFlight = true;
  submission.status = "in_flight";
  const button = $("create-route"); button.disabled = true; text(button, "Beregner rute …");
  try {
    const result = await api("/api/routes", { method: "POST", body: JSON.stringify(submission.payload) });
    if (requestEpoch !== state.authEpoch) return;
    submission.status = "saved"; submission.routeId = result.id;
    await loadRoutes();
    if (submissionIsCurrent(submission)) {
      if (map && result.geometry) {
        if (routeLayer) routeLayer.remove();
        routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
        map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
      }
      renderRouteResult(result, submission);
      setStatus(`Opprettet ${result.name}.`);
    } else {
      setStatus(`Den tidligere ruten «${result.name}» ble lagret. Gjeldende utkast ble ikke overskrevet.`);
    }
  } catch (error) {
    if (requestEpoch !== state.authEpoch || isStaleRequest(error)) return;
    if (!error.status || error.status >= 500 || error.code === "ROUTE_PENDING" || error.code === "ROUTE_LEASE_EXPIRED") {
      submission.status = "uncertain";
      const reconciled = await reconcileRouteSubmission(submission).catch(() => null);
      if (requestEpoch !== state.authEpoch) return;
      if (reconciled) {
        submission.status = "saved"; submission.routeId = reconciled.id;
        await loadRoutes();
        if (submissionIsCurrent(submission)) await loadRoute(reconciled.id);
        else setStatus("Den tidligere ruteforespørselen ble lagret; gjeldende utkast er uendret.");
      } else if (submissionIsCurrent(submission)) {
        renderRouteReconciliation(submission, `Lagringsutfallet er uklart (${error.message}). Utkast og forespørsels-ID er bevart. Les tilbake før du velger å sende samme uendrede forespørsel igjen.`);
      }
    } else {
      submission.status = "failed";
      if (submissionIsCurrent(submission)) text($("route-result"), error.message);
      setStatus(error.message);
    }
  } finally {
    if (requestEpoch === state.authEpoch) {
      state.routeRequestInFlight = false;
      text(button, "Beregn rute");
      renderRouteStops();
    }
  }
}

function currentRoutePayload() {
  return {
    name: $("route-name").value.trim() || "Feltur i Trondheim",
    start: state.start ? { lat: Number(state.start.lat), lon: Number(state.start.lon) } : null,
    site_ids: [...state.routeSiteIds],
    mode: $("route-mode").value || "one_way",
    end: $("route-mode").value === "explicit_end" ? { lat: Number($("route-end-lat").value), lon: Number($("route-end-lon").value) } : null,
    profile: $("route-profile").value || "foot-hiking",
    visit_minutes: state.routeSiteIds.map((id) => state.routeVisitMinutes.get(id) ?? null),
    declared_budget_minutes: $("route-declared-budget").value === "" ? null : Number($("route-declared-budget").value),
  };
}

async function loadRoute(id) {
  if (!state.token || !hasSessionRole("owner")) return;
  const read = beginRead("route-detail");
  try {
    const result = await api(`/api/routes/${id}`, { signal: read.controller.signal });
    if (!currentRead(read)) return;
    if (routeLayer) { routeLayer.remove(); routeLayer = null; }
    if (result.data_status === "unavailable" || !result.geometry) {
      invalidateRouteResult();
      state.loadedRouteId = null;
      state.routeStopSnapshots = [];
      state.routeSiteIds = [];
      state.routeVisitMinutes.clear();
      renderRouteStops();
      renderRouteResult(result);
      return;
    }
    invalidateRouteResult();
    state.loadedRouteId = result.id;
    $("route-name").value = result.name || "Lagret rute";
    $("route-mode").value = result.mode || "one_way";
    configureRouteProfiles(state.config);
    $("route-profile").value = Array.from($("route-profile").options).some((option) => option.value === result.profile) ? result.profile : "foot-hiking";
    $("route-end-lat").value = result.end?.lat ?? "";
    $("route-end-lon").value = result.end?.lon ?? "";
    $("route-declared-budget").value = result.declared_budget_minutes ?? "";
    updateRouteModeControls();
    updateRouteStart(result.start, "lagret rutestart", "loaded");
    state.routeSiteIds = (result.stops || []).map((stop) => stop.site_id).filter((siteId) => siteId != null);
    state.routeStopSnapshots = (result.stops || []).map((stop) => ({ ...stop }));
    state.routeVisitMinutes.clear();
    state.routeSiteIds.forEach((siteId, index) => state.routeVisitMinutes.set(siteId, result.visit_minutes?.[index] ?? null));
    await Promise.all(state.routeSiteIds.filter((siteId) => !state.siteCache.has(siteId)).map(async (siteId) => {
      try { const site = await api(`/api/sites/${siteId}`, { signal: read.controller.signal }); if (currentRead(read)) state.siteCache.set(site.id, site); } catch { /* Saved stop snapshot remains readable when the site is gone. */ }
    }));
    if (!currentRead(read)) return;
    renderRouteStops();
    if (map) {
      routeLayer = L.geoJSON(result.geometry, { style: { color: "#c65d2e", weight: 4 } }).addTo(map);
      map.fitBounds(routeLayer.getBounds(), { padding: [24, 24] });
    }
    renderRouteResult(result, { site_ids: state.routeSiteIds, generation: state.routeDraftGeneration });
    setStatus(`Lastet inn ${result.name}.`);
  } catch (error) { if (!isStaleRequest(error) && currentRead(read)) text($("route-result"), error.message); }
  finally { finishRead(read); }
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

function updateRouteStart(point, label, source = "manual") {
  const changed = state.start && (Number(state.start.lat) !== Number(point.lat) || Number(state.start.lon) !== Number(point.lon));
  if (changed) invalidateRouteResult();
  state.start = point;
  state.routeStartSource = source;
  if (startMarker) startMarker.remove();
  startMarker = map ? L.circleMarker([point.lat, point.lon], { color: "#c65d2e", fillColor: "#fff", fillOpacity: 1, radius: 8, weight: 3 }).addTo(map).bindTooltip("Rutestart") : null;
  text($("route-start"), `Start: ${label || `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`}`);
}

function addRouteSite(id) {
  const site = siteById(id);
  if (!site || !hasReviewedPublicApproach(site)) {
    setStatus(site?.route_blocking_reason || "Stoppet mangler en gjennomgått offentlig tilnærming.");
    return;
  }
  if (state.routeSiteIds.includes(id)) return;
  invalidateRouteResult();
  state.routeSettingsTouched = true;
  if (!state.routeSiteIds.includes(id)) state.routeSiteIds.push(id);
  renderRouteStops(); renderSiteList(); renderMap(false);
}

function renderRouteStops() {
  const list = $("route-stops"); list.replaceChildren();
  state.routeSiteIds.forEach((id, index) => {
    const site = siteById(id);
    const snapshot = state.routeStopSnapshots.find((stop) => stop.site_id === id);
    if (!site && !snapshot) return;
    const item = document.createElement("li"); item.className = "route-stop";
    const name = document.createElement("span"); name.className = "route-stop-name"; text(name, site?.name || snapshot.name || `Sted ${id}`);
    const access = document.createElement("span"); access.className = "route-stop-meta"; text(access, site ? accessLabel(site.approach_access || site.access) : "Historisk stopp – gjeldende sted utilgjengelig");
    item.append(name, access);
    if (site?.route_eligible === false && site.route_blocking_reason) { const reason = document.createElement("span"); reason.className = "route-stop-meta"; text(reason, `Nå utilgjengelig: ${site.route_blocking_reason}`); item.append(reason); }
    const visit = inputControl("number", state.routeVisitMinutes.get(id) ?? ""); visit.name = `visit_minutes_${id}`; visit.min = "0"; visit.max = "1440"; visit.step = "1"; visit.inputMode = "numeric";
    const visitLabel = labeledControl("Besøkstid (minutter; tomt = ukjent)", visit); visitLabel.className = "route-stop-visit"; item.append(visitLabel);
    const updateVisit = () => {
      state.routeVisitMinutes.set(id, visit.value === "" ? null : Number(visit.value));
      invalidateRouteResult("Besøkstid per stopp er endret. Beregn ruten på nytt før GPX lastes ned.");
    };
    visit.addEventListener("input", updateVisit);
    visit.addEventListener("change", updateVisit);
    const up = document.createElement("button"); up.className = "small"; up.type = "button"; up.title = "Flytt opp"; text(up, "Opp"); up.disabled = index === 0;
    up.addEventListener("click", () => { invalidateRouteResult(); [state.routeSiteIds[index - 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index - 1]]; renderRouteStops(); });
    const down = document.createElement("button"); down.className = "small"; down.type = "button"; down.title = "Flytt ned"; text(down, "Ned"); down.disabled = index === state.routeSiteIds.length - 1;
    down.addEventListener("click", () => { invalidateRouteResult(); [state.routeSiteIds[index + 1], state.routeSiteIds[index]] = [state.routeSiteIds[index], state.routeSiteIds[index + 1]]; renderRouteStops(); });
    const remove = document.createElement("button"); remove.className = "small"; remove.type = "button"; remove.title = "Fjern stopp"; text(remove, "Fjern");
    remove.addEventListener("click", () => { invalidateRouteResult(); state.routeSettingsTouched = true; state.routeSiteIds.splice(index, 1); state.routeVisitMinutes.delete(id); state.routeStopSnapshots = state.routeStopSnapshots.filter((stop) => stop.site_id !== id); renderRouteStops(); renderSiteList(); renderMap(); });
    item.append(up, down, remove); list.append(item);
  });
  syncRouteCreateButton();
}

function syncRouteCreateButton() {
  const explicitEnd = $("route-mode").value === "explicit_end";
  const endpointInvalid = explicitEnd && (!$("route-end-lat").checkValidity() || !$("route-end-lon").checkValidity() || !$("route-end-lat").value || !$("route-end-lon").value);
  const visitInvalid = [...$("route-stops").querySelectorAll('input[type="number"]')].some((input) => !input.checkValidity());
  $("create-route").disabled = state.routeRequestInFlight || !state.start || state.routeSiteIds.length === 0 || !$("route-profile").value || endpointInvalid || !$("route-declared-budget").checkValidity() || visitInvalid || state.routeSiteIds.some((id) => !hasReviewedPublicApproach(siteById(id)));
}

async function createRoute() {
  if (!state.start || state.routeSiteIds.length === 0 || state.routeRequestInFlight || $("create-route").disabled) return;
  const routeSites = state.routeSiteIds.map(siteById);
  const unavailable = routeSites.find((site) => !site || !hasReviewedPublicApproach(site));
  if (unavailable) {
    setStatus(unavailable?.route_blocking_reason || "Et valgt stopp er ikke lenger tilgjengelig med gjennomgått offentlig tilnærming.");
    return;
  }
  const payload = currentRoutePayload();
  const generation = state.routeDraftGeneration;
  const key = routePayloadKey(payload);
  let submission = state.routeSubmissions.get(generation);
  if (!submission) {
    const snapshot = {
      generation, key,
      payload: { ...payload, start: { ...payload.start }, site_ids: [...payload.site_ids], request_id: crypto.randomUUID() },
      site_snapshots: routeSites.map((site) => ({ id: site.id, revision: site.revision, approach_latitude: site.approach_latitude, approach_longitude: site.approach_longitude, approach_access: site.approach_access, approach_reviewed_at: site.approach_reviewed_at })),
      request_id: null, status: "ready", routeId: null,
    };
    snapshot.request_id = snapshot.payload.request_id;
    submission = snapshot;
    state.routeSubmissions.set(generation, submission);
    trimRouteSubmissions();
  }
  await sendRouteSubmission(submission);
}

$("auth-form").addEventListener("submit", (event) => { event.preventDefault(); loadSites(); });
$("close-detail").addEventListener("click", closeDetail);
$("lock-app").addEventListener("click", lockWorkspace);
$("refresh-sites").addEventListener("click", loadSites);
$("status-filter").addEventListener("change", () => { updateNavigationUrl(); loadSites(); });
$("kind-filter").addEventListener("change", () => { renderSiteList(); renderMap(); });
$("category-filter").addEventListener("change", () => { updateNavigationUrl(); renderSiteList(); renderMap(); });
$("access-filter").addEventListener("change", () => { updateNavigationUrl(); loadSites(); });
$("confidence-filter").addEventListener("change", () => { updateNavigationUrl(); loadSites(); });
$("site-search").addEventListener("change", loadSites);
$("clear-filters").addEventListener("click", () => {
  ["status-filter", "category-filter", "kind-filter", "site-search", "access-filter", "confidence-filter"].forEach((id) => { $(id).value = ""; });
  updateNavigationUrl();
  if (state.token) loadSites(); else { renderSiteList(); renderMap(); }
});
$("map-provider").addEventListener("change", (event) => selectMapProvider(event.target.value));
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
$("pick-start").addEventListener("click", () => {
  if (!map) { setStatus("Kartet er ikke tilgjengelig; bruk manuell posisjon eller nettleserposisjon for rutestart."); return; }
  state.pickingStart = true; setStatus("Klikk på kartet for å sette rutestart.");
});
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
    updateRouteStart(point, "nåværende posisjon", "device"); map?.setView([point.lat, point.lon], 15);
    text($("location-status"), `Nåværende posisjon er satt som rutestart (nøyaktighet ${Math.round(position.coords.accuracy)} m).`);
    setStatus("Nåværende posisjon er satt som rutestart.");
  }, () => {
    if (requestEpoch !== state.authEpoch) return;
    text($("location-status"), "Kunne ikke lese nåværende posisjon.");
    setStatus("Kunne ikke lese nåværende posisjon.");
  }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
});
$("create-route").addEventListener("click", createRoute);
$("route-name").addEventListener("input", () => { state.routeSettingsTouched = true; invalidateRouteResult(); syncRouteCreateButton(); });
$("route-mode").addEventListener("change", () => {
  state.routeSettingsTouched = true;
  updateRouteModeControls(); invalidateRouteResult(); syncRouteCreateButton();
  saveDraft("route:settings", { fields: { name: $("route-name").value, mode: $("route-mode").value, profile: $("route-profile").value, end_lat: $("route-end-lat").value, end_lon: $("route-end-lon").value, declared_budget_minutes: $("route-declared-budget").value } });
});
$("route-profile").addEventListener("change", () => {
  state.routeSettingsTouched = true;
  invalidateRouteResult(); syncRouteCreateButton();
  saveDraft("route:settings", { fields: { name: $("route-name").value, mode: $("route-mode").value, profile: $("route-profile").value, end_lat: $("route-end-lat").value, end_lon: $("route-end-lon").value, declared_budget_minutes: $("route-declared-budget").value } });
});
["route-end-lat", "route-end-lon", "route-declared-budget"].forEach((id) => $(id).addEventListener("input", () => {
  state.routeSettingsTouched = true;
  invalidateRouteResult(); syncRouteCreateButton();
  saveDraft("route:settings", { fields: { name: $("route-name").value, mode: $("route-mode").value, profile: $("route-profile").value, end_lat: $("route-end-lat").value, end_lon: $("route-end-lon").value, declared_budget_minutes: $("route-declared-budget").value } });
}));
updateRouteModeControls();
document.querySelectorAll(".surface-nav button").forEach((button) => {
  button.addEventListener("click", () => resetIdleLockTimer());
});
for (const eventName of ["pointerdown", "keydown", "touchstart", "wheel", "focusin"]) {
  document.addEventListener(eventName, () => { if (state.token) resetIdleLockTimer(); }, { passive: true, capture: true });
}
document.addEventListener("visibilitychange", () => {
  clearTimeout(hiddenLockTimer); hiddenLockTimer = null;
  if (document.visibilityState === "hidden") startHiddenLockTimer();
  else resetIdleLockTimer();
});

initializeMap();
loadPublicConfig();
updateRouteStart(DEFAULT_ROUTE_START, "Standardstart i Trondheim", "default");
restoreNavigationFromUrl();
