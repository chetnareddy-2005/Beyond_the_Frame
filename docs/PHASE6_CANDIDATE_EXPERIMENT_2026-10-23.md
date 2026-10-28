# Phase 6 — Candidate detector experiment (2026-10-23)

**Status: Colab training execution finished; the preregistered improvement gates were not completed, so Phase 6 acceptance is unmet and no candidate is promoted.** This report supersedes the earlier Phase 6 stop decision for training readiness, which concerned the local Roboflow image export only. It does not change Phase 2 acceptance rules or Phase 5 diagnostic records.

## Eligible corpus and audit

- Source: [DroneDetectionThesis official dataset repository](https://github.com/DroneDetectionThesis/Drone-detection-dataset), with upstream CC0-1.0 declaration recorded in the project manifest.
- Selection: 165 visible-light source videos containing Bird or Drone labels. Videos, not frames, are the split unit.
- Audit: every frame and corresponding MCOS label file was decoded; frame counts, label coordinates, and source video/frame mapping were checked. Spot-review sheets cover all 152 structurally aligned candidate sequences. Eleven unresolved Bird sequences and two empty-label videos were excluded.
- Split: 104 source videos for training, 21 for validation, 24 quarantined for test. No source video ID crosses partitions. Upstream does not provide camera/site/session IDs, so distinct-scene independence remains unknown; results are development-only.
- Frame export and class map: `datasets/processed/phase6_candidate_dataset_2026-10-22/`, class 0=Bird and 1=Drone. Train 1,079 frames / 1,003 Bird and Drone boxes; validation 685 frames / 685 boxes (164 Bird, 521 Drone); quarantined test 787 frames / 734 boxes (198 Bird, 536 Drone). Phase 5 examples `V_BIRD_029`, `V_DRONE_007`, and `V_DRONE_024` are excluded from train/validation and retained only in quarantine.

## Frozen comparison

The active checkpoint remains unchanged and has SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`. Embedded checkpoint metadata indicates epoch 76 of a 200-epoch run and scratch initialization, but the original dataset manifest/config is still missing. Its prior exposure to this corpus is therefore unresolved.

On the fixed 685-frame validation split at 640px, confidence 0.25 and NMS IoU 0.45, the active checkpoint produced:

| Class | Instances | Precision | Recall | AP50 | AP50:95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bird | 164 | 0.000 | 0.000 | 0.000 | 0.000 |
| Drone | 521 | 0.583 | 0.673 | 0.637 | 0.164 |
| Macro / overall | 685 | 0.291 | 0.337 | 0.318 | 0.0818 |

Recorded output: `datasets/processed/phase6_candidate_runs_2026-10-22/active_baseline_validation_retry/validation_metrics.json`. The validator printed metrics before a process-shutdown OpenMP error; the metrics and label outputs were written before that cleanup failure.

## Candidate run

- Architecture: YOLOv5 GhostConv/GhostBottleneck + AFNO2D (`base_detector/yolov5_ghostnet_AFNO/models/yv5_gh.yaml`), initialized from scratch.
- Settings: 640px; batch 8; AdamW; seed 20261022; 20-epoch cap; patience 15; CPU; no frozen layers; `hyp.scratch-low.yaml`.
- Windows runtime adjustment: `cv2.warpAffine` in the stock random-perspective augmenter aborts with duplicate Intel OpenMP initialization. The dedicated runner preserves the center crop needed for 2× Mosaic images and clips/adjusts their boxes, while bypassing random affine/perspective warps. Mosaic/mixup, HSV, and flips remain enabled. Plots and data-loader subprocesses are disabled; model validation/checkpoint writing remain enabled. This is a declared experiment deviation, not a hidden production change.
- Runner: `tools/phase6_train_detector.py`; no project checkpoint or installed package is modified.
- Output: `datasets/processed/phase6_candidate_runs_2026-10-22/phase6_ghost_afno_cc0_corrected_640/`.

## Predeclared decision

The candidate must exceed validation macro AP50 by at least 0.05 and Bird AP50 by at least 0.10, with Drone AP50 no worse than -0.05, macro AP50:95 no worse than -0.03, and small-object recall no worse than -0.05 versus the active checkpoint. If any gate fails, stop and do not inspect quarantined test predictions. If every gate passes, run the candidate once on the quarantined test split and report it separately. Keep the active app checkpoint unchanged unless validation and the one-time test both support promotion and the checkpoint exposure issue is resolved.

## Remaining exit checks

1. Finish the declared candidate run; save immutable best/last weights and epoch metrics.
2. Compare candidate against the active checkpoint on the same validation frames, including per-class AP50/AP50:95, precision/recall, and source-resolution size bins.
3. Apply the frozen gates without changing thresholds. Open the quarantined test set only if all gates pass.
4. Record final SHA-256 values, limitations, and update `docs/PROJECT_READINESS_CHECKLIST.md`.

## Colab execution outcome — 2026-10-23

- The uploaded `run/weights/last.pt` checkpoint resumed from epoch 17 and completed epochs 18–19, reaching the configured 20-epoch total. The custom Ghost+AFNO model loaded and the run wrote `best.pt`, `last.pt`, `results.csv`, `opt.yaml`, and `hyp.yaml` under `Behind_the_Frame phase6_colab_transfer /colab_runs/phase6_colab_resume_20261023_161136/`.
- The same 685-frame staged validation split was evaluated for the uploaded `run/weights/best.pt` (SHA-256 `41381e5ffbb14bb278baee9293934f83fb2fbf6543f760d162e9445a89da611`) and the new run's `best.pt` (SHA-256 `69907c0e5d096a3486a398f0847a53e45f4a8a3af10f969b5aa97d34187ba987`), at 640 px with identical validator settings (`conf_thres=0.001`, NMS IoU `0.60`). Both returned overall P=0.904, R=0.838, AP50=0.884, AP50:95=0.401; Bird AP50=0.871 and Drone AP50=0.898.
- A tensor comparison found 441/441 shared effective model tensors identical (`max absolute difference=0`). The candidate therefore contains the same model weights as the checkpoint used as the Colab “baseline”; the extra two resumed epochs did not improve/change the selected best model. The final `results.csv` epoch metrics (AP50=0.664, AP50:95=0.301) are the last-epoch row, whereas the post-run validation above evaluates the retained `best.pt`.
- **This is not the preregistered active-checkpoint comparison.** The comparison used the uploaded `transfer/run/weights/best.pt`, while the frozen project active RGB comparator is `artifacts/checkpoints/best.pt` with SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`. The frozen comparator was not loaded/evaluated in this Colab comparison. The earlier preregistered active-checkpoint metrics (macro AP50=0.318) therefore cannot be compared to the Colab 0.884 result.
- As a result, the preregistered Bird/Drone AP gates, operating-point comparison, and source-resolution size-bin gate are **not established**. The quarantined test split was not opened. The app checkpoint remains unchanged.

**Phase 6 decision:** the Colab training job completed successfully as a compute job, but **Phase 6 did not complete successfully against its frozen plan**. Record it as *execution complete; validation improvement not demonstrated; required comparator/gates still outstanding; no promotion*. To close the planned experiment, evaluate the frozen active checkpoint and candidate on the exact same staged validation split with the preregistered settings/size bins, then apply the frozen gates. Do not open the quarantined test unless every gate passes.
