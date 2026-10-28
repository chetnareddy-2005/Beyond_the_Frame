const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

function sourceBetween(startMarker, endMarker) {
  const start = appSource.indexOf(startMarker);
  const end = appSource.indexOf(endMarker, start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  return appSource.slice(start, end);
}

function element() {
  return {
    children: [],
    attributes: {},
    disabled: false,
    innerHTML: '',
    textContent: '',
    value: '',
    replaceChildren(...children) { this.children = [...children]; },
    add(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

function makeHarness(trackIds = ['7', '8']) {
  const elements = new Map([
    ['#dashboard-trajectory-track', element()],
    ['#dashboard-trajectory-preview', element()],
    ['#dashboard-trajectory-preview-content', element()],
    ['#current-frame-status', element()],
    ['#trajectory-dialog', { open: false }],
    ['#trajectory-dialog-title', element()],
    ['#trajectory-dialog-content', element()],
  ]);
  const requests = [];
  const trackDetail = (trackId, runId) => ({
    track_id: trackId,
    class: trackId === trackIds[0] ? 'Drone' : 'Bird',
    start_frame: 2,
    end_frame: 5,
    selected_evidence: [],
    reliability: { status: 'CONFIDENT', reliability_score: 0.762 },
    motion_trajectory: {
      trajectory_points: [
        { frame: 5, timestamp_sec: 0.4, x_px: 50, y_px: 60, smoothed_x_px: 45, smoothed_y_px: 55 },
        { frame: 2, timestamp_sec: 0.1, x_px: 20, y_px: 30, smoothed_x_px: 22, smoothed_y_px: 32 },
      ],
      summary: { observations: 2 },
    },
  });
  const sandbox = {
    state: {
      runId: 'RUN_selected',
      run: { input_type: 'rgb' },
      tracks: trackIds.map((track_id, index) => ({
        track_id,
        class: index === 0 ? 'Drone' : 'Bird',
      })),
      selectedPhaseTrackId: trackIds[0],
      selectedTrajectoryTrackId: null,
      trajectoryDetails: new Map(),
      trajectoryDetailsRunId: 'RUN_selected',
      trajectoryRequest: 0,
      phaseTrackDetail: null,
      inputMetadata: { fps: 30, frame_count: 300 },
      frameLookupRequest: 0,
      lastFrameLookupKey: null,
      alertEvidence: null,
      report: {
        run_id: 'RUN_selected',
        tracks: [],
        alert: {
          evidence: trackIds.map((track_id) => {
            const frame_index = track_id === trackIds[0] ? 248 : 148;
            return {
              track_id,
              observation: {
                frame_index,
                track_id: Number(track_id),
                detector_class: track_id === trackIds[0] ? 'Drone' : 'Bird',
                detector_confidence: track_id === trackIds[0] ? 0.91 : 0.88,
                bbox: { x1: 10, y1: 20, x2: 50, y2: 60 },
                timestamp: (frame_index - 1) / 30,
                total_frames: 302,
              },
            };
          }),
        },
      },
    },
    $: (selector) => elements.get(selector),
    Option: class Option {
      constructor(text, value) { this.text = text; this.value = value; }
    },
    formatNumber: (value) => String(value),
    formatVideoTimestamp: (value) => `t${value}`,
    videoFrameMetadata: () => ({ fps: 30, totalFrames: 300 }),
    escapeHTML: (value) => String(value),
    detailStat: (label, value) => `<div>${label}: ${value}</div>`,
    clearCurrentFrameObservations(message) { elements.get('#current-frame-status').textContent = message; },
    updateTrajectoryOverlayToggle() {},
    selectedEvidenceLookups: [],
    async viewAlertEvidence(evidence, label) {
      sandbox.selectedEvidenceLookups.push({
        runId: sandbox.state.runId,
        frameIndex: evidence.frame_index,
        timestamp: evidence.timestamp,
        trackId: evidence.track_id,
        label,
        request: `/api/runs/${sandbox.state.runId}/frames/${evidence.frame_index}`,
      });
    },
    async api(pathname) {
      requests.push(pathname);
      const parts = pathname.split('/');
      return trackDetail(parts.at(-1), decodeURIComponent(parts[3]));
    },
  };
  const source = sourceBetween('function trajectoryPlot(', 'function renderAssessment(');
  vm.runInNewContext(`${source}\nthis.renderDashboardTrajectory = renderDashboardTrajectory;\nthis.renderExpandedTrajectory = renderExpandedTrajectory;\nthis.selectTrajectoryTrack = selectTrajectoryTrack;`, sandbox);
  return { sandbox, elements, requests };
}

test('active and recent alerts share the same card with the recent-alert table retained', () => {
  const activeCard = indexSource.match(/<section id="active-alerts"[\s\S]*?<\/section>/)?.[0];
  assert.ok(activeCard);
  assert.match(activeCard, /> ALERTS <span id="active-alert-count"/);
  assert.match(activeCard, /id="active-alert-list"/);
  assert.match(activeCard, /Recent Alerts/);
  assert.match(activeCard, /id="recent-alert-rows"/);
  assert.match(activeCard, /id="alert-pagination"/);
  assert.doesNotMatch(indexSource, /id="recent-alerts"/);
});

test('trajectory plot uses chronological actual centers and retains raw/smoothed labels', async () => {
  const { sandbox, elements, requests } = makeHarness();
  await sandbox.renderDashboardTrajectory();

  assert.deepEqual(requests, ['/api/runs/RUN_selected/tracks/7']);
  assert.equal(elements.get('#dashboard-trajectory-track').value, '7');
  assert.equal(elements.get('#dashboard-trajectory-preview').disabled, false);
  const preview = elements.get('#dashboard-trajectory-preview-content').innerHTML;
  assert.match(preview, /Track 7 · Drone/);
  assert.match(preview, /class="trajectory-raw" points="18\.00,18\.00 582\.00,202\.00"/);
  assert.match(preview, /class="trajectory-smoothed" points=/);
  assert.match(preview, /Raw center/);
  assert.match(preview, /Smoothed center/);
});

test('expanded view reuses cached stored data and shows track, ranges, observations, and disclaimer', async () => {
  const { sandbox, elements, requests } = makeHarness();
  await sandbox.renderDashboardTrajectory();
  const requestCount = requests.length;
  const detail = sandbox.state.trajectoryDetails.get('RUN_selected:7');
  sandbox.renderExpandedTrajectory(sandbox.state.tracks[0], detail);

  const expanded = elements.get('#trajectory-dialog-content').innerHTML;
  assert.equal(elements.get('#trajectory-dialog-title').textContent, 'Track 7 trajectory');
  assert.match(expanded, /Track ID: 7/);
  assert.match(expanded, /Frame range: 2–5/);
  assert.match(expanded, /Timestamp range: t0\.1–t0\.4/);
  assert.match(expanded, /Observations: 2/);
  assert.match(expanded, /class="trajectory-raw"/);
  assert.match(expanded, /class="trajectory-smoothed"/);
  assert.match(expanded, /Image-plane pixels only • Y coordinates follow image pixel direction • no physical-world scale or 3D calibration\./);
  assert.equal(requests.length, requestCount, 'expanding a cached trajectory must not request new data');
  assert.equal(sandbox.state.runId, 'RUN_selected');
});

test('trajectory selection uses the existing read-only track endpoint and never analysis actions', async () => {
  const { sandbox, elements, requests } = makeHarness();
  await sandbox.renderDashboardTrajectory();
  elements.get('#dashboard-trajectory-track').value = '8';
  sandbox.state.selectedTrajectoryTrackId = '8';
  await sandbox.renderDashboardTrajectory();

  assert.deepEqual(requests, [
    '/api/runs/RUN_selected/tracks/7',
    '/api/runs/RUN_selected/tracks/8',
  ]);
  const loader = sourceBetween('async function renderDashboardTrajectory(', 'function renderAssessment(');
  assert.doesNotMatch(loader, /phase4\/detect|phase5\/track|vlm\/verify|start-analysis|reanalyze/i);
});

test('trajectory options distinguish RGB detector history from the final decision', async () => {
  const { sandbox, elements } = makeHarness(['7']);
  sandbox.state.tracks[0].class_history = ['Drone', 'Bird'];
  sandbox.state.report.tracks = [{ track_id: '7', final_label: 'Other' }];

  await sandbox.renderDashboardTrajectory();

  assert.equal(
    elements.get('#dashboard-trajectory-track').children[0].text,
    'Track 7 · Detector history: Drone → Bird · Final decision: Other',
  );
});

test('thermal trajectory options distinguish detector history from temporal consensus', async () => {
  const { sandbox, elements } = makeHarness(['7']);
  sandbox.state.run.input_type = 'thermal';
  sandbox.state.tracks[0].class_history = ['Drone', 'Bird'];
  sandbox.state.report.tracks = [
    { track_id: '7', temporal_consensus: { consensus_label: 'Uncertain' } },
  ];

  await sandbox.renderDashboardTrajectory();

  assert.equal(
    elements.get('#dashboard-trajectory-track').children[0].text,
    'Track 7 · Detector history: Drone → Bird · Temporal consensus: Uncertain',
  );
});

test('trajectory selection displays each track own stored evidence frame and timestamp', async () => {
  const { sandbox, requests } = makeHarness(['1', '2']);

  await sandbox.selectTrajectoryTrack('1');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).frameIndex, 248);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).timestamp, 247 / 30);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).trackId, 1);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).label, 'Selected Track Evidence');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).request, '/api/runs/RUN_selected/frames/248');

  await sandbox.selectTrajectoryTrack('2');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).frameIndex, 148);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).timestamp, 147 / 30);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).trackId, 2);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).request, '/api/runs/RUN_selected/frames/148');

  await sandbox.selectTrajectoryTrack('1');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).frameIndex, 248);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).timestamp, 247 / 30);
  assert.deepEqual(
    requests.filter((url) => url.includes('/tracks/')),
    [
      '/api/runs/RUN_selected/tracks/1',
      '/api/runs/RUN_selected/tracks/2',
    ],
  );
  assert.equal(sandbox.selectedEvidenceLookups.length, 3);
});

test('report alert evidence supplies frames when selected evidence artifacts are empty', async () => {
  const { sandbox } = makeHarness(['1', '2']);

  await sandbox.selectTrajectoryTrack('1');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).frameIndex, 248);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).timestamp, 8.233333333333333);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).label, 'Selected Track Evidence');

  await sandbox.selectTrajectoryTrack('2');
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).frameIndex, 148);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).timestamp, 4.9);
  assert.equal(sandbox.selectedEvidenceLookups.at(-1).label, 'Selected Track Evidence');
});

test('trajectory evidence lookup uses only the selected run track mapping', async () => {
  const { sandbox, requests } = makeHarness(['1', '2']);
  sandbox.state.runId = 'RUN_other';
  sandbox.state.trajectoryDetailsRunId = 'RUN_selected';
  sandbox.state.report.run_id = 'RUN_selected';

  await sandbox.selectTrajectoryTrack('1');

  assert.equal(sandbox.selectedEvidenceLookups.length, 0);
  assert.deepEqual(requests, ['/api/runs/RUN_other/tracks/1']);
});

test('track-specific evidence selection issues no analysis or inference request', async () => {
  const { sandbox, requests } = makeHarness(['1', '2']);

  await sandbox.selectTrajectoryTrack('2');

  assert.deepEqual(requests, ['/api/runs/RUN_selected/tracks/2']);
  assert.equal(sandbox.selectedEvidenceLookups[0].request, '/api/runs/RUN_selected/frames/148');
  assert.doesNotMatch(
    [...requests, ...sandbox.selectedEvidenceLookups.map((item) => item.request)].join('\n'),
    /\/start|phase4\/detect|phase5\/track|phase6\/temporal|reliability|vlm|fusion/i,
  );
});
