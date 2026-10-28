# Phase 5 — Detection baseline (2026-10-22)

**Status: COMPLETE for the available, signed RGB diagnostic baseline.** Independent held-out evaluation and paper-grade accuracy claims remain open under Phase 12; this closure does not claim model reliability or generalization.

## Implementation plan completed

1. Freeze evaluation scope to the Phase 4 RGB samples accepted by Chetna Reddy; exclude thermal and unsigned/unreviewed media.
2. Verify source videos, annotation files, review decisions, and checkpoint hashes before inference; map each video frame to its reviewed GT row/label slot.
3. Run the active RGB detector at its frozen operational settings and retain every per-frame detection, confidence, source/frame reference, and annotation hash.
4. Run a separate low-score pass on the same checkpoint/frames for 101-point interpolated AP; do not change weights, NMS, image size, or operational threshold.
5. Compute per-class/per-clip TP, FP, FN, precision, recall, F1 at IoU 0.50 and 0.25, class-confusion counts, AP, and small/medium/large-object recall.
6. Save annotated frame previews, machine-readable ledgers, a hash manifest, report, and a reproducible script. Update the project readiness checklist with scoped status and limitations.

**No training, threshold tuning, source-label edits, source-video edits, or checkpoint changes were made.**

## Frozen evaluation inputs and method

- 22 RGB frames with recorded human `ACCEPT`: 12 frames from the four SMOT4SB training-pilot sequences and 5 each from known clips `V_BIRD_029` and `V_DRONE_007`.
- 59 GT boxes: 54 Bird, 5 Drone. The SMOT4SB GT is copied from Phase 4 source CSVs; the two DroneDetectionThesis label files were decoded read-only with `mcos-decoder` using the Phase 4 reviewed +1 slot mapping.
- RGB checkpoint: `artifacts/checkpoints/best.pt`, SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`; class mapping 0=Bird, 1=Drone.
- Operational inference: project `DetectionService`, image size 640, confidence 0.25, NMS IoU 0.45, CPU.
- AP pass: same frames/checkpoint/preprocessing/NMS, score floor 0.001. AP is 101-point interpolated and reported only as a small-sample diagnostic.
- Matching: per-frame, class-aware, greedy one-to-one IoU. Primary threshold 0.50; 0.25 is diagnostic. Object size bins use maximum source-image box side: small ≤32 px, medium >32–96 px, large >96 px.

## Key result at IoU 0.50 and operational confidence 0.25

| Class | GT | Predictions | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bird | 54 | 0 | 0 | 0 | 54 | 0.000* | 0.000 | 0.000 |
| Drone | 5 | 8 | 5 | 3 | 0 | 0.625 | 1.000 | 0.769 |

\*Bird precision uses the conventional `zero_division=0` reporting rule because the detector emitted no Bird predictions. Three Bird GT objects had overlapping predictions labelled Drone (class confusions). All 53 small Bird GT boxes were missed; the one medium Bird box was also missed. In these selected frames, all five Drone boxes matched (1 small, 4 medium). The Drone result has only five GT boxes and is highly uncertain; it is not a reliable performance estimate.

101-point AP at IoU 0.50 from the score-sweep pass: Bird **0.000** (54 GT; 102 candidates), Drone **1.000** (5 GT; 364 candidates). The Drone AP can be 1.0 while fixed-threshold precision is 0.625 because the five true Drone detections rank above the three false Drone predictions on Bird frames. The tiny, selected support makes this AP unsuitable for comparison or generalization claims.

## Artifacts and reproduction

- Full report and tables: `datasets/processed/phase5_detection_baseline_2026-10-22/PHASE5_DETECTION_BASELINE_REPORT.md`
- Reproducible evaluator: `tools/phase5_detection_baseline.py`
- Machine-readable metrics: `summary.json`
- Frame-by-frame metadata and source hashes: `frames.csv`
- Derived signed GT boxes: `ground_truth.csv`
- Operational detections/confidences: `predictions.csv`
- AP score-sweep predictions/confidences: `ap_score_sweep_predictions.csv`
- Class-confusion records: `class_confusions.csv`
- Artifact/source/overlay hash record: `manifest.json`
- Annotated previews: one `*_overlay.jpg` per evaluated frame in the same output folder.

From the repository root, install the optional documented reader (`python -m pip install mcos-decoder`), then run `python tools/phase5_detection_baseline.py`. The script verifies signed inputs and source hashes before inference, uses derived output only, and does not write to source annotations or model files.

## Exit decision and limitations

- The available-data baseline is reproducible, per-class/per-clip, hash-linked, and includes per-frame confidence evidence and small-object diagnostics. **Phase 5 is complete for this explicit pilot scope.**
- This sample combines known regression clips and SMOT4SB training-split samples. It is not an independent holdout; sample selection is sparse and small. V1 mixed-scene, V2 multi-drone, full-video Bird recall, thermal metrics, and deployment performance are not established.
- The result confirms a severe Bird-vs-Drone issue at the frozen checkpoint/settings. Do not tune on these accepted samples. Phase 6 must first establish checkpoint/training provenance and training-only data, then use a separate validation split.
- Held-out performance, data rights/provenance for other candidates, and final paper evidence remain for Phase 12.
