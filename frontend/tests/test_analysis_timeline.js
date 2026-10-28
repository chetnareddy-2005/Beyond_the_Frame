const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const timelineStart = appSource.indexOf('function formatAnalysisTimelineDuration(');
const timelineEnd = appSource.indexOf('function formatVideoTime(', timelineStart);
const timelineSource = appSource.slice(timelineStart, timelineEnd);

function makeTimelineItem(stage) {
  const classes = new Set();
  return {
    dataset: { timelineStage: stage },
    classList: {
      add(name) { classes.add(name); },
      remove(...names) { names.forEach((name) => classes.delete(name)); },
      contains(name) { return classes.has(name); },
    },
    marker: { textContent: '' },
    label: { textContent: stage },
    querySelector(selector) {
      return selector === '.timeline-marker' ? this.marker : this.label;
    },
  };
}

function makeHarness() {
  const now = { value: Date.parse('2026-10-06T08:00:00Z') };
  const elements = new Map();
  const items = [
    'uploaded', 'detection', 'tracking', 'temporal', 'reliability',
    'evidence', 'vlm', 'fusion', 'assessment', 'completed',
  ].map(makeTimelineItem);
  const intervals = new Map();
  const clearedIntervals = [];
  let nextInterval = 1;
  const sandbox = {
    PIPELINE_STAGES: [
      'uploaded', 'detection', 'tracking', 'temporal', 'reliability',
      'evidence', 'vlm', 'fusion', 'assessment', 'completed',
    ],
    state: {
      runId: null,
      run: null,
      analysisTimelineTimer: null,
      analysisTimelineRunId: null,
    },
    Date: {
      now: () => now.value,
      parse: (value) => Date.parse(value),
    },
    $: (selector) => {
      if (!elements.has(selector)) {
        elements.set(selector, { hidden: false, textContent: '', className: '' });
      }
      return elements.get(selector);
    },
    document: {
      querySelectorAll() { return items; },
    },
    setInterval(callback) {
      const id = nextInterval++;
      intervals.set(id, callback);
      return id;
    },
    clearInterval(id) {
      clearedIntervals.push(id);
      intervals.delete(id);
    },
    apiCalls: 0,
  };
  vm.runInNewContext(
    `${timelineSource}\nthis.renderAnalysisTimeline = renderAnalysisTimeline; this.analysisTimelineDuration = analysisTimelineDuration; this.actualAnalysisDuration = actualAnalysisDuration; this.formatAnalysisTimelineDuration = formatAnalysisTimelineDuration; this.runFilename = runFilename;`,
    sandbox,
  );
  return { sandbox, now, elements, items, intervals, clearedIntervals };
}

function run(runId, status, createdAt, extra = {}) {
  return {
    run_id: runId,
    status,
    stage: status === 'processing' ? 'detection' : status,
    created_at: createdAt,
    ...extra,
  };
}

test('no selected run shows the exact empty state and no fabricated duration', () => {
  const { sandbox, elements, intervals } = makeHarness();

  sandbox.renderAnalysisTimeline(null);

  assert.equal(elements.get('#analysis-timeline-empty').hidden, false);
  assert.equal(elements.get('#analysis-timeline-duration').textContent, '');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, '');
  assert.equal(elements.get('#analysis-timeline-stages').hidden, true);
  assert.equal(elements.get('#analysis-timeline-endpoints').hidden, true);
  assert.equal(intervals.size, 0);
  assert.match(indexSource, /No analysis run selected\. Start an analysis to view elapsed time\./);
});

test('processing elapsed time advances from persisted run creation time', () => {
  const { sandbox, now, elements, intervals } = makeHarness();
  const selected = run('RUN_A', 'processing', '2026-10-06T07:58:48Z', {
    started_at: '2026-10-06T07:59:30Z',
    phase_progress: { detection: { status: 'processing' } },
  });
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;

  sandbox.renderAnalysisTimeline(selected);
  assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 01:12');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 00:30');
  assert.equal(intervals.size, 1);
  assert.equal(elements.get('#analysis-timeline-status').textContent, 'Status: PROCESSING');

  now.value += 1000;
  [...intervals.values()][0]();
  assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 01:13');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 00:31');
});

test('completed run uses persisted creation and terminal timestamps and stays frozen', () => {
  const { sandbox, elements, intervals } = makeHarness();
  const selected = run('RUN_DONE', 'completed', '2026-10-06T07:58:32Z', {
    started_at: '2026-10-06T07:59:00Z',
    completed_at: '2026-10-06T08:00:00Z',
    analysis_duration_seconds: 71,
  });
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;

  sandbox.renderAnalysisTimeline(selected);

  assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 01:28');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 01:00');
  assert.equal(elements.get('#analysis-timeline-endpoint').textContent, '01:28 Completed');
  assert.equal(intervals.size, 0);
});

test('failed and cancelled runs use persisted terminal duration and status', () => {
  for (const [status, label] of [['failed', 'FAILED'], ['cancelled', 'CANCELLED']]) {
    const { sandbox, elements, intervals } = makeHarness();
    const selected = run(`RUN_${status}`, status, '2026-10-06T07:59:13Z', {
      started_at: '2026-10-06T07:59:30Z',
      completed_at: '2026-10-06T08:00:00Z',
    });
    sandbox.state.runId = selected.run_id;
    sandbox.state.run = selected;

    sandbox.renderAnalysisTimeline(selected);

    assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 00:47');
    assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 00:30');
    assert.equal(elements.get('#analysis-timeline-status').textContent, `Status: ${label}`);
    assert.equal(intervals.size, 0);
  }
});

test('prepared run shows no fabricated actual analysis time before start', () => {
  const { sandbox, elements } = makeHarness();
  const selected = run('RUN_PREPARED', 'prepared', '2026-10-06T07:59:40Z');
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;

  sandbox.renderAnalysisTimeline(selected);

  assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 00:20');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: Not started');
});

test('switching selected runs clears the old timer and renders only the new run time', () => {
  const { sandbox, now, elements, intervals, clearedIntervals } = makeHarness();
  const first = run('RUN_A', 'processing', '2026-10-06T07:59:00Z', {
    started_at: '2026-10-06T07:59:50Z',
  });
  sandbox.state.runId = first.run_id;
  sandbox.state.run = first;
  sandbox.renderAnalysisTimeline(first);
  const firstTimer = sandbox.state.analysisTimelineTimer;

  const second = run('RUN_B', 'processing', '2026-10-06T07:59:18Z', {
    started_at: '2026-10-06T07:59:40Z',
  });
  sandbox.state.runId = second.run_id;
  sandbox.state.run = second;
  sandbox.renderAnalysisTimeline(second);

  assert.ok(clearedIntervals.includes(firstTimer));
  assert.equal(sandbox.state.analysisTimelineRunId, 'RUN_B');
  assert.equal(elements.get('#analysis-timeline-duration').textContent, 'Total Elapsed: 00:42');
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 00:20');
  assert.equal(intervals.size, 1);
  assert.equal(now.value, Date.parse('2026-10-06T08:00:00Z'));
});

test('legacy run falls back only to its persisted existing analysis duration', () => {
  const { sandbox, elements, intervals } = makeHarness();
  const selected = run('RUN_LEGACY', 'completed', null, {
    completed_at: null,
    analysis_duration_seconds: 78,
  });
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;

  sandbox.renderAnalysisTimeline(selected);

  assert.equal(
    elements.get('#analysis-timeline-duration').textContent,
    'Legacy stored analysis time: 01:18',
  );
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, 'Actual Analysis Time: 01:18 (stored duration)');
  assert.equal(intervals.size, 0);
});

test('missing legacy timestamps and duration remain explicitly unavailable', () => {
  for (const analysis_duration_seconds of [undefined, null, '']) {
    const { sandbox, elements, intervals } = makeHarness();
    const selected = run('RUN_UNKNOWN', 'failed', null, {
      completed_at: null,
      analysis_duration_seconds,
    });
    sandbox.state.runId = selected.run_id;
    sandbox.state.run = selected;

    sandbox.renderAnalysisTimeline(selected);

    assert.equal(
      elements.get('#analysis-timeline-duration').textContent,
      'Timing unavailable for this legacy run.',
    );
    assert.equal(
      elements.get('#analysis-timeline-actual-time').textContent,
      'Actual Analysis Time: Timing unavailable for this legacy run.',
    );
    assert.equal(intervals.size, 0);
  }
});

test('analysis duration formatting supports hours without changing video time', () => {
  const { sandbox } = makeHarness();
  assert.equal(sandbox.formatAnalysisTimelineDuration(3725), '01:02:05');
  assert.equal(sandbox.runFilename({ original_filename: 'source.mp4', input_filename: 'input_video.mp4' }), 'source.mp4');
  assert.equal(sandbox.runFilename({ input_filename: 'input_video.mp4' }), 'input_video.mp4');
  assert.equal(sandbox.formatAnalysisTimelineDuration(65), '01:05');
});

test('timeline phase markers mirror backend phase states and do not call run or inference APIs', () => {
  const { sandbox, items } = makeHarness();
  const selected = run('RUN_PHASES', 'processing', '2026-10-06T07:59:00Z', {
    phase_progress: {
      detection: { status: 'completed' },
      tracking: { status: 'processing' },
      temporal: { status: 'pending' },
    },
  });
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;

  sandbox.renderAnalysisTimeline(selected);

  assert.ok(items[1].classList.contains('timeline-complete'));
  assert.ok(items[2].classList.contains('timeline-active'));
  assert.ok(items[3].classList.contains('timeline-pending'));
  assert.equal(sandbox.apiCalls, 0);
  assert.doesNotMatch(timelineSource, /fetch\s*\(|api\s*\(|currentTime|create_run|inference/i);
});

test('completed timeline milestone exposes the label targeted by terminal-state rendering', () => {
  const completedStage = indexSource.match(/<li data-timeline-stage="completed">([\s\S]*?)<\/li>/)?.[1];
  assert.ok(completedStage);
  assert.match(completedStage, /<span class="stage-label">Completed<\/span>/);
});

test('video seeking does not alter the analysis elapsed duration', () => {
  const { sandbox, elements, intervals } = makeHarness();
  const selected = run('RUN_VIDEO', 'processing', '2026-10-06T07:59:00Z');
  sandbox.state.runId = selected.run_id;
  sandbox.state.run = selected;
  const player = { currentTime: 0 };

  sandbox.renderAnalysisTimeline(selected);
  const beforeSeek = elements.get('#analysis-timeline-duration').textContent;
  const beforeActual = elements.get('#analysis-timeline-actual-time').textContent;
  player.currentTime = 9.5;
  [...intervals.values()][0]();

  assert.equal(elements.get('#analysis-timeline-duration').textContent, beforeSeek);
  assert.equal(elements.get('#analysis-timeline-actual-time').textContent, beforeActual);
});

test('timeline is separate from and immediately precedes the unchanged Analysis Pipeline', () => {
  const timelineIndex = indexSource.indexOf('id="analysis-timeline"');
  const pipelineIndex = indexSource.indexOf('id="run-section"');

  assert.notEqual(timelineIndex, -1);
  assert.ok(timelineIndex < pipelineIndex);
  assert.match(indexSource, /id="run-title">Analysis Pipeline/);
  assert.match(indexSource, /id="input-video-player" controls/);
  assert.match(indexSource, /id="analysis-timeline-title">ANALYSIS TIMELINE/);
});
