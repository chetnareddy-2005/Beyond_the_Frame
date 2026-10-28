import argparse
import os
import sys
import pathlib
import types

# Windows compatibility for checkpoints containing PosixPath
pathlib.PosixPath = pathlib.WindowsPath

m = types.ModuleType("pathlib._local")
m.PosixPath = pathlib.WindowsPath
m.WindowsPath = pathlib.WindowsPath
m.PurePosixPath = pathlib.PurePosixPath
m.PureWindowsPath = pathlib.PureWindowsPath
sys.modules["pathlib._local"] = m

DETECTOR_ROOT = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = DETECTOR_ROOT.parents[1]
parser = argparse.ArgumentParser(description="Run the detector's Phase 2 video tracking example.")
parser.add_argument(
    "--weights",
    type=pathlib.Path,
    default=PROJECT_ROOT / "artifacts" / "checkpoints" / "best.pt",
)
parser.add_argument(
    "--source",
    type=pathlib.Path,
    default=PROJECT_ROOT / "datasets" / "videos" / "V_DRONE_005.mp4",
)
parser.add_argument("--project", type=pathlib.Path, default=PROJECT_ROOT / "results")
parser.add_argument("--name", default="PHASE2_V_DRONE_005")
args = parser.parse_args()
weights = args.weights.expanduser().resolve()
source = args.source.expanduser().resolve()
project = args.project.expanduser().resolve()
if not weights.is_file():
    parser.error(f"Checkpoint does not exist: {weights}")
if not source.exists():
    parser.error(f"Video source does not exist: {source}")

sys.argv = [
    "detect.py",
    "--weights", str(weights),
    "--source", str(source),
    "--imgsz", "544",
    "--conf-thres", "0.25",
    "--track",
    "--project", str(project),
    "--name", args.name,
]

detector_script = DETECTOR_ROOT / "detect.py"
previous_directory = pathlib.Path.cwd()
try:
    os.chdir(DETECTOR_ROOT)
    exec(
        compile(detector_script.read_text(encoding="utf-8"), str(detector_script), "exec"),
        {"__name__": "__main__", "__file__": str(detector_script)},
    )
finally:
    os.chdir(previous_directory)
