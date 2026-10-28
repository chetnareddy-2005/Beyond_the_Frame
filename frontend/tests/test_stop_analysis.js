const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function extractFunction(name, nextName) {
  const start = appSource.indexOf(`async function ${name}(`);
  const end = appSource.indexOf(`async function ${nextName}(`, start);
  return appSource.slice(start, end);
}

const updateRunStatusSource = extractFunction('updateRunStatus', 'pollRun');
const stopAnalysisSource = extractFunction('stopAnalysis', 'pollEarlyUavAlert');
const renderPipelineSource = appSource.slice(
  appSource.indexOf('function renderPipeline('),
  appSource.indexOf('function setPhaseExecution(', appSource.indexOf('function renderPipeline(')),
);

function makeElement() {
  return { hidden: false, disabled: false, textContent: '', value: 0 };
}

async function updateButtonFor(status) {
  const elements = new Map();
  const $ = (selector) => {
    if (!elements.has(selector)) elements.set(selector, makeElement());
    return elements.get(selector);
  };
  const sandbox = {
    state: {
      runId: 'RUN_selected',
      run: null,
      selectedFile: null,
      cancelPending: false,
      busy: false,
      phaseExecution: {},
    },
    $,
    api: async () => ({
      run_id: 'RUN_selected',
      status,
      stage: 'detection',
      progress: status === 'processing' ? 8 : 0,
      input_metadata: {},
    }),
    refreshStoredFrameObservations() {},
    renderVideoMetadata() {},
    renderPipeline() {},
    readableStage: String,
    formatNumber: String,
  };
  vm.runInNewContext(`${updateRunStatusSource}\nthis.updateRunStatus = updateRunStatus;`, sandbox);
  await sandbox.updateRunStatus('RUN_selected');
  return $('#stop-analysis');
}

test('Stop Analysis is visible only for the displayed processing run', async () => {
  for (const status of ['prepared', 'completed', 'cancelled', 'failed']) {
    const button = await updateButtonFor(status);
    assert.equal(button.hidden, true, `${status} should hide Stop Analysis`);
  }

  const processingButton = await updateButtonFor('processing');
  assert.equal(processingButton.hidden, false);
});

function makeStopHarness(runStatus) {
  const calls = [];
  const elements = new Map();
  const $ = (selector) => {
    if (!elements.has(selector)) elements.set(selector, makeElement());
    return elements.get(selector);
  };
  const sandbox = {
    state: {
      runId: 'RUN_selected',
      run: { status: runStatus },
      cancelPending: false,
      pollTimer: null,
      authenticated: true,
    },
    $,
    api: async (url, options) => {
      calls.push({ url, options });
      return { run_id: 'RUN_selected', status: 'cancelled' };
    },
    updateRunStatus: async () => {
      sandbox.state.run = { status: 'cancelled' };
      return sandbox.state.run;
    },
    clearTimeout() {},
    pollRun() {},
  };
  vm.runInNewContext(`${stopAnalysisSource}\nthis.stopAnalysis = stopAnalysis;`, sandbox);
  return { sandbox, calls };
}

test('prepared displayed run does not send a cancellation request', async () => {
  const { sandbox, calls } = makeStopHarness('prepared');

  await sandbox.stopAnalysis();

  assert.deepEqual(calls, []);
});

test('processing cancellation targets only the selected run ID', async () => {
  const { sandbox, calls } = makeStopHarness('processing');
  sandbox.state.runId = 'RUN_selected_processing';

  await sandbox.stopAnalysis();

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    '/api/runs/RUN_selected_processing/cancel',
  );
  assert.equal(calls[0].options.method, 'POST');
});

function makePipelineElement() {
  const descendants = new Map();
  const classes = new Set();
  return {
    hidden: false,
    disabled: false,
    textContent: '',
    value: 0,
    attributes: {},
    children: [],
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
    },
    querySelector(selector) {
      if (!descendants.has(selector)) descendants.set(selector, makePipelineElement());
      return descendants.get(selector);
    },
    append(...children) {
      this.children.push(...children);
    },
    replaceChildren(...children) {
      this.children = children;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

function renderPipelineFor(run) {
  const items = new Map();
  const elements = new Map();
  const sandbox = {
    PIPELINE_STAGES: [
      'uploaded', 'detection', 'tracking', 'temporal', 'reliability',
      'evidence', 'vlm', 'fusion', 'assessment',
    ],
    state: { phaseExecution: {}, selectedStage: 'detection' },
    document: {
      querySelector(selector) {
        if (!items.has(selector)) items.set(selector, makePipelineElement());
        return items.get(selector);
      },
      createElement() {
        return makePipelineElement();
      },
    },
    $: (selector) => {
      if (!elements.has(selector)) elements.set(selector, makePipelineElement());
      return elements.get(selector);
    },
    renderAnalysisTimeline() {},
    stopAnalysisTimelineTimer() {},
  };
  vm.runInNewContext(`${renderPipelineSource}\nthis.renderPipeline = renderPipeline;`, sandbox);
  sandbox.renderPipeline(run);
  const item = (stage) => items.get(`[data-stage="${stage}"]`);
  return { item };
}

function progressChildren(rendered, stage) {
  return rendered.item(stage).querySelector('.stage-progress').children;
}

test('pipeline renders backend frame and evidence counters as measurable progress', () => {
  const rendered = renderPipelineFor({
    status: 'processing',
    stage: 'detection',
    phase_progress: {
      detection: { status: 'processing', current: 480, total: 800, message: '480 / 800 frames processed' },
      tracking: { status: 'processing', current: 480, total: 800, message: '480 / 800 frames tracked' },
      vlm: { status: 'processing', current: 3, total: 6, message: '3 / 6 evidence frames verified' },
    },
  });

  for (const [stage, count, percent] of [
    ['detection', '480 / 800 frames processed', 60],
    ['tracking', '480 / 800 frames tracked', 60],
    ['vlm', '3 / 6 evidence frames verified', 50],
  ]) {
    const children = progressChildren(rendered, stage);
    assert.equal(children[0].value, percent);
    assert.equal(children[1].textContent, `${percent}%`);
    assert.equal(children[2].textContent, count);
  }
});

test('pipeline renders truthful pending, completed, failed, and skipped states', () => {
  const pending = renderPipelineFor({ status: 'prepared', stage: 'uploaded' });
  assert.equal(progressChildren(pending, 'temporal')[0].textContent, 'Waiting for previous phase...');

  const completed = renderPipelineFor({
    status: 'completed',
    stage: 'completed',
    phase_progress: {
      detection: { status: 'completed', current: 800, total: 800, message: '800 / 800 frames processed' },
      fusion: { status: 'completed', current: 4, total: 4, message: '4 / 4 tracks fused' },
      assessment: { status: 'completed', message: 'Final assessment generated ✓' },
    },
  });
  assert.equal(progressChildren(completed, 'detection')[1].textContent, '100%');
  assert.equal(progressChildren(completed, 'detection')[2].textContent, '800 / 800 frames processed');
  assert.equal(progressChildren(completed, 'fusion')[1].textContent, '100%');
  assert.equal(progressChildren(completed, 'fusion')[2].textContent, '4 / 4 tracks fused');
  assert.equal(progressChildren(completed, 'assessment')[0].textContent, 'Final assessment generated ✓');

  const failed = renderPipelineFor({
    status: 'failed',
    stage: 'failed',
    phase_progress: { reliability: { status: 'failed', message: 'Phase failed' } },
  });
  assert.equal(progressChildren(failed, 'reliability')[0].textContent, 'Phase failed');

  const skipped = renderPipelineFor({
    status: 'completed',
    stage: 'completed',
    vlm_status: 'skipped',
    phase_progress: {
      vlm: { status: 'skipped', message: 'No evidence selected for VLM verification' },
    },
  });
  assert.equal(
    progressChildren(skipped, 'vlm')[0].textContent,
    'No evidence selected for VLM verification',
  );
});

test('pipeline progress rendering has no timer-based progress', () => {
  assert.doesNotMatch(renderPipelineSource, /setInterval|setTimeout/);
});
