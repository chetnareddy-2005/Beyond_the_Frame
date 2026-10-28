const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const normalizedAppSource = appSource.replace(/\r\n/g, '\n');
const indexSource = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const start = appSource.indexOf('function parseRoute(');
const end = appSource.indexOf('function navigateTo(', start);
const routeParser = appSource.slice(start, end);
const sandbox = {};
vm.runInNewContext(`${routeParser}\nthis.parseRoute = parseRoute;`, sandbox);
const accessStart = appSource.indexOf('const SECTOR_ALLOWED_PAGES = new Set(');
const accessEnd = appSource.indexOf('function navigateTo(', accessStart);
const accessSandbox = {};
vm.runInNewContext(
  `${appSource.slice(accessStart, accessEnd)}\nthis.routeAllowedForRole = routeAllowedForRole;`,
  accessSandbox,
);

test('sidebar destinations resolve to distinct application pages', () => {
  const routes = [
    ['/dashboard', 'dashboard', 'dashboard'],
    ['/live-cctv', 'live-cctv', 'cctv'],
    ['/results', 'results', 'results'],
    ['/history', 'history', 'history'],
    ['/alerts', 'alerts', 'alerts'],
    ['/reports', 'reports', 'reports'],
    ['/settings', 'settings', 'settings'],
  ];
  for (const [pathname, page, navigation] of routes) {
    assert.deepEqual({ ...sandbox.parseRoute(pathname) }, { page, navigation });
  }
});

test('sidebar contains the requested sector navigation destinations', () => {
  const sidebar = indexSource.match(/<nav>([\s\S]*?)<\/nav>/)?.[1];
  assert.ok(sidebar, 'sidebar navigation should exist');
  const expected = [
    ['/dashboard', 'dashboard'],
    ['/sector-dashboard', 'sector-dashboard'],
    ['/live-cctv', 'cctv'],
    ['/video-analysis', 'video-analysis'],
    ['/results', 'results'],
    ['/history', 'history'],
    ['/alerts', 'alerts'],
    ['/reports', 'reports'],
    ['/settings', 'settings'],
  ];
  for (const [href, navigation] of expected) {
    assert.match(sidebar, new RegExp(`href="${href}" data-navigation="${navigation}"`));
    const route = sandbox.parseRoute(href);
    assert.equal(accessSandbox.routeAllowedForRole('sector', route), true, `${href} should be sector-accessible`);
  }
  for (const page of [
    'live-cctv',
    'video-analysis',
    'results',
    'history',
    'alerts',
    'reports',
    'settings',
    'sector-dashboard',
  ]) {
    assert.match(indexSource, new RegExp(`<section data-route-page="${page}"`), `${page} must retain its existing page`);
  }
  assert.match(appSource, /if \(route\.page === 'live-cctv'\) \{\s*moveRouteNode\('#cctv-input'/);
  assert.match(appSource, /\} else if \(route\.page === 'video-analysis'\) \{\s*moveRouteNode\('#video-analysis'/);
  assert.match(appSource, /\} else if \(route\.page === 'results'\) \{\s*\$\('#results-library'\)\.hidden = false;/);
  assert.match(appSource, /\} else if \(route\.page === 'history'\) \{\s*moveRouteNode\('#history-section'/);
  assert.match(appSource, /\} else if \(route\.page === 'alerts'\) \{\s*moveRouteNode\('#active-alerts'/);
  assert.match(appSource, /\} else if \(route\.page === 'reports'\) \{\s*await loadHistory\(false\);/);
  assert.match(appSource, /\} else if \(route\.page === 'settings'\) \{\s*\$\('#settings-username'\)/);
  assert.match(appSource, /if \(role && !routeAllowedForRole\(role, route\)\) \{\s*history\.replaceState\(null, '', role === 'sector' \? '\/sector-dashboard' : '\/dashboard'\)/);
  assert.match(appSource, /if \(state\.profile\?\.role === 'sector'\) \{\s*if \(route\.invalid \|\| window\.location\.pathname === '\/' \|\| window\.location\.pathname === '\/login'\)/);
  assert.match(appSource, /if \(sectorAccount\) link\.hidden = !sectorNavigation\.has\(link\.dataset\.navigation\)/);
  assert.equal(accessSandbox.routeAllowedForRole('sector', sandbox.parseRoute('/headquarters-sectors')), false);
  assert.equal(accessSandbox.routeAllowedForRole('sector', sandbox.parseRoute('/authorization')), false);
  assert.equal(accessSandbox.routeAllowedForRole('headquarters_admin', sandbox.parseRoute('/video-analysis')), false);
  assert.equal(accessSandbox.routeAllowedForRole('headquarters_admin', sandbox.parseRoute('/headquarters-sectors')), true);
});

test('stored result and report detail routes retain their parent sidebar state', () => {
  const runId = 'RUN_20261003_141356_b7f92b69fd4d6559113218b4';
  assert.deepEqual(
    { ...sandbox.parseRoute(`/results/${runId}`) },
    { page: 'result-detail', navigation: 'results', runId },
  );
  assert.deepEqual(
    { ...sandbox.parseRoute(`/reports/${runId}`) },
    { page: 'report-detail', navigation: 'reports', runId },
  );
});

test('stored result detail rehydrates when cached run state has no loaded results', () => {
  const runId = 'RUN_20261006_084105_cb04d1a76ab6fb175b5579b0';
  const routeStart = normalizedAppSource.indexOf("state.routeRunId = route.runId;");
  const conditionStart = normalizedAppSource.indexOf('if (', routeStart);
  const conditionEnd = normalizedAppSource.indexOf(') {\n      state.runId = route.runId;', conditionStart);
  assert.notEqual(routeStart, -1);
  assert.notEqual(conditionStart, -1);
  assert.notEqual(conditionEnd, -1);
  const shouldRestore = new Function(
    'state',
    'route',
    `return (${normalizedAppSource.slice(conditionStart + 'if ('.length, conditionEnd)});`,
  );
  const cachedRunWithoutRenderedResults = {
    runId,
    run: { run_id: runId, status: 'completed' },
    resultsLoadedRunId: null,
  };

  assert.equal(shouldRestore(cachedRunWithoutRenderedResults, { runId }), true);
  assert.match(appSource, /resultsLoadedRunId: null/);
  assert.match(appSource, /state\.resultsLoadedRunId = runId;/);
  assert.match(appSource, /state\.resultsLoadedRunId === route\.runId/);
  const clearStart = appSource.indexOf('function clearRunResults()');
  const clearEnd = appSource.indexOf('function showSelectedFile(', clearStart);
  assert.match(appSource.slice(clearStart, clearEnd), /state\.resultsLoadedRunId = null;/);
});

test('stored result card navigation preserves the selected run ID in its route', () => {
  const openStart = appSource.indexOf('async function openHistoryRun(runId)');
  const openEnd = appSource.indexOf('function changePage(', openStart);
  const openHistory = appSource.slice(openStart, openEnd);

  assert.match(openHistory, /navigateTo\(`\/results\/\$\{encodeURIComponent\(runId\)\}`\)/);
  assert.match(openHistory, /return state\.runId === runId && Boolean\(state\.run\)/);
});

test('stored result details reveal cached results and populate the trajectory selector', () => {
  assert.match(
    appSource,
    /if \(route\.page === 'result-detail' && resultsAlreadyLoaded\) \{\s*\$\('#results-section'\)\.hidden = false;\s*void renderDashboardTrajectory\(\);/,
  );
  assert.match(
    appSource,
    /if \(\['dashboard', 'result-detail'\]\.includes\(state\.route\?\.page\)\) \{\s*void renderDashboardTrajectory\(\);/,
  );
});

test('stored result hydration uses only read-only run data endpoints', () => {
  const loaderStart = appSource.indexOf('async function loadResults(runId)');
  const loaderEnd = appSource.indexOf('function renderSummary()', loaderStart);
  const loader = appSource.slice(loaderStart, loaderEnd);
  assert.match(loader, /api\(`\$\{prefix\}\/overview`\)/);
  assert.match(loader, /api\(`\$\{prefix\}\/tracks`\)/);
  assert.match(loader, /api\(`\$\{prefix\}\/fusion\/summary`\)/);
  assert.match(loader, /api\(`\$\{prefix\}\/report`\)/);
  assert.doesNotMatch(loader, /method:\s*['"]POST['"]|\/start|phase4\/detect|phase5\/track|vlm\/verify/i);
});

test('loading a stored result populates run-scoped data without creating a run', async () => {
  const runId = 'RUN_20261006_084105_cb04d1a76ab6fb175b5579b0';
  const loaderStart = normalizedAppSource.indexOf('async function loadResults(runId)');
  const loaderEnd = normalizedAppSource.indexOf('function renderSummary()', loaderStart);
  const section = { hidden: true };
  const requests = [];
  let trajectoryRendered = 0;
  const sandbox = {
    state: {
      runId,
      route: { page: 'result-detail' },
      resultsRequest: 0,
      trajectoryDetailsRunId: null,
      trajectoryDetails: new Map(),
      tracks: [],
    },
    $(selector) {
      assert.equal(selector, '#results-section');
      return section;
    },
    async api(pathname) {
      requests.push(pathname);
      if (pathname.endsWith('/overview')) return { total_tracks: 2, confident_tracks: 2 };
      if (pathname.endsWith('/tracks')) {
        return { tracks: [{ track_id: '1' }, { track_id: '2' }], count: 2 };
      }
      if (pathname.endsWith('/fusion/summary')) return { fusion_mode_counts: { detector_temporal: 2 } };
      if (pathname.endsWith('/report')) {
        return {
          run_id: runId,
          alert: {
            level: 'HIGH',
            evidence: [
              { track_id: 1, observation: { frame_index: 248, timestamp: 247 / 30 } },
              { track_id: 2, observation: { frame_index: 148, timestamp: 147 / 30 } },
            ],
          },
          tracks: [{ track_id: '1' }, { track_id: '2' }],
        };
      }
      throw new Error(`Unexpected stored-run request: ${pathname}`);
    },
    renderAlerts(report) { sandbox.state.alertData = report; },
    populateClassFilter() {},
    renderSummary() {},
    renderTracks() {},
    renderSelectedPhaseDetail() {},
    renderDashboardTrajectory() { trajectoryRendered += 1; },
    clearGlobalError() {},
    showGlobalError(message) { throw new Error(message); },
  };
  vm.runInNewContext(
    `${normalizedAppSource.slice(loaderStart, loaderEnd)}\nthis.loadResults = loadResults;`,
    sandbox,
  );

  await sandbox.loadResults(runId);

  assert.deepEqual(requests, [
    `/api/runs/${runId}/overview`,
    `/api/runs/${runId}/tracks`,
    `/api/runs/${runId}/fusion/summary`,
    `/api/runs/${runId}/report`,
  ]);
  assert.equal(sandbox.state.resultsLoadedRunId, runId);
  assert.equal(sandbox.state.tracks.length, 2);
  assert.equal(sandbox.state.alertData.alert.level, 'HIGH');
  assert.deepEqual(
    sandbox.state.report.alert.evidence.map((item) => item.observation.frame_index),
    [248, 148],
  );
  assert.equal(section.hidden, false);
  assert.equal(trajectoryRendered, 1);
  assert.ok(requests.every((pathname) => !/\/start$|\/detect|\/track$|\/vlm|\/fusion$/.test(pathname)));
});

test('detail routes reject malformed run IDs and navigation remains same-tab', () => {
  assert.equal(sandbox.parseRoute('/results/not-a-run').invalid, true);
  assert.doesNotMatch(indexSource, /target\s*=\s*["']_blank["']/i);
  assert.doesNotMatch(appSource, /window\.open\s*\(/);
  assert.match(appSource, /history\.pushState\(routeState, '', targetPath\)/);
  assert.match(appSource, /state\.resultsDateFilter = route\.page === 'results'/);
  assert.match(appSource, /window\.addEventListener\('popstate'/);
});

test('Dashboard preserves an explicitly selected completed run when leaving its detail route', () => {
  const normalizedSource = appSource.replace(/\r\n/g, '\n');
  const conditionStart = normalizedSource.indexOf('if (\n    state.routeRunId');
  const conditionEnd = normalizedSource.indexOf(') {\n    state.routeRunId = null;', conditionStart);
  assert.notEqual(conditionStart, -1);
  assert.notEqual(conditionEnd, -1);
  const shouldClearRun = new Function(
    'state',
    'route',
    `return (${normalizedSource.slice(conditionStart + 'if ('.length, conditionEnd)});`,
  );
  const selectedRun = {
    routeRunId: 'RUN_20261003_141356_b7f92b69fd4d6559113218b4',
    runId: 'RUN_20261003_141356_b7f92b69fd4d6559113218b4',
    run: { status: 'completed' },
  };

  assert.equal(shouldClearRun(selectedRun, { page: 'dashboard' }), false);
  assert.equal(shouldClearRun(selectedRun, { page: 'results' }), true);
});

test('source video IDs are rendered separately from technical run IDs', () => {
  assert.match(indexSource, /<th>VIDEO ID<\/th><th>RUN ID<\/th>/);
  assert.match(appSource, /run\.source_video_id \|\| 'Unavailable'/);
  assert.match(appSource, /title\.textContent = run\.source_video_id \|\| 'Video ID unavailable'/);
  assert.match(appSource, /Run ID: \$\{run\.run_id\}/);
  assert.match(appSource, /run\.source_video_id \|\| 'Video ID unavailable'\} · \$\{runFilename\(run\)/);
  assert.match(appSource, /function runFilename\(run\)[\s\S]*run\?\.original_filename \|\| run\?\.input_filename/);
  assert.match(indexSource, /ACTUAL ANALYSIS TIME/);
});

test('thermal preparation preserves prior results and offers the detection/tracking run workflow', () => {
  const beginStart = normalizedAppSource.indexOf('async function beginAnalysis()');
  const beginEnd = normalizedAppSource.indexOf('async function startPreparedAnalysis()', beginStart);
  const beginAnalysis = normalizedAppSource.slice(beginStart, beginEnd);
  const thermalBranchStart = beginAnalysis.indexOf('if (thermalPreparation) {');
  const thermalBranchEnd = beginAnalysis.indexOf('} else {', thermalBranchStart);
  const thermalBranch = beginAnalysis.slice(thermalBranchStart, thermalBranchEnd + 1);

  assert.match(thermalBranch || '', /setThermalPreparationView\(true\)/);
  assert.match(thermalBranch || '', /state\.preparedThermalRun = \{/);
  assert.doesNotMatch(thermalBranch || '', /clearRunResults\(\)/);
  assert.match(
    normalizedAppSource,
    /function setThermalPreparationView\(active\)[\s\S]*'#active-alerts'[\s\S]*'#dashboard-trajectory'/,
  );
  assert.match(
    normalizedAppSource,
    /if \(route\.page === 'dashboard' && state\.phase3Input\?\.input_type === 'thermal'\)/,
  );
  assert.match(beginAnalysis, /full-video thermal detection and tracking are enabled/i);
  assert.match(beginAnalysis, /#start-analysis-flow'\)\.hidden = !\['rgb', 'thermal'\]\.includes\(created\.input_type\)/);
  assert.match(
    normalizedAppSource,
    /function setPhaseExecution\(stage, status\) \{[\s\S]*renderPipeline\(state\.preparedThermalRun \|\| state\.run \|\|/,
  );
  assert.match(
    normalizedAppSource,
    /function selectPipelineStage\(stage, scroll = true\) \{[\s\S]*renderPipeline\(state\.preparedThermalRun \|\| state\.run \|\|/,
  );
  const startStart = normalizedAppSource.indexOf('async function startPreparedAnalysis()');
  const startEnd = normalizedAppSource.indexOf('async function pollEarlyUavAlert(', startStart);
  const startFlow = normalizedAppSource.slice(startStart, startEnd);
  assert.match(startFlow, /const thermalRun = state\.preparedThermalRun/);
  assert.match(startFlow, /\/api\/runs\/\$\{encodeURIComponent\(runId\)\}\/start/);
  assert.match(startFlow, /state\.run = \{ \.\.\.thermalRun, status: 'processing'/);
  assert.match(startFlow, /renderAlerts\(\{ run_id: runId, alert_status: 'not_run' \}\)/);
  assert.match(startFlow, /Unsupported downstream stages will be marked as skipped/);
  assert.match(indexSource, /Thermal results include detector observations, tracking, offline temporal consensus, and review alerts for Drone-class tracks/);
  assert.match(indexSource, /account-dropdown[\s\S]*data-theme-choice="dark"[\s\S]*data-theme-choice="light"/);
  assert.match(appSource, /const thermalOnly = state\.run\?\.input_type === 'thermal'/);
  assert.match(appSource, /PREDICTED CLASS/);
  assert.match(appSource, /if \(track\.analysis_mode === 'thermal_detection_tracking_only'\)/);
  assert.match(appSource, /Thermal Track Evidence Ledger/);
  assert.match(appSource, /RAW CONFIDENCE/);
  assert.match(appSource, /ledger\?\.class_changes/);
  assert.match(appSource, /ledger\?\.observation_gaps/);
  assert.match(appSource, /evidence_ledger/);
  assert.match(appSource, /renderTemporalConsensus\(ledger\)/);
  assert.match(appSource, /Supporting predicted frames/);
  assert.match(appSource, /report\?\.alert_status === 'not_run'/);
  assert.match(appSource, /run\.status === 'processing' && run\.input_type !== 'thermal'/);
  assert.match(appSource, /state\.playerRunId !== state\.runId/);
});

test('stored Result Details includes its run-scoped Analysis Timeline', () => {
  assert.match(
    normalizedAppSource,
    /if \(route\.page === 'result-detail'\) moveRouteNode\('#analysis-timeline', '#result-detail-slot'\)/,
  );
  assert.match(appSource, /detailStat\('Source Video ID', state\.run\.source_video_id \|\| 'Video ID unavailable'\)/);
  assert.match(appSource, /storedRun\.source_video_id \|\| 'Video ID unavailable'/);
  assert.match(appSource, /Source Video \$\{state\.report\.source_video_id \|\| 'ID unavailable'\}/);
  assert.match(appSource, /Source Video \$\{state\.historyRuns\.find/);
});

test('temporal consensus details display the decision and its actual frame references', () => {
  const start = appSource.indexOf('function renderTemporalConsensus(');
  const end = appSource.indexOf('\nfunction renderDetail(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const sandbox = {
    detailStat: (label, value) => `${label}: ${value}`,
    escapeHTML: (value) => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
    formatNumber: (value) => String(value),
  };
  vm.runInNewContext(
    `${appSource.slice(start, end)}\nthis.renderTemporalConsensus = renderTemporalConsensus;`,
    sandbox,
  );

  const rendered = sandbox.renderTemporalConsensus({
    temporal_consensus: {
      source: 'persisted_temporal_consensus',
      result: {
        consensus_label: 'Uncertain',
        consensus_confidence: 0.41,
        observation_count: 3,
        score_interpretation: 'heuristic decision score; not calibrated probability',
        reasons: ['class_switching_conflict'],
        supporting_frame_indexes: [1, 3],
        contradictory_frame_indexes: [2],
      },
    },
  });

  assert.match(rendered, /Track-level decision: Uncertain/);
  assert.match(rendered, /Supporting predicted frames: 1, 3/);
  assert.match(rendered, /Contradictory predicted frames: 2/);
  assert.match(rendered, /not calibrated probability/);
});
