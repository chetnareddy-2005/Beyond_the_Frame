# Phase 6 detector experiment preregistration (2026-10-22)

This file freezes the experiment before baseline evaluation and candidate training. Phase 2 scenario requirements and Phase 5 evidence remain unchanged.

## Data

- Source: upstream `DroneDetectionThesis/Drone-detection-dataset`, declared CC0-1.0.
- Modality/classes: visible RGB clips; Bird and Drone only. Project labels are 0=Bird, 1=Drone.
- Source split unit: whole recording/video ID, seed `20261022`; no source video crosses train, validation, or quarantined test.
- Excluded: 11 Bird sequences with unresolved frame/label alignment, two videos with no annotated boxes, all non-Bird/Drone classes, and Phase 5 diagnostic videos `V_BIRD_029`, `V_DRONE_007`, `V_DRONE_024`.
- Frame sampling: training 20 evenly spaced frames per Bird video and 7 per Drone video to keep the training sample balanced; validation and quarantined test sample every 10th frame plus final frame.
- Export: `datasets/processed/phase6_candidate_dataset_2026-10-22/`; source media and labels are untouched.
- Caveat: video IDs are disjoint, but provider metadata has no camera/site/session IDs; cross-video same-scene independence is unknown. This is a controlled development experiment, not a paper-grade external benchmark.

## Frozen model and training settings

- Architecture: project YOLOv5 GhostConv/GhostBottleneck + AFNO2D config `base_detector/yolov5_ghostnet_AFNO/models/yv5_gh.yaml`.
- Initialization: scratch (`--weights ""`), avoiding unresolved exposure in the active checkpoint.
- Input: 640 px; batch 8; AdamW; seed 20261022; max 20 epochs (CPU runtime cap); early stopping patience 15; CPU; 0 dataloader workers; `hyp.scratch-low.yaml`; no frozen layers (omit `--freeze`, whose default `[0]` matches no model parameter).
- Runner: `tools/phase6_train_detector.py` disables the upstream optional dependency auto-install and Git fetch checks for this run; it does not modify installed packages or project training code.
- Windows OpenMP constraint (frozen before candidate training): YOLOv5's `cv2.warpAffine` path in `random_perspective` aborts this environment with duplicate `libiomp5md.dll` initialization, even after disabling OpenCV's thread pool. The runner preserves the central crop needed to turn 2× mosaics back into 640px training frames and adjusts/clips their boxes; only random rotation/scale/translation/shear/perspective warping is omitted. Mosaic/mixup, HSV, and flips remain enabled. This deviation is explicit in all results.
- Training plots are disabled to avoid unrelated OpenCV plotting operations; epoch metrics, validation and checkpoints remain enabled.
- Validation: fixed validation videos only; max 20 epochs; do not inspect the quarantined test split until the candidate and validation decision are frozen.
- Run output: unique new directory below `datasets/processed/phase6_candidate_runs_2026-10-22/`; never overwrite the active checkpoint.

## Acceptance rule

Compare the unchanged active RGB checkpoint and the single scratch-trained candidate at 640 px on the same validation frames. Report mAP@0.50 and mAP@0.50:0.95 by class, plus precision/recall at the project's fixed confidence 0.25 and NMS IoU 0.45. Use source-resolution object-size bins (small ≤32 px, medium >32–96 px, large >96 px).

Candidate is eligible for a one-time quarantined-test evaluation only if all validation gates pass:

1. Macro Bird/Drone AP50 improves by at least 0.05 absolute over the baseline.
2. Bird AP50 improves by at least 0.10 absolute over the baseline.
3. Drone AP50 does not fall by more than 0.05 absolute.
4. Macro AP50:95 does not fall by more than 0.03 absolute, and small-object recall does not regress by more than 0.05 absolute.

If any gate fails, do not promote or tune on the test split. If all gates pass, evaluate once on the quarantined test split and report it separately; promotion still requires the test result not to contradict the validation conclusion. The active checkpoint's training data manifest remains unresolved, so its baseline exposure cannot currently be proven. Results will be labelled development-only until that lineage and cross-scene independence are resolved.

