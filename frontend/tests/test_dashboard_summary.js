const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const stylesheet = fs.readFileSync(path.join(__dirname, '..', 'styles.css'), 'utf8');

function element() {
  return {
    textContent: '',
    className: '',
    hidden: false,
    replaceChildren() {},
    append() {},
  };
}

function makeHarness(inputType, reportTracks, alertTrackIds) {
  const selectors = [
    '#dashboard-total-objects',
    '#dashboard-drones',
    '#dashboard-birds',
    '#dashboard-drones-label',
    '#dashboard-birds-label',
    '#dashboard-uncertain-label',
    '#dashboard-uncertain',
    '#dashboard-reliability',
    '#dashboard-run-status',
    '#total-tracks',
    '#confident-tracks',
    '#uncertain-tracks',
    '#vlm-tracks',
    '#results-run-id',
    '#evidence-summary',
    '#alert-summary',
    '#thermal-run-note',
    '#fusion-modes',
    '#run-vlm-badge',
    '#run-vlm-summary',
  ];
  const elements = new Map(selectors.map((selector) => [selector, element()]));
  const sandbox = {
    state: {
      runId: 'RUN_summary',
      run: { status: 'completed', input_type: inputType },
      tracks: reportTracks.map((track) => ({ track_id: track.track_id, reliability_score: null })),
      report: {
        run_id: 'RUN_summary',
        analysis_mode: inputType === 'thermal' ? 'thermal_detection_tracking_only' : 'rgb',
        tracks: reportTracks,
        alert_track_ids: alertTrackIds,
        alert: { level: 'REVIEW', evidence: [] },
      },
      overview: {
        total_tracks: reportTracks.length + 5,
        confident_tracks: 2,
        uncertain_tracks: 99,
        vlm_enabled_tracks: 0,
      },
      fusionSummary: {
        final_label_counts: { UAV: 2, Bird: 1, Other: 1 },
        fusion_mode_counts: {},
      },
    },
    $: (selector) => elements.get(selector),
    formatNumber: (value) => Number.isFinite(Number(value)) ? String(value) : 'N/A',
    document: { createElement: element },
  };
  const start = appSource.indexOf('function renderSummary()');
  const end = appSource.indexOf('function renderAlerts(', start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  vm.runInNewContext(
    `${appSource.slice(start, end)}\nthis.renderSummary = renderSummary;`,
    sandbox,
  );
  return { sandbox, elements };
}

test('RGB summary counts final labels and alert tracks, not status or raw track totals', () => {
  const { sandbox, elements } = makeHarness(
    'rgb',
    [{ track_id: '1' }, { track_id: '2' }, { track_id: '3' }, { track_id: '4' }],
    ['2'],
  );
  sandbox.renderSummary();

  assert.equal(elements.get('#dashboard-total-objects').textContent, '4');
  assert.equal(elements.get('#dashboard-drones-label').textContent, 'Final UAV decisions');
  assert.equal(elements.get('#dashboard-drones').textContent, '2');
  assert.equal(elements.get('#dashboard-birds').textContent, '1');
  assert.equal(elements.get('#dashboard-uncertain').textContent, '1');
  assert.equal(elements.get('#alert-summary').textContent, '1 review alert track(s)');
});

test('Thermal summary uses per-track consensus and detector-only alert IDs', () => {
  const { sandbox, elements } = makeHarness(
    'thermal',
    [
      { track_id: '1', temporal_consensus: { consensus_label: 'Drone' } },
      { track_id: '2', temporal_consensus: { consensus_label: 'Bird' } },
      { track_id: '3', temporal_consensus: { consensus_label: 'Uncertain' } },
    ],
    ['1'],
  );
  sandbox.renderSummary();

  assert.equal(elements.get('#dashboard-total-objects').textContent, '3');
  assert.equal(elements.get('#dashboard-drones-label').textContent, 'Consensus Drone tracks');
  assert.equal(elements.get('#dashboard-drones').textContent, '1');
  assert.equal(elements.get('#dashboard-birds-label').textContent, 'Consensus Bird tracks');
  assert.equal(elements.get('#dashboard-birds').textContent, '1');
  assert.equal(elements.get('#dashboard-uncertain-label').textContent, 'Other / uncertain consensus');
  assert.equal(elements.get('#dashboard-uncertain').textContent, '1');
  assert.equal(elements.get('#alert-summary').textContent, '1 detector-only review alert track(s)');
  assert.equal(elements.get('#evidence-summary').textContent, 'Not run for thermal');
});

test('Light theme gives trajectory plots a light surface and readable raw path', () => {
  assert.match(stylesheet, /html\[data-theme="light"\] \.trajectory-plot-wrap\s*\{[^}]*background:\s*#F1F5F9/);
  assert.match(stylesheet, /html\[data-theme="light"\] \.trajectory-plot\s*\{[^}]*background:\s*linear-gradient/);
  assert.match(stylesheet, /html\[data-theme="light"\] \.trajectory-raw\s*\{[^}]*stroke:\s*#64748B/);
});
