# Phase 4: Reliability Estimation

This module reads Phase 3 `temporal_features.csv` and writes one reliability row per track. The score is a simple heuristic for the available evidence, not a learned or experimentally optimized formula.

From the project root:

```bash
python -m reliability.reliability_score path/to/temporal_features.csv
```

By default, output is `reliability_results.csv` beside the input. Use `--output` to choose another path and `--threshold` to change the default status threshold of `0.6`.

Default component weights are confidence `0.35`, consistency `0.25`, persistence `0.25`, and motion `0.15`. They are simple starting values, not optimal weights. The weighted sum is divided by the sum of the weights and clipped to `[0, 1]`.

- Confidence score is `mean_confidence`, clipped to `[0, 1]`.
- Consistency score is `1 - confidence_std / 0.5`, clipped to `[0, 1]`.
- Persistence score is `min(frames_seen / 10, 1) * persistence`.
- Motion score is `1 / (1 + mean_displacement / box_diagonal)`, where `box_diagonal = sqrt(mean_width^2 + mean_height^2)`. With a zero-sized box, it is `1` only when displacement is zero.

Scores at or above the threshold are `CONFIDENT`; lower scores are `UNCERTAIN`. These heuristic choices should be evaluated experimentally in the project.

## Dynamic-run trajectory diagnostic

Dynamic runs may pass their run-local `motion_track_summary.csv` using
`trajectory_summary_csv`. This adds `legacy_motion_score` (an exact copy of the
existing `motion_score`) and `trajectory_motion_score` plus named component
diagnostics to the run's reliability output. Without this optional argument,
the historical output schema and default formula are unchanged.

The trajectory diagnostic is the arithmetic mean of the available components:

- direction consistency, straightness ratio, and consecutive-frame ratio as
  emitted by temporal motion analysis;
- speed stability, `1 / (1 + speed_std / mean_speed_px_per_second)`;
- acceleration stability, `abs(mean_acceleration_px_per_second2) /
  (acceleration_std + abs(mean_acceleration_px_per_second2))`, or `1` when
  both values are zero.

If mean speed is zero with nonzero speed variation, speed stability is zero.
Missing component values are omitted from the mean; if no component is
available, the trajectory score is null. Motion state and quality are also
passed through as diagnostics but do not affect the score. These are
image-plane, heuristic diagnostics—not calibrated probabilities, physical
world measurements, or Drone/Bird identifiers. The trajectory score does not
change `reliability_score`, its status threshold, or the historical component
weights.
