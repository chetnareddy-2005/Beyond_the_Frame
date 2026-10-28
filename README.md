# Beyond the Frame

Beyond the Frame is a local RGB/Thermal video-analysis prototype with a
Python API and a same-origin static dashboard. The dashboard coordinates
authenticated uploads, run progress, and run-scoped results; the backend owns
input preparation, detection/tracking, saved artifacts, and result APIs.

## Project map

- `backend/` — HTTP API, authentication, upload and CCTV input preparation,
  frame acquisition, detection, tracking, run management, and pipeline stages.
- `frontend/` — static dashboard (`index.html`, `app.js`, `styles.css`) and its
  local static server/API proxy.
- `base_detector/yolov5_ghostnet_AFNO/` — the custom YOLOv5 GhostNet/AFNO
  detector implementation and its dependency manifest.
- `artifacts/checkpoints/best.pt` — RGB detector checkpoint.
- `artifacts/checkpoints/thermal_bird_drone_best.pt` — Thermal detector
  checkpoint; class mapping must remain `0=Bird`, `1=Drone`.
- `multimodal/`, `tracking/`, `temporal/`, `reliability/`, `evidence/`,
  `vlm/`, and `fusion/` — reusable analysis components and research code.
- `results/caltech_rgb_tracking/` — frozen historical experiment inputs for
  legacy read APIs; these are distinct from newly uploaded runs.
- `results/dashboard_runs/` — per-run inputs, status, manifests, and outputs
  created by the dashboard.
- `datasets/` — local datasets and example videos. Datasets are needed for
  training/evaluation, not for an ordinary upload analysis.
- `docs/`, component `README.md` files, and `tools/` — readiness/provenance
  notes, research component documentation, and data-review utilities.

Do not remove or replace checkpoints, datasets, videos, frozen results, or
saved dashboard runs as routine cleanup. Authentication data is stored
separately under `results/auth/`.

## Requirements and setup

Use Python 3.10 or newer. The root `requirements.txt` delegates to the
detector's existing requirements, including OpenCV, NumPy, PyTorch,
TorchVision, and Supervision. It is a compatibility-range manifest, not a
fully pinned lockfile. PyTorch CPU/CUDA wheel selection depends on the host;
use the [PyTorch install selector](https://pytorch.org/get-started/locally/)
when the default package index does not match your hardware.
For the verified Windows/Python 3.12 CPU environment, use the fully pinned
[`requirements-windows-py312-cpu.lock`](requirements-windows-py312-cpu.lock)
instead of `requirements.txt`:

```powershell
python -m pip install -r requirements-windows-py312-cpu.lock
```

That lock is specific to Windows x64, Python 3.12, and CPU-only PyTorch; do not
use it for CUDA or another operating system. On other hosts, use
`requirements.txt` and install the appropriate PyTorch build.

Node.js 18 or newer is needed only for the browser JavaScript tests.

From the project root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-windows-py312-cpu.lock
```

The command above is for the verified Windows/Python 3.12 CPU target. On
other platforms or when using CUDA, install `requirements.txt` and select the
matching PyTorch build as described above.

The default backend also loads and validates the frozen historical result
files at startup. Keep `results/caltech_rgb_tracking/` and the required
checkpoints in place. Checkpoints are local assets; this project does not
download them automatically.

## Start the application

Open two PowerShell terminals in the project root. In the backend terminal,
bootstrap a first local account with a unique username and strong password:

```powershell
$env:BTF_AUTH_USERNAME = "your-local-username"
$env:BTF_AUTH_PASSWORD = "use-a-unique-strong-password"
python -m backend.server
```

Then start the dashboard proxy in the second terminal:

```powershell
python -m frontend.server
```

Open <http://127.0.0.1:8001>. When `results/auth/users.sqlite3` already
exists, startup credentials do not replace its account; sign in with the
existing account. Keep passwords out of source control. The backend defaults
to `127.0.0.1:8000`; use `--host`, `--port`, `--project-root`, or
`BTF_PROJECT_ROOT` to change its address or project root. Set
`BTF_API_ORIGIN` or pass `--api-origin` to the frontend if the backend uses a
different address.

## Smoke check and normal workflow

With both servers running, verify the backend and dashboard from PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
(Invoke-WebRequest http://127.0.0.1:8001/).StatusCode
```

For an end-to-end smoke run, sign in to the dashboard, choose **Upload
Video**, select **RGB**, prepare an existing short clip (for example
`datasets\videos\V_DRONE_005_square.mp4`), then explicitly choose **Start
Analysis**. Follow its run status and open the completed run from Results.
Uploads are stored in a new run directory under `results/dashboard_runs/`;
they do not overwrite historical experiment outputs. The project readiness
checklist records the previously completed 300-frame RGB smoke run and its
known scientific limitations.

From the project root, run the automated checks with:

```powershell
python -m pytest
node --test frontend\tests\*.js
```

The Windows CPU lock includes the development test dependency. On other
platforms, install `requirements-dev.txt` instead of `requirements.txt`
before running the checks. Optional manual VLM inference/tools need
`python -m pip install -r requirements-vlm.txt`; the SmolVLM weights must
still already exist in the local Hugging Face cache.
BOREAS Radar research utilities need
`python -m pip install -r requirements-radar.txt` and their documented local
dataset/devkit assets. Generic Radar upload and dashboard analysis remain
unsupported.

## Supported paths and limitations

- RGB uploads run the existing full-video detection, tracking, temporal,
  reliability, evidence, fusion, assessment, and report path. Automatic VLM
  inference is skipped by default.
- Thermal uploads run sequential detection and tracking, followed by
  offline track-level temporal consensus. Reliability, evidence selection,
  VLM, fusion, and assessment are not run. A completed Thermal report may
  include a detector-only REVIEW cue for predicted Drone tracks, with evidence
  linked to that run's stored observations; it is unverified model output.
  Thermal intensity is not calibrated temperature, and track consensus is a
  non-causal heuristic rather than validated ground truth.
- CCTV is an RTSP input/debug path with session-bound frame acquisition and
  optional single-frame stages; it does not claim real-time FPS.
- Radar upload is unsupported because no generic upload format/parser is
  defined. Existing Radar research code is limited to its documented data.
- Detection, reliability, and fusion values are not calibrated accuracy
  guarantees. Paper-reported metrics are provenance only; independent,
  sequence-level evaluation and human review remain incomplete.

See [backend/README.md](backend/README.md) for API details,
[frontend/README.md](frontend/README.md) for dashboard behavior, and
[docs/PROJECT_READINESS_CHECKLIST.md](docs/PROJECT_READINESS_CHECKLIST.md)
for the current software/scientific readiness boundary.
