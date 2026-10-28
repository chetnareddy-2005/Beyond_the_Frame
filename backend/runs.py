"""Run-scoped upload storage, status persistence, and background execution."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)
RUN_ID_PATTERN = re.compile(r"RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}\Z")
VIDEO_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_VIDEO_INDEX_NAME = "source_video_ids.json"
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
STATUS_PROGRESS = {
    "uploaded": 0,
    "detection": 8,
    "tracking": 25,
    "temporal": 38,
    "reliability": 50,
    "evidence": 63,
    "vlm": 78,
    "vlm_skipped": 78,
    "vlm_completed": 80,
    "fusion": 90,
    "completed": 100,
}
VLM_STATUSES = {"pending", "processing", "completed", "skipped", "failed", "unknown"}
PHASES = ("detection", "tracking", "temporal", "reliability", "evidence", "vlm", "fusion", "assessment")


class RunError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


class RunCancelled(Exception):
    """Raised by a run worker when its active run has been cancelled."""


class RunExecutionContext:
    def __init__(self, manager: "RunManager", record: "RunRecord"):
        self.manager = manager
        self.record = record

    def __call__(self, stage: str, progress: int) -> None:
        self.check_cancelled()
        self.manager._set_stage(self.record, stage, progress)

    def check_cancelled(self) -> None:
        if self.record.cancel_event.is_set():
            raise RunCancelled("Analysis cancelled by user")

    def update_phase_progress(
        self,
        phase: str,
        status: str,
        *,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        if phase not in PHASES or status not in {"pending", "processing", "completed", "failed", "skipped"}:
            raise ValueError("Invalid phase progress update")
        if current is not None and (current < 0 or total is None or current > total):
            raise ValueError("Invalid phase progress counts")
        if total is not None and total < 0:
            raise ValueError("Invalid phase progress total")
        with self.manager._lock:
            self.check_cancelled()
            progress: dict[str, Any] = {"status": status}
            if current is not None:
                progress.update(current=current, total=total)
            if message:
                progress["message"] = message
            self.record.phase_progress[phase] = progress

    def set_process(self, process: subprocess.Popen[str]) -> None:
        with self.manager._lock:
            self.record.process = process
            cancelled = self.record.cancel_event.is_set()
        if cancelled:
            _terminate_process(process)
            raise RunCancelled("Analysis cancelled by user")

    def clear_process(self, process: subprocess.Popen[str]) -> None:
        with self.manager._lock:
            if self.record.process is process:
                self.record.process = None

    def begin_finalization(self) -> None:
        with self.manager._lock:
            self.check_cancelled()
            self.record.finalizing = True

    def run_if_active(self, action: Callable[[], Any]) -> Any:
        with self.manager._lock:
            self.check_cancelled()
            return action()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_seconds(started_at: str | None, completed_at: str | None) -> float | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at) if completed_at else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return max(0.0, (completed - started).total_seconds())


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        if process.poll() is None:
            process.kill()
            process.wait()
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _safe_upload_extension(filename: str) -> str:
    if (
        not filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or Path(filename).is_absolute()
        or PureWindowsPath(filename).is_absolute()
        or PureWindowsPath(filename).drive
        or filename in {".", ".."}
    ):
        raise RunError(400, "unsafe_filename", "Upload filename must be a plain video filename")
    extension = Path(filename).suffix.casefold()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise RunError(415, "unsupported_video_type", "Supported video extensions are mp4, avi, mov, and mkv")
    return extension


@dataclass
class RunRecord:
    run_id: str
    root: Path
    input_filename: str
    created_at: str
    original_filename: str | None = None
    status: str = "queued"
    stage: str = "uploaded"
    vlm_status: str = "pending"
    progress: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    source_type: str | None = None
    input_type: str | None = None
    input_status: str | None = None
    input_metadata: dict[str, Any] = field(default_factory=dict)
    input_reference: str | None = None
    source_video_sha256: str | None = None
    source_video_id: str | None = None
    owner_id: int = 1
    resume_after_tracking: bool = False
    project_data: Any = None
    thread: threading.Thread | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    finalizing: bool = field(default=False, repr=False)
    phase_progress: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    @property
    def input_path(self) -> Path:
        """Absolute path to the uploaded input video file."""
        return self.root / "input" / self.input_filename

    def as_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "stage": self.stage,
            "vlm_status": self.vlm_status,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "source_type": self.source_type,
            "input_type": self.input_type,
            "input_status": self.input_status,
            "input_metadata": self.input_metadata,
            "input_reference": self.input_reference,
            "input_filename": self.input_filename,
            "original_filename": self.original_filename,
            "source_video_id": self.source_video_id,
            "analysis_duration_seconds": _duration_seconds(self.started_at, self.completed_at),
            "resume_after_tracking": self.resume_after_tracking,
            "phase_progress": self.phase_progress,
        }


class RunManager:
    def __init__(
        self,
        project_root: Path,
        max_upload_bytes: int,
        runner: Callable[[Path, Path, Path, Callable[[str, int], None]], Any],
        runs_root: Path | None = None,
    ):
        self.project_root = project_root.resolve()
        self._allow_external_runs_root = runs_root is not None
        self.runs_root = (
            runs_root.resolve()
            if runs_root is not None
            else self.project_root / "results" / "dashboard_runs"
        )
        self.max_upload_bytes = max_upload_bytes
        self.runner = runner
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._source_video_ids: dict[str, str] = {}
        self._load_source_video_ids()
        self._load_existing()
        self._migrate_source_video_ids()

    def _load_source_video_ids(self) -> None:
        self._resolved_runs_root()
        index_path = self.runs_root / SOURCE_VIDEO_INDEX_NAME
        if index_path.is_symlink():
            raise ValueError("Source-video identity index must not be a symlink")
        if not index_path.exists():
            return
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            mapping = payload["videos"]
            if payload.get("version") != 1 or not isinstance(mapping, dict):
                raise ValueError("Unsupported source-video identity index")
            if any(
                not isinstance(source_hash, str)
                or not VIDEO_HASH_PATTERN.fullmatch(source_hash)
                or not isinstance(video_id, str)
                or not re.fullmatch(r"V-[1-9][0-9]*", video_id)
                for source_hash, video_id in mapping.items()
            ):
                raise ValueError("Invalid source-video identity index entries")
            if len(set(mapping.values())) != len(mapping):
                raise ValueError("Duplicate IDs in source-video identity index")
            self._source_video_ids = dict(mapping)
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("Could not load source-video identity index") from error

    def _persist_source_video_ids(self) -> None:
        self._resolved_runs_root()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        index_path = self.runs_root / SOURCE_VIDEO_INDEX_NAME
        if index_path.is_symlink():
            raise ValueError("Source-video identity index must not be a symlink")
        temporary = index_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"version": 1, "videos": self._source_video_ids},
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        temporary.replace(index_path)

    def _video_id_for_hash(self, source_hash: str) -> str:
        if not VIDEO_HASH_PATTERN.fullmatch(source_hash):
            raise ValueError("Source video SHA-256 must be a 64-character lowercase hex digest")
        video_id = self._source_video_ids.get(source_hash)
        if video_id is not None:
            return video_id
        next_number = max(
            (int(existing_id.removeprefix("V-")) for existing_id in self._source_video_ids.values()),
            default=0,
        ) + 1
        video_id = f"V-{next_number}"
        self._source_video_ids[source_hash] = video_id
        self._persist_source_video_ids()
        return video_id

    def _migrate_source_video_ids(self) -> None:
        ordered_runs = sorted(
            self._runs.values(),
            key=lambda record: (record.created_at, record.run_id),
        )
        for record in ordered_runs:
            source_hash = record.source_video_sha256
            original_hash = source_hash
            if not isinstance(source_hash, str) or not VIDEO_HASH_PATTERN.fullmatch(source_hash):
                if not record.input_path.is_file():
                    LOGGER.warning("Source video is unavailable for run %s; no video ID assigned", record.run_id)
                    continue
                digest = hashlib.sha256()
                with record.input_path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                source_hash = digest.hexdigest()
                record.source_video_sha256 = source_hash
            video_id = self._video_id_for_hash(source_hash)
            if original_hash != source_hash or record.source_video_id != video_id:
                record.source_video_id = video_id
                self._update_run_manifest(
                    record,
                    source_video_sha256=source_hash,
                    source_video_id=video_id,
                )
            else:
                record.source_video_id = video_id

    def _resolved_runs_root(self) -> Path:
        results_root = self.project_root / "results"
        if (
            not self._allow_external_runs_root
            and (results_root.is_symlink() or self.runs_root.is_symlink())
        ):
            raise ValueError("Dashboard run storage must not use symlinked result directories")
        resolved = self.runs_root.resolve()
        if not self._allow_external_runs_root:
            try:
                resolved.relative_to(results_root.resolve())
            except ValueError as error:
                raise ValueError("Dashboard run storage must remain under project results") from error
        return resolved

    def _load_existing(self) -> None:
        if not self.runs_root.is_dir():
            return
        runs_root = self._resolved_runs_root()
        for directory in runs_root.iterdir():
            if not directory.is_dir() or not RUN_ID_PATTERN.fullmatch(directory.name):
                continue
            try:
                resolved = directory.resolve()
                resolved.relative_to(runs_root)
                manifest = json.loads((resolved / "run_manifest.json").read_text(encoding="utf-8"))
                status = json.loads((resolved / "status.json").read_text(encoding="utf-8"))
                if manifest.get("run_id") != directory.name:
                    continue
                state = status.get("status")
                if state not in {"prepared", "queued", "processing", "completed", "failed", "cancelled"}:
                    continue
                vlm_status = status.get(
                    "vlm_status", "unknown" if state == "completed" else "pending"
                )
                if vlm_status not in VLM_STATUSES:
                    raise ValueError("Invalid VLM run status")
                record = RunRecord(
                    run_id=directory.name,
                    root=resolved,
                    input_filename=manifest["input_filename"],
                    created_at=manifest["created_at"],
                    original_filename=manifest.get("original_filename"),
                    status=state,
                    stage=status.get("stage", "uploaded"),
                    vlm_status=vlm_status,
                    progress=int(status.get("progress", 0)),
                    started_at=status.get("started_at"),
                    completed_at=status.get("completed_at"),
                    error=status.get("error"),
                    source_type=manifest.get("source_type"),
                    input_type=manifest.get("input_type"),
                    input_status=manifest.get("input_status"),
                    input_metadata=manifest.get("input_metadata", {}),
                    input_reference=manifest.get("input_reference"),
                    source_video_sha256=manifest.get("source_video_sha256"),
                    source_video_id=manifest.get("source_video_id"),
                    owner_id=int(manifest.get("owner_id", 1)),
                    resume_after_tracking=manifest.get("resume_after_tracking") is True,
                    phase_progress=status.get("phase_progress", {}),
                )
                if record.status == "processing":
                    record.status = "failed"
                    record.stage = "failed"
                    if record.vlm_status == "processing":
                        record.vlm_status = "unknown"
                    record.error = "Backend restarted while processing; start a new run"
                    record.completed_at = _now()
                    self._persist(record)
                self._runs[record.run_id] = record
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                LOGGER.warning("Ignoring invalid dashboard run metadata in %s", directory.name)

    def _persist(self, record: RunRecord) -> None:
        record.root.mkdir(parents=True, exist_ok=True)
        status_tmp = record.root / "status.json.tmp"
        status_tmp.write_text(
            json.dumps(record.as_json(), indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        status_tmp.replace(record.root / "status.json")
        manifest_path = record.root / "run_manifest.json"
        if not manifest_path.exists():
            manifest = {
                "run_id": record.run_id,
                "input_filename": record.input_filename,
                "original_filename": record.original_filename,
                "result_root": ".",
                "created_at": record.created_at,
                "source_type": record.source_type,
                "input_type": record.input_type,
                "input_status": record.input_status,
                "input_metadata": record.input_metadata,
                "input_reference": record.input_reference,
                "owner_id": record.owner_id,
                "resume_after_tracking": record.resume_after_tracking,
            }
            manifest_tmp = record.root / "run_manifest.json.tmp"
            manifest_tmp.write_text(
                json.dumps(manifest, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            manifest_tmp.replace(manifest_path)

    def _update_run_manifest(self, record: RunRecord, **metadata: Any) -> None:
        manifest_path = record.root / "run_manifest.json"
        manifest: dict[str, Any]
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                manifest = {}
        else:
            manifest = {}
        manifest.setdefault("run_id", record.run_id)
        manifest.setdefault("input_filename", record.input_filename)
        manifest.setdefault("original_filename", record.original_filename)
        manifest.setdefault("result_root", ".")
        manifest.setdefault("created_at", record.created_at)
        for key, value in metadata.items():
            manifest[key] = value
        manifest_tmp = record.root / "run_manifest.json.tmp"
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_tmp.replace(manifest_path)

    def record_input_state(self, **state: Any) -> Path:
        input_id = secrets.token_hex(16)
        runs_root = self._resolved_runs_root()
        directory = runs_root / "input_states"
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise RunError(500, "input_storage_error", "Could not safely create input state storage.")
        try:
            directory.resolve().relative_to(runs_root)
        except ValueError as error:
            raise RunError(500, "input_storage_error", "Could not safely create input state storage.") from error
        target = directory / f"{input_id}.json"
        temporary = directory / f"{input_id}.json.tmp"
        temporary.write_text(
            json.dumps(
                {"input_id": input_id, "created_at": _now(), **state},
                indent=2,
                allow_nan=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    @staticmethod
    def update_input_state(path: Path, **state: Any) -> None:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update(state)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, allow_nan=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def create_run(
        self,
        filename: str,
        content: bytes,
        *,
        source_type: str | None = None,
        input_type: str | None = None,
        input_status: str | None = None,
        input_metadata: dict[str, Any] | None = None,
        input_reference: str | None = None,
        original_filename: str | None = None,
        prepared: bool = False,
        owner_id: int = 1,
        resume_after_tracking: bool = False,
    ) -> RunRecord:
        extension = _safe_upload_extension(filename)
        if not content:
            raise RunError(400, "empty_upload", "Uploaded video is empty")
        if len(content) > self.max_upload_bytes:
            raise RunError(413, "upload_too_large", "Uploaded video exceeds the configured size limit")
        with self._lock:
            self.runs_root.mkdir(parents=True, exist_ok=True)
            runs_root = self._resolved_runs_root()
            for _ in range(5):
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                run_id = f"RUN_{timestamp}_{secrets.token_hex(12)}"
                root = self.runs_root / run_id
                try:
                    root.mkdir()
                    root.resolve().relative_to(runs_root)
                    break
                except FileExistsError:
                    continue
                except ValueError as error:
                    raise RunError(500, "run_storage_error", "Could not safely create run storage") from error
            else:
                raise RunError(500, "run_storage_error", "Could not allocate a unique run ID")
            input_dir = root / "input"
            input_dir.mkdir()
            input_filename = f"input_video{extension}"
            try:
                with (input_dir / input_filename).open("xb") as output:
                    output.write(content)
                source_hash = hashlib.sha256(content).hexdigest()
                record = RunRecord(
                    run_id=run_id,
                    root=root.resolve(),
                    input_filename=input_filename,
                    created_at=_now(),
                    original_filename=original_filename or filename,
                    status="prepared" if prepared else "queued",
                    source_type=source_type,
                    input_type=input_type,
                    input_status=input_status,
                    input_metadata=input_metadata or {},
                    input_reference=input_reference or f"runs/{run_id}/input/{input_filename}",
                    source_video_sha256=source_hash,
                    source_video_id=self._video_id_for_hash(source_hash),
                    owner_id=owner_id,
                    resume_after_tracking=resume_after_tracking,
                )
                self._persist(record)
                self._update_run_manifest(
                    record,
                    source_video_sha256=source_hash,
                    source_video_id=record.source_video_id,
                )
                self._runs[run_id] = record
                return record
            except Exception:
                shutil.rmtree(root, ignore_errors=True)
                raise

    def get_owned(self, run_id: str, owner_id: int) -> RunRecord:
        record = self.get(run_id)
        if record.owner_id != owner_id:
            raise RunError(404, "run_not_found", f"Run {run_id} does not exist")
        return record

    def reuse_candidates(self, record: RunRecord, owner_id: int) -> list[dict[str, Any]]:
        try:
            current_manifest = json.loads(
                (record.root / "run_manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise RunError(500, "run_manifest_unavailable", "Run compatibility metadata is unavailable.") from error
        source_hash = current_manifest.get("source_video_sha256")
        if not isinstance(source_hash, str) or not source_hash:
            return []
        candidates = []
        for candidate in self.list_runs(owner_id):
            if candidate.run_id == record.run_id or candidate.status != "completed":
                continue
            try:
                manifest = json.loads(
                    (candidate.root / "run_manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                LOGGER.warning("Skipping run %s with unreadable compatibility metadata", candidate.run_id)
                continue
            if manifest.get("source_video_sha256") != source_hash:
                continue
            has_persisted_results = (
                (candidate.root / "final_results.json").is_file()
                and (candidate.root / "tracking" / "detector" / "tracks.csv").is_file()
            )
            if not has_persisted_results:
                continue
            candidates.append({
                "run_id": candidate.run_id,
                "status": candidate.status,
                "created_at": candidate.created_at,
                "completed_at": candidate.completed_at,
                "analysis_duration_seconds": _duration_seconds(candidate.started_at, candidate.completed_at),
                "input_filename": candidate.input_filename,
                "reuse_mode": "read_only_completed_results",
            })
        return candidates

    def resume_from_tracking(self, run_id: str, owner_id: int) -> RunRecord:
        source, tracks_path, detector_manifest_path, current_configuration = (
            self._validated_tracking_checkpoint(run_id, owner_id)
        )
        resumed = self.create_run(
            source.input_filename,
            source.input_path.read_bytes(),
            original_filename=source.original_filename or source.input_filename,
            source_type=source.source_type,
            input_type=source.input_type,
            input_status=source.input_status,
            input_metadata=source.input_metadata,
            prepared=True,
            owner_id=owner_id,
            resume_after_tracking=True,
        )
        detector_output = resumed.root / "tracking" / "detector"
        detector_output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tracks_path, detector_output / "tracks.csv")
        shutil.copy2(detector_manifest_path, resumed.root / "detector_manifest.json")
        self._update_run_manifest(
            resumed,
            **current_configuration,
            resume_configuration=current_configuration,
            resumed_from_run_id=source.run_id,
            resume_after_tracking=True,
        )
        return resumed

    def resume_checkpoint(self, run_id: str, owner_id: int) -> dict[str, Any]:
        try:
            self._validated_tracking_checkpoint(run_id, owner_id)
        except RunError as error:
            return {"available": False, "reason": error.message}
        return {"available": True, "stage": "tracking"}

    def _validated_tracking_checkpoint(
        self, run_id: str, owner_id: int
    ) -> tuple[RunRecord, Path, Path, dict[str, object]]:
        source = self.get_owned(run_id, owner_id)
        if source.status not in {"failed", "cancelled"}:
            raise RunError(409, "run_not_resumable", "Only failed or cancelled runs can resume from a checkpoint.")
        if source.source_type != "upload" or source.input_type != "rgb":
            raise RunError(
                409,
                "run_not_resumable",
                "Only uploaded RGB analysis runs can resume from a checkpoint.",
            )
        if any(
            source.phase_progress.get(phase, {}).get("status") != "completed"
            for phase in ("detection", "tracking")
        ):
            raise RunError(
                409,
                "checkpoint_unavailable",
                "This run has no completed detector/tracker checkpoint; use fresh analysis instead.",
            )
        tracks_path = source.root / "tracking" / "detector" / "tracks.csv"
        detector_manifest_path = source.root / "detector_manifest.json"
        if (
            not source.input_path.is_file()
            or not tracks_path.is_file()
            or not detector_manifest_path.is_file()
        ):
            raise RunError(
                409,
                "checkpoint_unavailable",
                "The detector/tracker checkpoint artifacts are incomplete.",
            )
        try:
            source_manifest = json.loads(
                (source.root / "run_manifest.json").read_text(encoding="utf-8")
            )
            detector_manifest = json.loads(
                detector_manifest_path.read_text(encoding="utf-8")
            )
            if not isinstance(source_manifest, dict) or not isinstance(detector_manifest, dict):
                raise ValueError("Checkpoint metadata must be JSON objects")
            from backend.run_pipeline import run_reproducibility_metadata

            current_configuration = run_reproducibility_metadata(
                self.project_root, source.input_path
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, FileNotFoundError) as error:
            raise RunError(
                409,
                "checkpoint_incompatible",
                "The current source or detector configuration cannot validate this checkpoint.",
            ) from error
        if source_manifest.get("run_id") != source.run_id:
            raise RunError(
                409,
                "checkpoint_incompatible",
                "The stored checkpoint metadata does not belong to this run.",
            )
        if any(source_manifest.get(key) != value for key, value in current_configuration.items()):
            raise RunError(
                409,
                "checkpoint_incompatible",
                "The uploaded video or analysis configuration differs from this checkpoint.",
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
            detector_manifest.get(key) != current_configuration.get(key)
            for key in detector_keys
        ):
            raise RunError(
                409,
                "checkpoint_incompatible",
                "The stored detector artifacts do not match the current detector configuration.",
            )
        self._validate_tracks_checkpoint(tracks_path)
        return source, tracks_path, detector_manifest_path, current_configuration

    @staticmethod
    def _validate_tracks_checkpoint(tracks_path: Path) -> None:
        required = {"frame", "track_id", "class_name", "confidence", "x1", "y1", "x2", "y2"}
        with tracks_path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if not required.issubset(reader.fieldnames or ()):
                raise RunError(409, "checkpoint_invalid", "The detector checkpoint has an invalid CSV schema.")
            count = 0
            for row in reader:
                try:
                    frame = int(row["frame"])
                    track_id = int(row["track_id"])
                    confidence = float(row["confidence"])
                    bbox = [float(row[key]) for key in ("x1", "y1", "x2", "y2")]
                except (TypeError, ValueError) as error:
                    raise RunError(409, "checkpoint_invalid", "The detector checkpoint contains invalid rows.") from error
                if (
                    frame < 0
                    or track_id < 0
                    or not row["class_name"]
                    or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1
                    or not all(math.isfinite(value) for value in bbox)
                    or bbox[2] < bbox[0]
                    or bbox[3] < bbox[1]
                ):
                    raise RunError(409, "checkpoint_invalid", "The detector checkpoint contains invalid rows.")
                count += 1
        if not count:
            raise RunError(409, "checkpoint_invalid", "The detector checkpoint contains no observations.")

    def get(self, run_id: str) -> RunRecord:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise RunError(400, "malformed_run_id", "run_id has an invalid format")
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunError(404, "run_not_found", f"Run {run_id} does not exist")
        return record

    def start(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self.get(run_id)
            if record.status not in {"queued", "prepared"}:
                raise RunError(409, "run_already_started", "Run is not in a startable state")
            if record.status == "prepared" and (
                record.source_type != "upload"
                or record.input_type not in {"rgb", "thermal"}
                or record.input_status != "validated"
            ):
                raise RunError(
                    409,
                    "input_not_supported",
                    "The analysis pipeline can currently be started only for prepared RGB or Thermal video.",
                )
            if any(item.status == "processing" for item in self._runs.values()):
                raise RunError(409, "another_run_processing", "Another video run is already processing")
            record.status = "processing"
            record.stage = "temporal" if record.resume_after_tracking else "detection"
            record.progress = 34 if record.resume_after_tracking else STATUS_PROGRESS[record.stage]
            record.started_at = _now()
            record.phase_progress = {
                phase: {"status": "pending", "message": "Waiting for previous phase..."}
                for phase in PHASES
            }
            if record.resume_after_tracking:
                for phase in ("detection", "tracking"):
                    record.phase_progress[phase] = {
                        "status": "completed",
                        "message": "Reused validated detector/tracker checkpoint",
                    }
            self._persist(record)
            worker = threading.Thread(
                target=self._execute,
                args=(record,),
                name=f"video-run-{record.run_id}",
                daemon=True,
            )
            record.thread = worker
            worker.start()
            return record

    def cancel(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self.get(run_id)
            if record.status == "cancelled":
                return record
            if record.status != "processing":
                raise RunError(409, "run_not_processing", "Only a processing run can be cancelled.")
            if record.finalizing:
                raise RunError(409, "run_finalizing", "The run is already finalizing and cannot be cancelled.")
            cancellation_already_requested = record.cancel_event.is_set()
            record.cancel_event.set()
            process = None if cancellation_already_requested else record.process
            worker = record.thread

        if process is not None and process.poll() is None:
            try:
                _terminate_process(process)
            except OSError as error:
                raise RunError(
                    500,
                    "cancellation_failed",
                    "The active detector process could not be stopped safely.",
                ) from error

        if worker is not None and worker is not threading.current_thread():
            worker.join()
        with self._lock:
            if record.status == "processing":
                record.status = "cancelled"
                record.stage = "cancelled"
                record.completed_at = _now()
                record.error = "Analysis cancelled by user."
                if record.vlm_status == "processing":
                    record.vlm_status = "unknown"
                self._persist(record)
            return record

    def _set_stage(self, record: RunRecord, stage: str, progress: int) -> None:
        if stage not in STATUS_PROGRESS or stage in {"uploaded", "completed"}:
            raise ValueError(f"Unsupported run stage: {stage}")
        if not 0 <= progress <= 99:
            raise ValueError("Run progress must be between 0 and 99 before completion")
        if stage == "vlm_completed":
            vlm_path = record.root / "vlm" / "vlm_results.csv"
            if not vlm_path.is_file():
                raise ValueError("VLM cannot be completed without run-scoped result rows")
            with vlm_path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                required = {"track_id", "frame", "label", "confidence"}
                rows = list(reader)
                if not required.issubset(reader.fieldnames or ()) or not rows:
                    raise ValueError("VLM cannot be completed without valid result rows")
                for row in rows:
                    try:
                        frame = int(row["frame"])
                        confidence = float(row["confidence"])
                    except (TypeError, ValueError) as error:
                        raise ValueError("VLM result rows must contain a frame and confidence") from error
                    if (
                        not row["track_id"]
                        or frame < 0
                        or not row["label"]
                        or not math.isfinite(confidence)
                        or not 0 <= confidence <= 1
                    ):
                        raise ValueError("VLM result rows contain invalid inference output")
        elif stage == "vlm_skipped":
            vlm_path = record.root / "vlm" / "vlm_results.csv"
            if vlm_path.is_file():
                with vlm_path.open(newline="", encoding="utf-8-sig") as source:
                    if next(csv.DictReader(source), None) is not None:
                        raise ValueError("VLM cannot be skipped when run-scoped result rows exist")
        with self._lock:
            if record.cancel_event.is_set():
                raise RunCancelled("Analysis cancelled by user")
            if stage in {"vlm_skipped", "vlm_completed"}:
                record.stage = "vlm"
                record.vlm_status = "skipped" if stage == "vlm_skipped" else "completed"
                existing_progress = record.phase_progress.get("vlm", {})
                record.phase_progress["vlm"] = {
                    **existing_progress,
                    "status": "skipped" if stage == "vlm_skipped" else "completed",
                    **(
                        {
                            "message": existing_progress.get("message")
                            or "VLM was skipped; detector/temporal labels remain unverified."
                        }
                        if stage == "vlm_skipped"
                        else {}
                    ),
                }
            else:
                record.stage = stage
                existing_progress = record.phase_progress.get(stage, {})
                if (
                    stage in PHASES
                    and existing_progress.get("status") not in {"completed", "skipped"}
                ):
                    record.phase_progress[stage] = {"status": "processing"}
                if stage == "vlm":
                    record.vlm_status = "processing"
            record.progress = progress
            self._persist(record)

    def _execute(self, record: RunRecord) -> None:
        input_video = record.root / "input" / record.input_filename
        context = RunExecutionContext(self, record)
        try:
            context.check_cancelled()
            from backend.run_pipeline import run_reproducibility_metadata

            self._update_run_manifest(
                record,
                **run_reproducibility_metadata(
                    self.project_root,
                    input_video,
                    input_type=record.input_type,
                ),
            )
            context.check_cancelled()
            data = self.runner(
                self.project_root,
                record.root,
                input_video,
                context,
            )
            with self._lock:
                if record.cancel_event.is_set():
                    raise RunCancelled("Analysis cancelled by user")
                context.check_cancelled()
                record.project_data = data
                record.status = "completed"
                record.stage = "completed"
                if record.vlm_status == "pending":
                    record.vlm_status = (
                        "skipped" if record.input_type == "thermal" else "unknown"
                    )
                record.progress = 100
                record.completed_at = _now()
                for phase in PHASES:
                    phase_result = record.phase_progress.get(phase, {})
                    if phase_result.get("status") != "skipped":
                        record.phase_progress[phase] = {
                            **phase_result,
                            "status": "completed",
                        }
                self._persist(record)
        except RunCancelled:
            with self._lock:
                if record.stage in PHASES:
                    record.phase_progress[record.stage] = {
                        "status": "skipped",
                        "message": "Cancelled",
                    }
                record.status = "cancelled"
                record.stage = "cancelled"
                record.completed_at = _now()
                record.error = "Analysis cancelled by user."
                if record.vlm_status == "processing":
                    record.vlm_status = "unknown"
                self._persist(record)
        except Exception as error:
            LOGGER.exception("Dashboard run %s failed during %s", record.run_id, record.stage)
            with self._lock:
                if record.cancel_event.is_set():
                    if record.stage in PHASES:
                        record.phase_progress[record.stage] = {
                            "status": "skipped",
                            "message": "Cancelled",
                        }
                    record.status = "cancelled"
                    record.stage = "cancelled"
                    record.completed_at = _now()
                    record.error = "Analysis cancelled by user."
                    if record.vlm_status == "processing":
                        record.vlm_status = "unknown"
                    self._persist(record)
                    return
                if record.vlm_status == "processing":
                    record.vlm_status = "failed"
                if record.stage in PHASES:
                    record.phase_progress[record.stage] = {
                        "status": "failed",
                        "message": "Phase failed",
                    }
                record.status = "failed"
                record.stage = "failed"
                record.progress = min(record.progress, 99)
                record.completed_at = _now()
                record.error = f"{type(error).__name__}: processing failed; review server logs"
                self._persist(record)
        finally:
            self._fail_if_worker_exited(record)

    def _fail_if_worker_exited(self, record: RunRecord) -> None:
        with self._lock:
            if record.status != "processing":
                return
            LOGGER.error(
                "Dashboard run %s worker exited before reaching a terminal state",
                record.run_id,
            )
            if record.vlm_status == "processing":
                record.vlm_status = "failed"
            record.status = "failed"
            record.stage = "failed"
            record.progress = min(record.progress, 99)
            record.completed_at = _now()
            record.error = (
                "Worker exited before reaching a terminal run state; "
                "underlying cause is unavailable"
            )
            self._persist(record)

    def evidence_path(self, run_id: str, relative_path: str) -> Path:
        record = self.get(run_id)
        if not relative_path or "\\" in relative_path:
            raise RunError(400, "invalid_evidence_path", "Evidence path must be relative")
        relative = Path(relative_path)
        if relative.is_absolute() or relative.drive or any(part in {"", ".", ".."} for part in relative.parts):
            raise RunError(400, "invalid_evidence_path", "Evidence path must remain within this run")
        evidence_root = (record.root / "evidence").resolve()
        candidate = (evidence_root / relative).resolve()
        try:
            candidate.relative_to(evidence_root)
        except ValueError as error:
            raise RunError(400, "invalid_evidence_path", "Evidence path must remain within this run") from error
        if candidate.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise RunError(404, "evidence_not_found", "Evidence image does not exist")
        if not candidate.is_file():
            raise RunError(404, "evidence_not_found", "Evidence image does not exist")
        return candidate

    def project_data(self, record: RunRecord) -> Any:
        if record.status != "completed":
            raise RunError(409, "run_results_unavailable", "Run results are not available yet")
        with self._lock:
            if record.project_data is not None:
                return record.project_data
            from backend.run_pipeline import load_run_project_data

            record.project_data = load_run_project_data(self.project_root, record.root)
            return record.project_data

    def list_runs(self, owner_id: int | None = None) -> list["RunRecord"]:
        """Return all known runs, newest first."""
        with self._lock:
            records = self._runs.values()
            if owner_id is not None:
                records = (record for record in records if record.owner_id == owner_id)
            return sorted(records, key=lambda r: r.created_at, reverse=True)

    def delete_video_runs(self, source_video_id: str, owner_id: int) -> list[str]:
        """Delete all stored runs for one source video owned by the user."""
        if not re.fullmatch(r"V-[1-9][0-9]*", source_video_id):
            raise RunError(400, "malformed_video_id", "source_video_id has an invalid format.")
        with self._lock:
            records = [
                record for record in self._runs.values()
                if record.owner_id == owner_id and record.source_video_id == source_video_id
            ]
            if not records:
                raise RunError(404, "video_not_found", "No stored analyses were found for this video.")
            if any(record.status in {"queued", "processing"} for record in records):
                raise RunError(409, "video_analysis_active", "Stop or wait for this video's active analysis before deleting its results.")

            runs_root = self._resolved_runs_root()
            staging = runs_root / f".deleting-{secrets.token_hex(12)}"
            staged: list[tuple[RunRecord, Path]] = []
            try:
                staging.mkdir()
                for record in records:
                    if record.root.is_symlink():
                        raise RunError(409, "unsafe_run_storage", "A run directory could not be safely deleted.")
                    resolved = record.root.resolve(strict=True)
                    resolved.relative_to(runs_root)
                    if resolved != record.root:
                        raise RunError(409, "unsafe_run_storage", "A run directory could not be safely deleted.")
                    destination = staging / record.run_id
                    os.replace(resolved, destination)
                    staged.append((record, destination))
            except Exception as error:
                for record, destination in reversed(staged):
                    if destination.exists():
                        os.replace(destination, record.root)
                shutil.rmtree(staging, ignore_errors=True)
                if isinstance(error, RunError):
                    raise
                raise RunError(500, "video_delete_failed", "The video's stored analyses could not be safely removed.") from error

            for record in records:
                self._runs.pop(record.run_id, None)
            run_ids = [record.run_id for record in records]
            try:
                shutil.rmtree(staging)
            except OSError as error:
                LOGGER.exception("Could not fully remove staged video analyses for %s", source_video_id)
                raise RunError(500, "video_delete_incomplete", "The run records were removed, but some stored files could not be cleaned up.") from error
            return run_ids
