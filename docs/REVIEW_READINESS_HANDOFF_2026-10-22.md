# Beyond the Frame — review-readiness handoff

**Review date:** 2026-10-22 (updated after official dataset-source review and UB-SOD metadata acquisition)  
**Status:** The documented application startup and saved RGB/Thermal software paths are verified. Dashboard consistency and responsive/theme fixes are covered by tests and browser checks. Scientific validation and Phase 2 V1/V2/V3 exit criteria remain open. This is not an accuracy or completion claim.

The workspace does not contain a Git repository root, so this handoff records changed paths rather than a Git diff or commit reference.

## Main pieces and saved-run evidence

Run artifacts are scoped under `results/dashboard_runs/`; reports, track lists, observations, and trajectories use the same run ID. The frontend reads the run overview, tracks, fusion summary, report, and per-track details from the backend. RGB and Thermal do not share the same downstream capability claim.

| Input / run | Saved pipeline result | Dashboard verification |
|---|---|---|
| RGB — `RUN_20261021_101203_a421af98c64fc5b8d76d4310` | 305 input frames; 305 detection and tracking frames; 296 persisted observations over 296 frames; 2 tracks; 2 evidence JPGs; trajectory artifact present. Detection, tracking, temporal, reliability, evidence, fusion, assessment completed; VLM skipped. Report alert is a review cue, not verified identity or accuracy evidence. | Two track options distinguish detector history (`Drone`) from final decision (`UAV`). Summary shows 2 tracks, 2 final UAV decisions, 0 Bird, 0 other/uncertain, and 2 review alert tracks. Evidence is available on track selection. |
| Thermal — `RUN_20261021_101328_aaf8521dcecd5acb167cc350` | 305 input/detection/tracking frames; 305 persisted observations over 305 frames; 1 track; image-plane trajectory artifact present. Detection, tracking, and temporal consensus completed; reliability, evidence selection, VLM, fusion, and assessment skipped. Stored detector-only alert refers to track 1. | Track option distinguishes detector history (`Drone`) from temporal consensus (`Drone`). Summary shows 1 consensus Drone, 0 Bird, 0 other/uncertain, and 1 detector-only review alert track. Evidence selection is explicitly not run for Thermal. |

The two run artifacts and full phase outputs are preserved under the run-specific directories in `results/dashboard_runs/`. The smoke methodology, API sequence, and software-only limitations are documented in `docs/PHASE1_SMOKE_RUN_2026-10-21.md`. No extra run was created during this review; the saved runs were reused.

Thermal trajectories are detector/tracker image-plane pixel paths. They are not calibrated physical motion, distance, or temperature. Neither run establishes model accuracy, biological identity, authorization, or threat status.

## Dashboard fixes and manual checks

- Trajectory options label detector class history separately from the RGB final decision and Thermal temporal consensus.
- Summary track totals use the run track list; RGB class totals use final fusion labels, Thermal class totals use per-track temporal consensus, and alert totals count unique alert track IDs.
- Alert IDs remain represented when an alert ID has no evidence observation; duplicate evidence for one ID does not inflate the track count.
- Fixed Light-mode table headers, trajectory surface/path contrast, and narrow-layout min-content overflow. The existing black video-player surface remains an intentional playback surface.
- Before the fixes, the Thermal selector showed only `Track 1 · Drone` despite stored detector history/consensus; its summary said Thermal alerting was not run despite a detector-only REVIEW alert; a 390px viewport expanded to 495px; and the Light table header and trajectory plot retained dark styling.
- Browser checks covered the saved RGB and Thermal result routes; Dark and Light theme switching and persistence; desktop width; and a 390px viewport. The narrow document width was 390px, with wide tables retained in their own scroll containers. At desktop, the Light trajectory plot used the light gradient and contrast-adjusted raw path; the Light table header was readable.

Focused JS checks were run during implementation. The final full frontend test suite is recorded below.

## Checkpoint provenance and baseline evidence

Both active model files were hashed and compared with saved run manifests. The manifests record architecture `yv5_gh (GhostNet + AFNO2D)`, image size 640, confidence 0.25, IoU 0.45, CPU runtime, and project classes `0=Bird`, `1=Drone`.

| Checkpoint | SHA-256 | Provenance status |
|---|---|---|
| `artifacts/checkpoints/best.pt` (RGB) | `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b` | Active file and run-manifest hash agree. The complete training run/config is not established. It differs from the retained one-epoch CPU smoke-training checkpoint (`bc8ac58b1a91bd71b314b07bfa305d769c8059b41202549b3af4f6e89b2f6051`); do not attribute the active checkpoint to that run. |
| `artifacts/checkpoints/thermal_bird_drone_best.pt` (Thermal) | `ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477` | Active file and run-manifest hash agree. This does not establish independent Thermal accuracy or calibration. |

Training provenance is unresolved for **both** active checkpoints. For RGB, the local data configuration names the Roboflow `drone-vs-bird-lanzg` v2 export, but there is no verified link from that data/config to the active weights. For Thermal, saved checkpoint metadata records `epoch=-1` and null/absent training fields for dataset, model configuration, initialization weights, image size, hyperparameters, and results. The available `cpu_smoke_test*` runs do not establish the Thermal checkpoint's training run. The Thermal source data config declares `train/`, `val/`, and `test/` with `0=Drone`, `1=Bird`; that is not evidence those files trained this checkpoint. Existing local Thermal image records also report 256 exact-duplicate groups crossing declared splits. See `results/v3_thermal_provenance_request_20261016/provenance_and_review_readiness.md` and `results/v3_thermal_independent_eval_20261016/eligibility_report.md`.

SMOT4SB pilot baseline evidence is in `datasets/processed/phase2/pilot_baseline_eval/`:

- Four training-split development sequences: 772 frames, 4,337 bird boxes, 45 sequence-local IDs.
- Active RGB checkpoint at 640 input / confidence 0.25: 95 predictions (50 Bird, 45 Drone), 0 IoU≥0.50 matches. The scale diagnostics did not establish a usable match baseline.
- Oracle GT-box ByteTrack run: 2,566/4,337 boxes matched at IoU≥0.50 (59.2%), 111 observed ID switches, and 107 matched gaps. The aggregate JSON is authoritative; stale report prose was corrected from 112 to 111.
- These are pilot diagnostics on training data, not a held-out evaluation or evidence of improvement. The existing image export also has source-video IDs crossing splits; do not use its image-level test score as sequence-independent evidence.

## Dataset audit and blockers

The full local source/access review is in `docs/PHASE2_DATASET_SOURCE_AND_ACCESS_AUDIT.md`; local video mapping is in `datasets/processed/phase2/PHASE2_VIDEO_ASSIGNMENT_AND_GAPS.md`.

- **V1 WOSDETC / Drone-vs-Bird Challenge:** official repo and challenge page confirm request-gated research-use access after DUA signature. No DUA PDF or signing portal is published; request goes to `wosdetc@googlegroups.com`. The shown annotation schema is per-frame and lacks persistent IDs. No request/signature/download was performed; user must personally obtain/review the DUA and confirm completed authorized access before any acquisition.
- **V2 Purdue MultiTarget UAV:** annotation ZIP exists (955,489 bytes; SHA-256 `61581df9426c1827acfa8dfc1e4f930dfa265d9b6c2dcbfef0993f9d82f388d6`; CRC passed) and contains 50 clip annotation files with frame/object ID/box fields. Matching video, official source/license, and split remain unverified.
- **Additional V2 Anti-UAV Track 3:** official page describes 200 training and 100 test sequences with multiple UAV targets, but active official training access is not verified (ModelScope link appears commented in page source); no license was verified. Not acquired.
- **V3 SMOT4SB:** official repo provides bird MOT format and the dataset Drive is public, but Drive README identifies its current Phase 1 package as public-test and says dataset terms are in a separate license. Commercial use requires organizer contact. No current public-test package was downloaded. Existing four CRC-checked pilot sequences remain development data per their local manifest; reconcile source provenance before more acquisition. Preserve eight incomplete `.zip.part` files.
- **FBD-SV-2024:** local notes describe private/research-only sharing; no local copy. Owner access and exact terms remain unverified.
- **UB-SOD:** officially verified as public Figshare v1, CC BY 4.0. Only annotations, metadata/splits, and checksum file were downloaded to a new dated directory; archives passed provider hash/CRC checks and annotation counts matched. The 2.2 GB image archive was not downloaded. Image-only, no track IDs, no source sequence mapping; not video/MOT or a held-out tracking set. Details are in the new acquisition manifest.
- **Authorization:** no authorization registry/feed or permission subsystem exists in the architecture reviewed. No simulated MVP was added. Unknown IDs remain Unknown / Needs review, never “rogue.”

No gated requests, DUA actions, installs, training, threshold changes, or existing-data overwrites/deletions were made. The UB-SOD annotation/split/checksum files were downloaded from the official public Figshare record under CC BY 4.0; the new extraction and manifest were written to a unique dated directory.

## Verification

Commands were run from the project root:

| Command | Result |
|---|---|
| `python -m pytest` | **364 passed**, 1 `pkg_resources` deprecation warning, 0 failures; Python 3.12.9 / pytest 9.1.1. No dependency was installed. |
| `node --test frontend\tests\*.js` | **74 passed**, 0 failures. The first full attempt found eight alert-sound harness failures because it extracted `renderAlerts` without the new shared helper; the harness was corrected, its 8 focused tests passed, and the full suite then passed. |
| `node --test frontend\tests\test_dashboard_trajectory.js frontend\tests\test_alert_evidence.js frontend\tests\test_dashboard_summary.js` | **25 passed**, 0 failures, including a nested subtest. |
| `node --test frontend\tests\test_alert_sound.js` | **8 passed**, 0 failures after the harness boundary fix. |
| Browser | RGB and Thermal results, theme switching/persistence, Light contrast, 390px page width, and table-local overflow checked. |
| Server lifecycle | Backend and frontend were stopped after browser review; ports 8000 and 8001 were confirmed released. |

## Preserved cleanup candidates — review only

No files were deleted. These are not required for the verified smoke path or are locally invalid/incomplete; retain them unless the owner explicitly approves cleanup or recovery:

1. `datasets/external/drone_detection_thesis_v1.zip` (12,090,316 bytes): local ZIP check found no central directory. Existing extraction neighbors were empty, and the archive is not usable as acquired. Preserve it as source evidence; reacquire only from the official source if approved.
2. `datasets/external/drone_detection_thesis_official_retry/`: incomplete retry tree with `.git` metadata and partial archive files `0001.zip.part`, `0002.zip.part`, `0004.zip.part`–`0009.zip.part`. They are not complete verified archives; preserve in case they are resumable or needed for provenance.
3. `datasets/external/SMOT4SB_initial_train_pilot/train_archives/0001.zip.part`, `0002.zip.part`, `0004.zip.part`–`0009.zip.part`: eight incomplete transfers, distinct from the four validated archives. Preserve; do not extract or count them as acquired sequences.
4. `datasets/external/UB-SOD_figshare_v1/UB-SOD_annotations.zip` (556,429 bytes): earlier local ZIP validation failed. This original file was preserved; the separately downloaded official Figshare v1 annotation archive under `UB-SOD_figshare_v1_verified_2026-10-22/` passed integrity and content checks.

Existing checkpoints, videos, validated datasets, run artifacts, and reports were preserved.

## Remaining limitations and best next action

Open items include blinded review of the known `V_BIRD_029.mp4` false positive; verified RGB training provenance; independent held-out, sequence-level labels; lawful reviewed V1/V2 videos; an approved independent V3 holdout; standard IDF1/HOTA-style tracking evaluation; and owner-confirmed access/terms. Phase 2 and scientific validation remain open.

**Best next action:** obtain and personally review the WOSDETC DUA from the organizer at `wosdetc@googlegroups.com`. The official page does not publish the agreement document; do not download WOSDETC until you confirm that you reviewed/completed the provider's process and data is available. In parallel, request contemporaneous training/split provenance for both active checkpoints and verify Purdue and Anti-UAV video dataset access/terms.

## Startup and smoke-check commands

From the project root, use two PowerShell terminals:

```powershell
python -m backend.server
```

```powershell
python -m frontend.server
```

The configured endpoints are backend `127.0.0.1:8000` and frontend `127.0.0.1:8001`. In a third terminal, check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
(Invoke-WebRequest http://127.0.0.1:8001/).StatusCode
```

For the dashboard/API video smoke workflow, log in through the local dashboard, prepare one local video with `POST /api/input/prepare` (`source=upload`, modality, and file), start it with `POST /api/runs/{run_id}/start`, poll `GET /api/runs/{run_id}`, then inspect `GET /api/runs/{run_id}/report` and `/tracks`. The preserved RGB and Thermal sample procedures, exact IDs, and outputs are in `docs/PHASE1_SMOKE_RUN_2026-10-21.md`; do not rerun them merely to reproduce this review, because their complete artifacts already exist.
