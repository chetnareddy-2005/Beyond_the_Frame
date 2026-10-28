# Phase 7 — Multi-object tracking diagnostic

**Status:** Phase 7's scoped exit criteria are met for the available evidence: detector/tracker errors are separated, IDF1 and continuity errors are reported, and unavailable V2 acceptance is explicitly pending. The four local clips are SMOT4SB initial-train bird-flock sequences, so multi-drone behavior and independent held-out performance are not evaluated.

## Implementation plan and completed work

1. **Freeze the comparison scope.** Use the same four aligned SMOT4SB clips (0003, 0011, 0034, 0040), 772 frames, existing project tracker defaults, and IoU 0.50. Preserve the videos and labels. These clips are development data only.
2. **Separate detector errors from association errors.** Read the saved detector-plus-ByteTrack run summary, then feed GT boxes directly through the same project `TrackingService` as an oracle diagnostic. Never attribute detector misses to ByteTrack.
3. **Use identity-aware metrics.** Recompute oracle frame-level associations and report IDF1, ID switches, fragments, GT coverage, and per-sequence results. The IDF1 implementation performs class-aware Hungarian box matching at IoU 0.50 per frame, then globally assigns tracker identities to GT identities by maximum matched duration.
4. **Record limits and decision.** Do not tune the tracker on these training-partition clips. V2 remains pending until eligible multi-drone video with reviewed identities and approved use is available.

## Detector + ByteTrack result (saved full-pilot run)

The existing Phase 2 baseline used the frozen RGB checkpoint (`fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`), 640×640 input, confidence 0.25, NMS IoU 0.45, and project ByteTrack adapter on all four clips.

| Measure | Result |
| --- | ---: |
| Frames | 772 |
| GT boxes / GT identities | 4,337 / 45 |
| Detector predictions | 95 (50 Bird, 45 Drone) |
| Class-aware box matches at IoU ≥ 0.50 | 0 |
| GT misses / unmatched detections | 4,337 / 95 |
| Predicted track IDs / matched GT track frames | 3 / 0 |
| IDF1 at IoU 0.50 | 0.000 (no matched GT-predicted observations) |
| ID switches / fragments | Not measurable meaningfully: no GT-linked predicted track observations |

This makes detector recall/class-localization the first blocker in the end-to-end result. The zero IDF1 does not indicate that ByteTrack alone has zero ability; none of its predicted observations associated with these GT boxes. Source: `datasets/processed/phase2/pilot_baseline_eval/summary.json`.

These detector-plus-tracker counts are from the previously saved full-pilot run. An attempted fresh local CPU inference stopped at initialization with the existing duplicate Intel OpenMP runtime error; no unsafe runtime override was used. The reproducible oracle rerun below does not load the detector and completed normally.

## GT-box oracle result (tracker-only diagnostic)

The oracle supplies GT boxes as perfect detections, then runs the project ByteTrack adapter. It isolates association behavior from the detector.

| Measure | Aggregate result |
| --- | ---: |
| Frames / GT boxes / GT identities | 772 / 4,337 / 45 |
| Tracker observations / predicted track IDs | 2,566 / 136 |
| Matched tracker observations at IoU ≥ 0.50 | 2,566 (59.2% of GT boxes) |
| IDTP / IDFP / IDFN | 1,711 / 855 / 2,626 |
| IDF1 | **49.6%** |
| ID switches between consecutive matched observations | **111** |
| Track fragments | **106** |

The earlier oracle summary records 107 “matched gaps”; this report's 106 fragments use a defined visible-GT interruption rule (matched → an annotated GT observation is missed → matched again). The terms count slightly different events and must not be presented as the same metric.

Per-sequence IDF1: 0003 **44.2%**, 0011 **53.2%**, 0034 **66.8%**, 0040 **11.9%**. Detailed counts and the frame-level oracle tracker output are in `datasets/processed/phase2/pilot_baseline_eval/phase7_oracle_tracking_final/summary.json` and `oracle_track_observations.csv`.

## Decision and remaining acceptance

- The detector-driven pipeline produced no GT-linked observation on this pilot; it cannot support a meaningful detector-driven identity-switch assessment here.
- The oracle confirms a separate association/continuity problem even with GT boxes: IDF1 49.6%, 111 switches, and 106 fragments. Do not tune thresholds against these same clips; they are training-partition development data.
- **V2 multiple-drone acceptance is pending.** No eligible V2 multi-drone video with reviewed persistent IDs is available locally. The available four clips are bird-only and cannot substitute for V2.
- The earlier software-contract checks for simultaneous tracks, temporary missed detections, run isolation, and tracking serialization remain evidence of software behavior only, not tracking accuracy.

**Phase 7 closeout:** complete for the agreed diagnostic scope. V2 multi-drone acceptance and independent held-out V1/V2/V3 evaluation remain pending until eligible video and reviewed GT are available. No tracker parameter or production code was changed.
