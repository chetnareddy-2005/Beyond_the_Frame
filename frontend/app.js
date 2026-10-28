const $ = (selector) => document.querySelector(selector);
const MAX_UPLOAD_BYTES = 250 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set(['mp4', 'avi', 'mov', 'mkv']);
const RUN_STORAGE_KEY = 'btf-current-run-id';
const PLAYED_HIGH_ALERTS_STORAGE_KEY = 'btf-played-high-alerts-v2';
const THEME_STORAGE_KEY = 'btf-color-theme';
const PIPELINE_STAGES = ['uploaded', 'detection', 'tracking', 'temporal', 'reliability', 'evidence', 'vlm', 'fusion', 'assessment', 'completed'];
let photoFile = null;
let photoDetectionResult = null;
let photoProcessingStartedAt = null;
let photoProgressTimer = null;
let loginTerminalTimer = null;

function applyColorTheme(theme, persist = true) {
  const selectedTheme = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selectedTheme;
  document.documentElement.style.colorScheme = selectedTheme;
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = selectedTheme === 'light' ? '#F0F4F8' : '#0A0F14';
  document.querySelectorAll('[data-theme-choice]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.themeChoice === selectedTheme));
  });
  const themeToggle = $('#theme-toggle');
  if (themeToggle) {
    const nextTheme = selectedTheme === 'dark' ? 'light' : 'dark';
    themeToggle.setAttribute('aria-label', `Switch to ${nextTheme} mode`);
    themeToggle.title = `Switch to ${nextTheme} mode`;
    $('#theme-toggle-sun').toggleAttribute('hidden', selectedTheme !== 'dark');
    $('#theme-toggle-moon').toggleAttribute('hidden', selectedTheme !== 'light');
  }
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, selectedTheme); } catch { /* Storage can be disabled. */ }
  }
}

try {
  applyColorTheme(localStorage.getItem(THEME_STORAGE_KEY) || 'dark', false);
} catch {
  applyColorTheme('dark', false);
}

const state = {
  runId: null,
  preparedThermalRun: null,
  playerRunId: null,
  run: null,
  tracks: [],
  overview: null,
  fusionSummary: null,
  page: 1,
  activeTrackRequest: 0,
  pollTimer: null,
  analysisTimelineTimer: null,
  analysisTimelineRunId: null,
  cancelPending: false,
  busy: false,
  muted: false,
  authenticated: false,
  profile: null,
  operatorLocation: null,
  inputSource: null,
  inputType: null,
  cctvConnected: false,
  cctvReference: null,
  phase3Input: null,
  thermalPreparationView: false,
  thermalPreparationSnapshot: null,
  phase4Busy: false,
  phase4Ended: false,
  phase5Busy: false,
  phase5Ended: false,
  alarmTransitions: new Set(),
  playedHighAlerts: new Set(),
  previewUrl: null,
  selectedFile: null,
  inputMetadata: null,
  activeResultTab: 'detection',
  pageSize: 10,
  report: null,
  trajectoryDetails: new Map(),
  trajectoryDetailsRunId: null,
  selectedTrajectoryTrackId: null,
  trajectoryRequest: 0,
  trajectorySeekTimer: null,
  phaseTrackDetail: null,
  selectedPhaseTrackId: null,
  phaseDetailRequest: 0,
  preparedFrameResolution: null,
  selectedStage: 'uploaded',
  phaseExecution: {},
  currentFrameIndex: null,
  frameLookupRequest: 0,
  lastFrameLookupKey: null,
  alertEvidence: null,
  currentFrameObservations: [],
  currentFrameObservationFrame: null,
  selectedTrailObservations: [],
  showVideoTrajectory: false,
  historyRuns: [],
  sessionHistory: [],
  historyPage: 1,
  historyLoaded: false,
  reuseCandidateRunId: null,
  alertRows: [],
  alertPage: 1,
  alertError: '',
  alertRunId: null,
  alertRequest: 0,
  alertCueSignature: '',
  sensorTransitionTimer: null,
  resultCardRequest: 0,
  reportCardRequest: 0,
  pendingAlertEvidence: null,
  resultsRequest: 0,
  resultsLoadedRunId: null,
  resultsDateFilter: null,
  route: null,
  routeGeneration: 0,
  routeRunId: null,
  routedNodes: new Map(),
};

function parseRoute(pathname) {
  const pageRoutes = {
    '/dashboard': { page: 'dashboard', navigation: 'dashboard' },
    '/sector-dashboard': { page: 'sector-dashboard', navigation: 'sector-dashboard' },
    '/photo-detection': { page: 'photo-detection', navigation: 'photo-detection' },
    '/live-cctv': { page: 'live-cctv', navigation: 'cctv' },
    '/video-analysis': { page: 'video-analysis', navigation: 'video-analysis' },
    '/results': { page: 'results', navigation: 'results' },
    '/history': { page: 'history', navigation: 'history' },
    '/alerts': { page: 'alerts', navigation: 'alerts' },
    '/reports': { page: 'reports', navigation: 'reports' },
    '/settings': { page: 'settings', navigation: 'settings' },
    '/authorization': { page: 'authorization', navigation: 'authorization' },
    '/headquarters-sectors': { page: 'headquarters-sectors', navigation: 'headquarters-sectors' },
    '/headquarters-drones': { page: 'headquarters-drones', navigation: 'headquarters-drones' },
    '/headquarters-users': { page: 'headquarters-users', navigation: 'headquarters-users' },
  };
  if (pageRoutes[pathname]) return pageRoutes[pathname];
  const detail = pathname.match(/^\/(results|reports)\/(RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24})$/);
  if (detail) {
    return {
      page: detail[1] === 'results' ? 'result-detail' : 'report-detail',
      navigation: detail[1],
      runId: detail[2],
    };
  }
  return { page: 'dashboard', navigation: 'dashboard', invalid: pathname !== '/' };
}

const SECTOR_ALLOWED_PAGES = new Set([
  'dashboard',
  'sector-dashboard',
  'live-cctv',
  'video-analysis',
  'results',
  'result-detail',
  'history',
  'alerts',
  'reports',
  'report-detail',
  'settings',
]);
const HEADQUARTERS_ALLOWED_PAGES = new Set([
  'dashboard',
  'headquarters-sectors',
  'headquarters-drones',
  'headquarters-users',
  'history',
  'settings',
]);

function routeAllowedForRole(role, route) {
  if (role === 'sector') return SECTOR_ALLOWED_PAGES.has(route.page);
  if (role === 'headquarters_admin') return HEADQUARTERS_ALLOWED_PAGES.has(route.page);
  return route.page !== 'sector-dashboard';
}

function navigateTo(path, replace = false, routeState = null) {
  const targetUrl = new URL(path, window.location.origin);
  const targetPath = `${targetUrl.pathname}${targetUrl.search}${targetUrl.hash}`;
  if (replace) history.replaceState(routeState, '', path);
  else if (`${window.location.pathname}${window.location.search}${window.location.hash}` !== targetPath) history.pushState(routeState, '', targetPath);
  else if (routeState !== null) history.replaceState(routeState, '', path);
  return renderCurrentRoute();
}

function moveRouteNode(nodeSelector, slotSelector) {
  const node = $(nodeSelector);
  const slot = $(slotSelector);
  if (!state.routedNodes.has(node)) {
    const marker = document.createComment('route-placeholder');
    node.parentNode.insertBefore(marker, node);
    state.routedNodes.set(node, marker);
  }
  slot.append(node);
}

function restoreRouteNodes() {
  for (const [node, marker] of state.routedNodes) {
    if (marker.parentNode) marker.parentNode.insertBefore(node, marker);
    marker.remove();
  }
  state.routedNodes.clear();
}

function showRoutePage(route) {
  document.querySelectorAll('[data-route-page]').forEach((section) => {
    section.hidden = section.dataset.routePage !== route.page;
  });
}

async function renderCurrentRoute() {
  const generation = ++state.routeGeneration;
  const route = parseRoute(window.location.pathname);
  const rawDateFilter = new URLSearchParams(window.location.search).get('date');
  state.resultsDateFilter = route.page === 'results' && /^\d{4}-\d{2}-\d{2}$/.test(rawDateFilter || '')
    ? rawDateFilter
    : null;
  const role = state.profile?.role;
  if (role && !routeAllowedForRole(role, route)) {
    history.replaceState(null, '', role === 'sector' ? '/sector-dashboard' : '/dashboard');
    return renderCurrentRoute();
  }
  if (route.page === 'dashboard' && state.phase3Input?.input_type === 'thermal') {
    setThermalPreparationView(true);
  } else if (['result-detail', 'report-detail', 'alerts'].includes(route.page)) {
    setThermalPreparationView(false);
    if (['result-detail', 'report-detail'].includes(route.page)) state.phase3Input = null;
  }
  const routeEvidence = route.page === 'result-detail'
    ? window.history.state?.alertEvidence
    : null;
  state.pendingAlertEvidence = routeEvidence?.run_id === route.runId
    ? routeEvidence
    : null;
  if (state.route?.page === 'alerts' && route.page !== 'alerts') {
    renderAlerts(state.report);
  }
  if (state.route?.page === 'dashboard' && route.page !== 'dashboard') {
    $('#trajectory-dialog').close();
    if ($('#current-frame-dialog').open) $('#current-frame-dialog').close();
  }
  state.route = route;
  $('#route-error').hidden = true;
  if (route.invalid) {
    history.replaceState(null, '', '/dashboard');
  }
  if (
    state.routeRunId
    && route.runId !== state.routeRunId
    && route.page !== 'dashboard'
    && state.runId === state.routeRunId
    && !['processing', 'prepared'].includes(state.run?.status)
  ) {
    state.routeRunId = null;
    state.runId = null;
    localStorage.removeItem(RUN_STORAGE_KEY);
    clearRunResults();
    resetUpload();
  }
  restoreRouteNodes();
  const isDashboard = route.page === 'dashboard';
  const headquartersDashboard = isDashboard && state.profile?.role === 'headquarters_admin';
  const sectorOperationalDashboard = isDashboard && state.profile?.role === 'sector';
  $('#dashboard-overview').hidden = !isDashboard;
  $('#hq-admin-overview').hidden = !headquartersDashboard;
  $('#sector-operational-overview').hidden = !sectorOperationalDashboard;
  $('#analysis-dashboard-workspace').hidden = headquartersDashboard || sectorOperationalDashboard;
  $('#route-content').hidden = isDashboard;
  if (headquartersDashboard) void loadHeadquartersDashboard();
  if (sectorOperationalDashboard) await loadSectorDashboard();
  if (isDashboard) {
    if (state.report) renderAlerts(state.report);
    if (state.tracks.length) void renderDashboardTrajectory();
    $('#results-section').hidden = true;
    $('#results-library').hidden = false;
    $('#history-section').hidden = true;
    showRoutePage({ page: '' });
  } else {
    showRoutePage(route);
  }
  document.querySelectorAll('[data-navigation]').forEach((link) => {
    const active = link.dataset.navigation === route.navigation;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (route.invalid) {
    history.replaceState(null, '', '/dashboard');
    route.page = 'dashboard';
    route.navigation = 'dashboard';
    $('#dashboard-overview').hidden = false;
    $('#route-content').hidden = true;
    document.querySelectorAll('[data-navigation]').forEach((link) => {
      const active = link.dataset.navigation === 'dashboard';
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    return;
  }
  if (route.page === 'video-analysis' && state.runId === state.routeRunId) {
    state.routeRunId = null;
  }
  if (route.page === 'live-cctv') {
    moveRouteNode('#cctv-input', '#live-cctv-slot');
    moveRouteNode('#phase3-panel', '#live-cctv-slot');
    await selectInputSource('cctv');
  } else if (route.page === 'video-analysis') {
    moveRouteNode('#video-analysis', '#video-analysis-slot');
    moveRouteNode('#run-section', '#video-analysis-slot');
    moveRouteNode('.monitor-workspace', '#video-analysis-slot');
    await selectInputSource('upload');
  } else if (route.page === 'results') {
    $('#results-library').hidden = false;
    moveRouteNode('#results-library', '#results-library-slot');
    await loadHistory(false);
  } else if (route.page === 'result-detail' || route.page === 'report-detail') {
    moveRouteNode('.monitor-workspace', route.page === 'result-detail' ? '#result-detail-slot' : '#report-detail-slot');
    if (route.page === 'result-detail') moveRouteNode('#analysis-timeline', '#result-detail-slot');
    moveRouteNode('#run-section', route.page === 'result-detail' ? '#result-detail-slot' : '#report-detail-slot');
    moveRouteNode('#results-section', route.page === 'result-detail' ? '#result-detail-slot' : '#report-detail-slot');
    $('#results-library').hidden = true;
    state.routeRunId = route.runId;
    const resultsAlreadyLoaded = state.runId === route.runId
      && state.run?.run_id === route.runId
      && state.resultsLoadedRunId === route.runId;
    if (
      state.runId !== route.runId
      || state.run?.run_id !== route.runId
      || state.resultsLoadedRunId !== route.runId
    ) {
      state.runId = route.runId;
      localStorage.setItem(RUN_STORAGE_KEY, route.runId);
      clearRunResults();
      await resumeCurrentRun(route.runId);
    }
    if (generation !== state.routeGeneration) return;
    if (route.page === 'result-detail' && resultsAlreadyLoaded) {
      $('#results-section').hidden = false;
      void renderDashboardTrajectory();
    }
    void applyPendingAlertEvidence(route.runId);
    if (route.page === 'report-detail') renderReportDetail();
  } else if (route.page === 'history') {
    moveRouteNode('#history-section', '#history-slot');
    $('#history-section').hidden = false;
    $('#history-owner-label').textContent = 'Showing analysis and login history for ' + (state.profile?.username || 'this account') + ' only.';
    await loadHistory();
  } else if (route.page === 'alerts') {
    moveRouteNode('#active-alerts', '#alerts-slot');
    await loadHistory(false);
    if (generation !== state.routeGeneration) return;
    populateAlertRunSelector();
  } else if (route.page === 'reports') {
    await loadHistory(false);
    if (generation !== state.routeGeneration) return;
  } else if (route.page === 'sector-dashboard') {
    await loadSectorDashboard();
  } else if (route.page === 'headquarters-drones') {
    await loadHeadquartersDrones();
  } else if (route.page === 'headquarters-users') {
    await Promise.all([loadHeadquartersUsers(), loadHeadquartersRegistry(), loadHeadquartersPasswordResets()]);
  } else if (route.page === 'settings') {
    $('#settings-username').textContent = $('#authenticated-username').textContent || 'N/A';
    $('#settings-api-status').textContent = $('#health-text').textContent || 'N/A';
  } else if (route.page === 'authorization') {
    await loadAuthorizationSimulation();
  } else if (route.page === 'headquarters-sectors') {
    await loadHeadquartersSectors();
    await refreshOperatorLocation();
  }
  if (generation !== state.routeGeneration) return;
  window.scrollTo(0, 0);
}

let headquartersLeafletMap = null;
let headquartersLeafletLayers = null;
let headquartersLeafletBoundsKey = null;
let headquartersSectorDrafts = null;
let headquartersSectorDrawing = false;
let headquartersSectorFirstCorner = null;
let headquartersSectorPreviewLayer = null;
let headquartersSectorDrawBounds = null;
let headquartersLocationSetting = false;
let headquartersLocationDraft = null;
function visibleHeadquartersSectors(payload) {
  return headquartersSectorDrafts === null ? payload.sectors : headquartersSectorDrafts;
}
function renderHeadquartersSectorMap(payload) {
  const container = $('#hq-sector-leaflet-map');
  const fallback = $('#hq-sector-map-fallback');
  if (!container || !payload) return;
  if (!window.L) {
    if (fallback) fallback.hidden = false;
    return;
  }
  if (fallback) fallback.hidden = true;
  const { west, east, south, north } = payload.config;
  const locations = window.operatorLocationCache || [];
  const headquartersLocation = payload.config.headquarters_location;
  const key = [west, east, south, north, headquartersLocation?.lat, headquartersLocation?.lon, ...locations.map((item) => item.updated_at)].join(':');
  const bounds = [[south, west], [north, east]];
  if (!headquartersLeafletMap) {
    headquartersLeafletMap = L.map(container, { zoomControl: true, scrollWheelZoom: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(headquartersLeafletMap);
    headquartersLeafletLayers = L.layerGroup().addTo(headquartersLeafletMap);
    headquartersLeafletMap.on('click', (event) => {
      if (headquartersLocationSetting) {
        headquartersLocationDraft = {lat: event.latlng.lat, lon: event.latlng.lng};
        headquartersLocationSetting = false;
        $('#hq-sector-place-location').textContent = 'Set HQ location on map';
        $('#hq-sector-location-status').textContent = 'Headquarters location pinned. Saving…';
        void saveHeadquartersSectors();
        return;
      }
      if (!headquartersSectorDrawing) return;
      const point = event.latlng;
      const outer = {
        west: Number($('#hq-sector-west').value), east: Number($('#hq-sector-east').value),
        south: Number($('#hq-sector-south').value), north: Number($('#hq-sector-north').value),
      };
      if (!(point.lat >= outer.south && point.lat <= outer.north && point.lng >= outer.west && point.lng <= outer.east)) {
        $('#hq-sector-draw-status').textContent = 'Choose both corners inside the monitored boundary.';
        headquartersSectorFirstCorner = null;
        return;
      }
      if (!headquartersSectorFirstCorner) {
        headquartersSectorFirstCorner = point;
        $('#hq-sector-draw-status').textContent = 'First corner set. Click the opposite corner to finish the rectangle.';
        return;
      }
      const first = headquartersSectorFirstCorner;
      headquartersSectorFirstCorner = null;
      const drawn = {
        west: Math.min(first.lng, point.lng), east: Math.max(first.lng, point.lng),
        south: Math.min(first.lat, point.lat), north: Math.max(first.lat, point.lat),
      };
      if (drawn.east - drawn.west < 0.000001 || drawn.north - drawn.south < 0.000001) {
        $('#hq-sector-draw-status').textContent = 'The rectangle is too small. Choose two distinct corners.';
        return;
      }
      if (headquartersSectorPreviewLayer) headquartersLeafletLayers.removeLayer(headquartersSectorPreviewLayer);
      headquartersSectorDrawBounds = drawn;
      headquartersSectorPreviewLayer = L.rectangle([[drawn.south, drawn.west], [drawn.north, drawn.east]], {
        color: '#e59a22', weight: 2, dashArray: '6 4', fillColor: '#f5b942', fillOpacity: 0.15,
      }).addTo(headquartersLeafletLayers);
      $('#hq-sector-add-area').disabled = false;
      $('#hq-sector-draw-status').textContent = 'Rectangle ready. Enter its official sector ID and name, then add it.';
    });
  }
  if (headquartersLeafletBoundsKey !== key) {
    if (headquartersLocation) {
      headquartersLeafletMap.setView([headquartersLocation.lat, headquartersLocation.lon], 15);
    } else {
      headquartersLeafletMap.fitBounds(L.latLngBounds(bounds), { padding: [18, 18], maxZoom: 16 });
    }
    headquartersLeafletBoundsKey = key;
  }
  headquartersLeafletLayers.clearLayers();
  visibleHeadquartersSectors(payload).forEach((sector) => {
    const b = sector.bounds;
    const ring = sector.geometry?.coordinates?.[0];
    const shape = ring?.length ? ring.map(([lon, lat]) => [lat, lon]) : [[b.south,b.west],[b.south,b.east],[b.north,b.east],[b.north,b.west]];
    const inactive = sector.is_active === false;
    const polygon = L.polygon(shape, {
      color: inactive ? '#7e8995' : (headquartersSectorDrafts === null ? '#00c9ed' : '#169b8a'),
      weight: 2, dashArray: inactive ? '7 6' : null,
      fillColor: inactive ? '#89939c' : (headquartersSectorDrafts === null ? '#00bde0' : '#26b99d'),
      fillOpacity: inactive ? 0.08 : 0.2,
    }).addTo(headquartersLeafletLayers);
    const area = sector.geometry?.area_km2 == null ? '' : `<br>${Number(sector.geometry.area_km2).toFixed(3)} km²`;
    if (sector.geometry) {
      const center = sector.geometry.centroid ? [sector.geometry.centroid[1], sector.geometry.centroid[0]] : polygon.getBounds().getCenter();
      const label = `<span class="sector-map-label">${escapeHTML(sector.name)}<br>${escapeHTML(sector.id)}${area}</span>`;
      L.marker(center, { interactive:false, icon:L.divIcon({className:'sector-map-label-anchor',html:label,iconSize:null}) }).addTo(headquartersLeafletLayers);
    } else polygon.bindTooltip(`<strong>${escapeHTML(sector.name)} (${escapeHTML(sector.id)})</strong>`, { sticky:true });
  });
  if (headquartersSectorPreviewLayer && headquartersSectorDrawBounds) {
    const b = headquartersSectorDrawBounds;
    headquartersSectorPreviewLayer = L.rectangle([[b.south,b.west],[b.north,b.east]], {
      color: '#e59a22', weight: 2, dashArray: '6 4', fillColor: '#f5b942', fillOpacity: 0.15,
    }).addTo(headquartersLeafletLayers);
  }
  if (headquartersLocation) {
    L.circleMarker([headquartersLocation.lat, headquartersLocation.lon], {radius:9, color:'#fff', weight:3, fillColor:'#f04fa5', fillOpacity:1})
      .bindTooltip(`HQ pin · ${headquartersLocation.lat.toFixed(5)}, ${headquartersLocation.lon.toFixed(5)}`, {permanent:true, direction:'top', className:'sector-map-label'})
      .addTo(headquartersLeafletLayers);
  }
  locations.forEach((location) => {
    const label = location.role === 'sector' ? `Sector ${location.sector_number || ''}` : 'Headquarters administrator';
    L.circleMarker([location.latitude, location.longitude], { radius: 7, color: '#fff', weight: 2, fillColor: '#f4b740', fillOpacity: 1 })
      .bindPopup(`${escapeHTML(label)}<br>Approx. 100 m precision<br>Last shared: ${escapeHTML(location.updated_at)}`)
      .addTo(headquartersLeafletLayers);
  });
  window.setTimeout(() => headquartersLeafletMap?.invalidateSize(), 0);
}
function renderHeadquartersSectorDraftList(payload) {
  const sectors = visibleHeadquartersSectors(payload);
  const approved = Array.isArray(window.hqApprovedSectors) ? window.hqApprovedSectors : [];
  const byId = new Map(sectors.map((sector) => [String(sector.id).toUpperCase(), sector]));
  const approvedCards = approved.filter((entry) => entry.is_active !== false && entry.active !== false).map((entry) => {
    const sector = byId.get(String(entry.sector_id || entry.id || '').toUpperCase());
    const bounds = sector?.bounds;
    const coverage = entry.coverage || sector?.coverage || 'Not provided';
    const coordinates = bounds
      ? `<small>Lat ${bounds.south.toFixed(5)}–${bounds.north.toFixed(5)} · Lon ${bounds.west.toFixed(5)}–${bounds.east.toFixed(5)}</small>`
      : '';
    return `<article class="hq-sector-card"><strong>${escapeHTML(entry.sector_name || entry.name || sector?.name || entry.sector_id)}</strong><span>${escapeHTML(entry.sector_id || entry.id)}</span><small>Approved and active</small><small>Covered localities: ${escapeHTML(coverage)}</small>${coordinates}</article>`;
  });
  $('#hq-sector-count').textContent = `${approvedCards.length} approved`;
  $('#hq-sector-list').innerHTML = approvedCards.length ? approvedCards.join('') : '<p class="input-help">No active sectors have been approved yet.</p>';
  const draftList = $('#hq-sector-draft-list');
  if (draftList) draftList.innerHTML = headquartersSectorDrafts === null
    ? '<p class="input-help">Default setup preview only. Draw and name areas that HQ can assign during approval.</p>'
    : sectors.map((sector) => `<article class="hq-sector-card"><strong>${escapeHTML(sector.name)}</strong><span>${escapeHTML(sector.id)}</span><button class="button button-secondary hq-sector-remove" type="button" data-remove-sector="${escapeHTML(sector.id)}">Delete area</button></article>`).join('');
  $('#hq-sector-updated-at').textContent = payload.updated_at
    ? `Last saved: ${payload.updated_at} UTC · Headquarters configuration`
    : `Demo setup preview · ${payload.config.south.toFixed(5)}, ${payload.config.west.toFixed(5)} to ${payload.config.north.toFixed(5)}, ${payload.config.east.toFixed(5)} · choose the real HQ location before using this layout.`;
}
function createTrendChart(rows, kind, title, available) {
  const section = document.createElement('section');
  section.className = 'dashboard-trend-chart';
  const heading = document.createElement('h3');
  heading.textContent = title;
  section.append(heading);
  if (!available || !Array.isArray(rows) || rows.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Not available: no completed reports with readable trend data.';
    section.append(empty);
    return section;
  }
  const normalized = rows.map((row) => ({
    date: typeof row.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(row.date) ? row.date : '',
    drone: Math.max(0, Number(row.drone) || 0),
    bird: Math.max(0, Number(row.bird) || 0),
    unknown: Math.max(0, Number(row.unknown) || 0),
    alerts: Math.max(0, Number(row.alerts) || 0),
  })).filter((row) => row.date);
  if (!normalized.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Not available: the report dates could not be read.';
    section.append(empty);
    return section;
  }
  const max = Math.max(1, ...normalized.map((row) => kind === 'detections'
    ? row.drone + row.bird + row.unknown
    : row.alerts));
  const chart = document.createElement('div');
  chart.className = 'dashboard-trend-columns';
  chart.setAttribute('role', 'group');
  chart.setAttribute('aria-label', `${title}; choose a date to open filtered results.`);
  for (const row of normalized) {
    const day = document.createElement('button');
    day.type = 'button';
    day.className = 'dashboard-trend-day';
    day.dataset.dashboardDate = row.date;
    const dateLabel = new Date(`${row.date}T12:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
    day.setAttribute('aria-label', `Open results for ${row.date}: ${row.drone} drone, ${row.bird} bird, ${row.unknown} unknown detections; ${row.alerts} review-alert indicators.`);
    const bars = document.createElement('span');
    bars.className = 'dashboard-trend-bars';
    if (kind === 'detections') {
      for (const [key, count] of [['drone', row.drone], ['bird', row.bird], ['unknown', row.unknown]]) {
        const segment = document.createElement('i');
        segment.className = `dashboard-trend-segment is-${key}`;
        segment.style.height = `${(count / max) * 100}%`;
        if (count) segment.title = `${key}: ${count}`;
        bars.append(segment);
      }
    } else {
      const segment = document.createElement('i');
      segment.className = 'dashboard-trend-segment is-alert';
      segment.style.height = `${(row.alerts / max) * 100}%`;
      if (row.alerts) segment.title = `${row.alerts} review-alert indicators`;
      bars.append(segment);
    }
    const value = document.createElement('strong');
    value.textContent = String(kind === 'detections'
      ? row.drone + row.bird + row.unknown
      : row.alerts);
    const date = document.createElement('small');
    date.textContent = dateLabel;
    day.append(value, bars, date);
    chart.append(day);
  }
  section.append(chart);
  if (kind === 'detections') {
    const legend = document.createElement('ul');
    legend.className = 'dashboard-trend-legend';
    for (const [key, label] of [['drone', 'Drone'], ['bird', 'Bird'], ['unknown', 'Unknown']]) {
      const item = document.createElement('li');
      item.className = `is-${key}`;
      item.textContent = label;
      legend.append(item);
    }
    section.append(legend);
  }
  return section;
}

async function loadHeadquartersDashboard() {
  const status = $('#hq-dashboard-status');
  if (!status || state.profile?.role !== 'headquarters_admin') return;
  $('#hq-dashboard-username').textContent = state.profile.username || 'Headquarters Admin';
  $('#hq-dashboard-api').textContent = $('#health-text').textContent || 'Connected';
  try {
    const [sectorData, droneData, userData, requestData] = await Promise.all([
      api('/api/headquarters/sectors'),
      api('/api/headquarters/drones'),
      api('/api/headquarters/users'),
      api('/api/headquarters/registry'),
    ]);
    $('#hq-stat-sectors').textContent = formatNumber(Array.isArray(userData.approved_sectors) ? userData.approved_sectors.length : 0);
    $('#hq-stat-drones').textContent = formatNumber(Array.isArray(droneData.drones) ? droneData.drones.length : 0);
    $('#hq-stat-users').textContent = formatNumber(Array.isArray(userData.approved_sectors) ? userData.approved_sectors.reduce((total, sector) => total + (sector.members || []).length, 0) : 0);
    $('#hq-stat-pending').textContent = formatNumber(Array.isArray(requestData.requests) ? requestData.requests.length : 0);
    status.textContent = 'Summary refreshed ' + new Date().toLocaleTimeString() + '.';
    const trendStatus = $('#hq-trend-status');
    const trendsContainer = $('#hq-dashboard-trends');
    try {
      const trends = await api('/api/headquarters/dashboard-trends');
      trendsContainer.replaceChildren(
        createTrendChart(trends.daily_detection_trend, 'detections', 'Daily detections', trends.available),
        createTrendChart(trends.daily_alert_trend, 'alerts', 'Daily review-alert indicators', trends.available),
      );
      trendsContainer.hidden = !trends.available;
      trendStatus.textContent = trends.available
        ? `Aggregated across active approved sector accounts · ${trends.window_start} to ${trends.window_end}.`
        : 'No completed, readable reports are available yet. Cross-sector trends are not available.';
    } catch (error) {
      trendsContainer.hidden = true;
      trendStatus.textContent = `Cross-sector trends are not available: ${error.message}`;
    }
  } catch (error) {
    status.textContent = 'Could not load the Headquarters summary: ' + error.message;
  }
}

async function loadHeadquartersDrones() {
  const status = $('#hq-drones-status');
  const list = $('#hq-drones-list');
  if (!status || !list) return;
  status.textContent = 'Loading authorized drone registry…';
  try {
    const payload = await api('/api/headquarters/drones');
    const groups = new Map();
    for (const drone of payload.drones || []) {
      const key = drone.sector_id || 'Unassigned';
      if (!groups.has(key)) groups.set(key, { name: drone.sector_name || key, drones: [] });
      groups.get(key).drones.push(drone);
    }
    list.innerHTML = groups.size ? [...groups.entries()].map(([sectorId, group]) =>
      '<article class="hq-sector-card"><strong>' + escapeHTML(group.name) + '</strong><span>' + escapeHTML(sectorId) + ' · ' + group.drones.length + ' device(s)</span>' +
      group.drones.map((drone) => '<div class="hq-drone-row"><strong>' + escapeHTML(drone.label || drone.device_id) + '</strong><small>' + escapeHTML(drone.device_id_type.replaceAll('_', ' ')) + ' · ' + escapeHTML(drone.status) + '</small><small>Valid ' + escapeHTML(drone.valid_from) + ' → ' + escapeHTML(drone.valid_until) + '</small></div>').join('') +
      '</article>').join('') : '<p class="input-help">No authorized drones are registered yet.</p>';
    status.textContent = (payload.drones || []).length + ' authorized device(s) across ' + groups.size + ' sector(s).';
  } catch (error) {
    status.textContent = 'Could not load authorized drones: ' + error.message;
  }
}

function renderHeadquartersSectorSummary(sector, account) {
  const head = (sector.members || []).find((member) => member.member_role === 'head');
  return '<article class="hq-sector-card hq-approved-sector-card"><header class="hq-approved-sector-head"><div class="hq-approved-sector-title"><span class="hq-card-eyebrow">APPROVED SECTOR</span><h3>' + escapeHTML(sector.sector_name) + '</h3><span class="hq-sector-id">' + escapeHTML(sector.sector_id) + '</span></div></header>' +
    '<dl class="hq-sector-summary-details"><div><dt>SECTOR HEAD</dt><dd>' + escapeHTML(head?.full_name || 'Not provided') + '</dd></div><div><dt>HEAD PHONE</dt><dd>' + escapeHTML(sector.head_phone || 'Not provided') + '</dd></div></dl>' +
    '<div class="hq-sector-summary-actions"><button class="button button-secondary" type="button" data-view-sector-details="' + escapeHTML(sector.sector_id) + '">View more details</button></div>' +
    '<footer class="hq-sector-card-actions"><span>Decommissioning revokes this shared login and hides the area from active maps.</span><button class="button button-danger" type="button" data-decommission-sector="' + escapeHTML(sector.sector_id) + '">Decommission sector</button></footer></article>';
}

function renderHeadquartersSectorDetails(sector, account) {
  const members = (sector.members || []).map((member) => {
    const isHead = member.member_role === 'head';
    const initials = String(member.full_name || '?').trim().split(/\s+/).slice(0, 2).map((part) => part[0] || '').join('').toUpperCase();
    return '<article class="hq-member-row"><div class="hq-member-identity"><span class="hq-member-avatar" aria-hidden="true">' + escapeHTML(initials) + '</span><div class="hq-member-name"><strong>' + escapeHTML(member.full_name) + '</strong><span class="hq-member-role ' + (isHead ? 'is-head' : '') + '">' + (isHead ? 'Sector head' : 'Technical member') + '</span></div></div><div class="hq-member-meta"><span><b>Rank</b> ' + escapeHTML(member.rank) + '</span></div>' +
      '<button class="button button-secondary hq-member-personnel-action" type="button" data-replace-personnel="' + escapeHTML(sector.sector_id) + '" data-member-role="' + escapeHTML(member.member_role) + '" data-member-index="' + escapeHTML(member.member_index) + '">Change details</button></article>';
  }).join('');
  const accountState = account ? (account.must_change_password ? 'Password update required' : 'No password update pending') : 'Account unavailable';
  return '<div class="hq-sector-contact-grid"><div class="hq-sector-contact-item"><span>SECTOR ADDRESS</span><strong>' + escapeHTML(sector.sector_address || 'Not provided') + '</strong></div><div class="hq-sector-contact-item"><span>HEAD EMAIL</span><strong>' + escapeHTML(sector.head_email || account?.username || 'Not provided') + '</strong></div></div>' +
    '<p class="hq-sector-password-state">Head email is a roster contact address; changing it will not change the shared-login identity.</p><div class="hq-shared-login"><span>SHARED LOGIN</span><strong>' + escapeHTML(account?.username || 'Not available') + '</strong></div><p class="hq-sector-password-state">' + escapeHTML(accountState) + '</p>' +
    '<div class="hq-approved-roster-heading"><h3>Sector roster</h3><span>' + (sector.members || []).length + ' members</span></div><div class="hq-member-list">' + members + '</div>';
}

const HQ_USERS_PAGE_SIZE = 4;
let hqUsersPage = 1;

function renderHeadquartersUsersList(rosters, accounts) {
  const list = $('#hq-users-list');
  const pagination = $('#hq-users-pagination');
  const accountOnly = accounts.filter((account) => !rosters.some((sector) =>
    String(sector.sector_id || '').toUpperCase() === String(account.sector_number || '').toUpperCase()));
  const entries = [
    ...rosters.map((sector) => ({ type: 'sector', value: sector })),
    ...accountOnly.map((account) => ({ type: 'account', value: account })),
  ];
  const totalPages = Math.max(1, Math.ceil(entries.length / HQ_USERS_PAGE_SIZE));
  hqUsersPage = Math.min(Math.max(1, hqUsersPage), totalPages);
  const pageEntries = entries.slice((hqUsersPage - 1) * HQ_USERS_PAGE_SIZE, hqUsersPage * HQ_USERS_PAGE_SIZE);
  const rendered = pageEntries.map((entry) => {
    if (entry.type === 'sector') {
      const account = accounts.find((item) => String(item.sector_number || '').toUpperCase() === String(entry.value.sector_id || '').toUpperCase());
      return renderHeadquartersSectorSummary(entry.value, account);
    }
    const account = entry.value;
    return '<article class="hq-sector-card"><strong>Sector ' + escapeHTML(account.sector_number || 'Unassigned') + '</strong><span>Shared account</span><p class="input-help">' + escapeHTML(account.username) + (account.must_change_password ? ' · password update required' : '') + '</p></article>';
  });
  list.innerHTML = rendered.length ? rendered.join('') : '<p class="input-help">No approved sector users are registered yet.</p>';
  pagination.hidden = entries.length <= HQ_USERS_PAGE_SIZE;
  pagination.innerHTML = entries.length > HQ_USERS_PAGE_SIZE
    ? '<button class="button button-secondary" type="button" data-hq-users-page="previous"' + (hqUsersPage === 1 ? ' disabled' : '') + '>Previous</button><span aria-live="polite">Page ' + hqUsersPage + ' of ' + totalPages + '</span><button class="button button-secondary" type="button" data-hq-users-page="next"' + (hqUsersPage === totalPages ? ' disabled' : '') + '>Next</button>'
    : '';
}

async function loadHeadquartersUsers() {
  const status = $('#hq-users-status');
  const list = $('#hq-users-list');
  if (!status || !list) return;
  status.textContent = 'Loading approved sector users…';
  try {
    const payload = await api('/api/headquarters/users');
    const accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
    const rosters = Array.isArray(payload.approved_sectors) ? payload.approved_sectors : [];
    const decommissioned = Array.isArray(payload.decommissioned_sectors) ? payload.decommissioned_sectors : [];
    window.hqApprovedSectors = rosters;
    window.hqSharedAccounts = accounts;
    renderHeadquartersUsersList(rosters, accounts);
    status.textContent = rosters.length + ' active approved sector roster(s) | ' + accounts.length + ' shared account(s).';
    const history = $('#hq-decommission-history');
    if (history) history.innerHTML = decommissioned.length ? decommissioned.map((item) =>
      '<article class="hq-decommission-history-row"><div><strong>' + escapeHTML(item.sector_name) + ' <span>' + escapeHTML(item.sector_id) + '</span></strong><small>' + escapeHTML(item.decommissioned_at) + ' | ' + escapeHTML(item.head_email || 'No head email') + '</small></div><p>' + escapeHTML(item.reason) + '</p></article>'
    ).join('') : '<p class="input-help">No sectors have been decommissioned.</p>';
  } catch (error) {
    status.textContent = 'Could not load sector users: ' + error.message;
  }
}

async function loadHeadquartersSectors() {
  const status = $('#hq-sector-config-status');
  try {
    const [payload, locationPayload, usersPayload] = await Promise.all([
      api('/api/headquarters/sectors'), api('/api/headquarters/operator-locations'), api('/api/headquarters/users'),
    ]);
    window.operatorLocationCache = locationPayload.locations || [];
    window.hqApprovedSectors = Array.isArray(usersPayload.approved_sectors) ? usersPayload.approved_sectors : [];
    window.hqSectorPayload = payload;
    headquartersSectorDrafts = Array.isArray(payload.config.sector_areas)
      ? payload.config.sector_areas.map((sector) => ({...sector, bounds: {...sector.bounds}})) : null;
    headquartersLocationDraft = payload.config.headquarters_location ? {...payload.config.headquarters_location} : null;
    $('#hq-sector-location-status').textContent = headquartersLocationDraft
      ? `Pinned at ${headquartersLocationDraft.lat.toFixed(5)}, ${headquartersLocationDraft.lon.toFixed(5)}`
      : 'Headquarters location is not set.';
    headquartersSectorDrawing = false;
    headquartersSectorFirstCorner = null;
    headquartersSectorDrawBounds = null;
    headquartersSectorPreviewLayer = null;
    $('#hq-sector-draw-toggle').textContent = 'Draw sector area';
    $('#hq-sector-draw-status').textContent = '';
    $('#hq-sector-add-area').disabled = true;
    for (const [id, key] of [['hq-sector-south','south'],['hq-sector-north','north'],['hq-sector-west','west'],['hq-sector-east','east']]) {
      $(`#${id}`).value = payload.config[key];
    }
    renderHeadquartersSectorDraftList(payload);
    renderHeadquartersSectorMap(payload);
    if (status) status.textContent = '';
  } catch (error) {
    if (status) status.textContent = `Could not load HQ sectors: ${error.message}`;
  }
}
function toggleHeadquartersSectorDrawing() {
  if (!headquartersLeafletMap) {
    $('#hq-sector-draw-status').textContent = 'The map is unavailable. Check the map connection and try again.';
    return;
  }
  headquartersSectorDrawing = !headquartersSectorDrawing;
  headquartersSectorFirstCorner = null;
  if (headquartersSectorDrawing) {
    if (headquartersSectorDrafts === null) headquartersSectorDrafts = [];
    $('#hq-sector-draw-toggle').textContent = 'Finish drawing';
    $('#hq-sector-draw-status').textContent = 'Click two opposite corners on the map, inside the monitored boundary.';
    renderHeadquartersSectorDraftList(window.hqSectorPayload);
    renderHeadquartersSectorMap(window.hqSectorPayload);
  } else {
    $('#hq-sector-draw-toggle').textContent = 'Draw sector area';
    $('#hq-sector-draw-status').textContent = '';
  }
}
function addHeadquartersSectorDraft() {
  const status = $('#hq-sector-draw-status');
  const id = $('#hq-sector-new-id').value.trim().toUpperCase();
  const name = $('#hq-sector-new-name').value.trim();
  if (!headquartersSectorDrawBounds) { status.textContent = 'Draw a rectangle on the map first.'; return; }
  if (!/^S[1-9][0-9]*$/.test(id)) { status.textContent = 'Sector ID must look like S1 or S2.'; return; }
  if (!name || name.length > 80) { status.textContent = 'Enter a sector name from 1 to 80 characters.'; return; }
  if (headquartersSectorDrafts.some((sector) => sector.id.toUpperCase() === id)) { status.textContent = 'That sector ID is already in this layout.'; return; }
  const coverage = $('#hq-sector-coverage').value.trim();
  if (coverage.length > 240) { status.textContent = 'Keep village and locality names within 240 characters.'; return; }
  const next = headquartersSectorDrawBounds;
  const overlaps = headquartersSectorDrafts.some((sector) => {
    const b = sector.bounds;
    return Math.max(b.west,next.west) < Math.min(b.east,next.east) && Math.max(b.south,next.south) < Math.min(b.north,next.north);
  });
  if (overlaps) { status.textContent = 'Sector rectangles may touch edges but cannot overlap.'; return; }
  headquartersSectorDrafts.push({id, name, coverage, row: Math.floor((headquartersSectorDrafts.length)/8), column: headquartersSectorDrafts.length%8, bounds: {...next}});
  headquartersSectorDrawBounds = null;
  headquartersSectorPreviewLayer = null;
  $('#hq-sector-new-id').value = '';
  $('#hq-sector-new-name').value = '';
  $('#hq-sector-coverage').value = '';
  $('#hq-sector-add-area').disabled = true;
  renderHeadquartersSectorDraftList(window.hqSectorPayload);
  renderHeadquartersSectorMap(window.hqSectorPayload);
  status.textContent = `Added ${name} (${id}). Draw the next area or finish drawing.`;
}
async function saveHeadquartersSectors() {
  const button = $('#save-hq-sector-config');
  const status = $('#hq-sector-config-status');
  const config = {
    rows: Number($('#hq-sector-rows')?.value || 2), columns: Number($('#hq-sector-columns')?.value || 2),
    south: Number($('#hq-sector-south').value), north: Number($('#hq-sector-north').value),
    west: Number($('#hq-sector-west').value), east: Number($('#hq-sector-east').value),
  };
  if (headquartersSectorDrafts !== null) config.sector_areas = headquartersSectorDrafts;
  if (headquartersLocationDraft) config.headquarters_location = headquartersLocationDraft;
  button.disabled = true;
  status.textContent = headquartersSectorDrafts === null ? 'Saving the Headquarters sector grid…' : 'Validating and saving the drawn sector areas…';
  try {
    const payload = await api('/api/headquarters/sectors', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config),
    });
    window.hqSectorPayload = payload;
    headquartersSectorDrafts = Array.isArray(payload.config.sector_areas)
      ? payload.config.sector_areas.map((sector) => ({...sector, bounds: {...sector.bounds}})) : null;
    headquartersLocationDraft = payload.config.headquarters_location ? {...payload.config.headquarters_location} : null;
    $('#hq-sector-location-status').textContent = headquartersLocationDraft
      ? `Pinned at ${headquartersLocationDraft.lat.toFixed(5)}, ${headquartersLocationDraft.lon.toFixed(5)}`
      : 'Headquarters location is not set.';
    renderHeadquartersSectorDraftList(payload);
    renderHeadquartersSectorMap(payload);
    status.textContent = 'Saved. Registry requests can now use these official sector names and IDs.';
  } catch (error) {
    status.textContent = error.message;
    return false;
  } finally {
    button.disabled = false;
  }
  return true;
}

function authorizationMapPoint(lat, lon, config) {
  return { x: 80 + ((lon - config.west) / (config.east - config.west)) * 800, y: 60 + ((config.north - lat) / (config.north - config.south)) * 480 };
}
let authorizationLeafletMap = null;
let authorizationLeafletLayers = null;
let authorizationLeafletBoundsKey = null;
let latestKinematicAssessments = [];
let latestTrackLifecycleRows = [];
function renderAuthorizationLeafletMap(payload, scenario = null, result = null) {
  const container = $('#authorization-leaflet-map');
  const fallback = $('#authorization-leaflet-fallback');
  if (!container || !payload) return;
  if (!window.L) {
    if (fallback) fallback.hidden = false;
    return;
  }
  if (fallback) fallback.hidden = true;
  const { west, east, south, north } = payload.config;
  const boundsKey = [west, east, south, north].join(':');
  const bounds = [[south, west], [north, east]];
  if (!authorizationLeafletMap) {
    authorizationLeafletMap = L.map(container, { zoomControl: true, scrollWheelZoom: true });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(authorizationLeafletMap);
    authorizationLeafletLayers = L.layerGroup().addTo(authorizationLeafletMap);
    authorizationLeafletMap.fitBounds(bounds, { padding: [16, 16] });
    authorizationLeafletBoundsKey = boundsKey;
  }
  if (authorizationLeafletBoundsKey !== boundsKey) {
    authorizationLeafletMap.fitBounds(bounds, { padding: [16, 16] });
    authorizationLeafletBoundsKey = boundsKey;
  }
  authorizationLeafletLayers.clearLayers();
  for (const sector of payload.sectors) {
    const b = sector.bounds;
    L.polygon([[b.south,b.west],[b.south,b.east],[b.north,b.east],[b.north,b.west]], {
      color: '#00c9ed', weight: 2, fillColor: '#00bde0', fillOpacity: 0.13
    }).bindTooltip(escapeHTML(sector.name), { sticky: true }).addTo(authorizationLeafletLayers);
  }
  for (const item of payload.registry) {
    L.circleMarker([item.demo_location.lat, item.demo_location.lon], {
      radius: 5, color: '#075b72', weight: 2, fillColor: '#4cd7fc', fillOpacity: 0.9
    }).bindPopup(`${escapeHTML(item.label)}<br>${escapeHTML(item.drone_id)}<br>Home ${escapeHTML(item.home_sector)} · synthetic registry`).addTo(authorizationLeafletLayers);
  }
  if (scenario?.trail?.length > 1) {
    L.polyline(scenario.trail.map((point) => [point.lat, point.lon]), {
      color: '#d58a00', weight: 3, opacity: 0.9, dashArray: '7 6'
    }).addTo(authorizationLeafletLayers);
  }
  if (scenario?.lat != null && scenario?.lon != null) {
    L.circleMarker([scenario.lat, scenario.lon], {
      radius: 9, color: '#5a3500', weight: 3, fillColor: '#ffc247', fillOpacity: 1
    }).bindPopup(`${escapeHTML(scenario.drone_id)}<br>${escapeHTML(result?.current_sector || 'Scenario position')}<br>SIMULATED LOCATION`).addTo(authorizationLeafletLayers).openPopup();
  }
    for (const assessment of latestKinematicAssessments) {
      const trail = (assessment.points || []).map((point) => [Number(point.latitude), Number(point.longitude)]);
      if (!trail.length) continue;
      const color = assessment.derived_classification === 'bird' ? '#35c979' : assessment.derived_classification === 'drone' ? '#ef5261' : '#a8b2bd';
      if (trail.length > 1) L.polyline(trail, {color,weight:3,opacity:0.9}).addTo(authorizationLeafletLayers);
      const last = trail[trail.length - 1];
      const labelKind = ['bird','drone','unknown'].includes(assessment.derived_classification) ? assessment.derived_classification : 'unknown';
      const icon = L.divIcon({className:'trajectory-classification-map-label is-' + labelKind,html:'<span>' + escapeHTML(labelKind.toUpperCase()) + ' - ' + escapeHTML(assessment.device_id) + '</span>',iconSize:null});
      L.marker(last,{icon,interactive:false}).addTo(authorizationLeafletLayers);
    }
  window.setTimeout(() => authorizationLeafletMap?.invalidateSize(), 0);
}
function renderAuthorizationMap(payload, scenario = null, result = null) {
  const map = $('#authorization-map');
  if (!map || !payload) return;
  const regions = payload.sectors.map((sector) => {
    const b = sector.bounds;
    const left = authorizationMapPoint(b.south, b.west, payload.config).x;
    const right = authorizationMapPoint(b.south, b.east, payload.config).x;
    const top = authorizationMapPoint(b.north, b.west, payload.config).y;
    const bottom = authorizationMapPoint(b.south, b.west, payload.config).y;
    return `<g><rect class="authorization-sector sector-${sector.id.toLowerCase()}" x="${left}" y="${top}" width="${right-left}" height="${bottom-top}"/><text class="authorization-sector-label" x="${left+18}" y="${top+32}">${escapeHTML(sector.name)}</text></g>`;
  }).join('');
  const verticalGrid = Array.from({ length: payload.config.columns - 1 }, (_, i) => { const x = 80 + ((i + 1) / payload.config.columns) * 800; return `M${x} 60V540`; });
  const horizontalGrid = Array.from({ length: payload.config.rows - 1 }, (_, i) => { const y = 60 + ((i + 1) / payload.config.rows) * 480; return `M80 ${y}H880`; });
  const gridPath = [...verticalGrid, ...horizontalGrid].join(' ');
  const demoMarkers = payload.registry.map((item) => { const p = authorizationMapPoint(item.demo_location.lat, item.demo_location.lon, payload.config); return `<circle class="authorization-registry-marker" cx="${p.x}" cy="${p.y}" r="6"><title>${escapeHTML(item.drone_id)} · demo registry position</title></circle>`; }).join('');
  let route = '';
  let marker = '';
  if (scenario?.trail?.length) {
    const points = scenario.trail.map((point) => authorizationMapPoint(point.lat, point.lon, payload.config));
    route = `<polyline class="authorization-trail" points="${points.map((point) => `${point.x},${point.y}`).join(' ')}"/>`;
  }
  if (scenario?.lat != null && scenario?.lon != null) {
    const point = authorizationMapPoint(scenario.lat, scenario.lon, payload.config);
    marker = `<circle class="authorization-marker" cx="${point.x}" cy="${point.y}" r="10"><title>${escapeHTML(scenario.drone_id)} · ${escapeHTML(result?.current_sector || 'sample point')}</title></circle><text class="authorization-marker-label" x="${point.x+14}" y="${point.y-12}">${escapeHTML(scenario.drone_id)}</text>`;
  }
  map.innerHTML = `<svg viewBox="0 0 960 600" role="img" aria-label="Synthetic configurable sector grid and simulated drone trail">${regions}<path class="authorization-grid-line" d="${gridPath}"/>${demoMarkers}${route}${marker}<text class="authorization-map-caption" x="80" y="575">SYNTHETIC COORDINATE GRID · NOT A LIVE MAP</text></svg>`;
  $('#authorization-map-status').textContent = result ? `${result.current_sector || 'GPS unavailable'} · ${result.status.replaceAll('_', ' ')}` : 'Choose a scenario to replay';
  renderAuthorizationLeafletMap(payload, scenario, result);
}

function renderTrackCards(container, tracks) {
  if (!container) return;
  const hq = state.profile?.role === 'headquarters_admin';
  if (!tracks.length) { container.innerHTML = '<p class="input-help">No recent registered-device tracks are available for this account scope.</p>'; return; }
  container.innerHTML = tracks.map((item) => {
    const m=item.metrics||{}, id=escapeHTML(item.track_id), registry=Number(item.registry_id);
    const classification=escapeHTML(String(item.derived_classification||'unknown').toUpperCase());
    const review=escapeHTML(item.review_status||'unreviewed');
    const buttons='<button class="button button-secondary" type="button" data-track-details="'+registry+'" data-track-id="'+id+'" aria-expanded="false">View event history</button>' +
      (hq ? ['reviewed','cleared','simulated_neutralized'].map((decision)=>'<button class="button button-secondary" type="button" data-track-review="'+decision+'" data-registry-id="'+registry+'" data-track-id="'+id+'">'+(decision==='simulated_neutralized'?'Record simulated neutralization':decision==='reviewed'?'Mark reviewed':'Clear track')+'</button>').join('') : '');
    return '<article class="track-card"><div class="track-card-heading"><strong class="track-card-label trajectory-classification-label is-'+escapeHTML(item.derived_classification||'unknown')+'">'+classification+' · '+escapeHTML(item.device_id)+'</strong><span class="track-review-note">HQ review: '+review+'</span></div>'+
      '<span class="track-card-meta">'+escapeHTML(item.sector_name||item.sector_id)+' · Geofence: '+escapeHTML(item.authorization_state||'unknown')+' · Source: '+escapeHTML(item.source_type||'unknown')+' / '+escapeHTML(item.source_id||'unknown')+'</span>'+
      '<span class="track-card-meta">Last observation '+escapeHTML(item.last_seen_at||'unknown')+' · '+Number(m.average_velocity||0).toFixed(2)+' m/s · Heading variance '+Number(m.heading_variance||0).toFixed(4)+' · Jerk '+Number(m.max_jerk||0).toFixed(2)+' m/s³ · Hover '+(m.is_hovering?'yes':'no')+' · '+escapeHTML(item.classifier_version||'')+'</span>'+
      '<span class="track-card-explanation">'+escapeHTML(item.explanation||'No explanation recorded.')+'</span><div class="track-card-actions">'+buttons+'</div><div class="track-event-target" aria-live="polite"></div></article>';
  }).join('');
}
function renderKinematicAssessmentList() { renderTrackCards($('#trajectory-assessment-list'), latestTrackLifecycleRows); }
async function refreshKinematicAssessments() {
  const status = $('#trajectory-classifier-status');
  try {
    const [mapPayload, trackPayload] = await Promise.all([api('/api/authorization/trajectory/latest'), api('/api/authorization/tracks')]);
    latestKinematicAssessments = Array.isArray(mapPayload.assessments) ? mapPayload.assessments : [];
    latestTrackLifecycleRows = Array.isArray(trackPayload.tracks) ? trackPayload.tracks : [];
    renderKinematicAssessmentList();
    if (window.authorizationSimulationCache) renderAuthorizationLeafletMap(window.authorizationSimulationCache);
    if (status) status.textContent = latestTrackLifecycleRows.length ? 'Showing saved tracks, separate geofence results, and auditable HQ review state.' : 'No recent tracks in this account scope.';
  } catch (error) {
    if (status) status.textContent = 'Could not load saved tracks: ' + error.message;
  }
}
async function loadSectorTrackHistory() {
  const status=$('#sector-track-status');
  try { const payload=await api('/api/authorization/tracks'); renderTrackCards($('#sector-track-list'),payload.tracks||[]); if(status)status.textContent=(payload.tracks||[]).length?'Showing tracks assigned to this sector only.':'No tracks have been recorded for this sector.'; }
  catch(error) { if(status)status.textContent='Track history unavailable: '+error.message; }
}
async function openTrackEventHistory(button) {
  const card=button.closest('.track-card'), target=card?.querySelector('.track-event-target'); if(!target)return;
  if(button.getAttribute('aria-expanded')==='true'){target.replaceChildren();button.setAttribute('aria-expanded','false');return;}
  target.textContent='Loading event history…';
  try {
    const payload=await api('/api/authorization/tracks/'+encodeURIComponent(button.dataset.registryId||button.dataset.trackDetails)+'/'+encodeURIComponent(button.dataset.trackId));
    const events=payload.track.events||[];
    target.innerHTML=events.length?'<ol class="track-event-list">'+events.map((e)=>'<li><strong>'+escapeHTML(e.event_type)+'</strong> · '+escapeHTML(e.occurred_at)+' · '+escapeHTML(e.previous_value||'—')+' → '+escapeHTML(e.new_value||'—')+(e.reason?' · '+escapeHTML(e.reason):'')+'</li>').join('')+'</ol>':'<p class="input-help">No events recorded.</p>';
    button.setAttribute('aria-expanded','true');
  } catch(error) { target.textContent='Could not load history: '+error.message; }
}
async function submitTrackReview(button) {
  const reason=window.prompt('Reason for this HQ review decision (5–500 characters):'); if(reason===null)return;
  if(reason.trim().length<5||reason.trim().length>500){window.alert('Enter a reason from 5 to 500 characters.');return;}
  button.disabled=true;
  try { await api('/api/headquarters/tracks/'+encodeURIComponent(button.dataset.registryId)+'/'+encodeURIComponent(button.dataset.trackId)+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:button.dataset.trackReview,reason:reason.trim()})}); await refreshKinematicAssessments(); }
  catch(error) { const status=$('#trajectory-classifier-status'); if(status)status.textContent='Review not saved: '+error.message; button.disabled=false; }
}

async function submitKinematicTrajectory() {
  const button = $('#classify-trajectory');
  const status = $('#trajectory-classifier-status');
  const resultNode = $('#trajectory-classifier-result');
  let points;
  try {
    points = JSON.parse($('#trajectory-points-input').value);
    if (!Array.isArray(points)) throw new Error('Enter a JSON array of GPS points.');
  } catch (error) {
    status.textContent = error.message || 'The points JSON is invalid.';
    return;
  }
  const deviceId = $('#trajectory-device-id').value.trim();
  if (!deviceId) { status.textContent = 'Enter the registered device ID first.'; return; }
  button.disabled = true;
  status.textContent = 'Validating, classifying, and saving the trajectory window...';
  try {
    const result = await api('/api/authorization/telemetry', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({device_id:deviceId,device_id_type:$('#trajectory-device-type').value,
        track_id:$('#trajectory-track-id').value.trim() || 'default',points}),
    });
    const m = result.metrics;
    resultNode.innerHTML = '<strong class="trajectory-classification-label is-' + escapeHTML(result.derived_classification) +
      '">' + escapeHTML(result.derived_classification.toUpperCase()) + '</strong><span>' + escapeHTML(result.reason_code) +
      '</span><small>Average velocity ' + Number(m.average_velocity).toFixed(2) + ' m/s - Heading variance ' +
      Number(m.heading_variance).toFixed(4) + ' - Maximum jerk ' + Number(m.max_jerk).toFixed(2) +
      ' m/s^3 - Sustained hover ' + (m.is_hovering ? 'yes' : 'no') + '</small>';
    status.textContent = 'Trajectory classification response received.';
    await refreshKinematicAssessments();
  } catch (error) {
    status.textContent = 'Trajectory could not be classified: ' + error.message;
  } finally {
    button.disabled = false;
  }
}
async function loadAuthorizationSimulation() {
  const resultNode = $('#authorization-result');
  try {
    const payload = await api('/api/authorization/simulation');
    window.authorizationSimulationCache = payload;
    const config = payload.config;
    for (const [id, key] of [['sector-rows','rows'],['sector-columns','columns'],['sector-south','south'],['sector-north','north'],['sector-west','west'],['sector-east','east']]) {
      $(`#${id}`).value = config[key];
    }
    const select = $('#authorization-scenario');
    select.replaceChildren(...payload.scenarios.map((scenario) => {
      const option = document.createElement('option');
      option.value = scenario.id;
      option.textContent = scenario.title;
      return option;
    }));
    $('#authorization-registry-count').textContent = `${payload.registry.length} synthetic records`;
    $('#authorization-registry').innerHTML = payload.registry.map((item) => `<article class="authorization-unit"><span class="unit-status-dot"></span><div><strong>${escapeHTML(item.label)}</strong><small>${escapeHTML(item.drone_id)} · Home ${escapeHTML(item.home_sector)}</small></div><span class="unit-validity">Valid to ${escapeHTML(item.valid_until.slice(0,10))}</span></article>`).join('');
    renderAuthorizationMap(payload, payload.scenarios[0]);
    await refreshKinematicAssessments();
    resultNode.textContent = 'Select a scenario, then evaluate the simulated telemetry point.';
    $('#authorization-evaluate').disabled = false;
  } catch (error) {
    resultNode.textContent = `Could not load simulation: ${error.message}`;
    $('#authorization-evaluate').disabled = true;
  }
}

async function saveAuthorizationSectorConfig() {
  const status = $('#sector-config-status');
  const button = $('#save-sector-config');
  const config = {
    rows: Number($('#sector-rows').value), columns: Number($('#sector-columns').value),
    south: Number($('#sector-south').value), north: Number($('#sector-north').value),
    west: Number($('#sector-west').value), east: Number($('#sector-east').value),
  };
  button.disabled = true;
  status.textContent = 'Saving sector layout…';
  try {
    await api('/api/authorization/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
    status.textContent = 'Saved. Sector grid and demo routes updated.';
    await loadAuthorizationSimulation();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
async function evaluateAuthorizationScenario() {
  const payload = window.authorizationSimulationCache;
  const scenario = payload?.scenarios.find((item) => item.id === $('#authorization-scenario').value);
  if (!scenario) return;
  const button = $('#authorization-evaluate');
  const resultNode = $('#authorization-result');
  button.disabled = true;
  resultNode.textContent = 'Checking registry, permission window, and sector…';
  try {
    const result = await api('/api/authorization/evaluate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drone_id: scenario.drone_id, lat: scenario.lat, lon: scenario.lon, observed_at: scenario.time }),
    });
    resultNode.innerHTML = `<strong class="authorization-state state-${escapeHTML(result.status)}">${escapeHTML(result.status.replaceAll('_', ' ').toUpperCase())}</strong><p>${escapeHTML(result.message)}</p><dl><div><dt>Drone ID</dt><dd>${escapeHTML(result.drone_id || 'Not supplied')}</dd></div><div><dt>Home sector</dt><dd>${escapeHTML(result.home_sector || 'No registry match')}</dd></div><div><dt>Current sector</dt><dd>${escapeHTML(result.current_sector || 'Unavailable')}</dd></div><div><dt>Coordinate</dt><dd>${scenario.lat == null ? 'GPS unavailable' : `${scenario.lat.toFixed(4)}, ${scenario.lon.toFixed(4)}`}</dd></div></dl><small>Simulation only · not a hostile/friendly judgment</small>`;
    renderAuthorizationMap(payload, scenario, result);
  } catch (error) {
    resultNode.textContent = `Evaluation failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}
function setEntryView(view) {
  $('#splash-view').hidden = view !== 'splash';
  $('#login-view').hidden = view !== 'login';
  $('#registry-view').hidden = view !== 'registry';
  $('#dashboard-view').hidden = view !== 'dashboard';
}

function setLoginStatus(nodeText, nodeState = 'idle', progressText = '', progressState = 'busy') {
  const node = $('#login-node-status');
  if (node) {
    $('#login-node-label').textContent = nodeText;
    node.dataset.state = nodeState;
  }
  const progress = $('#login-progress');
  if (progress) {
    progress.hidden = !progressText;
    progress.dataset.state = progressState;
    $('#login-progress-text').textContent = progressText;
  }
}

function setLoginTerminal(lines = []) {
  const terminal = $('#login-terminal');
  window.clearInterval(loginTerminalTimer);
  terminal.replaceChildren();
  terminal.hidden = !lines.length;
  let index = 0;
  const appendNext = () => {
    if (index >= lines.length) {
      window.clearInterval(loginTerminalTimer);
      loginTerminalTimer = null;
      return;
    }
    const row = document.createElement('p');
    row.textContent = `> ${lines[index++]}`;
    terminal.append(row);
  };
  appendNext();
  if (lines.length > 1) loginTerminalTimer = window.setInterval(appendNext, 50);
}

function showLogin(message = '') {
  resetDashboardRun();
  state.inputSource = null;
  state.inputType = null;
  state.authenticated = false;
  state.cctvConnected = false;
  state.cctvReference = null;
  state.phase3Input = null;
  if ($('#source-cctv')) $('#source-cctv').setAttribute('aria-pressed', 'false');
  $('#source-upload').setAttribute('aria-pressed', 'false');
  document.querySelectorAll('[data-input-type]').forEach((button) => {
    button.setAttribute('aria-pressed', 'false');
  });
  $('#cctv-input').hidden = true;
  $('#upload-input').hidden = true;
  $('#file-picker-panel').hidden = true;
  $('#upload-status-panel').hidden = true;
  $('#phase3-panel').hidden = true;
  $('#phase4-panel').hidden = true;
  $('#phase5-panel').hidden = true;
  $('#phase6-panel').hidden = true;
  $('#phase3-preview').removeAttribute('src');
  $('#phase4-preview').removeAttribute('src');
  $('#cctv-status').textContent = 'Not connected';
  setEntryView('login');
  $('#login-error').textContent = message;
  $('#login-error').hidden = !message;
  $('#login-submit').disabled = false;
  $('#login-submit').setAttribute('aria-busy', 'false');
  $('#login-submit-label').textContent = 'Sign in';
  $('.login-submit-spinner').hidden = true;
  setLoginStatus(message ? 'Sign-in needs attention' : 'Waiting for sign-in', message ? 'error' : 'idle');
  setLoginTerminal(message ? ['Session needs attention. Sign in again.'] : ['Authentication API ready. Awaiting credentials.']);
  $('#authenticated-username').textContent = '';
  if (window.location.pathname !== '/login') history.replaceState(null, '', '/login');
}

function showDashboard(username) {
  state.authenticated = true;
  setEntryView('dashboard');
  $('#authenticated-username').textContent = username;
  $('#user-avatar').textContent = username.trim().charAt(0).toUpperCase() || 'U';
  $('#login-error').hidden = true;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    });
  } catch (error) {
    throw new Error(`Could not reach the dashboard API: ${error.message}`);
  }
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { message: text };
    }
  }
  if (!response.ok) {
    const message = payload.message || payload.error || `Request failed (${response.status})`;
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event('btf-auth-required'));
    }
    throw new Error(`${message} (HTTP ${response.status})`);
  }
  return payload;
}

function photoMimeType(file) {
  if (['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return file.type;
  const extension = file.name.split('.').pop().toLowerCase();
  return ({ jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp' })[extension] || '';
}

function renderPhotoProgress(phase, elapsedSeconds = 0) {
  const panel = $('#photo-progress');
  panel.hidden = !photoFile;
  const steps = [...panel.querySelectorAll('[data-photo-stage]')];
  const currentIndex = phase === 'ready' ? 0 : phase === 'processing' || phase === 'failed' ? 1 : 2;
  const messages = {
    ready: 'Photo ready — click Detect objects to start.',
    processing: 'Uploading photo and running detection…',
    completed: `Detection completed in ${elapsedSeconds.toFixed(1)} s.`,
    failed: `Detection stopped after ${elapsedSeconds.toFixed(1)} s. See the error above.`,
  };
  $('#photo-progress-message').textContent = messages[phase];
  const seconds = Math.floor(elapsedSeconds);
  $('#photo-progress-elapsed').textContent = `Elapsed ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  steps.forEach((step, index) => {
    step.classList.toggle('complete', phase === 'completed' || index < currentIndex || (phase === 'processing' && index === 0));
    step.classList.toggle('active', (phase === 'ready' && index === 0) || (phase === 'processing' && index === 1));
    step.classList.toggle('failed', phase === 'failed' && index === 1);
    const detail = step.querySelector('small');
    if (phase === 'ready') detail.textContent = index === 0 ? 'Ready to process' : index === 1 ? 'Waiting to start' : 'Waiting for detection';
    else if (phase === 'processing') detail.textContent = index === 0 ? 'Ready' : index === 1 ? 'In progress' : 'Waiting for detection';
    else if (phase === 'completed') detail.textContent = index === 0 ? 'Ready' : index === 1 ? 'Completed' : 'Ready to review';
    else detail.textContent = index === 0 ? 'Ready' : index === 1 ? 'Failed' : 'Not reached';
  });
}

function selectPhoto(file) {
  const error = $('#photo-error');
  error.hidden = true;
  $('#photo-results').hidden = true;
  photoDetectionResult = null;
  photoFile = null;
  const mime = photoMimeType(file);
  if (!mime) {
    error.textContent = 'Choose a JPEG, PNG, or WebP image.';
    error.hidden = false;
  } else if (file.size > 20 * 1024 * 1024) {
    error.textContent = 'The image is larger than the 20 MiB limit.';
    error.hidden = false;
  } else {
    photoFile = file;
    $('#photo-file-name').textContent = `${file.name} · ${(file.size / (1024 * 1024)).toFixed(2)} MiB`;
  }
  if (!photoFile) $('#photo-file-name').textContent = 'No valid photo selected.';
  $('#photo-detect-button').disabled = !photoFile;
  $('#photo-reset-button').disabled = !photoFile;
  if (photoFile) renderPhotoProgress('ready');
  else $('#photo-progress').hidden = true;
}

async function detectPhoto() {
  if (!photoFile || state.photoBusy) return;
  const button = $('#photo-detect-button');
  const error = $('#photo-error');
  error.hidden = true;
  state.photoBusy = true;
  photoProcessingStartedAt = performance.now();
  renderPhotoProgress('processing', 0);
  window.clearInterval(photoProgressTimer);
  photoProgressTimer = window.setInterval(() => {
    renderPhotoProgress('processing', (performance.now() - photoProcessingStartedAt) / 1000);
  }, 250);
  button.disabled = true;
  $('#photo-reset-button').disabled = true;
  $('#photo-input').disabled = true;
  button.textContent = 'Detecting…';
  try {
    const result = await api('/api/photo/detect', {
      method: 'POST',
      headers: { 'Content-Type': photoMimeType(photoFile) },
      body: photoFile,
    });
    photoDetectionResult = result;
    window.clearInterval(photoProgressTimer);
    photoProgressTimer = null;
    renderPhotoProgress('completed', (performance.now() - photoProcessingStartedAt) / 1000);
    $('#photo-result-image').src = `data:image/jpeg;base64,${result.frame_base64}`;
    const detections = result.detections || [];
    const counts = detections.reduce((summary, item) => {
      const label = item.class_name || 'Unknown';
      summary[label] = (summary[label] || 0) + 1;
      return summary;
    }, {});
    const droneCandidates = detections.filter((item) => /drone|uav/i.test(item.class_name || '')).length;
    const reviewAlert = $('#photo-review-alert');
    reviewAlert.hidden = droneCandidates === 0;
    reviewAlert.textContent = droneCandidates
      ? `Review recommended · ${droneCandidates} possible drone candidate${droneCandidates === 1 ? '' : 's'} in this photo. This is an unverified detector result; a still image cannot confirm authorization, identity, or intent.`
      : '';
    const classSummary = Object.entries(counts).map(([label, count]) => `${count} ${label}${count === 1 ? '' : 's'}`).join(' · ');
    $('#photo-result-summary').textContent = `${detections.length} object${detections.length === 1 ? '' : 's'} detected${classSummary ? ` · ${classSummary}` : ''} · ${result.width} × ${result.height} px · ${Number(result.inference_time_ms).toFixed(1)} ms · ${result.model_name}`;
    const countChips = $('#photo-class-counts');
    countChips.replaceChildren();
    Object.entries(counts).forEach(([label, count]) => {
      const chip = document.createElement('span');
      chip.className = `photo-count-chip ${/bird/i.test(label) ? 'bird' : /drone|uav/i.test(label) ? 'drone' : ''}`;
      chip.textContent = `${label}: ${count}`;
      countChips.append(chip);
    });
    const cards = $('#photo-detection-cards');
    cards.replaceChildren();
    if (!detections.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'No objects detected in this image.';
      cards.append(empty);
    } else {
      detections.forEach((detection, index) => {
        const card = document.createElement('article');
        const className = detection.class_name || 'Unknown';
        card.className = `photo-detection-card ${/bird/i.test(className) ? 'bird' : /drone|uav/i.test(className) ? 'drone' : ''}`;
        const heading = document.createElement('div');
        heading.className = 'photo-detection-card-heading';
        const title = document.createElement('strong');
        title.textContent = `Object ${index + 1} · ${className}`;
        const confidence = document.createElement('span');
        confidence.textContent = `${(Number(detection.confidence) * 100).toFixed(1)}% confidence`;
        heading.append(title, confidence);
        const bar = document.createElement('div');
        bar.className = 'photo-confidence-track';
        const fill = document.createElement('span');
        fill.style.width = `${Math.max(0, Math.min(100, Number(detection.confidence) * 100))}%`;
        bar.append(fill);
        const coords = document.createElement('p');
        coords.textContent = `Box position · left ${Math.round(detection.x1)}, top ${Math.round(detection.y1)} · right ${Math.round(detection.x2)}, bottom ${Math.round(detection.y2)} px`;
        card.append(heading, bar, coords);
        cards.append(card);
      });
    }
    $('#photo-results').hidden = false;
    $('#photo-results').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (requestError) {
    window.clearInterval(photoProgressTimer);
    photoProgressTimer = null;
    renderPhotoProgress('failed', (performance.now() - photoProcessingStartedAt) / 1000);
    error.textContent = `Detection could not complete: ${requestError.message}`;
    error.hidden = false;
  } finally {
    state.photoBusy = false;
    button.textContent = 'Detect objects';
    button.disabled = !photoFile;
    $('#photo-reset-button').disabled = !photoFile;
    $('#photo-input').disabled = false;
  }
}

function resetPhotoDetection() {
  photoFile = null;
  photoDetectionResult = null;
  window.clearInterval(photoProgressTimer);
  photoProgressTimer = null;
  photoProcessingStartedAt = null;
  $('#photo-input').value = '';
  $('#photo-file-name').textContent = 'No photo selected.';
  $('#photo-error').hidden = true;
  $('#photo-results').hidden = true;
  $('#photo-review-alert').hidden = true;
  $('#photo-progress').hidden = true;
  $('#photo-detect-button').disabled = true;
  $('#photo-reset-button').disabled = true;
}

function downloadPhotoResult(kind) {
  if (!photoDetectionResult) return;
  const base = (photoFile?.name || 'photo').replace(/\.[^.]+$/, '').replace(/[^a-z0-9_-]+/gi, '_');
  let href;
  let filename;
  if (kind === 'image') {
    href = `data:image/jpeg;base64,${photoDetectionResult.frame_base64}`;
    filename = `${base}_detected.jpg`;
  } else {
    const { frame_base64, ...metadata } = photoDetectionResult;
    href = URL.createObjectURL(new Blob([JSON.stringify({ source_file: photoFile?.name, ...metadata }, null, 2)], { type: 'application/json' }));
    filename = `${base}_detections.json`;
  }
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.click();
  if (kind !== 'image') window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return 'N/A';
  const total = Math.floor(Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours ? `${hours}h ${minutes}m ${remainder}s` : `${minutes}m ${remainder}s`;
}

function formatAnalysisTimelineDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return hours
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function durationFromTimestamps(startAt, endAt) {
  const start = Date.parse(startAt || '');
  const end = typeof endAt === 'number' ? endAt : Date.parse(endAt || '');
  return Number.isFinite(start) && Number.isFinite(end) && end >= start
    ? (end - start) / 1000
    : null;
}

function actualAnalysisDuration(run, now = Date.now()) {
  if (!run) return null;
  const terminal = ['completed', 'failed', 'cancelled'].includes(run.status);
  const start = Date.parse(run.started_at || '');
  const end = terminal ? Date.parse(run.completed_at || '') : now;
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
    return { seconds: (end - start) / 1000, source: 'timestamps' };
  }
  const rawDuration = run.analysis_duration_seconds;
  const legacyDuration = typeof rawDuration === 'number'
    ? rawDuration
    : typeof rawDuration === 'string' && rawDuration.trim()
      ? Number(rawDuration)
      : Number.NaN;
  if (terminal && Number.isFinite(legacyDuration) && legacyDuration >= 0) {
    return { seconds: legacyDuration, source: 'legacy' };
  }
  return null;
}

function runFilename(run) {
  return run?.original_filename || run?.input_filename || 'Uploaded video';
}

function analysisTimelineDuration(run, now = Date.now()) {
  if (!run) return null;
  const terminal = ['completed', 'failed', 'cancelled'].includes(run.status);
  const seconds = durationFromTimestamps(run.created_at, terminal ? run.completed_at : now);
  if (seconds !== null) {
    return { seconds, source: 'timestamps' };
  }
  const rawLegacyDuration = run.analysis_duration_seconds;
  const legacyDuration = typeof rawLegacyDuration === 'number'
    ? rawLegacyDuration
    : typeof rawLegacyDuration === 'string' && rawLegacyDuration.trim()
      ? Number(rawLegacyDuration)
      : Number.NaN;
  if (terminal
    && Number.isFinite(legacyDuration) && legacyDuration >= 0) {
    return { seconds: legacyDuration, source: 'legacy' };
  }
  return null;
}

function analysisTimelinePhaseStates(run) {
  const phases = ['detection', 'tracking', 'temporal', 'reliability', 'evidence', 'vlm', 'fusion', 'assessment'];
  const states = { uploaded: run ? 'complete' : 'pending' };
  if (!run) {
    for (const phase of phases) states[phase] = 'pending';
    states.completed = 'pending';
    return states;
  }
  const stageIndex = PIPELINE_STAGES.indexOf(run.stage);
  for (const [index, phase] of phases.entries()) {
    let phaseState = 'pending';
    const backendPhaseStatus = run.phase_progress?.[phase]?.status;
    if (phase === 'vlm' && !backendPhaseStatus && run.vlm_status) {
      phaseState = ({
        completed: 'complete',
        processing: 'active',
        failed: 'failed',
        skipped: 'skipped',
      })[run.vlm_status] || 'pending';
    } else if (backendPhaseStatus) {
      phaseState = ({
        completed: 'complete',
        processing: 'active',
        failed: 'failed',
        skipped: 'skipped',
        pending: 'pending',
      })[backendPhaseStatus] || 'pending';
    } else if (run.status === 'completed') {
      phaseState = 'complete';
    } else if (run.status === 'processing' && index + 1 < stageIndex) {
      phaseState = 'complete';
    } else if (run.status === 'processing' && index + 1 === stageIndex) {
      phaseState = 'active';
    }
    states[phase] = phaseState;
  }
  states.completed = run.status === 'completed'
    ? 'complete'
    : ['failed', 'cancelled'].includes(run.status) ? run.status : 'pending';
  return states;
}

function stopAnalysisTimelineTimer() {
  if (state.analysisTimelineTimer) clearInterval(state.analysisTimelineTimer);
  state.analysisTimelineTimer = null;
  state.analysisTimelineRunId = null;
}

function renderAnalysisTimeline(run = state.run) {
  const empty = !run || !run.run_id || run.run_id !== state.runId;
  const emptyMessage = $('#analysis-timeline-empty');
  const summary = $('#analysis-timeline-duration');
  const status = $('#analysis-timeline-status');
  const endpoint = $('#analysis-timeline-endpoint');
  const endpoints = $('#analysis-timeline-endpoints');
  const stages = $('#analysis-timeline-stages');
  const timerValue = $('#analysis-timeline-duration');
  emptyMessage.hidden = !empty;
  endpoints.hidden = empty;
  stages.hidden = empty;
  const states = analysisTimelinePhaseStates(empty ? null : run);
  document.querySelectorAll('[data-timeline-stage]').forEach((item) => {
    item.classList.remove('timeline-complete', 'timeline-active', 'timeline-failed', 'timeline-cancelled', 'timeline-skipped');
    const stageState = states[item.dataset.timelineStage];
    item.classList.add(`timeline-${stageState}`);
    item.querySelector('.timeline-marker').textContent = ({
      complete: '✓',
      active: '◉',
      failed: '⚠',
      cancelled: '×',
      skipped: '–',
      pending: '○',
    })[stageState] || '○';
    if (item.dataset.timelineStage === 'completed') {
      item.querySelector('.stage-label').textContent = run?.status === 'failed'
        ? 'Failed'
        : run?.status === 'cancelled' ? 'Cancelled' : 'Completed';
    }
  });
  if (empty) {
    stopAnalysisTimelineTimer();
    summary.textContent = '';
    $('#analysis-timeline-actual-time').textContent = '';
    status.textContent = '';
    endpoint.textContent = '';
    return;
  }

  const terminal = ['completed', 'failed', 'cancelled'].includes(run.status);
  const timing = analysisTimelineDuration(run);
  const actualTiming = actualAnalysisDuration(run);
  summary.textContent = timing
    ? `${timing.source === 'legacy' ? 'Legacy stored analysis time' : 'Total Elapsed'}: ${formatAnalysisTimelineDuration(timing.seconds)}`
    : 'Timing unavailable for this legacy run.';
  $('#analysis-timeline-actual-time').textContent = actualTiming
    ? `Actual Analysis Time: ${formatAnalysisTimelineDuration(actualTiming.seconds)}${actualTiming.source === 'legacy' ? ' (stored duration)' : ''}`
    : ['prepared', 'queued'].includes(run.status) && !run.started_at
      ? 'Actual Analysis Time: Not started'
      : 'Actual Analysis Time: Timing unavailable for this legacy run.';
  status.textContent = terminal && run.status !== 'completed'
    ? `Status: ${run.status.toUpperCase()}`
    : `Status: ${String(run.status || 'UNKNOWN').toUpperCase()}`;
  status.className = `timeline-status timeline-status-${run.status || 'unknown'}`;
  endpoint.textContent = terminal && timing ? `${formatAnalysisTimelineDuration(timing.seconds)} ${run.status === 'completed' ? 'Completed' : run.status.toUpperCase()}` : 'In progress';

  if (terminal || !Number.isFinite(Date.parse(run.created_at || ''))) {
    stopAnalysisTimelineTimer();
  } else if (state.analysisTimelineRunId !== run.run_id) {
    stopAnalysisTimelineTimer();
    state.analysisTimelineRunId = run.run_id;
    state.analysisTimelineTimer = setInterval(() => {
      if (state.runId !== run.run_id || state.run?.run_id !== run.run_id
        || ['completed', 'failed', 'cancelled'].includes(state.run.status)) {
        stopAnalysisTimelineTimer();
        return;
      }
      const currentTiming = analysisTimelineDuration(state.run);
      timerValue.textContent = currentTiming
        ? `Total Elapsed: ${formatAnalysisTimelineDuration(currentTiming.seconds)}`
        : 'Timing unavailable for this legacy run.';
      const currentActualTiming = actualAnalysisDuration(state.run);
      $('#analysis-timeline-actual-time').textContent = currentActualTiming
        ? `Actual Analysis Time: ${formatAnalysisTimelineDuration(currentActualTiming.seconds)}`
        : ['prepared', 'queued'].includes(state.run.status) && !state.run.started_at
          ? 'Actual Analysis Time: Not started'
          : 'Actual Analysis Time: Timing unavailable for this legacy run.';
      endpoint.textContent = 'In progress';
    }, 1000);
  }
}

function formatVideoTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'N/A';
  const wholeSeconds = Math.floor(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainder = wholeSeconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function updateVideoPlaybackControls() {
  const player = $('#input-video-player');
  const button = $('#video-play-toggle');
  if (!player || !button) return;
  const available = Boolean(player.currentSrc || player.getAttribute('src'))
    && player.readyState >= HTMLMediaElement.HAVE_METADATA;
  button.disabled = !available;
  const playing = !player.paused && !player.ended;
  $('#video-play-toggle-icon').textContent = playing ? '⏸' : '▶';
  $('#video-play-toggle-label').textContent = playing ? 'Pause video' : player.ended ? 'Play again' : 'Play video';
  button.setAttribute('aria-label', playing ? 'Pause video' : player.ended ? 'Play video again' : 'Play video');
  const current = formatVideoTime(player.currentTime);
  const duration = formatVideoTime(player.duration);
  $('#video-playback-time').textContent = `${current} / ${duration}`;
}

function formatVideoTimestamp(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'N/A';
  const milliseconds = Math.floor((seconds % 1) * 1000);
  return `${formatVideoTime(seconds)}.${String(milliseconds).padStart(3, '0')}`;
}

function videoFrameMetadata() {
  const fps = Number(state.inputMetadata?.fps);
  const totalFrames = Number(state.inputMetadata?.frame_count);
  if (!Number.isFinite(fps) || fps <= 0 || !Number.isSafeInteger(totalFrames) || totalFrames <= 0) {
    return null;
  }
  return { fps, totalFrames };
}

function currentFrameLabel() {
  const metadata = videoFrameMetadata();
  return state.currentFrameIndex && metadata
    ? `${state.currentFrameIndex} / ${formatNumber(metadata.totalFrames)}`
    : `— / ${formatNumber(metadata?.totalFrames)}`;
}

function currentFrameOverlayText(duration = $('#input-video-player').duration) {
  const evidenceLabel = state.alertEvidence?.frame_index === state.currentFrameIndex
    ? ' · ALERT EVIDENCE'
    : '';
  const player = $('#input-video-player');
  return `Frame ${currentFrameLabel()}  ·  ${formatVideoTime(player.currentTime)} / ${formatVideoTime(duration)}${evidenceLabel}`;
}

function renderVideoMetadata(metadata = {}) {
  const player = $('#input-video-player');
  const width = metadata.width || player.videoWidth;
  const height = metadata.height || player.videoHeight;
  const preparedWidth = state.preparedFrameResolution?.width
    || metadata.prepared_frame_width
    || metadata.prepared_width;
  const preparedHeight = state.preparedFrameResolution?.height
    || metadata.prepared_frame_height
    || metadata.prepared_height;
  const modelWidth = metadata.model_input_width;
  const modelHeight = metadata.model_input_height;
  const metadataDuration = Number(metadata.duration_seconds);
  const duration = metadata.duration_seconds !== null
    && metadata.duration_seconds !== undefined
    && Number.isFinite(metadataDuration)
    && metadataDuration >= 0
    ? metadataDuration
    : (Number.isFinite(player.duration) ? player.duration : null);
  const metadataFileSize = Number(metadata.file_size_bytes);
  const hasMetadataFileSize = metadata.file_size_bytes !== null
    && metadata.file_size_bytes !== undefined
    && Number.isFinite(metadataFileSize)
    && metadataFileSize >= 0;
  const storedRun = !state.selectedFile && state.run?.run_id === state.runId ? state.run : null;
  $('#video-info-filename').textContent = state.selectedFile?.name
    || (storedRun
      ? `${storedRun.source_video_id || 'Video ID unavailable'} · ${runFilename(storedRun)}`
      : metadata.filename)
    || 'N/A';
  $('#video-info-resolution').textContent = width && height ? `${width} × ${height}` : 'N/A';
  $('#video-info-prepared-resolution').textContent = preparedWidth && preparedHeight
    ? `${preparedWidth} × ${preparedHeight}`
    : 'N/A';
  $('#video-info-model-resolution').textContent = modelWidth && modelHeight
    ? `${modelWidth} × ${modelHeight}`
    : 'N/A';
  $('#video-info-fps').textContent = metadata.fps !== null
    && metadata.fps !== undefined
    && Number.isFinite(Number(metadata.fps))
    ? formatNumber(metadata.fps, 2)
    : 'N/A';
  $('#video-info-duration').textContent = duration !== null ? `${formatNumber(duration, 2)} seconds` : 'N/A';
  $('#video-info-frames').textContent = metadata.frame_count !== null && metadata.frame_count !== undefined
    ? formatNumber(metadata.frame_count)
    : 'N/A';
  $('#video-info-size').textContent = state.selectedFile
    ? `${(state.selectedFile.size / (1024 * 1024)).toFixed(1)} MiB`
    : hasMetadataFileSize
      ? `${(metadataFileSize / (1024 * 1024)).toFixed(1)} MiB`
      : 'N/A';
  $('#video-info-format').textContent = state.selectedFile?.name.split('.').pop().toUpperCase()
    || metadata.format
    || 'N/A';
  const inputType = state.inputType || state.run?.input_type || metadata.input_type;
  $('#video-info-type').textContent = inputType ? inputType.toUpperCase() : 'N/A';
  $('#current-frame-resolution').textContent = width && height ? `${width} × ${height}` : 'N/A';
  $('#video-frame-overlay').textContent = currentFrameOverlayText(duration);
}

function drawAlertEvidenceBox() {
  const evidence = state.alertEvidence;
  if (
    !evidence
    || evidence.frame_index !== state.currentFrameObservationFrame
    || evidence.frame_index !== state.currentFrameIndex
  ) return;
  const observation = evidence.observation || state.currentFrameObservations.find(
    (item) => String(item.track_id) === String(evidence.track_id),
  );
  const player = $('#input-video-player');
  if (!observation || !player.videoWidth || !player.videoHeight) return;
  const canvas = $('#current-frame-canvas');
  const context = canvas.getContext('2d');
  const { bbox } = observation;
  const xScale = canvas.width / player.videoWidth;
  const yScale = canvas.height / player.videoHeight;
  context.strokeStyle = '#ff6c72';
  context.lineWidth = 2;
  context.strokeRect(
    bbox.x1 * xScale,
    bbox.y1 * yScale,
    (bbox.x2 - bbox.x1) * xScale,
    (bbox.y2 - bbox.y1) * yScale,
  );
}

function drawCurrentVideoFrame() {
  const player = $('#input-video-player');
  if (!player.videoWidth || !player.videoHeight || player.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
  const canvas = $('#current-frame-canvas');
  const context = canvas.getContext('2d');
  context.drawImage(player, 0, 0, canvas.width, canvas.height);
  const evidenceTimestamp = state.alertEvidence?.frame_index === state.currentFrameIndex
    ? state.alertEvidence.observation?.timestamp
    : null;
  $('#current-frame-timestamp').textContent = Number.isFinite(evidenceTimestamp)
    ? formatVideoTimestamp(evidenceTimestamp)
    : formatVideoTimestamp(player.currentTime);
  $('#current-frame-resolution').textContent = `${player.videoWidth} × ${player.videoHeight}`;
  const metadata = videoFrameMetadata();
  const mappedFrameIndex = metadata
    ? Math.min(metadata.totalFrames, Math.floor(player.currentTime * metadata.fps) + 1)
    : null;
  state.currentFrameIndex = state.alertEvidence
    && Number.isSafeInteger(state.alertEvidence.frame_index)
    && Number.isFinite(state.alertEvidence.seek_timestamp)
    && Math.abs(player.currentTime - state.alertEvidence.seek_timestamp) < 0.000001
    ? state.alertEvidence.frame_index
    : mappedFrameIndex;
  $('#current-frame-index').textContent = currentFrameLabel();
  $('#video-frame-overlay').textContent = currentFrameOverlayText();
  drawAlertEvidenceBox();
  drawVideoTrailOverlay();
}

function drawVideoTrailOverlay() {
  const canvas = $('#video-trail-overlay');
  const player = $('#input-video-player');
  const stage = canvas.parentElement;
  const context = canvas.getContext('2d');
  const stageRect = stage.getBoundingClientRect();
  const videoRect = player.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(stageRect.width * scale));
  canvas.height = Math.max(1, Math.round(stageRect.height * scale));
  context.setTransform(scale, 0, 0, scale, 0, 0);
  context.clearRect(0, 0, stageRect.width, stageRect.height);
  if (!state.showVideoTrajectory) return;
  if (
    state.inputType === 'thermal'
    && state.selectedFile
    && state.playerRunId !== state.runId
  ) return;
  if (!player.videoWidth || !player.videoHeight || !state.currentFrameIndex) return;
  const samples = state.selectedTrailObservations
    .filter((item) => Number.isFinite(Number(item.frame))
      && Number(item.frame) <= state.currentFrameIndex
      && item.bbox
      && ['x1', 'y1', 'x2', 'y2'].every((key) => Number.isFinite(Number(item.bbox[key]))))
    .sort((left, right) => Number(left.frame) - Number(right.frame));
  if (samples.length < 2) return;
  const offsetX = videoRect.left - stageRect.left;
  const offsetY = videoRect.top - stageRect.top;
  context.beginPath();
  samples.forEach((item, index) => {
    const x = offsetX + ((Number(item.bbox.x1) + Number(item.bbox.x2)) / 2 / player.videoWidth) * videoRect.width;
    const y = offsetY + ((Number(item.bbox.y1) + Number(item.bbox.y2)) / 2 / player.videoHeight) * videoRect.height;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.strokeStyle = '#f4c542';
  context.lineWidth = 1.5;
  context.globalAlpha = 0.9;
  context.stroke();
  context.globalAlpha = 1;
}

function updateTrajectoryOverlayToggle() {
  const button = $('#video-trajectory-toggle');
  if (!button) return;
  const player = $('#input-video-player');
  const hasPath = Array.isArray(state.selectedTrailObservations)
    && state.selectedTrailObservations.length >= 2
    && Boolean(player.videoWidth && player.videoHeight);
  button.disabled = !hasPath;
  button.setAttribute('aria-pressed', String(state.showVideoTrajectory && hasPath));
  $('#video-trajectory-toggle-label').textContent = state.showVideoTrajectory ? 'Hide trajectory' : 'Show trajectory';
  button.title = hasPath
    ? 'Show or hide the selected track path on the video. The line uses image-plane pixels.'
    : 'Select a track with at least two stored observations to show its path.';
  drawVideoTrailOverlay();
}

function renderCurrentFrameObservations(frameIndex, observations) {
  state.currentFrameObservationFrame = frameIndex;
  state.currentFrameObservations = observations;
  const details = $('#current-frame-detection-details');
  details.replaceChildren();
  const heading = document.createElement('strong');
  heading.textContent = state.alertEvidence?.frame_index === frameIndex
    ? `${state.alertEvidence.label || 'Alert Evidence'} · Frame: ${frameIndex}`
    : `Frame: ${frameIndex}`;
  const count = document.createElement('p');
  count.textContent = `Tracked Objects: ${observations.length}`;
  details.append(heading, count);
  if (!observations.length) {
    const empty = document.createElement('p');
    empty.textContent = 'No tracked objects in this frame.';
    details.append(empty);
    return;
  }
  const list = document.createElement('ul');
  for (const observation of observations) {
    const item = document.createElement('li');
    if (
      state.alertEvidence?.frame_index === frameIndex
      && String(state.alertEvidence.track_id) === String(observation.track_id)
    ) item.className = 'alert-evidence-observation';
    const isSelectedEvidence = state.alertEvidence?.frame_index === frameIndex
      && String(state.alertEvidence.track_id) === String(observation.track_id)
      && state.alertEvidence.observation;
    const evidenceObservation = isSelectedEvidence
      ? state.alertEvidence.observation
      : null;
    const bbox = evidenceObservation?.bbox || observation.bbox;
    const className = evidenceObservation?.detector_class || observation.class;
    const confidence = evidenceObservation?.detector_confidence ?? observation.confidence;
    const selectedDetails = evidenceObservation
      ? ` · Reliability ${evidenceObservation.reliability_status || 'N/A'} · Score ${formatNumber(evidenceObservation.reliability_score, 3)}`
      : '';
    item.textContent = `Track ID ${observation.track_id} · ${className} · Confidence ${formatNumber(confidence, 3)}${selectedDetails} · Bounding Box (${formatNumber(bbox.x1, 1)}, ${formatNumber(bbox.y1, 1)}, ${formatNumber(bbox.x2, 1)}, ${formatNumber(bbox.y2, 1)})`;
    list.append(item);
  }
  details.append(list);
  drawAlertEvidenceBox();
}

function currentFrameDialogObservations() {
  const frame = state.currentFrameIndex;
  const observations = state.currentFrameObservationFrame === frame
    ? [...state.currentFrameObservations]
    : [];
  const evidence = state.alertEvidence;
  if (evidence?.frame_index === frame && evidence.observation) {
    const existingIndex = observations.findIndex(
      (item) => String(item.track_id) === String(evidence.track_id),
    );
    const enriched = {
      ...(existingIndex >= 0 ? observations[existingIndex] : {}),
      ...evidence.observation,
      track_id: evidence.track_id,
      class: evidence.observation.detector_class || evidence.label || 'Object',
      confidence: evidence.observation.detector_confidence ?? evidence.observation.confidence,
    };
    if (existingIndex >= 0) observations[existingIndex] = enriched;
    else observations.push(enriched);
  }
  return observations;
}

function renderCurrentFrameDialog() {
  const player = $('#input-video-player');
  const frame = state.currentFrameIndex;
  const evidenceTimestamp = state.alertEvidence?.frame_index === frame
    ? state.alertEvidence.observation?.timestamp
    : null;
  const timestamp = evidenceTimestamp !== null
    && evidenceTimestamp !== undefined
    && Number.isFinite(Number(evidenceTimestamp))
    ? Number(evidenceTimestamp)
    : player.currentTime;
  const observations = currentFrameDialogObservations();
  const observationsReady = state.currentFrameObservationFrame === frame;
  $('#current-frame-dialog-title').textContent = Number.isSafeInteger(frame)
    ? `Frame ${frame} detections`
    : 'Current frame detections';
  const frameCount = state.inputMetadata?.frame_count;
  $('#current-frame-dialog-meta').textContent = `Timestamp ${formatVideoTimestamp(timestamp)} · ${Number.isSafeInteger(frame) ? `Frame ${frame}${frameCount ? ` of ${formatNumber(frameCount)}` : ''}` : 'Frame index unavailable'} · ${observationsReady ? `${observations.length} detected object${observations.length === 1 ? '' : 's'}` : 'detections not loaded'}`;

  const image = $('#current-frame-dialog-canvas');
  const sourceCanvas = $('#current-frame-canvas');
  const width = player.videoWidth || sourceCanvas.width || 320;
  const height = player.videoHeight || sourceCanvas.height || 180;
  image.width = width;
  image.height = height;
  const context = image.getContext('2d');
  if (player.videoWidth && player.videoHeight && player.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
    context.drawImage(player, 0, 0, width, height);
  } else {
    context.drawImage(sourceCanvas, 0, 0, width, height);
  }

  const list = $('#current-frame-dialog-detections');
  list.replaceChildren();
  if (!observations.length) {
    const empty = document.createElement('p');
    empty.className = 'current-frame-dialog-empty';
    empty.textContent = observationsReady
      ? 'No tracked detections were saved for this frame.'
      : state.run?.status === 'completed'
        ? 'Stored detections for this frame have not loaded yet.'
        : 'Frame detections are not available yet. They appear after analysis results are saved.';
    list.append(empty);
  }

  for (const observation of observations) {
    const bbox = observation.bbox;
    const validBox = bbox && ['x1', 'y1', 'x2', 'y2'].every((key) => bbox[key] !== null && bbox[key] !== '' && Number.isFinite(Number(bbox[key])))
      && Number(bbox.x2) > Number(bbox.x1)
      && Number(bbox.y2) > Number(bbox.y1);
    const className = observation.detector_class || observation.class || 'Object';
    const accent = /bird/i.test(className) ? '#ffcc4d' : /drone|uav/i.test(className) ? '#4bd8ff' : '#c2d0da';
    if (validBox) {
      const x = Number(bbox.x1);
      const y = Number(bbox.y1);
      const boxWidth = Number(bbox.x2) - x;
      const boxHeight = Number(bbox.y2) - y;
      context.strokeStyle = accent;
      context.lineWidth = Math.max(2, width / 500);
      context.strokeRect(x, y, boxWidth, boxHeight);
      const label = `ID ${observation.track_id ?? '—'} · ${className}`;
      context.font = `600 ${Math.max(13, width / 55)}px Segoe UI, Arial, sans-serif`;
      const labelWidth = context.measureText(label).width + 12;
      const labelHeight = Math.max(22, width / 35);
      const labelY = Math.max(labelHeight, y);
      context.fillStyle = '#07121ddd';
      context.fillRect(x, labelY - labelHeight, labelWidth, labelHeight);
      context.fillStyle = accent;
      context.fillText(label, x + 6, labelY - labelHeight / 2, labelWidth - 10);
    }

    const card = document.createElement('article');
    card.className = 'frame-detection-card';
    card.style.setProperty('--frame-detection-accent', accent);
    const heading = document.createElement('div');
    heading.className = 'frame-detection-card-heading';
    const title = document.createElement('strong');
    title.textContent = `${className} · Track ${observation.track_id ?? '—'}`;
    const confidence = document.createElement('span');
    const confidenceValue = observation.detector_confidence ?? observation.confidence;
    confidence.textContent = confidenceValue !== null && confidenceValue !== undefined && Number.isFinite(Number(confidenceValue))
      ? `${(Number(confidenceValue) * 100).toFixed(1)}% confidence`
      : 'Confidence unavailable';
    heading.append(title, confidence);
    card.append(heading);
    if (validBox) {
      const boxText = document.createElement('p');
      boxText.textContent = `Bounding box: (${formatNumber(bbox.x1, 1)}, ${formatNumber(bbox.y1, 1)}, ${formatNumber(bbox.x2, 1)}, ${formatNumber(bbox.y2, 1)})`;
      card.append(boxText);
    }
    const reliability = observation.reliability_status;
    if (reliability) {
      const reliabilityText = document.createElement('p');
      const hasReliabilityScore = observation.reliability_score !== null
        && observation.reliability_score !== undefined
        && Number.isFinite(Number(observation.reliability_score));
      reliabilityText.textContent = `Reliability: ${reliability}${hasReliabilityScore ? ` · Score ${formatNumber(observation.reliability_score, 3)}` : ''}`;
      card.append(reliabilityText);
    }
    list.append(card);
  }
}

function openCurrentFrameDialog() {
  renderCurrentFrameDialog();
  $('#current-frame-dialog').showModal();
  $('#current-frame-canvas').setAttribute('aria-expanded', 'true');
}

function triggerSensorModeFeedback(mode) {
  const dashboard = $('#dashboard-view');
  const stage = $('.video-stage');
  const overlay = $('#sensor-mode-transition');
  dashboard.dataset.sensorMode = mode;
  stage.dataset.sensorMode = mode;
  overlay.textContent = mode === 'thermal'
    ? 'THERMAL SOURCE SELECTED · ORIGINAL PIXELS PRESERVED'
    : mode === 'rgb'
      ? 'RGB SOURCE SELECTED'
      : 'RADAR FORMAT NOT SUPPORTED';
  stage.classList.remove('sensor-transition-active');
  void stage.offsetWidth;
  stage.classList.add('sensor-transition-active');
  window.clearTimeout(state.sensorTransitionTimer);
  state.sensorTransitionTimer = window.setTimeout(() => {
    stage.classList.remove('sensor-transition-active');
  }, 850);
}

function clearCurrentFrameObservations(message) {
  const details = $('#current-frame-detection-details');
  details.replaceChildren();
  const heading = document.createElement('strong');
  heading.textContent = 'Detection Details';
  const status = document.createElement('p');
  status.textContent = message;
  details.append(heading, status);
}

async function refreshStoredFrameObservations() {
  if (
    state.inputType === 'thermal'
    && state.selectedFile
    && state.playerRunId !== state.runId
  ) {
    $('#current-frame-status').textContent = 'Thermal preview';
    clearCurrentFrameObservations(
      'RGB tracked observations are not displayed for thermal previews. Stored thermal detections are shown after Start Analysis completes.',
    );
    return;
  }
  const player = $('#input-video-player');
  const metadata = videoFrameMetadata();
  if (!metadata || !state.currentFrameIndex || !state.runId) {
    $('#current-frame-status').textContent = metadata
      ? 'Select an analyzed run'
      : 'Video time shown; frame mapping unavailable';
    clearCurrentFrameObservations('Stored tracked observations are unavailable for this frame.');
    return;
  }
  const isSelectedAlertEvidence = state.alertEvidence?.frame_index === state.currentFrameIndex;
  if (state.run?.status !== 'completed' && !(state.run?.status === 'processing' && isSelectedAlertEvidence)) {
    const isProcessing = state.run?.status === 'processing';
    $('#current-frame-status').textContent = isProcessing
      ? 'Full-video analysis in progress'
      : 'Not analyzed yet';
    clearCurrentFrameObservations(
      isProcessing
        ? 'Stored frame results will be available after the full-video analysis completes.'
        : 'Start Analysis to create stored tracked observations.',
    );
    return;
  }
  const frameIndex = state.currentFrameIndex;
  const requestKey = `${state.runId}:${frameIndex}`;
  if (state.lastFrameLookupKey === requestKey) return;
  state.lastFrameLookupKey = requestKey;
  const requestId = ++state.frameLookupRequest;
  $('#current-frame-status').textContent = 'Loading stored results…';
  clearCurrentFrameObservations('Loading stored tracked observations…');
  try {
    const result = await api(
      `/api/runs/${encodeURIComponent(state.runId)}/frames/${frameIndex}`,
    );
    if (requestId !== state.frameLookupRequest || requestKey !== `${state.runId}:${state.currentFrameIndex}`) return;
    renderCurrentFrameObservations(result.frame_index, result.observations);
    $('#current-frame-index').textContent = `${result.frame_index} / ${formatNumber(result.total_frames)}`;
    $('#current-frame-status').textContent = state.alertEvidence?.frame_index === result.frame_index
      ? (state.alertEvidence.label || 'Alert Evidence')
      : 'Analyzed';
    $('#current-frame-timestamp').textContent = result.timestamp === null
      ? formatVideoTimestamp(player.currentTime)
      : formatVideoTimestamp(
        state.alertEvidence?.frame_index === result.frame_index
          && Number.isFinite(state.alertEvidence.observation?.timestamp)
          ? state.alertEvidence.observation.timestamp
          : result.timestamp,
      );
  } catch (error) {
    if (requestId !== state.frameLookupRequest) return;
    $('#current-frame-status').textContent = `Lookup failed: ${error.message}`;
    clearCurrentFrameObservations('Stored tracked observations could not be loaded.');
  }
}

async function viewAlertEvidence(evidence, label = 'Alert Evidence') {
  const metadata = videoFrameMetadata();
  const player = $('#input-video-player');
  if (
    !evidence
    || !Number.isSafeInteger(evidence.frame_index)
    || evidence.frame_index < 1
    || !Number.isSafeInteger(evidence.track_id)
    || evidence.track_id < 0
    || !state.runId
  ) {
    $('#current-frame-status').textContent = 'Evidence unavailable';
    return;
  }
  const totalFrames = metadata?.totalFrames || evidence.total_frames;
  if (!Number.isSafeInteger(totalFrames) || evidence.frame_index > totalFrames) {
    $('#current-frame-status').textContent = 'Frame mapping unavailable';
    return;
  }
  if (state.playerRunId !== state.runId || !Number.isFinite(player.duration) || !player.videoWidth) {
    $('#current-frame-status').textContent = 'Matching video preview unavailable';
    return;
  }
  const timestamp = Number.isFinite(evidence.timestamp) && evidence.timestamp >= 0
    ? evidence.timestamp
    : metadata && (evidence.frame_index - 1) / metadata.fps;
  if (!Number.isFinite(timestamp) || timestamp < 0 || timestamp > player.duration) {
    $('#current-frame-status').textContent = 'Evidence timestamp unavailable';
    return;
  }
  state.alertEvidence = {
    frame_index: evidence.frame_index,
    track_id: evidence.track_id,
    observation: evidence,
    seek_timestamp: timestamp,
    label,
  };
  state.currentFrameIndex = evidence.frame_index;
  state.lastFrameLookupKey = null;
  $('#current-frame-index').textContent = `${evidence.frame_index} / ${formatNumber(totalFrames)}`;
  $('#current-frame-timestamp').textContent = formatVideoTimestamp(timestamp);
  $('#current-frame-status').textContent = label;
  if (Math.abs(player.currentTime - timestamp) > 0.001) {
    await new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        cleanup();
        reject(new Error('The evidence frame could not be loaded from the video.'));
      }, 8000);
      const cleanup = () => {
        window.clearTimeout(timeout);
        player.removeEventListener('seeked', onSeeked);
        player.removeEventListener('error', onError);
      };
      const onSeeked = () => { cleanup(); resolve(); };
      const onError = () => { cleanup(); reject(new Error('The video player could not seek to the evidence frame.')); };
      player.addEventListener('seeked', onSeeked, { once: true });
      player.addEventListener('error', onError, { once: true });
      player.currentTime = timestamp;
    });
  }
  drawCurrentVideoFrame();
  await refreshStoredFrameObservations();
}

async function applyPendingAlertEvidence(runId) {
  const pending = state.pendingAlertEvidence;
  if (
    !pending
    || pending.run_id !== runId
    || state.route?.page !== 'result-detail'
    || state.runId !== runId
  ) return;
  const player = $('#input-video-player');
  if (state.playerRunId !== runId || !player.videoWidth || !Number.isFinite(player.duration)) return;
  state.pendingAlertEvidence = null;
  await viewAlertEvidence(pending.evidence);
}

function handleAlertEvidenceClick(event) {
  const button = event.target.closest('[data-evidence-index]');
  if (!button) return;
  const evidenceEntry = state.alertRows[0]?.evidence?.[Number(button.dataset.evidenceIndex)];
  if (!evidenceEntry?.observation) return;
  const runId = state.alertRunId;
  if (!runId || runId !== state.routeRunId && state.route?.page === 'result-detail') return;
  void navigateTo(`/results/${encodeURIComponent(runId)}`, false, {
    alertEvidence: {
      run_id: runId,
      evidence: {
        ...evidenceEntry.observation,
        track_id: evidenceEntry.track_id,
      },
    },
  });
}

function updateCurrentVideoFrame() {
  drawCurrentVideoFrame();
  updateDashboardTrajectoryPlayback();
  void refreshStoredFrameObservations();
}

function showProcessedFrame(dataUrl, frameIndex, status) {
  const image = new Image();
  image.addEventListener('load', () => {
    const canvas = $('#current-frame-canvas');
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
  }, { once: true });
  image.src = dataUrl;
  $('#current-frame-index').textContent = frameIndex ?? 'N/A';
  $('#current-frame-status').textContent = status;
  $('#current-frame-resolution').textContent = state.selectedFile && $('#input-video-player').videoWidth
    ? `${$('#input-video-player').videoWidth} × ${$('#input-video-player').videoHeight}`
    : $('#phase3-dimensions').textContent.split(' · ')[0] || 'N/A';
}

function readableStage(stage) {
  return ({
    uploaded: 'Upload complete',
    detection: 'Detection',
    tracking: 'Tracking',
    temporal: 'Temporal analysis',
    reliability: 'Reliability',
    evidence: 'Selective evidence',
    vlm: 'VLM',
    fusion: 'Fusion',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
  })[stage] || 'Waiting';
}

function setApiHealth(online) {
  $('#health-dot').className = `health-dot ${online ? 'online' : 'offline'}`;
  $('#health-text').textContent = online ? 'API connected' : 'API unavailable';
}

function showGlobalError(message) {
  $('#global-error').textContent = message;
  $('#global-error').hidden = false;
}

function clearGlobalError() {
  $('#global-error').hidden = true;
  $('#global-error').textContent = '';
}

function clearRunResults() {
  state.resultsRequest += 1;
  state.alertRequest += 1;
  state.trajectoryRequest += 1;
  state.trajectoryDetails.clear();
  state.trajectoryDetailsRunId = null;
  state.selectedTrajectoryTrackId = null;
  state.run = null;
  state.resultsLoadedRunId = null;
  renderAnalysisTimeline(null);
  state.tracks = [];
  state.overview = null;
  state.fusionSummary = null;
  state.phaseTrackDetail = null;
  state.selectedPhaseTrackId = null;
  state.preparedFrameResolution = null;
  state.page = 1;
  state.activeTrackRequest += 1;
  state.frameLookupRequest += 1;
  state.lastFrameLookupKey = null;
  state.playerRunId = null;
  state.alertEvidence = null;
  state.currentFrameObservations = [];
  state.currentFrameObservationFrame = null;
  state.selectedTrailObservations = [];
  state.showVideoTrajectory = false;
  updateTrajectoryOverlayToggle();
  drawVideoTrailOverlay();
  clearCurrentFrameObservations('Stored tracked observations will appear after analysis completes.');
  $('#dashboard-total-objects').textContent = '—';
  $('#dashboard-drones').textContent = '—';
  $('#dashboard-birds').textContent = '—';
  $('#dashboard-uncertain').textContent = '—';
  $('#dashboard-reliability').textContent = 'N/A';
  $('#dashboard-run-status').textContent = 'N/A';
  $('#generate-report').disabled = true;
  state.report = null;
  if ($('#trajectory-dialog').open) $('#trajectory-dialog').close();
  $('#dashboard-trajectory-track').replaceChildren(new Option('No tracks available', ''));
  $('#dashboard-trajectory-track').disabled = true;
  $('#dashboard-trajectory-preview').disabled = true;
  $('#dashboard-trajectory-preview').setAttribute('aria-expanded', 'false');
  $('#dashboard-trajectory-preview-content').textContent = 'Select a completed run to view its stored trajectory.';
  $('#trajectory-dialog-content').replaceChildren();
  renderAlerts(null);
  $('#results-section').hidden = true;
  $('#run-error').hidden = true;
  $('#track-rows').replaceChildren();
  $('#pagination').replaceChildren();
  $('#detail-backdrop').hidden = true;
  $('#detail-panel').classList.remove('open');
  $('#detail-panel').setAttribute('aria-hidden', 'true');
  $('#detail-panel').setAttribute('inert', '');
}

function setThermalPreparationView(active) {
  const selectors = [
    '#analysis-timeline',
    '#run-section',
    '#results-section',
    '#active-alerts',
    '#dashboard-trajectory',
    '.run-summary-card',
    '#generate-report',
  ];
  const elements = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
  if (active && !state.thermalPreparationView) {
    state.thermalPreparationSnapshot = {
      phaseExecution: { ...state.phaseExecution },
      selectedStage: state.selectedStage,
      visibility: elements.map((element) => [element, element.hidden]),
    };
  }
  state.thermalPreparationView = active;
  if (active) {
    elements.forEach((element) => { element.hidden = true; });
    return;
  }
  const snapshot = state.thermalPreparationSnapshot;
  state.thermalPreparationSnapshot = null;
  snapshot?.visibility.forEach(([element, hidden]) => { element.hidden = hidden; });
  if (snapshot) {
    state.phaseExecution = snapshot.phaseExecution;
    state.selectedStage = snapshot.selectedStage;
    if (state.run) renderPipeline(state.run);
    if (state.report) renderAlerts(state.report);
    if (state.tracks.length) void renderDashboardTrajectory();
  }
}

function showSelectedFile(file) {
  const extension = file.name.split('.').pop().toLowerCase();
  const error = !ALLOWED_EXTENSIONS.has(extension)
    ? 'Unsupported video type. Choose MP4, AVI, MOV, or MKV.'
    : file.size === 0
      ? 'The selected video is empty.'
      : file.size > MAX_UPLOAD_BYTES
        ? 'The selected video exceeds the 250 MiB upload limit.'
        : '';
  $('#file-name').textContent = file.name;
  $('#file-type').textContent = `${extension.toUpperCase()} · ${formatNumber(file.size)} bytes`;
  $('#selected-file').hidden = false;
  $('#drop-zone').hidden = true;
  state.selectedFile = file;
  state.inputMetadata = null;
  state.playerRunId = null;
  state.currentFrameIndex = null;
  state.lastFrameLookupKey = null;
  state.frameLookupRequest += 1;
  state.alertEvidence = null;
  state.currentFrameObservations = [];
  state.currentFrameObservationFrame = null;
  state.preparedFrameResolution = null;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = URL.createObjectURL(file);
  const player = $('#input-video-player');
  player.src = state.previewUrl;
  state.selectedTrailObservations = [];
  state.showVideoTrajectory = false;
  updateTrajectoryOverlayToggle();
  player.hidden = false;
  $('#video-player-empty').hidden = true;
  $('#video-frame-overlay').hidden = false;
  $('#player-source-status').textContent = 'Local preview';
  $('#video-info-filename').textContent = file.name;
  $('#video-info-size').textContent = `${(file.size / (1024 * 1024)).toFixed(1)} MiB`;
  $('#video-info-format').textContent = extension.toUpperCase();
  $('#video-info-type').textContent = state.inputType ? state.inputType.toUpperCase() : 'N/A';
  $('#video-info-fps').textContent = 'N/A';
  $('#video-info-frames').textContent = 'N/A';
  $('#video-info-resolution').textContent = 'N/A';
  $('#video-info-duration').textContent = 'N/A';
  $('#current-frame-status').textContent = 'Local preview';
  $('#current-frame-index').textContent = 'N/A';
  $('#current-frame-timestamp').textContent = '0:00';
  $('#current-frame-resolution').textContent = 'N/A';
  $('#prepare-input').disabled = Boolean(error) || state.busy || !state.inputType || state.inputType === 'radar';
  $('#prepare-input').hidden = false;
  $('#player-source-status').textContent = 'Local preview';
  $('#current-frame-canvas').getContext('2d').clearRect(
    0, 0, $('#current-frame-canvas').width, $('#current-frame-canvas').height,
  );
  renderVideoMetadata();
  clearCurrentFrameObservations('Stored tracked observations will appear after analysis completes.');
  if (error) showGlobalError(error);
  else clearGlobalError();
}

function resetUpload() {
  $('#video-input').value = '';
  $('#selected-file').hidden = true;
  $('#drop-zone').hidden = false;
  $('#prepare-input').disabled = true;
  $('#upload-progress-wrap').hidden = true;
  $('#upload-progress').value = 0;
  $('#input-prepared').hidden = true;
  $('#global-error').hidden = true;
  state.selectedFile = null;
  state.inputMetadata = null;
  state.preparedThermalRun = null;
  $('#start-analysis-flow').hidden = true;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  const player = $('#input-video-player');
  player.pause();
  player.removeAttribute('src');
  player.load();
  updateVideoPlaybackControls();
  player.hidden = true;
  $('#video-player-empty').hidden = false;
  $('#video-frame-overlay').hidden = true;
  $('#player-source-status').textContent = 'Awaiting input';
  $('#current-frame-status').textContent = 'Waiting';
  state.preparedFrameResolution = null;
  for (const id of ['video-info-filename', 'video-info-resolution', 'video-info-prepared-resolution', 'video-info-model-resolution', 'video-info-fps', 'video-info-duration', 'video-info-frames', 'video-info-size', 'video-info-format', 'video-info-type', 'current-frame-index', 'current-frame-timestamp', 'current-frame-resolution']) {
    $(`#${id}`).textContent = 'N/A';
  }
  const context = $('#current-frame-canvas').getContext('2d');
  context.clearRect(0, 0, $('#current-frame-canvas').width, $('#current-frame-canvas').height);
}

function setBusy(busy) {
  state.busy = busy;
  const file = $('#video-input').files[0];
  $('#prepare-input').disabled = busy || !file
    || !ALLOWED_EXTENSIONS.has(file.name.split('.').pop().toLowerCase())
    || file.size === 0
    || file.size > MAX_UPLOAD_BYTES
    || !state.inputType || state.inputType === 'radar'
    || state.run?.status === 'prepared';
  $('#start-analysis-flow').disabled = busy;
  $('#new-analysis').disabled = busy || ['queued', 'processing'].includes(state.run?.status);
}

function prepareUploadedInput(file, inputType) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('source', 'upload');
    form.append('input_type', inputType);
    form.append('file', file, file.name);
    const request = new XMLHttpRequest();
    request.open('POST', '/api/input/prepare');
    request.withCredentials = true;
    request.setRequestHeader('Accept', 'application/json');
    request.upload.addEventListener('progress', (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
      $('#upload-progress').value = percent;
      $('#upload-progress-value').textContent = `${percent}%`;
    });
    request.addEventListener('load', () => {
      let payload = {};
      try {
        payload = JSON.parse(request.responseText || '{}');
      } catch {
        reject(new Error('The server returned an unreadable upload response.'));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        if (request.status === 401) window.dispatchEvent(new Event('btf-auth-required'));
        reject(new Error(`${payload.message || payload.error || 'Upload failed'} (HTTP ${request.status})`));
        return;
      }
      resolve(payload);
    });
    request.addEventListener('error', () => reject(new Error('Network error while uploading the video.')));
    request.addEventListener('abort', () => reject(new Error('The video upload was cancelled.')));
    request.send(form);
  });
}

async function beginAnalysis() {
  const file = $('#video-input').files[0];
  if (!file || !state.inputType || state.inputType === 'radar' || state.busy) return;
  if (state.phase3Input) await stopPhase3Input();
  setBusy(true);
  clearGlobalError();
  $('#upload-progress-wrap').hidden = false;
  $('#upload-progress-label').textContent = 'Validating and preparing input';
  $('#upload-progress').value = 0;
  $('#upload-progress-value').textContent = '0%';
  try {
    const created = await prepareUploadedInput(file, state.inputType);
    const thermalPreparation = created.input_type === 'thermal';
    if (thermalPreparation) {
      setThermalPreparationView(true);
      state.preparedThermalRun = {
        ...created,
        status: 'prepared',
        stage: 'uploaded',
        progress: 0,
        input_metadata: created.metadata || {},
      };
    } else {
      state.preparedThermalRun = null;
      setThermalPreparationView(false);
      clearRunResults();
      state.runId = created.run_id;
      state.playerRunId = created.run_id;
      state.run = { ...created, status: 'prepared' };
      state.phaseExecution = {};
      state.selectedStage = 'uploaded';
      localStorage.setItem(RUN_STORAGE_KEY, state.runId);
    }
    state.preparedFrameResolution = null;
    state.inputMetadata = created.metadata || {};
    renderVideoMetadata(state.inputMetadata);
    updateCurrentVideoFrame();
    $('#player-source-status').textContent = 'Input validated';
    $('#current-frame-status').textContent = 'Input validated';
    state.phase3Input = null;
    if (!thermalPreparation) {
      $('#run-section').hidden = false;
      $('#run-id').textContent = state.runId;
      $('#results-run-id').textContent = `Run ${state.runId}`;
      $('#run-stage').textContent = 'Input prepared';
      $('#run-status-text').textContent = 'VALIDATED · PIPELINE NOT STARTED';
      $('#run-progress-text').textContent = '0%';
      $('#run-progress').value = 0;
      $('#run-status-badge').textContent = 'Prepared';
      $('#run-status-badge').className = 'status-badge status-prepared';
    }
    $('#start-analysis-flow').hidden = !['rgb', 'thermal'].includes(created.input_type);
    $('#start-analysis-flow').disabled = false;
    if (!thermalPreparation) renderPipeline(state.run);
    $('#upload-progress').value = 100;
    $('#upload-progress-value').textContent = '100%';
    const metadata = Object.entries(created.metadata || {})
      .filter(([, value]) => value !== null)
      .map(([key, value]) => `${key.replaceAll('_', ' ')}: ${typeof value === 'object' ? JSON.stringify(value) : value}`)
      .join(' · ');
    const supportStatus = thermalPreparation
      ? 'Full-video thermal detection and tracking are enabled. Drone-class tracks produce detector-only review alerts; predicted classes are not verified ground truth.'
      : 'Analysis has not been started.';
    $('#input-prepared').textContent = `${created.input_type.toUpperCase()} input validated. ${created.source_video_id ? `Source Video ID: ${created.source_video_id}. ` : ''}${metadata || 'Optional video metadata unavailable.'} ${supportStatus}`;
    $('#input-prepared').hidden = false;
    if (!thermalPreparation) {
      state.reuseCandidateRunId = null;
      $('#reuse-suggestion').hidden = true;
      void loadReuseCandidates(created.run_id);
    }
    showPhase3Input({
      source_type: 'upload',
      input_type: created.input_type,
      run_id: created.run_id,
    });
  } catch (error) {
    showGlobalError(`Input preparation failed: ${error.message}`);
  } finally {
    setBusy(false);
    $('#upload-progress-wrap').hidden = true;
  }
}

async function startPreparedAnalysis() {
  const thermalRun = state.preparedThermalRun;
  const runId = thermalRun?.run_id || state.runId;
  if (!runId || state.busy) return;
  $('#concurrent-run-warning').hidden = true;
  setBusy(true);
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/start`, {
      method: 'POST',
      body: '',
    });
    $('#input-prepared').textContent = 'Analysis process started. Processing is underway; follow the live progress below.';
    $('#input-prepared').hidden = false;
    if (thermalRun) {
      setThermalPreparationView(false);
      clearRunResults();
      state.runId = runId;
      state.phaseExecution = {};
      state.selectedStage = 'uploaded';
      state.phase3Input = null;
      state.run = { ...thermalRun, status: 'processing', stage: 'detection', progress: 8 };
      state.playerRunId = runId;
      state.preparedThermalRun = null;
      localStorage.setItem(RUN_STORAGE_KEY, runId);
      $('#run-section').hidden = false;
      renderAlerts({ run_id: runId, alert_status: 'not_run' });
      renderPipeline(state.run);
    }
    state.phase3Input = null;
    $('#phase3-panel').hidden = true;
    $('#phase4-panel').hidden = true;
    $('#phase5-panel').hidden = true;
    $('#phase6-panel').hidden = true;
    $('#start-analysis-flow').hidden = true;
    $('#phase-detail-empty').hidden = false;
    $('#phase-detail-empty').textContent = thermalRun
      ? 'Thermal detection, tracking and offline temporal consensus are processing the uploaded video. Unsupported downstream stages will be marked as skipped.'
      : 'The full-video analysis is running. Frame-level controls are unavailable while this run is active.';
    await pollRun(runId);
  } catch (error) {
    if (error.message === 'Another video run is already processing (HTTP 409)') {
      await showConcurrentRunWarning();
    } else {
      showGlobalError(`Could not start analysis: ${error.message}`);
    }
  } finally {
    setBusy(false);
  }
}

async function showConcurrentRunWarning() {
  const warning = $('#concurrent-run-warning');
  const message = $('#concurrent-run-message');
  const viewButton = $('#view-processing-run');
  warning.hidden = false;
  const selectedVideo = state.selectedFile?.name || state.runId;
  message.textContent = `Another analysis is currently running. Your selected video (${selectedVideo}) is prepared; a different video is being processed.`;
  viewButton.hidden = true;
  $('#run-error').hidden = true;
  try {
    const result = await api('/api/runs');
    const processingRun = result.runs?.find((run) => run.status === 'processing');
    if (processingRun) {
      viewButton.hidden = false;
    } else {
      message.textContent = 'The other analysis is no longer processing. Please try Start Analysis again.';
    }
  } catch (error) {
    message.textContent = `Another analysis is currently running, but its status could not be loaded: ${error.message}`;
  }
}

async function viewProcessingRun() {
  const warning = $('#concurrent-run-warning');
  const message = $('#concurrent-run-message');
  const viewButton = $('#view-processing-run');
  viewButton.disabled = true;
  try {
    const result = await api('/api/runs');
    const processingRun = result.runs?.find((run) => run.status === 'processing');
    if (!processingRun) {
      message.textContent = 'The other analysis is no longer processing. Please try Start Analysis again.';
      viewButton.hidden = true;
      return;
    }
    const opened = await openHistoryRun(processingRun.run_id);
    if (!opened || state.runId !== processingRun.run_id || state.run?.status !== 'processing') {
      message.textContent = 'The other analysis is no longer processing. Please try Start Analysis again.';
      viewButton.hidden = true;
      return;
    }
    warning.hidden = true;
  } catch (error) {
    message.textContent = `Could not open the processing run: ${error.message}`;
  } finally {
    viewButton.disabled = false;
  }
}

function renderPipeline(run) {
  renderAnalysisTimeline(run);
  if (run?.status === 'prepared') {
    $('#run-stage').textContent = 'Input prepared';
    $('#run-status-text').textContent = 'VALIDATED · PIPELINE NOT STARTED';
    $('#run-progress-text').textContent = '0%';
    $('#run-progress').value = 0;
    $('#run-status-badge').textContent = 'Prepared';
    $('#run-status-badge').className = 'status-badge status-prepared';
    $('#start-analysis-flow').hidden = !['rgb', 'thermal'].includes(run.input_type);
  }
  if (run?.status !== 'prepared') $('#start-analysis-flow').hidden = true;
  const stageIndex = PIPELINE_STAGES.indexOf(run?.stage);
  for (const [index, stage] of PIPELINE_STAGES.entries()) {
    const item = document.querySelector(`[data-stage="${stage}"]`);
    const icon = item.querySelector('.stage-icon');
    const stageState = item.querySelector('.stage-state');
    let progressDetail = item.querySelector('.stage-progress');
    if (!progressDetail) {
      progressDetail = document.createElement('div');
      progressDetail.className = 'stage-progress';
      item.append(progressDetail);
    }
    item.classList.remove('stage-complete', 'stage-active', 'stage-failed', 'stage-skipped');
    let status = 'pending';

    if (stage === 'vlm') {
      const vlmStatus = run?.vlm_status || (run?.status === 'completed' ? 'unknown' : 'pending');
      status = ({
        completed: 'complete',
        processing: 'active',
        failed: 'failed',
        skipped: 'skipped',
      })[vlmStatus] || (vlmStatus === 'unknown' ? 'unknown' : 'pending');
    } else {
      if (run?.status === 'completed') {
        status = 'complete';
      } else if (run?.status === 'queued' || run?.status === 'prepared') {
        status = index === 0 ? 'complete' : 'pending';
      } else if (run?.status === 'processing' && index < stageIndex) {
        status = 'complete';
      } else if (run?.status === 'processing' && index === stageIndex) {
        status = 'active';
      } else if (run?.status === 'failed' || run?.status === 'cancelled') {
        status = index === 0 ? 'complete' : 'pending';
      }
    }

    const phaseProgress = run?.phase_progress?.[stage];
    if (phaseProgress?.status) {
      status = ({
        completed: 'complete',
        processing: 'active',
        failed: 'failed',
        skipped: 'skipped',
        pending: 'pending',
      })[phaseProgress.status] || status;
    }
    const directStatus = state.phaseExecution[stage];
    if (directStatus) status = directStatus;
    const selected = state.selectedStage === stage;
    const button = item.querySelector('.stage-button');
    button.setAttribute('aria-pressed', String(selected));
    item.classList.toggle('stage-selected', selected);
    item.classList.add(`stage-${status}`);
    icon.textContent = ({
      complete: '✓',
      active: '◉',
      failed: '⚠',
      skipped: '–',
      unknown: '?',
      pending: '○',
    })[status] || '○';
    stageState.textContent = directStatus === 'complete'
      ? (stage === 'uploaded' ? 'Ready' : 'Processed')
      : ({
        complete: 'Completed',
        active: 'Processing',
        failed: 'Failed',
        skipped: 'Skipped',
        unknown: 'Unknown',
        pending: 'Pending',
      })[status] || 'Pending';
    progressDetail.replaceChildren();
    const current = phaseProgress?.current;
    const total = phaseProgress?.total;
    const hasCount = Number.isInteger(current) && Number.isInteger(total) && total > 0;
    if (hasCount && ['active', 'complete'].includes(status)) {
      const percent = Math.round((current / total) * 100);
      const bar = document.createElement('progress');
      bar.max = 100;
      bar.value = percent;
      bar.setAttribute('aria-label', `${stage} progress`);
      const count = document.createElement('small');
      count.textContent = phaseProgress.message || `${current} / ${total}`;
      const percentage = document.createElement('small');
      percentage.textContent = `${percent}%`;
      progressDetail.append(bar, percentage, count);
    } else {
      const detail = document.createElement('small');
      if (phaseProgress?.message) {
        detail.textContent = phaseProgress.message;
      } else if (status === 'pending') {
        detail.textContent = 'Waiting for previous phase...';
      } else if (stage === 'assessment' && status === 'active') {
        detail.textContent = 'Generating final assessment...';
      } else if (stage === 'assessment' && status === 'complete') {
        detail.textContent = 'Final assessment generated ✓';
      } else if (status === 'active') {
        detail.textContent = 'Processing...';
      } else if (status === 'complete') {
        detail.textContent = 'Completed ✓';
      } else if (status === 'failed') {
        detail.textContent = 'Failed';
      } else if (status === 'skipped') {
        detail.textContent = 'Skipped';
      }
      progressDetail.append(detail);
    }
  }
}

function setPhaseExecution(stage, status) {
  state.phaseExecution[stage] = status;
  renderPipeline(state.preparedThermalRun || state.run || { status: 'idle', stage: 'uploaded' });
}

function selectPipelineStage(stage, scroll = true) {
  const phasePanel = ({
    uploaded: '#phase3-panel',
    detection: '#phase4-panel',
    tracking: '#phase5-panel',
    temporal: '#phase6-panel',
  })[stage];
  const resultsTab = ({
    reliability: 'fusion',
    evidence: 'evidence',
    vlm: 'vlm',
    fusion: 'fusion',
    assessment: 'fusion',
    completed: 'fusion',
  })[stage];
  state.selectedStage = stage;
  renderPipeline(state.preparedThermalRun || state.run || { status: 'idle', stage: 'uploaded' });
  document.querySelectorAll('.phase3-panel').forEach((panel) => {
    panel.hidden = panel.id !== phasePanel?.slice(1) || !state.phase3Input;
  });
  const hasPhasePanel = Boolean(phasePanel && state.phase3Input);
  $('#phase-detail-content').hidden = true;
  $('#phase-detail-track-picker').hidden = true;
  $('#phase-detail-empty').hidden = hasPhasePanel || !['reliability', 'evidence', 'vlm', 'fusion', 'assessment', 'completed'].includes(stage);
  if (hasPhasePanel) {
    $('#phase-detail-title').textContent = ({
      uploaded: 'Frame Acquisition',
      detection: 'Detection Details',
      tracking: 'Tracking Details',
      temporal: 'Temporal Details',
    })[stage];
    $('#phase-detail-message').textContent = ({
      uploaded: 'Acquire and validate the next input frame.',
      detection: 'Run the detector on the next prepared frame.',
      tracking: 'Track objects across the next prepared frame.',
      temporal: 'Review temporal history produced by tracking.',
    })[stage];
    $('#phase-detail-empty').textContent = '';
  } else {
    $('#phase-detail-title').textContent = ({
      uploaded: 'Input Details',
      detection: 'Detection Details',
      tracking: 'Tracking Details',
      temporal: 'Temporal Details',
      reliability: 'Reliability Results',
      evidence: 'Evidence Frames',
      vlm: 'VLM Analysis',
      fusion: 'Fusion Results',
      assessment: 'Assessment',
      completed: 'Run Completion',
    })[stage] || 'Selected Phase';
    $('#phase-detail-message').textContent = ({
      reliability: 'Review the available Phase 7 reliability scores for a selected track.',
      evidence: 'Review selected frames and crops from the existing evidence output.',
      vlm: 'Review verification outputs actually reported for a selected track.',
      fusion: 'Review the detector, reliability, and fusion output. Detector-only labels remain unverified.',
      assessment: 'Review final run-level counts and alert information.',
      completed: 'Review final decisions from the completed run report.',
    })[stage] || 'The video player remains available above while phase details change below the pipeline.';
    $('#phase-detail-empty').textContent = state.phase3Input
      ? 'This phase detail is available below the pipeline.'
      : 'Prepare an input or start a live source to make phase controls available.';
    $('#phase-detail-empty').hidden = false;
  }
  if (['reliability', 'evidence', 'vlm', 'fusion'].includes(stage)) {
    renderTrackPhaseDetail(stage);
  } else if (stage === 'assessment') {
    renderAssessmentPhaseDetail();
  } else if (stage === 'completed') {
    renderCompletedPhaseDetail();
  }
  if (resultsTab) selectResultsTab(resultsTab);
  const target = $('#phase-detail-area');
  if (scroll) target.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

const PHASE_EMPTY_MESSAGES = {
  reliability: 'Waiting for sufficient temporal observations.',
  evidence: 'Evidence selection is pending reliability analysis.',
  vlm: 'Automatic VLM verification is disabled. Detector-only class labels remain unverified.',
  fusion: 'Fusion is pending reliability and verification results.',
  assessment: 'Final assessment is pending fusion.',
  completed: 'Run completion is pending.',
};

function showPhaseDetailEmpty(stage, message = PHASE_EMPTY_MESSAGES[stage]) {
  $('#phase-detail-content').hidden = true;
  $('#phase-detail-track-picker').hidden = true;
  $('#phase-detail-empty').textContent = message || 'Phase results are unavailable.';
  $('#phase-detail-empty').hidden = false;
}

function phaseTrackOptions() {
  const select = $('#phase-detail-track-select');
  const availableIds = new Set(state.tracks.map((track) => String(track.track_id)));
  if (!availableIds.has(String(state.selectedPhaseTrackId))) {
    state.selectedPhaseTrackId = state.tracks[0]?.track_id ?? null;
  }
  select.replaceChildren();
  for (const track of state.tracks) {
    const option = new Option(
      `Track ${track.track_id} · ${track.class || 'Class unavailable'}`,
      String(track.track_id),
    );
    select.add(option);
  }
  if (state.selectedPhaseTrackId !== null) select.value = String(state.selectedPhaseTrackId);
  return state.selectedPhaseTrackId;
}

async function renderTrackPhaseDetail(stage) {
  const trackId = phaseTrackOptions();
  if (trackId === null || !state.runId || state.run?.status !== 'completed') {
    showPhaseDetailEmpty(stage);
    return;
  }
  $('#phase-detail-track-picker').hidden = false;
  if (!state.phaseTrackDetail || String(state.phaseTrackDetail.track_id) !== String(trackId)) {
    const requestId = ++state.phaseDetailRequest;
    $('#phase-detail-content').hidden = true;
    $('#phase-detail-empty').textContent = 'Loading run-scoped phase results…';
    $('#phase-detail-empty').hidden = false;
    try {
      const detail = await api(
        `/api/runs/${encodeURIComponent(state.runId)}/tracks/${encodeURIComponent(trackId)}`,
      );
      if (requestId !== state.phaseDetailRequest
        || String(state.selectedPhaseTrackId) !== String(trackId)
        || state.selectedStage !== stage) return;
      state.phaseTrackDetail = detail;
    } catch (error) {
      if (requestId !== state.phaseDetailRequest) return;
      showPhaseDetailEmpty(stage, `Could not load ${stage} details: ${error.message}`);
      return;
    }
  }
  if (state.selectedStage !== stage) return;
  state.selectedTrailObservations = Array.isArray(state.phaseTrackDetail.detector_observations)
    ? state.phaseTrackDetail.detector_observations
    : [];
  updateTrajectoryOverlayToggle();
  renderTrackPhaseContent(stage, state.phaseTrackDetail);
}

function renderSelectedPhaseDetail() {
  const stage = state.selectedStage;
  if (['reliability', 'evidence', 'vlm', 'fusion'].includes(stage)) {
    renderTrackPhaseDetail(stage);
  } else if (stage === 'assessment') {
    renderAssessmentPhaseDetail();
  } else if (stage === 'completed') {
    renderCompletedPhaseDetail();
  }
}

function phaseScore(value) {
  return value === null || value === undefined ? 'N/A' : formatNumber(value, 3);
}

function renderTrackPhaseContent(stage, detail) {
  if (
    detail.analysis_mode === 'thermal_detection_tracking_only'
    && ['reliability', 'evidence', 'vlm', 'fusion', 'assessment'].includes(stage)
  ) {
    showPhaseDetailEmpty(stage, `${stage[0].toUpperCase()}${stage.slice(1)} was not run for thermal inputs.`);
    return;
  }
  const reliability = detail.reliability || {};
  const fusion = detail.fusion || {};
  const vlm = detail.vlm || fusion.vlm || {};
  const evidence = Array.isArray(detail.selected_evidence) ? detail.selected_evidence : [];
  const vlmResults = vlm.result
    ? [vlm.result]
    : Array.isArray(vlm.results)
      ? vlm.results
      : [];
  let content = '';

  if (stage === 'reliability') {
    content = `<section class="detail-block">
      <div class="detail-block-head"><h3>Reliability Analysis</h3><span class="status-tag">${escapeHTML(reliability.status || 'N/A')}</span></div>
      <div class="detail-grid">
        ${detailStat('Track ID', detail.track_id)}
        ${detailStat('Confidence Score', phaseScore(reliability.confidence_score))}
        ${detailStat('Consistency Score', phaseScore(reliability.consistency_score))}
        ${detailStat('Persistence Score', phaseScore(reliability.persistence_score))}
        ${detailStat('Motion Score', phaseScore(reliability.motion_score))}
        ${detailStat('Reliability Score', phaseScore(reliability.reliability_score))}
        ${detailStat('Reliability Status', reliability.status || 'N/A')}
        ${detailStat('Frames Seen', formatNumber(reliability.frames_seen))}
      </div>
      <p>${escapeHTML(reliability.interpretation || 'Reliability data supplied by the run-scoped Phase 7 output.')}</p>
    </section>`;
  } else if (stage === 'evidence') {
    if (!evidence.length) {
      showPhaseDetailEmpty(stage);
      return;
    }
    const groups = {
      'Early Segment': evidence.filter((item) => /early/i.test(item.selection_group || '')),
      'Middle Segment': evidence.filter((item) => /middle|mid/i.test(item.selection_group || '')),
      'Late Segment': evidence.filter((item) => /late/i.test(item.selection_group || '')),
    };
    const evidenceCards = evidence.map((item) => {
      let imageUrl = '';
      try {
        imageUrl = evidenceImageUrl(item.crop_path);
      } catch {
        imageUrl = '';
      }
      return `<article class="evidence-card">
        ${imageUrl
          ? `<img src="${escapeHTML(imageUrl)}" alt="Evidence for track ${escapeHTML(item.track_id)}, frame ${formatNumber(item.frame)}" loading="lazy">`
          : '<div class="image-unavailable">Evidence thumbnail unavailable</div>'}
        <div class="evidence-meta">
          <strong>Frame ${formatNumber(item.frame)}</strong>
          <span>${escapeHTML(item.selection_group || item.selection_stage || 'Selection reason unavailable')}</span>
          <span>Confidence: ${phaseScore(item.confidence)}</span>
          <span>Evidence reference: ${escapeHTML(item.crop_path || 'N/A')}</span>
        </div>
      </article>`;
    }).join('');
    content = `<section class="detail-block">
      <div class="detail-block-head"><h3>Evidence Selection</h3><span class="badge badge-validated">${evidence.length} selected</span></div>
      <div class="detail-grid">
        ${detailStat('Track ID', detail.track_id)}
        ${detailStat('Selected Evidence Frames', evidence.map((item) => formatNumber(item.frame)).join(', '))}
        ${detailStat('Selection Reason', [...new Set(evidence.map((item) => item.selection_group).filter(Boolean))].join(', ') || 'N/A')}
        ${Object.entries(groups).map(([label, items]) => detailStat(label, items.length ? items.map((item) => formatNumber(item.frame)).join(', ') : 'N/A')).join('')}
      </div>
      <div class="evidence-grid">${evidenceCards}</div>
    </section>`;
  } else if (stage === 'vlm') {
    if (!vlm.available || !vlmResults.length) {
      showPhaseDetailEmpty(stage);
      return;
    }
    content = `<section class="detail-block">
      <div class="detail-block-head"><h3>VLM Verification</h3><span class="badge badge-validated">Available</span></div>
      ${vlmResults.map((result) => `<div class="detail-block">
        <div class="detail-grid">
          ${detailStat('Track ID', result.track_id ?? detail.track_id)}
          ${detailStat('VLM Label', result.label || 'N/A')}
          ${detailStat('VLM Confidence', phaseScore(result.confidence))}
          ${detailStat('Evidence Frame', formatNumber(result.frame))}
          ${detailStat('Verification Status', 'Completed')}
        </div>
        <p>${escapeHTML(result.reason || 'No VLM reason was reported.')}</p>
      </div>`).join('')}
      <p>${escapeHTML(vlm.confidence_interpretation || 'VLM confidence is not a calibrated probability.')}</p>
    </section>`;
  } else {
    const vlmResult = vlmResults[0] || null;
    if (!fusion.final_label) {
      showPhaseDetailEmpty(stage);
      return;
    }
    content = `<section class="detail-block">
      <div class="detail-block-head"><h3>Evidence Fusion</h3><span class="badge badge-limited">${escapeHTML(String(fusion.fusion_mode || 'N/A').replaceAll('_', ' '))}</span></div>
      <div class="detail-grid">
        ${detailStat('Track ID', detail.track_id)}
        ${detailStat('Detector Class', fusion.detector_evidence?.class || detail.class || 'N/A')}
        ${detailStat('Detector Score', phaseScore(fusion.detector_evidence?.mean_confidence))}
        ${detailStat('Reliability Score', phaseScore(fusion.reliability?.score))}
        ${detailStat('VLM Label', vlmResult?.label || 'N/A')}
        ${detailStat('VLM Confidence', phaseScore(vlmResult?.confidence))}
        ${detailStat('Fusion Mode', String(fusion.fusion_mode || 'N/A').replaceAll('_', ' '))}
        ${detailStat('Final Label', fusion.final_label)}
        ${detailStat('Decision Confidence', phaseScore(fusion.final_confidence))}
      </div>
      <p>${escapeHTML(fusion.confidence_interpretation || 'Decision confidence is not a calibrated probability.')}</p>
    </section>`;
  }

  $('#phase-detail-empty').hidden = true;
  $('#phase-detail-content').innerHTML = content;
  $('#phase-detail-content').hidden = false;
}

function renderAssessmentPhaseDetail() {
  if (state.run?.input_type === 'thermal') {
    showPhaseDetailEmpty('assessment', 'Decision assessment is not implemented for thermal input. Drone-class tracks still produce detector-only review alerts.');
    return;
  }
  if (state.run?.status !== 'completed' || !state.overview || !state.fusionSummary) {
    showPhaseDetailEmpty('assessment');
    return;
  }
  const counts = state.fusionSummary.final_label_counts || {};
  const reliabilityScores = state.tracks
    .map((track) => Number(track.reliability_score))
    .filter(Number.isFinite);
  const averageReliability = reliabilityScores.length
    ? formatNumber(reliabilityScores.reduce((total, score) => total + score, 0) / reliabilityScores.length, 3)
    : 'N/A';
  const knownLabels = new Set(['UAV', 'Drone', 'Bird', 'Uncertain', 'UNCERTAIN']);
  const otherCount = Object.entries(counts)
    .filter(([label]) => !knownLabels.has(label))
    .reduce((total, [, count]) => total + Number(count || 0), 0);
  const alertSummary = state.run?.input_type === 'thermal'
    ? 'Not run for thermal'
    : state.report?.alert?.message || (state.report ? 'No alert reported.' : 'N/A');
  $('#phase-detail-empty').hidden = true;
  $('#phase-detail-content').innerHTML = `<section class="detail-block">
    <div class="detail-block-head"><h3>Final Assessment</h3><span class="status-tag">${escapeHTML(state.run.status.toUpperCase())}</span></div>
    <div class="detail-grid">
      ${detailStat('Track IDs', formatNumber(state.overview.total_tracks))}
      ${detailStat('UAV / Drone', formatNumber(counts.UAV ?? counts.Drone))}
      ${detailStat('Bird', formatNumber(counts.Bird))}
      ${detailStat('Other', formatNumber(otherCount))}
      ${detailStat('Uncertain', formatNumber(state.overview.uncertain_tracks))}
      ${detailStat('Average Reliability', averageReliability)}
      ${detailStat('Final Status', state.run.status.toUpperCase())}
      ${detailStat('Alert Summary', alertSummary)}
    </div>
  </section>`;
  $('#phase-detail-content').hidden = false;
}

function renderCompletedPhaseDetail() {
  if (state.run?.status !== 'completed') {
    showPhaseDetailEmpty('completed');
    return;
  }
  const finalTracks = Array.isArray(state.report?.tracks) ? state.report.tracks : null;
  if (!state.report || !finalTracks) {
    showPhaseDetailEmpty('completed', 'Run completed, but final_results.json data is unavailable from the existing report endpoint.');
    return;
  }
  const decisions = {};
  for (const track of finalTracks) {
    const label = track.final_label || 'N/A';
    decisions[label] = (decisions[label] || 0) + 1;
  }
  const alertSummary = state.report.alert_status === 'not_run'
    ? 'Not run for thermal'
    : state.report.alert?.message || (state.report.uav_alert ? 'Alert reported.' : 'No alert reported.');
  $('#phase-detail-empty').hidden = true;
  $('#phase-detail-content').innerHTML = `<section class="detail-block">
    <div class="detail-block-head"><h3>Completed Run Summary</h3><span class="status-tag status-confident">Completed</span></div>
    <div class="detail-grid">
      ${detailStat('Source Video ID', state.run.source_video_id || 'Video ID unavailable')}
      ${detailStat('Run ID', state.runId)}
      ${detailStat('Processing Status', state.run.status.toUpperCase())}
      ${detailStat('Track IDs', formatNumber(state.report.track_count))}
      ${detailStat('Model outputs · detector-only labels unverified', Object.entries(decisions).map(([label, count]) => `${label}: ${formatNumber(count)}`).join(' · ') || 'N/A')}
      ${detailStat('Alerts', alertSummary)}
      ${detailStat('Report Availability', 'Available')}
    </div>
  </section>`;
  $('#phase-detail-content').hidden = false;
}

async function updateRunStatus(runId) {
  if (runId !== state.runId) return null;
  const run = await api(`/api/runs/${encodeURIComponent(runId)}`);
  if (runId !== state.runId) return null;
  state.run = run;
  if (!state.selectedFile) {
    state.inputMetadata = run.input_metadata || {};
    state.inputType = run.input_type || null;
    renderVideoMetadata(state.inputMetadata);
  }
  void refreshStoredFrameObservations();
  $('#run-section').hidden = false;
  $('#dashboard-run-status').textContent = run.status.toUpperCase();
  $('#generate-report').disabled = run.status !== 'completed';
  const stopButton = $('#stop-analysis');
  stopButton.hidden = run.status !== 'processing';
  stopButton.disabled = state.cancelPending;
  stopButton.textContent = state.cancelPending ? 'Stopping…' : 'Stop analysis';
  if (run.status === 'prepared') {
    state.run = run;
    renderPipeline(run);
    return run;
  }
  $('#run-id').textContent = run.run_id;
  $('#run-stage').textContent = readableStage(run.stage);
  $('#run-status-text').textContent = run.status.toUpperCase();
  $('#run-progress-text').textContent = `${formatNumber(run.progress)}%`;
  $('#run-progress').value = Math.max(0, Math.min(100, Number(run.progress) || 0));
  const badge = $('#run-status-badge');
  badge.textContent = run.status === 'processing'
    ? 'Processing'
    : run.status === 'completed'
      ? 'Completed'
      : run.status === 'failed'
        ? 'Failed'
        : run.status === 'cancelled'
          ? 'Cancelled'
          : 'Queued';
  badge.className = `status-badge status-${run.status}`;
  $('#new-analysis').disabled = state.busy || ['queued', 'processing'].includes(run.status);
  renderPipeline(run);
  return run;
}

async function pollRun(runId) {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  try {
    const run = await updateRunStatus(runId);
    if (!run) return;
    if (state.cancelPending) return;
    if (run.status === 'processing' && run.input_type !== 'thermal') await pollEarlyUavAlert(runId);
    if (run.status === 'completed') {
      await loadResults(runId);
      return;
    }
    if (run.status === 'failed' || run.status === 'cancelled') {
      $('#run-error').textContent = run.error || `The run ${run.status}. No results were loaded.`;
      $('#run-error').hidden = false;
      return;
    }
    if (!state.cancelPending) {
      state.pollTimer = setTimeout(() => pollRun(runId), 1200);
    }
  } catch (error) {
    if (!state.authenticated || state.cancelPending) return;
    $('#run-error').textContent = `Run status could not be retrieved: ${error.message}. Retrying shortly.`;
    $('#run-error').hidden = false;
    state.pollTimer = setTimeout(() => pollRun(runId), 3000);
  }
}

async function stopAnalysis() {
  const runId = state.runId;
  if (!runId || state.run?.status !== 'processing' || state.cancelPending) return;
  state.cancelPending = true;
  const button = $('#stop-analysis');
  button.disabled = true;
  button.textContent = 'Stopping…';
  $('#run-status-text').textContent = 'STOPPING…';
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  try {
    const run = await api(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: 'POST',
      body: '',
    });
    if (runId !== state.runId) return;
    state.run = run;
    await updateRunStatus(runId);
    $('#run-error').textContent = 'Analysis Cancelled. Choose New analysis to prepare another video.';
    $('#run-error').hidden = false;
  } catch (error) {
    if (runId === state.runId) {
      $('#run-error').textContent = `Analysis could not be stopped: ${error.message}`;
      $('#run-error').hidden = false;
    }
  } finally {
    state.cancelPending = false;
    if (runId === state.runId && state.run?.status === 'processing') {
      button.disabled = false;
      button.textContent = 'Stop analysis';
      void pollRun(runId);
    }
  }
}

function alertTimingSummary(alert) {
  const parts = [];
  const fps = Number(state.run?.input_metadata?.fps || state.inputMetadata?.fps || state.run?.source_video_fps);
  const frameTime = (frame) => Number.isSafeInteger(frame) && frame > 0 && Number.isFinite(fps) && fps > 0
    ? formatVideoTimestamp((frame - 1) / fps)
    : null;
  const firstFrame = Number(alert?.first_detection_frame);
  const publishedFrame = Number(alert?.published_frame);
  if (Number.isSafeInteger(firstFrame) && firstFrame > 0) {
    const time = frameTime(firstFrame);
    parts.push(`First detection: frame ${firstFrame}${time ? ` (${time})` : ''}`);
  }
  if (Number.isSafeInteger(publishedFrame) && publishedFrame > 0) {
    const time = frameTime(publishedFrame);
    parts.push(`Alert trigger: frame ${publishedFrame}${time ? ` (${time})` : ''}`);
  }
  const created = alert?.timestamp || alert?.created_at;
  if (created && Number.isFinite(Date.parse(created))) {
    parts.push(`Alert created: ${new Date(created).toLocaleTimeString()}`);
  }
  return parts.join(' · ');
}
async function pollEarlyUavAlert(runId) {
  const result = await api(`/api/runs/${encodeURIComponent(runId)}/early-alert`);
  if (runId === state.runId && result.early_alert) {
    if (result.early_alert.uav_alert === true && result.early_alert.state === 'provisional') {
      renderAlerts({
        run_id: runId,
        alert: result.alert,
        alert_track_ids: result.alert_track_ids,
        alert_status: result.alert_status,
      });
    } else if (['not_confirmed', 'interrupted'].includes(result.early_alert.state)) {
      renderAlerts({ run_id: runId, alert: null, alert_status: result.early_alert.state });
    }
  }
}

function populateClassFilter() {
  const select = $('#class-filter');
  const selected = select.value;
  select.replaceChildren(new Option('All classes', ''));
  const classes = [...new Set(state.tracks.map((track) => track.class).filter(Boolean))].sort();
  for (const className of classes) select.add(new Option(className, className));
  if (classes.includes(selected)) select.value = selected;
}

async function loadResults(runId) {
  if (runId !== state.runId) return;
  const requestId = ++state.resultsRequest;
  $('#results-section').hidden = true;
  try {
    const prefix = `/api/runs/${encodeURIComponent(runId)}`;
    const [overview, trackPayload, fusionSummary, reportResult] = await Promise.all([
      api(`${prefix}/overview`),
      api(`${prefix}/tracks`),
      api(`${prefix}/fusion/summary`),
      api(`${prefix}/report`).then((report) => ({ report }), (error) => ({ error })),
    ]);
    if (requestId !== state.resultsRequest || runId !== state.runId) return;
    if (state.trajectoryDetailsRunId !== runId) {
      state.trajectoryDetails.clear();
      state.trajectoryDetailsRunId = runId;
    }
    state.overview = overview;
    state.tracks = trackPayload.tracks || [];
    state.selectedPhaseTrackId = state.tracks[0]?.track_id ?? null;
    state.selectedTrajectoryTrackId = state.selectedPhaseTrackId;
    state.phaseTrackDetail = null;
    state.fusionSummary = fusionSummary;
    state.report = reportResult.report || null;
    if (state.route?.page !== 'alerts') {
      renderAlerts(state.report, reportResult.error?.message);
    }
    state.page = 1;
    populateClassFilter();
    renderSummary();
    renderTracks();
    renderSelectedPhaseDetail();
    if (['dashboard', 'result-detail'].includes(state.route?.page)) {
      void renderDashboardTrajectory();
    }
    state.resultsLoadedRunId = runId;
    $('#results-section').hidden = false;
    if (state.tracks.length === 0) {
      showGlobalError('Analysis completed, but no tracks were detected in this video.');
    } else {
      clearGlobalError();
    }
  } catch (error) {
    showGlobalError(`The run completed, but its results could not be loaded: ${error.message}`);
  }
}

function renderSummary() {
  const thermalOnly = state.run?.input_type === 'thermal';
  const finalLabels = state.fusionSummary.final_label_counts || {};
  const reportTracks = state.report?.run_id === state.runId
    && Array.isArray(state.report.tracks)
    ? state.report.tracks
    : [];
  const consensusLabels = reportTracks.map((track) => track.temporal_consensus?.consensus_label);
  const hasThermalConsensus = thermalOnly
    && reportTracks.length === state.tracks.length
    && consensusLabels.every((label) => typeof label === 'string' && label.length > 0);
  const consensusCount = (label) => consensusLabels.filter(
    (value) => value.toUpperCase() === label,
  ).length;
  const thermalDroneCount = hasThermalConsensus
    ? consensusCount('DRONE') + consensusCount('UAV')
    : null;
  const thermalBirdCount = hasThermalConsensus ? consensusCount('BIRD') : null;
  $('#dashboard-total-objects').textContent = formatNumber(state.tracks.length);
  $('#dashboard-drones').textContent = formatNumber(thermalOnly
    ? thermalDroneCount
    : finalLabels.UAV || 0);
  $('#dashboard-birds').textContent = formatNumber(thermalOnly
    ? thermalBirdCount
    : finalLabels.Bird || 0);
  $('#dashboard-drones-label').textContent = thermalOnly ? 'Consensus Drone tracks' : 'Final UAV decisions';
  $('#dashboard-birds-label').textContent = thermalOnly ? 'Consensus Bird tracks' : 'Final Bird decisions';
  $('#dashboard-uncertain-label').textContent = thermalOnly
    ? 'Other / uncertain consensus'
    : 'Final other / uncertain decisions';
  $('#dashboard-uncertain').textContent = thermalOnly
    ? hasThermalConsensus
      ? formatNumber(state.tracks.length - thermalDroneCount - thermalBirdCount)
      : 'N/A'
    : formatNumber(finalLabels.Other || 0);
  const reliabilityScores = state.tracks
    .map((track) => Number(track.reliability_score))
    .filter((score) => Number.isFinite(score));
  $('#dashboard-reliability').textContent = reliabilityScores.length
    ? formatNumber(reliabilityScores.reduce((sum, score) => sum + score, 0) / reliabilityScores.length, 3)
    : 'N/A';
  $('#dashboard-run-status').textContent = state.run?.status?.toUpperCase() || 'N/A';
  $('#total-tracks').textContent = formatNumber(state.overview.total_tracks);
  $('#confident-tracks').textContent = thermalOnly ? 'N/A' : formatNumber(state.overview.confident_tracks);
  $('#uncertain-tracks').textContent = thermalOnly ? 'N/A' : formatNumber(state.overview.uncertain_tracks);
  $('#vlm-tracks').textContent = thermalOnly ? 'Not run' : formatNumber(state.overview.vlm_enabled_tracks);
  $('#results-run-id').textContent = `Run ${state.runId}`;
  $('#evidence-summary').textContent = thermalOnly ? 'Not run for thermal' : 'Loaded on track selection';
  const alertTrackCount = alertEvidenceByTrack(state.report).length;
  $('#alert-summary').textContent = state.report?.run_id !== state.runId
    ? 'Unavailable'
    : thermalOnly
      ? `${formatNumber(alertTrackCount)} detector-only review alert track(s)`
      : `${formatNumber(alertTrackCount)} review alert track(s)`;
  $('#thermal-run-note').hidden = !thermalOnly;
  const modes = $('#fusion-modes');
  modes.replaceChildren();
  const modeCounts = state.fusionSummary.fusion_mode_counts || {};
  if (!Object.keys(modeCounts).length) {
    const empty = document.createElement('span');
    empty.textContent = thermalOnly ? 'Fusion was not run for thermal inputs' : 'No fusion results';
    modes.append(empty);
  }
  for (const [mode, count] of Object.entries(modeCounts)) {
    const item = document.createElement('span');
    item.textContent = mode.replaceAll('_', ' ');
    const number = document.createElement('strong');
    number.textContent = formatNumber(count);
    item.append(number);
    modes.append(item);
  }
  const vlmAvailable = Number(state.overview.vlm_enabled_tracks) > 0;
  const badge = $('#run-vlm-badge');
  badge.textContent = thermalOnly ? 'Not run' : vlmAvailable ? 'Available' : 'Unavailable';
  badge.className = `badge ${vlmAvailable ? 'badge-validated' : 'badge-unavailable'}`;
  $('#run-vlm-summary').textContent = vlmAvailable
    ? `${formatNumber(state.overview.vlm_enabled_tracks)} track(s) have VLM outputs in this run.`
    : thermalOnly
      ? 'VLM is not implemented for thermal runs.'
      : 'VLM was not executed for this run; no semantic model result is implied.';
}

function alertEvidenceByTrack(report) {
  if (!report?.alert || typeof report.alert !== 'object') return [];
  const evidence = Array.isArray(report.alert.evidence) ? report.alert.evidence : [];
  const byTrack = new Map();
  for (const item of evidence) {
    if (item?.track_id !== null && item?.track_id !== undefined) {
      byTrack.set(String(item.track_id), item);
    }
  }
  const trackIds = Array.isArray(report.alert_track_ids) ? report.alert_track_ids : [];
  for (const trackId of [...trackIds, ...byTrack.keys()]) {
    if (trackId === null || trackId === undefined) continue;
    const key = String(trackId);
    if (!byTrack.has(key)) byTrack.set(key, { track_id: key, observation: null });
  }
  return [...byTrack.values()];
}

function renderAlerts(report, errorMessage = '') {
  const alertList = $('#active-alert-list');
  alertList.replaceChildren();
  state.alertRunId = report?.run_id || null;
  state.alertRows = report?.alert && typeof report.alert === 'object' ? [report.alert] : [];
  if (
    ['HIGH', 'REVIEW'].includes(state.alertRows[0]?.level?.toUpperCase())
    && state.alertRows[0]?.provisional !== true
  ) {
    playHighAlertOnce(report, state.alertRows[0]);
  }
  state.alertPage = 1;
  state.alertError = errorMessage;
  const dashboardAlertSummary = state.route?.page === 'dashboard';
  const evidenceItems = state.alertRows.flatMap((alert) =>
    Array.isArray(alert.evidence) ? alert.evidence : []);
  const uniqueEvidenceItems = alertEvidenceByTrack(report);
  const activeAlertCount = uniqueEvidenceItems.length;
  $('#active-alert-count').textContent = `(${activeAlertCount})`;
  const alertSignature = activeAlertCount
    ? `${state.alertRunId || 'run'}:${uniqueEvidenceItems.map((item) => item.track_id).sort().join('|')}`
    : '';
  if (alertSignature !== state.alertCueSignature) {
    state.alertCueSignature = alertSignature;
    const alertCard = $('#active-alerts');
    alertCard.classList.remove('alert-arrival-cue', 'alert-arrival-review');
    if (alertSignature) {
      void alertCard.offsetWidth;
      alertCard.classList.add('alert-arrival-cue');
      if (state.alertRows[0]?.level?.toUpperCase() === 'REVIEW') alertCard.classList.add('alert-arrival-review');
    }
  }
  if (!state.alertRows.length || (dashboardAlertSummary && !activeAlertCount)) {
    alertList.className = 'alert-empty';
    alertList.textContent = errorMessage
      ? `Alert data unavailable: ${errorMessage}`
      : report?.alert_status === 'not_run'
        ? 'Alerting was not run for this thermal analysis.'
      : report?.alert_status === 'not_confirmed'
        ? 'Preliminary candidate was not retained by final evidence fusion.'
      : report?.alert_status === 'interrupted'
        ? 'Run ended before evidence fusion; the preliminary candidate was not confirmed.'
      : dashboardAlertSummary
        ? 'No active alerts for this run.'
        : 'No active alerts';
    $('#notification-count').hidden = true;
  } else if (dashboardAlertSummary) {
    alertList.className = 'active-alert-list';
    for (const [index, entry] of uniqueEvidenceItems.entries()) {
      const card = document.createElement('article');
      card.className = 'active-alert-track';
      const track = document.createElement('strong');
      track.className = 'active-alert-track-id';
      track.textContent = `TRACK ID ${entry.track_id}`;
      const summary = document.createElement('span');
      summary.className = 'active-alert-track-summary';
      summary.textContent = `${entry.observation?.detector_class || 'Object'} · ${state.alertRows[0].detector_only ? 'Detector-only review' : state.alertRows[0].provisional ? 'Preliminary review' : (state.alertRows[0].level || 'Alert')}`;
      card.append(track, summary);
      const timingText = alertTimingSummary(state.alertRows[0]);
      if (timingText) {
        const timing = document.createElement('span');
        timing.className = 'active-alert-track-timing';
        timing.textContent = timingText;
        card.append(timing);
      }
      if (entry.observation && Number.isSafeInteger(entry.observation.frame_index)) {
        const viewButton = document.createElement('button');
        viewButton.type = 'button';
        viewButton.className = 'alert-evidence-button';
        viewButton.dataset.evidenceIndex = String(evidenceItems.indexOf(entry));
        viewButton.textContent = state.alertRows[0].provisional || state.alertRows[0].detector_only
          ? 'VIEW DETECTION FRAME'
          : 'VIEW EVIDENCE FRAME';
        card.append(viewButton);
      } else {
        const unavailable = document.createElement('span');
        unavailable.className = 'alert-evidence-unavailable';
        unavailable.textContent = 'Evidence unavailable';
        card.append(unavailable);
      }
      alertList.append(card);
    }
    $('#notification-count').textContent = formatNumber(activeAlertCount);
    $('#notification-count').hidden = false;
  } else {
    alertList.className = 'alert-content';
    for (const alert of state.alertRows) {
      const title = document.createElement('strong');
      title.textContent = alert.detector_only
        ? 'Detector-only review — unverified'
        : alert.provisional
          ? 'Preliminary review — possible UAV'
          : alert.level?.toUpperCase() === 'REVIEW'
            ? 'Review recommended — possible UAV'
            : `${alert.level || 'Alert'} priority alert`;
      const message = document.createElement('p');
      message.textContent = alert.message || 'Run report contains an alert.';
      alertList.append(title, message);
      const timingText = alertTimingSummary(alert);
      if (timingText) {
        const timing = document.createElement('p');
        timing.className = 'alert-timing-summary';
        timing.textContent = timingText;
        alertList.append(timing);
      }
      const evidenceItems = Array.isArray(alert.evidence) ? alert.evidence : [];
      if (!evidenceItems.length && alert.level?.toUpperCase() === 'HIGH') {
        const unavailable = document.createElement('p');
        unavailable.className = 'alert-evidence-unavailable';
        unavailable.textContent = 'Evidence unavailable';
        alertList.append(unavailable);
      }
      evidenceItems.forEach((item, index) => {
        const block = document.createElement('div');
        block.className = 'alert-evidence';
        const observation = item.observation;
        const track = document.createElement('strong');
        track.textContent = `Track ID ${item.track_id}`;
        block.append(track);
        if (!observation) {
          const unavailable = document.createElement('p');
          unavailable.textContent = 'Evidence unavailable';
          block.append(unavailable);
        } else {
          const totalFrames = observation.total_frames
            ? ` / ${formatNumber(observation.total_frames)}`
            : '';
          const timestamp = observation.timestamp === null
            ? 'N/A'
            : formatVideoTimestamp(observation.timestamp);
          const bbox = observation.bbox;
          const details = document.createElement('p');
          details.textContent = [
            `${alert.provisional || alert.detector_only ? 'Detection Frame' : 'Evidence Frame'} ${observation.frame_index}${totalFrames}`,
            `Timestamp ${timestamp}`,
            `Detector ${observation.detector_class || 'N/A'}`,
            `Confidence ${formatNumber(observation.detector_confidence, 3)}`,
            `Reliability ${observation.reliability_status || 'N/A'}`,
            `Score ${formatNumber(observation.reliability_score, 3)}`,
            `Bounding Box (${formatNumber(bbox.x1, 1)}, ${formatNumber(bbox.y1, 1)}, ${formatNumber(bbox.x2, 1)}, ${formatNumber(bbox.y2, 1)})`,
          ].join(' · ');
          block.append(details);
          if (Number.isSafeInteger(observation.frame_index) && observation.frame_index > 0) {
            const viewButton = document.createElement('button');
            viewButton.type = 'button';
            viewButton.className = 'alert-evidence-button';
            viewButton.dataset.evidenceIndex = String(index);
            viewButton.textContent = alert.provisional || alert.detector_only
              ? 'VIEW DETECTION FRAME'
              : 'VIEW EVIDENCE FRAME';
            block.append(viewButton);
          }
        }
        alertList.append(block);
      });
    }
    $('#notification-count').textContent = formatNumber(activeAlertCount);
    $('#notification-count').hidden = false;
  }
  renderAlertRows(report);
}

function populateAlertRunSelector() {
  const select = $('#alert-run-select');
  const runs = state.historyRuns.filter((run) => run.status === 'completed');
  select.replaceChildren();
  if (!runs.length) {
    select.add(new Option('No completed runs', ''));
    select.disabled = true;
    state.alertRunId = null;
    $('#alerts-run-label').textContent = 'No completed runs are available for alert review.';
    renderAlerts(null);
    return;
  }
  select.disabled = false;
  for (const run of runs) {
    const option = new Option(
      `${run.source_video_id || 'Video ID unavailable'} · ${run.run_id}`,
      run.run_id,
    );
    select.add(option);
  }
  const selectedRunId = runs.some((run) => run.run_id === state.runId)
    ? state.runId
    : runs[0].run_id;
  select.value = selectedRunId;
  void loadAlertsForRun(selectedRunId);
}

async function loadAlertsForRun(runId) {
  if (!runId) return;
  const requestId = ++state.alertRequest;
  $('#alerts-run-label').textContent = `Loading alerts for run ${runId}…`;
  try {
    const report = await api(`/api/runs/${encodeURIComponent(runId)}/report`);
    if (requestId !== state.alertRequest || state.route?.page !== 'alerts') return;
    if (report.run_id !== runId) throw new Error('The returned report belongs to a different run.');
    renderAlerts(report);
    $('#alerts-run-label').textContent =
      `Source Video ${state.historyRuns.find((run) => run.run_id === runId)?.source_video_id || 'ID unavailable'} · Alerts for run ${runId}`;
  } catch (error) {
    if (requestId !== state.alertRequest || state.route?.page !== 'alerts') return;
    renderAlerts(null, error.message);
    const videoId = state.historyRuns.find((run) => run.run_id === runId)?.source_video_id;
    $('#alerts-run-label').textContent =
      `Source Video ${videoId || 'ID unavailable'} · Alerts unavailable for run ${runId}: ${error.message}`;
  }
}

function playHighAlertOnce(report, alert) {
  const runId = report?.run_id || state.runId;
  if (!runId) return;
  const alertKey = JSON.stringify([runId, alert.level, alert.message || '']);
  const inFlight = state.alertSoundInFlight || (state.alertSoundInFlight = new Set());
  if (
    state.playedHighAlerts.has(alertKey)
    || inFlight.has(alertKey)
    || (state.pendingAlertSound?.key === alertKey && state.alertSoundUnlockAttached)
  ) return;

  let storedAlerts = [];
  try {
    storedAlerts = JSON.parse(localStorage.getItem(PLAYED_HIGH_ALERTS_STORAGE_KEY) || '[]');
  } catch (error) {
    console.warn('Could not read alert sound history:', error);
  }
  if (Array.isArray(storedAlerts)) {
    for (const storedAlert of storedAlerts) {
      if (typeof storedAlert === 'string') state.playedHighAlerts.add(storedAlert);
    }
  }
  if (Array.isArray(storedAlerts) && storedAlerts.includes(alertKey)) {
    state.playedHighAlerts.add(alertKey);
    return;
  }

  const markPlayed = () => {
    inFlight.delete(alertKey);
    state.playedHighAlerts.add(alertKey);
    try {
      localStorage.setItem(
        PLAYED_HIGH_ALERTS_STORAGE_KEY,
        JSON.stringify([...state.playedHighAlerts]),
      );
    } catch (error) {
      console.warn('Could not save alert sound history:', error);
    }
  };
  const retryFromUserGesture = () => {
    state.alertSoundUnlockAttached = false;
    const pending = state.pendingAlertSound;
    if (!pending) return;
    state.pendingAlertSound = null;
    playHighAlertOnce(pending.report, pending.alert);
  };
  const queueGestureRetry = () => {
    state.pendingAlertSound = { key: alertKey, report, alert };
    if (state.alertSoundUnlockAttached || typeof document.addEventListener !== 'function') return;
    state.alertSoundUnlockAttached = true;
    document.addEventListener('pointerdown', retryFromUserGesture, { once: true });
    document.addEventListener('keydown', retryFromUserGesture, { once: true });
  };

  try {
    inFlight.add(alertKey);
    const playback = new Audio('/assets/high-alert.wav').play();
    if (playback && typeof playback.then === 'function') {
      playback.then(markPlayed).catch((error) => {
        inFlight.delete(alertKey);
        console.warn('Alert sound playback was blocked:', error);
        queueGestureRetry();
      });
    } else {
      markPlayed();
    }
  } catch (error) {
    inFlight.delete(alertKey);
    console.warn('Alert sound playback could not start:', error);
    queueGestureRetry();
  }
}

function renderAlertRows(report) {
  const rows = $('#recent-alert-rows');
  rows.replaceChildren();
  const pageCount = Math.max(1, Math.ceil(state.alertRows.length / state.pageSize));
  state.alertPage = Math.min(state.alertPage, pageCount);
  const firstIndex = state.alertRows.length ? (state.alertPage - 1) * state.pageSize : 0;
  const visible = state.alertRows.slice(firstIndex, firstIndex + state.pageSize);
  if (!state.alertRows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 3;
    cell.className = 'alert-empty';
    cell.textContent = state.alertError ? 'Alert data unavailable' : 'No alert data';
    row.append(cell);
    rows.append(row);
    $('#alert-pagination-summary').textContent = 'No alerts';
  } else {
    for (const alert of visible) {
      const row = document.createElement('tr');
      const timestamp = document.createElement('td');
      const event = document.createElement('td');
      const severity = document.createElement('td');
      const timestampValue = alert.timestamp || alert.created_at || report?.completed_at;
      const date = timestampValue ? new Date(timestampValue) : null;
      timestamp.textContent = date && Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : 'N/A';
      event.textContent = alert.message || 'Run alert';
      severity.textContent = alert.level || 'N/A';
      row.append(timestamp, event, severity);
      rows.append(row);
    }
    $('#alert-pagination-summary').textContent = `Showing ${firstIndex + 1}–${Math.min(firstIndex + state.pageSize, state.alertRows.length)} of ${state.alertRows.length}`;
  }
  renderPageNavigation($('#alert-pagination'), state.alertPage, pageCount, (page) => {
    state.alertPage = Math.max(1, Math.min(pageCount, page));
    renderAlertRows(report);
  });
}

async function generateReport() {
  if (!state.runId || state.run?.status !== 'completed') return;
  const button = $('#generate-report');
  button.disabled = true;
  try {
    const report = await api(`/api/runs/${encodeURIComponent(state.runId)}/report`);
    state.report = report;
    renderAlerts(report);
    const file = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${state.runId}-report.json`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    showGlobalError(`Could not generate the run report: ${error.message}`);
  } finally {
    button.disabled = state.run?.status !== 'completed';
  }
}

async function downloadStoredReport(runId, videoId) {
  try {
    const report = await api(`/api/runs/${encodeURIComponent(runId)}/report`);
    const file = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(file);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${videoId || runId}-report.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    showGlobalError(`Could not download the stored report: ${error.message}`);
  }
}

async function deleteVideoAnalysisResults(videoId) {
  if (!videoId) {
    showGlobalError('This video has no stored video ID, so its results cannot be safely grouped for deletion.');
    return;
  }
  const matchingRuns = state.historyRuns.filter((run) => run.source_video_id === videoId);
  if (!matchingRuns.length) return;
  const confirmed = window.confirm(
    `Delete all ${matchingRuns.length} stored analysis ${matchingRuns.length === 1 ? 'result' : 'results'} for ${videoId}? This also removes its uploaded video and reports and cannot be undone.`,
  );
  if (!confirmed) return;
  const button = [...$('#results-run-cards').querySelectorAll('[data-delete-video-id]')]
    .find((item) => item.dataset.deleteVideoId === videoId);
  if (button) button.disabled = true;
  try {
    await api(`/api/videos/${encodeURIComponent(videoId)}/delete`, { method: 'POST' });
    if (matchingRuns.some((run) => run.run_id === state.runId)) {
      state.routeRunId = null;
      state.runId = null;
      localStorage.removeItem(RUN_STORAGE_KEY);
      clearRunResults();
      resetUpload();
      await navigateTo('/results');
    } else {
      await loadHistory(false);
    }
  } catch (error) {
    if (button) button.disabled = false;
    showGlobalError(`Could not delete stored results for ${videoId}: ${error.message}`);
  }
}

function selectResultsTab(tab) {
  state.activeResultTab = tab;
  document.querySelectorAll('[data-results-tab]').forEach((button) => {
    const selected = button.dataset.resultsTab === tab;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', String(selected));
  });
  const title = ({
    detection: 'Detection Results',
    tracking: 'Tracking Results',
    evidence: 'Evidence Frames',
    vlm: 'VLM Analysis',
    fusion: 'Fusion & Assessment',
  })[tab];
  $('#tracks-title').childNodes[0].textContent = title + ' ';
  renderTracks();
}

function filteredTracks() {
  const query = $('#track-search').value.trim().toLowerCase();
  const status = $('#status-filter').value;
  const className = $('#class-filter').value;
  return state.tracks.filter((track) => {
    const matchesId = !query || String(track.track_id).toLowerCase().includes(query);
    return matchesId
      && (!status || track.reliability_status === status)
      && (!className || track.class === className);
  });
}

function renderTracks() {
  const matches = filteredTracks();
  const pageCount = Math.max(1, Math.ceil(matches.length / state.pageSize));
  state.page = Math.min(state.page, pageCount);
  const firstIndex = matches.length ? (state.page - 1) * state.pageSize : 0;
  const visible = matches.slice(firstIndex, firstIndex + state.pageSize);
  $('#track-count-label').textContent = formatNumber(matches.length);
  $('#no-tracks').hidden = matches.length > 0;
  const tbody = $('#track-rows');
  tbody.replaceChildren();
  const thermalOnly = state.run?.input_type === 'thermal';
  const columns = thermalOnly
    ? {
      detection: [
        ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
        ['PREDICTED CLASS', (track) => track.class || 'N/A'],
        ['OBSERVATIONS', (track) => formatNumber(track.frames_seen)],
        ['MEAN DETECTOR CONFIDENCE', (track) => formatNumber(track.mean_detector_confidence, 2)],
        ['FRAME RANGE', (track) => `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`],
      ],
      tracking: [
        ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
        ['PREDICTED CLASS', (track) => track.class || 'N/A'],
        ['OBSERVATIONS', (track) => formatNumber(track.frames_seen)],
        ['FRAME RANGE', (track) => `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`],
        ['DETECTOR CONFIDENCE', (track) => formatNumber(track.mean_detector_confidence, 2)],
      ],
      evidence: [
        ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
        ['PREDICTED CLASS', (track) => track.class || 'N/A'],
        ['EVIDENCE SELECTION', () => 'Not run for thermal'],
      ],
      vlm: [
        ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
        ['PREDICTED CLASS', (track) => track.class || 'N/A'],
        ['VLM', () => 'Not run for thermal'],
      ],
      fusion: [
        ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
        ['PREDICTED CLASS', (track) => track.class || 'N/A'],
        ['FUSION / ASSESSMENT', () => 'Not run for thermal'],
      ],
    }[state.activeResultTab]
    : {
    detection: [
      ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
      ['CLASS', (track) => track.class || 'N/A'],
      ['CONFIDENCE', (track) => formatNumber(track.mean_detector_confidence, 2)],
      ['BOUNDING BOX', () => 'N/A'],
      ['FRAME', (track) => `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`],
      ['TIME (S)', () => 'N/A'],
      ['RELIABILITY', (track) => formatNumber(track.reliability_score, 3)],
      ['STATUS', (track) => track.reliability_status || 'N/A'],
    ],
    tracking: [
      ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
      ['CLASS', (track) => track.class || 'N/A'],
      ['OBSERVATIONS', (track) => formatNumber(track.frames_seen)],
      ['FRAME RANGE', (track) => `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`],
      ['MOTION STATE', (track) => track.motion_state || 'N/A'],
      ['SPEED (PX/S)', (track) => formatNumber(track.mean_speed_px_per_second, 2)],
      ['RELIABILITY', (track) => formatNumber(track.reliability_score, 3)],
      ['STATUS', (track) => track.reliability_status || 'N/A'],
    ],
    evidence: [
      ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
      ['CLASS', (track) => track.class || 'N/A'],
      ['FRAME RANGE', (track) => `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`],
      ['EVIDENCE', () => 'Open track detail'],
    ],
    vlm: [
      ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
      ['CLASS', (track) => track.class || 'N/A'],
      ['VLM OUTPUT', (track) => track.fusion_mode === 'detector_vlm' ? 'Available' : 'Unavailable'],
      ['FUSION MODE', (track) => (track.fusion_mode || 'N/A').replaceAll('_', ' ')],
    ],
    fusion: [
      ['TRACK ID', (track) => `TRK-${String(track.track_id).padStart(3, '0')}`],
      ['CLASS', (track) => track.class || 'N/A'],
      ['FINAL LABEL', (track) => track.final_label || 'N/A'],
      ['FINAL SCORE', (track) => formatNumber(track.final_confidence, 3)],
      ['FUSION MODE', (track) => (track.fusion_mode || 'N/A').replaceAll('_', ' ')],
      ['RELIABILITY', (track) => track.reliability_status || 'N/A'],
    ],
    }[state.activeResultTab];
  $('#track-table-head').innerHTML = `<tr>${columns.map(([heading]) => `<th>${heading}</th>`).join('')}<th>ACTIONS</th></tr>`;
  for (const track of visible) {
    const row = document.createElement('tr');
    for (const [heading, valueForTrack] of columns) {
      const cell = document.createElement('td');
      const value = valueForTrack(track);
      if (heading === 'TRACK ID') {
        const link = document.createElement('button');
        link.type = 'button';
        link.className = 'track-link';
        link.dataset.trackId = track.track_id;
        link.textContent = value;
        cell.append(link);
      } else if (heading === 'STATUS' || heading === 'RELIABILITY') {
        const badge = document.createElement('span');
        badge.className = `status-tag ${track.reliability_status === 'CONFIDENT' ? 'status-confident' : 'status-uncertain'}`;
        badge.textContent = value;
        cell.append(badge);
      } else {
        cell.textContent = value;
      }
      row.append(cell);
    }
    const actions = document.createElement('td');
    const inspect = document.createElement('button');
    inspect.type = 'button';
    inspect.className = 'track-link';
    inspect.dataset.trackId = track.track_id;
    inspect.textContent = 'Inspect';
    actions.append(inspect);
    row.append(actions);
    tbody.append(row);
  }
  $('#pagination-summary').textContent = matches.length
    ? `Showing ${firstIndex + 1}–${Math.min(firstIndex + state.pageSize, matches.length)} of ${matches.length}`
    : 'No tracks';
  renderPageNavigation($('#pagination'), state.page, pageCount, changePage);
}

function renderPageNavigation(nav, currentPage, pageCount, onPageChange) {
  nav.replaceChildren();
  const previous = document.createElement('button');
  previous.type = 'button';
  previous.textContent = 'Previous';
  previous.disabled = currentPage <= 1;
  previous.addEventListener('click', () => onPageChange(currentPage - 1));
  nav.append(previous);

  let pages = [...Array(pageCount).keys()].map((index) => index + 1);
  if (pageCount > 7) {
    const start = Math.max(2, currentPage - 1);
    const end = Math.min(pageCount - 1, currentPage + 1);
    pages = [1, ...Array.from({ length: end - start + 1 }, (_, index) => start + index), pageCount];
    if (start > 2) pages.splice(1, 0, '…');
    if (end < pageCount - 1) pages.splice(pages.length - 1, 0, '…');
  }
  for (const page of pages) {
    if (page === '…') {
      const ellipsis = document.createElement('span');
      ellipsis.textContent = '…';
      ellipsis.className = 'pagination-ellipsis';
      nav.append(ellipsis);
      continue;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = String(page);
    button.className = page === currentPage ? 'current-page' : '';
    button.setAttribute('aria-current', page === currentPage ? 'page' : 'false');
    button.addEventListener('click', () => onPageChange(page));
    nav.append(button);
  }
  const next = document.createElement('button');
  next.type = 'button';
  next.textContent = 'Next';
  next.disabled = currentPage >= pageCount;
  next.addEventListener('click', () => onPageChange(currentPage + 1));
  nav.append(next);
}

function renderHistory() {
  const pageCount = Math.max(1, Math.ceil(state.historyRuns.length / state.pageSize));
  state.historyPage = Math.min(state.historyPage, pageCount);
  const firstIndex = state.historyRuns.length ? (state.historyPage - 1) * state.pageSize : 0;
  const visible = state.historyRuns.slice(firstIndex, firstIndex + state.pageSize);
  const rows = $('#history-rows');
  rows.replaceChildren();
  $('#history-count').textContent = formatNumber(state.historyRuns.length);
  if (!state.historyLoaded || !state.historyRuns.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.className = 'empty-state';
    cell.textContent = state.historyLoaded ? 'No runs are available for this account.' : 'Run history has not been loaded.';
    row.append(cell);
    rows.append(row);
    $('#history-pagination-summary').textContent = state.historyLoaded ? 'No history entries' : 'History not loaded';
  } else {
    for (const run of visible) {
      const row = document.createElement('tr');
      const actualTiming = actualAnalysisDuration(run);
      for (const value of [
        run.source_video_id || 'Unavailable',
        run.run_id || 'N/A',
        run.input_type ? String(run.input_type).toUpperCase() : 'N/A',
        run.status ? String(run.status).toUpperCase() : 'N/A',
        readableStage(run.stage),
        run.created_at && Number.isFinite(new Date(run.created_at).getTime())
          ? new Date(run.created_at).toLocaleString()
          : 'N/A',
            actualTiming
              ? formatDuration(actualTiming.seconds)
              : run.started_at ? 'Timing unavailable' : 'Not started',
      ]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      }
      const actions = document.createElement('td');
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'track-link';
      open.dataset.historyRunId = run.run_id;
      open.dataset.historyAction = 'open';
      open.textContent = 'Open run';
      actions.append(open);
      if (run.source_type === 'upload' && run.input_type === 'rgb') {
        const fresh = document.createElement('button');
        fresh.type = 'button';
        fresh.className = 'track-link';
        fresh.dataset.historyRunId = run.run_id;
        fresh.dataset.historyAction = 'reanalyze';
        fresh.textContent = 'Fresh analysis';
        actions.append(document.createTextNode(' · '), fresh);
      }
      if (['failed', 'cancelled'].includes(run.status)) {
        const resume = document.createElement('button');
        resume.type = 'button';
        resume.className = 'track-link';
        resume.dataset.historyRunId = run.run_id;
        resume.dataset.historyAction = 'resume';
        resume.textContent = 'Check resume';
        actions.append(document.createTextNode(' · '), resume);
      }
      row.append(actions);
      rows.append(row);
    }
    $('#history-pagination-summary').textContent = `Showing ${firstIndex + 1}–${Math.min(firstIndex + state.pageSize, state.historyRuns.length)} of ${state.historyRuns.length}`;
  }
  renderPageNavigation($('#history-pagination'), state.historyPage, pageCount, (page) => {
    state.historyPage = Math.max(1, Math.min(pageCount, page));
    renderHistory();
  });
}

function renderSessionHistory() {
  const rows = $('#session-history-rows');
  rows.replaceChildren();
  if (!state.sessionHistory.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.className = 'empty-state';
    cell.textContent = 'No login sessions are recorded for this account.';
    row.append(cell);
    rows.append(row);
    return;
  }
  for (const session of state.sessionHistory) {
    const row = document.createElement('tr');
    const values = [
      session.session_id || 'N/A',
      session.logged_in_at ? new Date(session.logged_in_at).toLocaleString() : 'N/A',
      session.logged_out_at ? new Date(session.logged_out_at).toLocaleString() : 'Active',
      formatDuration(session.duration_seconds),
      session.active ? 'Active' : 'Ended',
    ];
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = value;
      if (session.active && value === 'Active') cell.className = 'session-active';
      row.append(cell);
    }
    rows.append(row);
  }
}

function renderResultsRunCards() {
  const container = $('#results-run-cards');
  container.replaceChildren();
  if (state.route?.page !== 'results') {
    state.resultCardRequest += 1;
    return;
  }
  const dateFilter = $('#results-date-filter');
  dateFilter.hidden = !state.resultsDateFilter;
  dateFilter.textContent = state.resultsDateFilter
    ? `Showing analyses completed on ${state.resultsDateFilter}. `
    : '';
  if (state.resultsDateFilter) {
    const clear = document.createElement('a');
    clear.href = '/results';
    clear.dataset.routeLink = '/results';
    clear.textContent = 'Clear date filter';
    clear.addEventListener('click', (event) => {
      event.preventDefault();
      void navigateTo('/results');
    });
    dateFilter.append(clear);
  }
  const groups = new Map();
  const visibleRuns = state.resultsDateFilter
    ? state.historyRuns.filter((run) => String(run.completed_at || '').slice(0, 10) === state.resultsDateFilter)
    : state.historyRuns;
  for (const run of visibleRuns) {
    const key = run.source_video_id || `filename:${String(run.original_filename || run.input_filename || run.run_id).toLowerCase()}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }
  const videos = [...groups.values()].map((runs) => ({
    runs,
    latest: runs[0],
    completed: runs.find((run) => run.status === 'completed') || null,
    source: runs.find((run) => run.source_type === 'upload' && ['rgb', 'thermal'].includes(run.input_type)) || null,
  }));
  if (!videos.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = state.historyLoaded
      ? (state.resultsDateFilter
        ? `No stored analyses completed on ${state.resultsDateFilter}.`
        : 'No stored video analyses are available for this account.')
      : 'Load analysis history to view stored results.';
    container.append(empty);
    return;
  }
  const requestId = ++state.resultCardRequest;
  const requestRuns = [];
  for (const video of videos) {
    const { runs, latest, completed, source } = video;
    const run = completed || latest;
    if (completed) requestRuns.push(completed);
    const card = document.createElement('article');
    card.className = 'results-run-card';
    card.dataset.resultVideoId = run.source_video_id || '';
    const thumbnail = document.createElement('video');
    thumbnail.className = 'run-card-thumbnail';
    thumbnail.muted = true;
    thumbnail.playsInline = true;
    thumbnail.preload = 'metadata';
    thumbnail.setAttribute('aria-label', `Video preview for ${run.source_video_id || runFilename(run)}`);
    thumbnail.src = `/api/runs/${encodeURIComponent(run.run_id)}/video`;
    thumbnail.addEventListener('loadedmetadata', () => {
      if (Number.isFinite(thumbnail.duration) && thumbnail.duration > 0) {
        thumbnail.currentTime = Math.min(0.05, thumbnail.duration / 2);
      }
    }, { once: true });
    const title = document.createElement('strong');
    title.textContent = run.source_video_id || runFilename(run) || 'Video ID unavailable';
    const meta = document.createElement('span');
    meta.className = 'run-card-meta';
    const completedCount = runs.filter((item) => item.status === 'completed').length;
    meta.textContent = `${runFilename(run)} · ${completedCount} stored ${completedCount === 1 ? 'analysis' : 'analyses'} · Latest status ${String(latest.status || 'unknown').toUpperCase()}`;
    const actions = document.createElement('div');
    actions.className = 'run-card-actions';
    const details = document.createElement('div');
    details.className = 'run-card-details';
    const alertSummary = document.createElement('span');
    alertSummary.className = 'run-card-meta run-card-alert-summary';
    alertSummary.textContent = completed ? 'Alert summary: Loading stored report…' : 'No completed result is available yet.';
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'track-link';
    view.textContent = 'View stored results';
    if (completed) {
      view.dataset.historyRunId = completed.run_id;
      view.dataset.historyAction = 'open';
    } else {
      view.disabled = true;
      view.title = 'No completed analysis is available yet.';
    }
    actions.append(view);
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'track-link';
    download.textContent = 'Download report';
    if (completed) {
      download.dataset.reportDownloadRunId = completed.run_id;
      download.dataset.videoId = completed.source_video_id || '';
    } else {
      download.disabled = true;
      download.title = 'A report is available after an analysis completes.';
    }
    actions.append(download);
    const fresh = document.createElement('button');
    fresh.type = 'button';
    fresh.className = 'track-link';
    fresh.textContent = 'Fresh re-analysis';
    if (source) {
      fresh.dataset.historyRunId = source.run_id;
      fresh.dataset.historyAction = 'reanalyze';
    } else {
      fresh.disabled = true;
      fresh.title = 'The original uploaded video is unavailable for re-analysis.';
    }
    actions.append(fresh);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'track-link result-delete-button';
    remove.dataset.deleteVideoId = run.source_video_id || '';
    remove.disabled = !run.source_video_id;
    if (!run.source_video_id) remove.title = 'This older record has no stable video ID and cannot be safely grouped for deletion.';
    remove.textContent = 'Delete analysis results';
    actions.append(remove);
    details.append(thumbnail, title, meta, alertSummary);
    card.append(details, actions);
    container.append(card);
  }
  void loadResultCardAlertSummaries(requestRuns, requestId);
}

async function loadResultCardAlertSummaries(runs, requestId) {
  for (const run of runs) {
    if (requestId !== state.resultCardRequest || state.route?.page !== 'results') return;
    const card = [...$('#results-run-cards').children]
      .find((item) => item.querySelector(`[data-report-download-run-id="${run.run_id}"]`));
    if (!card) continue;
    const summary = card.querySelector('.run-card-alert-summary');
    try {
      const report = await api(`/api/runs/${encodeURIComponent(run.run_id)}/report`);
      if (requestId !== state.resultCardRequest || state.route?.page !== 'results') return;
      summary.textContent = `Alert summary: ${report.alert?.message || (report.uav_alert ? 'Alert reported' : 'No alert reported')}`;
    } catch (error) {
      if (requestId !== state.resultCardRequest || state.route?.page !== 'results') return;
      summary.textContent = `Alert summary unavailable: ${error.message}`;
    }
  }
}

function renderReportsRunCards() {
  const container = $('#reports-run-cards');
  container.replaceChildren();
  if (state.route?.page !== 'reports') {
    state.reportCardRequest += 1;
    return;
  }
  const completed = state.historyRuns.filter((run) => run.status === 'completed');
  if (!completed.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = state.historyLoaded
      ? 'No completed reports are available for this account.'
      : 'Report history has not been loaded.';
    container.append(empty);
    return;
  }
  for (const run of completed) {
    const card = document.createElement('article');
    card.className = 'results-run-card';
    const title = document.createElement('strong');
    title.textContent = run.source_video_id || 'Video ID unavailable';
    const meta = document.createElement('span');
    meta.className = 'run-card-meta';
    const created = run.completed_at || run.created_at;
    const actualTiming = actualAnalysisDuration(run);
    const elapsedTiming = analysisTimelineDuration(run);
    const totalElapsed = elapsedTiming?.source === 'timestamps' ? formatDuration(elapsedTiming.seconds) : 'N/A';
    meta.textContent = `Run ID: ${run.run_id} · ${runFilename(run)} · final_results.json · ${created ? new Date(created).toLocaleString() : 'Report date unavailable'} · Actual analysis time ${actualTiming ? formatDuration(actualTiming.seconds) : 'N/A'} · Total elapsed ${totalElapsed} · Completed`;
    const summary = document.createElement('span');
    summary.className = 'run-card-meta report-card-summary';
    summary.textContent = 'Loading stored assessment…';
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'track-link';
    open.dataset.reportRunId = run.run_id;
    open.textContent = 'Open stored report';
    card.dataset.reportRunId = run.run_id;
    card.append(title, meta, summary, open);
    container.append(card);
  }
  const requestId = ++state.reportCardRequest;
  void loadReportCardSummaries(completed, requestId);
}

async function loadReportCardSummaries(runs, requestId) {
  for (const run of runs) {
    if (requestId !== state.reportCardRequest || state.route?.page !== 'reports') return;
    const card = [...$('#reports-run-cards').children]
      .find((item) => item.dataset.reportRunId === run.run_id);
    if (!card) continue;
    const summary = card.querySelector('.report-card-summary');
    try {
      const report = await api(`/api/runs/${encodeURIComponent(run.run_id)}/report`);
      if (requestId !== state.reportCardRequest || state.route?.page !== 'reports') return;
      const finalLabels = {};
      for (const track of Array.isArray(report.tracks) ? report.tracks : []) {
        const label = track.final_label || 'N/A';
        finalLabels[label] = (finalLabels[label] || 0) + 1;
      }
      const labels = Object.entries(finalLabels)
        .map(([label, count]) => `${label}: ${formatNumber(count)}`)
        .join(', ');
      summary.textContent = [
        `Alert: ${report.alert_status === 'not_run'
          ? 'Not run for thermal'
          : report.alert?.message || (report.uav_alert ? 'Alert reported' : 'No alert reported')}`,
        `Final labels: ${report.analysis_mode === 'thermal_detection_tracking_only'
          ? 'Not produced for thermal'
          : labels || 'Not reported'}`,
      ].join(' · ');
    } catch (error) {
      if (requestId !== state.reportCardRequest || state.route?.page !== 'reports') return;
      summary.textContent = `Stored report unavailable: ${error.message}`;
    }
  }
}

function renderReportDetail() {
  const summary = $('#report-detail-summary');
  summary.replaceChildren();
  if (!state.report || state.report.run_id !== state.runId) {
    const unavailable = document.createElement('p');
    unavailable.className = 'empty-state';
    unavailable.textContent = 'The stored report is not available for this run.';
    summary.append(unavailable);
    return;
  }
  const heading = document.createElement('h2');
  heading.textContent = `Source Video ${state.report.source_video_id || 'ID unavailable'}`;
  const details = document.createElement('p');
  details.textContent = [
    `Status: ${state.run?.status || state.report.run_status || 'N/A'}`,
    `Run ID: ${state.report.run_id}`,
    `Completed: ${state.report.completed_at ? new Date(state.report.completed_at).toLocaleString() : 'N/A'}`,
    `Tracks: ${formatNumber(state.report.track_count)}`,
    `Assessment: ${state.report.alert_status === 'not_run'
      ? 'Not run for thermal'
      : state.report.alert?.message || (state.report.uav_alert ? 'Alert reported' : 'No alert reported')}`,
  ].join(' · ');
  summary.append(heading, details);
}

async function loadReuseCandidates(runId) {
  try {
    const result = await api(`/api/runs/${encodeURIComponent(runId)}/reuse-candidates`);
    if (state.runId !== runId || !result.candidates?.length) return;
    const candidate = result.candidates[0];
    state.reuseCandidateRunId = candidate.run_id;
    $('#reuse-suggestion-text').textContent =
      'A previous completed run uses the same uploaded video. Viewing it is read-only; Start Analysis remains a fresh run.';
    $('#view-cached-results').hidden = false;
    $('#reuse-suggestion').hidden = false;
  } catch (error) {
    if (state.runId !== runId) return;
    $('#reuse-suggestion-text').textContent = `Could not check stored results for reuse: ${error.message}`;
    $('#view-cached-results').hidden = true;
    $('#reuse-suggestion').hidden = false;
  }
}

async function startFreshReanalysis(sourceRunId) {
  setBusy(true);
  try {
    const created = await api(`/api/runs/${encodeURIComponent(sourceRunId)}/reanalyze`, {
      method: 'POST',
      body: '',
    });
    state.runId = created.run_id;
    state.run = null;
    state.playerRunId = null;
    state.phaseExecution = {};
    state.selectedStage = 'uploaded';
    state.phaseTrackDetail = null;
    state.selectedPhaseTrackId = null;
    state.selectedTrailObservations = [];
    localStorage.setItem(RUN_STORAGE_KEY, created.run_id);
    clearRunResults();
    await resumeCurrentRun();
    await navigateTo('/video-analysis');
  } catch (error) {
    showGlobalError(`Could not create a fresh analysis: ${error.message}`);
  } finally {
    setBusy(false);
  }

  async function resumeRunFromCheckpoint(sourceRunId, button) {
    button.disabled = true;
    try {
      const checkpoint = await api(
        `/api/runs/${encodeURIComponent(sourceRunId)}/resume-checkpoint`,
      );
      if (!checkpoint.available) {
        showGlobalError(
          `${checkpoint.reason || 'No validated checkpoint is available.'} Use Fresh analysis to rerun this source.`,
        );
        return;
      }
      const resumed = await api(`/api/runs/${encodeURIComponent(sourceRunId)}/resume`, {
        method: 'POST',
        body: '',
      });
      await openHistoryRun(resumed.run_id);
      $('#input-prepared').textContent =
        'Validated detector/tracker results were reused in a new run. Later analysis stages will run from the tracking checkpoint.';
      $('#input-prepared').hidden = false;
    } catch (error) {
      showGlobalError(`Could not resume from the stored checkpoint: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }
}

function selectHistoryTab(tab) {
  for (const button of document.querySelectorAll('[data-history-tab]')) {
    const active = button.dataset.historyTab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  }
  $('#analysis-history-panel').hidden = tab !== 'analysis';
  $('#session-history-panel').hidden = tab !== 'session';
}

async function loadHistory(showSection = true) {
  const section = $('#history-section');
  if (showSection) section.hidden = false;
  $('#history-error').hidden = true;
  try {
    const [result, sessionResult] = await Promise.all([
      api('/api/runs'),
      api('/api/auth/history'),
    ]);
    state.historyRuns = Array.isArray(result.runs) ? result.runs : [];
    state.sessionHistory = Array.isArray(sessionResult.sessions) ? sessionResult.sessions : [];
    state.historyLoaded = true;
    state.historyPage = 1;
    renderHistory();
    renderResultsRunCards();
    renderReportsRunCards();
    renderSessionHistory();
  } catch (error) {
    $('#history-error').textContent = `Run history could not be loaded: ${error.message}`;
    $('#history-error').hidden = false;
    if (['results', 'reports', 'history', 'alerts'].includes(state.route?.page)) {
      $('#route-error').textContent = `Run history could not be loaded: ${error.message}`;
      $('#route-error').hidden = false;
    }
    if (!showSection) {
      $('#results-run-cards').replaceChildren();
      const message = document.createElement('p');
      message.className = 'empty-state';
      message.textContent = `Stored results could not be loaded: ${error.message}`;
      $('#results-run-cards').append(message);
    }
  }
}

async function openHistoryRun(runId) {
  if (!/^RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}$/.test(runId)) return false;
  await navigateTo(`/results/${encodeURIComponent(runId)}`);
  if (state.run?.status === 'prepared') {
    await navigateTo('/video-analysis');
    return true;
  }
  return state.runId === runId && Boolean(state.run);
}

function changePage(page) {
  const pageCount = Math.max(1, Math.ceil(filteredTracks().length / state.pageSize));
  state.page = Math.max(1, Math.min(pageCount, page));
  renderTracks();
}

function resetPageAndRender() {
  state.page = 1;
  renderTracks();
}

function setRowsPerPage(value) {
  state.pageSize = Number(value);
  state.page = 1;
  state.historyPage = 1;
  state.alertPage = 1;
  $('#rows-per-page').value = String(state.pageSize);
  $('#history-rows-per-page').value = String(state.pageSize);
  $('#alert-rows-per-page').value = String(state.pageSize);
  renderTracks();
  renderHistory();
  renderAlerts(state.report, state.alertError);
}

function detailStat(label, value) {
  return `<div class="detail-stat"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;
}

function trajectoryPlot(trajectory, compact = false) {
  const points = [...(trajectory?.trajectory_points || [])]
    .sort((left, right) => Number(left.frame) - Number(right.frame));
  const valid = points.filter((point) =>
    Number.isFinite(Number(point.x_px))
    && Number.isFinite(Number(point.y_px))
    && Number.isFinite(Number(point.smoothed_x_px))
    && Number.isFinite(Number(point.smoothed_y_px)));
  if (!valid.length) return '<p>Trajectory plot unavailable; usable image-plane positions were not reported.</p>';
  const width = 600;
  const height = 220;
  const padding = 18;
  const xValues = valid.flatMap((point) => [Number(point.x_px), Number(point.smoothed_x_px)]);
  const yValues = valid.flatMap((point) => [Number(point.y_px), Number(point.smoothed_y_px)]);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues);
  const minY = Math.min(...yValues);
  const maxY = Math.max(...yValues);
  const map = (x, y) => {
    const px = padding + (maxX === minX ? 0.5 : (x - minX) / (maxX - minX)) * (width - 2 * padding);
    const py = padding + (maxY === minY ? 0.5 : (y - minY) / (maxY - minY)) * (height - 2 * padding);
    return `${px.toFixed(2)},${py.toFixed(2)}`;
  };
  const rawPoints = valid.map((point) => map(Number(point.x_px), Number(point.y_px))).join(' ');
  const smoothPoints = valid.map((point) => map(Number(point.smoothed_x_px), Number(point.smoothed_y_px))).join(' ');
  const framePoints = valid.map((point) => [
    Number(point.frame), Number(point.x_px), Number(point.y_px),
    Number(point.smoothed_x_px), Number(point.smoothed_y_px),
    Number.isFinite(Number(point.timestamp_sec)) ? Number(point.timestamp_sec) : null,
    Number.isFinite(Number(point.speed_px_per_second)) ? Number(point.speed_px_per_second) : null,
  ]);
  const waypointNodes = compact ? valid.map((point) => {
    const [cx, cy] = map(Number(point.smoothed_x_px), Number(point.smoothed_y_px)).split(',');
    const frame = Number(point.frame);
    const timestamp = Number.isFinite(Number(point.timestamp_sec)) ? Number(point.timestamp_sec) : '';
    const speed = Number.isFinite(Number(point.speed_px_per_second)) ? Number(point.speed_px_per_second) : '';
    return `<circle class="trajectory-waypoint" cx="${cx}" cy="${cy}" r="6" data-trajectory-frame="${escapeHTML(frame)}" data-trajectory-timestamp="${escapeHTML(timestamp)}" data-trajectory-speed="${escapeHTML(speed)}" data-trajectory-x="${escapeHTML(point.smoothed_x_px)}" data-trajectory-y="${escapeHTML(point.smoothed_y_px)}" aria-hidden="true"></circle>`;
  }).join('') : '';
  return `<div class="trajectory-plot-wrap${compact ? ' trajectory-plot-compact' : ''}">
    <svg class="trajectory-plot" viewBox="0 0 ${width} ${height}" role="${compact ? 'group' : 'img'}" aria-label="${compact ? 'Hover over trajectory points to seek the matching frame' : 'Raw and smoothed tracked center positions in chronological frame order, using image-plane pixel coordinates'}"${compact ? ` data-trajectory-points="${escapeHTML(JSON.stringify(framePoints))}" data-trajectory-bounds="${escapeHTML(JSON.stringify([minX, maxX, minY, maxY]))}"` : ''}>
      <polyline class="trajectory-raw" points="${rawPoints}"></polyline>
      <polyline class="trajectory-smoothed" points="${smoothPoints}"></polyline>
      ${waypointNodes}
      <circle class="trajectory-start" cx="${map(Number(valid[0].smoothed_x_px), Number(valid[0].smoothed_y_px)).replace(',', '" cy="')}" r="4"></circle>
      <circle class="trajectory-end" cx="${map(Number(valid.at(-1).smoothed_x_px), Number(valid.at(-1).smoothed_y_px)).replace(',', '" cy="')}" r="4"></circle>
    </svg>
    <div class="plot-legend"><span>Raw center</span><span>Smoothed center (used for motion-state classification)</span></div>
    ${compact ? '' : '<p>Image-plane pixels only • Y coordinates follow image pixel direction • no physical-world scale or 3D calibration.</p>'}
  </div>`;
}

function renderExpandedTrajectory(track, detail) {
  const trajectory = detail?.motion_trajectory;
  if (!trajectory) {
    $('#trajectory-dialog-content').innerHTML = '<p>Stored trajectory data is unavailable for this track.</p>';
    return;
  }
  const points = [...(trajectory.trajectory_points || [])]
    .sort((left, right) => Number(left.frame) - Number(right.frame));
  const firstPoint = points[0];
  const lastPoint = points.at(-1);
  const frameRange = firstPoint && lastPoint
    ? `${formatNumber(firstPoint.frame)}–${formatNumber(lastPoint.frame)}`
    : `${formatNumber(detail.start_frame)}–${formatNumber(detail.end_frame)}`;
  const timestampRange = firstPoint
    && lastPoint
    && firstPoint.timestamp_sec !== null
    && lastPoint.timestamp_sec !== null
    && Number.isFinite(Number(firstPoint.timestamp_sec))
    && Number.isFinite(Number(lastPoint.timestamp_sec))
    ? `${formatVideoTimestamp(Number(firstPoint.timestamp_sec))}–${formatVideoTimestamp(Number(lastPoint.timestamp_sec))}`
    : 'Unavailable';
  const observations = trajectory.summary?.observations !== null
    && trajectory.summary?.observations !== undefined
    && Number.isFinite(Number(trajectory.summary.observations))
    ? formatNumber(trajectory.summary.observations)
    : formatNumber(points.length);
  $('#trajectory-dialog-title').textContent = `Track ${track.track_id} trajectory`;
  $('#trajectory-dialog-content').innerHTML = `<div class="detail-grid trajectory-detail-stats">
      ${detailStat('Track ID', track.track_id)}
      ${detailStat('Frame range', frameRange)}
      ${detailStat('Timestamp range', timestampRange)}
      ${detailStat('Observations', observations)}
    </div>${trajectoryPlot(trajectory)}`;
}

function renderDashboardTrajectoryDetail(track, detail) {
  const preview = $('#dashboard-trajectory-preview');
  const content = $('#dashboard-trajectory-preview-content');
  const trajectory = detail?.motion_trajectory;
  state.selectedTrailObservations = Array.isArray(detail?.detector_observations)
    ? detail.detector_observations
    : [];
  updateTrajectoryOverlayToggle();
  if (!trajectory) {
    preview.disabled = true;
    content.textContent = `Track ${track.track_id} has no stored trajectory data.`;
    $('#trajectory-dialog-content').innerHTML = '<p>Stored trajectory data is unavailable for this track.</p>';
    return;
  }
  preview.disabled = false;
  preview.setAttribute('aria-label', `Expand trajectory for Track ${track.track_id}`);
  content.innerHTML = `<span class="trajectory-preview-track">Track ${escapeHTML(track.track_id)} · ${escapeHTML(track.class || 'Class unavailable')}</span>${trajectoryPlot(trajectory, true)}`;
  updateDashboardTrajectoryPlayback();
  if ($('#trajectory-dialog').open) renderExpandedTrajectory(track, detail);
}

function updateDashboardTrajectoryPlayback() {
  const svg = $('#dashboard-trajectory-preview-content .trajectory-plot[data-trajectory-points]');
  if (!svg) return;
  if (!Array.isArray(svg._trajectoryPoints)) {
    try {
      svg._trajectoryPoints = JSON.parse(svg.dataset.trajectoryPoints || '[]');
      svg._trajectoryBounds = JSON.parse(svg.dataset.trajectoryBounds || '[0,1,0,1]');
    } catch {
      svg._trajectoryPoints = [];
      svg._trajectoryBounds = [0, 1, 0, 1];
    }
  }
  const visible = Number.isFinite(state.currentFrameIndex)
    ? svg._trajectoryPoints.filter((point) => Number(point[0]) <= state.currentFrameIndex)
    : [];
  const [minX, maxX, minY, maxY] = svg._trajectoryBounds;
  const padding = 18;
  const map = (x, y) => {
    const { width, height } = svg.viewBox.baseVal;
    const px = padding + (maxX === minX ? 0.5 : (x - minX) / (maxX - minX)) * (width - 2 * padding);
    const py = padding + (maxY === minY ? 0.5 : (y - minY) / (maxY - minY)) * (height - 2 * padding);
    return [px.toFixed(2), py.toFixed(2)];
  };
  const raw = visible.map((point) => map(Number(point[1]), Number(point[2])).join(',')).join(' ');
  const smooth = visible.map((point) => map(Number(point[3]), Number(point[4])).join(',')).join(' ');
  svg.querySelector('.trajectory-raw')?.setAttribute('points', raw);
  svg.querySelector('.trajectory-smoothed')?.setAttribute('points', smooth);
  const start = svg.querySelector('.trajectory-start');
  const current = svg.querySelector('.trajectory-end');
  if (!visible.length) {
    start?.setAttribute('visibility', 'hidden');
    current?.setAttribute('visibility', 'hidden');
    return;
  }
  const [startX, startY] = map(Number(visible[0][3]), Number(visible[0][4]));
  const [currentX, currentY] = map(Number(visible.at(-1)[3]), Number(visible.at(-1)[4]));
  start?.setAttribute('cx', startX);
  start?.setAttribute('cy', startY);
  start?.setAttribute('visibility', 'visible');
  current?.setAttribute('cx', currentX);
  current?.setAttribute('cy', currentY);
  current?.setAttribute('visibility', 'visible');
  svg.querySelectorAll('.trajectory-waypoint').forEach((waypoint) => {
    const active = Number(waypoint.dataset.trajectoryFrame) === state.currentFrameIndex;
    waypoint.classList.toggle('trajectory-waypoint-current', active);
  });
}

function inspectTrajectoryWaypoint(waypoint) {
  const frame = Number(waypoint.dataset.trajectoryFrame);
  if (!Number.isSafeInteger(frame) || frame < 1) return;
  const metadata = videoFrameMetadata();
  const timestamp = waypoint.dataset.trajectoryTimestamp !== '' && Number.isFinite(Number(waypoint.dataset.trajectoryTimestamp))
    ? Number(waypoint.dataset.trajectoryTimestamp)
    : metadata ? (frame - 1) / metadata.fps : NaN;
  const x = Number(waypoint.dataset.trajectoryX);
  const y = Number(waypoint.dataset.trajectoryY);
  const speed = waypoint.dataset.trajectorySpeed !== '' ? Number(waypoint.dataset.trajectorySpeed) : NaN;
  $('#trajectory-waypoint-readout').textContent = `Track ${state.selectedTrajectoryTrackId} · Frame ${frame} · ${Number.isFinite(timestamp) ? formatVideoTimestamp(timestamp) : 'time unavailable'} · Center (${Number.isFinite(x) ? x.toFixed(1) : '—'}, ${Number.isFinite(y) ? y.toFixed(1) : '—'}) px · Speed ${Number.isFinite(speed) ? `${speed.toFixed(1)} px/s` : 'unavailable'}`;

  const player = $('#input-video-player');
  if (
    !state.runId
    || state.playerRunId !== state.runId
    || !Number.isFinite(player.duration)
    || !Number.isFinite(timestamp)
    || timestamp < 0
    || timestamp > player.duration
  ) {
    $('#current-frame-status').textContent = 'Matching run video is unavailable for waypoint scrubbing.';
    return;
  }
  state.alertEvidence = null;
  state.lastFrameLookupKey = null;
  state.frameLookupRequest += 1;
  state.currentFrameIndex = frame;
  $('#current-frame-index').textContent = `${frame} / ${formatNumber(metadata?.totalFrames)}`;
  $('#current-frame-timestamp').textContent = formatVideoTimestamp(timestamp);
  $('#current-frame-status').textContent = `Trajectory waypoint · Track ${state.selectedTrajectoryTrackId}`;
  if (Math.abs(player.currentTime - timestamp) > 0.001) player.currentTime = timestamp;
  else {
    drawCurrentVideoFrame();
    void refreshStoredFrameObservations();
  }
  updateDashboardTrajectoryPlayback();
}

async function renderDashboardTrajectory() {
  const select = $('#dashboard-trajectory-track');
  const preview = $('#dashboard-trajectory-preview');
  const content = $('#dashboard-trajectory-preview-content');
  const tracks = state.tracks || [];
  select.replaceChildren();
  for (const track of tracks) {
    const reportTrack = state.report?.run_id === state.runId
      ? state.report.tracks?.find((item) => String(item.track_id) === String(track.track_id))
      : null;
    const detectorHistory = Array.isArray(track.class_history) && track.class_history.length
      ? [...new Set(track.class_history)]
      : [track.class || 'Class unavailable'];
    const thermalOnly = state.run?.input_type === 'thermal';
    const decision = thermalOnly
      ? reportTrack?.temporal_consensus?.consensus_label
      : reportTrack?.final_label || track.final_label;
    const decisionLabel = thermalOnly ? 'Temporal consensus' : 'Final decision';
    select.add(new Option(
      `Track ${track.track_id} · Detector history: ${detectorHistory.join(' → ')} · ${decisionLabel}: ${decision || 'Unavailable'}`,
      String(track.track_id),
    ));
  }
  select.disabled = !tracks.length;
  if (!state.runId || !tracks.length) {
    state.selectedTrajectoryTrackId = null;
    preview.disabled = true;
    content.textContent = 'No tracked objects are available for this run.';
    return;
  }
  const selectedTrack = tracks.find((track) => String(track.track_id) === String(state.selectedTrajectoryTrackId))
    || tracks.find((track) => String(track.track_id) === String(state.selectedPhaseTrackId))
    || tracks[0];
  state.selectedTrajectoryTrackId = selectedTrack.track_id;
  select.value = String(selectedTrack.track_id);

  const runId = state.runId;
  const trackId = selectedTrack.track_id;
  const cacheKey = `${runId}:${trackId}`;
  if (state.trajectoryDetailsRunId !== runId) {
    state.trajectoryDetails.clear();
    state.trajectoryDetailsRunId = runId;
  }
  if (state.trajectoryDetails.has(cacheKey)) {
    renderDashboardTrajectoryDetail(selectedTrack, state.trajectoryDetails.get(cacheKey));
    return;
  }
  if (String(state.phaseTrackDetail?.track_id) === String(trackId)) {
    state.trajectoryDetails.set(cacheKey, state.phaseTrackDetail);
    renderDashboardTrajectoryDetail(selectedTrack, state.phaseTrackDetail);
    return;
  }

  const requestId = ++state.trajectoryRequest;
  preview.disabled = true;
  content.textContent = `Loading stored trajectory for Track ${trackId}…`;
  try {
    const detail = await api(
      `/api/runs/${encodeURIComponent(runId)}/tracks/${encodeURIComponent(trackId)}`,
    );
    if (
      requestId !== state.trajectoryRequest
      || runId !== state.runId
      || String(trackId) !== String(state.selectedTrajectoryTrackId)
    ) return;
    state.trajectoryDetails.set(cacheKey, detail);
    renderDashboardTrajectoryDetail(selectedTrack, detail);
  } catch (error) {
    if (requestId !== state.trajectoryRequest || runId !== state.runId) return;
    content.textContent = `Stored trajectory could not be loaded: ${error.message}`;
  }
}

async function selectTrajectoryTrack(trackId) {
  const runId = state.runId;
  if (!runId || !trackId) return;
  state.selectedTrajectoryTrackId = trackId;
  state.selectedPhaseTrackId = trackId;
  state.selectedTrailObservations = [];
  updateTrajectoryOverlayToggle();
  state.phaseTrackDetail = null;
  state.alertEvidence = null;
  state.lastFrameLookupKey = null;
  state.frameLookupRequest += 1;
  $('#current-frame-status').textContent = 'Loading selected track evidence…';

  await renderDashboardTrajectory();
  if (runId !== state.runId || String(trackId) !== String(state.selectedTrajectoryTrackId)) return;

  const detail = state.trajectoryDetails.get(`${runId}:${trackId}`);
  if (!detail || String(detail.track_id) !== String(trackId)) return;
  const selectedEvidence = (Array.isArray(detail.selected_evidence) ? detail.selected_evidence : [])
    .find((item) => String(item.track_id) === String(trackId));
  const reportEvidence = state.report?.run_id === runId
    ? (Array.isArray(state.report.alert?.evidence) ? state.report.alert.evidence : [])
      .find((item) => String(item.track_id) === String(trackId))?.observation
    : null;
  const evidence = selectedEvidence || reportEvidence;
  const rawFrame = evidence?.frame_index ?? evidence?.frame;
  const frameIndex = typeof rawFrame === 'number'
    ? rawFrame
    : typeof rawFrame === 'string' && /^\d+$/.test(rawFrame) ? Number(rawFrame) : NaN;
  const numericTrackId = /^\d+$/.test(String(trackId)) ? Number(trackId) : NaN;
  if (
    !evidence
    || !Number.isSafeInteger(frameIndex)
    || frameIndex < 1
    || !Number.isSafeInteger(numericTrackId)
    || numericTrackId < 0
  ) {
    $('#current-frame-status').textContent = `No stored evidence frame for Track ${trackId}`;
    clearCurrentFrameObservations('No stored evidence is available for the selected track.');
    return;
  }

  const metadata = videoFrameMetadata();
  const timestamp = Number.isFinite(evidence.timestamp)
    ? evidence.timestamp
    : Number.isFinite(evidence.timestamp_sec)
      ? evidence.timestamp_sec
      : metadata ? (frameIndex - 1) / metadata.fps : NaN;
  const track = state.tracks.find((item) => String(item.track_id) === String(trackId));
  await viewAlertEvidence({
    frame_index: frameIndex,
    timestamp,
    track_id: numericTrackId,
    detector_class: track?.class || detail.class,
    detector_confidence: evidence.confidence,
    reliability_status: detail.reliability?.status,
    reliability_score: detail.reliability?.reliability_score,
    bbox: evidence.bbox,
  }, 'Selected Track Evidence');
}

function renderAssessment(track, fusion, evidence, assessment) {
  const stateName = assessment?.assessment || 'MANUAL_REVIEW';
  const reason = assessment?.reason || 'Review the available evidence. This system does not establish operational threat certainty.';
  const panels = {
    HIGH_PRIORITY_ALERT: ['UAV DETECTED', 'HIGH PRIORITY ALERT', 'assessment-high'],
    WARNING: ['UAV SUSPECTED', 'VERIFICATION REQUIRED', 'assessment-warning'],
    NON_THREAT: ['NON-THREAT OBJECT', `${fusion.final_label} DETECTED`, 'assessment-safe'],
    MANUAL_REVIEW: ['INSUFFICIENT EVIDENCE', 'MANUAL REVIEW REQUIRED', 'assessment-manual'],
  };
  const [title, subtitle, className] = panels[stateName] || panels.MANUAL_REVIEW;
  const transitionKey = `${state.runId}:${track.track_id}:${stateName}`;
  const isHighPriority = stateName === 'HIGH_PRIORITY_ALERT';
  if (isHighPriority && !state.alarmTransitions.has(transitionKey)) {
    state.alarmTransitions.add(transitionKey);
    state.muted = false;
  }
  const alarmActive = isHighPriority && !state.muted;
  const frameLabels = evidence.length
    ? evidence.map((item) => `Frame ${formatNumber(item.frame)}`).join(', ')
    : 'No selected RGB evidence';
  const completed = state.run?.completed_at ? new Date(state.run.completed_at).toLocaleString() : 'Run completion time unavailable';
  return `<section class="assessment-panel ${className}" aria-live="polite">
    <div class="assessment-head"><div><p class="eyebrow">FINAL ASSESSMENT · DECISION SUPPORT</p><h3><span class="assessment-symbol" aria-hidden="true">${stateName === 'HIGH_PRIORITY_ALERT' || stateName === 'WARNING' ? '!' : stateName === 'NON_THREAT' ? '✓' : '?'}</span>${title}</h3><strong>${subtitle}</strong></div>
      <div class="alarm-control"><span class="alarm-indicator ${alarmActive ? 'alarm-on' : 'alarm-off'}">${alarmActive ? 'ALARM ON' : state.muted && isHighPriority ? 'ALARM MUTED' : 'ALARM OFF'}</span>
      <button class="button button-quiet" type="button" data-mute-alarm ${isHighPriority ? '' : 'disabled'}>${state.muted ? 'Unmute alarm' : 'Mute alarm'}</button></div></div>
    <div class="detail-grid">${detailStat('Track ID', track.track_id)}${detailStat('Final label', fusion.final_label)}${detailStat('Final confidence score', formatNumber(fusion.final_confidence, 3))}${detailStat('Reliability score', formatNumber(fusion.reliability?.score, 3))}${detailStat('Evidence frames', frameLabels)}${detailStat('Run completed', completed)}</div>
    <p class="detail-reason">${escapeHTML(reason)}</p>
    <p class="scientific-note">Fusion and reliability values are heuristic decision scores, not calibrated probabilities. This dashboard does not trigger physical actions.</p>
  </section>`;
}

function evidenceImageUrl(rawPath) {
  const url = new URL(rawPath, window.location.origin);
  const prefix = `/api/runs/${encodeURIComponent(state.runId)}/evidence/`;
  if (url.origin !== window.location.origin || !url.pathname.startsWith(prefix)) {
    throw new Error('Evidence URL is outside the current run.');
  }
  return `${url.pathname}${url.search}`;
}

function renderTemporalConsensus(ledger) {
  const temporal = ledger?.temporal_consensus;
  const result = temporal?.result;
  if (!result) {
    return '<p>Temporal consensus was not run for this result.</p>';
  }
  const reasons = Array.isArray(result.reasons) ? result.reasons : [];
  const supportingFrames = Array.isArray(result.supporting_frame_indexes)
    ? result.supporting_frame_indexes
    : [];
  const contradictoryFrames = Array.isArray(result.contradictory_frame_indexes)
    ? result.contradictory_frame_indexes
    : [];
  const source = temporal.source === 'persisted_temporal_consensus'
    ? 'Persisted run artifact'
    : 'Derived from stored detector observations';
  const referencesAvailable = result.evidence_references_available !== false;
  return `<div class="detail-grid">
      ${detailStat('Track-level decision', result.consensus_label || 'Uncertain')}
      ${detailStat('Decision score (not calibrated probability)', formatNumber(result.consensus_confidence, 3))}
      ${detailStat('Decision source', source)}
      ${detailStat('Observations used', formatNumber(result.observation_count))}
    </div>
    <p>${escapeHTML(result.score_interpretation || 'Heuristic decision score; not calibrated probability.')}</p>
    <p>Reasons: ${reasons.length ? reasons.map((reason) => escapeHTML(reason)).join('; ') : 'No reason codes reported'}.</p>
    <p>Supporting predicted frames: ${!referencesAvailable ? 'Not recorded' : supportingFrames.length ? supportingFrames.map((frame) => formatNumber(frame)).join(', ') : 'None'}.
      Contradictory predicted frames: ${!referencesAvailable ? 'Not recorded' : contradictoryFrames.length ? contradictoryFrames.map((frame) => formatNumber(frame)).join(', ') : 'None'}.</p>`;
}

function renderDetail(track, evidence, vlm, modalities, fusion, assessment, optionalErrors) {
  if (track.analysis_mode === 'thermal_detection_tracking_only') {
    const observations = Array.isArray(track.detector_observations)
      ? track.detector_observations
      : [];
    const ledger = track.evidence_ledger;
    const temporalConsensus = ledger?.temporal_consensus;
    const classChanges = Array.isArray(ledger?.class_changes) ? ledger.class_changes : [];
    const observationGaps = Array.isArray(ledger?.observation_gaps) ? ledger.observation_gaps : [];
    $('#detail-content').innerHTML = `<section class="detail-block">
      <div class="detail-block-head"><h3>Thermal Track Evidence Ledger</h3><span class="status-tag">Predictions, not ground truth</span></div>
      <div class="detail-grid">
        ${detailStat('Track ID', track.track_id)}
        ${detailStat('First predicted class', track.class || track.detector_label)}
        ${detailStat('Frame range', `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`)}
        ${detailStat('Observations', formatNumber(track.frames_seen))}
        ${detailStat('Mean detector confidence', formatNumber(track.detector_confidence, 3))}
        ${detailStat('Ledger status', ledger?.status || 'Unavailable')}
        ${detailStat('Checkpoint SHA-256', ledger?.model_provenance?.checkpoint_sha256 || 'Unavailable')}
        ${detailStat('Reliability', 'Not run')}
        ${detailStat('Evidence / VLM / fusion / assessment', 'Not run')}
      </div>
      <p>Raw detector confidence is not calibrated probability. Image-quality indicators and independently verified supporting or contradictory labels are unavailable.</p>
      <h4>Temporal consensus</h4>${renderTemporalConsensus(ledger)}
      <p>Consensus is a heuristic derived from predicted observations, not ground truth or a validated accuracy measure. The full-track decision is offline and may use later frames in the run.</p>
      <p>Per-frame class changes: ${classChanges.length ? classChanges.map((item) => `Frame ${formatNumber(item.frame)}: ${escapeHTML(item.previous_class)} → ${escapeHTML(item.predicted_class)}`).join('; ') : 'None observed'}.</p>
      <p>Unobserved intervals for this track: ${observationGaps.length ? observationGaps.map((item) => `${formatNumber(item.missing_source_frame_count)} source frame(s) between observations`).join('; ') : 'None observed'}.</p>
      <div class="table-wrap"><table><thead><tr><th>FRAME</th><th>SOURCE FRAME</th><th>TIME (S)</th><th>CLASS ID</th><th>PREDICTED CLASS</th><th>RAW CONFIDENCE</th><th>BOUNDING BOX</th></tr></thead>
      <tbody>${observations.map((item) => `<tr>
        <td>${formatNumber(item.frame)}</td>
        <td>${formatNumber(item.source_frame_index)}</td>
        <td>${item.timestamp_sec === null ? 'N/A' : formatNumber(item.timestamp_sec, 3)}</td>
        <td>${formatNumber(item.class_id)}</td>
        <td>${escapeHTML(item.class || 'N/A')}</td>
        <td>${formatNumber(item.confidence, 3)}</td>
        <td>${['x1', 'y1', 'x2', 'y2'].map((key) => formatNumber(item.bbox?.[key], 1)).join(', ')}</td>
      </tr>`).join('')}</tbody></table></div>
    </section>`;
    return;
  }
  const thermal = modalities.thermal || {};
  const radar = modalities.radar || {};
  const evidenceContent = evidence.length
    ? `<div class="evidence-grid">${evidence.map((item) => {
      let source;
      try {
        source = escapeHTML(evidenceImageUrl(item.crop_path));
      } catch {
        source = '';
      }
      return `<article class="evidence-card">
        ${source ? `<img src="${source}" alt="Selected RGB evidence, Track ${escapeHTML(item.track_id)}, frame ${formatNumber(item.frame)}" loading="lazy">` : '<div class="image-unavailable">Evidence image path unavailable</div>'}
        <div class="evidence-meta"><strong>Frame ${formatNumber(item.frame)} · ${formatNumber(Number(item.confidence) * 100, 1)}% detector confidence</strong><span>Selected RGB evidence · ${escapeHTML(item.selection_group || 'selection group not reported')}</span></div>
      </article>`;
    }).join('')}</div>`
    : '<p>No selected RGB evidence is available for this track. This is not negative evidence.</p>';
  const vlmContent = vlm.available && vlm.result
    ? `<div class="detail-grid">${detailStat('VLM label', vlm.result.label)}${detailStat('Confidence proxy', formatNumber(vlm.result.confidence, 3))}${detailStat('Evidence frame', formatNumber(vlm.result.frame))}</div><p class="scientific-note">Generated-token confidence proxy; not calibrated probability.</p><p>${escapeHTML(vlm.result.reason || '')}</p>`
    : '<p>VLM was not run automatically. The detector/temporal label is unverified; inspect the evidence before acting.</p>';
  const optionalWarning = optionalErrors.length
    ? `<p class="detail-error">Some optional track evidence could not be loaded: ${escapeHTML(optionalErrors.join('; '))}</p>`
    : '';
  const reliability = track.reliability || {};
  const trajectory = track.motion_trajectory;
  const motionSummary = trajectory?.summary || {};
  const trajectoryContent = trajectory
    ? `<div class="detail-grid">
      ${detailStat('Track ID', track.track_id)}
      ${detailStat('Class', track.class)}
      ${detailStat('Motion state', trajectory.motion_state)}
      ${detailStat('Motion quality', motionSummary.motion_quality)}
      ${detailStat('Current speed', trajectory.current_speed_px_per_second === null ? 'Unavailable' : `${formatNumber(trajectory.current_speed_px_per_second, 2)} px/s`)}
      ${detailStat('Current heading', trajectory.current_heading_deg === null ? 'Undefined / no motion' : `${formatNumber(trajectory.current_heading_deg, 1)} deg`)}
      ${detailStat('Current image-plane acceleration', trajectory.current_acceleration_px_per_second2 === null ? 'Unavailable' : `${formatNumber(trajectory.current_acceleration_px_per_second2, 2)} px/s²`)}
      ${detailStat('Path length', `${formatNumber(motionSummary.path_length_px, 1)} px`)}
      ${detailStat('Net displacement', `${formatNumber(motionSummary.net_displacement_px, 1)} px`)}
      ${detailStat('Straightness ratio', formatNumber(motionSummary.straightness_ratio, 3))}
      ${detailStat('Direction consistency', formatNumber(motionSummary.direction_consistency, 3))}
      ${detailStat('Trajectory duration', motionSummary.duration_sec === null ? 'Unavailable' : `${formatNumber(motionSummary.duration_sec, 2)} s`)}
      ${detailStat('Observation count', formatNumber(motionSummary.observations))}
      ${detailStat('Trajectory motion diagnostic', reliability.trajectory_motion_score === null || reliability.trajectory_motion_score === undefined ? 'Unavailable' : formatNumber(reliability.trajectory_motion_score, 3))}
      ${detailStat('Continuity', motionSummary.consecutive_frame_ratio === null ? 'Insufficient observations' : `${formatNumber(motionSummary.consecutive_frame_ratio * 100, 1)}% consecutive intervals`)}
    </div>${trajectoryPlot(trajectory)}
    <p>Image-plane motion metrics only. The trajectory motion diagnostic is additional evidence and does not alter the historical reliability score or independently identify Drone versus Bird.</p>`
    : '<p>Image-plane trajectory data is unavailable for this run.</p>';
  const detectorFrames = track.detector_observations || [];
  const frameRange = `${formatNumber(track.start_frame)}–${formatNumber(track.end_frame)}`;
  const thermalStatus = thermal.synchronized === true ? 'Synchronized; spatial association not implied' : 'Unavailable for this run';
  const radarStatus = radar.track_association_status === 'established' ? 'Associated (validated status reported)' : 'Track association not established';
  const consensusContent = renderTemporalConsensus(track.evidence_ledger);
  const outputAssessment = renderAssessment(track, fusion, evidence, assessment);

  $('#detail-content').innerHTML = `${optionalWarning}
    <section class="detail-block"><div class="detail-block-head"><h3>Track information</h3><span class="status-tag ${reliability.status === 'CONFIDENT' ? 'status-confident' : 'status-uncertain'}">${escapeHTML(reliability.status || 'Status not reported')}</span></div>
      <div class="detail-grid">${detailStat('Track ID', track.track_id)}${detailStat('Class', track.class)}${detailStat('Frame range', frameRange)}${detailStat('Observations', formatNumber(track.frames_seen))}${detailStat('Reliability', formatNumber(reliability.reliability_score, 3))}${detailStat('Legacy motion score', formatNumber(reliability.legacy_motion_score ?? reliability.motion_score, 3))}${detailStat('Mean detector confidence', formatNumber(reliability.mean_confidence, 3))}</div>
      <p>Reliability and the legacy motion score preserve the existing heuristic formula. The trajectory diagnostic is separate evidence, not calibrated probability. ${formatNumber(detectorFrames.length)} detector observations are included for this track.</p></section>
    <section class="detail-block"><div class="detail-block-head"><h3>Temporal consensus</h3><span class="badge badge-limited">Track-level heuristic</span></div>${consensusContent}</section>
    <section class="detail-block"><div class="detail-block-head"><h3>Selected RGB evidence</h3><span class="badge ${evidence.length ? 'badge-validated' : 'badge-unavailable'}">${evidence.length ? `${evidence.length} available` : 'Unavailable'}</span></div>${evidenceContent}</section>
    <section class="detail-block"><div class="detail-block-head"><h3>VLM verification</h3><span class="badge ${vlm.available ? 'badge-validated' : 'badge-limited'}">${vlm.available ? 'Available' : 'Not run'}</span></div>${vlmContent}</section>
    <section class="detail-block"><div class="detail-block-head"><h3>Motion &amp; trajectory intelligence</h3><span class="badge badge-limited">Image-plane motion metrics</span></div>${trajectoryContent}</section>
    <section class="detail-block"><div class="detail-block-head"><h3>Modalities</h3><span class="badge badge-limited">Missing is not negative</span></div>
      <div class="modality-detail-grid">${detailStat('RGB', 'Available for this run')}${detailStat('Thermal', thermalStatus)}${detailStat('Radar', radarStatus)}</div>
      <p>Thermal evidence is not automatically associated with uploaded video. External Radar validation does not establish Radar-to-Caltech-track association or persistent Radar identity.</p></section>
    <section class="detail-block"><div class="detail-block-head"><h3>Fusion result</h3><span class="badge badge-limited">${escapeHTML(String(fusion.fusion_mode || 'Fusion mode unavailable').replaceAll('_', ' '))}</span></div>
      <div class="detail-grid">${detailStat(fusion.fusion_mode === 'detector_temporal' ? 'Detector/temporal output · unverified' : 'Fusion output', fusion.fusion_mode === 'detector_temporal' && fusion.final_label === 'UAV' ? 'Possible UAV' : fusion.final_label)}${detailStat('Final confidence score', formatNumber(fusion.final_confidence, 3))}${detailStat('Detector class', fusion.detector_evidence?.class)}${detailStat('VLM used', fusion.fusion_mode === 'detector_vlm' ? 'Yes' : 'No')}</div>
      <p>${escapeHTML(fusion.confidence_interpretation || 'Fusion confidence is a decision score, not calibrated probability.')}</p></section>
    ${outputAssessment}`;
  $('#detail-content').querySelector('[data-mute-alarm]')?.addEventListener('click', () => {
    state.muted = !state.muted;
    renderDetail(track, evidence, vlm, modalities, fusion, assessment, optionalErrors);
  });
}

async function openTrack(trackId) {
  const runId = state.runId;
  if (!runId) return;
  const requestId = ++state.activeTrackRequest;
  $('#detail-track-id').textContent = trackId;
  $('#detail-content').innerHTML = '<p class="detail-loading">Loading run-scoped track evidence…</p>';
  showDetail();
  const prefix = `/api/runs/${encodeURIComponent(runId)}/tracks/${encodeURIComponent(trackId)}`;
  try {
    const [track, evidenceResult, vlmResult, modalitiesResult, fusionResult] = await Promise.allSettled([
      api(prefix),
      api(`${prefix}/evidence`),
      api(`${prefix}/vlm`),
      api(`${prefix}/modalities`),
      api(`${prefix}/fusion`),
    ]);
    if (requestId !== state.activeTrackRequest || runId !== state.runId) return;
    if (track.status === 'rejected' || fusionResult.status === 'rejected') {
      throw track.status === 'rejected' ? track.reason : fusionResult.reason;
    }
    const optionalErrors = [];
    const getOptional = (result, fallback, name) => {
      if (result.status === 'fulfilled') return result.value;
      optionalErrors.push(`${name}: ${result.reason.message}`);
      return fallback;
    };
    const evidence = getOptional(evidenceResult, [], 'selected evidence');
    const vlm = getOptional(vlmResult, { available: false, result: null }, 'VLM');
    const modalities = getOptional(modalitiesResult, { thermal: {}, radar: {} }, 'modalities');
    renderDetail(track.value, evidence, vlm, modalities, fusionResult.value, track.value.assessment, optionalErrors);
  } catch (error) {
    if (requestId !== state.activeTrackRequest) return;
    $('#detail-content').innerHTML = `<p class="detail-error">Could not load this track: ${escapeHTML(error.message)}</p>`;
  }
}

function showDetail() {
  $('#detail-backdrop').hidden = false;
  $('#detail-panel').classList.add('open');
  $('#detail-panel').setAttribute('aria-hidden', 'false');
  $('#detail-panel').removeAttribute('inert');
  $('#close-detail').focus();
}

function closeDetail() {
  state.activeTrackRequest += 1;
  $('#detail-backdrop').hidden = true;
  $('#detail-panel').classList.remove('open');
  $('#detail-panel').setAttribute('aria-hidden', 'true');
  $('#detail-panel').setAttribute('inert', '');
}

async function newAnalysis() {
  if (state.phase3Input) await stopPhase3Input();
  resetDashboardRun();
}

function resetDashboardRun() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  stopAnalysisTimelineTimer();
  state.runId = null;
  state.cancelPending = false;
  state.phase3Input = null;
  state.run = null;
  state.phaseExecution = {};
  state.selectedStage = 'uploaded';
  state.phaseTrackDetail = null;
  state.selectedPhaseTrackId = null;
  state.activeResultTab = 'detection';
  state.currentFrameIndex = null;
  $('#concurrent-run-warning').hidden = true;
  $('#phase3-panel').hidden = true;
  $('#phase4-panel').hidden = true;
  $('#phase5-panel').hidden = true;
  $('#phase6-panel').hidden = true;
  $('#phase3-preview').removeAttribute('src');
  state.alarmTransitions.clear();
  localStorage.removeItem(RUN_STORAGE_KEY);
  clearRunResults();
  selectResultsTab('detection');
  resetUpload();
  setBusy(false);
  clearGlobalError();
  $('#run-section .run-meta').hidden = true;
  $('#run-id').textContent = '—';
  $('#results-run-id').textContent = '';
  $('#run-stage').textContent = '—';
  $('#run-status-text').textContent = '—';
  $('#run-progress-text').textContent = '0%';
  $('#run-progress').value = 0;
  $('#stop-analysis').hidden = true;
  $('#stop-analysis').disabled = false;
  $('#stop-analysis').textContent = 'Stop analysis';
  $('#start-analysis-flow').hidden = true;
  $('#start-analysis-flow').disabled = true;
  $('#track-rows').innerHTML = '<tr><td colspan="9" class="empty-state">Upload a video to begin a run.</td></tr>';
  $('#track-count-label').textContent = '0';
  $('#pagination-summary').textContent = 'No tracks';
  $('#run-status-badge').textContent = 'Waiting';
  $('#run-status-badge').className = 'status-badge status-pending';
  $('#dashboard-run-status').textContent = 'N/A';
  $('#phase-detail-title').textContent = 'Selected Phase';
  $('#phase-detail-message').textContent = 'Choose a stage in the analysis pipeline to view its controls or results.';
  $('#phase-detail-empty').textContent = '';
  $('#phase-detail-empty').hidden = true;
  renderPipeline({ status: 'idle', stage: 'uploaded' });
}

async function resumeCurrentRun(runIdOverride = null) {
  const runId = runIdOverride || localStorage.getItem(RUN_STORAGE_KEY);
  if (!runId) return;
  state.runId = runId;
  $('#run-id').textContent = runId;
  $('#results-run-id').textContent = `Run ${runId}`;
  $('#run-section').hidden = false;
  try {
    const run = await updateRunStatus(runId);
    if (!run || runId !== state.runId) return;
    restoreRunVideo(run);
    if (run.status === 'completed') await loadResults(runId);
    else if (run.status === 'prepared') {
      $('#input-prepared').textContent = `${(run.input_type || 'Input').toUpperCase()} input is validated and ready. Analysis has not been started.`;
      $('#input-prepared').hidden = false;
      if (run.source_type === 'upload' && ['rgb', 'thermal'].includes(run.input_type)) {
        showPhase3Input({
          source_type: 'upload',
          input_type: run.input_type,
          run_id: run.run_id,
        });
      }
    }
    else if (run.status === 'failed' || run.status === 'cancelled') {
      $('#run-error').textContent = run.error || `This run ${run.status}.`;
      $('#run-error').hidden = false;
    } else await pollRun(runId);
  } catch (error) {
    showGlobalError(`The recent run could not be restored: ${error.message}`);
    $('#run-error').textContent = error.message;
    $('#run-error').hidden = false;
  }
}

function restoreRunVideo(run) {
  if (state.playerRunId === run.run_id) return;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  state.selectedFile = null;
  $('#video-input').value = '';
  state.playerRunId = run.run_id;
  state.inputMetadata = run.input_metadata || {};
  const player = $('#input-video-player');
  player.pause();
  player.src = `/api/runs/${encodeURIComponent(run.run_id)}/video`;
  player.hidden = false;
  player.load();
  $('#video-player-empty').hidden = true;
  $('#video-frame-overlay').hidden = false;
  $('#player-source-status').textContent = 'Stored uploaded video';
  $('#video-info-filename').textContent =
    `${run.source_video_id || 'Video ID unavailable'} · ${runFilename(run)}`;
  $('#video-info-type').textContent = (run.input_type || 'video').toUpperCase();
  renderVideoMetadata(state.inputMetadata);
  player.addEventListener('loadedmetadata', () => {
    updateCurrentVideoFrame();
    void applyPendingAlertEvidence(run.run_id);
  }, { once: true });
}

let publicRegistrySectors = [];
let basecampMap = null;
let basecampMarker = null;
let basecampPinMethod = '';
function registryPinIcon() { return L.divIcon({className:'registry-pin-icon',html:'📍',iconSize:[30,34],iconAnchor:[15,30]}); }
function selectedRegistryLocationMethod() { return document.querySelector('[name="registry-location-source"]:checked')?.value || ''; }
function clearRegistryBasecamp() {
  basecampMarker?.remove();basecampMarker=null;basecampPinMethod='';
  $('#registry-base-lat').value='';$('#registry-base-lon').value='';$('#registry-location-method').value='';$('#registry-resolved-address').value='';$('#registry-location-token').value='';
}
function setBasecampPin(latlng,method) {
  if(!basecampMap||!Number.isFinite(latlng.lat)||!Number.isFinite(latlng.lng))return;
  if(!basecampMarker)basecampMarker=L.marker(latlng,{draggable:false,icon:registryPinIcon()}).addTo(basecampMap);else basecampMarker.setLatLng(latlng);
  basecampPinMethod=method;$('#registry-base-lat').value=latlng.lat.toFixed(7);$('#registry-base-lon').value=latlng.lng.toFixed(7);$('#registry-location-method').value=method;
  if(method!=='ADDRESS_GEOCODING')$('#registry-resolved-address').value=$('#registry-sector-address').value.trim();
  $('#registry-basecamp-status').textContent=`${method==='DEVICE_HARDWARE'?'System location':method==='ADDRESS_GEOCODING'?'Resolved address · verify the 📍 marker':'Manual map selection'} · ${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}.`;
}
function initializeBasecampPicker() {
  const node=$('#registry-basecamp-map');if(!node||!window.L||basecampMap)return;
  basecampMap=L.map(node,{zoomControl:true,scrollWheelZoom:true});
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(basecampMap);
  basecampMap.on('click',event=>{if(selectedRegistryLocationMethod()==='MANUAL_MAP_CLICK')setBasecampPin(event.latlng,'MANUAL_MAP_CLICK');});
  window.setTimeout(()=>basecampMap?.invalidateSize(),0);
}
function centerBasecampPickerOnSelectedSector() {
  const value=$('#registry-sector').value.trim().toLowerCase();const selected=publicRegistrySectors.find(sector=>value===sector.id.toLowerCase()||value===`${sector.name} (${sector.id})`.toLowerCase());
  if(selected?.bounds&&basecampMap&&!basecampMarker)basecampMap.fitBounds([[selected.bounds.south,selected.bounds.west],[selected.bounds.north,selected.bounds.east]],{padding:[12,12],maxZoom:15});
}
async function loadPublicRegistrySectors() {
  const suggestions=$('#registry-sector-options');try{const payload=await api('/api/sector-registry/sectors');publicRegistrySectors=payload.sectors||[];
    suggestions.innerHTML=publicRegistrySectors.map(sector=>'<option value="'+escapeHTML(sector.name+' ('+sector.id+')')+'" label="Covers: '+escapeHTML(sector.coverage||'Coverage not listed')+'"></option>').join('');
  }catch(error){suggestions.replaceChildren();$('#registry-email-status').textContent='HQ sector list could not be loaded. Check that the API is running.';$('#registry-email-status').dataset.state='error';}
}

let registryStep=1;
function setRegistryStep(step){
  registryStep=Math.max(1,Math.min(3,step));
  document.querySelectorAll('[data-reg-step]').forEach((node)=>{node.hidden=Number(node.dataset.regStep)!==registryStep;});
  document.querySelectorAll('[data-reg-step-indicator]').forEach((node)=>{const n=Number(node.dataset.regStepIndicator);node.classList.toggle('active',n===registryStep);node.classList.toggle('complete',n<registryStep);if(n===registryStep)node.setAttribute('aria-current','step');else node.removeAttribute('aria-current');});
  $('#registry-submit-error').hidden=true; $('#registry-view').scrollTo({top:0,behavior:'smooth'});
  if(registryStep===3)window.requestAnimationFrame(()=>{initializeBasecampPicker();basecampMap?.invalidateSize();centerBasecampPickerOnSelectedSector();});
}
function registryStepError(message){const node=$('#registry-submit-error');node.textContent=message;node.hidden=false;}
function registryHeadDetailsValid(){return ['registry-sector','registry-head-name','registry-head-id','registry-head-rank','registry-head-phone','registry-head-email'].every((id)=>document.getElementById(id).value.trim());}
function validateRegistryHeadStep(){
  if(!registryHeadDetailsValid()){registryStepError('Enter the sector, head name, 7-digit ID, rank, phone, and email first.');return false;}
  const headId=$('#registry-head-id').value.trim();
  if(!/^[0-9]{7}$/.test(headId)){registryStepError('Sector-head ID must contain exactly 7 digits.');return false;}
  const phone=$('#registry-head-phone').value.trim().replace(/[\s().-]/g,'');
  if(!/^\+?[1-9][0-9]{7,14}$/.test(phone)){registryStepError('Enter a valid sector-head phone number with country code.');return false;}
  const email=$('#registry-head-email').value.trim();
  if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)){registryStepError('Enter a valid sector-head email address.');return false;}
  const file=$('#registry-head-document').files?.[0];if(!file||file.size>5*1024*1024||!['image/jpeg','image/png','application/pdf'].includes(file.type)){registryStepError('Choose a JPEG, PNG, or PDF sector head work ID up to 5 MiB.');return false;}
  $('#registry-submit-error').hidden=true;return true;
}
function validateRegistryTechnicalStep(){
  const roles=['technical1','technical2'];
  for(const role of roles){
    const name=document.querySelector('[data-reg-member="'+role+'"][data-reg-field="full_name"]').value.trim();
    const memberId=document.querySelector('[data-reg-member="'+role+'"][data-reg-field="member_id"]').value.trim();
    const rank=document.querySelector('[data-reg-member="'+role+'"][data-reg-field="rank"]').value.trim();
    const file=$('#registry-'+(role==='technical1'?'tech1':'tech2')+'-document').files?.[0];
    if(!name||!rank||!/^[0-9]{7}$/.test(memberId)){registryStepError('Enter each technical member’s name, exactly 7-digit ID, and rank.');return false;}
    if(!file||file.size>5*1024*1024||!['image/jpeg','image/png','application/pdf'].includes(file.type)){registryStepError('Choose a JPEG, PNG, or PDF work ID up to 5 MiB for each technical member.');return false;}
  }
  const ids=[$('#registry-head-id').value.trim(),...roles.map((role)=>document.querySelector('[data-reg-member="'+role+'"][data-reg-field="member_id"]').value.trim())];
  if(new Set(ids).size!==ids.length){registryStepError('Member IDs must be unique.');return false;}
  $('#registry-submit-error').hidden=true;return true;
}
function validateRegistryAddressStep(){
  const address=$('#registry-sector-address').value.trim();
  if(!address){registryStepError('Enter the sector address before submission.');return false;}
  const method=selectedRegistryLocationMethod();
  const latitude=Number($('#registry-base-lat').value),longitude=Number($('#registry-base-lon').value);
  if(!['DEVICE_HARDWARE','ADDRESS_GEOCODING','MANUAL_MAP_CLICK'].includes(method)||basecampPinMethod!==method||$('#registry-location-method').value!==method||!Number.isFinite(latitude)||!Number.isFinite(longitude)){registryStepError('Choose a location method and successfully lock its 📍 map pin before submission.');return false;}
  if(method==='ADDRESS_GEOCODING'&&(!$('#registry-resolved-address').value||!$('#registry-location-token').value)){registryStepError('Resolve the current sector address before submitting.');return false;}
  $('#registry-submit-error').hidden=true;return true;
}
async function encodeRegistryDocument(file){
  if(!file||file.size>5*1024*1024)throw new Error('Each member work-ID file must be up to 5 MiB.');
  if(!['image/jpeg','image/png','application/pdf'].includes(file.type))throw new Error('Upload a JPEG, PNG, or PDF work ID.');
  const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('A selected work-ID file could not be read.'));reader.onload=()=>resolve(String(reader.result).split(',',2)[1]||'');reader.readAsDataURL(file);});
  return {filename:file.name,content_type:file.type,document_base64:encoded};
}
async function submitSectorRegistration(event){
  event.preventDefault();if(registryStep!==3||!validateRegistryAddressStep())return;
  const button=$('#registry-submit');button.disabled=true;$('#registry-submit-error').hidden=true;
  try{
    const technicalIds=['technical1','technical2'].map((role)=>document.querySelector('[data-reg-member="'+role+'"][data-reg-field="member_id"]').value.trim());
    if(technicalIds.some((memberId)=>!/^[0-9]{7}$/.test(memberId)))throw new Error('Each technical member ID must contain exactly 7 digits.');
    const members=[{role:'head',full_name:$('#registry-head-name').value.trim(),member_id:$('#registry-head-id').value.trim(),rank:$('#registry-head-rank').value.trim()},
      ...['technical1','technical2'].map((role)=>({role:'technical',full_name:document.querySelector('[data-reg-member="'+role+'"][data-reg-field="full_name"]').value.trim(),member_id:document.querySelector('[data-reg-member="'+role+'"][data-reg-field="member_id"]').value.trim(),rank:document.querySelector('[data-reg-member="'+role+'"][data-reg-field="rank"]').value.trim()}))];
    const documents={head:await encodeRegistryDocument($('#registry-head-document').files?.[0]),technical1:await encodeRegistryDocument($('#registry-tech1-document').files?.[0]),technical2:await encodeRegistryDocument($('#registry-tech2-document').files?.[0])};
    button.textContent='Encrypting and sending…';
    const result=await api('/api/sector-registry/requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sector_id:$('#registry-sector').value,sector_address:$('#registry-sector-address').value.trim(),base_camp_lat:Number($('#registry-base-lat').value),base_camp_lon:Number($('#registry-base-lon').value),chosen_lat:Number($('#registry-base-lat').value),chosen_lon:Number($('#registry-base-lon').value),resolved_address_string:$('#registry-resolved-address').value||$('#registry-sector-address').value.trim(),location_verification_token:$('#registry-location-token').value,location_capture_method:basecampPinMethod,head_phone:$('#registry-head-phone').value.trim(),head_email:$('#registry-head-email').value.trim(),members,documents})});
    $('#registry-submit-status').textContent='Reference: '+result.request_id+'. Your three IDs are encrypted. If approved, they remain available only to the assigned HQ reviewer for up to 30 days; rejected or expired files are purged. If approved, an invitation will be sent to '+$('#registry-head-email').value.trim()+'.';
    $('#registry-form-stepper').hidden=true;$('#sector-registration-form').hidden=true;$('#registry-confirmation').hidden=false;
  }catch(error){registryStepError(error.message);}finally{button.disabled=false;button.textContent='Submit for HQ review';}
}
$('#registry-next-step')?.addEventListener('click',()=>{if(validateRegistryHeadStep())setRegistryStep(2);});
$('#registry-technical-next')?.addEventListener('click',()=>{if(validateRegistryTechnicalStep())setRegistryStep(3);});
$('#registry-sector')?.addEventListener('change',()=>{clearRegistryBasecamp();centerBasecampPickerOnSelectedSector();});
document.querySelectorAll('[name="registry-location-source"]').forEach(radio=>radio.addEventListener('change',()=>{clearRegistryBasecamp();const status=$('#registry-basecamp-status');if(radio.value==='MANUAL_MAP_CLICK'){centerBasecampPickerOnSelectedSector();status.textContent='Manual pin selected. Click the exact base-camp point on the map.';}else if(radio.value==='DEVICE_HARDWARE')status.textContent='Click Detect system location to request device coordinates.';else status.textContent='Click Resolve address to search the entered address.';}));
$('#registry-sector-address')?.addEventListener('input',()=>{if(selectedRegistryLocationMethod()==='ADDRESS_GEOCODING'){clearRegistryBasecamp();$('#registry-basecamp-status').textContent='Address changed. Resolve it again to refresh the 📍 pin.';}});
$('#registry-use-device-location')?.addEventListener('click',()=>{document.querySelector('[name="registry-location-source"][value="DEVICE_HARDWARE"]').checked=true;clearRegistryBasecamp();const status=$('#registry-basecamp-status');if(!navigator.geolocation){status.textContent='This browser does not provide device location. Choose address lookup or manual map pin.';return;}status.textContent='Allow the browser location prompt to detect the device position…';navigator.geolocation.getCurrentPosition(position=>{const point=L.latLng(position.coords.latitude,position.coords.longitude);setBasecampPin(point,'DEVICE_HARDWARE');basecampMap?.setView(point,16);status.textContent+=` · accuracy ±${Math.round(position.coords.accuracy)} m`;},error=>{clearRegistryBasecamp();status.textContent=error.code===1?'Location permission denied. No fallback pin was set; choose address lookup or manual map pin.':'Could not read device location. No fallback pin was set; choose address lookup or manual map pin.';},{enableHighAccuracy:true,timeout:15000,maximumAge:0});});
$('#registry-resolve-address')?.addEventListener('click',async()=>{document.querySelector('[name="registry-location-source"][value="ADDRESS_GEOCODING"]').checked=true;clearRegistryBasecamp();const address=$('#registry-sector-address').value.trim(),button=$('#registry-resolve-address'),status=$('#registry-basecamp-status');if(!address){status.textContent='Enter the sector address before resolving it.';return;}button.disabled=true;status.textContent='Resolving the entered address on the server…';try{const result=await api('/api/sector-registry/geocode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address})});const point=L.latLng(result.latitude,result.longitude);setBasecampPin(point,'ADDRESS_GEOCODING');$('#registry-resolved-address').value=result.resolved_address_string;$('#registry-location-token').value=result.verification_token;basecampMap?.setView(point,16);status.textContent=`Address resolved · ${point.lat.toFixed(6)}, ${point.lng.toFixed(6)}. Verify the 📍 marker before continuing.`;}catch(error){clearRegistryBasecamp();status.textContent=error.message.includes('Address could not be geocoded')?error.message:`${error.message} Address could not be geocoded. Please verify or use alternative capture methods.`;}finally{button.disabled=false;}});
document.querySelectorAll('[data-reg-back]').forEach((button)=>button.addEventListener('click',()=>setRegistryStep(registryStep-1)));

const registryPolygonDrafts = new Map();
const registryReviewMaps = new Map();
function paintRegistryPolygon(id, points) {
  const item=registryReviewMaps.get(id); if(!item)return;
  item.layer.clearLayers();
  const b=item.bounds; if(b)L.rectangle([[b.south,b.west],[b.north,b.east]],{color:'#22b8d5',weight:1,dashArray:'5 5',fillOpacity:.03}).addTo(item.layer);
  if(item.basecamp){
    L.marker(item.basecamp,{icon:registryPinIcon(),interactive:true}).bindTooltip(`📍 Base camp · ${item.basecamp.lat.toFixed(6)}, ${item.basecamp.lng.toFixed(6)}`,{direction:'top'}).addTo(item.layer);
  }
  if(item.drawingMode!=='circle') points.forEach(point=>L.circleMarker(point,{radius:6,color:'#b51669',fillColor:'#fff',fillOpacity:1,weight:3}).addTo(item.layer));
  if(item.drawingMode==='polygon' && points.length>=2 && points.length<3) L.polyline(points,{color:'#e33d9b',weight:3,dashArray:'6 4',className:'registry-draft-line'}).addTo(item.layer);
  if(item.circleCenter && (!item.basecamp || item.circleCenter.distanceTo(item.basecamp)>1))L.circleMarker(item.circleCenter,{radius:6,color:'#fff',weight:2,fillColor:'#e33d9b',fillOpacity:1}).bindTooltip('Circle center',{direction:'top'}).addTo(item.layer);
  if(points.length>=3){const polygon=L.polygon(points,{color:'#e33d9b',weight:3,fillColor:'#e33d9b',fillOpacity:.18}).addTo(item.layer);polygon.bindTooltip(`<strong>${escapeHTML(item.name)}</strong><br>Sector boundary`,{permanent:true,direction:'center',className:'sector-map-label'}).openTooltip();const center=polygon.getBounds().getCenter();const card=document.querySelector(`[data-registry-request="${CSS.escape(id)}"]`);const info=card?.querySelector('[data-registry-map-status]');if(info)info.textContent=`${points.length} boundary points · center ${center.lat.toFixed(5)}, ${center.lng.toFixed(5)} · area calculated on save`;}
}
function circleBoundaryPoints(center, radiusKm, steps=72) {
  const earthRadiusKm=6371.0088, angularDistance=radiusKm/earthRadiusKm;
  const latitude=Math.PI*center.lat/180, longitude=Math.PI*center.lng/180;
  return Array.from({length:steps},(_,index)=>{
    const bearing=2*Math.PI*index/steps;
    const targetLatitude=Math.asin(Math.sin(latitude)*Math.cos(angularDistance)+Math.cos(latitude)*Math.sin(angularDistance)*Math.cos(bearing));
    const targetLongitude=longitude+Math.atan2(Math.sin(bearing)*Math.sin(angularDistance)*Math.cos(latitude),Math.cos(angularDistance)-Math.sin(latitude)*Math.sin(targetLatitude));
    return [targetLatitude*180/Math.PI,targetLongitude*180/Math.PI];
  });
}
function initializeRegistryReviewMaps(requests){
  for(const entry of registryReviewMaps.values())entry.map.remove();
  registryReviewMaps.clear();
  requests.forEach(request=>{
    const node=document.getElementById(`registry-map-${request.request_id}`);
    if(!node||!window.L)return;
    const sector=(window.hqSectorPayload?.sectors||[]).find(item=>item.id===request.sector_id);
    const b=sector?.bounds||null;
    const pin=Number.isFinite(Number(request.base_camp_lat))&&request.base_camp_lat!==null&&request.base_camp_lon!==null
      ? L.latLng(Number(request.base_camp_lat),Number(request.base_camp_lon)) : null;
    const map=L.map(node,{zoomControl:true,scrollWheelZoom:true});
    if(pin)map.setView(pin,14);else if(b)map.fitBounds([[b.south,b.west],[b.north,b.east]]);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);
    const layer=L.layerGroup().addTo(map);
    const item={map,layer,bounds:b,drawing:false,drawingMode:'none',name:request.sector_name,basecamp:pin,basecampVerified:Boolean(pin)};
    registryReviewMaps.set(request.request_id,item);
    paintRegistryPolygon(request.request_id,[]);
    map.on('click',event=>{
      if(item.drawingMode!=='polygon')return;
      const points=registryPolygonDrafts.get(request.request_id)||[];points.push([event.latlng.lat,event.latlng.lng]);registryPolygonDrafts.set(request.request_id,points);paintRegistryPolygon(request.request_id,points);
      const info=document.querySelector(`[data-registry-request="${CSS.escape(request.request_id)}"] [data-registry-map-status]`);
      if(info)info.textContent=points.length<3?`${points.length} point(s) set. Click ${3-points.length} more point(s); line preview shown.`:`${points.length} points set. Continue clicking to refine, then choose Finish polygon.`;
    });
  });
}

async function loadHeadquartersRegistry() {
  const status=$('#registry-review-status'),list=$('#registry-review-list');if(!list)return;
  try{
    const [payload,sectors]=await Promise.all([api('/api/headquarters/registry'),api('/api/headquarters/sectors')]);window.hqSectorPayload=sectors;
    status.textContent=payload.requests.length?payload.requests.length+' request(s) assigned to this administrator.':'No pending requests are assigned to this administrator.';registryPolygonDrafts.clear();
    list.innerHTML=payload.requests.map(request=>'<article class="registry-review-card" data-registry-request="'+escapeHTML(request.request_id)+'"><div class="registry-review-detail-panel">'+
      '<div class="section-heading"><div><h3>'+escapeHTML(request.sector_name)+' · '+escapeHTML(request.sector_id)+'</h3><small>'+escapeHTML(request.request_id)+' · Submitted '+escapeHTML(request.submitted_at)+'</small></div><strong class="registry-pending">PENDING</strong></div>'+ 
      '<div class="registry-review-members">'+request.members.map(member=>{const documentKey=member.member_role==='head'?'head':'technical'+(Number(member.member_index)+1);return '<div><strong>'+escapeHTML(member.member_role==='head'?'Sector head':'Technical member '+(Number(member.member_index)+1))+'</strong><span>'+escapeHTML(member.full_name)+'</span><small>'+escapeHTML(member.rank)+' · ID '+escapeHTML(member.member_id)+'</small>'+(request.documents?.[documentKey]?'<button class="button button-secondary" type="button" data-registry-document="'+escapeHTML(request.request_id)+'" data-registry-member="'+documentKey+'">View '+(documentKey==='head'?'head':documentKey==='technical1'?'technical member 1':'technical member 2')+' ID</button>':'')+'</div>';}).join('')+'</div>'+ 
      '<div class="registry-review-address-card"><strong>📍 Sector address &amp; contact</strong><p>'+escapeHTML(request.sector_address||'Address not provided')+'</p><small>Covered villages / localities: '+escapeHTML((window.hqSectorPayload?.sectors||[]).find(sector=>sector.id===request.sector_id)?.coverage||'Not provided')+'</small><small>'+(Number.isFinite(Number(request.base_camp_lat))&&request.base_camp_lat!==null&&request.base_camp_lon!==null?'Map pin: '+Number(request.base_camp_lat).toFixed(6)+', '+Number(request.base_camp_lon).toFixed(6):'No coordinate pin supplied')+' · Capture method: '+escapeHTML(request.location_capture_method||'Not recorded')+'</small><small>Resolved address: '+escapeHTML(request.resolved_address_string||'Not recorded')+'</small><small>Head phone: '+escapeHTML(request.head_phone||'Not provided')+' · Sector-head email: '+escapeHTML(request.head_email||'Not provided')+'</small><small>Member work IDs are encrypted and access-audited.</small></div>'+ 
      '<div class="input-actions registry-review-decisions"><button class="button button-primary" type="button" data-registry-decision="approved" data-request-id="'+escapeHTML(request.request_id)+'" disabled>Approve &amp; Allocate</button><button class="button button-danger" type="button" data-registry-decision="rejected" data-request-id="'+escapeHTML(request.request_id)+'">Reject</button></div></div>'+
      '<section class="registry-boundary-editor"><div class="section-heading"><div><strong>Allocate sector boundary</strong><small>The 📍 intake pin is the reference point for this allocation.</small></div><div class="input-actions"><span class="registry-fixed-pin-note">📍 Intake pin is fixed</span><button class="button button-secondary" type="button" data-registry-draw="'+escapeHTML(request.request_id)+'">Draw polygon</button><label class="registry-radius-control">Radius (km)<input class="text-input" type="number" min="0.1" max="100" step="0.1" value="5" data-registry-radius></label><button class="button button-secondary" type="button" data-registry-circle="'+escapeHTML(request.request_id)+'">Draw radius</button><button class="button button-secondary" type="button" data-registry-finish="'+escapeHTML(request.request_id)+'">Finish polygon</button><button class="button button-secondary" type="button" data-registry-clear="'+escapeHTML(request.request_id)+'">Clear boundary</button></div></div><div id="registry-map-'+escapeHTML(request.request_id)+'" class="registry-boundary-map" aria-label="Draw sector boundary"></div><small data-registry-map-status>Use Draw polygon or Draw radius around the captured intake pin.</small></section>'+ 
      '</article>').join('');
    initializeRegistryReviewMaps(payload.requests);
    window.setTimeout(() => registryReviewMaps.forEach((item) => item.map.invalidateSize()), 0);
  }catch(error){status.textContent='Could not load HQ review requests: '+error.message;}
}

async function loadHeadquartersPasswordResets() {
  const list = $('#password-reset-list');
  if (!list) return;
  try {
    const payload = await api('/api/headquarters/password-resets');
    list.innerHTML = payload.requests.length ? payload.requests.map((item) =>
      '<article class="registry-review-card" data-reset-id="' + escapeHTML(item.reset_id) + '"><strong>' +
      escapeHTML(item.sector_number) + ' · ' + escapeHTML(item.head_member_id) + '</strong><p class="input-help">Head email request · ' +
      escapeHTML(item.created_at) + '</p><div class="input-actions"><button class="button button-primary" type="button" data-reset-decision="approved" data-reset-id="' +
      escapeHTML(item.reset_id) + '">Authorize reset</button><button class="button button-danger" type="button" data-reset-decision="rejected" data-reset-id="' +
      escapeHTML(item.reset_id) + '">Reject</button></div></article>').join('') : '<p class="input-help">No sector password-reset requests are waiting for HQ.</p>';
  } catch (error) { list.textContent = 'Could not load password-reset requests: ' + error.message; }
}

let oneTimeCredentialTimer = null;
function showRegistryDecisionStatus(message, accountName = '', password = '') {
  const status = $('#registry-credential-result');
  window.clearTimeout(oneTimeCredentialTimer);
  status.replaceChildren();
  const notice = document.createElement('p');
  notice.textContent = message;
  status.append(notice);
  if (password) {
    const label = document.createElement('label');
    label.className = 'one-time-credential-label';
    label.textContent = `One-time temporary password for ${accountName || 'the sector account'} — copy it now; it will not be shown again.`;
    const row = document.createElement('div');
    row.className = 'one-time-credential-row';
    const value = document.createElement('input');
    value.type = 'text';
    value.readOnly = true;
    value.autocomplete = 'off';
    value.spellcheck = false;
    value.value = password;
    value.setAttribute('aria-label', 'One-time temporary password');
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'button button-secondary';
    copy.textContent = 'Copy';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(value.value); copy.textContent = 'Copied'; }
      catch { value.focus(); value.select(); copy.textContent = 'Select and copy'; }
    }, { once: true });
    row.append(value, copy);
    status.append(label, row);
    oneTimeCredentialTimer = window.setTimeout(() => {
      value.value = '';
      status.replaceChildren();
      const expired = document.createElement('p');
      expired.textContent = 'One-time credential display cleared. If it was not recorded safely, start a new HQ-authorized reset.';
      status.append(expired);
    }, 90_000);
  }
}

async function reviewPasswordReset(resetId, decision) {
  if (decision === 'rejected' && !window.confirm('Reject this verified sector-head password-reset request?')) return;
  const status = $('#registry-credential-result');
  try {
    const result = await api('/api/headquarters/password-reset/' + encodeURIComponent(resetId) + '/decision', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    const password = result.delivery?.temporary_password;
    showRegistryDecisionStatus(result.status === 'completed'
      ? (result.delivery?.sent ? 'Reset authorized. Invitation was emailed to the registered sector-head address.' : 'Reset authorized. Email was not sent; securely hand off this one-time credential to the sector head.')
      : 'Password-reset request rejected.', result.account?.username || '', password || '');
    await loadHeadquartersPasswordResets();
  } catch (error) { status.textContent = error.message; }
}

async function reviewSectorRequest(requestId, decision) {
  if (decision === 'rejected' && !window.confirm('Reject this sector registration? The uploaded work ID will be purged.')) return;
  const status = $('#registry-credential-result');
  try {
    if (decision === 'approved' && !registryReviewMaps.get(requestId)?.basecamp) throw new Error('This request has no captured base-camp pin. Ask the sector head to resubmit using one of the three location methods.');
    if (decision === 'approved' && (registryPolygonDrafts.get(requestId) || []).length < 3) throw new Error('Draw and finish a boundary or allocate a radius around the confirmed base-camp pin before approval.');
    const result = await api('/api/headquarters/registry/' + encodeURIComponent(requestId) + '/decision', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, geometry: registryPolygonDrafts.get(requestId) || [] }),
    });
    const password = result.delivery?.temporary_password;
    showRegistryDecisionStatus(result.decision === 'approved'
      ? (result.delivery?.sent ? 'Approved. Invitation was emailed to the registered sector-head address.' : 'Approved. Email was not sent; securely hand off this one-time credential to the sector head.')
      : 'Rejected. The work-ID file has been purged.', result.account?.username || '', password || '');
    await loadHeadquartersRegistry();
  } catch (error) { status.textContent = error.message; }
}

async function openRegistryWorkId(requestId, memberKey = 'head') {
  const status = $('#registry-review-status');
  const dialog = $('#registry-id-preview');
  const previewStatus = $('#registry-id-preview-status');
  const previewContent = $('#registry-id-preview-content');
  const title = $('#registry-id-preview-title');
  if (!dialog || !previewStatus || !previewContent || !title) return;
  const labels = { head: 'Sector head work ID', technical1: 'Technical member 1 work ID', technical2: 'Technical member 2 work ID' };
  title.textContent = labels[memberKey] || 'Work ID preview';
  previewStatus.textContent = 'Loading secure document…';
  previewContent.replaceChildren();
  dialog.showModal();
  try {
    const response = await fetch('/api/headquarters/registry/' + encodeURIComponent(requestId) + '/work-id/' + encodeURIComponent(memberKey), { credentials: 'same-origin' });
    if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.message || 'Work ID could not be opened.'); }
    const blob = await response.blob();
    const contentType = (response.headers.get('Content-Type') || blob.type).split(';')[0].toLowerCase();
    if (!['image/jpeg', 'image/png', 'application/pdf'].includes(contentType)) throw new Error('This work ID has an unsupported file type.');
    const url = URL.createObjectURL(blob);
    dialog.dataset.previewUrl = url;
    if (contentType === 'application/pdf') {
      const frame = document.createElement('iframe');
      frame.className = 'registry-id-preview-frame';
      frame.title = title.textContent;
      frame.src = url;
      previewContent.append(frame);
    } else {
      const image = document.createElement('img');
      image.className = 'registry-id-preview-image';
      image.alt = title.textContent;
      image.src = url;
      previewContent.append(image);
    }
    previewStatus.textContent = 'Secure preview. This access is recorded in the HQ audit log.';
    status.textContent = 'Document view recorded in the HQ audit log.';
  } catch (error) {
    previewStatus.textContent = error.message;
    status.textContent = error.message;
  }
}

$('#close-registry-id-preview')?.addEventListener('click', () => $('#registry-id-preview')?.close());
$('#registry-id-preview')?.addEventListener('click', (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$('#registry-id-preview')?.addEventListener('close', (event) => {
  const dialog = event.currentTarget;
  if (dialog.dataset.previewUrl) URL.revokeObjectURL(dialog.dataset.previewUrl);
  delete dialog.dataset.previewUrl;
  $('#registry-id-preview-content')?.replaceChildren();
  $('#registry-id-preview-status').textContent = '';
});

$('#sector-registration-form')?.addEventListener('submit', submitSectorRegistration);
$('#reload-registry-requests')?.addEventListener('click', () => void loadHeadquartersRegistry());
$('#reload-hq-drones')?.addEventListener('click', () => void loadHeadquartersDrones());
$('#reload-hq-users')?.addEventListener('click', () => void loadHeadquartersUsers());
$('#hq-users-pagination')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-hq-users-page]');
  if (!button || button.disabled) return;
  hqUsersPage += button.dataset.hqUsersPage === 'next' ? 1 : -1;
  renderHeadquartersUsersList(window.hqApprovedSectors || [], window.hqSharedAccounts || []);
});
let hqSectorDetailsOpener = null;
const hqSectorDetailsDialog = $('#hq-sector-details-dialog');
$('#close-hq-sector-details')?.addEventListener('click', () => hqSectorDetailsDialog?.close());
hqSectorDetailsDialog?.addEventListener('click', (event) => {
  if (event.target === hqSectorDetailsDialog) hqSectorDetailsDialog.close();
});
hqSectorDetailsDialog?.addEventListener('close', () => {
  if (hqSectorDetailsOpener?.isConnected) hqSectorDetailsOpener.focus({ preventScroll: true });
  hqSectorDetailsOpener = null;
});
let hqPersonnelSector = null;
let hqPersonnelMember = null;
let hqPersonnelDocumentOpener = null;

function renderPersonnelHistory(history) {
  if (!history.length) return '<p class="empty-state">No previous personnel changes are recorded for this sector.</p>';
  return history.map((entry) => {
    const role = entry.member_role === 'head' ? 'Sector head' : `Technical member ${Number(entry.member_index)}`;
    return '<article class="hq-personnel-history-item"><strong>' + escapeHTML(role) + ' · ' + escapeHTML(entry.full_name) + '</strong><small>' +
      escapeHTML(entry.rank) + ' · Changed ' + escapeHTML(entry.changed_at) + ' by HQ actor ' + escapeHTML(entry.changed_by) + '</small>' +
      (entry.document_available ? '<button class="button button-secondary" type="button" data-personnel-history-document="' + escapeHTML(entry.history_id) + '">View prior work ID</button>' : '<small>Prior work ID unavailable or retention period ended</small>') +
      '</article>';
  }).join('');
}

async function showPersonnelChangeForm(sector, member) {
  hqPersonnelSector = sector;
  hqPersonnelMember = member;
  const isHead = member.member_role === 'head';
  const roleLabel = isHead ? 'sector head' : `technical member ${Number(member.member_index)}`;
  $('#hq-sector-details-title').textContent = `Change ${roleLabel} · ${sector.sector_name}`;
  const content = $('#hq-sector-details-content');
  content.innerHTML = '<p class="input-help" role="status">Loading personnel history…</p>';
  let history = [];
  let historyError = '';
  try {
    const result = await api(`/api/headquarters/sectors/${encodeURIComponent(sector.sector_id)}/personnel-history`);
    history = Array.isArray(result.history) ? result.history : [];
  } catch (error) {
    historyError = `History could not be loaded: ${error.message}`;
  }
  content.innerHTML = `
    <form id="hq-personnel-form" class="hq-personnel-form" novalidate>
      <p class="input-help">Replace the current roster member. A required JPEG, PNG, or PDF work-ID document is encrypted before storage. The sector identity and boundary stay unchanged.</p>
      ${isHead ? '<p class="input-help">Head email and phone update roster contact details only. The shared-login email and password are unchanged.</p>' : ''}
      <label for="hq-personnel-name">Name<input id="hq-personnel-name" class="text-input" maxlength="120" required value="${escapeHTML(member.full_name || '')}"></label>
      <label for="hq-personnel-id">Work ID (exactly 7 digits)<input id="hq-personnel-id" class="text-input" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required value="${escapeHTML(member.member_id || '')}"></label>
      <label for="hq-personnel-rank">Rank<input id="hq-personnel-rank" class="text-input" maxlength="80" required value="${escapeHTML(member.rank || '')}"></label>
      ${isHead ? `<label for="hq-personnel-phone">Sector-head phone<input id="hq-personnel-phone" class="text-input" type="tel" maxlength="32" value="${escapeHTML(sector.head_phone || '')}"></label>
      <label for="hq-personnel-email">Sector-head contact email<input id="hq-personnel-email" class="text-input" type="email" maxlength="254" value="${escapeHTML(sector.head_email || '')}"></label>` : ''}
      <label for="hq-personnel-work-id-document">Replacement work-ID document<input id="hq-personnel-work-id-document" class="text-input" type="file" accept="image/jpeg,image/png,application/pdf" required></label>
      <p class="input-help">JPEG, PNG, or PDF; maximum 5 MiB. Access is restricted and audited. Passwords are not requested or changed.</p>
      <p id="hq-personnel-form-status" class="input-help" role="status" aria-live="polite">${escapeHTML(historyError)}</p>
      <div class="input-actions"><button class="button button-primary" type="submit">Save personnel change</button><button class="button button-secondary" type="button" data-personnel-back>Back to sector details</button></div>
    </form>
    <section class="hq-personnel-history" aria-labelledby="hq-personnel-history-title"><h3 id="hq-personnel-history-title">Previous roster history</h3><div class="hq-personnel-history-list">${renderPersonnelHistory(history)}</div></section>`;
  $('#hq-personnel-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = $('#hq-personnel-form-status');
    const submit = event.currentTarget.querySelector('[type="submit"]');
    const file = $('#hq-personnel-work-id-document').files?.[0];
    const id = $('#hq-personnel-id').value.trim();
    if (!/^[0-9]{7}$/.test(id)) {
      status.textContent = 'Work ID must contain exactly 7 digits.';
      return;
    }
    if (!file) {
      status.textContent = 'Choose the replacement member’s work-ID document.';
      return;
    }
    submit.disabled = true;
    status.textContent = 'Encrypting document and saving the roster update…';
    try {
      const document = await encodeRegistryDocument(file);
      const payload = {
        member_role: member.member_role,
        member_index: Number(member.member_index),
        full_name: $('#hq-personnel-name').value.trim(),
        member_id: id,
        rank: $('#hq-personnel-rank').value.trim(),
        document,
      };
      if (isHead) {
        payload.head_phone = $('#hq-personnel-phone').value.trim();
        payload.head_email = $('#hq-personnel-email').value.trim();
      }
      const result = await api(`/api/headquarters/sectors/${encodeURIComponent(sector.sector_id)}/personnel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await loadHeadquartersUsers();
      const updatedSector = (window.hqApprovedSectors || []).find((item) => String(item.sector_id).toUpperCase() === String(sector.sector_id).toUpperCase());
      if (updatedSector) {
        const account = (window.hqSharedAccounts || []).find((item) => String(item.sector_number || '').toUpperCase() === String(sector.sector_id).toUpperCase());
        $('#hq-sector-details-content').innerHTML = renderHeadquartersSectorDetails(updatedSector, account);
        $('#hq-sector-details-title').textContent = `${updatedSector.sector_name} · ${updatedSector.sector_id}`;
      }
      $('#hq-users-status').textContent = `Personnel change saved for ${result.sector_id}. Previous roster details were retained; shared login unchanged.`;
    } catch (error) {
      status.textContent = `Could not save the personnel change: ${error.message}`;
      submit.disabled = false;
    }
  });
}

async function openPersonnelHistoryDocument(historyId, opener) {
  const dialog = $('#hq-personnel-document-preview');
  const status = $('#hq-personnel-document-status');
  const content = $('#hq-personnel-document-content');
  hqPersonnelDocumentOpener = opener;
  content.replaceChildren();
  status.textContent = 'Loading secure historical document…';
  dialog.showModal();
  try {
    const response = await fetch(`/api/headquarters/personnel-history/${encodeURIComponent(historyId)}/work-id`, { credentials: 'same-origin' });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || 'Historical work ID could not be opened.');
    }
    const blob = await response.blob();
    const type = (response.headers.get('Content-Type') || blob.type).split(';')[0].toLowerCase();
    if (!['image/jpeg', 'image/png', 'application/pdf'].includes(type)) throw new Error('This work ID has an unsupported file type.');
    const url = URL.createObjectURL(blob);
    dialog.dataset.previewUrl = url;
    if (type === 'application/pdf') {
      const frame = document.createElement('iframe');
      frame.className = 'registry-id-preview-frame';
      frame.title = 'Historical work-ID preview';
      frame.src = url;
      content.append(frame);
    } else {
      const image = document.createElement('img');
      image.className = 'registry-id-preview-image';
      image.alt = 'Historical work-ID document';
      image.src = url;
      content.append(image);
    }
    status.textContent = 'Secure preview. This access is recorded in the HQ audit log.';
  } catch (error) {
    status.textContent = error.message;
  }
}

$('#close-hq-personnel-document')?.addEventListener('click', () => $('#hq-personnel-document-preview')?.close());
$('#hq-personnel-document-preview')?.addEventListener('click', (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});
$('#hq-personnel-document-preview')?.addEventListener('close', (event) => {
  const dialog = event.currentTarget;
  if (dialog.dataset.previewUrl) URL.revokeObjectURL(dialog.dataset.previewUrl);
  delete dialog.dataset.previewUrl;
  $('#hq-personnel-document-content')?.replaceChildren();
  if (hqPersonnelDocumentOpener?.isConnected) hqPersonnelDocumentOpener.focus({ preventScroll: true });
  hqPersonnelDocumentOpener = null;
});

$('#hq-users-list')?.addEventListener('click', async (event) => {
  const doc = event.target.closest('[data-registry-document]');
  if (doc) { void openRegistryWorkId(doc.dataset.registryDocument, doc.dataset.registryMember || 'head'); return; }
  const detailsButton = event.target.closest('[data-view-sector-details]');
  if (detailsButton) {
    const sectorId = detailsButton.dataset.viewSectorDetails;
    const sector = (window.hqApprovedSectors || []).find((item) => String(item.sector_id).toUpperCase() === String(sectorId).toUpperCase());
    if (!sector) {
      $('#hq-users-status').textContent = `Could not open details for ${sectorId}: sector data is no longer available. Refresh the list and try again.`;
      return;
    }
    const account = (window.hqSharedAccounts || []).find((item) => String(item.sector_number || '').toUpperCase() === String(sector.sector_id || '').toUpperCase());
    $('#hq-sector-details-title').textContent = `${sector.sector_name} · ${sector.sector_id}`;
    $('#hq-sector-details-content').innerHTML = renderHeadquartersSectorDetails(sector, account);
    hqSectorDetailsOpener = detailsButton;
    hqSectorDetailsDialog.showModal();
    $('#close-hq-sector-details').focus();
    return;
  }
  const replaceButton = event.target.closest('[data-replace-personnel]');
  if (replaceButton) {
    const sector = (window.hqApprovedSectors || []).find((item) => String(item.sector_id).toUpperCase() === String(replaceButton.dataset.replacePersonnel).toUpperCase());
    const member = sector?.members?.find((item) => item.member_role === replaceButton.dataset.memberRole && Number(item.member_index) === Number(replaceButton.dataset.memberIndex));
    if (!sector || !member) {
      $('#hq-users-status').textContent = 'Personnel roster changed. Refresh the approved-sector list and try again.';
      return;
    }
    await showPersonnelChangeForm(sector, member);
    $('#hq-personnel-name')?.focus();
    return;
  }
  const button = event.target.closest('[data-decommission-sector]');
  if (!button) return;
  const sectorId = button.dataset.decommissionSector;
  const reason = window.prompt('Reason for decommissioning this sector (5 to 500 characters):');
  if (reason === null) return;
  if (reason.trim().length < 5 || reason.trim().length > 500) {
    $('#hq-users-status').textContent = 'Enter a reason between 5 and 500 characters.';
    return;
  }
  if (!window.confirm(`Decommission ${sectorId}? This disables the shared login, logs out current sessions, hides the area from active maps, and revokes its drone authorizations. Historical records will remain available.`)) return;
  button.disabled = true;
  try {
    const result = await api(`/api/headquarters/sectors/${encodeURIComponent(sectorId)}/decommission`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: reason.trim() }),
    });
    $('#hq-sector-details-content')?.addEventListener('click', (event) => {
      if (event.target.closest('[data-personnel-back]')) {
        const sector = hqPersonnelSector;
        const account = (window.hqSharedAccounts || []).find((item) => String(item.sector_number || '').toUpperCase() === String(sector?.sector_id || '').toUpperCase());
        const updatedSector = (window.hqApprovedSectors || []).find((item) => String(item.sector_id).toUpperCase() === String(sector?.sector_id || '').toUpperCase()) || sector;
        if (updatedSector) {
          $('#hq-sector-details-title').textContent = `${updatedSector.sector_name} · ${updatedSector.sector_id}`;
          $('#hq-sector-details-content').innerHTML = renderHeadquartersSectorDetails(updatedSector, account);
        }
        return;
      }
      const documentButton = event.target.closest('[data-personnel-history-document]');
      if (documentButton) void openPersonnelHistoryDocument(documentButton.dataset.personnelHistoryDocument, documentButton);
    });
    await Promise.all([loadHeadquartersUsers(), loadHeadquartersDashboard(), loadHeadquartersSectors(), loadHeadquartersDrones()]);
    $('#hq-users-status').textContent = result.already_decommissioned ? `${sectorId} was already decommissioned; account lock was reconciled.` : `${sectorId} decommissioned. Its shared login and sessions are disabled.`;
  } catch (error) {
    $('#hq-users-status').textContent = `Could not decommission ${sectorId}: ${error.message}`;
    button.disabled = false;
  }
});
$('#registry-review-list')?.addEventListener('click', async (event) => {
  const doc = event.target.closest('[data-registry-document]');
  if (doc) { void openRegistryWorkId(doc.dataset.registryDocument, doc.dataset.registryMember || 'head'); return; }
  const draw=event.target.closest('[data-registry-draw]'); if(draw){const id=draw.dataset.registryDraw,item=registryReviewMaps.get(id);if(item){item.drawing=true;item.drawingMode='polygon';item.circleCenter=null;item.map.getContainer().classList.add('is-drawing');registryPolygonDrafts.set(id,[]);paintRegistryPolygon(id,[]);const info=draw.closest('[data-registry-request]')?.querySelector('[data-registry-map-status]');if(info)info.textContent='Drawing enabled: click at least three map points. A line preview appears after point two.';}const approve=draw.closest('[data-registry-request]')?.querySelector('[data-registry-decision="approved"]');if(approve)approve.disabled=true;return;}
  const circle=event.target.closest('[data-registry-circle]'); if(circle){const id=circle.dataset.registryCircle,item=registryReviewMaps.get(id),card=circle.closest('[data-registry-request]'),radius=Number(card?.querySelector('[data-registry-radius]')?.value);if(!Number.isFinite(radius)||radius<0.1||radius>100){const info=card?.querySelector('[data-registry-map-status]');if(info)info.textContent='Enter a radius from 0.1 to 100 km.';return;}if(!item?.basecamp){const info=card?.querySelector('[data-registry-map-status]');if(info)info.textContent='This request has no captured 📍 pin; it must be resubmitted before area allocation.';return;}const points=circleBoundaryPoints(item.basecamp,radius);registryPolygonDrafts.set(id,points);item.drawing=false;item.drawingMode='circle';item.circleCenter=item.basecamp;item.map.getContainer().classList.remove('is-drawing');paintRegistryPolygon(id,points);item.drawingMode='none';const approve=card?.querySelector('[data-registry-decision="approved"]');if(approve)approve.disabled=false;const info=card?.querySelector('[data-registry-map-status]');if(info)info.textContent=`${radius} km radius drawn around the 📍 base-camp pin (${item.basecamp.lat.toFixed(6)}, ${item.basecamp.lng.toFixed(6)}). Ready to allocate.`;return;}
  const finish=event.target.closest('[data-registry-finish]'); if(finish){const id=finish.dataset.registryFinish,item=registryReviewMaps.get(id),points=registryPolygonDrafts.get(id)||[];if(item){item.drawing=false;item.drawingMode='none';item.map.getContainer().classList.remove('is-drawing');}if(points.length<3){const info=finish.closest('[data-registry-request]')?.querySelector('[data-registry-map-status]');if(info)info.textContent='Add at least three boundary points before finishing.';return;}const card=finish.closest('[data-registry-request]');const approve=card?.querySelector('[data-registry-decision="approved"]');if(approve)approve.disabled=false;$('#registry-review-status').textContent='Boundary ready to save with approval.';return;}
  const clear=event.target.closest('[data-registry-clear]');if(clear){const id=clear.dataset.registryClear;registryPolygonDrafts.delete(id);const item=registryReviewMaps.get(id);if(item){item.drawing=false;item.drawingMode='none';item.circleCenter=null;item.map.getContainer().classList.remove('is-drawing');paintRegistryPolygon(id,[]);}const card=clear.closest('[data-registry-request]');const approve=card?.querySelector('[data-registry-decision="approved"]');if(approve)approve.disabled=true;const info=card?.querySelector('[data-registry-map-status]');if(info)info.textContent='Boundary cleared. The 📍 base-camp pin is kept; draw a polygon or radius again.';return;}
  const decision = event.target.closest('[data-registry-decision]');
  if (decision) { void reviewSectorRequest(decision.dataset.requestId, decision.dataset.registryDecision); return; }
  const reset = event.target.closest('[data-reset-decision]');
  if (reset) void reviewPasswordReset(reset.dataset.resetId, reset.dataset.resetDecision);
});

let sectorHomeMap = null;
let sectorHomeLayer = null;
async function loadSectorDashboard(skipLocationRefresh = false) {
  const status = $('#sector-home-bounds');
  try {
    const payload = await api('/api/sector/dashboard');
    const sector = payload.sector;
    $('#sector-home-name').textContent = sector.name;
    $('#sector-home-identifier').textContent = `Shared account: ${payload.username} · Assigned sector: ${sector.id}`;
    $('#sector-dashboard-area-name').textContent = sector.name;
    $('#sector-welcome-heading').textContent = `Welcome, ${sector.name}`;
    const summary = payload.summary || {};
    const showMetric = (selector, value) => {
      $(selector).textContent = value === null || value === undefined ? 'Not available' : formatNumber(value);
    };
    showMetric('#sector-stat-devices', summary.authorized_devices);
    showMetric('#sector-stat-tracks', summary.recent_tracks);
    showMetric('#sector-stat-alerts', summary.review_alerts);
    showMetric('#sector-stat-completed', summary.completed_analyses);
    $('#sector-dashboard-summary-status').textContent = summary.trend_available
      ? `Sector-scoped reports · ${summary.window_start} to ${summary.window_end}.`
      : 'Daily trends are not available because no completed, readable run reports were found.';
    $('#sector-dashboard-detection-trend').replaceChildren(
      createTrendChart(summary.daily_detection_trend, 'detections', 'Daily Drone / Bird / Unknown detections', summary.trend_available),
    );
    $('#sector-dashboard-alert-trend').replaceChildren(
      createTrendChart(summary.daily_alert_trend, 'alerts', 'Daily review-alert indicators', summary.trend_available),
    );
    const activity = $('#sector-dashboard-activity');
    activity.replaceChildren();
    if (!Array.isArray(summary.recent_activity) || !summary.recent_activity.length) {
      const empty = document.createElement('p');
      empty.className = 'empty-state';
      empty.textContent = 'No analysis activity is available for this sector account.';
      activity.append(empty);
    } else {
      for (const run of summary.recent_activity) {
        const item = document.createElement('article');
        item.className = 'dashboard-activity-item';
        const title = document.createElement('strong');
        title.textContent = `${String(run.input_type || 'Analysis').toUpperCase()} · ${String(run.status || 'unknown').toUpperCase()}`;
        const time = document.createElement('small');
        time.textContent = run.completed_at || run.created_at || 'Time unavailable';
        const open = document.createElement('a');
        open.href = `/results/${encodeURIComponent(run.run_id)}`;
        open.textContent = 'Open run';
        open.addEventListener('click', (event) => {
          event.preventDefault();
          void navigateTo(open.href);
        });
        item.append(title, time, open);
        activity.append(item);
      }
    }
    const mapStatus = $('#sector-dashboard-area-status');
    mapStatus.textContent = sector.geometry
      ? 'Assigned boundary loaded from saved Headquarters-approved geometry.'
      : 'Assigned area is available from configured sector bounds; no saved polygon is available.';

    if (state.route?.page === 'sector-dashboard') {
      let gpsAssessments = [];
      let gpsStatus = '';
      try {
        const gps = await api('/api/authorization/trajectory/latest');
        gpsAssessments = gps.assessments || [];
      } catch (error) {
        gpsStatus = ` Recent GPS track assessments are unavailable: ${error.message}`;
      }
      const b = sector.bounds;
      status.textContent = `Showing ${payload.sectors?.length || 1} approved sector boundary/boundaries and ${gpsAssessments.length} recent GPS track(s). Pink is your assigned sector; yellow shows neighboring approved sectors.${gpsStatus}`;
      const mapNode = $('#sector-home-map');
      const fallback = $('#sector-home-map-fallback');
      if (!window.L) {
        fallback.hidden = false;
      } else {
        fallback.hidden = true;
        if (!sectorHomeMap) {
          sectorHomeMap = L.map(mapNode, { zoomControl: true, scrollWheelZoom: true });
          L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
          }).addTo(sectorHomeMap);
          sectorHomeLayer = L.layerGroup().addTo(sectorHomeMap);
        }
        sectorHomeLayer.clearLayers();
        const allBounds = [];
        const sectors = payload.sectors?.length ? payload.sectors : [sector];
        for (const item of sectors) {
          const own = String(item.id).toUpperCase() === String(sector.id).toUpperCase();
          const color = own ? '#e64297' : '#e2bb24';
          const ring = item.geometry?.coordinates?.[0];
          const latlngs = ring?.length
            ? ring.map(([lon, lat]) => [lat, lon])
            : [[item.bounds.south, item.bounds.west], [item.bounds.south, item.bounds.east], [item.bounds.north, item.bounds.east], [item.bounds.north, item.bounds.west]];
          const polygon = L.polygon(latlngs, { color, weight: own ? 4 : 3, fillColor: color, fillOpacity: own ? .19 : .10 }).addTo(sectorHomeLayer);
          const center = item.geometry?.centroid
            ? [item.geometry.centroid[1], item.geometry.centroid[0]]
            : polygon.getBounds().getCenter();
          const area = item.geometry?.area_km2 == null ? 'Area pending' : `${Number(item.geometry.area_km2).toFixed(3)} km²`;
          const labelClass = own ? 'sector-map-label sector-map-label-own' : 'sector-map-label';
          L.marker(center, {
            interactive: false,
            icon: L.divIcon({
              className: 'sector-map-label-anchor',
              html: `<span class="${labelClass}">${escapeHTML(item.name)}<br>${area}</span>`,
              iconSize: null,
            }),
          }).addTo(sectorHomeLayer);
          allBounds.push(...latlngs);
        }
        for (const assessment of gpsAssessments) {
          const trail = (assessment.points || []).map((point) => [Number(point.latitude), Number(point.longitude)]);
          if (!trail.length) continue;
          const kind = ['bird', 'drone', 'unknown'].includes(assessment.derived_classification) ? assessment.derived_classification : 'unknown';
          const color = kind === 'bird' ? '#35c979' : kind === 'drone' ? '#ef5261' : '#a8b2bd';
          if (trail.length > 1) L.polyline(trail, { color, weight: 3, opacity: 0.9 }).addTo(sectorHomeLayer);
          const icon = L.divIcon({ className: `trajectory-classification-map-label is-${kind}`, html: `<span>${escapeHTML(kind.toUpperCase())} · ${escapeHTML(assessment.device_id)}</span>`, iconSize: null });
          L.marker(trail[trail.length - 1], { icon, interactive: false }).addTo(sectorHomeLayer);
        }
        if (allBounds.length) sectorHomeMap.fitBounds(L.latLngBounds(allBounds), { padding: [20, 20], maxZoom: 16 });
        else sectorHomeMap.fitBounds([[b.south, b.west], [b.north, b.east]], { padding: [20, 20] });
        window.setTimeout(() => sectorHomeMap?.invalidateSize(), 0);
      }
      if (!skipLocationRefresh) await refreshOperatorLocation();
    }
  } catch (error) {
    $('#sector-home-name').textContent = 'Sector view unavailable';
    $('#sector-dashboard-summary-status').textContent = `Could not load the sector summary: ${error.message}`;
    status.textContent = error.message;
  }
}

async function refreshOperatorLocation() {
  try {
    state.operatorLocation = await api('/api/operator/location');
    const shared = Boolean(state.operatorLocation.sharing);
    for (const suffix of ['hq','sector']) {
      const button = $(`#${suffix === 'hq' ? 'hq-' : ''}operator-location-toggle`);
      const status = $(`#${suffix === 'hq' ? 'hq-' : ''}operator-location-status`);
      if (button) button.textContent = shared ? 'Stop sharing and remove my location' : 'Share my approximate location';
      if (status) status.textContent = shared ? `Sharing is on · approx. 100 m precision · last updated ${state.operatorLocation.updated_at}` : 'Location sharing is off.';
    }
    if (state.profile?.role === 'headquarters_admin') {
      const result = await api('/api/headquarters/operator-locations');
      window.operatorLocationCache = result.locations || [];
      if (headquartersLeafletMap && window.hqSectorPayload) renderHeadquartersSectorMap(window.hqSectorPayload);
    }
  } catch (error) {
    for (const id of ['operator-location-status','hq-operator-location-status']) {
      const status = $(`#${id}`); if (status) status.textContent = `Location sharing status unavailable: ${error.message}`;
    }
  }
}

async function toggleOperatorLocation() {
  const buttons = [$('#operator-location-toggle'), $('#hq-operator-location-toggle')].filter(Boolean);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    if (state.operatorLocation?.sharing) {
      await api('/api/operator/location', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:false}) });
    } else {
      if (!navigator.geolocation) throw new Error('This browser does not provide location services.');
      const position = await new Promise((resolve,reject) => navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy:false, timeout:12000, maximumAge:60000 }));
      await api('/api/operator/location', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:true,latitude:position.coords.latitude,longitude:position.coords.longitude}) });
    }
    await refreshOperatorLocation();
    if (state.profile?.role === 'sector' && state.route?.page === 'sector-dashboard') await loadSectorDashboard(true);
  } catch (error) {
    const message = error.code === 1 ? 'Location permission was denied. You can enable it in browser settings.' : error.message || 'Could not update location sharing.';
    for (const id of ['operator-location-status','hq-operator-location-status']) { const status=$(`#${id}`); if(status) status.textContent=message; }
  } finally { buttons.forEach((button) => { button.disabled=false; }); }
}
$('#operator-location-toggle')?.addEventListener('click', () => void toggleOperatorLocation());
$('#hq-operator-location-toggle')?.addEventListener('click', () => void toggleOperatorLocation());

async function initializeDashboard(profile) {
  if (typeof profile === 'string') profile = { username: profile, role: 'headquarters_admin' };
  state.profile = profile || { role: 'headquarters_admin' };
  showDashboard(state.profile.username || 'User');
  const sectorAccount = state.profile.role === 'sector';
  const headquartersAccount = state.profile.role === 'headquarters_admin';
  const sectorNavigation = new Set([
    'dashboard',
    'sector-dashboard',
    'cctv',
    'video-analysis',
    'results',
    'history',
    'alerts',
    'reports',
    'settings',
  ]);
  document.querySelectorAll('.dashboard-sidebar [data-navigation]').forEach((link) => {
    if (sectorAccount) link.hidden = !sectorNavigation.has(link.dataset.navigation);
    else if (headquartersAccount) link.hidden = !HEADQUARTERS_ALLOWED_PAGES.has(
      parseRoute(link.getAttribute('href')).page,
    );
    else link.hidden = link.dataset.navigation === 'sector-dashboard';
  });
  const createAnalysis = $('#new-analysis');
  if (createAnalysis) createAnalysis.hidden = sectorAccount || headquartersAccount;
  $('#hq-dashboard-username').textContent = state.profile.username || 'Headquarters Admin';
  $('#hq-dashboard-api').textContent = $('#health-text').textContent || 'Checking…';
  try {
    await api('/api/health');
    setApiHealth(true);
  } catch (error) {
    setApiHealth(false);
    showGlobalError(`Backend is unavailable: ${error.message}`);
  }
  const route = parseRoute(window.location.pathname);
  if (state.profile?.role === 'sector') {
    if (route.invalid || window.location.pathname === '/' || window.location.pathname === '/login') {
      history.replaceState(null, '', '/sector-dashboard');
    }
  } else if (route.invalid || window.location.pathname === '/' || window.location.pathname === '/login') {
    history.replaceState(null, '', '/dashboard');
    if (!headquartersAccount) await resumeCurrentRun();
  } else if (route.page === 'dashboard' && !headquartersAccount) {
    await resumeCurrentRun();
  }
  await renderCurrentRoute();
}

async function initialize() {
  if (window.location.pathname === '/sector-registry') {
    setEntryView('registry');
    await loadPublicRegistrySectors();
    return;
  }
  setEntryView('splash');
  const sessionRequest = api('/api/auth/session').then(
    (session) => ({ session }),
    (error) => ({ error }),
  );
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const result = await sessionRequest;
  if (result.error) {
    showLogin('Could not verify your session. Please try signing in.');
    setLoginStatus('Authentication API unavailable', 'error');
    setLoginTerminal(['Could not verify the existing session.', 'Authentication API unavailable.']);
    return;
  }
  if (result.session.authenticated && result.session.must_change_password) {
    const updated = await completeForcedPasswordChange();
    if (!updated) {
      try { await api('/api/auth/logout', { method: 'POST' }); }
      catch (error) { showGlobalError(`Could not end the temporary password-change session: ${error.message}`); }
      showLogin('Password change is required before this account can be used.');
      return;
    }
    await api('/api/auth/logout', { method: 'POST' });
    showLogin('Password saved. Sign in again with your registered email and new password.');
  } else if (result.session.authenticated) await initializeDashboard(result.session);
  else {
    showLogin();
    setLoginStatus('Authentication service ready', 'ready');
    setLoginTerminal(['Authentication API ready.', 'Session check complete. Awaiting sign-in.']);
  }
}

async function selectInputSource(source) {
  if (source === 'cctv') setThermalPreparationView(false);
  if (source === 'upload' && state.cctvConnected) {
    await stopCCTV();
  }
  if (state.phase3Input?.source_type === 'upload') {
    await stopPhase3Input();
  }
  state.inputSource = source;
  if ($('#source-cctv')) $('#source-cctv').setAttribute('aria-pressed', String(source === 'cctv'));
  $('#source-upload').setAttribute('aria-pressed', String(source === 'upload'));
  $('#cctv-input').hidden = source !== 'cctv';
  $('#upload-input').hidden = source !== 'upload';
  $('#file-picker-panel').hidden = source !== 'upload' || !state.inputType;
  $('#upload-status-panel').hidden = source !== 'upload' || !state.inputType;
  $('#player-source-status').textContent = state.selectedFile ? 'Local preview' : 'Awaiting input';
  clearGlobalError();
}

function showPhase3Input(input) {
  state.phase3Input = input;
  state.phaseExecution = {};
  state.phase5Ended = false;
  const thermalUpload = input.source_type === 'upload' && input.input_type === 'thermal';
  $('#thermal-debug-navigation').hidden = !thermalUpload;
  $('#phase3-guidance').textContent = thermalUpload
    ? 'Acquire normalized thermal frames here, or start full-video thermal detection and tracking. Later analysis stages are not run.'
    : 'Optional frame acquisition. Start Analysis runs the full pipeline for RGB.';
  $('#phase4-guidance').textContent = thermalUpload
    ? 'Single-frame detection uses the trained Thermal Bird=0, Drone=1 checkpoint.'
    : 'Single-frame detection uses the configured RGB detector.';
  $('#phase5-guidance').textContent = thermalUpload
    ? 'Thermal tracking reuses the Bird/Drone detector and keeps IDs across frames. Run-level temporal consensus is available after full-video processing.'
    : 'Single-frame tracking reuses the detector and keeps track IDs across frames.';
  $('#phase5-status').textContent = 'READY';
  $('#phase5-frame').textContent = '—';
  $('#phase5-count').textContent = '—';
  $('#phase5-results').textContent = 'No frame tracked';
  $('#phase5-track').disabled = !supportsSingleFrameDetectionInput(input);
  $('#phase6-panel').hidden = input.input_type === 'thermal';
  $('#phase6-status').textContent = 'WAITING';
  $('#phase6-frames').textContent = '—';
  $('#phase6-timing').textContent = '—';
  $('#phase6-results').textContent = 'No temporal history';
  $('#phase4-status').textContent = 'READY';
  $('#phase4-model').textContent = 'Loads on first detection';
  $('#phase4-inference').textContent = '—';
  $('#phase4-count').textContent = '—';
  $('#phase4-frame').textContent = '—';
  $('#phase4-results').textContent = input.input_type === 'thermal'
    ? 'Thermal Bird/Drone detector ready.'
    : 'No frame processed';
  $('#phase4-preview').hidden = true;
  $('#phase4-preview').removeAttribute('src');
  state.phase4Ended = false;
  $('#phase4-detect').disabled = !supportsSingleFrameDetectionInput(input);
  $('#phase3-readiness').textContent = 'READY_FOR_PHASE_3';
  $('#phase3-source').textContent = input.source_type.toUpperCase();
  $('#phase3-input-type').textContent = input.input_type ? input.input_type.toUpperCase() : 'LIVE';
  $('#phase3-frame-status').textContent = 'Awaiting frame acquisition';
  $('#phase3-validation').textContent = 'Not yet acquired';
  $('#phase3-preprocessing').textContent = 'Not yet prepared';
  $('#phase3-frame-index').textContent = '—';
  $('#phase3-dimensions').textContent = '—';
  $('#phase3-preview').hidden = true;
  $('#phase3-preview').removeAttribute('src');
  $('#phase3-acquire').disabled = false;
  setPhaseExecution('uploaded', 'complete');
  selectPipelineStage('uploaded', false);
}

function supportsSingleFrameDetectionInput(input) {
  return Boolean(input && (
    (input.source_type === 'upload' && ['rgb', 'thermal'].includes(input.input_type))
    || (input.source_type === 'cctv' && (input.input_type === null || input.input_type === 'rgb'))
  ));
}

async function acquireNextPhase3Frame() {
  const input = state.phase3Input;
  if (!input || state.busy) return;
  $('#phase3-acquire').disabled = true;
  $('#phase3-frame-status').textContent = 'Acquiring and validating frame…';
  try {
    const result = await api('/api/phase3/frame', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (result.end_of_video) {
      $('#phase3-readiness').textContent = 'END_OF_VIDEO';
      $('#phase3-frame-status').textContent = 'End of video';
      $('#phase3-acquire').disabled = true;
      return;
    }
    $('#phase3-readiness').textContent = 'READY_FOR_PHASE_4';
    $('#phase3-frame-status').textContent = 'Frame acquired and prepared';
    $('#phase3-validation').textContent = result.validation_status;
    $('#phase3-preprocessing').textContent = result.preprocessing_status;
    $('#phase3-frame-index').textContent = result.frame_index ?? 'Not provided by source';
    $('#phase3-dimensions').textContent = `${result.width} × ${result.height} · ${result.channels} channel(s) · ${result.dtype}`;
    state.preparedFrameResolution = { width: result.width, height: result.height };
    renderVideoMetadata(state.inputMetadata || {});
    $('#phase3-preview').src = `data:image/jpeg;base64,${result.frame_base64}`;
    $('#phase3-preview').hidden = false;
    showProcessedFrame(`data:image/jpeg;base64,${result.frame_base64}`, result.frame_index, 'Frame acquired');
  } catch (error) {
    $('#phase3-readiness').textContent = 'FRAME_ERROR';
    $('#phase3-frame-status').textContent = error.message;
    $('#phase3-acquire').disabled = true;
  }
}

async function detectNextPhase4Frame() {
  const input = state.phase3Input;
  if (!supportsSingleFrameDetectionInput(input) || state.phase4Busy) return;
  state.phase4Busy = true;
  $('#phase4-detect').disabled = true;
  $('#phase4-status').textContent = 'DETECTING';
  $('#phase4-results').textContent = 'Processing one prepared frame…';
  try {
    const result = await api('/api/phase4/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (result.end_of_video) {
      state.phase4Ended = true;
      $('#phase4-status').textContent = 'END_OF_VIDEO';
      $('#phase4-results').textContent = 'No more frames are available.';
      $('#phase4-count').textContent = '0';
      setPhaseExecution('detection', 'complete');
      return;
    }
    $('#phase4-status').textContent = 'COMPLETE';
    if (result.model_input_width && result.model_input_height) {
      state.inputMetadata = {
        ...state.inputMetadata,
        model_input_width: result.model_input_width,
        model_input_height: result.model_input_height,
      };
      renderVideoMetadata(state.inputMetadata);
    }
    $('#phase4-model').textContent = result.model_name;
    $('#phase4-inference').textContent = `${Number(result.inference_time_ms).toFixed(1)} ms`;
    $('#phase4-count').textContent = String(result.detection_count);
    $('#phase4-frame').textContent = result.frame_index ?? 'Live frame';
    $('#phase4-results').textContent = result.detections.length
      ? result.detections.map((detection) => `${detection.class_name} ${Number(detection.confidence).toFixed(2)}`).join(' · ')
      : 'No detections above the configured confidence threshold.';
    $('#phase4-preview').src = `data:image/jpeg;base64,${result.frame_base64}`;
    $('#phase4-preview').hidden = false;
    showProcessedFrame(`data:image/jpeg;base64,${result.frame_base64}`, result.frame_index, 'Detection complete');
    setPhaseExecution('detection', 'complete');
  } catch (error) {
    $('#phase4-status').textContent = 'DETECTION_ERROR';
    $('#phase4-results').textContent = error.message;
    setPhaseExecution('detection', 'failed');
  } finally {
    state.phase4Busy = false;
    $('#phase4-detect').disabled = state.phase4Ended;
  }
}

async function trackNextPhase5Frame() {
  const input = state.phase3Input;
  if (!supportsSingleFrameDetectionInput(input) || state.phase5Busy || state.phase5Ended) return;
  state.phase5Busy = true;
  $('#phase5-track').disabled = true;
  $('#phase5-status').textContent = 'TRACKING';
  $('#phase5-results').textContent = 'Detecting and tracking one prepared frame…';
  try {
    const result = await api('/api/phase5/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    if (result.end_of_video) {
      state.phase5Ended = true;
      $('#phase5-status').textContent = 'END_OF_VIDEO';
      $('#phase5-frame').textContent = '—';
      $('#phase5-count').textContent = '0';
      $('#phase5-results').textContent = 'No more frames are available.';
      setPhaseExecution('tracking', 'complete');
      if (input.input_type !== 'thermal') {
        await updatePhase6Temporal({ ...result, end_of_video: true });
      }
      return;
    }
    $('#phase5-status').textContent = 'TRACKED';
    $('#phase5-frame').textContent = result.frame_index ?? 'Live frame';
    $('#phase5-count').textContent = String(result.active_track_count);
    $('#phase5-results').textContent = result.tracks.length
      ? result.tracks.map((track) => `ID ${track.track_id}: ${track.class_name} ${Number(track.confidence).toFixed(2)} [${[track.x1, track.y1, track.x2, track.y2].map((value) => Number(value).toFixed(0)).join(', ')}]`).join(' · ')
      : 'No active tracks in this frame.';
    setPhaseExecution('tracking', 'complete');
    if (input.input_type !== 'thermal') await updatePhase6Temporal(result);
  } catch (error) {
    $('#phase5-status').textContent = 'TRACKING_ERROR';
    $('#phase5-results').textContent = error.message;
    setPhaseExecution('tracking', 'failed');
  } finally {
    state.phase5Busy = false;
    $('#phase5-track').disabled = state.phase5Ended;
  }
}

async function updatePhase6Temporal(trackResult) {
  try {
    const result = await api('/api/phase6/temporal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(trackResult),
    });
    $('#phase6-status').textContent = result.status;
    $('#phase6-frames').textContent = String(result.frames_processed);
    $('#phase6-timing').textContent = result.frame_basis === 'phase5_observation_sequence'
      ? 'CCTV observation sequence · time/FPS unavailable'
      : result.timing_basis === 'video_fps'
        ? 'Uploaded video FPS'
        : 'Unavailable';
    $('#phase6-results').textContent = result.tracks.length
      ? result.tracks.map((track) => {
        const summary = track.temporal_summary;
        const motion = track.motion_summary;
        return `ID ${track.track_id} (${track.class_history.join('/')}) · ${summary.frames_seen} observations · ${Number(motion.path_length_px).toFixed(1)} px path · ${track.trajectory.motion_state}`;
      }).join(' | ')
      : 'No track observations are available for temporal analysis.';
    setPhaseExecution('temporal', 'complete');
  } catch (error) {
    $('#phase6-status').textContent = 'TEMPORAL_ERROR';
    $('#phase6-results').textContent = error.message;
    setPhaseExecution('temporal', 'failed');
  }
}

async function resetPhase5Tracking() {
  $('#phase5-reset').disabled = true;
  try {
    await api('/api/phase5/reset', { method: 'POST', body: '' });
    state.phase5Ended = false;
    $('#phase5-status').textContent = 'RESET';
    $('#phase5-frame').textContent = '—';
    $('#phase5-count').textContent = '—';
    $('#phase5-results').textContent = 'Tracking state cleared';
    $('#phase6-status').textContent = 'RESET';
    $('#phase6-frames').textContent = '—';
    $('#phase6-timing').textContent = '—';
    $('#phase6-results').textContent = state.phase3Input?.input_type === 'thermal'
      ? 'Single-frame Thermal Phase 6 analysis is not supported. Full-video runs expose offline track-level temporal consensus after processing.'
      : 'Temporal history cleared';
    delete state.phaseExecution.tracking;
    delete state.phaseExecution.temporal;
    renderPipeline(state.run || { status: 'idle', stage: 'uploaded' });
    $('#phase5-track').disabled = !supportsSingleFrameDetectionInput(state.phase3Input);
  } catch (error) {
    $('#phase5-status').textContent = 'RESET_ERROR';
    $('#phase5-results').textContent = error.message;
  } finally {
    $('#phase5-reset').disabled = false;
  }
}

async function stopPhase3Input() {
  const input = state.phase3Input;
  if (!input) return;
  $('#phase3-acquire').disabled = true;
  try {
    if (input.source_type === 'cctv') {
      await stopCCTV();
      if (state.cctvConnected) throw new Error('Live CCTV source could not be stopped.');
    } else {
      await api('/api/phase3/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: input.run_id }),
      });
    }
    state.phase3Input = null;
    $('#thermal-debug-navigation').hidden = true;
    document.querySelectorAll('.phase3-panel').forEach((panel) => { panel.hidden = true; });
    $('#phase3-preview').removeAttribute('src');
    $('#phase4-preview').removeAttribute('src');
    selectPipelineStage('uploaded', false);
  } catch (error) {
    $('#phase3-frame-status').textContent = `Could not stop frame source: ${error.message}`;
    $('#phase3-acquire').disabled = false;
  }
}

async function prepareCCTV(action) {
  const url = $('#rtsp-url').value.trim();
  $('#test-cctv').disabled = true;
  $('#start-cctv').disabled = true;
  $('#cctv-status').textContent = action === 'test' ? 'Testing connection…' : 'Connecting…';
  try {
    const result = await api('/api/input/prepare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'cctv', action, rtsp_url: url }),
    });
    if (action === 'start') {
      state.cctvConnected = true;
      state.cctvReference = result.live_reference_id;
      showPhase3Input({
        source_type: 'cctv',
        input_type: null,
        live_reference_id: result.live_reference_id,
      });
    }
    $('#cctv-status').textContent = action === 'test'
      ? 'Connection test succeeded. The test capture was released.'
      : 'Connected. Live input is READY_FOR_PHASE_3; analysis has not started.';
    $('#stop-cctv').disabled = !state.cctvConnected;
    const dimensions = result.metadata?.width && result.metadata?.height
      ? ` · ${result.metadata.width}×${result.metadata.height}`
      : '';
    $('#cctv-status').textContent += dimensions;
  } catch (error) {
    $('#cctv-status').textContent = `Connection failed: ${error.message}`;
    if (action === 'start') {
      state.cctvConnected = false;
      state.cctvReference = null;
    }
    $('#stop-cctv').disabled = !state.cctvConnected;
  } finally {
    $('#test-cctv').disabled = false;
    $('#start-cctv').disabled = false;
  }
}

async function stopCCTV() {
  if (!state.cctvConnected) return;
  $('#stop-cctv').disabled = true;
  try {
    await api('/api/input/cctv/stop', { method: 'POST', body: '' });
    state.cctvConnected = false;
    state.cctvReference = null;
    state.phase3Input = null;
    $('#phase3-panel').hidden = true;
    $('#phase4-panel').hidden = true;
    $('#phase5-panel').hidden = true;
    $('#phase6-panel').hidden = true;
    $('#phase3-preview').removeAttribute('src');
    $('#cctv-status').textContent = 'Live input stopped. Stream resources released.';
    selectPipelineStage('uploaded', false);
  } catch (error) {
    $('#cctv-status').textContent = `Could not stop live input: ${error.message}`;
    $('#stop-cctv').disabled = false;
  }
}

let lastLoginPingAt = 0;
let loginRadarTrackStarted = false;
let loginRadarTrackEntry = Promise.resolve();
let loginRadarStage = 'idle';
let loginRadarWanderTimer = null;
let loginRadarCenterPromise = null;
let loginRadarOutsideLeftPct = 10;

function randomizeLoginBirdPath(bird) {
  const drift = () => `${Math.round(Math.random() * 52 - 26)}px`;
  for (let point = 1; point <= 3; point += 1) {
    bird.style.setProperty(`--login-bird-x${point}`, drift());
    bird.style.setProperty(`--login-bird-y${point}`, drift());
  }
}

document.querySelectorAll('.login-radar-bird').forEach((bird) => {
  randomizeLoginBirdPath(bird);
  bird.addEventListener('animationiteration', () => randomizeLoginBirdPath(bird));
});

function resetLoginRadarAnimation() {
  loginRadarTrackStarted = false;
  loginRadarStage = 'idle';
  loginRadarTrackEntry = Promise.resolve();
  loginRadarCenterPromise = null;
  window.clearTimeout(loginRadarWanderTimer);
  loginRadarWanderTimer = null;
  const uav = $('#login-radar-uav');
  uav.getAnimations().forEach((animation) => animation.cancel());
  uav.classList.remove('is-active', 'is-entering', 'is-locked', 'is-cleared');
  uav.removeAttribute('style');
  $('#login-radar-crosshair').classList.remove('is-locking');
  $('#login-radar-crosshair').removeAttribute('style');
  $('#login-radar-flash').classList.remove('is-flashing');
  $('#login-radar-status').textContent = '[IDLE] Radar scanning. Demo tracks only; no live telemetry.';
  $('#login-radar-status').dataset.state = 'idle';
  $('#login-view').classList.remove('login-auth-success');
  const welcome = $('#login-welcome-overlay');
  welcome.classList.remove('is-visible');
  welcome.setAttribute('aria-hidden', 'true');
}

function scheduleLoginRadarWander() {
  if (loginRadarStage !== 'wandering') return;
  const uav = $('#login-radar-uav');
  uav.style.transition = 'left 1.15s ease-in-out, top 1.15s ease-in-out';
  uav.style.left = `${loginRadarOutsideLeftPct - Math.random() * 8 / $('.login-hero-art').getBoundingClientRect().width * 100}%`;
  uav.style.top = `${38 + Math.random() * 24}%`;
  loginRadarWanderTimer = window.setTimeout(scheduleLoginRadarWander, 1200 + Math.random() * 1100);
}

function startLoginRadarTrack() {
  if (loginRadarTrackStarted) return loginRadarTrackEntry;
  loginRadarTrackStarted = true;
  loginRadarStage = 'entering';
  const uav = $('#login-radar-uav');
  const status = $('#login-radar-status');
  uav.classList.add('is-active');
  uav.style.transition = 'none';
  const artBounds = $('.login-hero-art').getBoundingClientRect();
  const radarDiameter = Math.min(artBounds.width * .92, 350);
  loginRadarOutsideLeftPct = Math.max(1, ((artBounds.width - radarDiameter) / 2 - 7) / artBounds.width * 100);
  const entryStart = Math.max(-3, loginRadarOutsideLeftPct - 4);
  uav.style.left = `${entryStart}%`;
  uav.style.top = '50%';
  uav.style.right = 'auto';
  status.textContent = '[WARN] Unidentified simulated UAV entering from the west boundary…';
  status.dataset.state = 'warning';
  loginRadarTrackEntry = new Promise((resolve) => {
    const entry = uav.animate([
      { left: `${entryStart}%`, top: '50%' }, { left: `${loginRadarOutsideLeftPct - 1}%`, top: '45%' }, { left: `${loginRadarOutsideLeftPct}%`, top: '52%' },
    ], { duration: 950, easing: 'cubic-bezier(.2,.72,.28,1)', fill: 'forwards' });
    entry.onfinish = () => {
      uav.style.left = `${loginRadarOutsideLeftPct}%`;
      uav.style.top = '52%';
      entry.cancel();
      if (loginRadarStage === 'entering') {
        loginRadarStage = 'wandering';
        status.textContent = '[WARN] Small simulated UAV holding outside radar perimeter.';
        scheduleLoginRadarWander();
      }
      resolve();
    };
  });
  return loginRadarTrackEntry;
}

async function moveLoginRadarToCenter() {
  if (!loginRadarTrackStarted) startLoginRadarTrack();
  await loginRadarTrackEntry;
  if (loginRadarStage === 'approaching' || loginRadarStage === 'locked' || loginRadarStage === 'neutralized') return loginRadarCenterPromise;
  loginRadarStage = 'approaching';
  window.clearTimeout(loginRadarWanderTimer);
  loginRadarWanderTimer = null;
  const uav = $('#login-radar-uav');
  const status = $('#login-radar-status');
  status.textContent = '[WARN] Password entered. Simulated UAV approaching radar center; awaiting authentication.';
  status.dataset.state = 'warning';
  uav.classList.add('is-approaching');
  uav.style.transition = 'left 850ms cubic-bezier(.2,.72,.28,1), top 850ms cubic-bezier(.2,.72,.28,1), width 250ms ease, height 250ms ease';
  loginRadarCenterPromise = new Promise((resolve) => window.setTimeout(() => {
    uav.style.left = '50%';
    uav.style.top = '50%';
    window.setTimeout(resolve, 900);
  }, 30));
  await loginRadarCenterPromise;
}

async function runLoginSuccessSequence() {
  if (!loginRadarTrackStarted) startLoginRadarTrack();
  await moveLoginRadarToCenter();
  loginRadarStage = 'locked';

  const art = $('.login-hero-art');
  const uav = $('#login-radar-uav');
  const crosshair = $('#login-radar-crosshair');
  const flash = $('#login-radar-flash');
  const status = $('#login-radar-status');
  const artBounds = art.getBoundingClientRect();
  const dotBounds = uav.getBoundingClientRect();
  const dotX = dotBounds.left + dotBounds.width / 2;
  const dotY = dotBounds.top + dotBounds.height / 2;
  const x = ((dotX - artBounds.left) / artBounds.width) * 100;
  const y = ((dotY - artBounds.top) / artBounds.height) * 100;

  uav.style.left = `${x}%`;
  uav.style.top = `${y}%`;
  uav.style.right = 'auto';
  uav.classList.remove('is-active', 'is-entering', 'is-approaching');
  uav.classList.add('is-locked');
  crosshair.style.left = `${x}%`;
  crosshair.style.top = `${y}%`;
  crosshair.classList.add('is-locking');
  await new Promise((resolve) => {
    const timeout = window.setTimeout(resolve, 650);
    crosshair.addEventListener('animationend', () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
  });

  flash.classList.add('is-flashing');
  uav.classList.remove('is-locked');
  uav.classList.add('is-cleared');
  loginRadarStage = 'neutralized';
  status.textContent = '[SUCCESS] Simulated UAV neutralized. Secure system session established.';
  status.dataset.state = 'success';

  const welcome = $('#login-welcome-overlay');
  $('#login-view').classList.add('login-auth-success');
  welcome.setAttribute('aria-hidden', 'false');
  welcome.classList.add('is-visible');
  await new Promise((resolve) => window.setTimeout(resolve, 1500));
  welcome.classList.remove('is-visible');
  welcome.setAttribute('aria-hidden', 'true');
}

$('#login-view').addEventListener('pointermove', (event) => {
  const now = performance.now();
  if (now - lastLoginPingAt < 140) return;
  lastLoginPingAt = now;
  const bounds = $('#login-view').getBoundingClientRect();
  const ping = document.createElement('span');
  ping.className = 'login-ping';
  ping.style.left = `${event.clientX - bounds.left}px`;
  ping.style.top = `${event.clientY - bounds.top}px`;
  $('#login-ping-layer').append(ping);
  window.setTimeout(() => ping.remove(), 900);
});

function isValidFirstPassword(password) {
  return password.length >= 8 && password.length <= 12
    && /[A-Z]/.test(password) && /[a-z]/.test(password)
    && /[0-9]/.test(password) && /[^A-Za-z0-9\s]/.test(password);
}

let forcedPasswordCurrent = '';
let resolveForcedPasswordChange = null;

function completeForcedPasswordChange(currentPassword = '') {
  const dialog = $('#forced-password-dialog');
  const currentWrap = $('#forced-current-password-wrap');
  forcedPasswordCurrent = currentPassword;
  currentWrap.hidden = Boolean(currentPassword);
  $('#forced-current-password').value = '';
  $('#forced-new-password').value = '';
  $('#forced-confirm-password').value = '';
  $('#forced-password-error').hidden = true;
  dialog.showModal();
  (currentPassword ? $('#forced-new-password') : $('#forced-current-password')).focus();
  return new Promise((resolve) => { resolveForcedPasswordChange = resolve; });
}

$('#forced-password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const currentPassword = forcedPasswordCurrent || $('#forced-current-password').value;
  const newPassword = $('#forced-new-password').value;
  const confirmation = $('#forced-confirm-password').value;
  const error = $('#forced-password-error');
  if (!currentPassword) error.textContent = 'Enter your current or one-time password.';
  else if (!isValidFirstPassword(newPassword)) error.textContent = 'Use 8–12 characters, including an uppercase letter, a lowercase letter, a number, and a symbol.';
  else if (confirmation !== newPassword) error.textContent = 'The new passwords do not match.';
  else {
    const submit = event.currentTarget.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await api('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      resolveForcedPasswordChange?.(true);
      resolveForcedPasswordChange = null;
      $('#forced-password-dialog').close();
    } catch (requestError) {
      error.textContent = requestError.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
    return;
  }
  error.hidden = false;
});

$('#cancel-forced-password').addEventListener('click', () => $('#forced-password-dialog').close());
$('#forced-password-dialog').addEventListener('close', () => {
  $('#forced-current-password').value = '';
  $('#forced-new-password').value = '';
  $('#forced-confirm-password').value = '';
  forcedPasswordCurrent = '';
  resolveForcedPasswordChange?.(false);
  resolveForcedPasswordChange = null;
});

let activeLoginSectors = [];
let loginSectorLoadRequest = 0;
function renderLoginSectorOptions(sectors) {
  return sectors.filter((sector) => typeof sector.id === 'string' && sector.id && typeof sector.name === 'string' && sector.name);
}

async function updateLoginSectorSelector() {
  const requestId = ++loginSectorLoadRequest;
  const selectorWrap = $('#login-sector-selector');
  const selector = $('#login-sector-number');
  const status = $('#login-sector-status');
  const submit = $('#login-submit');
  const sectorSelected = $('#login-account-type').value === 'sector';
  selectorWrap.hidden = !sectorSelected;
  if (!sectorSelected) {
    submit.disabled = false;
    return;
  }
  selector.disabled = true;
  submit.disabled = true;
  selector.replaceChildren(new Option('Select an approved sector', ''));
  status.textContent = 'Loading active, HQ-approved sectors…';
  try {
    const payload = await api('/api/sector-registry/approved-sectors');
    if (requestId !== loginSectorLoadRequest || $('#login-account-type').value !== 'sector') return;
    activeLoginSectors = renderLoginSectorOptions(Array.isArray(payload.sectors) ? payload.sectors : []);
    for (const sector of activeLoginSectors) selector.add(new Option(`${sector.name} (${sector.id})`, sector.id));
    selector.disabled = activeLoginSectors.length === 0;
    if (activeLoginSectors.length) {
      status.textContent = 'Choose your approved sector. Sign in with its registered shared-account email and password.';
      submit.disabled = true;
    } else {
      status.textContent = 'No active, HQ-approved sectors are available for Sector Account sign-in.';
    }
  } catch (error) {
    if (requestId !== loginSectorLoadRequest || $('#login-account-type').value !== 'sector') return;
    activeLoginSectors = [];
    selector.replaceChildren(new Option('Approved sectors unavailable', ''));
    status.textContent = `Could not load approved sectors: ${error.message}`;
    submit.disabled = true;
  }
}

$('#login-account-type').addEventListener('change', () => { void updateLoginSectorSelector(); });
$('#login-sector-number').addEventListener('change', () => {
  if ($('#login-account-type').value === 'sector') $('#login-submit').disabled = !$('#login-sector-number').value;
});

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const username = $('#login-username').value.trim();
  const password = $('#login-password').value;
  const accountType = $('#login-account-type').value;
  const sectorNumber = accountType === 'sector' ? $('#login-sector-number').value : '';
  if (!username || !password) {
    showLogin('Enter your email/login ID and password.');
    return;
  }
  if (accountType === 'sector' && (!sectorNumber || !activeLoginSectors.some((sector) => sector.id === sectorNumber))) {
    showLogin(activeLoginSectors.length ? 'Choose an active, approved sector before signing in.' : 'No approved sectors are available for Sector Account sign-in.');
    return;
  }
  $('#login-error').hidden = true;
  $('#login-submit').disabled = true;
  $('#login-submit').setAttribute('aria-busy', 'true');
  $('#login-submit-label').textContent = 'Contacting API…';
  $('.login-submit-spinner').hidden = false;
  setLoginStatus('Contacting authentication service', 'busy', 'Sending sign-in request to the backend…', 'busy');
  setLoginTerminal(['Sending credentials to the authentication API…']);
  try {
    const session = await api('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, account_type: accountType, ...(accountType === 'sector' ? { sector_number: sectorNumber } : {}) }),
    });
    if (session.must_change_password) {
      const updated = await completeForcedPasswordChange(password);
      if (!updated) {
        try { await api('/api/auth/logout', { method: 'POST' }); }
        catch (error) { showGlobalError(`Could not end the temporary password-change session: ${error.message}`); }
      } else {
        await api('/api/auth/logout', { method: 'POST' });
      }
      $('#login-username').value = username;
      $('#login-password').value = '';
      $('#login-submit').disabled = false;
      $('#login-submit').setAttribute('aria-busy', 'false');
      $('.login-submit-spinner').hidden = true;
      showLogin(updated ? 'Password saved. Sign in again with your registered email and new password.' : 'Password change is required before this account can be used.');
      setLoginStatus(updated ? 'Password updated' : 'Password change required', 'ready');
      return;
    }
    $('#login-submit-label').textContent = 'Session accepted…';
    setLoginTerminal([
      'Authentication API: credentials accepted.',
      'Session established by backend.',
      'Loading account dashboard…',
    ]);
    setLoginStatus('Authenticated session created', 'ready', 'Sign-in accepted. Loading your dashboard…', 'success');
    $('#login-password').value = '';
    await runLoginSuccessSequence();
    $('#dashboard-view').classList.add('login-dashboard-enter');
    $('#dashboard-view').addEventListener('animationend', (animationEvent) => {
      if (animationEvent.animationName === 'login-dashboard-arrive') $('#dashboard-view').classList.remove('login-dashboard-enter');
    }, { once: true });
    await initializeDashboard(session);
    setLoginTerminal([
      'Authentication API: credentials accepted.',
      'Session established by backend.',
      $('#health-text').textContent.toLowerCase() === 'api connected'
        ? 'Dashboard loaded; API health confirmed.'
        : 'Dashboard loaded; API health needs attention.',
    ]);
  } catch (error) {
    $('#login-error').textContent = error.message;
    $('#login-error').hidden = false;
    if (loginRadarTrackStarted) {
      $('#login-radar-status').textContent = '[WARN] Credentials rejected. Simulated UAV remains unverified.';
      $('#login-radar-status').dataset.state = 'warning';
    } else {
      $('#login-radar-status').textContent = '[IDLE] Credentials were not accepted. No track generated.';
      $('#login-radar-status').dataset.state = 'idle';
    }
    const unavailable = error.message.includes('Could not reach') || error.message.includes('unavailable');
    setLoginStatus(unavailable ? 'Authentication API unavailable' : 'Sign-in rejected', 'error', unavailable ? 'Could not reach the backend. Check that the API is running.' : 'The backend rejected the sign-in. Check the username and password.', 'error');
    setLoginTerminal([unavailable ? 'Authentication API unavailable.' : 'Authentication API rejected the credentials.']);
    $('#login-submit').disabled = false;
    $('#login-submit').setAttribute('aria-busy', 'false');
    $('#login-submit-label').textContent = 'Try again';
    $('.login-submit-spinner').hidden = true;
  }
});

$('.login-input-wrap').addEventListener('pointerenter', () => startLoginRadarTrack());
$('#login-username').addEventListener('focus', () => startLoginRadarTrack());
$('#login-password').addEventListener('input', (event) => {
  if (event.currentTarget.value.length > 0) void moveLoginRadarToCenter();
});

$('#toggle-password').addEventListener('click', () => {
  const passwordInput = $('#login-password');
  const visible = passwordInput.type === 'password';
  passwordInput.type = visible ? 'text' : 'password';
  $('#toggle-password').textContent = visible ? 'Hide' : 'Show';
  $('#toggle-password').setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
  $('#toggle-password').setAttribute('aria-pressed', String(visible));
});

$('#logout-button').addEventListener('click', async () => {
  $('#logout-button').disabled = true;
  try {
    await api('/api/auth/logout', { method: 'POST' });
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
    resetLoginRadarAnimation();
    showLogin();
  } catch (error) {
    showGlobalError(`Could not end the authenticated session: ${error.message}`);
  } finally {
    $('#logout-button').disabled = false;
  }
});

window.addEventListener('btf-auth-required', () => {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
  resetLoginRadarAnimation();
  showLogin('Your session has expired. Sign in to continue.');
});

document.querySelectorAll('[data-results-tab]').forEach((button) => {
  button.addEventListener('click', () => selectResultsTab(button.dataset.resultsTab));
});
$('#rows-per-page').addEventListener('change', (event) => {
  setRowsPerPage(event.target.value);
});
$('#history-rows-per-page').addEventListener('change', (event) => {
  setRowsPerPage(event.target.value);
});
$('#alert-rows-per-page').addEventListener('change', (event) => {
  setRowsPerPage(event.target.value);
});
$('#generate-report').addEventListener('click', generateReport);
$('#pipeline-stages').addEventListener('click', (event) => {
  const button = event.target.closest('[data-stage-target]');
  if (button) selectPipelineStage(button.dataset.stageTarget);
});
$('#history-rows').addEventListener('click', (event) => {
  const button = event.target.closest('[data-history-run-id]');
  if (button) {
    $('#concurrent-run-warning').hidden = true;
    if (button.dataset.historyAction === 'reanalyze') {
      startFreshReanalysis(button.dataset.historyRunId);
    } else if (button.dataset.historyAction === 'resume') {
      resumeRunFromCheckpoint(button.dataset.historyRunId, button);
    } else {
      openHistoryRun(button.dataset.historyRunId);
    }
  }
});
$('#results-run-cards').addEventListener('click', (event) => {
  const download = event.target.closest('[data-report-download-run-id]');
  if (download) {
    void downloadStoredReport(download.dataset.reportDownloadRunId, download.dataset.videoId);
    return;
  }
  const remove = event.target.closest('[data-delete-video-id]');
  if (remove) {
    void deleteVideoAnalysisResults(remove.dataset.deleteVideoId);
    return;
  }
  const button = event.target.closest('[data-history-run-id]');
  if (!button) return;
  if (button.dataset.historyAction === 'reanalyze') {
    startFreshReanalysis(button.dataset.historyRunId);
  } else if (button.dataset.historyAction === 'resume') {
    resumeRunFromCheckpoint(button.dataset.historyRunId, button);
  } else {
    openHistoryRun(button.dataset.historyRunId);
  }
});
document.querySelectorAll('[data-history-tab]').forEach((button) => {
  button.addEventListener('click', () => selectHistoryTab(button.dataset.historyTab));
});
$('#view-cached-results').addEventListener('click', () => {
  if (state.reuseCandidateRunId) openHistoryRun(state.reuseCandidateRunId);
});
$('#phase-detail-track-select').addEventListener('change', (event) => {
  state.selectedPhaseTrackId = event.target.value;
  state.phaseTrackDetail = null;
  state.selectedTrajectoryTrackId = event.target.value;
  renderSelectedPhaseDetail();
  if (state.route?.page === 'dashboard') void renderDashboardTrajectory();
});
$('#dashboard-trajectory-track').addEventListener('change', (event) => {
  void selectTrajectoryTrack(event.target.value);
});
$('#dashboard-trajectory-preview-content').addEventListener('pointerover', (event) => {
  const waypoint = event.target.closest('[data-trajectory-frame]');
  if (!waypoint) return;
  window.clearTimeout(state.trajectorySeekTimer);
  state.trajectorySeekTimer = window.setTimeout(() => inspectTrajectoryWaypoint(waypoint), 110);
});
$('#dashboard-trajectory-preview').addEventListener('click', () => {
  const track = state.tracks.find(
    (item) => String(item.track_id) === String(state.selectedTrajectoryTrackId),
  );
  const detail = state.trajectoryDetails.get(`${state.runId}:${state.selectedTrajectoryTrackId}`);
  if (!track || !detail?.motion_trajectory) return;
  renderExpandedTrajectory(track, detail);
  $('#trajectory-dialog').showModal();
  $('#dashboard-trajectory-preview').setAttribute('aria-expanded', 'true');
});
$('#close-trajectory').addEventListener('click', () => $('#trajectory-dialog').close());
$('#trajectory-dialog').addEventListener('close', () => {
  $('#dashboard-trajectory-preview').setAttribute('aria-expanded', 'false');
});
$('#current-frame-canvas').addEventListener('click', openCurrentFrameDialog);
$('#current-frame-canvas').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  openCurrentFrameDialog();
});
$('#close-current-frame-dialog').addEventListener('click', () => $('#current-frame-dialog').close());
$('#current-frame-dialog').addEventListener('close', () => {
  $('#current-frame-canvas').setAttribute('aria-expanded', 'false');
});
$('#current-frame-dialog').addEventListener('click', (event) => {
  if (event.target === $('#current-frame-dialog')) $('#current-frame-dialog').close();
});
document.querySelectorAll('[data-navigation], [data-app-route]').forEach((link) => {
  link.addEventListener('click', (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const target = new URL(link.href, window.location.origin);
    void navigateTo(`${target.pathname}${target.search}${target.hash}`);
  });
});
document.querySelectorAll('[data-route-link]').forEach((button) => {
  button.addEventListener('click', (event) => {
    event.preventDefault();
    void navigateTo(button.dataset.routeLink);
  });
});
document.addEventListener('click', (event) => {
  const day = event.target.closest('[data-dashboard-date]');
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day.dataset.dashboardDate || '')) return;
  void navigateTo(`/results?date=${encodeURIComponent(day.dataset.dashboardDate)}`);
});
window.addEventListener('popstate', () => void renderCurrentRoute());
$('#reports-run-cards').addEventListener('click', (event) => {
  const button = event.target.closest('[data-report-run-id]');
  if (button) void navigateTo(`/reports/${encodeURIComponent(button.dataset.reportRunId)}`);
});
$('#save-sector-config').addEventListener('click', () => void saveAuthorizationSectorConfig());
$('#save-hq-sector-config').addEventListener('click', () => void saveHeadquartersSectors());
$('#hq-sector-draw-toggle')?.addEventListener('click', toggleHeadquartersSectorDrawing);
$('#hq-sector-add-area')?.addEventListener('click', addHeadquartersSectorDraft);
$('#hq-sector-place-location')?.addEventListener('click', () => {
  if (!headquartersLeafletMap) { $('#hq-sector-location-status').textContent = 'The map is unavailable. Check the map connection and try again.'; return; }
  headquartersLocationSetting = !headquartersLocationSetting;
  $('#hq-sector-place-location').textContent = headquartersLocationSetting ? 'Cancel location pin' : 'Set HQ location on map';
  $('#hq-sector-location-status').textContent = headquartersLocationSetting ? 'Click the exact Headquarters location on the map.' : (headquartersLocationDraft ? `Pinned at ${headquartersLocationDraft.lat.toFixed(5)}, ${headquartersLocationDraft.lon.toFixed(5)}` : 'Headquarters location is not set.');
});
$('#hq-sector-use-device-location')?.addEventListener('click', () => {
  const status = $('#hq-sector-location-status');
  if (!navigator.geolocation) { status.textContent = 'This browser does not provide device location. Click the map to set the HQ pin manually.'; return; }
  status.textContent = 'Requesting device location. Allow the browser permission prompt to continue…';
  navigator.geolocation.getCurrentPosition(async (position) => {
    headquartersLocationDraft = {lat: position.coords.latitude, lon: position.coords.longitude};
    headquartersLocationSetting = false;
    $('#hq-sector-place-location').textContent = 'Set HQ location on map';
    status.textContent = `Device location accuracy ±${Math.round(position.coords.accuracy)} m · ${headquartersLocationDraft.lat.toFixed(5)}, ${headquartersLocationDraft.lon.toFixed(5)}. Saving HQ pin…`;
    await saveHeadquartersSectors();
  }, (error) => {
    const reason = error.code === 1 ? 'Location permission was denied.' : error.code === 3 ? 'Location request timed out.' : 'Device location is unavailable.';
    status.textContent = `${reason} Click the map to set the HQ pin manually.`;
  }, {enableHighAccuracy:true, timeout:15000, maximumAge:0});
});
async function removeHeadquartersSector(event) {
  const button = event.target.closest('[data-remove-sector]');
  if (!button || !window.hqSectorPayload) return;
  const sector = visibleHeadquartersSectors(window.hqSectorPayload).find((item) => item.id === button.dataset.removeSector);
  if (!sector || !window.confirm(`Delete ${sector.name} (${sector.id}) from the HQ sector setup?`)) return;
  const nextSectors = (headquartersSectorDrafts ?? window.hqSectorPayload.sectors.map((item) => ({...item, bounds:{...item.bounds}})))
    .filter((item) => item.id !== sector.id);
  if (!nextSectors.length) { $('#hq-sector-config-status').textContent = 'At least one sector must remain configured.'; return; }
  headquartersSectorDrafts = nextSectors;
  renderHeadquartersSectorDraftList(window.hqSectorPayload);
  renderHeadquartersSectorMap(window.hqSectorPayload);
  if (!await saveHeadquartersSectors()) await loadHeadquartersSectors();
}
document.querySelectorAll('#hq-sector-list, #hq-sector-draft-list').forEach((list) => list.addEventListener('click', removeHeadquartersSector));
$('#authorization-evaluate').addEventListener('click', () => void evaluateAuthorizationScenario());
$('#classify-trajectory')?.addEventListener('click', () => void submitKinematicTrajectory());
$('#trajectory-assessment-list, #sector-track-list')?.addEventListener('click', (event) => { const detail=event.target.closest('[data-track-details]'); if(detail){void openTrackEventHistory(detail);return;} const review=event.target.closest('[data-track-review]'); if(review)void submitTrackReview(review); });
$('#sector-track-refresh')?.addEventListener('click', () => void loadSectorTrackHistory());
$('#refresh-trajectory-assessments')?.addEventListener('click', () => void refreshKinematicAssessments());
$('#authorization-scenario').addEventListener('change', () => {
  const payload = window.authorizationSimulationCache;
  if (!payload) return;
  renderAuthorizationMap(payload, payload.scenarios.find((item) => item.id === $('#authorization-scenario').value));
  $('#authorization-result').textContent = 'Select a scenario, then evaluate the simulated telemetry point.';
});
$('#alert-run-select').addEventListener('change', (event) => {
  void loadAlertsForRun(event.target.value);
});

$('#video-input').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (file) showSelectedFile(file);
});
$('#photo-input').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (file) selectPhoto(file);
});
$('#photo-detect-button').addEventListener('click', detectPhoto);
$('#photo-reset-button').addEventListener('click', resetPhotoDetection);
$('#photo-download-image').addEventListener('click', () => downloadPhotoResult('image'));
$('#photo-download-json').addEventListener('click', () => downloadPhotoResult('json'));
$('#input-video-player').addEventListener('loadedmetadata', () => {
  renderVideoMetadata(state.inputMetadata || {});
  updateCurrentVideoFrame();
  updateVideoPlaybackControls();
  updateTrajectoryOverlayToggle();
});
$('#input-video-player').addEventListener('timeupdate', () => {
  updateCurrentVideoFrame();
  updateVideoPlaybackControls();
});
$('#input-video-player').addEventListener('play', updateVideoPlaybackControls);
$('#input-video-player').addEventListener('pause', updateVideoPlaybackControls);
$('#input-video-player').addEventListener('ended', updateVideoPlaybackControls);
$('#input-video-player').addEventListener('emptied', () => {
  updateVideoPlaybackControls();
  updateTrajectoryOverlayToggle();
});
$('#video-play-toggle').addEventListener('click', async () => {
  const player = $('#input-video-player');
  if (player.paused || player.ended) {
    if (player.ended) player.currentTime = 0;
    try {
      await player.play();
    } catch (error) {
      $('#current-frame-status').textContent = `Could not play video: ${error.message}`;
    }
  } else {
    player.pause();
  }
  updateVideoPlaybackControls();
});
$('#video-trajectory-toggle').addEventListener('click', () => {
  if ($('#video-trajectory-toggle').disabled) return;
  state.showVideoTrajectory = !state.showVideoTrajectory;
  updateTrajectoryOverlayToggle();
});
$('#input-video-player').addEventListener('seeked', () => {
  updateCurrentVideoFrame();
  updateVideoPlaybackControls();
});
window.addEventListener('resize', drawVideoTrailOverlay);
$('#input-video-player').addEventListener('error', () => {
  $('#current-frame-status').textContent = state.selectedFile
    ? 'Video preview unavailable'
    : state.playerRunId
      ? 'Stored source video unavailable'
      : 'Video preview unavailable';
});
$('#active-alert-list').addEventListener('click', handleAlertEvidenceClick);
$('#source-cctv')?.addEventListener('click', () => selectInputSource('cctv'));
$('#source-upload').addEventListener('click', () => selectInputSource('upload'));
document.querySelectorAll('[data-input-type]').forEach((button) => {
  button.addEventListener('click', async () => {
    if (state.phase3Input?.source_type === 'upload') await stopPhase3Input();
    state.preparedThermalRun = null;
    $('#start-analysis-flow').hidden = true;
    state.inputType = button.dataset.inputType;
    triggerSensorModeFeedback(state.inputType);
    document.querySelectorAll('[data-input-type]').forEach((choice) => {
      choice.setAttribute('aria-pressed', String(choice === button));
    });
    const isRadar = state.inputType === 'radar';
    if (state.inputType === 'thermal') setThermalPreparationView(true);
    else if (!state.selectedFile) setThermalPreparationView(false);
    $('#modality-note').textContent = isRadar
      ? 'Radar input format is not currently supported. The project does not define a supported Radar upload format.'
      : state.inputType === 'thermal'
        ? 'Thermal video input uses the existing frame reader. Supported containers: MP4, AVI, MOV, MKV.'
        : 'RGB visible-light video. Supported containers: MP4, AVI, MOV, MKV.';
    $('#file-picker-panel').hidden = isRadar;
    $('#upload-status-panel').hidden = false;
    if (state.selectedFile) $('#video-info-type').textContent = state.inputType.toUpperCase();
    $('#input-prepared').hidden = true;
    setBusy(false);
  });
});
document.querySelectorAll('[data-thermal-stage-target]').forEach((button) => {
  button.addEventListener('click', () => selectPipelineStage(button.dataset.thermalStageTarget));
});
$('#test-cctv').addEventListener('click', () => prepareCCTV('test'));
$('#start-cctv').addEventListener('click', () => prepareCCTV('start'));
$('#stop-cctv').addEventListener('click', stopCCTV);
$('#prepare-input').addEventListener('click', beginAnalysis);
$('#phase3-acquire').addEventListener('click', acquireNextPhase3Frame);
$('#phase3-stop').addEventListener('click', stopPhase3Input);
$('#phase4-detect').addEventListener('click', detectNextPhase4Frame);
$('#phase5-track').addEventListener('click', trackNextPhase5Frame);
$('#phase5-reset').addEventListener('click', resetPhase5Tracking);
$('#start-analysis-flow').addEventListener('click', startPreparedAnalysis);
$('#stop-analysis').addEventListener('click', stopAnalysis);
$('#view-processing-run').addEventListener('click', viewProcessingRun);
$('#new-analysis').addEventListener('click', async () => {
  await navigateTo('/video-analysis');
  newAnalysis();
});
$('#track-search').addEventListener('input', resetPageAndRender);
$('#status-filter').addEventListener('change', resetPageAndRender);
$('#class-filter').addEventListener('change', resetPageAndRender);
$('#clear-filters').addEventListener('click', () => {
  $('#track-search').value = '';
  $('#status-filter').value = '';
  $('#class-filter').value = '';
  resetPageAndRender();
});
$('#track-rows').addEventListener('click', (event) => {
  const button = event.target.closest('[data-track-id]');
  if (button) openTrack(button.dataset.trackId);
});
$('#close-detail').addEventListener('click', closeDetail);
$('#detail-backdrop').addEventListener('click', closeDetail);
$('#drop-zone').addEventListener('dragover', (event) => {
  event.preventDefault();
  $('#drop-zone').classList.add('drag-active');
});
$('#drop-zone').addEventListener('dragleave', () => $('#drop-zone').classList.remove('drag-active'));
$('#drop-zone').addEventListener('drop', (event) => {
  event.preventDefault();
  $('#drop-zone').classList.remove('drag-active');
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const input = $('#video-input');
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  showSelectedFile(file);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('#detail-backdrop').hidden) closeDetail();
});

$('#theme-toggle').addEventListener('click', () => {
  applyColorTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});

document.querySelectorAll('[data-theme-choice]').forEach((button) => {
  button.addEventListener('click', () => applyColorTheme(button.dataset.themeChoice));
});

initialize();
