# Phase 3: Tracklet Memory and Temporal Evidence

This small standard-library module reads Phase 2 `tracks.csv`, groups observations by `track_id`, and computes per-track temporal features. It does not run detection or tracking.

Run it from the project root:

```bash
python -m temporal.temporal_features path/to/tracks.csv
```

By default it writes `temporal_features.csv` and `track_motion.csv` beside the input. `--output` and `--motion-output` can specify different paths.

`persistence` is the number of observed frames divided by the inclusive frame span. Confidence and size variations are population standard deviations. Motion rows describe each observation relative to the preceding observation for that track; their `frame` is the current observation's frame. A track with only one observation has no motion rows and a mean displacement of zero.

## Image-plane motion and trajectory intelligence

`motion_trajectory.py` is an additive per-track analysis of the actual detector
`tracks.csv` columns (`frame`, `track_id`, `class_name`, `confidence`, and
`x1,y1,x2,y2`). The tracker ID is the existing `track_id`; timestamps and
image size are not present in that CSV. Dynamic runs obtain FPS and frame
dimensions from the uploaded video with OpenCV. Detector frame indices are
one-based, so frame `n` maps to `(n - 1) / fps` seconds. An optional
`timestamp_sec` or `timestamp` CSV column takes precedence only when all
timestamps in that track are finite and strictly increasing. Missing or
invalid timestamps are derived from a valid video FPS; without either source,
time-based values are empty/null.

From the project root, run:

```bash
python -m temporal.motion_trajectory path/to/tracks.csv --fps 30 --frame-width 640 --frame-height 480
```

The module writes `motion_observations.csv`, `motion_track_summary.csv`, and
`trajectories.json` to the selected output directory (beside `tracks.csv` by
default). These outputs contain image-plane pixel quantities only:
`speed_px_per_frame`, `speed_px_per_second`, and corresponding image-plane
acceleration units. They do not represent metres/second, physical acceleration,
altitude, range, or a 3D path. `heading_deg` uses image coordinates with
`atan2(dy, dx)` normalized to `[0, 360)`; a zero displacement has an undefined
heading (`null`).

Center positions are retained raw and smoothed using a configurable
exponential moving average (`MotionConfig.smoothing_alpha`, default `0.35`).
Per-frame displacement columns are raw; speed, heading, acceleration, path
statistics, and rule-based motion state use smoothed centers. Defaults and
classification thresholds are declared in `MotionConfig`: stationary is at
most `0.5 px/frame`, slow at most `2 px/frame`, steady below `20 px/frame`,
high motion at least `20 px/frame`, turning uses a `35 degree` local turn
threshold, and irregular motion uses speed coefficient of variation `0.75` or
direction consistency at most `0.35`. These are transparent heuristic
categories, not Drone/Bird identifiers. Motion quality distinguishes short
tracks, unavailable time, gaps, and usable image-plane histories.

Duplicate track/frame rows are reduced to the highest-confidence row (first
row wins ties); rows are then sorted by frame. Acceleration is left undefined
for the first usable interval and around frame gaps. Undefined quantities are
written as empty CSV cells / JSON `null`, never NaN or Infinity. The new module
runs alongside, and does not replace or change, the existing
`temporal_features.py` or Phase 3 reliability score. Dynamic track detail
exposes the trajectory summary and points. New dynamic reliability output
retains the existing Phase 3 `motion_score`, weights, reliability score, and
status unchanged, and may append a separate `trajectory_motion_score`
diagnostic. That diagnostic averages available direction consistency,
straightness, continuity, speed stability (`1 / (1 + speed CV)`), and
acceleration stability (`abs(mean acceleration) / (acceleration std +
abs(mean acceleration))`, or `1` when both are zero). Missing metrics are
excluded; no available components yields null. Motion state and quality are
reported but do not enter the diagnostic formula. The extra score is
image-plane evidence only and is not calibrated probability or a physical
measurement. Historical results remain unchanged and do not acquire generated
motion artifacts.

## Track-level temporal consensus

`temporal_consensus.py` aggregates the existing per-frame Bird/Drone detector
observations for each stable track. Defaults require at least 3 observations,
70% dominant-class consistency, mean dominant-class detector confidence of at
least 0.35, no more than 25% class switches, and tolerate at most 50% missing
frames before raising an uncertainty reason. The decision score weights raw
detector confidence, class consistency, and track continuity at 0.40, 0.40,
and 0.20. These are transparent engineering defaults, not empirically tuned
thresholds; the score is not calibrated probability.

The result is `Bird`, `Drone`, or `Uncertain` and includes reason codes plus
supporting, contradictory, and unclassified frame references. Raw detector
observations are not changed. `process_tracks_consensus` writes these derived
decisions to a separate CSV and supports per-track progress and cancellation.
Full-run RGB and Thermal processing use the complete track sequence, so this
is an offline, non-causal analysis and must not be presented as a live decision.
The causal `causal_up_to_frame` argument is available when evaluating prefixes
of a sequence; future observations are excluded.

Thermal full-video runs persist `temporal/temporal_consensus.csv` and a
configuration/version manifest. Single-frame Thermal controls still do not
run this full-track stage. No independent Bird/Drone ground truth is currently
available, so these decisions have not been shown to improve classification
accuracy.
