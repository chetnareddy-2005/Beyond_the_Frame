# Phase 6: Evidence-Level Fusion

Run from the project root:

```bash
python -m fusion.evidence_fusion tracks.csv temporal_features.csv reliability_results.csv
```

Add `--vlm vlm_results.csv` when real Phase 5 VLM results are available.
The default output is `fusion_results.csv` beside `tracks.csv`; `--output`
can choose another path.

Detector confidence is averaged per observed class and those means are
normalized across the classes present for each track when multiple classes
are observed. For a single observed class, its mean confidence is preserved.
`Drone` maps to `UAV`; `Bird` stays `Bird`. Unknown detector classes are
rejected.

When a track has semantic VLM results, its confidence is fused using this
formula. If Selective Evidence supplies multiple rows for a track, Phase 5
first averages the VLM confidence values separately for each semantic label
across its selected frames, then supplies all per-label means to this same
formula. Conflicting labels are retained as distinct class evidence; none of
the selected-frame rows is arbitrarily discarded. If per-label means tie, the
existing deterministic ascending-label tie-break is used. `Unknown` rows do not
contribute semantic evidence, and are retained as the track-level VLM
metadata only when no semantic label is present.

```text
fused_score(class) =
    reliability_score * detector_score(class)
    + (1 - reliability_score) * vlm_confidence(class)
```

The reliability score supplies the detector's weight; the complementary
weight is used for VLM evidence. Unreported classes have a score of zero.
`Unknown` rows are preserved as metadata but do not contribute semantic
evidence. If no semantic VLM label is available, the normalized detector
evidence determines the label (`detector_temporal` mode); no VLM score is
invented. Its decision confidence is the leading detector score multiplied by
the Phase 4 reliability score, clipped to `[0, 1]`. This is a reliability-
weighted heuristic score, not a calibrated probability. Temporal features
are joined for `frames_seen` metadata only, since their motion/persistence
information is already summarized by the Phase 4 reliability score.

## Dashboard run fusion with track consensus

The RGB dashboard pipeline also supplies its persisted temporal-consensus CSV
to `run_phase5_fusion`. In that mode, per-track detector support is the
fraction of observed Bird and Drone detections in each class, and completed
temporal consensus gates the track label. This prevents a small number of
high-confidence class flips from outweighing a long, consistent track. Tracks
whose consensus is uncertain abstain as `Other`; they do not raise a UAV alert.
Raw frame-level detector observations remain unchanged in `tracks.csv` and the
evidence ledger. Early UAV alerts use the same confident Drone consensus rule.

The standalone `fusion.evidence_fusion` CLI described above has no temporal
consensus input and retains its historical per-class mean-confidence behavior.

VLM confidence is a generation-token probability proxy, not a calibrated
probability. Averaging selected-frame confidence proxies does not calibrate
them; the aggregated values remain heuristic VLM evidence.

The rule and Phase 4 reliability values are initial explainable choices, not
claims of optimality. The output contains one row per track and records the
leading detector class as reported by the detector/its normalized score, reliability, optional VLM label/confidence,
fusion mode, final label/decision confidence, frame count, and Phase 4 status.
