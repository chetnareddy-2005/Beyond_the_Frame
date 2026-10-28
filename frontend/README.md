# Beyond the Frame dynamic dashboard

The dashboard is a run-scoped interface for uploading a video, following the
backend processing status, and reviewing only the results belonging to that
run. It does not load the frozen Caltech result set as the result of a new
upload. The historical read-only backend endpoints remain available but are
not used by this dashboard flow.

## Run locally

Open two PowerShell terminals from the project root.

Start the existing backend:

```powershell
$env:BTF_AUTH_USERNAME = "demo-admin"
$env:BTF_AUTH_PASSWORD = "replace-me-with-a-strong-password"
python -m backend.server
```

Choose a unique username and a strong password for your installation. These
environment variables bootstrap the first account when the authentication
database does not exist; keep the same values in this terminal when restarting
the backend.

Start the dashboard static server and same-origin API proxy:

```powershell
python -m frontend.server
```

Open <http://127.0.0.1:8001>. The proxy forwards supported input preparation,
frame acquisition, detection and tracking, run-start, authentication, and `/api/...` read requests to the backend
at `http://127.0.0.1:8000`. The opening screen leads to login; protected
dashboard APIs require the backend's HTTP-only session cookie. To use a
different backend origin, set
`BTF_API_ORIGIN` or pass `--api-origin`.

## Application pages

The authenticated dashboard shell uses same-tab URL navigation:

- `/dashboard` — active run monitoring and the primary video player.
- `/live-cctv` — existing RTSP connection, test/start/stop, and live-frame controls.
- `/video-analysis` — upload, modality selection, input preparation, Start Analysis, and processing status.
- `/results` — completed stored-run library; selecting a run opens `/results/<run_id>`.
- `/history` — analysis history and persisted session history.
- `/alerts` — run selector and alerts loaded from each selected run's stored report.
- `/reports` — completed stored reports; selecting one opens `/reports/<run_id>`.
- `/settings` — account and backend status. This dashboard currently has no editable model/source preference controls.

The static server serves the existing authenticated app shell for these routes.
Stored result and report routes use the existing owner-checked read APIs; they
do not start detection, VLM, or another analysis run. Browser Back/Forward
updates the active page and sidebar state in the same tab.

If the default backend port is already occupied by an older read-only server,
start the current backend and dashboard on alternate ports:

```powershell
python -m backend.server --port 8003
python -m frontend.server --port 8004 --api-origin http://127.0.0.1:8003
```

## Input selection and dashboard behavior

- Choose between live CCTV and Upload Video as separate input sources.
- CCTV accepts an RTSP URL with connection-test and explicit start/stop controls.
- Starting CCTV exposes a session-bound `READY_FOR_PHASE_3` live reference.
  The backend handoff and JPEG current-frame endpoints are proxied through the
  dashboard; no analysis stage is started by this handoff.
- Phase 3 provides an authenticated one-frame-at-a-time acquisition control
  for the active CCTV reference or prepared RGB/Thermal run. It displays
  validation/preprocessing state, available frame index, dimensions, and a
  preview; stop/EOF releases upload readers. Radar reports its unsupported
  input format.
- Phase 4 provides a separate single-frame detector action for RGB and
  Thermal uploads and visible-light CCTV. Thermal uses the separate trained
  Bird=0, Drone=1 GhostNet + AFNO checkpoint and displays confidence results
  with an annotated frame. Radar remains unsupported; this action does not
  start tracking or any later analysis stage.
- Phase 5 provides a separate one-frame-at-a-time tracking action. It reuses
  the Phase 4 detector and `supervision.ByteTrack`, displays active track IDs,
  classes, confidence, and bounding boxes, and retains tracker state for the
  same RGB or Thermal run or live CCTV reference. Reset, stop, source changes,
  end-of-video, logout, and backend shutdown clear the in-memory tracker.
  Single-frame Thermal tracking does not start the interactive Phase 6
  temporal endpoint; this action does not start reliability, evidence, VLM,
  fusion, or later analysis stages. Full-video runs have a separate offline
  track-consensus stage.
- Upload Video requires choosing RGB, Thermal, or Radar first. RGB and Thermal
  accept the existing MP4, AVI, MOV, and MKV video types; Radar remains
  unavailable until the project defines a supported upload format.
- Prepare Input validates and stores the upload without starting detection or
  downstream analysis. Start Analysis invokes the existing full-video RGB
  pipeline for prepared RGB uploads and a sequential full-video detection and
  tracking run for prepared Thermal uploads. Thermal uses the existing
  `ThermalAdapter` preprocessing and Bird=0, Drone=1 production detector. It
  stores run-scoped detector/tracker observations and reports actual backend
  progress and terminal status.
- Thermal runs perform sequential detection/tracking followed by offline,
  track-level temporal consensus. Reliability scoring, evidence selection,
  VLM verification, fusion, and assessment are marked not run. A completed
  Thermal report may include a detector-only REVIEW cue for predicted Drone
  tracks; its representative frame references that run's stored detector
  observations and is explicitly unverified. Consensus uses the completed
  track and may include later frames, so it is not a causal live decision; it
  remains a heuristic, not an accuracy measure. Predicted classes are not
  independent ground truth, and detector confidence is not calibrated
  probability.
- Thermal track detail presents a versioned Track Evidence Ledger derived
  from the run-scoped detector observations and checkpoint manifest. It shows
  observed class changes and intervals without track observations, labels
  confidence as raw, and reports the temporal decision with reason codes and
  references to supporting/contradictory predicted frames. These references
  are model outputs, not human-verified labels. Image-quality indicators and
  independent frame evidence remain unavailable; older runs without a saved
  consensus artifact may show a clearly identified derived-on-read result.
- Keeps the selected local upload in a persistent browser video player while
  the compact pipeline selection changes the phase details below it.
- After a run completes, video playback and seeking load tracked observations
  from the run-scoped `tracking/detector/tracks.csv` artifact through an
  authenticated read-only frame endpoint. Detector CSV frames are one-based:
  the player maps `floor(currentTime * fps) + 1` to the stored/displayed frame.
  The player timestamp remains authoritative; frame mapping may be approximate
  for variable-frame-rate video.
- Phase 3–6 single-frame controls remain available only as explicitly labeled
  optional debug tools; they are not the normal uploaded-video workflow.
- RGB reliability, evidence, VLM, and fusion phase details reuse the selected
  run-scoped track-detail API; assessment and completion views use the existing
  overview, fusion summary, and final report. Thermal downstream stages are
  shown as not run, rather than as negative or synthesized results.
- Distinguishes original video, acquired prepared-frame, and model-input
  resolutions; model-input dimensions remain unavailable unless an existing
  response reports them.
- Accepts MP4, AVI, MOV, and MKV video files up to 250 MiB.
- Requires a valid backend session before showing dashboard functionality.
- Creates and follows a backend-generated run ID; the current run ID is saved
  in browser storage so it can be restored after reload. This value is not an
  authentication token or session.
- Logout invalidates the server-side session while preserving the saved run ID.
- Displays actual backend stage, status, and progress. No frame-level progress
  is fabricated.
- Loads overview, tracks, and fusion summary only from the current run.
- Combines active evidence cards and the paginated recent-alert table in one
  dashboard Alerts card; evidence actions continue to seek the exact stored
  frame for the selected run.
- Shows a compact selectable track-trajectory preview from the existing
  run-scoped track-detail response. Expanding the preview reuses the stored
  raw and smoothed image-plane centers without starting analysis or inference.
- Loads authenticated run history from the existing run-list API and paginates
  its real records without introducing a separate history service.
- Results lists completed runs with read-only stored-result viewing and an
  explicit fresh re-analysis action. Matching uploads can surface a prior
  completed run by source-video hash; viewing it does not start inference.
- Failed/cancelled runs offer a checkpoint check. Resume creates a separate
  run only when the completed detector/tracker artifact and current source and
  configuration validate; temporal and later stages are rerun. Otherwise the
  dashboard directs the user to fresh analysis.
- History separates Analysis History from persisted Session History. Analysis
  duration measures `started_at` through the terminal time, excluding upload
  preparation.
- Opening a stored run restores its source video through the authenticated
  run-scoped video endpoint. Browser seeking uses HTTP byte ranges and the
  existing read-only frame lookup; it never triggers detection.
- Provides adjustable track pagination, track-ID search, reliability and class
  filters, and run-scoped track detail, evidence, VLM, modalities, and fusion.
- Displays the run's image-plane motion state, quality, trajectory plot,
  pixel-based speed/heading/acceleration, and continuity metrics when present.
- Draws an optional selected-track image-plane trail over the persistent player
  using that run's stored detector observations; no 3D or world coordinates are
  implied.
- Displays the backend's manual-review assessment without inventing decision
  thresholds. A new HIGH or REVIEW run alert plays one short notification sound
  when the browser permits audio playback; if autoplay is blocked, the sound
  retries on the next user gesture. The visual alert remains available. No
  physical alarm is triggered.
- Uses only the supported dynamic API for image evidence; the frontend proxy
  does not serve arbitrary project files.

The interface preserves the scientific limitations: reliability and fusion
scores are not calibrated probabilities; missing evidence is not negative
evidence; VLM results are shown only when actually produced; Thermal spatial
association, Radar-to-Caltech-track association, and persistent Radar
identity are not established. Motion measurements are image-plane pixels and
do not imply world scale, 3D trajectory, or physical acceleration. This is
decision-support UI, not an operational threat determination.
