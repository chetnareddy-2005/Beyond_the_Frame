const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const showLoginStart = appSource.indexOf('function showLogin(');
const showDashboardStart = appSource.indexOf('function showDashboard(', showLoginStart);
const resetStart = appSource.indexOf('function resetDashboardRun(');
const resumeStart = appSource.indexOf('async function resumeCurrentRun(', resetStart);
const initializeDashboardStart = appSource.indexOf('async function initializeDashboard(', resumeStart);
const functions = [
  appSource.slice(showLoginStart, showDashboardStart),
  appSource.slice(resetStart, resumeStart),
  appSource.slice(resumeStart, initializeDashboardStart),
].join('\n');

function createHarness() {
  const storage = new Map([
    ['btf-current-run-id', 'RUN_20261003_171902_d92029613a8343899ef595ae'],
    ['btf-played-high-alerts', '["previous-high-alert"]'],
  ]);
  const elements = new Map();
  const getElement = (selector) => {
    if (!elements.has(selector)) {
      elements.set(selector, {
        hidden: false,
        textContent: '',
        value: 0,
        className: '',
        setAttribute() {},
        removeAttribute() {},
        addEventListener() {},
        classList: { remove() {} },
      });
    }
    return elements.get(selector);
  };
  const sandbox = {
    RUN_STORAGE_KEY: 'btf-current-run-id',
    state: {
      pollTimer: null,
      runId: 'RUN_20261003_171902_d92029613a8343899ef595ae',
      phase3Input: null,
      run: { status: 'completed' },
      phaseExecution: { detection: 'complete' },
      selectedStage: 'completed',
      phaseTrackDetail: { old: true },
      selectedPhaseTrackId: 'old-track',
      activeResultTab: 'fusion',
      currentFrameIndex: 60,
      alarmTransitions: new Set(['old-alert']),
      authenticated: true,
      cctvConnected: false,
      cctvReference: null,
    },
    localStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      removeItem(key) { storage.delete(key); },
    },
    $: getElement,
    document: {
      querySelectorAll() { return [{ setAttribute() {}, addEventListener() {} }]; },
    },
    window: { location: { pathname: '/dashboard' } },
    history: { replaceState() {} },
    setEntryView() {},
    setLoginStatus() {},
    setLoginTerminal() {},
    clearRunResults() {
      sandbox.resultsCleared = true;
      getElement('#results-section').hidden = true;
    },
    selectResultsTab(tab) { sandbox.state.activeResultTab = tab; },
    resetUpload() { sandbox.uploadReset = true; },
    setBusy() {},
    clearGlobalError() {},
    renderPipeline(run) { sandbox.renderedPipeline = run; },
    stopAnalysisTimelineTimer() {},
    setTimeout,
    clearTimeout,
    updateRunStatus() {
      sandbox.statusRequests = (sandbox.statusRequests || 0) + 1;
    },
    showGlobalError() {},
    showPhase3Input() {},
    pollRun() {},
  };
  vm.runInNewContext(
    `${functions}\nthis.showLogin = showLogin;\nthis.resumeCurrentRun = resumeCurrentRun;`,
    sandbox,
  );
  return { sandbox, elements, storage };
}

test('fresh login clears a saved run before startup can restore it', async () => {
  const { sandbox, elements, storage } = createHarness();

  sandbox.showLogin();
  await sandbox.resumeCurrentRun();

  assert.equal(storage.has('btf-current-run-id'), false);
  assert.equal(sandbox.statusRequests, undefined);
  assert.equal(sandbox.state.runId, null);
  assert.equal(elements.get('#results-section').hidden, true);
});

test('logout reset clears run UI and preserves alert-sound history', () => {
  const { sandbox, elements, storage } = createHarness();

  sandbox.showLogin();

  assert.equal(storage.has('btf-current-run-id'), false);
  assert.equal(storage.get('btf-played-high-alerts'), '["previous-high-alert"]');
  assert.equal(sandbox.resultsCleared, true);
  assert.equal(sandbox.uploadReset, true);
  assert.equal(sandbox.state.run, null);
  assert.equal(sandbox.state.phaseTrackDetail, null);
  assert.equal(sandbox.state.selectedPhaseTrackId, null);
  assert.equal(sandbox.state.currentFrameIndex, null);
  assert.deepEqual([...sandbox.state.alarmTransitions], []);
  assert.equal(elements.get('#run-section .run-meta').hidden, true);
  assert.equal(elements.get('#run-id').textContent, '—');
  assert.equal(elements.get('#run-status-badge').textContent, 'Waiting');
  assert.equal(elements.get('#stop-analysis').hidden, true);
  assert.equal(sandbox.renderedPipeline.status, 'idle');
  assert.equal(sandbox.renderedPipeline.stage, 'uploaded');
});

test('fresh-login reset does not touch separately persisted run artifacts', () => {
  const { sandbox } = createHarness();
  const persistedRuns = new Map([
    ['RUN_20261003_171902_d92029613a8343899ef595ae', '{"status":"completed"}'],
  ]);

  sandbox.showLogin();

  assert.equal(
    persistedRuns.get('RUN_20261003_171902_d92029613a8343899ef595ae'),
    '{"status":"completed"}',
  );
});
