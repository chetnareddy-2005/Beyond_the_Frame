# Phase 3 — Local dataset disposition

**Review date:** 2026-10-22  
**Scope:** Decide how already-local data may be used for the frozen V1/V2/V3 requirements. This is a documentary/read-only triage: no dataset files were moved, deleted, downloaded, extracted, relabeled, or used for training in this review.  
**Decision status:** **Complete for the agreed local dataset search, audit, and disposition scope.** Project-wide evaluation-data readiness remains incomplete: V1/V2 access, an independent V3 holdout, and Thermal provenance/evaluation are deferred to Future Scope below.

The follow-on video/label alignment review is recorded in `docs/PHASE3_VIDEO_LABEL_ALIGNMENT_AUDIT_2026-10-22.md`. It verified all SMOT4SB source-JPG/derived-MP4 frame mappings and confirmed from the original annotation JSON that the apparent gaps in two sequences are intentionally empty-labeled frames. The owner confirmed the two unlabeled flock videos were found in their gallery; their original source and rights are unknown, so they are excluded from benchmark, paper, and shareable evaluation use unless provenance is recovered.

## Decision rules

- **Development/diagnostic use** does not mean held-out evaluation or validated model performance.
- A matching frame count does not prove annotation-to-video alignment.
- Keep all frames from a source sequence in one split. An image-level test split with unknown or cross-split video IDs is not an independent video test set.
- Do not infer friendly/authorized status from visual Bird/Drone labels.
- Preserve questionable or incomplete files in place; this review does not authorize cleanup or deletion.

## Local-data decisions

| Local data | Phase 3 disposition | Permitted use now | Conditions / reason |
|---|---|---|---|
| SMOT4SB sequences 0003, 0011, 0034, 0040 | **Use for V3 development diagnostics only** | Bird detection/tracking debugging and controlled development comparisons | 772 annotated frames, 4,337 Bird boxes, 45 sequence-local IDs; these are training-split sequences. Keep the four sequences intact and do not report them as held-out results. Preserve attribution and the local dataset terms; reconcile the older pilot's provenance with the current public release before acquiring more. |
| `datasets/drone_vs_bird` Roboflow v2 still images | **Development-only image supplement** | Exploratory image-level detector diagnostics | Local export declares CC BY 4.0; preserve attribution. It has no video or track IDs. Recovered source IDs cross existing splits, and most source IDs are unknown. Do not use current test metrics as independent evidence or mix these images into a claimed sequence-independent evaluation. |
| DroneDetectionThesis videos + MATLAB labels | **Selected pairs are diagnostic candidates after sampled alignment review; remaining pairs stay held** | Per-clip single-target RGB/Thermal diagnostics only | Upstream manifest records CC0-1.0. 397/650 pairs have equal frame counts; that alone does not prove alignment. All 130 Bird and 118 Drone mismatches need individual review. Three selected RGB pairs show sampled +1 label-slot mapping; two Thermal Drone pairs show sampled direct mapping. These do not satisfy mixed V1 or multi-drone V2. |
| Local RGB clips (`V_BIRD_029`, `V_DRONE_007`, `V_DRONE_024`, related samples) | **First three: known-clip regression/diagnostic only; all others held** | Reproduce known behavior and inspect candidate labels | First three sampled overlays align using label slot = 1-based video frame + 1 because slot 1 is empty. `V_BIRD_029` is a known bird false-positive clip, and these clips have prior model exposure; they are not blind held-out tests. Related clips need separate alignment review. |
| Two local flock videos (`18217550…mp4`, `19768258…mp4`) | **Exclude from project benchmark/paper/shareable evaluation unless provenance is recovered** | No scored evaluation; preserve files untouched | Owner found them in personal gallery; original source/license remains unknown and there are no GT boxes/IDs. Do not redistribute, annotate for scored evaluation, or assign a split unless rights are established. |
| Local Thermal clips (`IR_DRONE_040`, `IR_DRONE_041`, `IR_BIRD_007`) | **Drone clips: software/alignment review only; Bird clip: exclude from scoring** | Thermal pipeline smoke and visual label checks | Five sampled boxes per Drone clip visually align with direct frame indexing; this is not independent accuracy evidence because checkpoint provenance/sequence independence remain unresolved. `IR_BIRD_007` has a one-frame delta and zero boxes across decoded label slots. Thermal cannot satisfy RGB V1/V2. |
| `datasets/thermal_drone` image exports | **Hold from accuracy evaluation and retraining** | Provenance/split-integrity investigation only | Checkpoint training membership is unproven; the local 4,982-image multiclass export has reported exact duplicate groups crossing declared splits and no source-video/frame mapping. Anti-UAV-derived extraction terms also need verification. Do not treat its apparent train/val/test split as sequence-independent. |
| UB-SOD Figshare v1 annotations/split metadata | **Metadata-only; image archive absent** | Format, class mapping, and split metadata review | Public CC BY 4.0 terms are recorded in the Phase 2 source audit. The local acquisition contains annotations/metadata, not the image media. If images are later acquired, remap provider classes (`UAV=0`, `Bird=1`) explicitly and audit source-video grouping; it is still-image data, not MOT/V1/V2 evidence. |
| CSIR-CSIO YOLOBirDrone sample (`datasets/external/YOLOBirDrone_CSIR_sample_2026-10-22/`) | **Small image-level development supplement; not evaluation data** | Exploratory detector development after preserving the provisional class mapping | 10 images + 10 YOLO text labels (all images 3840×2160; 33 boxes total, IDs 0=15 and 1=18). Rows and normalized coordinates are valid. Manual overlay review of all 10 images consistently indicates ID 0=Drone and ID 1=Bird; the sample ZIP lacks an official class-map file, so this is a project-reviewed inference, not provider metadata. CSIR-CSIO's page permits non-commercial academic/research/model-development use, prohibits redistribution/public sharing without written permission, and requires citation; commercial use needs prior written permission/license. Still images cannot validate video tracking or trajectories. The full dataset access form is separate. See the sample's `acquisition_audit.json`. |
| Purdue MultiTarget UAV annotation ZIP | **Preserve; not usable for scored evaluation** | Source/format investigation | Matching videos, verified official terms, split, and full frame/ID alignment are unresolved. Do not pair with unrelated videos or infer a license from third-party copies. |
| WOSDETC / Drone-vs-Bird Challenge | **Future V1 candidate; access gated** | No local use | Requires provider-supplied DUA review/access. No request or signature has occurred. Do not acquire until the owner completes and confirms the provider process. |
| Anti-UAV Track 3 | **Future V2 tracking candidate; access/terms unverified** | No local use | Current official download/license path is unresolved in the Phase 2 audit. It supports multi-UAV tracking, not Bird-vs-Drone class validation. |
| Demo/example videos and incomplete/invalid archives | **Software smoke only / preserve unused archives** | UI and pipeline smoke where appropriate | No reviewed GT or benchmark provenance. Incomplete transfers and invalid archives are not inputs for evaluation; do not delete them as part of this decision. |
| Radar/Boreas/RADIATE material | **Out of scope for current RGB/Thermal V1–V3 evaluation** | None for the current video scenarios | Different sensing/task definition. Keep separate unless a radar input format and evaluation requirement are explicitly added. |

## Scenario readiness after disposition

| Frozen scenario | Current eligible local evidence | Phase 3 result |
|---|---|---|
| **V1: birds and drone in one scene** | None | **Not ready.** Existing bird-only and drone-only clips cannot be combined to simulate a real mixed scene. WOSDETC is a gated future candidate. |
| **V2: multiple drones** | None with verified video, terms, split, and reviewed separate-object GT | **Not ready.** Purdue has annotations without verified matching videos/terms; Anti-UAV Track 3 access/terms are unresolved. |
| **V3: multiple birds** | Four SMOT4SB training sequences | **Development ready only.** No independent local V3 holdout is approved. Two flock videos remain candidates pending rights and reviewed labels. |
| **Thermal evaluation** | Thermal pipeline smoke clips | **Software/alignment checks only.** No provenance-safe, independently annotated thermal evaluation set is approved. |

## Phase 3 exit decision

- [x] Local dataset candidates are assigned a specific current disposition.
- [x] “Development,” “software smoke,” “candidate,” and “not approved for evaluation” are distinguished.
- [x] V1/V2/V3 readiness is stated against the frozen Phase 2 scenarios.
- [x] Known license, provenance, frame-alignment, and split-leakage limits are attached to the relevant data.
- [x] Local candidate inventory, alignment evidence, permitted uses, and scenario readiness gaps are recorded.

**Conclusion:** Phase 3 is complete **within the agreed scope of auditing and deciding how the already-local data may be used**. It is not a claim that V1/V2/V3/Thermal datasets are ready or that accuracy has been evaluated. The following remain incomplete project needs and are deferred to **Future Scope**. Do not retrain on held-out candidates.

### Deferred to Future Scope

- Obtain/approve a rights-cleared V1 mixed bird+drone video and reviewed per-frame labels.
- Obtain/approve a rights-cleared V2 multi-drone video with persistent identities and reviewed labels.
- Establish a rights-cleared, independent V3 bird-flock holdout; the four SMOT4SB clips remain training-split development data.
- Establish Thermal checkpoint training provenance and a sequence-independent, human-labeled Thermal holdout.
- Optionally generate temporary diagnostic labels using the sampled +1 label-slot mapping for `V_BIRD_029`, `V_DRONE_007`, and `V_DRONE_024`, then rerun diagnostics. This remains debugging-only, does not alter source labels, and cannot serve as blind evaluation.

No further local data disposition task is open in this phase. These future items require data/access/provenance that is not currently available or approved; they are not silently counted as completed.

### Additional local archive inspection — 2026-10-22

- `MVA2025-SMOT4SB-main.zip` is the official baseline-code repository snapshot; its `datasets/` directory is only an empty placeholder. Its README links to a separate dataset Drive. The project's existing pilot manifest records four locally staged sequences (0003, 0011, 0034, 0040) from the training split, suitable for development/debugging only. No duplicate split was downloaded during this review.
- `Sample_Dataset.zip` was CRC-checked and extracted without changing the source archive to `datasets/external/YOLOBirDrone_CSIR_sample_2026-10-22/`. Its 10 image/label pairs passed structural checks and visual overlay review. The provisional mapping (class 0=Drone, class 1=Bird) is consistent across the ten images but is not supplied as official metadata. This small sample is available for development-only image experiments, not evaluation. No model run was performed.

## Evidence references

- `datasets/processed/phase2/LOCAL_DATASET_INVENTORY_AND_NEXT_EVALUATION_2026-10-22.md`
- `datasets/processed/phase2/PHASE2_VIDEO_ASSIGNMENT_AND_GAPS.md`
- `docs/PHASE2_DATASET_SOURCE_AND_ACCESS_AUDIT.md`
- `results/v3_thermal_provenance_request_20261016/provenance_and_review_readiness.md`
- `results/v3_thermal_independent_eval_20261016/eligibility_report.md`
