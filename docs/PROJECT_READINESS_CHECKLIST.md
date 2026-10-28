# Beyond the Frame readiness checklist

This checklist separates software readiness from evidence that the model is scientifically reliable. A completed software run is not, by itself, an accuracy validation.
The snapshot below maps the repository evidence to the owner's current 12-phase plan; completion is always scoped to the evidence recorded here.

## Current 12-phase status snapshot (2026-10-24)

This snapshot maps the owner's 12-step plan to the evidence currently in the repository. “Complete” always means complete for the stated scope; a pilot or software implementation is not treated as scientific acceptance.

| Phase | Current status | What is complete / what remains |
| --- | --- | --- |
| **1. Baseline freeze** | ✅ Complete | Source/config snapshot, hashes, checkpoints, run references, and known issues are recorded. Git history/repository setup remains separate. |
| **2. Requirements freeze** | ✅ Complete | V1 mixed birds+drone, V2 multiple drones, V3 multiple birds, and the authorization boundary are locked in the scenario spec. |
| **3. Local data selection** | ✅ Complete for local inventory/disposition scope | Relevant local dataset groups have documented use/disposition decisions and restrictions. Held-out evaluation data and unresolved/gated sources remain unavailable; this is not a claim that they are selected evaluation assets. |
| **4. Quick audit pilot** | ✅ Complete for pilot scope; broader audit open | 27 sampled rows were reviewed and accepted by Chetna Reddy; frame/label crosswalk and evidence are recorded. Full-frame audit, corrected/reviewed GT for benchmark videos, and independent labels remain open. |
| **5. Detection baseline** | ✅ Complete for signed RGB diagnostic scope | 22 Phase 4 accepted RGB frames were paired to 59 GT boxes and evaluated with the frozen checkpoint. Per-class/per-clip metrics, confidence-sweep AP, small-object recall, frame ledgers, overlays, and hashes are saved under `datasets/processed/phase5_detection_baseline_2026-10-22/`. These are development/regression samples, not held-out accuracy. |
| **6. Targeted detector improvement** | 🟡 Validation gates pass; test evidence unresolved | User-provided same-split comparison reports all four frozen validation gates passing, and candidate hash is frozen. A quarantined-test result folder contains plots but no metric report/provenance; whether the test was already consumed is unresolved. Do not rerun or promote until the existing artifacts are traced. Candidate remains development-only. See `docs/PHASE6_CANDIDATE_EXPERIMENT_2026-10-23.md` and `docs/PHASE6_VALIDATION_GATE_RESULTS_2026-10-24.md`. |
| **7. Multi-object tracking** | ✅ Scoped phase complete; V2 acceptance pending | On four SMOT4SB training-pilot clips, detector+ByteTrack had 0/4,337 class-aware IoU≥0.50 matches. GT-box oracle ByteTrack: IDF1 49.6%, 111 ID switches, 106 defined fragments. Phase 7 diagnostic exit criteria are met; V2 multi-drone and independent-holdout evaluation remain pending. See `experiments/evaluation/PHASE7_TRACKING_DIAGNOSTIC_2026-10-23.md`. |
| **8. Temporal decisions and alerts** | 🟡 Partial; automated contract checks pass, live UI and bird review pending | Focused tests verify provisional navigation to exact `published_frame`, completed navigation to selected representative evidence, distinct detection/evidence wording, and separation of video time from wall-clock creation time (2 backend tests and 13 frontend tests passed). Own-laptop run `RUN_20261024_021333_b615ba5904f8967afa98bae6` completed 305 frames; alert trigger was frame 3 and selected evidence was frames 239 and 259. Both tracks ended as unverified UAV/REVIEW. The alert was created about 77 seconds before completion, but the report has no run-start or per-stage timing, so this is **not** a measured total inference time. Live UI navigation is still unverified. `V_BIRD_029.mp4` remains a known detector regression with blank full-frame review; V3 bird-only zero-alert acceptance is **not passed**. Held-out V1/V2/V3 evaluation remains open. See `docs/PHASE8_TEMPORAL_ALERT_AUDIT_2026-10-24.md`. |
| **9. Thermal evaluation** | ✅ Scoped diagnostic/report complete; independent validation carried to Phase 12 | User-supplied `WORKSPACE1.ipynb` records a completed 30-epoch YOLOv5 GhostNet+AFNO thermal fine-tune and test-split evaluation on 937 images: Bird P/R/AP50/AP50–95 = 0.945/0.906/0.941/0.484; Drone = 0.901/0.769/0.823/0.395; overall = 0.923/0.838/0.882/0.440 (evaluation conf=0.001, NMS IoU=0.6). These are split-based saved metrics, not fixed operating-point metrics and not an independent benchmark. Thermal app remains detector/tracker-only (305 observations, alerting not run); prior video diagnostics had 906 frames / 927 predictions, all Drone. Sequence independence, reviewed label correctness, baseline checkpoint lineage, and checkpoint digest linkage to the notebook are unresolved. Pixel paths remain image-plane only. See `docs/PHASE9_THERMAL_EVALUATION_PLAN_AND_AUDIT_2026-10-24.md`. |
| **10. Friendly/authorized drone** | 🟡 Core simulation implemented; browser acceptance and deployment configuration remain open | HQ dashboard counts approved sector rosters; HQ setup supports named areas/localities, a pinned HQ map location, protected sector deletion, drawn polygons, and radius circles (0.1–100 km, default 5 km). Registry requires head email/contact phone/address, three work-ID files, and 7-digit member IDs. Approval assigns geometry and a shared sector login; first login forces password change. Role-scoped maps, opt-in approximate operator location, document encryption/audit, and retention purge are implemented. Saved active polygon geometry is authoritative for legacy and trajectory telemetry paths; focused tests pass. Approval/rejection purges documents immediately; pending documents purge at 30 days. MIME/signature validation and purge-worker test isolation are implemented. **Open:** browser visual/interaction acceptance, confirmation that the active backend inherited its crypto keys, SMTP delivery through a safe test sink, and API acceptance requiring local loopback access. No real GPS/device authentication or detector-track linkage. See `docs/PHASE10_HQ_REGISTRY_IMPLEMENTATION_PLAN_2026-10-24.md`. |
| **11. Dashboard acceptance** | 🟡 Partial | Photo/video flows, alert/evidence navigation, summaries, theme, trajectory, and early-review UI exist. Reconcile saved-run track IDs, counts, alerts, evidence frames, and RGB/Thermal states against the acceptance checklist. |
| **12. Final evaluation and paper pack** | 🔴 Not started | Frozen held-out evaluation, repeatable final metrics, error examples, dataset/license records, and model provenance are not ready. |

### Next phase to start

Phase 9 scoped reporting is complete using the saved notebook metrics; carry independent Thermal accuracy validation into Phase 12. Do not rerun the existing thermal test split or describe it as an independent benchmark. Phase 10 core simulation is implemented, but sign-off remains partial: confirm the active backend inherited its crypto keys; exercise SMTP through a test sink; verify HQ map pin, locality display, deletion protections, and both area-allocation modes in browser/API acceptance; complete desktop/mobile role-isolation acceptance; and decide whether application-level SQLite retry/backoff is needed. Saved polygon/circle geometry is authoritative in telemetry classification; focused tests and an isolated real-Fernet flow pass. Do not call it complete yet. Phase 8 remains partially open: verify detection-frame/evidence-frame navigation in the signed-in dashboard and complete human review of V_BIRD_029.mp4; the bird-only zero-alert gate is not passed. Phase 7 V2 acceptance also remains pending. Phase 6 quarantined-test provenance is unresolved; do not rerun or promote that candidate unless prior test use and lineage are resolved.
## Phase 1 — baseline freeze (2026-10-22)

- [x] Freeze the current source/config state without Git: 274 files hashed and preserved in `docs/baseline_freeze_2026-10-22/source_snapshot.zip`; the machine-readable per-file hashes and aggregate digest are in `docs/baseline_freeze_2026-10-22/baseline_manifest.json`.
- [x] Record active RGB/Thermal checkpoint hashes, relevant dataset/project manifest hashes, both completed RGB/Thermal smoke-run references, and known scientific/software limitations.
- [x] Verify every archived source/config entry against the manifest hash.

Phase 1 baseline freeze is complete. This establishes a reproducible starting point; it does not validate model accuracy. See `docs/PHASE1_BASELINE_FREEZE_2026-10-22.md`.

## Software and pipeline behavior

- [x] **Automated checks match the current product contract.** Navigation, VLM pending-state, and report assertions were updated; all Python tests pass.
- [x] **Evidence-navigation tests model browser seeking.** Added a minimal `window` timer and deterministic seek-event mock; all 68 frontend JavaScript tests pass.
- [x] **VLM behavior is documented accurately.** The default RGB pipeline skips automatic SmolVLM inference and explicitly marks detector/temporal labels unverified. The stale README claim was corrected. Enabling VLM remains a separate product decision.
- [x] **RGB end-to-end smoke test.** The persisted 300-frame RGB run completed detection, tracking, temporal analysis, reliability, fusion, assessment, and reporting. VLM was skipped. The known bird false positive is tracked separately below.
- [x] **Thermal alerts are detector-only review alerts.** A thermal track labeled Drone/UAV now creates a REVIEW alert with a representative frame. It is explicitly unverified; reliability, evidence selection, VLM, fusion, and assessment remain out of scope.
- [x] **Radar scope is accurately represented.** Generic Radar upload is unsupported until an input format and parser are defined.
- [x] **Theme acceptance.** Dark/Light palettes and saved preference were checked in a browser at desktop and 390px widths; the document fits the viewport, wide tables remain locally scrollable, and Light mode keeps trajectory plots and table headers readable.

## Scientific and data blockers

- [ ] **Review the known bird false positive.** In run `RUN_20261018_121435_e7001e8d1ff0fc08b9383d8e`, `V_BIRD_029.mp4` was labeled Drone/UAV. A qualified reviewer must confirm the clip and annotate representative/full frames before model changes are selected.
- [x] **VLM runtime policy for the current prototype.** Automatic VLM inference stays disabled for default RGB runs, so it cannot add delay to confidently labeled Drone tracks. This also means ambiguous tracks receive no VLM suggestion; do not enable that path until its outputs are independently validated. The known bird error cannot be repaired by trajectory consensus or an unvalidated VLM.
- [ ] **Evaluate bird-versus-drone performance on reviewed clips.** Report per-class precision/recall and false-positive/false-negative examples; do not tune on the held-out evaluation set.
- [ ] **Establish training data and checkpoint provenance.** Preserve the exact dataset version/split manifest, training code/configuration, run artifacts, and checkpoint hash together.
- [ ] **Establish sequence-level split independence.** Verify source-video IDs for every frame and keep whole sequences within one split. Exact-image deduplication is not sufficient.
- [ ] **Run a blinded independent evaluation.** Use new held-out thermal/RGB sequences with reviewer-created labels and frozen checkpoint/inference settings. Report limitations and reviewer agreement.

## Phase 2 — requirements freeze and test scenarios (2026-10-22)

- [x] V1 mixed birds+drone, V2 multiple drones, and V3 multiple birds now have explicit expected detection, track-ID, alert, and run-summary behavior.
- [x] Small/far targets, temporary occlusion, thermal limits, and unknown/authorization cases have expected behavior and evaluation metrics defined.
- [x] Inventory local candidate videos and map them to V1/V2/V3; document gaps and known use/label restrictions. See `datasets/processed/phase2/PHASE2_VIDEO_ASSIGNMENT_AND_GAPS.md`.
- [x] Lock scenario acceptance criteria, alert and summary semantics, metrics, and the visual-class/authorization boundary before model evaluation.

Phase 2 requirements freeze is complete. Its locked specification is `docs/PHASE2_TEST_SCENARIOS.md`; the immutable copy and SHA-256 record are under `docs/phase2_requirements_freeze_2026-10-22/`. Eligible held-out V1/V2 media, reviewed labels, and a sequence-level test split are data-readiness work for later phases; until then those scenarios remain unevaluated. See `datasets/processed/phase2/PHASE2_VIDEO_ASSIGNMENT_AND_GAPS.md` for current local data limits.

## Phase 3 — dataset selection and readiness disposition (2026-10-22)

- [x] Assign every relevant local dataset group a disposition: development, software smoke, conditional candidate, metadata-only, hold, or out of scope.
- [x] State what each dataset may support and why it cannot support independent evaluation where applicable.
- [x] Identify V1/V2/V3 and Thermal readiness from local files without treating missing/gated data as available.
- [x] Compare every SMOT4SB pilot source JPG against its derived MP4 frame; validate frame ordering, decode success, GT frame ranges, and box bounds. See `docs/PHASE3_VIDEO_LABEL_ALIGNMENT_AUDIT_2026-10-22.md`.
- [x] Resolve SMOT4SB CSV frame gaps against the original annotation JSON; confirmed they are frames explicitly present with no object annotations, and source JSON box/track counts match all four GT CSVs.
- [x] Decode and visually sample the MATLAB annotations for `V_BIRD_029`, `V_DRONE_007`, `V_DRONE_024`, `IR_DRONE_040`, and `IR_DRONE_041`; record the +1 RGB label-slot mapping and direct Thermal mapping without modifying source labels. `IR_BIRD_007` has zero boxes and is excluded from scoring.
- [x] Recheck the six local RGB/Thermal thesis clips' frame counts, FPS, dimensions, and file presence; other length-only matches remain candidates, not verified alignment.
- [x] Search local records and exact online filename/ID results for the two flock clips' source/license; source remains unverified.
- [x] Record the current local-data dispositions and explicitly separate available development/smoke data from evaluation-ready data.

**Phase 3 status: complete for the agreed local-search/disposition scope.** The local inventory, candidate review, alignment audit, allowed-use decisions, and readiness gaps are documented. This does **not** mean the project has evaluation-ready datasets: V1, V2, an independent V3 holdout, and Thermal evaluation remain unavailable/unapproved and are explicitly deferred to **Future Scope**. The proposed temporary +1 RGB diagnostic-label export and detector rerun are also deferred; no source labels were changed. Full decisions and reasons: `docs/PHASE3_LOCAL_DATASET_DISPOSITION_2026-10-22.md`. No data was moved or deleted, and no gated access was attempted.

### Deferred to Future Scope — data readiness and model evaluation

- [ ] Obtain/approve a V1 mixed bird+drone video with verified terms and reviewed per-frame labels.
- [ ] Obtain/approve a V2 multi-drone video with verified terms, persistent identities, and reviewed labels.
- [ ] Establish an independent V3 bird-flock holdout with verified rights and reviewed labels; the four SMOT4SB sequences remain development-only.
- [ ] Establish Thermal checkpoint lineage and a sequence-independent, human-labeled Thermal video holdout.
- [ ] If useful for debugging, generate temporary diagnostic labels with the sampled +1 mapping for the three known RGB clips and rerun diagnostics. This is not ground-truth certification or blind evaluation.

## Phase 4 — quick audit pilot (started 2026-10-22)

- [x] Select a small representative pilot: one SMOT4SB bird-flock development clip, one RGB Bird diagnostic, one RGB Drone diagnostic, and one Thermal Drone diagnostic.
- [x] Reuse and hash-link existing frame-count, sampled alignment, and overlay evidence; preserve original media/labels and do not train or tune on the pilot.
- [x] Create a 27-row manual review queue with source frame, annotation frame/slot, overlay reference, and reviewer decision fields.
- [x] Visually inspect the 27 sampled overlays; save decisions/limitations to `datasets/processed/phase4_quick_audit/phase4_sample_review.csv`. Tiny/blurred SMOT4SB bird examples remain explicitly ambiguous.
- [x] Write the frame-to-label crosswalk and copy 49 selected SMOT4SB source GT boxes unchanged to a separate sampled audit export; verify source and evidence hashes using `tools/phase4_quick_audit.py`.
- [x] Reviewer Chetna Reddy accepted all 27 sampled rows on 2026-10-22; record is `datasets/processed/phase4_quick_audit/phase4_human_review_signoff.csv`.
- [x] Close the scoped Phase 4 quick-audit pilot after recording decisions and limitations. V1 mixed-scene, V2 multi-drone, independent V3 holdout, and independent Thermal evaluation remain unevaluated.

**Phase 4 status: complete for the scoped quick-audit pilot.** The repeatable audit outputs, human decisions, mappings, unchanged sampled-label export, and validation record are in `datasets/processed/phase4_quick_audit/`. No provider labels were corrected. This does not mean V1/V2/independent V3/Thermal evaluation is complete. See `PHASE4_QUICK_AUDIT_PILOT.md` for scope and limitations.

## Phase 5 — detection baseline (2026-10-22)

- [x] Run the frozen active RGB checkpoint on exactly 22 accepted, hash-linked Phase 4 RGB frames; save operational predictions/confidences and a separate AP score sweep.
- [x] Pair the frames with 59 GT boxes (54 Bird, 5 Drone) using signed Phase 4 SMOT4SB CSV boxes and read-only decoded MATLAB labels for `V_BIRD_029`/`V_DRONE_007`.
- [x] Record per-class and per-clip TP/FP/FN, precision/recall/F1 at IoU 0.50 and 0.25; add class-confusion counts, 101-point AP, small-object recall, frame overlays, and source/output hashes.
- [x] Confirm source data/checkpoint were not changed and no training/tuning was performed; document the Bird misses and false Drone labels.

**Phase 5 status: complete for the available signed RGB diagnostic baseline.** The SMOT4SB frames are from its training split, and the RGB thesis clips are known regression clips reviewed at five frames each. This is not a held-out benchmark or a generalization claim. See `docs/PHASE5_DETECTION_BASELINE_CHECKPOINT_2026-10-22.md` and `datasets/processed/phase5_detection_baseline_2026-10-22/PHASE5_DETECTION_BASELINE_REPORT.md` for metrics, limits, and exact artifacts.

## Phase 6 — targeted detector improvement (started 2026-10-22; outcome recorded 2026-10-23)

- [x] Freeze the Phase 5 comparator and verify active RGB checkpoint hash against the frozen Phase 1/5 record.
- [x] Audit every local `drone_vs_bird` image/label pair, class IDs, normalized YOLO coordinates, exact image hashes, and recoverable source-video groups without modifying source data.
- [x] Add a reproducible fail-closed gate that refuses to authorize training or checkpoint promotion while provenance or sequence independence is unresolved.
- [ ] Recover the active RGB checkpoint's exact training data manifest/membership and determine whether it has seen the candidate validation videos. Embedded metadata suggests epoch 76/200 and scratch initialization, but source lineage is still unresolved.
- [x] Obtain an eligible, license-cleared Bird+Drone corpus with whole-source train/validation/quarantined-test videos; quarantine Phase 5 examples. See `datasets/processed/phase6_candidate_dataset_2026-10-22/dataset_summary.json`.
- [x] Freeze candidate training settings and the supplementary Phase 6 improvement rule before the valid training run in `docs/PHASE6_EXPERIMENT_PREREGISTRATION_2026-10-22.md`.
- [x] Finish the transferred run to its 20-epoch cap and retain Colab run artifacts. This was a resume from an epoch-17 checkpoint, not a new scratch initialization.
- [x] Evaluate the uploaded start checkpoint and resulting checkpoint on the same 685-frame staged validation split; compare their effective tensors and save hashes/output paths.
- [x] Evaluate the frozen active project checkpoint (`fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`) and candidate (`69907c0e5d096a3486a398f0847a53e45f4a8a3af10f969b5aa97d34187ba987`) on the same 685-frame validation split; record per-class AP50/AP50:95, fixed operating-point P/R, and source-resolution size-bin recall. Results were supplied from Colab and transcribed in the Phase 6 addendum.
- [x] Apply the predeclared validation gates to that comparison. All four pass; see the explicit deltas and threshold semantics in `docs/PHASE6_VALIDATION_GATE_RESULTS_2026-10-24.md`.
- [ ] Resolve whether the existing `phase6_quarantined_test_once` artifacts came from this candidate and the quarantined split; plots alone do not establish run identity or exact test metrics. Do not rerun until prior-use status is known.
- [ ] Promote candidate. Promotion remains blocked by unresolved test provenance, active-checkpoint training lineage, and unknown cross-scene independence; the candidate remains development-only.

**Phase 6 status: validation gates pass; full phase closure and promotion remain pending.** User-provided Colab results compare the frozen active checkpoint and candidate on the same validation split; all four predeclared gates pass. The candidate hash is recorded. A test-results directory already contains plots, but it has no machine-readable metrics or provenance, so we cannot claim either that the test was untouched or that those plots represent the frozen candidate's one-time test. Do not repeat the test run. Active-checkpoint training lineage and cross-scene independence also remain unresolved. Results are development-only. See `docs/PHASE6_VALIDATION_GATE_RESULTS_2026-10-24.md` and the historical Colab execution record in `docs/PHASE6_CANDIDATE_EXPERIMENT_2026-10-23.md`.

## Phase 7 — multi-object tracking (development diagnostic outcome 2026-10-23)

- [x] Freeze the four locally available SMOT4SB pilot sequences and use the same 772-frame development-only scope for the detector-driven and GT-box oracle comparisons.
- [x] Separate detector failure from tracker association: saved detector+ByteTrack summary has 0/4,337 class-aware IoU≥0.50 GT matches, 4,337 GT misses, 95 unmatched detector outputs, 3 predicted IDs, and no GT-linked track frames.
- [x] Re-run GT-box ByteTrack oracle using the project `TrackingService`; save frame-level tracker ledger, per-sequence metrics, aggregate IDF1, ID switches, and fragments. Reproduction command: `python tools/phase7_oracle_tracking_metrics.py`.
- [x] Report oracle IDF1=49.6%, ID switches=111, fragments=106, and GT observation coverage=59.2%, with metric definitions and per-sequence values.
- [x] Record scope and limits: the four clips belong to the SMOT4SB initial training partition and are bird-only development clips, not an independent test set.
- [ ] V2 multi-drone scenario acceptance; eligible video with reviewed persistent IDs is not locally available. Keep this pending rather than substituting bird-only footage.

**Phase 7 status: complete for the agreed diagnostic scope; V2 acceptance pending.** The detector+tracker output is currently dominated by zero detector/GT overlap, while the GT-box oracle exposes separate association errors. No tracker tuning was done on the same clips. See `experiments/evaluation/PHASE7_TRACKING_DIAGNOSTIC_2026-10-23.md` and `datasets/processed/phase2/pilot_baseline_eval/phase7_oracle_tracking_final/`.

### Dataset acquisition update — 2026-10-22

- [x] Public UB-SOD Figshare record and license verified. Acquired only its official annotations, split/metadata, and checksum files under `datasets/external/UB-SOD_figshare_v1_verified_2026-10-22/`; provider checksums, archive CRC, label structures, image split lists, and count consistency passed. This is image-only detector supplement data, not video/MOT evidence. Sequence-level independence is unknown and the 2.2 GB image archive was not acquired.
- [x] CSIR-CSIO YOLOBirDrone sample ZIP staged under `datasets/external/YOLOBirDrone_CSIR_sample_2026-10-22/`; 10/10 image-label pairs, 3840×2160 dimensions, YOLO row shape, and normalized coordinates passed checks (33 boxes: IDs 0=15, 1=18). Manual overlay review found class 0=Drone and class 1=Bird consistently across all 10 images; this is a project-reviewed inference because the sample has no official class map. Use only as a small image-level development supplement, never as video/tracking validation. Redistribution is prohibited by provider terms.
- [x] Inspected the downloaded `MVA2025-SMOT4SB-main.zip`: it contains baseline code and an empty datasets placeholder; its README points to a separate official dataset download. The project already has four verified training-split sequences (0003, 0011, 0034, 0040) for development, so no duplicate or challenge-test videos were downloaded in this step.
- [ ] V1 WOSDETC remains gated: official repo says organizers provide a DUA after request and data is for research use. No agreement or download was obtained. User must contact/review/sign if they choose to proceed; then explicitly confirm completed access before authorized acquisition.
- [ ] V2 Purdue annotations exist locally but the matching videos, official access/license record, and split are unresolved. Anti-UAV Track 3 has no verified active official download/license in this review.
- [ ] Verify the existing SMOT4SB training-pilot provenance against the current public Drive release. The current Drive README identifies its contents as Phase 1 public test; do not acquire or tune on that public-test package.

Full candidate records and the new UB-SOD manifest are in `docs/PHASE2_DATASET_SOURCE_AND_ACCESS_AUDIT.md` and `datasets/external/UB-SOD_figshare_v1_verified_2026-10-22/acquisition_manifest.json`.

## Audit update — 2026-10-18

The persisted `V_BIRD_029.mp4` run is an **RGB** run using `artifacts/checkpoints/best.pt` (SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`). It did not use the thermal candidate checkpoint (`artifacts/checkpoints/thermal_bird_drone_best.pt`, SHA-256 `ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477`). The run manifest's RGB checkpoint and source-video hashes match the local files.

The run processed 300 frames and recorded 240 Drone observations for one track. Temporal consensus was `Drone / confident`, with 32 missed-frame gaps; the final label was `UAV` in `detector_temporal` mode. **VLM was skipped** because no evidence frames were selected; no VLM result row exists. The trajectory and consensus scores are heuristic model outputs, not species ground truth. Therefore this run documents an RGB-model error report but does not evaluate the thermal candidate.

Blinded review materials are in `results/manual_review_V_BIRD_029_20261018/` and `results/manual_review_V_BIRD_029_20261018.zip`. They contain all 300 decoded frames, ten contact sheets, and a blank frame/object annotation template; no model predictions or boxes are included. The review checkbox stays open until human labels are returned. Do not change class thresholds, retrain, or claim accuracy based on this run alone.

## Completion rule

The project may be described as a working prototype when the RGB smoke test and product-contract checks pass, with Thermal and Radar limitations clearly disclosed. Do not describe model accuracy as validated until the scientific/data checklist is complete.

## Audit update — 2026-10-20 (RGB source-sequence split)

The local `datasets/drone_vs_bird` Roboflow v2 export contains 2,920 images (train 2,044, valid 584, test 292) and no source videos. Filename parsing recovered explicit source IDs for 69 images (`video14`–`video18`). All five IDs cross splits: video14/video15 span train+valid; video16/video17/video18 span train+valid+test. Six test images from video16–video18 share source video IDs with train/validation images. Therefore the existing RGB image-test metrics are not fully sequence-independent.

An ORB/RANSAC screen flagged 194/292 test images as same-scene candidates; this is not a confirmed leak count because distinct recordings from a fixed camera scene can match. Eight sample pairs were visually reviewed and show the same scene with the target at nearby positions. Do not automatically remove or move these images. The remaining 2,851 images have no recoverable source-video ID in the local export; sequence independence remains unknown. Detailed inventory and candidate table are in `results/v3_final_handoff_20261020/`.

The user identifies `V_BIRD_029.mp4` as a bird; frame-level blind labels have not been supplied. A clean held-out evaluation, full source-sequence grouping, theme browser acceptance, and thermal checkpoint provenance/independent evaluation remain open. Software readiness is distinct from scientific validation. See `results/v3_final_handoff_20261020/FINAL_HANDOFF.md` for the owner's final local smoke-run steps and limitations.

## Execution update — 2026-10-21 (multi-object, fusion, and data audit)

### Software checkpoint

The focused regression suite for tracking, mixed-class temporal fusion, and final UAV-alert decisions passes: `41 passed, 4 subtests passed`. Existing tests cover multiple simultaneous tracks, temporary missed detections, per-run tracker isolation, mixed Bird/Drone observations within one track, abstention for uncertain tracks, and preventing abstained tracks from raising a UAV alert. No code change was made in this checkpoint because these behaviors already pass their focused checks. This verifies software contracts only; it does not establish that the detector labels real clips correctly.

### Dataset search result

- **Drone-vs-Bird Challenge** is the strongest video-oriented candidate for the central bird-versus-drone problem. Its [public challenge repository](https://github.com/wanghaol23/challenge-data) describes per-frame, multi-object annotations, but access requires a data-use agreement; request and review that agreement before adopting the data. The [official challenge page](https://anti-uav.github.io/dataset/) also documents multi-UAV tracking sequences, which are useful for tracking tests but are not a bird-versus-drone classifier dataset.
- **UB-SOD** is a public CC BY 4.0 image-only detection supplement; official Figshare metadata and small annotation/split archives have now been verified locally. Its image-only split does not establish sequence independence or validate video tracking. See the 2026-10-22 dataset acquisition audit.
- **CSIR-CSIO YOLOBirDrone** is a relevant India-based candidate described on the [CSIR-CSIO dataset page](https://www.csio.res.in/CommonNew.php?ds=401&page=1) as mixed bird/drone detection imagery. Access terms restrict redistribution, and access is by request; confirm the terms before using it in this project or a paper.
- **Anti-UAV Track 3** is a candidate for multiple-UAV tracking and difficult thermal/RGB target tracking. Its [official challenge page](https://anti-uav.github.io/dataset/) says it does not provide the bird-vs-drone distinction needed for the main classifier, so keep it as a supplemental tracking evaluation set.
- The existing local Roboflow v2 dataset remains useful for a provisional detector baseline, but its known source-video IDs cross train/validation/test splits; do not use its existing test metrics as an independent sequence-level result.
- `datasets/external/drone_detection_thesis_v1.zip` cannot currently be validated or extracted: it has no ZIP central directory, and its two neighboring extraction folders are empty. Preserve the archive unchanged and reacquire it from its source if needed; do not train from it in this state.

### Identity/authorization boundary

None of these visual datasets establishes whether a drone is friendly/authorized. Keep authorization separate from Bird/Drone detection and require a trusted ID plus valid time/area permission data. Until a real authorized feed or receiver is available, only simulated authorization cases can be evaluated; missing ID must remain `Unknown / Needs review`, not be called rogue.

### Next work

1. Add the Drone-vs-Bird Challenge data request to the project acquisition checklist; do not send the request without the owner's explicit approval.
2. Verify formal release metadata, allowed use, split design, and annotation quality for the highest-priority candidate datasets.
3. Obtain blind frame/object labels for held-out project clips and create a video-level split before any detector retraining or threshold tuning.
4. After validated video data is available, run the three acceptance scenarios: mixed birds+drone, multiple drones, and multiple birds; report per-class detection, track ID switches, and alert/summary agreement.
5. Implement simulated friendly/authorization state checks only after the visual and tracking evaluation set is frozen.

## Review update — 2026-10-22 (saved runs, dashboard, provenance, and data)

- [x] **RGB and Thermal application paths.** The sequential Phase 1 smoke runs `RUN_20261021_101203_a421af98c64fc5b8d76d4310` (RGB) and `RUN_20261021_101328_aaf8521dcecd5acb167cc350` (Thermal) completed and saved outputs. Their detailed phase, frame, track, alert, evidence, and trajectory records are in `docs/PHASE1_SMOKE_RUN_2026-10-21.md`. These runs are software checks, not accuracy evidence.
- [x] **Run-scoped dashboard labels and summaries.** RGB trajectory choices distinguish detector history from final decision; Thermal choices distinguish detector history from temporal consensus. RGB final-label counts and Thermal consensus counts use the per-run track set; alert counts use unique alert track IDs. Browser checks confirmed RGB and Thermal values against their saved run reports.
- [x] **Thermal trajectory display.** The saved Thermal run has one track with 305 observations and an image-plane pixel trajectory. Dashboard wording identifies detector-only review alerts and the skipped Thermal evidence-selection stage. Pixel movement is not physical-world motion; Thermal intensity is not calibrated temperature.
- [x] **Browser theme and responsive checks.** Dark and Light themes, persistence after reload, and desktop/narrow layouts were checked. Light-mode table headers and trajectory surfaces/paths are readable; at 390px the document is 390px wide and wide tables scroll within their containers.
- [ ] **RGB and Thermal checkpoint provenance.** Active checkpoint hashes match their run manifests, but neither active checkpoint has complete verified training lineage. The RGB weights are not linked to the declared Roboflow data/config. Thermal metadata has `epoch=-1` and null/absent training fields; the local Thermal dataset config and CPU smoke runs do not establish its training data or split. See `results/v3_thermal_provenance_request_20261016/provenance_and_review_readiness.md`.
- [ ] **Independent detector/tracker validation.** The four SMOT4SB training sequences remain development data. The active RGB checkpoint had 0/4,337 IoU≥0.50 bird-box matches at the default 640/0.25 baseline. The GT-box oracle tracker JSON totals 2,566/4,337 matches, **111** observed ID switches, and 107 matched gaps; the earlier prose saying 112 switches was incorrect. Neither result is a leakage-safe held-out evaluation.
- [ ] **V1/V2/V3 acceptance and authorization.** No eligible reviewed V1 mixed clip or V2 multi-drone video is locally available. WOSDETC requires the user to obtain/review/sign the organizer's DUA before access; no request or signing occurred. Purdue annotations lack a verified matching video, license, and split. Anti-UAV Track 3 has no verified active training download/license. SMOT4SB's current public Drive package is public-test and was not acquired. UB-SOD annotations/splits were verified as CC BY 4.0 but are still-image only. No authorization registry/feed subsystem exists; no simulated authorization MVP was added.

See `docs/PHASE2_DATASET_SOURCE_AND_ACCESS_AUDIT.md` and `docs/REVIEW_READINESS_HANDOFF_2026-10-22.md` for the local evidence inventory, acquisition blockers, commands, verification results, and next action. Phase 2 and scientific validation remain open.
