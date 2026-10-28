const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function sourceBetween(startMarker, endMarker) {
  const start = appSource.indexOf(startMarker);
  const end = appSource.indexOf(endMarker, start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return appSource.slice(start, end);
}

function makeElement() {
  return { hidden: false, disabled: false, textContent: '', value: 0 };
}

function makeElements() {
  const elements = new Map();
  return {
    elements,
    $: (selector) => {
      if (!elements.has(selector)) elements.set(selector, makeElement());
      return elements.get(selector);
    },
  };
}

const warningSource = sourceBetween('async function showConcurrentRunWarning(', 'async function viewProcessingRun(');
const viewSource = sourceBetween('async function viewProcessingRun(', 'function renderPipeline(');
const startSource = sourceBetween('async function startPreparedAnalysis(', 'async function showConcurrentRunWarning(');
const updateRunStatusSource = sourceBetween('async function updateRunStatus(', 'async function pollRun(');

function makeWarningHarness(runs) {
  const { $, elements } = makeElements();
  const calls = [];
  const sandbox = {
    state: { runId: 'RUN_A', run: { status: 'prepared' }, busy: false, cancelPending: false },
    $,
    async api(url, options) {
      calls.push({ url, options });
      if (url === '/api/runs') return { runs };
      throw new Error('Unexpected request');
    },
    async openHistoryRun(runId) {
      sandbox.state.runId = runId;
      sandbox.state.run = { run_id: runId, status: 'processing', stage: 'detection' };
      return true;
    },
  };
  vm.runInNewContext(
    `${warningSource}\n${viewSource}\nthis.showConcurrentRunWarning = showConcurrentRunWarning; this.viewProcessingRun = viewProcessingRun;`,
    sandbox,
  );
  return { sandbox, $, elements, calls };
}

test('specific start 409 shows concurrency warning instead of generic error', async () => {
  const { $, calls, sandbox } = makeWarningHarness([
    { run_id: 'RUN_B', status: 'processing' },
  ]);
  let concurrencyHandlerCalls = 0;
  let globalErrors = 0;
  const startSandbox = {
    state: { runId: 'RUN_A', busy: false, phase3Input: null },
    $,
    api: async (url, options) => {
      calls.push({ url, options });
      throw new Error('Another video run is already processing (HTTP 409)');
    },
    async showConcurrentRunWarning() {
      concurrencyHandlerCalls += 1;
      await sandbox.showConcurrentRunWarning();
    },
    showGlobalError() { globalErrors += 1; },
    setBusy(value) { startSandbox.state.busy = value; },
  };
  vm.runInNewContext(`${startSource}\nthis.startPreparedAnalysis = startPreparedAnalysis;`, startSandbox);

  await startSandbox.startPreparedAnalysis();

  assert.equal(concurrencyHandlerCalls, 1);
  assert.equal(globalErrors, 0);
  assert.equal($('#concurrent-run-warning').hidden, false);
  assert.match($('#concurrent-run-message').textContent, /Another analysis is currently running/);
  assert.equal($('#view-processing-run').hidden, false);
  assert.equal(calls[0].url, '/api/runs/RUN_A/start');
  assert.equal(calls[1].url, '/api/runs');
});

test('processing-run navigation refreshes the list and selects only that run', async () => {
  const { sandbox, $, calls } = makeWarningHarness([
    { run_id: 'RUN_A', status: 'prepared' },
    { run_id: 'RUN_B', status: 'processing' },
  ]);
  $('#concurrent-run-warning').hidden = false;
  $('#view-processing-run').hidden = false;

  await sandbox.viewProcessingRun();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/runs');
  assert.equal(sandbox.state.runId, 'RUN_B');
  assert.equal(sandbox.state.run.status, 'processing');
  assert.equal($('#concurrent-run-warning').hidden, true);
});

test('a processing run that disappears before navigation gets a retry message', async () => {
  const { sandbox, $, calls } = makeWarningHarness([]);
  $('#concurrent-run-warning').hidden = false;
  $('#view-processing-run').hidden = false;

  await sandbox.viewProcessingRun();

  assert.equal(calls.length, 1);
  assert.equal(sandbox.state.runId, 'RUN_A');
  assert.equal($('#concurrent-run-warning').hidden, false);
  assert.equal($('#view-processing-run').hidden, true);
  assert.match($('#concurrent-run-message').textContent, /no longer processing.*try Start Analysis again/i);
});

test('the 409 warning omits navigation when no processing run is listed', async () => {
  const { sandbox, $ } = makeWarningHarness([]);

  await sandbox.showConcurrentRunWarning();

  assert.equal($('#concurrent-run-warning').hidden, false);
  assert.equal($('#view-processing-run').hidden, true);
  assert.match($('#concurrent-run-message').textContent, /Please try Start Analysis again/);
});

test('processing status refresh keeps Stop Analysis tied to the selected run', async () => {
  const { $, elements } = makeElements();
  const sandbox = {
    state: {
      runId: 'RUN_B',
      run: null,
      selectedFile: null,
      cancelPending: false,
      busy: false,
      phaseExecution: {},
    },
    $,
    api: async () => ({
      run_id: 'RUN_B',
      status: 'processing',
      stage: 'detection',
      progress: 8,
      input_metadata: {},
    }),
    refreshStoredFrameObservations() {},
    renderVideoMetadata() {},
    renderPipeline() {},
    readableStage: String,
    formatNumber: String,
  };
  vm.runInNewContext(`${updateRunStatusSource}\nthis.updateRunStatus = updateRunStatus;`, sandbox);

  await sandbox.updateRunStatus('RUN_B');

  assert.equal(elements.get('#stop-analysis').hidden, false);
});
