# Phase 6 — Targeted detector improvement (2026-10-22)

**Status: readiness audit and fail-closed implementation complete; detector training is blocked before training.** No candidate weights were created, no active model was replaced, and no improvement claim is made. This is the evidence-based result of applying the Phase 6 exit rules to the data currently available.

## Implementation plan

### Gate A — freeze the comparator and recover provenance

1. Freeze the Phase 5 RGB baseline, inference settings, and active checkpoint SHA-256.
2. Link the checkpoint to its original training run, initialization weights, exact data files and source groups, full hyperparameters, software/device, logs, and best-checkpoint selection.
3. If lineage is incomplete, do not interpret the old train/validation metrics as an independently reproducible baseline.

### Gate B — establish eligible training and validation data

4. Inventory each Bird/Drone candidate's license, class mapping, image/label validity, source video/sequence identity, exact and near duplicates, and previous checkpoint exposure.
5. Partition whole source sequences into training and validation, with a separate final test set kept untouched. Do not split frames from one source video across partitions.
6. Exclude Phase 5 reviewed diagnostic frames from any tuning. Keep the frozen Phase 5 checkpoint unchanged as the comparator.
7. If the available corpus cannot meet these requirements, stop before training and record the missing evidence.

### Gate C — preregister, train, and compare

8. Freeze the model change, seed, optimizer/hyperparameters, image size, class map, metric protocol, and acceptance rule before training. Keep the Phase 2 scenario requirements unchanged; Phase 6's improvement rule is an additional experiment rule.
9. Train into a new immutable run directory and record complete source/data/config/checkpoint hashes, logs, environment, and validation outputs.
10. Evaluate baseline and candidate on the same untouched validation sequences using per-class AP50 and AP50:95, fixed-operating-point precision/recall, Bird/Drone confusion, and source-resolution size bins.
11. Promote only if the predeclared rule passes without unacceptable class or small-object regression. Otherwise retain the baseline and report the failed experiment.

## Work implemented now

- Added `tools/phase6_readiness_audit.py`, a read-only auditor of the local RGB Roboflow image export. It checks every image/label pair, YOLO label shape/class/range, per-class box counts, image SHA-256 values, exact-image duplicates across splits, recoverable source-video group membership, and the frozen checkpoint hash.
- The auditor writes a machine-readable evidence report and a hash-linked per-image CSV under `datasets/processed/phase6_readiness_2026-10-22/`.
- The decision is fail-closed: `training_allowed=false` and `checkpoint_promotion_allowed=false` until checkpoint lineage and independent source-group splits are evidenced.
- Updated `docs/PROJECT_READINESS_CHECKLIST.md` with Phase 6's actual readiness and remaining exit criteria.
- No source image, label, video, model weight, Phase 5 record, or threshold was changed. No training or tuning was run.

## Audit findings

| Check | Result | Phase 6 consequence |
| --- | --- | --- |
| Active RGB checkpoint | SHA-256 matches frozen Phase 1/5 value `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b` | Comparator remains identified, but hash alone does not establish training lineage. |
| Checkpoint training lineage | Not verified. Existing handoff found embedded options suggesting a 200-epoch budget and epoch 76, but not the original command, complete config/results, exact data membership, source-group split, or checkpoint-to-run chain. | Cannot determine whether current weights have already seen candidate validation/test frames. |
| `datasets/drone_vs_bird` export | 2,920 images, 2,920 paired labels, 707 Bird boxes, 2,369 Drone boxes; all inspected rows are structurally valid. Local export declares CC BY 4.0. | Usable as an image-development candidate only after source identity and exposure are resolved. It contains no source videos/tracks. |
| Exact image duplicates across current splits | 0 groups found by the new full-file SHA-256 audit. | This clears exact-byte image duplicates only; it does not prove sequence independence. |
| Recoverable source IDs | 69/2,920 images (video14–video18); all five IDs cross declared train/validation/test splits. | Existing split boundaries leak known source videos. |
| Unidentified source grouping | 2,851/2,920 images have no recoverable source-video ID. The prior review also found 194/292 test images to be same-scene candidates in an ORB/RANSAC screen; those are not confirmed duplicates and were not removed. | Cannot certify the remaining current validation/test data as source-independent. Do not move or delete images based on heuristic scene matches. |
| Other local Bird/Drone candidates | SMOT4SB is Bird-only training-split video; DroneDetectionThesis samples are single-target diagnostic clips with only sampled mapping review; CSIR-CSIO is a 10-image sample with provisional class mapping and no video/source split; UB-SOD media archive is absent; WOSDETC access is DUA-gated. | None currently supplies the approved, provenance-safe, source-disjoint Bird+Drone training/validation pair required for this experiment. |
| Phase 5 evidence | 22 signed RGB samples and 59 boxes are already in a frozen diagnostic report. | Keep them out of all Phase 6 tuning and validation. |

The auditor found **zero exact image hashes crossing declared splits**, while separately confirming that **all five recoverable source-video groups cross splits**. These statements measure different things and are both important: byte-identical files are not the only way adjacent frames or repeated scenes leak across a split.

## Exit decision

Phase 6 is **not complete** because the target outcome—validated detector improvement—cannot be established from current evidence. Training on the current export and reporting its existing `valid` set as independent would be unsafe: 5 known source-video groups overlap partitions, the remaining source groups are unknown, and the active checkpoint's exposure is unresolved. A tiny random split from ten sample images, Bird-only SMOT4SB clips, or the Phase 5 accepted frames would not repair these problems.

The following are required to resume the training portion:

1. Recover the original RGB training run/data manifest and verify whether it includes any proposed validation sources; otherwise explicitly treat the active checkpoint as lineage-unknown and establish a new clean comparator.
2. Obtain or construct a rights-cleared Bird+Drone source corpus with source-video IDs, then reserve whole independent videos for validation and final test before model experiments.
3. Freeze the experiment config and supplementary improvement rule against the locked Phase 2 metric definitions.

## Reproduction

From the repository root run `python tools/phase6_readiness_audit.py`. It hashes every local image, reads labels, and writes only its dated Phase 6 output folder. It does not train, alter source files, or update checkpoints.

Evidence outputs:

- `datasets/processed/phase6_readiness_2026-10-22/PHASE6_READINESS_AND_IMPLEMENTATION_PLAN.md`
- `datasets/processed/phase6_readiness_2026-10-22/readiness_audit.json`
- `datasets/processed/phase6_readiness_2026-10-22/dataset_image_manifest.csv`
