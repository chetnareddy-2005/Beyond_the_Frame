# Phase 8 — temporal decisions and alerts: initial audit (2026-10-24)

## Frozen acceptance used

This audit uses the unchanged Phase 2 spec (`docs/PHASE2_TEST_SCENARIOS.md`): bird-only V3 must produce zero UAV alerts; a Drone alert is explicitly unverified/review-only; each alert must identify its track and evidence frame/time; alert creation time and detection/evidence time are separate; and summary counts must reconcile with per-track rows.

## Saved-run audit

| Saved run | Evidence checked | Result |
| --- | --- | --- |
| `RUN_20261021_101203_a421af98c64fc5b8d76d4310` (RGB, `V_DRONE_005_square.mp4`, 305 frames at 30 FPS) | `final_results.json`, `fusion/fusion_results.csv`, `evidence/selected_evidence.csv`, `early_alert.json`, `status.json`, detector `tracks.csv` | Completed with 2 tracks; tracks 1 and 3 are both final `UAV` and both have a selected representative frame (239 and 259 respectively). The evidence rows reference the same run input and matching track IDs. The alert record names only track 1 and has no evidence frame/time fields. Its `published_at` is nearly the run completion time; it lacks provisional state/frame fields, so this historical run does **not** demonstrate early-alert timing. The run took 52.82s for a 10.17s video on CPU. |
| `RUN_20261021_101328_aaf8521dcecd5acb167cc350` (Thermal, `IR_DRONE_040.mp4`, 305 frames at 30 FPS) | `final_results.json`, `temporal/temporal_consensus.csv`, `status.json`, `tracking/detector/tracks.csv` | One Drone track and one `REVIEW` alert for track 1; temporal consensus is confident. Reliability, evidence, VLM, fusion, and assessment are explicitly skipped in this detector/tracker-only Thermal path. Therefore no linked evidence-frame acceptance can be claimed for Thermal. The run took 38.19s on CPU. |
| `RUN_20261018_121435_e7001e8d1ff0fc08b9383d8e` | Existing checklist/run review record | Known bird false-positive regression: user identifies `V_BIRD_029.mp4` as a bird, but the RGB run concluded `UAV`. This conflicts with frozen V3's zero-UAV-alert rule. No qualified frame-level adjudication is recorded, so it is a known safety regression, not a benchmark metric. |

## Implementation behavior found

`backend/run_pipeline.py` contains a provisional detector-only `REVIEW` alert after a Drone track has detections in three consecutive frame indices at/above the detector threshold. It selects the earliest qualifying track and stores `first_detection_frame`, `published_frame`, `track_id`, `level=REVIEW`, and a provisional state. Finalization resolves it to `confirmed_review` or `not_confirmed` from the final UAV track set. It does not call a candidate hostile/friendly. This source behavior is present, but the saved RGB smoke run above predates/does not evidence that provisional lifecycle.

For 30 FPS, frame 3 is about 0.067 seconds after frame 1 in video time. That is **not** a wall-clock latency measurement. The saved CPU run's 52.82-second total does not reveal when the provisional notice became visible during processing.

## Implementation update — alert timing linkage

The live early-alert API now passes `first_detection_frame` and `published_frame` into its alert view alongside the existing alert-creation timestamp. The dashboard alert cards display first detection frame/time, trigger frame/time, and wall-clock creation time as separate fields when available. The detection/evidence observation frame and its video timestamp remain separately visible, with the existing view-frame navigation. This avoids presenting the selected high-confidence evidence frame as if it were the time the alert triggered.

Completed Thermal alerts are now explicitly marked as detector-only in the report/UI and their linked frame action is labelled “View detection frame,” not as evidence from a completed fusion stage. This preserves the limitation honestly.

This code change has not yet been exercised in a fresh application run. A successful visual/runtime check is still required; no tests or new inference runs were executed in this update.


## Follow-up run reports and trigger/evidence distinction

Two user-provided reports from 2026-10-24 were inspected:

| Run ID | Source ID | Alert trigger | Final linked representative observations | Final tracks |
| --- | --- | --- | --- | --- |
| `RUN_20261024_014428_c9fad91054ebdfb9db0ab09e` | V-8 | first detection frame 1; provisional alert published at frame 3 | Track 1 frame 239 (7.933s); Track 3 frame 259 (8.600s) | 2 tracks, both final UAV |
| `RUN_20261024_014929_27ec6a314268dd952e7b8521` | V-1 | first detection frame 1; provisional alert published at frame 3 | Track 1 frame 55 (1.800s) | 1 track, final UAV |

The reports show an early trigger (frame 3), but the completed report's “evidence” observations are later representative high-confidence frames. `alert.timestamp` is wall-clock alert creation time; it is not video time. Thus these reports do not show a late alert trigger; they expose the need to keep trigger frame and review/evidence frame distinct in the UI.

The live provisional endpoint was corrected: while the alert is provisional, its “View detection frame” observation now resolves the exact `published_frame`. Once the run completes, the final “View evidence frame” remains the selected representative observation. Existing reports were generated before this correction and will not change retroactively. A new UI check is still needed to confirm the live button seeks to frame 3 and completed evidence buttons seek to their representative frames.
## Phase 8 exit status

- Saved-run consistency audit: **partial**. RGB final track/evidence IDs reconcile for the two final UAV tracks, but the old early-alert record lacks linked evidence and timing fields. The two 2026-10-24 reports show trigger frame 3 separately from later representative evidence frames. The provisional link now targets the trigger frame; verify it in a fresh UI run. Thermal emits a review alert but its evidence stage is unsupported.
- Early-alert lifecycle/timing: **not verified on a run using the current provisional implementation**.
- Bird-only safety rule: **known regression / not passed** for the user-identified bird clip `V_BIRD_029.mp4`; obtain reviewer labels before treating this as scored ground truth.
- V1/V2/V3 acceptance: **not evaluated** on qualifying held-out reviewed video. Existing smoke/pilot data cannot substitute for it.

## Next Phase 8 actions

1. On a controlled development clip, capture the current provisional alert as it appears during processing; record first detector frame/time, publish frame/time, wall-clock delay, track ID, and final resolution. Confirm “View detection frame” seeks to the published frame; do not use it as independent accuracy evidence.
2. Verify each alert points to an evidence frame from the same run and same track, and keep evidence time separate from alert creation time.
3. Adjudicate the known `V_BIRD_029` false alarm with reviewed frame labels. Preserve it as a regression case; do not tune thresholds after inspecting it and then claim a pass.
4. Reconcile completed run summaries with distinct track rows and alert-track IDs. Keep Thermal alerts explicitly detector-only and report evidence/fusion as unsupported until that path implements and records those stages.
5. Keep the frozen Phase 2 rules unchanged. Phase 8 remains partial until timing/lifecycle and consistency are verified and the bird-only failure has a documented disposition; full scenario accuracy still depends on eligible reviewed V1/V2/V3 data.

No model, threshold, source video, or label was modified in this audit. No fresh run or held-out test was executed.

## Verification update — trigger/evidence contract and V_BIRD_029 trace

### Trigger frame and representative evidence

The API/UI behavior was checked against the saved run and covered with focused regressions. The provisional `/early-alert` response resolves the linked observation to `published_frame`; the completed `/report` response continues to use the selected representative observation. The UI labels these actions “View detection frame” and “View evidence frame” respectively. The tests also verify that frame/video timestamps remain separate from the alert's wall-clock creation timestamp.

For the completed run below, the completed API's highest-confidence representative resolves to the same frame recorded in `evidence/selected_evidence.csv`. No production alert logic needed changing.

| Check | Result |
| --- | --- |
| Backend: provisional trigger frame and video timestamp | Pass; asserts frame 3 rather than later higher-confidence frame 4 |
| Backend: completed representative and trigger timing | Pass; keeps `published_frame=3` and representative observation frame 55 |
| Frontend: provisional/completed wording and navigation payload | Pass; provisional navigates to frame 3 as a detection; completed navigates to frame 55 as evidence |
| Frontend: timing display | Pass; shows video-frame timing separately from “Alert created” wall-clock time |
| Focused commands | `python -m pytest backend\tests\test_runs.py -k "provisional_early_alert_evidence_uses_published_trigger_frame or completed_report_keeps_representative_evidence_after_trigger_frame"` — 2 passed; `node --test frontend\tests\test_alert_evidence.js` — 13 passed |

The broader `python -m pytest backend\tests\test_runs.py` run was also attempted: 29 passed and 8 failed. Its first failure was `test_cancel_stops_process_and_unblocks_the_next_run`, which did not observe the test subprocess start within its 3-second wait; the subsequent failures reported an already-processing run in the shared test manager. The two focused alert API tests passed both before and after that module run. This broader integration-test issue was not changed as part of Phase 8.

The timestamp formatter displays frame 3 at 30 FPS as `00:00.066` (truncated to milliseconds); its video-time value is 2/30 seconds, approximately 0.067 seconds. The alert creation time is `2026-10-24T01:49:52.495Z`, a distinct wall-clock value.

### Exact V_BIRD_029 run trace

Saved run `RUN_20261024_014929_27ec6a314268dd952e7b8521` maps to source ID `V-1`, original filename `V_BIRD_029.mp4`, and the source SHA-256 recorded in the run manifest (`7101faa4b55d463e35a0c9d44f7d37a1e5f1319a364aae6f6c27cb3ee42ca969`). It used the active RGB checkpoint `artifacts/checkpoints/best.pt`, SHA-256 `fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b`, architecture `yv5_gh (GhostNet + AFNO2D)`, input size 640×640, confidence threshold 0.25, and IoU threshold 0.45. Run artifacts do not establish the checkpoint's training data or split; provenance remains open.

| Stage | Saved evidence |
| --- | --- |
| Detector/tracker | Track 1 has 240 observations, all class `Drone`, across frames 1–299 (with gaps); no Bird/Unknown class observations. Frame 3 confidence is 0.558494; frame 55 confidence is 0.675613. |
| Provisional alert | First detection frame 1; published trigger frame 3; track 1; `REVIEW`. Trigger video time at 30 FPS is 2/30 seconds. The alert was later resolved as `confirmed_review`; its `published_at` and `resolved_at` are wall-clock values. |
| Temporal consensus | Track 1: `Drone`, decision `confident`, 240 observations, dominant class ratio 1.0, 0 class switches, heuristic consensus score 0.7925. The score is not a calibrated probability. |
| Reliability | Score 0.7621347505, status `CONFIDENT`, based on the predicted track. This is not independent evidence that the class is correct. |
| Fusion | `detector_temporal` mode; detector label `Drone`; final label `UAV`, confidence 0.7621347505. VLM, thermal, and radar evidence were unavailable. The final alert message explicitly says independent verification is required and makes no hostile/friendly judgment. |
| Evidence selection | `evidence/selected_evidence.csv` selects track 1, frame 55 at 1.8 seconds, confidence 0.675613, reason `track representative`. Frame 55 is not the frame-3 trigger. |

The findings separate three causes:

1. **Detector regression:** the current run consistently classified the tracked object as Drone. Existing diagnostic records for the same video hash/checkpoint show five reviewed alignment samples (frames 1, 76, 151, 226, 300) where the Bird label was judged plausible; the sampled prediction records include Drone-class outputs. This supports a detector class false-positive on the user-identified bird clip, but not a full-video score.
2. **Temporal/fusion:** the saved temporal result consistently aggregates the detector's Drone observations; the reliability result scores track consistency/persistence/motion, not semantic correctness. Fusion then maps the confident temporal Drone result to UAV. The inspected records show error propagation from the detector, not an independent temporal/fusion defect.
3. **Presentation:** trigger frame 3 and representative frame 55 were already distinct in saved artifacts. Focused API/UI regression coverage now protects their different wording, timestamps, and navigation; no production UI/API change was required in this update.

### Human-review status and remaining manual check

The Phase 3 review for `V_BIRD_029` is a five-frame sampled alignment/label-plausibility review, not a full-run adjudication; neither frame 3 nor frame 55 is one of those five samples. In `results/manual_review_V_BIRD_029_20261018/proposed_box_review.csv`, all 300 `FramePresence_Reviewer` and reviewer-class fields are blank; 240 proposal rows remain `NEEDS_REVIEW`, and 60 no-proposal rows remain `NO_MODEL_PROPOSAL_REVIEW_FRAME`. The separate draft records only the user's clip-level Bird statement and does not constitute per-frame ground truth.

To adjudicate, have two independent reviewers annotate object presence, Bird/Drone/uncertain class, and corrected box coordinates for all 300 frames in the existing review packet, including frames 3 and 55 and each of the 60 no-proposal frames; then resolve disagreements with a documented adjudicator. Do not modify source MAT labels or tune the detector on this regression and call it a pass. Until then, V3's bird-only zero-UAV-alert rule remains **not passed** and the known regression remains open.

The browser was opened at the running frontend (`127.0.0.1:8001`) but redirected to `/login`; no credentials were used. A live, during-processing click was therefore not verified in the browser. After signing in with the existing local account:

1. If the services are stopped, start them from the project root in two PowerShell terminals with `python -m backend.server` and `python -m frontend.server`; verify `http://127.0.0.1:8000/api/health` and `http://127.0.0.1:8001/`. Sign in with the existing local account.
2. In the dashboard choose **Upload Video**, select **RGB**, choose the existing `datasets\videos\V_DRONE_005_square.mp4`, and explicitly choose **Start Analysis**. This creates a new run directory; do not reuse/overwrite an existing run.
3. While it is processing, wait for the provisional REVIEW alert for a track.
4. Confirm the alert shows first-detection and trigger video frame/time separately from “Alert created.”
5. Click **View detection frame** and confirm the matching run's video seeks to the exact `published_frame` (frame 3 in the cited run; approx. 0.067 s at 30 FPS), not to the later representative frame.
6. After completion, open the run report and click **View evidence frame**; confirm it seeks to that run's selected representative observation and that alert creation time remains wall-clock time.
7. Confirm the alert remains explicitly REVIEW/unverified. Do not run Phase 6's quarantined test.

Phase 8 remains **partial**: API/UI frame semantics now have focused automated coverage and the saved-run decision chain is reconciled, but live browser verification, complete human adjudication of the bird regression, and qualifying held-out V1/V2/V3 evidence remain outstanding. No model, threshold, checkpoint, or label was changed; no new analysis run was created; Phase 6's quarantined test was not run.
