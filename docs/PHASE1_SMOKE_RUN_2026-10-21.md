# Phase 1 application smoke run — 2026-10-21

## Scope and method

This was a software/runtime smoke check, not an accuracy evaluation. The
documented backend and frontend commands were started from the project root,
using their configured ports and the existing local authentication database:

```powershell
python -m backend.server
python -m frontend.server
```

Backend health returned `{"status":"ok"}` on `127.0.0.1:8000`; the dashboard
returned HTTP 200 on `127.0.0.1:8001`. Both inputs were prepared and run
sequentially through the dashboard's same-origin API proxy:

1. `POST /api/auth/login`
2. `POST /api/input/prepare` with `source=upload`, the modality, and one `file`
3. `POST /api/runs/{run_id}/start`
4. Poll `GET /api/runs/{run_id}` through terminal status; retrieve the report
   and tracks via `GET /api/runs/{run_id}/report` and `/tracks`

No packages were installed, no external service was contacted, and no source
code or detector settings were changed. The original sample videos and all
existing project data were left in place. Each upload created a new run
directory under `results/dashboard_runs/`.

## Results

| Modality | Sample input | Run ID | Final status |
|---|---|---|---|
| RGB | `datasets/videos/V_DRONE_005_square.mp4` | `RUN_20261021_101203_a421af98c64fc5b8d76d4310` | `completed` |
| Thermal | `datasets/videos/thermal/IR_DRONE_040.mp4` | `RUN_20261021_101328_aaf8521dcecd5acb167cc350` | `completed` |

### RGB

- Input: 305 frames; detection processed 305/305 and tracking processed
  305/305.
- Persisted tracking observations: 296 rows across 296 distinct frames.
- Tracks in the run report: 2.
- Phase statuses: detection `completed`; tracking `completed`; temporal
  `completed`; reliability `completed`; evidence `completed`; VLM `skipped`;
  fusion `completed`; assessment `completed`.
- Alert fields from the report: `uav_alert=true`, level `REVIEW`,
  `alert_status=null`, and `alert_track_ids=null`. This is an unverified
  pipeline cue only; it is not a label or accuracy finding.
- Trajectory artifact: available at
  `results/dashboard_runs/RUN_20261021_101203_a421af98c64fc5b8d76d4310/temporal/trajectories.json`.
- Evidence: available; 2 persisted JPG files under this run's `evidence/`
  directory.

### Thermal

- Input: 305 frames; detection and tracking each processed 305/305.
- Persisted tracking observations: 305 rows across 305 distinct frames.
- Tracks in the run report: 1.
- Phase statuses: detection `completed`; tracking `completed`; temporal
  `completed`; reliability `skipped`; evidence `skipped`; VLM `skipped`;
  fusion `skipped`; assessment `skipped`.
- Alert fields from the report: `uav_alert=true`, level `REVIEW`,
  `alert_status=detector_only`, and `alert_track_ids=["1"]`. This is an
  unverified detector-only cue, not a verified class or accuracy finding.
- Trajectory artifact: available at
  `results/dashboard_runs/RUN_20261021_101328_aaf8521dcecd5acb167cc350/temporal/trajectories.json`.
- Evidence selection/artifact: unavailable; this stage is skipped for the
  current Thermal full-video path.

## Outcome and limitations

Both runs completed and persisted their run reports and tracking outputs.
Backend logs showed no failed phase or traceback. No source-code change or
rerun was needed.

The first RGB summary-print command encountered a Windows console
`UnicodeEncodeError` while printing a progress check mark after the run had
already completed. The run itself succeeded; its results were subsequently
retrieved from the API with UTF-8 output. This was a smoke-reporting console
encoding issue, not a backend failure.

These short runs establish only that the tested software paths start, process
the sample inputs, and save run-scoped outputs. They do not establish that
predicted classes, alerts, trajectories, or tracks are correct. RGB and
Thermal remain separate capabilities: RGB ran the full configured path with
VLM skipped; Thermal ran detection, tracking, and temporal consensus only,
with evidence selection and the other listed downstream stages skipped.
Thermal intensity is not calibrated temperature. No manual browser visual
acceptance or scientific validation was performed.
