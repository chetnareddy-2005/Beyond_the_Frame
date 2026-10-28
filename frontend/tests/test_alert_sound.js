const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const alertHelperStart = appSource.indexOf('function alertEvidenceByTrack(');
const rowsStart = appSource.indexOf('function renderAlertRows(');
const alertFunctions = `const PLAYED_HIGH_ALERTS_STORAGE_KEY = 'btf-played-high-alerts-v2';\n${appSource.slice(alertHelperStart, rowsStart)}\nthis.renderAlerts = renderAlerts;`;

function createHarness(storage = new Map(), rejectPlayback = false) {
  const elements = new Map();
  const createElement = () => ({
    children: [],
    dataset: {},
    hidden: false,
    replaceChildren(...children) { this.children = [...children]; },
    append(...children) { this.children.push(...children); },
    set textContent(value) { this.text = String(value); },
    get textContent() {
      return this.text || this.children.map((child) => child.textContent).join('');
    },
  });
  for (const selector of [
    '#active-alert-list',
    '#active-alert-count',
    '#notification-count',
  ]) {
    elements.set(selector, createElement());
  }
  const sandbox = {
    state: {
      runId: 'RUN_current',
      alertRows: [],
      alertPage: 1,
      alertError: '',
      pageSize: 10,
      playedHighAlerts: new Set(),
    },
    $: (selector) => elements.get(selector),
    document: { createElement },
    renderAlertRows() {},
    formatNumber: String,
    formatVideoTimestamp: String,
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, value); },
    },
    console: { warn() {} },
    attempts: 0,
    Audio: class {
      constructor(src) { sandbox.audioSource = src; }
      play() {
        sandbox.attempts += 1;
        return rejectPlayback
          ? Promise.reject(new Error('NotAllowedError'))
          : Promise.resolve();
      }
    },
  };
  vm.runInNewContext(alertFunctions, sandbox);
  return { sandbox, elements, storage };
}

const highReport = (runId = 'RUN_current') => ({
  run_id: runId,
  alert: {
    level: 'HIGH',
    message: 'One or more tracks have been classified as UAV.',
    evidence: [{
      track_id: 1,
      observation: {
        track_id: 1,
        frame_index: 1,
        timestamp: 0,
        total_frames: 1,
        detector_class: 'Drone',
        detector_confidence: 0.8,
        reliability_status: 'CONFIDENT',
        reliability_score: 0.8,
        bbox: { x1: 1, y1: 1, x2: 2, y2: 2 },
      },
    }],
  },
});

test('HIGH alert plays once and keeps the existing badge and alert card', () => {
  const { sandbox, elements } = createHarness();

  sandbox.renderAlerts(highReport());

  assert.equal(sandbox.attempts, 1);
  assert.equal(sandbox.audioSource, '/assets/high-alert.wav');
  assert.equal(elements.get('#notification-count').hidden, false);
  assert.equal(elements.get('#notification-count').textContent, '1');
  assert.equal(elements.get('#active-alert-count').textContent, '(1)');
  assert.equal(elements.get('#active-alert-list').className, 'alert-content');
  assert.match(elements.get('#active-alert-list').textContent, /HIGH priority alert/);
});

test('re-rendering the same HIGH alert does not play again', () => {
  const { sandbox } = createHarness();
  const report = highReport();

  sandbox.renderAlerts(report);
  sandbox.renderAlerts(report);

  assert.equal(sandbox.attempts, 1);
});

test('early HIGH alert and completed report share one sound identity', () => {
  const { sandbox } = createHarness();
  const earlyResult = {
    run_id: 'RUN_current',
    early_alert: { uav_alert: true },
    alert: { level: 'HIGH', message: highReport().alert.message },
  };

  sandbox.renderAlerts({ run_id: earlyResult.run_id, alert: earlyResult.alert });
  sandbox.renderAlerts(highReport(earlyResult.run_id));

  assert.equal(sandbox.attempts, 1);
});

test('a different HIGH alert run plays once after the same run was played', () => {
  const { sandbox } = createHarness();

  sandbox.renderAlerts(highReport('RUN_one'));
  sandbox.renderAlerts(highReport('RUN_two'));
  sandbox.renderAlerts(highReport('RUN_two'));

  assert.equal(sandbox.attempts, 2);
});

test('refreshing the same run does not replay a persisted HIGH alert', async () => {
  const firstPage = createHarness();
  firstPage.sandbox.renderAlerts(highReport());
  await Promise.resolve();
  const refreshedPage = createHarness(firstPage.storage);

  refreshedPage.sandbox.renderAlerts(highReport());

  assert.equal(firstPage.sandbox.attempts, 1);
  assert.equal(refreshedPage.sandbox.attempts, 0);
});

test('playing another run preserves prior alert identities across refresh', async () => {
  const firstPage = createHarness();
  firstPage.sandbox.renderAlerts(highReport('RUN_one'));
  firstPage.sandbox.renderAlerts(highReport('RUN_two'));
  await Promise.resolve();
  const refreshedPage = createHarness(firstPage.storage);

  refreshedPage.sandbox.renderAlerts(highReport('RUN_one'));

  assert.equal(firstPage.sandbox.attempts, 2);
  assert.equal(refreshedPage.sandbox.attempts, 0);
});

test('non-HIGH and absent alerts do not play sound', () => {
  const { sandbox } = createHarness();

  sandbox.renderAlerts({ run_id: 'RUN_low', alert: { level: 'LOW', message: 'Notice' } });
  sandbox.renderAlerts({ run_id: 'RUN_none', alert: null });

  assert.equal(sandbox.attempts, 0);
});

test('a rejected autoplay attempt does not interrupt alert rendering', async () => {
  const { sandbox, elements } = createHarness(new Map(), true);

  sandbox.renderAlerts(highReport());
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(sandbox.attempts, 1);
  assert.equal(elements.get('#notification-count').hidden, false);
  assert.equal(elements.get('#active-alert-list').className, 'alert-content');
  assert.match(elements.get('#active-alert-list').textContent, /classified as UAV/);
});
