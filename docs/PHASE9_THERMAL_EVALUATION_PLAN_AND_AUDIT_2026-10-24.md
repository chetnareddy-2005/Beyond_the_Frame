# Phase 9 — Thermal evaluation plan and audit

**Audit date:** 2026-10-24 (updated after notebook review)  
**Status:** ✅ Scoped thermal diagnostic/report complete; independent thermal accuracy validation remains unresolved.  
**Scope:** Existing thermal checkpoint, saved video outputs, data-integrity reports, thermal pipeline behavior, and image-plane trajectory limits. No new inference, training, threshold tuning, annotations, or checkpoint changes were made.

## Plan and acceptance gates

Phase 9 is intended to establish what the thermal pipeline does and, where eligible reviewed labels exist, evaluate detection and tracking separately from RGB. It must not treat model predictions as ground truth.

1. **Freeze identity and settings.** Record checkpoint hash, architecture/class order, thermal preprocessing, inference thresholds, and device. Keep the checkpoint unchanged.
2. **Establish evaluation eligibility before metrics.** Recover authoritative training-data/split lineage; map thermal images and clips to source sequences; identify and resolve train/validation/test duplicates; obtain eligible whole-sequence held-out clips with blinded, adjudicated frame/object labels and track IDs.
3. **Run only on eligible data.** Freeze clip IDs and inference configuration before running. Produce class-wise P/R, AP50, AP50:95, source-resolution object-size recall, and tracking metrics such as IDF1/HOTA or a preregistered equivalent. Include per-clip results and reviewed error examples.
4. **Audit trajectory and sensor limits.** Report trajectories only in image-plane pixels unless camera calibration, depth, and geo-registration support physical coordinates. Document grayscale normalization and any temporal/geometry limitations.
5. **Reconcile software behavior.** Verify the saved thermal run and dashboard state against its documented detector/tracker-only scope. Do not imply that unsupported RGB fusion or alert stages run for Thermal.
6. **Freeze a reproducibility pack.** Save manifests, hashes, run settings, per-class metrics, clip-level outputs, limits, and the exact acceptance decision. Do not modify the checkpoint or tune thresholds using the held-out evaluation.

### Acceptance decision rule

A thermal accuracy-evaluation claim requires all of: (a) checkpoint training lineage or an explicitly newly trained, lineage-complete checkpoint; (b) sequence-level independence from training and development data; (c) complete and suitable blinded human annotations with adjudication; (d) frozen inference settings and reproducible outputs; and (e) class-wise detection and tracking metrics with limitations. If any prerequisite is absent, report **blocked / metrics unavailable**, not zero performance and not a pass.

### Notebook evidence found after the initial audit

The user supplied `C:\Users\Ranik\Downloads\WORKSPACE1.ipynb` and `C:\Users\Ranik\Downloads\WorkSpace2 (1).ipynb`. The notebooks were inspected as saved records only; neither notebook was re-executed. `WORKSPACE1.ipynb` contains thermal dataset preparation, resumed fine-tuning, and a completed test-split evaluation (cells 80/82, 89–90, 99, 101–102, 105–106). `WorkSpace2 (1).ipynb` is Phase 6/RGB work and added no Thermal training/evaluation evidence.

- **Dataset in the notebook:** combined thermal Bird/Drone set, class order `0=Bird`, `1=Drone`; train 3,144 image/label pairs, validation 901, test 937; no missing pairs in the recorded checks (4,982 total).
- **Training:** custom YOLOv5 GhostNet + AFNO2D (`models/yv5_gh.yaml`), initialized from the saved RGB baseline checkpoint, 640 px, batch 16, AdamW, seed 0, max 30 epochs, Tesla T4. The first attempt stopped on a PyTorch checkpoint-loading compatibility error; the notebook records a compatibility fix and resume from epoch 14. The resumed run reached 30/30 epochs, exited code 0, and reports 35m50s elapsed. This corrects the earlier statement that no thermal training evidence was available.
- **Test evaluation:** cell 105 uses the run `best.pt`, `task=test`, `img=640`, `batch-size=16`, `device=0`, `conf_thres=0.001`, `iou_thres=0.6`. All 937 test images were processed and the command exited successfully. Saved console metrics:

| Class | Images | Instances | Precision | Recall | mAP50 | mAP50–95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All | 937 | 1,120 | 0.923 | 0.838 | 0.882 | 0.440 |
| Bird | 937 | 531 | 0.945 | 0.906 | 0.941 | 0.484 |
| Drone | 937 | 589 | 0.901 | 0.769 | 0.823 | 0.395 |

These are saved YOLO validation/test-task summary metrics for this protocol. They are **not** P/R at the production operating point `conf=0.25, NMS IoU=0.45`; do not describe them as fixed-threshold results. The notebook preserves the metrics, but the result folder had no text/CSV/JSON metric report. This is a dataset-split result, not an independent external-test claim.
- **Checkpoint identity:** cell 106 records copying the resulting 5.5 MB checkpoint to Drive as `Behind_the_Frame/thermal_bird_drone_best.pt`. The local SHA-256 above identifies the current local artifact, but the notebook does not record a training/evaluation-time digest; byte-for-byte identity between evaluated and current local files is therefore unproven.
- **Lineage/split caveats:** notebook settings identify the initialization and fine-tuning run, but not the RGB baseline's full training-data lineage, original thermal source-video IDs, or whole-sequence independence. A related local processed export had exact duplicate groups across declared splits; the notebook lacks a path/hash crosswalk proving those were the exact files used here. Independence is **unestablished / at risk**, not proven leakage and not a clean independent holdout.
- **Annotation caveat:** recorded pair counts and class IDs do not establish semantic correctness or blinded human adjudication of all test boxes. Treat these as recorded dataset-split metrics, not a human-reviewed benchmark.
## Evidence audited

### Checkpoint and preprocessing

- Current thermal checkpoint: `artifacts/checkpoints/thermal_bird_drone_best.pt`.
- Recomputed SHA-256: `ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477` (5,770,787 bytes); matches prior thermal audit metadata.
- Architecture recorded as custom YOLOv5 + GhostNet + AFNO2D; model class order `0=Bird`, `1=Drone`; production diagnostic used confidence 0.25, NMS IoU 0.45, image size 640, CPU.
- Thermal input is converted BGR→grayscale, normalized per frame by min/max to uint8, replicated to three channels, then letterboxed and scaled. This is image-intensity normalization, not calibrated temperature.
- Dataset audit found training letterbox geometry fixed at 640×640 while stride-auto inference for 5:4 inputs can produce 640×512. The effect on predictions has not been measured. Bird-source annotation/preprocessing provenance is incomplete.
- Checkpoint training-data lineage, exact source sequences, and original split membership are not established. The checkpoint is stripped (`epoch=-1`); embedded metadata does not establish a reliable dataset lineage.

### Existing video diagnostic (predictions only)

Source: `results/thermal_video_validation_20261013_1909/evaluation_report.json` and its saved detections/tracks. No new inference was run.

| Clip | Frames decoded | Model detections | Predicted Bird | Predicted Drone | Mean CPU inference time/frame | Tracker observations / IDs |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `IR_BIRD_007.mp4` | 301 | 310 | 0 | 310 | 0.1012 s | 305 / 2 |
| `IR_DRONE_040.mp4` | 305 | 317 | 0 | 317 | 0.1016 s | 305 / 1 |
| `IR_DRONE_041.mp4` | 300 | 300 | 0 | 300 | 0.1015 s | 300 / 1 |
| **Total** | **906** | **927** | **0** | **927** | — | **910 / 4 clip-local IDs** |

All 906 frames had at least one model detection. These are prediction counts, **not** precision, recall, AP, false-positive rate, or a measured accuracy result. `IR_BIRD_007` is a filename, not independently verified ground truth; nevertheless, its 310 Drone predictions are a high-priority human-review case. Existing analysis found raw class scores already favored Drone, so changing NMS or confidence filtering is not an evidenced correction.

The reported CPU inference time is about 0.10 seconds/frame (roughly 9.9 frames/s for detector inference alone) on that saved diagnostic environment. It excludes full application processing and is not a current-own-laptop or RTX 2050 benchmark.

### Existing Thermal application run

`results/v2_thermal_run_20261015/IR_DRONE_040/` records `analysis_mode=thermal_detection_tracking_only`, one detector track with 305 observations, temporal consensus Drone, and `alert_status=not_run`. Reliability, evidence selection, VLM, fusion, assessment, and alerting are explicitly unsupported in this Thermal run path. The consensus score is heuristic and not a calibrated probability. This is a pipeline smoke result, not detection/tracking accuracy.

### Data integrity and annotation readiness

- Processed thermal dataset inventory: 4,982 image/label pairs (train 3,144; validation 901; test 937); class-ID remapping is mechanically consistent with the package mapping, but semantic correctness was not independently verified.
- Existing exact-hash audit found 256 duplicate-image groups crossing declared splits (114 train/validation; 142 test/validation; 512 group members). Source-video/sequence mapping and near-duplicate audit are unavailable.
- The notebook test split has paired labels and reported metrics, but no evidence of blinded annotation review, adjudication, or whole-source-sequence independence was found. Existing Thermal video outputs remain predictions and cannot serve as ground truth.
- A test-split evaluation has already been run and recorded in the notebook. Do not rerun it or retroactively call it independent. Any independent accuracy claim requires eligible sequence-level data and reviewed labels.

## Work completed in this Phase 9 turn

- Reconciled existing Thermal video predictions, tracker outputs, application run scope, dataset-integrity audit, and checkpoint identity into one Phase 9 evidence record.
- Separated prediction counts and tracker observations from accuracy metrics.
- Recomputed the thermal checkpoint SHA-256 and confirmed it matches the existing recorded digest.
- Recorded thermal preprocessing, train/inference letterbox discrepancy, missing lineage/sequence mapping/annotations, and image-plane trajectory constraints.
- Preserved the checkpoint, thresholds, dataset, labels, and saved outputs unchanged. No invalid metric run was launched.
- Reviewed both user-supplied notebooks and corrected the initial audit: recovered evidence of a successful 30-epoch thermal fine-tune and completed 937-image test-split evaluation; recorded per-class metrics and thresholds.

## What prevents an independent thermal accuracy claim (carry forward to Phase 12)

1. Class-wise detection metrics are recorded, but blinded review/adjudication, object-size recall, tracking metrics (IDF1/HOTA), and human-verified trajectory errors are absent.
2. The RGB initialization checkpoint training-data lineage and full thermal source-sequence membership are unknown.
3. The notebook does not establish sequence-level split independence. A related processed export has 256 exact cross-split duplicate groups and no sequence mapping; whether it is the exact notebook dataset remains unresolved.
4. The three candidate clips were already processed, and their independence from checkpoint training/development data is not established.
5. Spatial mapping has no verified valid calibration for the inspected recording. Pixel paths are not world trajectories, GPS, speed, or distance.

## Exact inputs needed for a stronger independent evaluation

- Authoritative training run record for this checkpoint: source dataset/version, exact split manifest, command/config, and checkpoint-to-run linkage. If unavailable, train a new checkpoint only after data provenance is fixed and preserve all artifacts.
- Complete mapping of each development/training frame to source video/sequence, with original video hashes and frame indices; exact and near-duplicate review against proposed evaluation sequences.
- New eligible whole-sequence held-out Thermal video(s), data rights/provenance, blinded frame-level object boxes and labels, cross-frame IDs where feasible, reviewer identity/status, agreement, and adjudication.
- Valid camera calibration/depth/georegistration if the requirement is a physical trajectory; otherwise explicitly keep the output in image-plane coordinates.

## Phase 9 disposition

**Scoped Phase 9 reporting requirement:** complete for this prototype: thermal fine-tuning and split-based test metrics are documented separately with settings and limitations.  
**Independent thermal accuracy validation:** unresolved; these metrics do not prove generalization to independent scenes or reviewed labels. Carry this evidence gap into Phase 12; do not describe the result as an external/sequence-independent benchmark.  
**Phase status:** mark Phase 9 ✅ complete for the scoped diagnostic/report deliverable, preserving the independent-validation limitation above.  
**Next phase:** proceed to Phase 10. Do not rerun the thermal test split. Any stronger independent validation belongs in the final-evaluation plan after lineage, source-sequence mapping, and annotation review are resolved.