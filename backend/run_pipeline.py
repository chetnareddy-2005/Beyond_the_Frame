"""Run the existing video pipeline in an isolated dashboard run directory."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import logging
import math
import os
import queue
import re
import subprocess
import sys
import threading

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from backend.config import Settings
from backend.device import resolve_device
from backend.runs import RunCancelled, RunManager

LOGGER = logging.getLogger(__name__)
StageCallback = Callable[[str, int], None]
DETECTOR_FRAME_PROGRESS = re.compile(r"\bvideo\s+\d+/\d+\s+\((\d+)/(\d+)\)")
DETECTOR_DEFAULT_CHECKPOINT = Path("artifacts") / "checkpoints" / "best.pt"
THERMAL_DETECTOR_DEFAULT_CHECKPOINT = Path("artifacts") / "checkpoints" / "thermal_bird_drone_best.pt"
DETECTOR_ARCHITECTURE = "yv5_gh (GhostNet + AFNO2D)"
DETECTOR_IMAGE_SIZE = 640
DETECTOR_CONFIDENCE_THRESHOLD = 0.25
DETECTOR_IOU_THRESHOLD = 0.45
DETECTOR_PREPROCESSING = (
    "YOLOv5 LoadImages letterbox with model stride; BGR-to-RGB, HWC-to-CHW, "
    "float32 normalized to [0, 1]"
)
TRACKING_CHECKPOINT_CONTRACT = "detector-tracker-v1"
DETECTOR_BOOTSTRAP = """
import ctypes
import importlib.util
import os
import pathlib
import runpy
import sys
import types

# Load PyTorch's OpenMP runtime first so DLL lookup cannot select a second
# libiomp5md.dll from the Anaconda base Library\\bin directory.
if sys.platform == "win32":
    torch_spec = importlib.util.find_spec("torch")
    if torch_spec is not None and torch_spec.origin:
        torch_library_dir = pathlib.Path(torch_spec.origin).parent / "lib"
        torch_openmp_runtime = torch_library_dir / "libiomp5md.dll"
        if torch_openmp_runtime.is_file():
            # The Anaconda base PATH also contains another libiomp5md.dll in
            # Library\\bin. Put Torch's directory first for dependent DLL
            # lookups; add_dll_directory alone does not change PATH lookup
            # order for every native extension loaded by the detector.
            torch_library_dir_text = str(torch_library_dir.resolve())
            normalized_torch_dir = os.path.normcase(os.path.normpath(torch_library_dir_text))
            path_entries = [
                entry.strip('"')
                for entry in os.environ.get("PATH", "").split(os.pathsep)
                if entry.strip('"')
                and os.path.normcase(os.path.normpath(entry.strip('"'))) != normalized_torch_dir
            ]
            os.environ["PATH"] = os.pathsep.join([torch_library_dir_text, *path_entries])
            torch_dll_directory = os.add_dll_directory(str(torch_library_dir))
            torch_openmp_handle = ctypes.WinDLL(str(torch_openmp_runtime))

pathlib.PosixPath = pathlib.WindowsPath
compatibility_module = types.ModuleType("pathlib._local")
compatibility_module.PosixPath = pathlib.WindowsPath
compatibility_module.WindowsPath = pathlib.WindowsPath
compatibility_module.PurePosixPath = pathlib.PureWindowsPath
compatibility_module.PureWindowsPath = pathlib.PureWindowsPath
sys.modules["pathlib._local"] = compatibility_module
pathlib._local = compatibility_module
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _parse_detector_frame_progress(line: str) -> tuple[int, int] | None:
    match = DETECTOR_FRAME_PROGRESS.search(line)
    return tuple(map(int, match.groups())) if match else None


def _detector_subprocess_environment() -> dict[str, str]:
    """Keep the detector child from loading Anaconda's competing OpenMP DLL."""
    environment = os.environ.copy()
    if sys.platform != "win32":
        return environment

    # Conda's MKL runtime brings its own Intel OpenMP DLL, which conflicts
    # with the OpenMP runtime bundled with PyTorch when ByteTrack performs its
    # first non-empty assignment. Keep MKL available but use its sequential
    # threading layer inside this detector child process.
    environment["MKL_THREADING_LAYER"] = "SEQUENTIAL"

    torch_spec = importlib.util.find_spec("torch")
    if torch_spec is None or not torch_spec.origin:
        return environment

    torch_library_dir = (Path(torch_spec.origin).parent / "lib").resolve()
    torch_openmp_runtime = torch_library_dir / "libiomp5md.dll"
    if not torch_openmp_runtime.is_file():
        return environment

    normalized_torch_dir = os.path.normcase(os.path.normpath(str(torch_library_dir)))
    retained_path_entries = []
    for raw_entry in environment.get("PATH", "").split(os.pathsep):
        entry = raw_entry.strip('"')
        if not entry:
            continue
        normalized_entry = os.path.normcase(os.path.normpath(os.path.abspath(entry)))
        if normalized_entry == normalized_torch_dir:
            continue
        if (Path(entry) / "libiomp5md.dll").is_file():
            continue
        retained_path_entries.append(entry)

    # Sanitize PATH before Windows starts Python: bootstrap-time changes may
    # happen after interpreter startup has already loaded a competing DLL.
    environment["PATH"] = os.pathsep.join([str(torch_library_dir), *retained_path_entries])
    return environment


def _vlm_enabled() -> bool:
    """VLM is opt-in until its Bird/Drone behavior is validated on reviewed data."""
    return os.environ.get("BTF_ENABLE_VLM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _thermal_drone_track_ids(tracks: list[dict]) -> list[str]:
    """Return tracks explicitly classified Drone/UAV by the thermal detector."""
    return [
        str(track["track_id"])
        for track in tracks
        if str(track.get("class", "")).strip().casefold() in {"drone", "uav"}
    ]


if TYPE_CHECKING:
    from backend.app import ProjectData


def _publish_early_uav_alert(
    run_root: Path,
    tracks_csv: Path,
    reliability_csv: Path,
    temporal_consensus_csv: Path | None = None,
) -> dict[str, object] | None:
    """Persist an alert only when full-track consensus supports a Drone label."""
    from fusion.evidence_fusion import fuse_track
    from fusion.phase5_evidence_fusion import _normalized_detector_scores

    alert_path = run_root / "early_alert.json"
    if alert_path.exists():
        alert = json.loads(alert_path.read_text(encoding="utf-8"))
        if alert.get("uav_alert") is not True:
            raise ValueError("Persisted early alert must remain positive")
        return alert

    confident_tracks = {}
    with reliability_csv.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if row["status"].strip().upper() == "CONFIDENT":
                confident_tracks[str(int(row["track_id"]))] = float(row["reliability_score"])
    if not confident_tracks:
        return None

    consensus_by_track = None
    if temporal_consensus_csv is not None:
        consensus_by_track = {}
        with temporal_consensus_csv.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                track_id = str(int(row["track_id"]))
                if track_id in consensus_by_track:
                    raise ValueError(f"Duplicate temporal consensus track_id: {track_id}")
                consensus_by_track[track_id] = row

    observations_by_track: dict[str, list[dict[str, str]]] = {}
    with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            track_id = str(int(row["track_id"]))
            if track_id in confident_tracks:
                observations_by_track.setdefault(track_id, []).append(row)

    for track_id, reliability_score in confident_tracks.items():
        observations = observations_by_track.get(track_id)
        if not observations:
            continue
        if consensus_by_track is not None:
            consensus = consensus_by_track.get(track_id)
            if (
                consensus is None
                or consensus.get("consensus_label", "").strip() != "Drone"
                or consensus.get("decision_status", "").strip().casefold() != "confident"
            ):
                continue
            final_label = "UAV"
        else:
            scores = _normalized_detector_scores(observations)
            final_label, _, _ = fuse_track(scores, reliability_score)
        if final_label != "UAV":
            continue

        alert = {
            "uav_alert": True,
            "level": "REVIEW",
            "track_id": track_id,
            "message": "The detector/temporal pipeline flagged a possible UAV. This result has not been independently verified; review the evidence before acting.",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = alert_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(alert, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(alert_path)
        return alert

    return None


def _publish_provisional_uav_alert(
    run_root: Path,
    tracks_csv: Path,
    *,
    minimum_consecutive_frames: int = 3,
) -> dict[str, object] | None:
    """Publish a detector-only review notice once a Drone track persists briefly."""
    alert_path = run_root / "early_alert.json"
    if alert_path.is_file():
        try:
            existing = json.loads(alert_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and existing.get("state") == "provisional":
            return existing
    if not tracks_csv.is_file():
        return None

    frames_by_track: dict[str, dict[int, dict[str, str]]] = {}
    try:
        with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                if row.get("class_name", "").strip().casefold() not in {"drone", "uav"}:
                    continue
                try:
                    track_id = str(int(row["track_id"]))
                    frame = int(row["frame"])
                    confidence = float(row["confidence"])
                except (KeyError, TypeError, ValueError):
                    continue
                if frame < 1 or not math.isfinite(confidence) or confidence < DETECTOR_CONFIDENCE_THRESHOLD:
                    continue
                frames_by_track.setdefault(track_id, {})[frame] = row
    except (OSError, csv.Error):
        return None

    candidates = []
    for track_id, observations in frames_by_track.items():
        ordered_frames = sorted(observations)
        streak = 1
        for previous, current in zip(ordered_frames, ordered_frames[1:]):
            streak = streak + 1 if current == previous + 1 else 1
            if streak >= minimum_consecutive_frames:
                candidates.append((current, track_id, observations[current]))
                break
    if not candidates:
        return None

    frame, track_id, observation = min(candidates, key=lambda item: (item[0], int(item[1])))
    alert = {
        "uav_alert": True,
        "state": "provisional",
        "level": "REVIEW",
        "track_id": track_id,
        "first_detection_frame": min(frames_by_track[track_id]),
        "published_frame": frame,
        "detector_class": observation.get("class_name", "Drone"),
        "message": (
            "Preliminary detector review: this track has been labelled Drone across "
            f"{minimum_consecutive_frames} consecutive frames. Temporal/fusion assessment "
            "is still running; this is not a confirmed UAV or hostile identification."
        ),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = alert_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(alert, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(alert_path)
    return alert


def _finalize_early_uav_alert(
    run_root: Path,
    *,
    final_uav_track_ids: set[str],
) -> None:
    """Resolve a provisional notice against the completed run's final track decisions."""
    alert_path = run_root / "early_alert.json"
    if not alert_path.is_file():
        return
    try:
        alert = json.loads(alert_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Could not read provisional alert for finalization: %s", alert_path)
        return
    if not isinstance(alert, dict) or alert.get("state") != "provisional":
        return
    confirmed = str(alert.get("track_id", "")) in final_uav_track_ids
    alert.update(
        {
            "uav_alert": confirmed,
            "state": "confirmed_review" if confirmed else "not_confirmed",
            "message": (
                "Final evidence fusion retained this track as a possible UAV. "
                "Independent verification is still required; this is not a hostile/friendly decision."
                if confirmed
                else "Final evidence fusion did not retain this provisional Drone candidate as a UAV alert."
            ),
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    temporary = alert_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(alert, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(alert_path)


def _write_empty_csv(path: Path, fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        csv.writer(output).writerow(fields)


def _write_temporal_consensus_manifest(
    run_root: Path,
    config,
    track_count: int,
) -> None:
    from dataclasses import asdict

    from temporal.temporal_consensus import (
        CONSENSUS_SCORE_INTERPRETATION,
        TEMPORAL_CONSENSUS_VERSION,
    )

    detector_manifest_path = run_root / "detector_manifest.json"
    detector_manifest = (
        json.loads(detector_manifest_path.read_text(encoding="utf-8"))
        if detector_manifest_path.is_file()
        else {}
    )
    manifest = {
        "schema_version": 1,
        "status": "completed",
        "model_version": TEMPORAL_CONSENSUS_VERSION,
        "configuration": asdict(config),
        "observation_source": "tracking/detector/tracks.csv",
        "processing_mode": "offline_complete_track",
        "score_interpretation": CONSENSUS_SCORE_INTERPRETATION,
        "class_mapping": {"0": "Bird", "1": "Drone"},
        "track_count": track_count,
        "detector_provenance": {
            key: detector_manifest.get(key)
            for key in (
                "detector_checkpoint_name",
                "detector_checkpoint_sha256",
                "detector_model_architecture",
            )
        },
    }
    destination = run_root / "temporal" / "temporal_consensus_manifest.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _add_source_to_track_csv(path: Path, tracks_csv: Path) -> None:
    with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
        track_sources = {
            str(int(row["track_id"])): row["source"]
            for row in csv.DictReader(source)
        }
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    if "source" in fields:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields.insert(0, "source")
    with temporary.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            track_id = str(int(row["track_id"]))
            if track_id not in track_sources:
                raise ValueError(f"Temporal output references unknown track {track_id}")
            writer.writerow({"source": track_sources[track_id], **row})
    temporary.replace(path)


def _require_track_observations(tracks_csv: Path, *, allow_empty: bool = False) -> None:
    if not tracks_csv.is_file():
        raise FileNotFoundError("Detector/tracker did not produce tracks.csv")
    with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("Detector/tracker produced tracks.csv without a header")
        if not allow_empty and not next(reader, None):
            raise ValueError("No detections were produced for this video")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_detector_checkpoint(project_root: Path, *, input_type: str | None = None) -> tuple[Path, str]:
    if input_type == "thermal":
        configured_weights = os.environ.get("BTF_THERMAL_DETECTOR_WEIGHTS")
        default_weights = THERMAL_DETECTOR_DEFAULT_CHECKPOINT
    else:
        configured_weights = os.environ.get("BTF_DETECTOR_WEIGHTS")
        default_weights = DETECTOR_DEFAULT_CHECKPOINT

    weights = (
        Path(configured_weights).expanduser().resolve()
        if configured_weights
        else (project_root / default_weights).resolve()
    )
    if not weights.is_file():
        environment_name = (
            "BTF_THERMAL_DETECTOR_WEIGHTS" if input_type == "thermal" else "BTF_DETECTOR_WEIGHTS"
        )
        source = environment_name if configured_weights else default_weights.as_posix()
        raise FileNotFoundError(f"Detector checkpoint from {source} is unavailable")
    return weights, _sha256_file(weights)


def run_reproducibility_metadata(
    project_root: Path, input_video: Path, *, input_type: str | None = None
) -> dict[str, object]:
    weights, checkpoint_sha256 = resolve_detector_checkpoint(project_root, input_type=input_type)
    fps, width, height = _video_metadata(input_video)
    configured_weights = os.environ.get(
        "BTF_THERMAL_DETECTOR_WEIGHTS" if input_type == "thermal" else "BTF_DETECTOR_WEIGHTS"
    )
    checkpoint_name = (
        Path(configured_weights).name
        if configured_weights
        else (
            THERMAL_DETECTOR_DEFAULT_CHECKPOINT
            if input_type == "thermal"
            else DETECTOR_DEFAULT_CHECKPOINT
        ).as_posix()
    )
    detector_device = resolve_device()
    vlm_device = resolve_device()
    LOGGER.info("Detector device: %s", detector_device)
    LOGGER.info("VLM device: %s", vlm_device)
    return {
        "detector_checkpoint_name": checkpoint_name,
        "detector_checkpoint_sha256": checkpoint_sha256,
        "detector_model_architecture": DETECTOR_ARCHITECTURE,
        "detector_image_size": [DETECTOR_IMAGE_SIZE, DETECTOR_IMAGE_SIZE],
        "detector_confidence_threshold": DETECTOR_CONFIDENCE_THRESHOLD,
        "detector_iou_threshold": DETECTOR_IOU_THRESHOLD,
        "detector_device": detector_device,
        "vlm_device": vlm_device,
        "detector_preprocessing": (
            "ThermalAdapter per-frame BGR-to-grayscale and min/max intensity normalization to uint8; "
            "YOLOv5 letterbox, grayscale-to-three-channel expansion, HWC-to-CHW, float32 normalized to [0, 1]"
            if input_type == "thermal"
            else DETECTOR_PREPROCESSING
        ),
        "tracking_checkpoint_contract": TRACKING_CHECKPOINT_CONTRACT,
        "source_video_sha256": _sha256_file(input_video),
        "source_video_fps": fps,
        "source_video_width": width,
        "source_video_height": height,
    }


def _video_metadata(video_path: Path) -> tuple[float | None, float | None, float | None]:
    try:
        import cv2
    except ImportError:
        LOGGER.warning("OpenCV is unavailable; image-plane motion will omit FPS and frame dimensions")
        return None, None, None
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            LOGGER.warning("Could not read video metadata; image-plane motion will omit FPS and frame dimensions")
            return None, None, None
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (
            fps if math.isfinite(fps) and fps > 0 else None,
            width if math.isfinite(width) and width > 0 else None,
            height if math.isfinite(height) and height > 0 else None,
        )
    finally:
        capture.release()


def _run_detector_and_tracker(
    project_root: Path,
    run_root: Path,
    input_video: Path,
    set_stage: StageCallback,
    report_phase: Callable[..., None],
    check_cancelled: Callable[[], None],
    *,
    input_type: str | None = None,
) -> Path:
    detector_root = project_root / "base_detector" / "yolov5_ghostnet_AFNO"
    weights, checkpoint_sha256 = resolve_detector_checkpoint(project_root, input_type=input_type)
    detector_device = resolve_device()
    configured_weights = os.environ.get(
        "BTF_THERMAL_DETECTOR_WEIGHTS" if input_type == "thermal" else "BTF_DETECTOR_WEIGHTS"
    )
    checkpoint_name = (
        Path(configured_weights).name
        if configured_weights
        else (
            THERMAL_DETECTOR_DEFAULT_CHECKPOINT
            if input_type == "thermal"
            else DETECTOR_DEFAULT_CHECKPOINT
        ).as_posix()
    )
    detector_manifest = run_root / "detector_manifest.json"
    detector_manifest.write_text(
        json.dumps(
            {
                "detector_checkpoint_name": checkpoint_name,
                "detector_checkpoint_sha256": checkpoint_sha256,
                "detector_model_architecture": DETECTOR_ARCHITECTURE,
                "detector_image_size": [DETECTOR_IMAGE_SIZE, DETECTOR_IMAGE_SIZE],
                "detector_confidence_threshold": DETECTOR_CONFIDENCE_THRESHOLD,
                "detector_iou_threshold": DETECTOR_IOU_THRESHOLD,
                "detector_device": detector_device,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    tracking_root = run_root / "tracking"
    tracking_root.mkdir(parents=True, exist_ok=True)
    set_stage("detection", 8)
    command = [
        sys.executable,
        "-c",
        DETECTOR_BOOTSTRAP,
        str(detector_root / "detect.py"),
        "--weights",
        str(weights),
        "--source",
        str(input_video),
        "--img",
        str(DETECTOR_IMAGE_SIZE),
        "--conf-thres",
        str(DETECTOR_CONFIDENCE_THRESHOLD),
        "--iou-thres",
        str(DETECTOR_IOU_THRESHOLD),
        "--device",
        detector_device,
        "--track",
        "--project",
        str(tracking_root),
        "--name",
        "detector",
    ]
    process = subprocess.Popen(
        command,
        cwd=detector_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_detector_subprocess_environment(),
    )
    set_process = getattr(set_stage, "set_process", None)
    clear_process = getattr(set_stage, "clear_process", None)
    progress_lines: queue.Queue[str | None] = queue.Queue()
    output_tail: list[str] = []
    output_reader = None

    def read_detector_output():
        if process.stdout is not None:
            for line in process.stdout:
                output_tail.append(line)
                if len(output_tail) > 20:
                    del output_tail[0]
                progress_lines.put(line)
        progress_lines.put(None)

    try:
        if set_process is not None:
            set_process(process)
        output_reader = threading.Thread(target=read_detector_output, daemon=True)
        output_reader.start()
        while output_reader.is_alive() or not progress_lines.empty():
            check_cancelled()
            try:
                line = progress_lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                continue
            frame_counts = _parse_detector_frame_progress(line)
            if frame_counts is not None:
                current, total = frame_counts
                for phase, noun in (
                    ("detection", "frames processed"),
                    ("tracking", "frames tracked"),
                ):
                    report_phase(
                        phase,
                        "processing",
                        current,
                        total,
                        f"{current} / {total} {noun}",
                    )
                if current <= 3 or current % 3 == 0:
                    _publish_provisional_uav_alert(
                        run_root, tracking_root / "detector" / "tracks.csv"
                    )
        output_reader.join()
        return_code = process.wait()
        output = "".join(output_tail)
        if return_code:
            raise subprocess.CalledProcessError(return_code, command, output=output)
    except RunCancelled:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if output_reader is not None:
            output_reader.join(timeout=5)
        else:
            process.communicate()
        raise
    except subprocess.CalledProcessError as error:
        output = (error.output or "").strip()
        detail = output.splitlines()[-1][:500] if output else "no detector diagnostic was captured"
        raise RuntimeError(
            f"Detector/tracker exited with status {error.returncode}: {detail}"
        ) from error
    finally:
        if clear_process is not None:
            clear_process(process)
    for phase in ("detection", "tracking"):
        progress = getattr(getattr(set_stage, "record", None), "phase_progress", {}).get(phase, {})
        if progress.get("current") is not None:
            report_phase(
                phase,
                "completed",
                progress["current"],
                progress["total"],
                progress.get("message"),
            )
        else:
            report_phase(phase, "completed")
    check_cancelled()
    tracks_csv = tracking_root / "detector" / "tracks.csv"
    # A video with zero detections is still a valid completed analysis. Keep
    # the empty, header-only CSV so downstream stages can report zero tracks.
    _require_track_observations(tracks_csv, allow_empty=True)
    return tracks_csv


def _run_thermal_detector_and_tracker(
    project_root: Path,
    run_root: Path,
    input_video: Path,
    set_stage: StageCallback,
    report_phase: Callable[..., None],
    check_cancelled: Callable[[], None],
) -> Path:
    import cv2
    from dataclasses import replace

    from backend.detection import DetectionService
    from backend.frames import _prepared_frame
    from backend.tracking import TrackingService
    from multimodal.thermal_adapter import ThermalAdapter

    weights, checkpoint_sha256 = resolve_detector_checkpoint(
        project_root, input_type="thermal"
    )
    detector_manifest = run_root / "detector_manifest.json"
    detector_manifest.write_text(
        json.dumps(
            {
                "detector_checkpoint_name": weights.name,
                "detector_checkpoint_sha256": checkpoint_sha256,
                "detector_model_architecture": DETECTOR_ARCHITECTURE,
                "detector_image_size": [DETECTOR_IMAGE_SIZE, DETECTOR_IMAGE_SIZE],
                "detector_confidence_threshold": DETECTOR_CONFIDENCE_THRESHOLD,
                "detector_iou_threshold": DETECTOR_IOU_THRESHOLD,
                "detector_device": resolve_device(),
                "class_names": {"0": "Bird", "1": "Drone"},
                "detector_preprocessing": (
                    "ThermalAdapter per-frame BGR-to-grayscale and min/max intensity "
                    "normalization to uint8; model runtime expands grayscale to three channels"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    metadata = getattr(getattr(set_stage, "record", None), "input_metadata", {})
    raw_frame_count = metadata.get("frame_count") if isinstance(metadata, dict) else None
    total_frames = (
        raw_frame_count
        if isinstance(raw_frame_count, int) and not isinstance(raw_frame_count, bool) and raw_frame_count > 0
        else None
    )
    raw_fps = metadata.get("fps") if isinstance(metadata, dict) else None
    fps = (
        float(raw_fps)
        if isinstance(raw_fps, (int, float))
        and not isinstance(raw_fps, bool)
        and math.isfinite(raw_fps)
        and raw_fps > 0
        else None
    )

    tracking_root = run_root / "tracking" / "detector"
    tracking_root.mkdir(parents=True, exist_ok=True)
    tracks_csv = tracking_root / "tracks.csv"
    fields = (
        "source",
        "frame",
        "source_frame_index",
        "timestamp_sec",
        "track_id",
        "class_id",
        "class_name",
        "confidence",
        "x1",
        "y1",
        "x2",
        "y2",
    )
    thermal_run_id = getattr(getattr(set_stage, "record", None), "run_id", None)
    if not thermal_run_id:
        raise RuntimeError("Thermal run metadata is unavailable")
    owner = f"thermal-run:{thermal_run_id}"
    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError("Thermal video could not be opened for sequential processing")

    detector = None
    tracker = None
    processed_frames = 0
    try:
        detector = DetectionService(project_root)
        tracker = TrackingService()
        set_stage("detection", 8)
        with tracks_csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            while True:
                check_cancelled()
                success, raw_frame = capture.read()
                if not success or raw_frame is None or raw_frame.size == 0:
                    if total_frames is not None and processed_frames < total_frames:
                        raise RuntimeError(
                            f"Thermal video ended at frame {processed_frames} of {total_frames}"
                        )
                    break
                normalized = ThermalAdapter.preprocess_frame(raw_frame)
                if normalized is None:
                    raise RuntimeError(
                        f"Thermal preprocessing failed for source frame {processed_frames}"
                    )

                timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
                timestamp_sec = (
                    timestamp_ms / 1000.0
                    if math.isfinite(timestamp_ms) and timestamp_ms >= 0
                    else (
                        processed_frames / fps
                        if fps is not None
                        else None
                    )
                )
                prepared = _prepared_frame(
                    normalized,
                    source_type="upload",
                    input_type="thermal",
                    run_id=thermal_run_id,
                    live_reference_id=None,
                    frame_index=processed_frames,
                    preprocessing_status=(
                        "thermal_adapter_grayscale_minmax_uint8_not_temperature_calibrated"
                    ),
                )
                prepared = replace(prepared, timestamp_sec=timestamp_sec)
                detection_result, _ = detector.detect(prepared)
                track_result = tracker.track(owner, detection_result)
                for observation in track_result.tracks:
                    writer.writerow(
                        {
                            "source": input_video.name,
                            "frame": processed_frames + 1,
                            "source_frame_index": processed_frames,
                            "timestamp_sec": (
                                observation.timestamp_sec
                                if observation.timestamp_sec is not None
                                else ""
                            ),
                            "track_id": observation.track_id,
                            "class_id": observation.class_id,
                            "class_name": observation.class_name,
                            "confidence": observation.confidence,
                            "x1": observation.x1,
                            "y1": observation.y1,
                            "x2": observation.x2,
                            "y2": observation.y2,
                        }
                    )
                output.flush()
                _publish_provisional_uav_alert(
                    run_root, tracks_csv, minimum_consecutive_frames=3
                )
                processed_frames += 1
                if total_frames is not None:
                    progress = min(24, 8 + int(16 * processed_frames / total_frames))
                    set_stage("detection", progress)
                progress_total = max(total_frames or processed_frames, processed_frames)
                report_phase(
                    "detection",
                    "processing",
                    processed_frames,
                    progress_total,
                    f"{processed_frames} / {progress_total} thermal frames processed",
                )
                report_phase(
                    "tracking",
                    "processing",
                    processed_frames,
                    progress_total,
                    f"{processed_frames} / {progress_total} thermal frames tracked",
                )
        if processed_frames == 0:
            raise RuntimeError("Thermal video contains no decodable frames")
        for phase in ("detection", "tracking"):
            report_phase(
                phase,
                "completed",
                processed_frames,
                max(total_frames or processed_frames, processed_frames),
                f"{processed_frames} thermal frames processed",
            )
        set_stage("tracking", 25)
        check_cancelled()
        return tracks_csv
    finally:
        try:
            capture.release()
        finally:
            try:
                if tracker is not None:
                    tracker.reset_owner(owner)
            finally:
                if detector is not None:
                    detector.close()


def _prepare_tracking_checkpoint(
    project_root: Path,
    run_root: Path,
    input_video: Path,
    set_stage: StageCallback,
    report_phase: Callable[..., None],
    check_cancelled: Callable[[], None],
    *,
    input_type: str | None = None,
) -> Path:
    check_cancelled()
    tracking_root = run_root / "tracking"
    tracks_csv = tracking_root / "detector" / "tracks.csv"
    record = getattr(set_stage, "record", None)
    if not getattr(record, "resume_after_tracking", False):
        return _run_detector_and_tracker(
            project_root,
            run_root,
            input_video,
            set_stage,
            report_phase,
            check_cancelled,
            input_type=input_type,
        )
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    expected_configuration = manifest.get("resume_configuration")
    actual_configuration = run_reproducibility_metadata(
        project_root, input_video, input_type=input_type
    )
    if expected_configuration != actual_configuration:
        raise RuntimeError("Detector/tracker resume configuration changed before execution")
    detector_manifest = json.loads(
        (run_root / "detector_manifest.json").read_text(encoding="utf-8")
    )
    detector_keys = (
        "detector_checkpoint_sha256",
        "detector_model_architecture",
        "detector_image_size",
        "detector_confidence_threshold",
        "detector_iou_threshold",
        "detector_device",
    )
    if any(
        detector_manifest.get(key) != actual_configuration.get(key)
        for key in detector_keys
    ):
        raise RuntimeError("Detector/tracker resume checkpoint no longer matches the configured weights")
    RunManager._validate_tracks_checkpoint(tracks_csv)
    for phase in ("detection", "tracking"):
        report_phase(
            phase,
            "completed",
            message="Reused validated detector/tracker checkpoint",
        )
    check_cancelled()
    return tracks_csv


def load_run_project_data(project_root: Path, run_root: Path) -> ProjectData:
    from backend.app import ProjectData, ThermalRunData

    manifest_path = run_root / "run_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("input_type") == "thermal":
            detector_manifest_path = run_root / "detector_manifest.json"
            detector_manifest = (
                json.loads(detector_manifest_path.read_text(encoding="utf-8"))
                if detector_manifest_path.is_file()
                else None
            )
            return ThermalRunData(
                run_root / "tracking" / "detector" / "tracks.csv",
                run_manifest=manifest,
                detector_manifest=detector_manifest,
                consensus_path=run_root / "temporal" / "temporal_consensus.csv",
                motion_trajectory_path=run_root / "temporal" / "trajectories.json",
            )

    tracking_root = run_root / "tracking" / "detector"
    paths = {
        "tracks": tracking_root / "tracks.csv",
        "temporal": run_root / "temporal" / "temporal_features.csv",
        "motion": run_root / "temporal" / "track_motion.csv",
        "motion_trajectory_json": run_root / "temporal" / "trajectories.json",
        "temporal_consensus": run_root / "temporal" / "temporal_consensus.csv",
        "reliability": run_root / "reliability" / "reliability_results.csv",
        "selected_evidence": run_root / "evidence" / "selected_evidence.csv",
        "vlm": run_root / "vlm" / "vlm_results.csv",
        "thermal_coverage": run_root / "modalities" / "thermal_coverage.csv",
        "thermal_manifest": run_root / "modalities" / "thermal_frame_manifest.csv",
        "radar_summary": run_root / "modalities" / "radar_validation_summary.csv",
        "fusion": run_root / "fusion" / "fusion_results.csv",
        "availability": run_root / "fusion" / "evidence_availability.csv",
        "evidence_root": run_root / "evidence",
    }
    return ProjectData(
        Settings(project_root=project_root),
        paths_override=paths,
        historical_compatibility=False,
    )


def run_video_pipeline(
    project_root: Path,
    run_root: Path,
    input_video: Path,
    set_stage: StageCallback,
) -> ProjectData:
    """Run detector/tracker and existing downstream components for one upload."""
    check_cancelled = getattr(set_stage, "check_cancelled", lambda: None)
    update_phase_progress = getattr(set_stage, "update_phase_progress", None)

    def report_phase(phase: str, status: str, current=None, total=None, message=None):
        if update_phase_progress is not None:
            update_phase_progress(
                phase,
                status,
                current=current,
                total=total,
                message=message,
            )

    run_record = getattr(set_stage, "record", None)
    input_type = getattr(run_record, "input_type", None)
    if input_type == "thermal":
        tracks_csv = _run_thermal_detector_and_tracker(
            project_root,
            run_root,
            input_video,
            set_stage,
            report_phase,
            check_cancelled,
        )
        from temporal.temporal_consensus import (
            TemporalConsensusConfig,
            load_tracks_consensus,
            process_tracks_consensus,
        )

        temporal_root = run_root / "temporal"
        temporal_root.mkdir(parents=True, exist_ok=True)
        from temporal.motion_trajectory import process_tracks_csv as process_motion_trajectories

        fps, frame_width, frame_height = _video_metadata(input_video)
        with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
            has_observations = next(csv.DictReader(source), None) is not None
        if has_observations:
            process_motion_trajectories(
                tracks_csv,
                temporal_root,
                fps=fps,
                frame_width=frame_width,
                frame_height=frame_height,
            )
        consensus_csv = temporal_root / "temporal_consensus.csv"
        temporal_config = TemporalConsensusConfig()
        set_stage("temporal", 34)
        report_phase(
            "temporal",
            "processing",
            message="Computing offline track-level temporal consensus",
        )
        consensus_rows = process_tracks_consensus(
            tracks_csv,
            consensus_csv,
            config=temporal_config,
            progress_callback=lambda current, total: report_phase(
                "temporal",
                "processing",
                current,
                total,
                f"{current} / {total} tracks evaluated",
            ),
            check_cancelled=check_cancelled,
        )
        _write_temporal_consensus_manifest(run_root, temporal_config, len(consensus_rows))
        report_phase(
            "temporal",
            "completed",
            len(consensus_rows),
            len(consensus_rows),
            f"{len(consensus_rows)} tracks evaluated",
        )
        check_cancelled()
        data = load_run_project_data(project_root, run_root)
        tracks = data.track_list()
        alert_track_ids = _thermal_drone_track_ids(tracks)
        consensus_by_track = load_tracks_consensus(consensus_csv)
        for phase in ("reliability", "evidence", "vlm", "fusion", "assessment"):
            report_phase(
                phase,
                "skipped",
                message=f"{phase.capitalize()} is not implemented for thermal runs",
            )
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "analysis_mode": "thermal_detection_tracking_only",
            "alert_status": "detector_only",
            "uav_alert": bool(alert_track_ids),
            "alert_track_ids": alert_track_ids,
            "alert_level": "REVIEW" if alert_track_ids else None,
            "alert_message": (
                "The thermal detector classified one or more tracks as Drone. "
                "This is an unverified detector output; review the evidence before acting."
                if alert_track_ids
                else "The thermal detector completed without any Drone-class tracks."
            ),
            "track_count": len(tracks),
            "tracks": [
                {
                    "track_id": track["track_id"],
                    "detector_class": track["class"],
                    "observation_count": track["frames_seen"],
                    "mean_detector_confidence": track["mean_detector_confidence"],
                    "temporal_consensus": consensus_by_track[track["track_id"]],
                }
                for track in tracks
            ],
            "unsupported_stages": [
                "reliability",
                "evidence",
                "vlm",
                "fusion",
                "assessment",
            ],
        }
        temporary = run_root / "final_results.json.tmp"
        temporary.write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(run_root / "final_results.json")
        _finalize_early_uav_alert(
            run_root, final_uav_track_ids={str(track_id) for track_id in alert_track_ids}
        )
        return data

    from evidence.crop_extractor import extract_crops
    from evidence.frame_selector import select_representative_frames, write_selected_evidence
    from fusion.phase5_evidence_fusion import run_phase5_fusion
    from reliability.reliability_score import process_temporal_csv
    from temporal.temporal_features import process_tracks_csv
    from temporal.motion_trajectory import process_tracks_csv as process_motion_trajectories
    from temporal.temporal_consensus import (
        TemporalConsensusConfig,
        process_tracks_consensus,
    )
    from vlm.vlm_adapter import SmolVLM, write_vlm_results

    tracks_csv = _prepare_tracking_checkpoint(
        project_root,
        run_root,
        input_video,
        set_stage,
        report_phase,
        check_cancelled,
        input_type=input_type,
    )
    resumed_after_tracking = getattr(
        getattr(set_stage, "record", None), "resume_after_tracking", False
    )
    if not resumed_after_tracking:
        set_stage("tracking", 25)
    temporal_root = run_root / "temporal"
    temporal_root.mkdir(parents=True, exist_ok=True)
    temporal_csv = temporal_root / "temporal_features.csv"
    motion_csv = temporal_root / "track_motion.csv"
    consensus_csv = temporal_root / "temporal_consensus.csv"
    fps, frame_width, frame_height = _video_metadata(input_video)
    set_stage("temporal", 34)
    process_tracks_csv(tracks_csv, temporal_csv, motion_csv)
    check_cancelled()
    _add_source_to_track_csv(temporal_csv, tracks_csv)
    _add_source_to_track_csv(motion_csv, tracks_csv)
    process_motion_trajectories(
        tracks_csv,
        temporal_root,
        fps=fps,
        frame_width=frame_width,
        frame_height=frame_height,
    )
    consensus_rows = process_tracks_consensus(
        tracks_csv,
        consensus_csv,
        progress_callback=lambda current, total: report_phase(
            "temporal",
            "processing",
            current,
            total,
            f"{current} / {total} tracks evaluated",
        ),
        check_cancelled=check_cancelled,
    )
    _write_temporal_consensus_manifest(
        run_root,
        TemporalConsensusConfig(),
        len(consensus_rows),
    )
    report_phase("temporal", "completed")
    check_cancelled()

    set_stage("reliability", 42)
    reliability_root = run_root / "reliability"
    reliability_root.mkdir(parents=True, exist_ok=True)
    reliability_csv = reliability_root / "reliability_results.csv"
    process_temporal_csv(
        temporal_csv,
        reliability_csv,
        trajectory_summary_csv=temporal_root / "motion_track_summary.csv",
        progress_callback=lambda current, total: report_phase(
            "reliability",
            "processing",
            current,
            total,
            f"{current} / {total} tracks evaluated",
        ),
    )
    reliability_progress = getattr(
        getattr(set_stage, "record", None), "phase_progress", {}
    ).get("reliability", {})
    report_phase(
        "reliability",
        "completed",
        reliability_progress.get("current"),
        reliability_progress.get("total"),
        reliability_progress.get("message"),
    )
    check_cancelled()
    set_stage("evidence", 60)
    check_cancelled()
    evidence_root = run_root / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    selected = select_representative_frames(
        tracks_csv,
        reliability_csv,
        input_video,
        consensus_csv=consensus_csv,
        max_frames_per_track=1,
        include_drone_candidates=True,
    )
    check_cancelled()
    if selected:
        report_phase(
            "evidence",
            "processing",
            0,
            len(selected),
            f"0 / {len(selected)} evidence items processed",
        )
    selected = extract_crops(
        input_video,
        selected,
        evidence_root,
        padding_fraction=1.0,
        progress_callback=lambda current, total: report_phase(
            "evidence",
            "processing",
            current,
            total,
            f"{current} / {total} evidence items processed",
        ),
    )
    report_phase(
        "evidence",
        "completed",
        len(selected),
        len(selected),
        (
            f"{len(selected)} / {len(selected)} evidence items processed"
            if selected
            else "No evidence selected"
        ),
    )
    check_cancelled()
    selected_csv = evidence_root / "selected_evidence.csv"
    write_selected_evidence(selected, selected_csv)

    vlm_root = run_root / "vlm"
    vlm_root.mkdir(parents=True, exist_ok=True)
    vlm_csv = vlm_root / "vlm_results.csv"
    vlm_fusion_csv = vlm_root / "vlm_fusion_input.csv"
    _write_empty_csv(
        vlm_csv,
        ("track_id", "frame", "crop_path", "label", "confidence", "reason"),
    )
    _write_empty_csv(
        vlm_fusion_csv,
        ("track_id", "frame", "crop_path", "label", "confidence", "reason"),
    )
    vlm_manifest = vlm_root / "vlm_manifest.json"
    if not _vlm_enabled():
        skip_reason = (
            "VLM is disabled by default because its Bird/Drone classifications "
            "are not validated; enable BTF_ENABLE_VLM=1 only for controlled advisory checks."
        )
        vlm_manifest.write_text(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": skip_reason,
                    "verified_count": 0,
                    "selected_count": len(selected),
                    "selection_policy": "one highest-confidence representative frame per uncertain track or detector-classified Drone/UAV track",
                    "crop_padding_fraction": 1.0,
                    "needs_review": True,
                    "fusion_applied": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report_phase("vlm", "skipped", message=skip_reason)
        set_stage("vlm_skipped", 78)
    elif not selected:
        skip_reason = "No uncertain tracks or detector-classified drone candidates had usable evidence."
        vlm_manifest.write_text(
            json.dumps(
                {
                    "status": "skipped",
                    "reason": skip_reason,
                    "verified_count": 0,
                    "needs_review": True,
                    "selection_policy": "one representative evidence frame per uncertain track or detector-classified drone candidate",
                    "crop_padding_fraction": 1.0,
                    "fusion_applied": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report_phase("vlm", "skipped", message=skip_reason)
        set_stage("vlm_skipped", 78)
    else:
        set_stage("vlm", 76)
        report_phase(
            "vlm",
            "processing",
            0,
            len(selected),
            f"0 / {len(selected)} representative track frames verified",
        )
        try:
            # Require cached weights; a dashboard run must never start a hidden download.
            vlm = SmolVLM(local_files_only=True)
            vlm_results = write_vlm_results(
                selected,
                vlm,
                vlm_csv,
                evidence_root,
                check_cancelled=check_cancelled,
                progress_callback=lambda current, total: report_phase(
                    "vlm",
                    "processing",
                    current,
                    total,
                    f"{current} / {total} representative track frames verified",
                ),
            )
            vlm_manifest.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "verified_count": len(vlm_results),
                        "selected_count": len(selected),
                        "selection_policy": "one highest-confidence representative frame per uncertain track or detector-classified drone candidate",
                        "crop_padding_fraction": 1.0,
                        "model_id": vlm.model_id,
                        "device": vlm.device,
                        "local_files_only": True,
                        "needs_review": True,
                        "confidence_note": "Generated-token confidence is an uncalibrated proxy; VLM output is advisory evidence, not ground truth.",
                        "fusion_applied": False,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            report_phase(
                "vlm",
                "completed",
                len(vlm_results),
                len(selected),
                f"{len(vlm_results)} / {len(selected)} representative track frames verified; results require review",
            )
            set_stage("vlm_completed", 80)
        except RunCancelled:
            raise
        except Exception as error:
            # VLM is advisory: its failure must not discard detector/tracker output.
            _write_empty_csv(
                vlm_csv,
                ("track_id", "frame", "crop_path", "label", "confidence", "reason"),
            )
            skip_reason = (
                "VLM unavailable; detector/temporal output remains unverified: "
                f"{type(error).__name__}: {error}"
            )
            vlm_manifest.write_text(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": skip_reason,
                        "verified_count": 0,
                        "selected_count": len(selected),
                        "selection_policy": "one representative evidence frame per uncertain track or detector-classified drone candidate",
                        "crop_padding_fraction": 1.0,
                        "local_files_only": True,
                        "needs_review": True,
                        "fusion_applied": False,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            report_phase("vlm", "skipped", message=skip_reason)
            set_stage("vlm_skipped", 78)

    check_cancelled()
    modalities_root = run_root / "modalities"
    thermal_coverage = modalities_root / "thermal_coverage.csv"
    thermal_manifest = modalities_root / "thermal_frame_manifest.csv"
    radar_summary = modalities_root / "radar_validation_summary.csv"
    _write_empty_csv(
        thermal_coverage,
        (
            "track_id",
            "thermal_frame_available",
            "thermal_coverage_status",
            "thermal_spatial_crop_available",
            "synchronized_rgb_observations",
        ),
    )
    _write_empty_csv(
        thermal_manifest,
        (
            "track_id",
            "source",
            "rgb_video_frame",
            "synchronization_status",
            "thermal_frame_path",
            "thermal_frame_available",
            "thermal_spatial_crop_available",
        ),
    )
    _write_empty_csv(
        radar_summary,
        (
            "sample_index",
            "rd_matrix_available",
            "radar_timestamp_available",
            "caltech_recording_id_available",
            "synchronization_metadata_available",
            "track24_association",
        ),
    )

    set_stage("fusion", 88)
    fusion_root = run_root / "fusion"
    fusion_csv = fusion_root / "fusion_results.csv"
    availability_csv = fusion_root / "evidence_availability.csv"
    report_txt = fusion_root / "fusion_report.txt"
    run_phase5_fusion(
        tracks_csv,
        temporal_csv,
        reliability_csv,
        vlm_fusion_csv,
        thermal_coverage,
        radar_summary,
        fusion_csv,
        availability_csv,
        report_txt,
        temporal_consensus_csv=consensus_csv,
        progress_callback=lambda current, total: report_phase(
            "fusion",
            "processing",
            current,
            total,
            f"{current} / {total} tracks fused",
        ),
    )
    fusion_progress = getattr(
        getattr(set_stage, "record", None), "phase_progress", {}
    ).get("fusion", {})
    report_phase(
        "fusion",
        "completed",
        fusion_progress.get("current"),
        fusion_progress.get("total"),
        fusion_progress.get("message"),
    )

    # Phase 10: persist final per-track decisions and alert flag to final_results.json
    report_phase("assessment", "processing", message="Generating final assessment...")
    begin_finalization = getattr(set_stage, "begin_finalization", None)
    if begin_finalization is not None:
        begin_finalization()
    check_cancelled()
    _write_final_results(run_root, fusion_csv)

    data = load_run_project_data(project_root, run_root)
    report_phase("assessment", "completed", message="Final assessment generated ✓")
    return data


def _write_final_results(run_root: Path, fusion_csv: Path) -> None:
    """Write final_results.json from the completed fusion CSV (Phase 10 persistence)."""
    from datetime import datetime, timezone

    tracks: list[dict] = []
    uav_alert = False
    if fusion_csv.is_file():
        with fusion_csv.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                final_label = row.get("final_label", "")
                try:
                    confidence = float(row.get("final_confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                if final_label == "UAV":
                    uav_alert = True
                tracks.append(
                    {
                        "track_id": row.get("track_id", ""),
                        "detector_label": row.get("detector_label", ""),
                        "final_label": final_label,
                        "final_confidence": confidence,
                        "fusion_mode": row.get("fusion_mode", ""),
                        "reliability_status": row.get("reliability_status", ""),
                        "review_status": (
                            "review_recommended"
                            if row.get("fusion_mode") == "detector_temporal_abstain"
                            or final_label == "Other"
                            else "unverified"
                        ),
                    }
                )
    _finalize_early_uav_alert(
        run_root,
        final_uav_track_ids={
            str(track["track_id"])
            for track in tracks
            if track.get("final_label") == "UAV"
        },
    )
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uav_alert": uav_alert,
        "alert_level": "REVIEW" if uav_alert else None,
        "alert_message": (
            "The detector/temporal pipeline flagged a possible UAV. This result has not been independently verified; review the evidence before acting."
            if uav_alert
            else None
        ),
        "track_count": len(tracks),
        "tracks": tracks,
    }
    tmp = run_root / "final_results.json.tmp"
    tmp.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(run_root / "final_results.json")
    LOGGER.info(
        "Phase 10: final_results.json written for %s (uav_alert=%s)",
        run_root.name,
        uav_alert,
    )
