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

function element() {
  return {
    children: [],
    dataset: {},
    textContent: '',
    replaceChildren(...children) { this.children = [...children]; },
    append(...children) { this.children.push(...children); },
  };
}

function seekablePlayer(properties) {
  const listeners = new Map();
  let currentTime = properties.currentTime ?? 0;
  return {
    ...properties,
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) {
      if (listeners.get(name) === listener) listeners.delete(name);
    },
    get currentTime() { return currentTime; },
    set currentTime(value) {
      currentTime = value;
      listeners.get('seeked')?.();
    },
  };
}

function makeAlertHarness(page = 'alerts') {
  const activeAlerts = element();
  const alertCard = {
    classList: { add() {}, remove() {} },
    get offsetWidth() { return 0; },
  };
  const elements = new Map([
    ['#active-alert-list', activeAlerts],
    ['#active-alert-count', element()],
    ['#notification-count', element()],
    ['#active-alerts', alertCard],
  ]);
  const nodes = [];
  const sandbox = {
    state: {
      alertRows: [],
      alertPage: 1,
      alertError: '',
      pageSize: 10,
      runId: 'RUN_one',
      run: { input_metadata: { fps: 30 } },
      route: { page },
      routeRunId: 'RUN_one',
      alertRunId: null,
    },
    $: (selector) => elements.get(selector),
    document: {
      createElement() {
        const node = element();
        nodes.push(node);
        return node;
      },
    },
    formatNumber: (value, digits = 0) => Number(value).toFixed(digits),
    formatVideoTimestamp: (value) => {
      const minutes = Math.floor(value / 60);
      const seconds = Math.floor(value % 60);
      const milliseconds = Math.floor((value % 1) * 1000);
      return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(milliseconds).padStart(3, '0')}`;
    },
    playHighAlertOnce() {},
    renderAlertRows() {},
  };
  const timingSource = sourceBetween('function alertTimingSummary(', 'async function pollEarlyUavAlert(');
  const renderSource = sourceBetween('function alertEvidenceByTrack(', 'function playHighAlertOnce(');
  vm.runInNewContext(
    `${timingSource}\n${renderSource}\nthis.renderAlerts = renderAlerts;`,
    sandbox,
  );
  return { sandbox, activeAlerts, nodes };
}

function allText(node) {
  return `${node.textContent || ''} ${node.children.map(allText).join(' ')}`;
}

const evidence = {
  frame_index: 4,
  total_frames: 20,
  timestamp: 0.12,
  track_id: 7,
  detector_class: 'Drone',
  detector_confidence: 0.82,
  reliability_status: 'CONFIDENT',
  reliability_score: 0.76,
  bbox: { x1: 10, y1: 20, x2: 50, y2: 60 },
};

test('HIGH alert renders stored evidence and a view-frame action', () => {
  const { sandbox, activeAlerts } = makeAlertHarness();

  sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: {
      level: 'HIGH',
      message: 'UAV detected',
      evidence: [{ track_id: 7, observation: evidence }],
    },
  });
  const rendered = allText(activeAlerts);
  assert.match(rendered, /HIGH priority alert/);
  assert.match(rendered, /Track ID 7/);
  assert.match(rendered, /Evidence Frame 4 \/ 20/);
  assert.match(rendered, /00:00\.120/);
  assert.match(rendered, /Detector Drone/);
  assert.match(rendered, /Confidence 0\.820/);
  assert.match(rendered, /Reliability CONFIDENT/);
  assert.match(rendered, /Score 0\.760/);
  assert.match(rendered, /Bounding Box \(10\.0, 20\.0, 50\.0, 60\.0\)/);
  assert.match(rendered, /VIEW EVIDENCE FRAME/);
});

test('provisional trigger navigation stays distinct from completed representative evidence', () => {
  const navigate = (sandbox, evidenceIndex) => {
    const navigations = [];
    sandbox.navigateTo = (...args) => navigations.push(args);
    const handlerSource = sourceBetween(
      'function handleAlertEvidenceClick(',
      'function updateCurrentVideoFrame(',
    );
    vm.runInNewContext(
      `${handlerSource}\nthis.handleAlertEvidenceClick = handleAlertEvidenceClick;`,
      sandbox,
    );
    sandbox.handleAlertEvidenceClick({
      target: { closest: () => ({ dataset: { evidenceIndex: String(evidenceIndex) } }) },
    });
    return navigations[0][2].alertEvidence.evidence;
  };

  const trigger = {
    ...evidence,
    frame_index: 3,
    timestamp: 2 / 30,
    total_frames: 60,
  };
  const provisional = makeAlertHarness();
  provisional.sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: {
      level: 'REVIEW',
      message: 'Possible UAV; review required.',
      provisional: true,
      first_detection_frame: 1,
      published_frame: 3,
      timestamp: '2026-10-24T01:49:52.495Z',
      evidence: [{ track_id: 7, observation: trigger }],
    },
  });
  const provisionalText = allText(provisional.activeAlerts);
  assert.match(provisionalText, /First detection: frame 1 \(00:00\.000\)/);
  assert.match(provisionalText, /Alert trigger: frame 3 \(00:00\.066\)/);
  assert.match(provisionalText, /Alert created:/);
  assert.match(provisionalText, /Detection Frame 3 \/ 60/);
  assert.match(provisionalText, /Timestamp 00:00\.066/);
  assert.match(provisionalText, /VIEW DETECTION FRAME/);
  assert.equal(navigate(provisional.sandbox, 0).frame_index, 3);

  const representative = { ...trigger, frame_index: 55, timestamp: 1.8 };
  const completed = makeAlertHarness();
  completed.sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: {
      level: 'REVIEW',
      message: 'Possible UAV; review required.',
      first_detection_frame: 1,
      published_frame: 3,
      timestamp: '2026-10-24T01:49:52.495Z',
      evidence: [{ track_id: 7, observation: representative }],
    },
  });
  const completedText = allText(completed.activeAlerts);
  assert.match(completedText, /Alert trigger: frame 3 \(00:00\.066\)/);
  assert.match(completedText, /Alert created:/);
  assert.match(completedText, /Evidence Frame 55 \/ 60/);
  assert.match(completedText, /Timestamp 00:01\.800/);
  assert.match(completedText, /VIEW EVIDENCE FRAME/);
  assert.equal(navigate(completed.sandbox, 0).frame_index, 55);
});

test('dashboard alert summary counts and renders one compact card per alert track', () => {
  const { sandbox, activeAlerts } = makeAlertHarness('dashboard');
  sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: {
      level: 'HIGH',
      message: 'This long summary should not be repeated in the compact dashboard cards.',
      evidence: [
        { track_id: 1, observation: { ...evidence, track_id: 1, detector_class: 'Drone' } },
        { track_id: 3, observation: { ...evidence, track_id: 3, detector_class: 'Drone', frame_index: 9 } },
      ],
    },
  });

  test('alert track counts deduplicate evidence and preserve alert IDs without evidence', () => {
    const { sandbox, activeAlerts } = makeAlertHarness('dashboard');
    sandbox.renderAlerts({
      run_id: 'RUN_one',
      alert_track_ids: ['1', '2', '2'],
      alert: {
        level: 'REVIEW',
        evidence: [
          { track_id: 1, observation: { ...evidence, track_id: 1 } },
          { track_id: 1, observation: { ...evidence, track_id: 1, frame_index: 5 } },
        ],
      },
    });

    assert.equal(sandbox.$('#active-alert-count').textContent, '(2)');
    assert.equal(activeAlerts.children.length, 2);
    assert.match(allText(activeAlerts.children[0]), /TRACK ID 1/);
    assert.match(allText(activeAlerts.children[1]), /TRACK ID 2/);
    assert.match(allText(activeAlerts.children[1]), /Evidence unavailable/);
  });

  assert.equal(sandbox.$('#active-alert-count').textContent, '(2)');
  assert.equal(activeAlerts.children.length, 2);
  assert.match(allText(activeAlerts.children[0]), /TRACK ID 1/);
  assert.match(allText(activeAlerts.children[0]), /Drone · HIGH/);
  assert.match(allText(activeAlerts.children[1]), /TRACK ID 3/);
  assert.match(allText(activeAlerts.children[1]), /VIEW EVIDENCE FRAME/);
  assert.doesNotMatch(allText(activeAlerts), /long summary should not be repeated|HIGH priority alert/);
});

test('dashboard zero-alert state has no empty cards and shows the run-scoped message', () => {
  const { sandbox, activeAlerts } = makeAlertHarness('dashboard');
  sandbox.renderAlerts({ run_id: 'RUN_one', alert: null });
  assert.equal(sandbox.$('#active-alert-count').textContent, '(0)');
  assert.equal(activeAlerts.children.length, 0);
  assert.equal(activeAlerts.textContent, 'No active alerts for this run.');
});

test('no alert or unresolved evidence does not fabricate a frame', () => {
  const empty = makeAlertHarness();
  empty.sandbox.renderAlerts({ run_id: 'RUN_one', alert: null });
  assert.doesNotMatch(allText(empty.activeAlerts), /Evidence Frame|Track ID|VIEW EVIDENCE/);

  const absent = makeAlertHarness();
  absent.sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: { level: 'HIGH', message: 'UAV detected' },
  });
  assert.match(allText(absent.activeAlerts), /Evidence unavailable/);

  const missing = makeAlertHarness();
  missing.sandbox.renderAlerts({
    run_id: 'RUN_one',
    alert: {
      level: 'HIGH',
      message: 'UAV detected',
      evidence: [{ track_id: 99, observation: null }],
    },
  });
  assert.match(allText(missing.activeAlerts), /Evidence unavailable/);
  assert.doesNotMatch(allText(missing.activeAlerts), /VIEW EVIDENCE FRAME/);
});

test('evidence action seeks by the stored one-based frame and requests read-only lookup', async () => {
  const seekSource = sourceBetween('async function viewAlertEvidence(', 'function handleAlertEvidenceClick(');
  const calls = [];
  const player = seekablePlayer({ duration: 5, videoWidth: 640, currentTime: 0 });
  const elements = new Map([
    ['#input-video-player', player],
    ['#current-frame-index', element()],
    ['#current-frame-timestamp', element()],
    ['#current-frame-status', element()],
  ]);
  const sandbox = {
    window: { setTimeout, clearTimeout },
    state: {
      runId: 'RUN_one',
      playerRunId: 'RUN_one',
      currentFrameIndex: 0,
      lastFrameLookupKey: null,
    },
    videoFrameMetadata: () => ({ fps: 25, totalFrames: 100 }),
    $: (selector) => elements.get(selector),
    formatNumber: String,
    formatVideoTimestamp: (value) => value.toFixed(3),
    drawCurrentVideoFrame() {},
    async refreshStoredFrameObservations() {
      calls.push(`/api/runs/${sandbox.state.runId}/frames/${sandbox.state.currentFrameIndex}`);
    },
  };
  vm.runInNewContext(`${seekSource}\nthis.viewAlertEvidence = viewAlertEvidence;`, sandbox);

  await sandbox.viewAlertEvidence(evidence);

  assert.equal(player.currentTime, (evidence.frame_index - 1) / 25);
  assert.equal(sandbox.state.currentFrameIndex, evidence.frame_index);
  assert.equal(elements.get('#current-frame-status').textContent, 'Alert Evidence');
  assert.equal(calls[0], '/api/runs/RUN_one/frames/4');
  const storedLookup = sourceBetween('async function refreshStoredFrameObservations(', 'function updateCurrentVideoFrame(');
  assert.match(storedLookup, /\/api\/runs\/\$\{encodeURIComponent\(state\.runId\)\}\/frames\/\$\{frameIndex\}/);
  assert.doesNotMatch(storedLookup, /phase4\/detect|detectNextPhase4Frame|vlm\/verify/i);
});

test('evidence navigation refuses to seek a video belonging to another run', async () => {
  const seekSource = sourceBetween('async function viewAlertEvidence(', 'function handleAlertEvidenceClick(');
  const player = { duration: 5, videoWidth: 640, currentTime: 1 };
  const elements = new Map([
    ['#input-video-player', player],
    ['#current-frame-status', element()],
  ]);
  let refreshCount = 0;
  const sandbox = {
    state: { runId: 'RUN_selected', playerRunId: 'RUN_other' },
    videoFrameMetadata: () => ({ fps: 25, totalFrames: 100 }),
    $: (selector) => elements.get(selector),
    async refreshStoredFrameObservations() { refreshCount += 1; },
  };
  vm.runInNewContext(`${seekSource}\nthis.viewAlertEvidence = viewAlertEvidence;`, sandbox);

  await sandbox.viewAlertEvidence(evidence);

  assert.equal(player.currentTime, 1);
  assert.equal(elements.get('#current-frame-status').textContent, 'Matching video preview unavailable');
  assert.equal(refreshCount, 0);
});

test('stored timestamp permits exact evidence navigation when FPS metadata is unavailable', async () => {
  const seekSource = sourceBetween('async function viewAlertEvidence(', 'function handleAlertEvidenceClick(');
  const status = element();
  const player = seekablePlayer({ duration: 5, videoWidth: 640, videoHeight: 360, currentTime: 0 });
  const sandbox = {
    window: { setTimeout, clearTimeout },
    state: { runId: 'RUN_one', playerRunId: 'RUN_one' },
    videoFrameMetadata: () => null,
    $: (selector) => selector === '#input-video-player' ? player : status,
    formatNumber: String,
    formatVideoTimestamp: String,
    drawCurrentVideoFrame() {},
    async refreshStoredFrameObservations() {},
  };
  vm.runInNewContext(`${seekSource}\nthis.viewAlertEvidence = viewAlertEvidence;`, sandbox);

  await sandbox.viewAlertEvidence(evidence);

  assert.equal(player.currentTime, evidence.timestamp);
  assert.equal(sandbox.state.currentFrameIndex, evidence.frame_index);
  assert.equal(status.textContent, 'Alert Evidence');
});

test('alert card navigates in the same tab with its exact run and stored evidence', () => {
  const handlerSource = sourceBetween('function handleAlertEvidenceClick(', 'function updateCurrentVideoFrame(');
  const navigations = [];
  const sandbox = {
    state: {
      alertRows: [{ evidence: [{ track_id: 7, observation: evidence }] }],
      alertRunId: 'RUN_one',
      route: { page: 'dashboard' },
    },
    encodeURIComponent,
    navigateTo: (...args) => navigations.push(args),
  };
  vm.runInNewContext(`${handlerSource}\nthis.handleAlertEvidenceClick = handleAlertEvidenceClick;`, sandbox);
  sandbox.handleAlertEvidenceClick({
    target: {
      closest: () => ({ dataset: { evidenceIndex: '0' } }),
    },
  });

  assert.equal(navigations.length, 1);
  assert.equal(navigations[0][0], '/results/RUN_one');
  assert.equal(navigations[0][1], false);
  assert.deepEqual(JSON.parse(JSON.stringify(navigations[0][2])), {
    alertEvidence: { run_id: 'RUN_one', evidence: { ...evidence, track_id: 7 } },
  });
});

test('alert evidence bounding box is drawn from the stored observation', () => {
  const drawSource = sourceBetween('function drawAlertEvidenceBox(', 'function drawCurrentVideoFrame(');
  const boxes = [];
  const sandbox = {
    state: {
      alertEvidence: { frame_index: 4, track_id: 7 },
      currentFrameIndex: 4,
      currentFrameObservationFrame: 4,
      currentFrameObservations: [
        { track_id: 7, bbox: { x1: 10, y1: 20, x2: 50, y2: 60 } },
      ],
    },
    $: (selector) => selector === '#input-video-player'
      ? { videoWidth: 640, videoHeight: 360 }
      : {
        width: 320,
        height: 180,
        getContext: () => ({
          strokeRect: (...values) => boxes.push(values),
        }),
      },
  };
  vm.runInNewContext(`${drawSource}\nthis.drawAlertEvidenceBox = drawAlertEvidenceBox;`, sandbox);

  sandbox.drawAlertEvidenceBox();

  assert.deepEqual(boxes, [[5, 10, 20, 20]]);
});

test('alert frame seek preserves stored timestamp and stored bbox over lookup values', async () => {
  const seekSource = sourceBetween('async function viewAlertEvidence(', 'async function applyPendingAlertEvidence(');
  const selected = { ...evidence, track_id: 7, timestamp: 0.137 };
  const player = seekablePlayer({ duration: 5, videoWidth: 640, videoHeight: 360, currentTime: 0 });
  const values = new Map([
    ['#input-video-player', player],
    ['#current-frame-index', element()],
    ['#current-frame-timestamp', element()],
    ['#current-frame-status', element()],
  ]);
  const sandbox = {
    window: { setTimeout, clearTimeout },
    state: { runId: 'RUN_one', playerRunId: 'RUN_one', currentFrameIndex: 0, lastFrameLookupKey: null },
    videoFrameMetadata: () => ({ fps: 25, totalFrames: 100 }),
    $: (selector) => values.get(selector),
    formatNumber: String,
    formatVideoTimestamp: String,
    drawCurrentVideoFrame() {},
    async refreshStoredFrameObservations() {},
  };
  vm.runInNewContext(`${seekSource}\nthis.viewAlertEvidence = viewAlertEvidence;`, sandbox);

  await sandbox.viewAlertEvidence(selected);

  assert.equal(player.currentTime, selected.timestamp);
  assert.equal(sandbox.state.currentFrameIndex, selected.frame_index);
  assert.equal(sandbox.state.alertEvidence.observation.bbox, selected.bbox);
  assert.equal(sandbox.state.alertEvidence.observation.reliability_score, selected.reliability_score);

  const drawSource = sourceBetween('function drawAlertEvidenceBox(', 'function drawCurrentVideoFrame(');
  const boxes = [];
  sandbox.$ = (selector) => selector === '#input-video-player'
    ? { videoWidth: 640, videoHeight: 360 }
    : { width: 320, height: 180, getContext: () => ({ strokeRect: (...box) => boxes.push(box) }) };
  sandbox.state.currentFrameObservationFrame = selected.frame_index;
  sandbox.state.currentFrameObservations = [
    { track_id: 7, bbox: { x1: 100, y1: 100, x2: 120, y2: 120 } },
  ];
  vm.runInNewContext(`${drawSource}\nthis.drawAlertEvidenceBox = drawAlertEvidenceBox;`, sandbox);
  sandbox.drawAlertEvidenceBox();
  assert.deepEqual(boxes, [[5, 10, 20, 20]]);
});

test('thermal video preview does not request or render stored RGB frame observations', async () => {
  const source = sourceBetween(
    'async function refreshStoredFrameObservations(',
    'function updateCurrentVideoFrame(',
  );
  const values = new Map([
    ['#current-frame-status', element()],
  ]);
  const sandbox = {
    state: {
      inputType: 'thermal',
      selectedFile: { name: 'IR_DRONE_040.mp4' },
      runId: 'RUN_rgb',
      currentFrameIndex: 153,
    },
    $: (selector) => values.get(selector),
    clearCurrentFrameObservations(message) {
      values.get('#current-frame-status').detailsMessage = message;
    },
    videoFrameMetadata: () => ({ fps: 30, totalFrames: 300 }),
    async api() {
      throw new Error('Thermal preview must not fetch stored RGB frames');
    },
  };
  vm.runInNewContext(`${source}\nthis.refreshStoredFrameObservations = refreshStoredFrameObservations;`, sandbox);

  await sandbox.refreshStoredFrameObservations();

  assert.equal(values.get('#current-frame-status').textContent, 'Thermal preview');
  assert.match(
    values.get('#current-frame-status').detailsMessage,
    /RGB tracked observations are not displayed/,
  );
  assert.match(
    sourceBetween('function drawVideoTrailOverlay(', 'function renderCurrentFrameObservations('),
    /state\.inputType === 'thermal'\s*&&\s*state\.selectedFile/,
  );
});
