"""API data access and HTTP routing for input preparation and pipeline outputs."""

from __future__ import annotations

import base64
import csv
import json
import logging
import math
import mimetypes
import re
import threading
import time
from http.cookies import CookieError, SimpleCookie
from collections import Counter, defaultdict
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from backend.authorization import evaluate as evaluate_authorization, simulation_payload as authorization_simulation_payload, load_config as load_authorization_config, save_config as save_authorization_config
from backend import operator_locations
from backend.dashboard_summary import build_headquarters_trends, build_sector_dashboard_summary
from backend.headquarters_sectors import is_sector_active, load_headquarters_sectors, save_headquarters_sectors
from backend import sector_registry
from backend.authorized_drones import (
    classify_telemetry, get_track_detail, latest_trajectory_assessments, list_authorized_drones, list_tracks, register_approved_drone, review_track, revoke_authorized_drone,
)
from backend.auth import (
    SESSION_COOKIE_NAME,
    AuthManager,
    cookie_header,
    expired_cookie_header,
    secure_cookies_enabled,
)
from backend.config import Settings, get_settings
from backend.frames import Phase3FrameManager, PreparedFrame
from backend.input_preparation import InputPreparer
from backend.run_pipeline import run_video_pipeline
from backend.runs import RunError, RunManager, RunRecord
from backend.temporal_analysis import TemporalAnalysisService
from backend.tracking import TrackingService
from evidence.frame_selector import acquire_evidence_for_tracks
from fusion.evidence_fusion import fuse_track
from vlm.vlm_adapter import MockVLM

LOGGER = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message


def _resolve_alert_evidence(
    record: RunRecord,
    track_id: str,
    *,
    requested_frame_index: int | None = None,
) -> dict[str, Any] | None:
    """Resolve a requested alert frame or the track's representative observation.

    This deterministic reference explains/navigates to an existing observation only;
    it does not participate in alert classification.
    """
    if not track_id or not track_id.isdecimal():
        return None

    tracks_path = record.root / "tracking" / "detector" / "tracks.csv"
    if not tracks_path.is_file():
        return None
    required = {"frame", "track_id", "class_name", "confidence", "x1", "y1", "x2", "y2"}
    observations: list[dict[str, Any]] = []
    with tracks_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("Run tracking CSV is missing required alert evidence columns")
        for row in reader:
            if any(value is None for value in row.values()):
                raise ValueError("Run tracking CSV contains a malformed alert evidence row")
            if not row["track_id"].isdecimal() or str(int(row["track_id"])) != str(int(track_id)):
                continue
            try:
                frame_index = int(row["frame"])
                confidence = float(row["confidence"])
                bbox = {key: float(row[key]) for key in ("x1", "y1", "x2", "y2")}
            except (TypeError, ValueError) as error:
                raise ValueError("Run tracking CSV contains invalid alert evidence") from error
            if (
                frame_index < 1
                or not row["class_name"]
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
                or not all(math.isfinite(value) for value in bbox.values())
            ):
                raise ValueError("Run tracking CSV contains invalid alert evidence")
            observations.append(
                {
                    "frame_index": frame_index,
                    "track_id": int(track_id),
                    "detector_class": row["class_name"],
                    "detector_confidence": confidence,
                    "bbox": bbox,
                }
            )
    if not observations:
        return None

    if requested_frame_index is not None:
        observation = next(
            (item for item in observations if item["frame_index"] == requested_frame_index),
            None,
        )
        if observation is None:
            return None
    else:
        # Representative observation is for explanation/navigation only.
        observation = min(
            observations,
            key=lambda item: (-item["detector_confidence"], item["frame_index"]),
        )
    metadata = record.input_metadata
    raw_fps = metadata.get("fps")
    timestamp = (
        (observation["frame_index"] - 1) / raw_fps
        if isinstance(raw_fps, (int, float))
        and not isinstance(raw_fps, bool)
        and math.isfinite(raw_fps)
        and raw_fps > 0
        else None
    )
    raw_total_frames = metadata.get("frame_count")
    total_frames = (
        int(raw_total_frames)
        if isinstance(raw_total_frames, (int, float))
        and not isinstance(raw_total_frames, bool)
        and math.isfinite(raw_total_frames)
        and raw_total_frames > 0
        else None
    )
    if total_frames is not None and observation["frame_index"] > total_frames:
        return None

    reliability_score = None
    reliability_status = None
    reliability_path = record.root / "reliability" / "reliability_results.csv"
    if reliability_path.is_file():
        with reliability_path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if not {"track_id", "reliability_score", "status"}.issubset(reader.fieldnames or ()):
                raise ValueError("Run reliability CSV is missing alert evidence columns")
            for row in reader:
                if row["track_id"].isdecimal() and str(int(row["track_id"])) == str(int(track_id)):
                    try:
                        score = float(row["reliability_score"])
                    except (TypeError, ValueError) as error:
                        raise ValueError("Run reliability CSV contains invalid alert evidence") from error
                    if not math.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError("Run reliability CSV contains invalid alert evidence")
                    reliability_score = score
                    reliability_status = row["status"] or None
                    break

    return {
        **observation,
        "total_frames": total_frames,
        "timestamp": timestamp,
        "reliability_score": reliability_score,
        "reliability_status": reliability_status,
    }


def _safe_resolve_alert_evidence(
    record: RunRecord,
    track_id: str,
    *,
    requested_frame_index: int | None = None,
) -> dict[str, Any] | None:
    """Treat a row being appended during live inference as temporarily unavailable."""
    try:
        return _resolve_alert_evidence(
            record, track_id, requested_frame_index=requested_frame_index
        )
    except (OSError, ValueError, csv.Error):
        return None


class ProjectData:
    """Validated, in-memory read model over existing project CSVs."""

    def __init__(
        self,
        settings: Settings,
        paths_override: dict[str, Path] | None = None,
        historical_compatibility: bool = True,
    ):
        self.settings = settings
        self.paths = settings.paths
        if paths_override:
            self.paths.update(paths_override)
        self.historical_compatibility = historical_compatibility
        self.temporal_consensus_path = self.paths.get("temporal_consensus")
        self.temporal_consensus: dict[str, dict[str, Any]] = {}
        self.tracks: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.temporal: dict[str, dict[str, str]] = {}
        self.motion: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.reliability: dict[str, dict[str, str]] = {}
        self.selected_evidence: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.vlm: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.thermal: dict[str, dict[str, str]] = {}
        self.fusion: dict[str, dict[str, str]] = {}
        self.availability: dict[str, dict[str, str]] = {}
        self.radar_rows: list[dict[str, str]] = []
        self.motion_trajectories: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
        if not path.is_file():
            raise FileNotFoundError(f"Required project output is missing: {path}")
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            missing = set(required) - set(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    f"{path} is missing required columns: {', '.join(sorted(missing))}"
                )
            rows = list(reader)
        for line_number, row in enumerate(rows, start=2):
            if any(value is None for value in row.values()):
                raise ValueError(f"{path} has a malformed row at line {line_number}")
        return rows

    @staticmethod
    def _track_id(value: str, source_name: str) -> str:
        if not value or not value.isdecimal():
            raise ValueError(f"Invalid track_id in {source_name}: {value!r}")
        return str(int(value))

    @staticmethod
    def _number(
        value: str,
        field_name: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid numeric value for {field_name}: {value!r}") from error
        if not math.isfinite(number):
            raise ValueError(f"{field_name} must be finite: {value!r}")
        if minimum is not None and number < minimum:
            raise ValueError(f"{field_name} must be >= {minimum}: {value!r}")
        if maximum is not None and number > maximum:
            raise ValueError(f"{field_name} must be <= {maximum}: {value!r}")
        return number

    @staticmethod
    def _boolean(value: str, field_name: str) -> bool:
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValueError(f"Invalid boolean for {field_name}: {value!r}")

    @staticmethod
    def _unique_track_rows(
        rows: list[dict[str, str]], name: str
    ) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            track_id = ProjectData._track_id(row["track_id"], name)
            if track_id in result:
                raise ValueError(f"Duplicate {name} track_id: {track_id}")
            result[track_id] = row
        return result

    def _safe_evidence_path(self, raw_path: str) -> str:
        if not raw_path:
            raise ValueError("Selected evidence has an empty crop_path")
        root = self.paths["evidence_root"].resolve()
        relative_path = Path(raw_path)
        if relative_path.is_absolute() or relative_path.drive:
            raise ValueError(f"Evidence crop path must be relative: {raw_path!r}")
        resolved = (root / relative_path).resolve()
        try:
            safe_relative = resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Evidence crop path escapes evidence directory: {raw_path!r}") from error
        if not resolved.is_file():
            raise ValueError(f"Evidence crop does not exist: {raw_path!r}")
        return safe_relative.as_posix()

    @staticmethod
    def _source_name(raw_source: str) -> str:
        source_name = (
            PureWindowsPath(raw_source).name
            if "\\" in raw_source
            else Path(raw_source).name
        )
        if source_name in {"", ".", ".."}:
            raise ValueError("Track source must include a filename")
        return source_name

    def _load(self) -> None:
        tracks_rows = self._read_csv(
            self.paths["tracks"],
            ("source", "frame", "track_id", "class_name", "confidence", "x1", "y1", "x2", "y2"),
        )
        temporal_rows = self._read_csv(
            self.paths["temporal"],
            ("source", "track_id", "frames_seen", "start_frame", "end_frame", "persistence", "mean_confidence"),
        )
        motion_rows = self._read_csv(
            self.paths["motion"],
            ("source", "track_id", "frame", "delta_x", "delta_y", "displacement"),
        )
        reliability_rows = self._read_csv(
            self.paths["reliability"],
            ("track_id", "confidence_score", "consistency_score", "persistence_score", "motion_score", "reliability_score", "status", "frames_seen", "mean_confidence", "persistence"),
        )
        selected_rows = self._read_csv(
            self.paths["selected_evidence"],
            ("track_id", "frame", "source", "x1", "y1", "x2", "y2", "confidence", "crop_path", "selection_reason"),
        )
        vlm_rows = self._read_csv(
            self.paths["vlm"],
            ("track_id", "frame", "crop_path", "label", "confidence", "reason"),
        )
        thermal_manifest_rows = self._read_csv(
            self.paths["thermal_manifest"],
            ("track_id", "source", "rgb_video_frame", "synchronization_status", "thermal_frame_path", "thermal_frame_available", "thermal_spatial_crop_available"),
        )
        thermal_rows = self._read_csv(
            self.paths["thermal_coverage"],
            ("track_id", "thermal_frame_available", "thermal_coverage_status", "thermal_spatial_crop_available", "synchronized_rgb_observations"),
        )
        self.radar_rows = self._read_csv(
            self.paths["radar_summary"],
            ("sample_index", "rd_matrix_available", "radar_timestamp_available", "caltech_recording_id_available", "synchronization_metadata_available", "track24_association"),
        )
        fusion_rows = self._read_csv(
            self.paths["fusion"],
            ("source", "track_id", "detector_label", "detector_confidence", "temporal_score", "reliability_score", "reliability_status", "vlm_label", "vlm_confidence", "thermal_evidence_available", "thermal_evidence_associated", "radar_evidence_available", "radar_evidence_associated", "fusion_mode", "final_label", "final_confidence"),
        )
        availability_rows = self._read_csv(
            self.paths["availability"],
            ("track_id", "thermal_synchronized", "thermal_spatially_associated", "radar_genuine", "radar_caltech_associated"),
        )
        baseline_rows = (
            self._read_csv(
                self.paths["baseline"], ("track_id", "final_label", "decision_confidence")
            )
            if self.historical_compatibility
            else []
        )
        exploratory_rows = (
            self._read_csv(
                self.paths["exploratory"],
                ("track_id", "final_label", "decision_confidence", "fusion_mode"),
            )
            if self.historical_compatibility
            else []
        )

        source_by_track: dict[str, str] = {}
        classes_by_track: dict[str, set[str]] = defaultdict(set)
        for row in tracks_rows:
            track_id = self._track_id(row["track_id"], "tracks.csv")
            if not row["source"] or not row["class_name"]:
                raise ValueError(f"tracks.csv has an empty source or class for track {track_id}")
            if track_id in source_by_track and source_by_track[track_id] != row["source"]:
                raise ValueError(f"Track {track_id} is associated with multiple sources")
            source_by_track[track_id] = row["source"]
            classes_by_track[track_id].add(row["class_name"])
            row["track_id"] = track_id
            self._number(row["confidence"], "detector confidence", minimum=0, maximum=1)
            for coordinate in ("x1", "y1", "x2", "y2"):
                self._number(row[coordinate], f"detector {coordinate}")
            self._integer(row["frame"], "detector frame", minimum=0)
            self.tracks[track_id].append(row)
        if not self.tracks:
            raise ValueError("tracks.csv contains no detector observations")
        self.temporal = self._unique_track_rows(temporal_rows, "temporal")
        self.reliability = self._unique_track_rows(reliability_rows, "reliability")
        self.thermal = self._unique_track_rows(thermal_rows, "thermal coverage")
        self.fusion = self._unique_track_rows(fusion_rows, "fusion")
        self.availability = self._unique_track_rows(availability_rows, "availability")
        ids = set(self.tracks)
        consensus_path = self.temporal_consensus_path
        if consensus_path is not None and consensus_path.is_file():
            from temporal.temporal_consensus import load_tracks_consensus

            self.temporal_consensus = load_tracks_consensus(consensus_path)
            if set(self.temporal_consensus) != ids:
                raise ValueError(
                    "temporal consensus track IDs do not match detector tracks"
                )
        for row in vlm_rows:
            track_id = self._track_id(row["track_id"], "VLM")
            row["track_id"] = track_id
            self.vlm[track_id].append(row)

        for name, rows in (
            ("temporal", self.temporal),
            ("reliability", self.reliability),
            ("fusion", self.fusion),
            ("availability", self.availability),
        ):
            if set(rows) != ids:
                raise ValueError(f"{name} track IDs do not match detector tracks")
        if set(self.vlm) - ids or set(self.thermal) - ids:
            raise ValueError("VLM or thermal evidence references an unknown track")
        trajectory_path = self.paths.get("motion_trajectory_json")
        if trajectory_path is not None and trajectory_path.is_file():
            self._load_motion_trajectories(trajectory_path, ids)

        self._validate_track_level_rows(source_by_track, classes_by_track)
        self._validate_thermal_manifest(thermal_manifest_rows, source_by_track)
        self._load_motion(motion_rows, source_by_track)
        self._load_selected(selected_rows, source_by_track)
        if self.historical_compatibility:
            self._validate_existing_fusion(baseline_rows, exploratory_rows)

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"Invalid non-finite JSON number: {value}")

    @staticmethod
    def _validate_finite_json(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Motion trajectory JSON contains a non-finite number")
        if isinstance(value, dict):
            for child in value.values():
                ProjectData._validate_finite_json(child)
        elif isinstance(value, list):
            for child in value:
                ProjectData._validate_finite_json(child)

    def _load_motion_trajectories(self, path: Path, expected_ids: set[str]) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Required run motion output is missing: {path}")
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=self._reject_json_constant,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"Invalid motion trajectory JSON: {path.name}") from error
        if not isinstance(payload, dict):
            raise ValueError("Motion trajectory JSON must contain a JSON object")
        self._validate_finite_json(payload)
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported motion trajectory schema version")
        if payload.get("coordinate_system") != "image_plane_pixels":
            raise ValueError("Motion trajectories must declare image-plane pixel coordinates")
        if payload.get("physical_world_calibration") != "not_available":
            raise ValueError("Motion trajectories must not claim unvalidated physical calibration")
        tracks = payload.get("tracks")
        if not isinstance(tracks, dict):
            raise ValueError("Motion trajectories must contain a track mapping")
        normalized = {self._track_id(str(track_id), "motion trajectories"): row for track_id, row in tracks.items()}
        if len(normalized) != len(tracks) or set(normalized) != expected_ids:
            raise ValueError("Motion trajectory track IDs do not match detector tracks")
        for track_id, row in normalized.items():
            if (
                not isinstance(row, dict)
                or row.get("coordinate_system") != "image_plane_pixels"
                or not isinstance(row.get("trajectory_points"), list)
                or not isinstance(row.get("summary"), dict)
            ):
                raise ValueError(f"Invalid motion trajectory for track {track_id}")
            if not row["trajectory_points"]:
                raise ValueError(f"Motion trajectory has no observations for track {track_id}")
        self.motion_trajectories = normalized

    @staticmethod
    def _integer(value: str, field_name: str, *, minimum: int | None = None) -> int:
        try:
            integer = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid integer for {field_name}: {value!r}") from error
        if minimum is not None and integer < minimum:
            raise ValueError(f"{field_name} must be >= {minimum}: {value!r}")
        return integer

    def _validate_track_level_rows(
        self, sources: dict[str, str], labels: dict[str, set[str]]
    ) -> None:
        for track_id in self.tracks:
            temporal = self.temporal[track_id]
            fusion = self.fusion[track_id]
            reliability = self.reliability[track_id]
            if temporal["source"] != sources[track_id] or fusion["source"] != sources[track_id]:
                raise ValueError(f"Track {track_id} source does not match across phase outputs")
            diagnostic_fields = {
                "legacy_motion_score",
                "trajectory_motion_score",
                "trajectory_direction_consistency",
                "trajectory_straightness_ratio",
                "trajectory_speed_stability",
                "trajectory_acceleration_stability",
                "trajectory_continuity",
                "trajectory_motion_state",
                "trajectory_motion_quality",
            }
            present_diagnostics = diagnostic_fields.intersection(reliability)
            if present_diagnostics and present_diagnostics != diagnostic_fields:
                raise ValueError(
                    f"Trajectory reliability diagnostics have an incomplete schema for track {track_id}"
                )
            count = len(self.tracks[track_id])
            seen = self._integer(temporal["frames_seen"], "frames_seen", minimum=0)
            if seen != count:
                raise ValueError(f"Track {track_id} frames_seen does not match detector observations")
            if self._integer(reliability["frames_seen"], "reliability frames_seen", minimum=0) != seen:
                raise ValueError(f"Track {track_id} frames_seen differs in reliability output")
            if self._number(temporal["persistence"], "persistence", minimum=0, maximum=1) > 1:
                raise ValueError(f"Invalid persistence for track {track_id}")
            self._number(temporal["mean_confidence"], "mean_confidence", minimum=0, maximum=1)
            for score_field in (
                "confidence_score",
                "consistency_score",
                "persistence_score",
                "motion_score",
                "reliability_score",
                "mean_confidence",
                "persistence",
            ):
                self._number(
                    reliability[score_field],
                    f"reliability {score_field}",
                    minimum=0,
                    maximum=1,
                )
            if "legacy_motion_score" in reliability:
                legacy_motion_score = self._number(
                    reliability["legacy_motion_score"],
                    "legacy_motion_score",
                    minimum=0,
                    maximum=1,
                )
                if legacy_motion_score != self._number(
                    reliability["motion_score"], "motion_score", minimum=0, maximum=1
                ):
                    raise ValueError(
                        f"Legacy motion score differs from motion_score for track {track_id}"
                    )
            for diagnostic_field in (
                "trajectory_motion_score",
                "trajectory_direction_consistency",
                "trajectory_straightness_ratio",
                "trajectory_speed_stability",
                "trajectory_acceleration_stability",
                "trajectory_continuity",
            ):
                if diagnostic_field in reliability and reliability[diagnostic_field]:
                    self._number(
                        reliability[diagnostic_field],
                        diagnostic_field,
                        minimum=0,
                        maximum=1,
                    )
            self._number(fusion["detector_confidence"], "detector_confidence", minimum=0, maximum=1)
            self._number(fusion["temporal_score"], "temporal_score", minimum=0, maximum=1)
            self._number(fusion["reliability_score"], "fusion reliability_score", minimum=0, maximum=1)
            self._number(fusion["final_confidence"], "final_confidence", minimum=0, maximum=1)
            if fusion["detector_label"] not in {"Drone", "Bird"}:
                raise ValueError(f"Unsupported fusion detector label for track {track_id}")
            if reliability["status"] not in {"CONFIDENT", "UNCERTAIN"}:
                raise ValueError(f"Unsupported reliability status for track {track_id}")
            if fusion["reliability_status"] != reliability["status"]:
                raise ValueError(f"Reliability status differs for track {track_id}")
            if fusion["detector_label"] not in labels[track_id]:
                raise ValueError(
                    f"Fusion class is absent from detector observations for track {track_id}"
                )
            if fusion["fusion_mode"] not in {
                "detector_temporal",
                "detector_vlm",
                "detector_temporal_abstain",
            }:
                raise ValueError(f"Unsupported fusion mode for track {track_id}")

        for rows in self.vlm.values():
            for row in rows:
                self._number(row["confidence"], "VLM confidence", minimum=0, maximum=1)
                self._integer(row["frame"], "VLM frame", minimum=0)
        genuine_radar_available = any(
            self._boolean(row["rd_matrix_available"], "rd_matrix_available")
            for row in self.radar_rows
        )
        validated_radar_associations = {track_id: False for track_id in self.tracks}
        track24_associated = any(
            self._boolean(row["rd_matrix_available"], "rd_matrix_available")
            and self._boolean(row["radar_timestamp_available"], "radar_timestamp_available")
            and self._boolean(row["caltech_recording_id_available"], "caltech_recording_id_available")
            and self._boolean(row["synchronization_metadata_available"], "synchronization_metadata_available")
            and self._boolean(row["track24_association"], "track24_association")
            for row in self.radar_rows
        )
        if track24_associated and "24" in validated_radar_associations:
            validated_radar_associations["24"] = True

        fusion_radar_availability = {
            track_id: self._boolean(row["radar_evidence_available"], "radar_evidence_available")
            for track_id, row in self.fusion.items()
        }
        legacy_radar_availability = all(
            available == genuine_radar_available
            for available in fusion_radar_availability.values()
        )
        current_radar_availability = all(
            available
            == (genuine_radar_available and validated_radar_associations[track_id])
            for track_id, available in fusion_radar_availability.items()
        )
        if not (legacy_radar_availability or current_radar_availability):
            raise ValueError(
                "Phase 5 Radar availability does not match legacy or per-track semantics"
            )

        for track_id, fusion in self.fusion.items():
            coverage = self.thermal.get(track_id)
            thermal_available = (
                self._boolean(coverage["thermal_frame_available"], "thermal_frame_available")
                if coverage
                else False
            )
            thermal_associated = (
                thermal_available
                and self._boolean(coverage["thermal_spatial_crop_available"], "thermal_spatial_crop_available")
                if coverage
                else False
            )
            if self._boolean(fusion["thermal_evidence_available"], "thermal_evidence_available") != thermal_available:
                raise ValueError(f"Phase 5 Thermal availability differs for track {track_id}")
            if self._boolean(fusion["thermal_evidence_associated"], "thermal_evidence_associated") != thermal_associated:
                raise ValueError(f"Phase 5 Thermal association differs for track {track_id}")
            radar_associated = validated_radar_associations[track_id]
            if self._boolean(fusion["radar_evidence_associated"], "radar_evidence_associated") != radar_associated:
                raise ValueError(f"Phase 5 Radar association differs for track {track_id}")
            availability = self.availability[track_id]
            expected_sync = "evidence_available" if thermal_available else "evidence_not_available"
            expected_thermal_association = (
                "evidence_available"
                if thermal_associated
                else ("evidence_not_associated" if thermal_available else "evidence_not_available")
            )
            if (
                availability["thermal_synchronized"] != expected_sync
                or availability["thermal_spatially_associated"] != expected_thermal_association
                or availability["radar_genuine"] != (
                    "evidence_available" if genuine_radar_available else "evidence_not_available"
                )
                or availability["radar_caltech_associated"] != (
                    "evidence_available"
                    if radar_associated
                    else (
                        "evidence_not_associated"
                        if genuine_radar_available
                        else "evidence_not_available"
                    )
                )
            ):
                raise ValueError(f"Phase 5 modality availability differs for track {track_id}")

    def _validate_thermal_manifest(
        self, rows: list[dict[str, str]], sources: dict[str, str]
    ) -> None:
        manifest_counts: Counter[str] = Counter()
        for row in rows:
            track_id = self._track_id(row["track_id"], "thermal_frame_manifest.csv")
            if track_id not in self.tracks or row["source"] != sources[track_id]:
                raise ValueError(f"Thermal manifest has unmatched source/track: {track_id}")
            available = self._boolean(row["thermal_frame_available"], "thermal_frame_available")
            spatial_crop = self._boolean(
                row["thermal_spatial_crop_available"], "thermal_spatial_crop_available"
            )
            synchronized = row["synchronization_status"].strip().casefold() == "synchronized"
            if synchronized != available:
                raise ValueError(f"Thermal manifest synchronization conflicts for track {track_id}")
            if spatial_crop:
                raise ValueError(f"Thermal spatial association is not validated for track {track_id}")
            if synchronized:
                manifest_counts[track_id] += 1
        for track_id, coverage in self.thermal.items():
            declared_count = self._integer(
                coverage["synchronized_rgb_observations"],
                "synchronized_rgb_observations",
                minimum=0,
            )
            if manifest_counts[track_id] != declared_count:
                raise ValueError(f"Thermal manifest count differs from coverage for track {track_id}")

    def _load_motion(self, rows: list[dict[str, str]], sources: dict[str, str]) -> None:
        for row in rows:
            track_id = self._track_id(row["track_id"], "track_motion.csv")
            if track_id not in self.tracks or row["source"] != sources[track_id]:
                raise ValueError(f"Motion row has unmatched source/track: {track_id}")
            for field in ("delta_x", "delta_y", "displacement"):
                self._number(row[field], f"motion {field}")
            self._integer(row["frame"], "motion frame", minimum=0)
            row["track_id"] = track_id
            self.motion[track_id].append(row)

    def _load_selected(self, rows: list[dict[str, str]], sources: dict[str, str]) -> None:
        for row in rows:
            track_id = self._track_id(row["track_id"], "selected_evidence.csv")
            if track_id not in self.tracks or row["source"] != sources[track_id]:
                raise ValueError(f"Selected evidence has unmatched source/track: {track_id}")
            self._number(row["confidence"], "selected evidence confidence", minimum=0, maximum=1)
            self._integer(row["frame"], "selected evidence frame", minimum=0)
            bbox = [self._number(row[key], f"selected bbox {key}") for key in ("x1", "y1", "x2", "y2")]
            if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
                raise ValueError(f"Selected evidence has invalid bbox for track {track_id}")
            row["track_id"] = track_id
            row["_safe_crop_path"] = self._safe_evidence_path(row["crop_path"])
            self.selected_evidence[track_id].append(row)

    def _validate_existing_fusion(
        self, baseline_rows: list[dict[str, str]], exploratory_rows: list[dict[str, str]]
    ) -> None:
        baseline = self._unique_track_rows(baseline_rows, "baseline")
        exploratory = self._unique_track_rows(exploratory_rows, "exploratory fusion")
        ids = set(self.tracks)
        if set(baseline) != ids or set(exploratory) != ids:
            raise ValueError("Existing baseline or exploratory track IDs do not match detector tracks")
        for track_id, row in baseline.items():
            if row["final_label"] not in {"UAV", "Bird", "Other"}:
                raise ValueError(f"Unsupported historical baseline label for track {track_id}")
            self._number(
                row["decision_confidence"],
                "historical baseline confidence",
                minimum=0,
                maximum=1,
            )
        for track_id, row in exploratory.items():
            if row["final_label"] not in {"UAV", "Bird", "Other"}:
                raise ValueError(f"Unsupported historical exploratory label for track {track_id}")
            if row["fusion_mode"] not in {
                "detector_temporal",
                "detector_vlm",
                "detector_temporal_abstain",
            }:
                raise ValueError(f"Unsupported historical fusion mode for track {track_id}")
            self._number(
                row["decision_confidence"],
                "historical exploratory confidence",
                minimum=0,
                maximum=1,
            )

    def _track(self, track_id: str) -> dict[str, str]:
        if track_id not in self.tracks:
            raise APIError(404, "track_not_found", f"Track {track_id} does not exist")
        return self.fusion[track_id]

    def _vlm_result(self, track_id: str) -> dict[str, Any]:
        rows = self.vlm.get(track_id, [])
        if not rows:
            return {
                "available": False,
                "result": None,
                "results": [],
                "confidence_interpretation": "generation-token probability proxy; not calibrated probability",
            }
        results = [
            {
                "track_id": track_id,
                "label": row["label"],
                "confidence": self._number(row["confidence"], "VLM confidence", minimum=0, maximum=1),
                "reason": row["reason"],
                "frame": self._integer(row["frame"], "VLM frame", minimum=0),
            }
            for row in rows
        ]
        return {
            "available": True,
            "result": results[0] if len(results) == 1 else None,
            "results": results,
            "confidence_interpretation": "generation-token probability proxy; not calibrated probability",
        }

    def _temporal_summary(self, track_id: str) -> dict[str, Any]:
        row = self.temporal[track_id]
        integer_fields = {"frames_seen", "start_frame", "end_frame"}
        summary: dict[str, Any] = {"source": self._source_name(row["source"])}
        for field, value in row.items():
            if field == "source" or field == "track_id":
                continue
            if field in integer_fields:
                summary[field] = self._integer(value, field, minimum=0)
            else:
                summary[field] = self._number(value, f"temporal {field}")
        summary["track_id"] = track_id
        return summary

    def _motion_summary(self, track_id: str) -> list[dict[str, Any]]:
        return [
            {
                "frame": self._integer(row["frame"], "motion frame", minimum=0),
                "delta_x": self._number(row["delta_x"], "motion delta_x"),
                "delta_y": self._number(row["delta_y"], "motion delta_y"),
                "displacement": self._number(row["displacement"], "motion displacement", minimum=0),
                "rgb_timestamp_sec": self._number(row["rgb_timestamp_sec"], "motion timestamp")
                if row.get("rgb_timestamp_sec")
                else None,
            }
            for row in self.motion.get(track_id, ())
        ]

    def _reliability_result(self, track_id: str) -> dict[str, Any]:
        row = self.reliability[track_id]
        result = {
            "track_id": track_id,
            "confidence_score": self._number(row["confidence_score"], "confidence_score", minimum=0, maximum=1),
            "consistency_score": self._number(row["consistency_score"], "consistency_score", minimum=0, maximum=1),
            "persistence_score": self._number(row["persistence_score"], "persistence_score", minimum=0, maximum=1),
            "motion_score": self._number(row["motion_score"], "motion_score", minimum=0, maximum=1),
            "reliability_score": self._number(row["reliability_score"], "reliability_score", minimum=0, maximum=1),
            "status": row["status"],
            "frames_seen": self._integer(row["frames_seen"], "reliability frames_seen", minimum=0),
            "mean_confidence": self._number(row["mean_confidence"], "reliability mean_confidence", minimum=0, maximum=1),
            "persistence": self._number(row["persistence"], "reliability persistence", minimum=0, maximum=1),
            "interpretation": "heuristic score; not calibrated probability",
        }
        if "trajectory_motion_score" in row:
            result.update(
                {
                    "legacy_motion_score": self._number(
                        row["legacy_motion_score"], "legacy_motion_score", minimum=0, maximum=1
                    ),
                    "trajectory_motion_score": (
                        self._number(
                            row["trajectory_motion_score"],
                            "trajectory_motion_score",
                            minimum=0,
                            maximum=1,
                        )
                        if row["trajectory_motion_score"]
                        else None
                    ),
                    "trajectory_motion_state": row["trajectory_motion_state"] or None,
                    "trajectory_motion_quality": row["trajectory_motion_quality"] or None,
                    "trajectory_motion_components": {
                        field.removeprefix("trajectory_"): (
                            self._number(row[field], field, minimum=0, maximum=1)
                            if row[field]
                            else None
                        )
                        for field in (
                            "trajectory_direction_consistency",
                            "trajectory_straightness_ratio",
                            "trajectory_speed_stability",
                            "trajectory_acceleration_stability",
                            "trajectory_continuity",
                        )
                    },
                    "trajectory_motion_interpretation": (
                        "diagnostic image-plane evidence only; not included in the "
                        "historical reliability score and not a calibrated probability"
                    ),
                }
            )
        return result

    def _thermal_status(self, track_id: str) -> dict[str, Any]:
        row = self.thermal.get(track_id)
        if row is None:
            return {
                "available": None,
                "synchronized": None,
                "spatially_associated": None,
                "status": "not_available",
                "spatial_mapping_status": "not_validated",
            }
        synchronized = self._boolean(row["thermal_frame_available"], "thermal_frame_available")
        spatially_associated = (
            self._boolean(row["thermal_spatial_crop_available"], "thermal_spatial_crop_available")
            if synchronized
            else None
        )
        return {
            "available": synchronized,
            "synchronized": synchronized,
            "spatially_associated": spatially_associated,
            "status": row["thermal_coverage_status"],
            "spatial_mapping_status": "validated" if spatially_associated else "not_validated",
            "synchronized_observations": self._integer(
                row["synchronized_rgb_observations"],
                "synchronized_rgb_observations",
                minimum=0,
            ),
        }

    def _radar_status(self) -> dict[str, Any]:
        genuine = [
            row
            for row in self.radar_rows
            if self._boolean(row["rd_matrix_available"], "rd_matrix_available")
        ]
        associated = [
            row
            for row in self.radar_rows
            if all(
                self._boolean(row[field], field)
                for field in (
                    "radar_timestamp_available",
                    "caltech_recording_id_available",
                    "synchronization_metadata_available",
                    "track24_association",
                )
            )
        ]
        return {
            "genuine_data_available": bool(genuine),
            "genuine_sample_count": len(genuine),
            "synchronized_with_caltech": None,
            "synchronization_status": "not_established",
            "track_associated": None,
            "track_association_status": "not_established",
            "caltech_associated_sample_count": len(associated),
        }

    def overview(self) -> dict[str, Any]:
        modes = sorted({row["fusion_mode"] for row in self.fusion.values()})
        statuses = Counter(row["status"] for row in self.reliability.values())
        return {
            "total_tracks": len(self.tracks),
            "total_observations": sum(len(rows) for rows in self.tracks.values()),
            "confident_tracks": statuses.get("CONFIDENT", 0),
            # The dashboard's uncertainty count represents final fusion
            # abstentions. Reliability confidence is a separate measure and
            # can be CONFIDENT even when temporal consensus abstains.
            "uncertain_tracks": sum(
                row["final_label"] == "Other" for row in self.fusion.values()
            ),
            "vlm_enabled_tracks": len(self.vlm),
            "synchronized_thermal_evidence_count": sum(
                self._boolean(row["thermal_frame_available"], "thermal_frame_available")
                for row in self.thermal.values()
            ),
            "spatially_associated_thermal_evidence_count": sum(
                self._boolean(row["thermal_frame_available"], "thermal_frame_available")
                and self._boolean(row["thermal_spatial_crop_available"], "thermal_spatial_crop_available")
                for row in self.thermal.values()
            ),
            "genuine_radar_sample_count": self._radar_status()["genuine_sample_count"],
            "caltech_associated_radar_count": self._radar_status()["caltech_associated_sample_count"],
            "available_fusion_modes": modes,
        }

    def track_list(self) -> list[dict[str, Any]]:
        tracks = []
        for track_id in sorted(self.tracks, key=int):
            result = {
                "source": self._source_name(self.fusion[track_id]["source"]),
                "track_id": track_id,
                "class": self.fusion[track_id]["detector_label"],
                "class_history": list(dict.fromkeys(
                    row["class_name"] for row in sorted(
                        self.tracks[track_id], key=lambda item: int(item["frame"])
                    )
                )),
                "frames_seen": self._integer(self.temporal[track_id]["frames_seen"], "frames_seen", minimum=0),
                "start_frame": self._integer(self.temporal[track_id]["start_frame"], "start_frame", minimum=0),
                "end_frame": self._integer(self.temporal[track_id]["end_frame"], "end_frame", minimum=0),
                "mean_detector_confidence": self._number(self.fusion[track_id]["detector_confidence"], "detector_confidence", minimum=0, maximum=1),
                "reliability_score": self._number(self.reliability[track_id]["reliability_score"], "reliability_score", minimum=0, maximum=1),
                "reliability_status": self.reliability[track_id]["status"],
                "fusion_mode": self.fusion[track_id]["fusion_mode"],
                "final_label": self.fusion[track_id]["final_label"],
                "final_confidence": self._number(self.fusion[track_id]["final_confidence"], "final_confidence", minimum=0, maximum=1),
            }
            trajectory = self.motion_trajectories.get(track_id)
            if trajectory is not None:
                motion_summary = trajectory["summary"]
                result.update(
                    {
                        "motion_state": trajectory["motion_state"],
                        "motion_quality": motion_summary["motion_quality"],
                        "mean_speed_px_per_second": motion_summary["mean_speed_px_per_second"],
                    }
                )
            tracks.append(result)
        return tracks

    def evidence(self, track_id: str) -> list[dict[str, Any]]:
        self._track(track_id)
        return [
            {
                "track_id": track_id,
                "frame": self._integer(row["frame"], "evidence frame", minimum=0),
                "confidence": self._number(row["confidence"], "evidence confidence", minimum=0, maximum=1),
                "bbox": {key: self._number(row[key], f"evidence bbox {key}") for key in ("x1", "y1", "x2", "y2")},
                "crop_path": row["_safe_crop_path"],
                "selection_stage": "phase4_selective_rgb",
                "selection_group": row["selection_reason"],
            }
            for row in self.selected_evidence.get(track_id, ())
        ]

    def modalities(self, track_id: str | None = None) -> dict[str, Any]:
        if track_id is not None:
            self._track(track_id)
            return {
                "track_id": track_id,
                "thermal": self._thermal_status(track_id),
                "radar": self._radar_status(),
            }
        thermal = self.overview()
        synchronized_count = thermal["synchronized_thermal_evidence_count"]
        return {
            "thermal": {
                "available": synchronized_count > 0,
                "synchronized": synchronized_count > 0,
                "spatially_associated": None,
                "synchronized_track_count": synchronized_count,
                "spatially_associated_track_count": thermal["spatially_associated_thermal_evidence_count"],
                "spatial_mapping_status": "not_validated",
            },
            "radar": self._radar_status(),
        }

    def fusion_result(self, track_id: str) -> dict[str, Any]:
        row = self._track(track_id)
        temporal = self.temporal[track_id]
        reliability = self.reliability[track_id]
        return {
            "track_id": track_id,
            "detector_evidence": {
                "class": row["detector_label"],
                "mean_confidence": self._number(row["detector_confidence"], "detector_confidence", minimum=0, maximum=1),
            },
            "temporal_score": self._number(row["temporal_score"], "temporal_score", minimum=0, maximum=1),
            "reliability": {
                "score": self._number(row["reliability_score"], "reliability_score", minimum=0, maximum=1),
                "status": reliability["status"],
                "interpretation": "heuristic score; not calibrated probability",
            },
            "vlm": self._vlm_result(track_id),
            "thermal": self._thermal_status(track_id),
            "radar": self._radar_status(),
            "fusion_mode": row["fusion_mode"],
            "final_label": row["final_label"],
            "final_confidence": self._number(row["final_confidence"], "final_confidence", minimum=0, maximum=1),
            "confidence_interpretation": "existing fusion decision score; not a calibrated probability",
        }

    ledger_schema_version = 1

    def _ledger_status(self, count: int) -> str:
        if getattr(self, "run_status", "completed") == "failed":
            return "partial"
        if not count:
            return "empty"
        return "completed"

    def _ledger_provenance(self) -> dict[str, Any]:
        manifest = getattr(self, "detector_manifest", {}) or {}
        checkpoint_sha256 = manifest.get("detector_checkpoint_sha256")
        return {
            "available": bool(checkpoint_sha256),
            "checkpoint_name": manifest.get("detector_checkpoint_name"),
            "checkpoint_sha256": checkpoint_sha256,
            "architecture": manifest.get("detector_model_architecture", "yv5_gh (GhostNet + AFNO2D)"),
            "image_size": manifest.get("detector_image_size", [640, 640]),
            "confidence_threshold": manifest.get("detector_confidence_threshold", 0.25),
            "iou_threshold": manifest.get("detector_iou_threshold", 0.45),
            "device": manifest.get("detector_device", "cpu"),
            "preprocessing": manifest.get("detector_preprocessing"),
        }

    def _evidence_ledger(
        self,
        track_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from temporal.temporal_consensus import (
            TEMPORAL_CONSENSUS_VERSION,
            evaluate_track_consensus,
        )

        class_changes = []
        observation_gaps = []
        for index, observation in enumerate(observations):
            if index:
                previous = observations[index - 1]
                if observation["class_id"] != previous["class_id"]:
                    class_changes.append(
                        {
                            "observation_index": index,
                            "frame": observation["frame"],
                            "source_frame_index": observation["source_frame_index"],
                            "previous_class_id": previous["class_id"],
                            "previous_class": previous["class"],
                            "predicted_class_id": observation["class_id"],
                            "predicted_class": observation["class"],
                        }
                    )
                missing_source_frames = (
                    observation["source_frame_index"]
                    - previous["source_frame_index"]
                    - 1
                )
                if missing_source_frames > 0:
                    observation_gaps.append(
                        {
                            "after_observation_index": index - 1,
                            "before_observation_index": index,
                            "first_missing_source_frame_index": previous["source_frame_index"] + 1,
                            "last_missing_source_frame_index": observation["source_frame_index"] - 1,
                            "missing_source_frame_count": missing_source_frames,
                        }
                    )

        if getattr(self, "run_status", "completed") == "failed":
            temporal_consensus = {
                "status": "not_run",
                "result": None,
                "model_version": None,
            }
        else:
            consensus = self.temporal_consensus.get(track_id)
            consensus_source = "persisted_temporal_consensus"
            if consensus is None:
                consensus = evaluate_track_consensus(observations)
                consensus_source = "derived_from_detector_observations"
            temporal_consensus = {
                "status": "completed",
                "result": consensus,
                "model_version": TEMPORAL_CONSENSUS_VERSION,
                "source": consensus_source,
                "score_interpretation": (
                    "heuristic decision score; not calibrated probability"
                ),
            }

        vlm_rows = getattr(self, "vlm", {}).get(track_id, [])
        if vlm_rows:
            selective_verification = {
                "status": "completed",
                "result": [
                    {
                        "label": r.get("label"),
                        "confidence": float(r.get("confidence", 0)),
                        "reason": r.get("reason"),
                        "frame": int(r.get("frame", 0)),
                    }
                    for r in vlm_rows
                ],
                "model_version": "SmolVLM-500M-Instruct",
            }
        else:
            selective_verification = {
                "status": "not_run",
                "result": None,
                "model_version": None,
            }

        run_id = getattr(self, "run_manifest", {}).get("run_id") if hasattr(self, "run_manifest") else None
        return {
            "schema_version": self.ledger_schema_version,
            "status": self._ledger_status(len(observations)),
            "run_id": run_id,
            "track_id": track_id,
            "observation_source": "detector_observations",
            "observation_count": len(observations),
            "class_mapping": {"0": "Bird", "1": "Drone"},
            "confidence_interpretation": "raw detector confidence; not calibrated probability",
            "model_provenance": self._ledger_provenance(),
            "class_changes": class_changes,
            "observation_gaps": observation_gaps,
            "image_quality": {
                "status": "not_available",
                "indicators": None,
            },
            "frame_review": {
                "status": "not_available",
                "ground_truth_available": False,
                "supporting_frame_indexes": [],
                "contradictory_frame_indexes": [],
            },
            "temporal_consensus": temporal_consensus,
            "selective_verification": selective_verification,
        }

    def track_detail(self, track_id: str) -> dict[str, Any]:
        fusion = self._track(track_id)
        temporal = self.temporal[track_id]
        observations = [
            {
                "frame": self._integer(row["frame"], "detector frame", minimum=0),
                "source_frame_index": max(0, self._integer(row["frame"], "detector frame", minimum=0) - 1),
                "class": row["class_name"],
                "class_id": 0 if row["class_name"].strip().lower() == "bird" else 1,
                "confidence": self._number(row["confidence"], "detector confidence", minimum=0, maximum=1),
                "bbox": {key: self._number(row[key], f"detector bbox {key}") for key in ("x1", "y1", "x2", "y2")},
            }
            for row in sorted(self.tracks[track_id], key=lambda item: int(item["frame"]))
        ]
        return {
            "source": self._source_name(fusion["source"]),
            "track_id": track_id,
            "class": fusion["detector_label"],
            "frames_seen": self._integer(temporal["frames_seen"], "frames_seen", minimum=0),
            "start_frame": self._integer(temporal["start_frame"], "start_frame", minimum=0),
            "end_frame": self._integer(temporal["end_frame"], "end_frame", minimum=0),
            "detector_observations": observations,
            "evidence_ledger": self._evidence_ledger(track_id, observations),
            "temporal_summary": self._temporal_summary(track_id),
            "motion_summary": self._motion_summary(track_id),
            "motion_trajectory": self.motion_trajectories.get(track_id),
            "reliability": self._reliability_result(track_id),
            "selected_evidence": self.evidence(track_id),
            "vlm": self._vlm_result(track_id),
            "thermal": self._thermal_status(track_id),
            "radar": self._radar_status(),
            "fusion": self.fusion_result(track_id),
        }

    def fusion_summary(self) -> dict[str, Any]:
        mode_counts = Counter(row["fusion_mode"] for row in self.fusion.values())
        return {
            "track_count": len(self.fusion),
            "available_fusion_modes": sorted(mode_counts),
            "fusion_mode_counts": dict(sorted(mode_counts.items())),
            "final_label_counts": dict(sorted(Counter(row["final_label"] for row in self.fusion.values()).items())),
        }


class ThermalRunData(ProjectData):
    """Run-scoped read model for thermal detection/tracking without later phases."""

    thermal_only = True
    unsupported_stages = (
        "reliability",
        "evidence",
        "vlm",
        "fusion",
        "assessment",
    )

    ledger_schema_version = 1

    def __init__(
        self,
        tracks_path: Path,
        run_manifest: dict[str, Any] | None = None,
        detector_manifest: dict[str, Any] | None = None,
        consensus_path: Path | None = None,
        motion_trajectory_path: Path | None = None,
    ):
        self.tracks = defaultdict(list)
        self.motion_trajectories: dict[str, dict[str, Any]] = {}
        self.run_manifest = run_manifest or {}
        self.detector_manifest = detector_manifest or {}
        self.temporal_consensus_path = consensus_path
        self.temporal_consensus: dict[str, dict[str, Any]] = {}
        if consensus_path is not None and consensus_path.is_file():
            from temporal.temporal_consensus import load_tracks_consensus

            self.temporal_consensus = load_tracks_consensus(consensus_path)
        self.run_status = "unknown"
        rows = self._read_csv(
            tracks_path,
            (
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
            ),
        )
        class_names = {0: "Bird", 1: "Drone"}
        for row in rows:
            track_id = self._track_id(row["track_id"], "thermal tracks.csv")
            class_id = self._integer(row["class_id"], "thermal class_id", minimum=0)
            if class_id not in class_names or row["class_name"] != class_names[class_id]:
                raise ValueError("Thermal track class IDs must match Bird=0 and Drone=1")
            self._integer(row["frame"], "thermal frame", minimum=1)
            self._integer(row["source_frame_index"], "source frame index", minimum=0)
            if row["timestamp_sec"]:
                self._number(row["timestamp_sec"], "thermal timestamp", minimum=0)
            if not row["source"]:
                raise ValueError("Thermal tracks.csv has an empty source")
            self._number(row["confidence"], "thermal detector confidence", minimum=0, maximum=1)
            for coordinate in ("x1", "y1", "x2", "y2"):
                self._number(row[coordinate], f"thermal detector {coordinate}")
            self.tracks[track_id].append(row)
        if (
            consensus_path is not None
            and consensus_path.is_file()
            and set(self.temporal_consensus) != set(self.tracks)
        ):
            raise ValueError(
                "Thermal temporal consensus track IDs do not match detector tracks"
            )
        if motion_trajectory_path is not None and motion_trajectory_path.is_file():
            self._load_motion_trajectories(motion_trajectory_path, set(self.tracks))
        self.thermal_only = True

    def _ledger_status(self, observation_count: int) -> str:
        if self.run_status == "completed":
            return "available" if observation_count else "empty"
        if observation_count:
            return "partial"
        return "unavailable"

    def _ledger_provenance(self) -> dict[str, Any]:
        manifest = self.detector_manifest
        checkpoint_sha256 = manifest.get("detector_checkpoint_sha256")
        return {
            "available": bool(checkpoint_sha256),
            "checkpoint_name": manifest.get("detector_checkpoint_name"),
            "checkpoint_sha256": checkpoint_sha256,
            "architecture": manifest.get("detector_model_architecture"),
            "image_size": manifest.get("detector_image_size"),
            "confidence_threshold": manifest.get("detector_confidence_threshold"),
            "iou_threshold": manifest.get("detector_iou_threshold"),
            "device": manifest.get("detector_device"),
            "preprocessing": manifest.get("detector_preprocessing"),
        }

    def _evidence_ledger(
        self,
        track_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ledger = super()._evidence_ledger(track_id, observations)
        if self.run_status == "failed":
            ledger["temporal_consensus"] = {
                "status": "not_run",
                "result": None,
                "model_version": None,
            }
        return ledger

    def _track(self, track_id: str) -> dict[str, Any]:
        if track_id not in self.tracks:
            raise APIError(404, "track_not_found", f"Track {track_id} does not exist")
        first = self.tracks[track_id][0]
        return {
            "source": first["source"],
            "track_id": track_id,
            "detector_label": first["class_name"],
            "detector_confidence": first["confidence"],
        }

    def _vlm_result(self, track_id: str) -> dict[str, Any]:
        self._track(track_id)
        return {
            "available": False,
            "status": "not_run",
            "reason": "VLM processing is not implemented for thermal runs.",
            "result": None,
            "results": [],
        }

    def overview(self) -> dict[str, Any]:
        observation_count = sum(len(rows) for rows in self.tracks.values())
        return {
            "analysis_mode": "thermal_detection_tracking_only",
            "total_tracks": len(self.tracks),
            "total_observations": observation_count,
            "evidence_ledger": {
                "schema_version": self.ledger_schema_version,
                "status": self._ledger_status(observation_count),
                "track_count": len(self.tracks),
                "observation_count": observation_count,
                "model_provenance": self._ledger_provenance(),
            },
            "confident_tracks": 0,
            "uncertain_tracks": 0,
            "vlm_enabled_tracks": 0,
            "synchronized_thermal_evidence_count": 0,
            "spatially_associated_thermal_evidence_count": 0,
            "genuine_radar_sample_count": 0,
            "caltech_associated_radar_count": 0,
            "unsupported_stages": list(self.unsupported_stages),
        }

    def track_list(self) -> list[dict[str, Any]]:
        tracks = []
        for track_id in sorted(self.tracks, key=int):
            rows = self.tracks[track_id]
            frames = [self._integer(row["frame"], "thermal frame", minimum=1) for row in rows]
            tracks.append(
                {
                    "source": self._source_name(rows[0]["source"]),
                    "track_id": track_id,
                    "class": rows[0]["class_name"],
                    "class_history": list(dict.fromkeys(
                        row["class_name"] for row in sorted(
                            rows, key=lambda item: int(item["frame"])
                        )
                    )),
                    "frames_seen": len(rows),
                    "start_frame": min(frames),
                    "end_frame": max(frames),
                    "mean_detector_confidence": sum(
                        self._number(row["confidence"], "thermal confidence", minimum=0, maximum=1)
                        for row in rows
                    ) / len(rows),
                    "reliability_score": None,
                    "reliability_status": "NOT_RUN",
                    "fusion_mode": "not_run",
                    "final_label": None,
                    "final_confidence": None,
                    "analysis_mode": "thermal_detection_tracking_only",
                }
            )
        return tracks

    def evidence(self, track_id: str) -> list[dict[str, Any]]:
        self._track(track_id)
        return []

    def modalities(self, track_id: str | None = None) -> dict[str, Any]:
        if track_id is not None:
            self._track(track_id)
        return {
            **({"track_id": track_id} if track_id is not None else {}),
            "thermal": {
                "available": True,
                "synchronized": False,
                "spatially_associated": False,
                "status": "source video only; no cross-modal association was run",
                "spatial_mapping_status": "not_applicable",
            },
            "radar": {
                "available": False,
                "track_association_status": "not_established",
            },
        }

    def fusion_result(self, track_id: str) -> dict[str, Any]:
        row = self._track(track_id)
        confidence = sum(
            self._number(item["confidence"], "thermal confidence", minimum=0, maximum=1)
            for item in self.tracks[track_id]
        ) / len(self.tracks[track_id])
        return {
            "track_id": track_id,
            "status": "not_run",
            "reason": "Fusion is not implemented for thermal runs.",
            "detector_evidence": {
                "class": row["detector_label"],
                "mean_confidence": confidence,
            },
            "final_label": None,
            "final_confidence": None,
            "fusion_mode": None,
            "confidence_interpretation": "No fusion decision was produced for this thermal run.",
        }

    def track_detail(self, track_id: str) -> dict[str, Any]:
        row = self._track(track_id)
        observations = [
            {
                "frame": self._integer(item["frame"], "thermal frame", minimum=1),
                "source_frame_index": self._integer(
                    item["source_frame_index"], "source frame index", minimum=0
                ),
                "timestamp_sec": (
                    self._number(item["timestamp_sec"], "thermal timestamp", minimum=0)
                    if item["timestamp_sec"]
                    else None
                ),
                "class": item["class_name"],
                "class_id": self._integer(item["class_id"], "thermal class_id", minimum=0),
                "confidence": self._number(
                    item["confidence"], "thermal confidence", minimum=0, maximum=1
                ),
                "bbox": {
                    key: self._number(item[key], f"thermal {key}")
                    for key in ("x1", "y1", "x2", "y2")
                },
            }
            for item in sorted(self.tracks[track_id], key=lambda value: int(value["frame"]))
        ]
        frames = [item["frame"] for item in observations]
        return {
            **row,
            "analysis_mode": "thermal_detection_tracking_only",
            "frames_seen": len(observations),
            "start_frame": min(frames),
            "end_frame": max(frames),
            "detector_observations": observations,
            "evidence_ledger": self._evidence_ledger(track_id, observations),
            "temporal_summary": None,
            "motion_summary": [],
            "motion_trajectory": self.motion_trajectories.get(track_id),
            "reliability": {
                "status": "not_run",
                "interpretation": "Reliability scoring is not implemented for thermal runs.",
            },
            "selected_evidence": [],
            "vlm": self._vlm_result(track_id),
            "thermal": self.modalities(track_id)["thermal"],
            "radar": self.modalities(track_id)["radar"],
            "fusion": self.fusion_result(track_id),
        }

    def fusion_summary(self) -> dict[str, Any]:
        return {
            "analysis_mode": "thermal_detection_tracking_only",
            "status": "not_run",
            "track_count": 0,
            "available_fusion_modes": [],
            "fusion_mode_counts": {},
            "final_label_counts": {},
            "unsupported_stages": list(self.unsupported_stages),
        }


def _json_bytes(payload: dict[str, Any] | list[Any]) -> bytes:
    return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")


def create_handler(
    data: ProjectData,
    runs: RunManager,
    auth: AuthManager,
    input_preparer: InputPreparer,
    frame_manager: Phase3FrameManager,
    detector_service: Any,
    tracking_service: TrackingService,
    temporal_service: TemporalAnalysisService,
    secure_cookies: bool,
) -> type[BaseHTTPRequestHandler]:
    class APIHandler(BaseHTTPRequestHandler):
        server_version = "BeyondTheFrameBackend/1.0"

        def _respond(
            self,
            status: int,
            payload: dict[str, Any] | list[Any],
            headers: dict[str, str] | None = None,
        ) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, error: str, message: str) -> None:
            self._respond(status, {"error": error, "message": message})

        def _respond_bytes(
            self,
            status: int,
            content_type: str,
            body: bytes,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _respond_video_file(self, path: Path, content_type: str) -> None:
            size = path.stat().st_size
            start = 0
            end = size - 1
            status = 200
            headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            }
            range_header = self.headers.get("Range")
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match or not any(match.groups()) or size <= 0:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                first, last = match.groups()
                if not first:
                    suffix_length = int(last)
                    if suffix_length <= 0:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    start = max(0, size - suffix_length)
                else:
                    start = int(first)
                    end = min(int(last), end) if last else end
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = source.read(min(64 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

        def _multipart_video(self) -> tuple[str, bytes]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.casefold().startswith("multipart/form-data"):
                raise RunError(400, "invalid_upload", "Expected multipart/form-data")
            try:
                content_length = int(self.headers.get("Content-Length", ""))
            except ValueError as error:
                raise RunError(411, "content_length_required", "A valid Content-Length is required") from error
            if content_length <= 0:
                raise RunError(400, "empty_upload", "Upload request is empty")
            if content_length > runs.max_upload_bytes + 65536:
                raise RunError(413, "upload_too_large", "Upload exceeds the configured size limit")
            body = self.rfile.read(content_length)
            if len(body) != content_length:
                raise RunError(400, "invalid_upload", "Upload request ended before Content-Length")
            try:
                message = BytesParser(policy=policy.default).parsebytes(
                    b"MIME-Version: 1.0\r\nContent-Type: "
                    + content_type.encode("ascii")
                    + b"\r\n\r\n"
                    + body
                )
            except (UnicodeEncodeError, ValueError) as error:
                raise RunError(400, "invalid_upload", "Malformed multipart request") from error
            if not message.is_multipart():
                raise RunError(400, "invalid_upload", "Expected a multipart video field")
            parts = list(message.iter_parts())
            file_parts = [
                part
                for part in parts
                if part.get_content_disposition() == "form-data"
                and part.get_filename()
            ]
            if (
                len(file_parts) != 1
                or file_parts[0].get_param("name", header="content-disposition") != "video"
            ):
                raise RunError(400, "invalid_upload", "Upload exactly one file field named 'video'")
            part = file_parts[0]
            payload = part.get_payload(decode=True)
            if payload is None:
                raise RunError(400, "invalid_upload", "Could not decode uploaded video")
            return part.get_filename(), payload

        def _multipart_input(self) -> tuple[str, str, str, bytes]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.casefold().startswith("multipart/form-data"):
                raise RunError(400, "invalid_upload", "Expected multipart/form-data.")
            try:
                content_length = int(self.headers.get("Content-Length", ""))
            except ValueError as error:
                raise RunError(411, "content_length_required", "A valid Content-Length is required.") from error
            if content_length <= 0:
                raise RunError(400, "empty_upload", "Upload request is empty.")
            if content_length > runs.max_upload_bytes + 65536:
                raise RunError(413, "upload_too_large", "Upload exceeds the configured size limit.")
            body = self.rfile.read(content_length)
            if len(body) != content_length:
                raise RunError(400, "invalid_upload", "Upload request ended before Content-Length.")
            try:
                message = BytesParser(policy=policy.default).parsebytes(
                    b"MIME-Version: 1.0\r\nContent-Type: "
                    + content_type.encode("ascii")
                    + b"\r\n\r\n"
                    + body
                )
            except (UnicodeEncodeError, ValueError) as error:
                raise RunError(400, "invalid_upload", "Malformed multipart request.") from error
            if not message.is_multipart():
                raise RunError(400, "invalid_upload", "Expected an uploaded input file.")
            fields: dict[str, str] = {}
            files = []
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                name = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    files.append(part)
                    continue
                if name in {"source", "input_type"} and name not in fields:
                    value = part.get_content()
                    if isinstance(value, str):
                        fields[name] = value.strip()
            if len(files) != 1 or files[0].get_param("name", header="content-disposition") != "file":
                raise RunError(400, "invalid_upload", "Upload exactly one file field named 'file'.")
            payload = files[0].get_payload(decode=True)
            if payload is None:
                raise RunError(400, "invalid_upload", "Could not decode uploaded input.")
            return fields.get("source", ""), fields.get("input_type", ""), files[0].get_filename(), payload

        @staticmethod
        def _assessment(final_label: str) -> dict[str, str]:
            return {
                "assessment": "MANUAL_REVIEW",
                "reason": (
                    f"Fusion classified this track as {final_label}; scores are heuristic "
                    "and do not establish operational threat certainty."
                ),
            }

        def _run_data(self, run_id: str) -> ProjectData:
            user = self._authenticated_user()
            if user is None:
                raise RunError(401, "authentication_required", "Authentication is required.")
            record = runs.get_owned(run_id, user[0])
            run_data = runs.project_data(record)
            if isinstance(run_data, ThermalRunData):
                run_data.run_status = record.status
                run_data.run_manifest.setdefault("run_id", record.run_id)
            return run_data

        def _owned_run(self, run_id: str):
            user = self._authenticated_user()
            if user is None:
                raise RunError(401, "authentication_required", "Authentication is required.")
            return runs.get_owned(run_id, user[0])

        def _session_token(self) -> str | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = cookie.get(SESSION_COOKIE_NAME)
            return morsel.value if morsel else None

        def _reset_phase_state(self, owner: str | None) -> None:
            tracking_service.reset_owner(owner)
            temporal_service.reset_owner(owner)

        def _authenticated_user(self) -> tuple[int, str] | None:
            token = self._session_token()
            user = auth.get_session(token)
            if user is not None:
                profile = auth.account_profile(user[0])
                if not profile or not profile.get("is_active", True):
                    auth.invalidate_session(token)
                    user = None
                elif profile.get("role") == "sector" and not is_sector_active(
                    data.settings.project_root, str(profile.get("sector_number") or "")
                ):
                    auth.invalidate_session(token)
                    user = None
            if user is None:
                self._reset_phase_state(token)
                input_preparer.stop_cctv_for_session(token)
                frame_manager.close_session(token)
            return user

        def _require_authentication(self) -> bool:
            user = self._authenticated_user()
            if user is None:
                self._error(401, "authentication_required", "Authentication is required.")
                return False
            profile = auth.account_profile(user[0])
            if profile and profile.get("must_change_password"):
                self._error(403, "password_change_required", "Set your own password before using this account.")
                return False
            return True

        def _require_headquarters_admin(self) -> bool:
            user = self._authenticated_user()
            profile = auth.account_profile(user[0]) if user else None
            if profile and profile.get("role") == "headquarters_admin":
                return True
            self._error(403, "headquarters_admin_required", "This endpoint is restricted to Headquarters administrators.")
            return False

        def _auth_request_payload(self, max_bytes: int = 8192) -> dict[str, Any]:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise APIError(400, "invalid_request", "Invalid authentication request.") from error
            if content_length < 0 or content_length > max_bytes:
                raise APIError(400, "invalid_request", "Invalid authentication request.")
            body = self.rfile.read(content_length)
            if len(body) != content_length:
                raise APIError(400, "invalid_request", "Invalid authentication request.")
            if not body:
                return {}
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise APIError(400, "invalid_request", "Invalid authentication request.") from error
            if not isinstance(payload, dict):
                raise APIError(400, "invalid_request", "Invalid authentication request.")
            return payload

        def do_GET(self) -> None:
            try:
                parsed = urlsplit(self.path)
                if parsed.query:
                    raise APIError(400, "malformed_request", "Query parameters are not supported")
                path = unquote(parsed.path)
                if path == "/api/auth/session":
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    self._respond(
                        200,
                        {"authenticated": user is not None, **({"username": user[1], **profile} if profile else {})},
                    )
                    return
                if path == "/api/auth/history":
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._respond(200, {"sessions": auth.session_history(user[0])})
                    return
                if path == "/api/sector-registry/sectors":
                    payload = load_headquarters_sectors(data.settings.project_root)
                    self._respond(200, {"sectors": payload["sectors"], "source": payload["source"]})
                    return
                if path == "/api/sector-registry/approved-sectors":
                    self._respond(200, {"sectors": sector_registry.list_active_approved_sectors(data.settings.project_root)})
                    return
                if path == "/api/sectors/active":
                    if not self._require_authentication(): return
                    self._respond(200, {"sectors": sector_registry.active_sector_geometries(data.settings.project_root), "simulation_only": True})
                    return
                if path == "/api/operator/location":
                    if not self._require_authentication(): return
                    user = self._authenticated_user()
                    self._respond(200, operator_locations.get_location(data.settings.project_root, user[0]))
                    return
                if path == "/api/sector/dashboard":
                    if not self._require_authentication(): return
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    if not profile or profile.get("role") != "sector":
                        self._error(403, "sector_account_required", "This page is restricted to sector accounts."); return
                    headquarters = load_headquarters_sectors(data.settings.project_root)
                    sector_number = str(profile.get("sector_number") or "").upper()
                    sector = next((item for item in headquarters["sectors"] if item["id"].upper() == sector_number), None)
                    if sector is None or not sector.get("is_active", True):
                        self._error(403, "sector_decommissioned", "This sector account has been decommissioned by Headquarters."); return
                    active_sectors = sector_registry.active_sector_geometries(data.settings.project_root)
                    assigned = next((item for item in active_sectors if item["id"].upper() == sector_number), None)
                    # Keep a legacy configured rectangle visible when an older approved account has no registry geometry.
                    if assigned is None:
                        assigned = {"id": sector["id"], "name": sector["name"], "bounds": sector["bounds"], "geometry": None}
                    summary = build_sector_dashboard_summary(
                        data.settings.project_root,
                        sector_number,
                        runs.list_runs(user[0]),
                    )
                    self._respond(200, {"username": user[1], "sector": assigned, "sectors": active_sectors, "config": headquarters["config"], "summary": summary, "simulation_only": True})
                    return
                track_detail_match = None
                if path.startswith("/api/") and path != "/api/health":
                    if not self._require_authentication():
                        return
                    track_detail_match = re.fullmatch(r"/api/authorization/tracks/([1-9][0-9]*)/([^/]{1,240})", path)
                    sector_track_read = path in {"/api/auth/history", "/api/authorization/trajectory/latest", "/api/authorization/tracks"} or track_detail_match is not None
                    sector_run_read = path == "/api/runs" or path.startswith("/api/runs/")
                    sector_live_input_read = re.fullmatch(
                        r"/api/input/cctv/(cctv:[A-Za-z0-9_-]{24})/(handoff|frame)",
                        path,
                    ) is not None
                    if (
                        not sector_track_read
                        and not sector_run_read
                        and not sector_live_input_read
                        and not self._require_headquarters_admin()
                    ):
                        return
                if path in {"/api/authorization/trajectory/latest", "/api/authorization/tracks"} or track_detail_match:
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    scope = str(profile.get("sector_number") or "") if profile and profile.get("role") == "sector" else None
                    if track_detail_match:
                        try:
                            detail = get_track_detail(data.settings.project_root, int(track_detail_match.group(1)), unquote(track_detail_match.group(2)), scope)
                        except ValueError as error:
                            raise RunError(400, "invalid_track_reference", str(error)) from error
                        if detail is None:
                            raise RunError(404, "track_not_found", "Track was not found.")
                        self._respond(200, {"track": detail})
                    elif path == "/api/authorization/tracks":
                        self._respond(200, {"tracks": list_tracks(data.settings.project_root, scope)})
                    else:
                        self._respond(200, {"assessments": latest_trajectory_assessments(data.settings.project_root, scope)})
                    return
                if path == "/api/headquarters/operator-locations":
                    self._respond(200, {"locations": operator_locations.list_locations(data.settings.project_root), "precision": "approximately 100 m", "opt_in_only": True})
                    return
                if path == "/api/headquarters/password-resets":
                    self._respond(200, {"requests": sector_registry.list_resets(data.settings.project_root)})
                    return
                if path == "/api/headquarters/dashboard-trends":
                    accounts = auth.list_sector_accounts()
                    sector_runs = [
                        (str(account["sector_number"]), runs.list_runs(int(account["user_id"])))
                        for account in accounts
                        if account.get("sector_number")
                    ]
                    self._respond(200, build_headquarters_trends(sector_runs))
                    return
                personnel_history_match = re.fullmatch(
                    r"/api/headquarters/sectors/(S[1-9][0-9]*)/personnel-history",
                    path,
                )
                if personnel_history_match:
                    self._respond(200, {
                        "history": sector_registry.list_personnel_history(
                            data.settings.project_root,
                            personnel_history_match.group(1),
                        ),
                    })
                    return
                personnel_history_document = re.fullmatch(
                    r"/api/headquarters/personnel-history/([1-9][0-9]*)/work-id",
                    path,
                )
                if personnel_history_document:
                    user = self._authenticated_user()
                    try:
                        body, content_type = sector_registry.get_personnel_history_work_id(
                            data.settings.project_root,
                            int(personnel_history_document.group(1)),
                            user[0],
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond_bytes(200, content_type, body, {
                        "Content-Disposition": "inline; filename=historical-work-id",
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    })
                    return
                if path == "/api/headquarters/users":
                    self._respond(200, {
                        "accounts": auth.list_sector_accounts(),
                        "approved_sectors": sector_registry.list_approved_sector_users(data.settings.project_root),
                        "decommissioned_sectors": sector_registry.list_decommissioned_sectors(data.settings.project_root),
                    })
                    return
                if path == "/api/headquarters/registry":
                    user = self._authenticated_user()
                    status = "pending"
                    self._respond(200, {"requests": sector_registry.list_requests(data.settings.project_root, user[0], status)})
                    return
                work_id_match = re.fullmatch(r"/api/headquarters/registry/(REG_[0-9a-f]{32})/work-id(?:/(head|technical1|technical2))?", path)
                if work_id_match:
                    user = self._authenticated_user()
                    member_key = work_id_match.group(2) or "head"
                    try:
                        body, content_type = sector_registry.get_work_id(data.settings.project_root, work_id_match.group(1), user[0], member_key)
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond_bytes(200, content_type, body, {
                        "Content-Disposition": "inline; filename=work-id",
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    })
                    return
                if path == "/api/headquarters/registry/audit":
                    user = self._authenticated_user()
                    self._respond(200, {"entries": sector_registry.audit_entries(data.settings.project_root, user[0])})
                    return
                if path == "/api/headquarters/sectors":
                    self._respond(200, load_headquarters_sectors(data.settings.project_root))
                    return
                if path == "/api/headquarters/drones":
                    self._respond(200, {"drones": list_authorized_drones(data.settings.project_root), "simulation_only": True})
                    return
                if path == "/api/authorization/simulation":
                    user = self._authenticated_user()
                    config = load_authorization_config(data.settings.project_root, user[0])
                    self._respond(200, authorization_simulation_payload(config))
                    return
                live_input_match = re.fullmatch(
                    r"/api/input/cctv/(cctv:[A-Za-z0-9_-]{24})/(handoff|frame)",
                    path,
                )
                if live_input_match:
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    reference_id, resource = live_input_match.groups()
                    if resource == "handoff":
                        self._respond(
                            200,
                            input_preparer.get_cctv_handoff(user[0], reference_id),
                        )
                    else:
                        frame, handoff = input_preparer.get_current_cctv_frame(
                            user[0], reference_id
                        )
                        try:
                            import cv2

                            encoded_ok, encoded = cv2.imencode(".jpg", frame)
                        except Exception as error:
                            raise RunError(
                                500,
                                "frame_encoding_failed",
                                "The current CCTV frame could not be encoded.",
                            ) from error
                        if not encoded_ok:
                            raise RunError(
                                500,
                                "frame_encoding_failed",
                                "The current CCTV frame could not be encoded.",
                            )
                        self._respond_bytes(
                            200,
                            "image/jpeg",
                            encoded.tobytes(),
                            {
                                "X-Input-Source": "cctv",
                                "X-Live-Reference-ID": handoff["live_reference_id"],
                                "X-Frame-Available": "true",
                            },
                        )
                    return
                evidence_match = re.fullmatch(r"/api/runs/([^/]+)/evidence/(.+)", path)
                if evidence_match:
                    run_id, relative_path = evidence_match.groups()
                    self._owned_run(run_id)
                    evidence_path = runs.evidence_path(run_id, relative_path)
                    content_type = mimetypes.guess_type(evidence_path.name)[0] or "application/octet-stream"
                    self._respond_bytes(200, content_type, evidence_path.read_bytes())
                    return
                video_match = re.fullmatch(r"/api/runs/([^/]+)/video", path)
                if video_match:
                    record = self._owned_run(video_match.group(1))
                    if not record.input_path.is_file():
                        raise RunError(404, "video_not_found", "The source video is unavailable.")
                    content_type = mimetypes.guess_type(record.input_filename)[0] or "video/mp4"
                    self._respond_video_file(record.input_path, content_type)
                    return
                # Phase 10: run history
                if path == "/api/runs":
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    user_runs = runs.list_runs(user[0])
                    self._respond(
                        200,
                        {
                            "runs": [r.as_json() for r in user_runs],
                            "count": len(user_runs),
                        },
                    )
                    return
                run_status_match = re.fullmatch(r"/api/runs/([^/]+)", path)
                if run_status_match:
                    self._respond(200, self._owned_run(run_status_match.group(1)).as_json())
                    return
                reuse_match = re.fullmatch(r"/api/runs/([^/]+)/reuse-candidates", path)
                if reuse_match:
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    record = runs.get_owned(reuse_match.group(1), user[0])
                    self._respond(
                        200,
                        {
                            "run_id": record.run_id,
                            "candidates": runs.reuse_candidates(record, user[0]),
                        },
                    )
                    return
                resume_match = re.fullmatch(r"/api/runs/([^/]+)/resume-checkpoint", path)
                if resume_match:
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._owned_run(resume_match.group(1))
                    self._respond(
                        200,
                        {
                            "run_id": resume_match.group(1),
                            **runs.resume_checkpoint(resume_match.group(1), user[0]),
                        },
                    )
                    return
                early_alert_match = re.fullmatch(r"/api/runs/([^/]+)/early-alert", path)
                if early_alert_match:
                    record = self._owned_run(early_alert_match.group(1))
                    alert_path = record.root / "early_alert.json"
                    early_alert = (
                        json.loads(alert_path.read_text(encoding="utf-8"))
                        if alert_path.is_file()
                        else None
                    )
                    if (
                        isinstance(early_alert, dict)
                        and early_alert.get("state") == "provisional"
                        and record.status in {"failed", "cancelled"}
                    ):
                        early_alert = {
                            **early_alert,
                            "uav_alert": False,
                            "state": "interrupted",
                            "message": (
                                "The run ended before final evidence fusion. "
                                "This preliminary detector candidate was not confirmed."
                            ),
                        }
                    if early_alert is not None and not isinstance(early_alert.get("uav_alert"), bool):
                        raise ValueError("Persisted early alert must have a boolean UAV status")
                    active_early_alert = (
                        early_alert if early_alert is not None and early_alert["uav_alert"] else None
                    )
                    self._respond(
                        200,
                        {
                            "run_id": record.run_id,
                            "run_status": record.status,
                            "early_alert": early_alert,
                            "alert_status": (
                                "detector_only"
                                if record.input_type == "thermal" and record.status == "completed"
                                else "pending"
                            ) if early_alert is None else str(early_alert.get("state", "available")),
                            "alert": (
                                {
                                    "level": early_alert["level"],
                                    "message": early_alert["message"],
                                    "timestamp": early_alert["published_at"],
                                    "state": early_alert.get("state", "available"),
                                    "provisional": early_alert.get("state") == "provisional",
                                    "first_detection_frame": early_alert.get("first_detection_frame"),
                                    "published_frame": early_alert.get("published_frame"),
                                    "evidence": (
                                        [
                                            {
                                                "track_id": int(early_alert["track_id"]),
                                                "observation": _safe_resolve_alert_evidence(
                                                    record,
                                                    early_alert["track_id"],
                                                    requested_frame_index=(
                                                        early_alert.get("published_frame")
                                                        if early_alert.get("state") == "provisional"
                                                        and isinstance(early_alert.get("published_frame"), int)
                                                        else None
                                                    ),
                                                ),
                                            }
                                        ]
                                        if active_early_alert is not None
                                        and isinstance(early_alert.get("track_id"), str)
                                        and early_alert["track_id"].isdecimal()
                                        else []
                                    ),
                                }
                                if active_early_alert is not None
                                else None
                            ),
                            "alert_track_ids": (
                                [early_alert["track_id"]]
                                if active_early_alert is not None
                                else []
                            ),
                        },
                    )
                    return
                frame_match = re.fullmatch(r"/api/runs/([^/]+)/frames/([^/]+)", path)
                if frame_match:
                    run_id, raw_frame_index = frame_match.groups()
                    if not re.fullmatch(r"[0-9]+", raw_frame_index):
                        raise RunError(
                            400,
                            "invalid_frame_index",
                            "frame_index must be a positive integer.",
                        )
                    try:
                        frame_index = int(raw_frame_index)
                    except ValueError as error:
                        raise RunError(
                            400,
                            "invalid_frame_index",
                            "frame_index must be a positive integer.",
                        ) from error
                    if frame_index < 1:
                        raise RunError(
                            400,
                            "invalid_frame_index",
                            "frame_index must be a positive integer.",
                        )
                    record = self._owned_run(run_id)
                    metadata = record.input_metadata
                    raw_total_frames = metadata.get("frame_count")
                    total_frames = (
                        int(raw_total_frames)
                        if isinstance(raw_total_frames, (int, float))
                        and not isinstance(raw_total_frames, bool)
                        and math.isfinite(raw_total_frames)
                        and raw_total_frames > 0
                        else None
                    )
                    if total_frames is not None and frame_index > total_frames:
                        raise RunError(
                            400,
                            "invalid_frame_index",
                            "frame_index exceeds the uploaded video's frame count.",
                        )
                    tracks_path = record.root / "tracking" / "detector" / "tracks.csv"
                    if not tracks_path.is_file():
                        raise RunError(
                            409,
                            "frame_results_unavailable",
                            "Tracked frame results are not available for this run yet.",
                        )
                    required = {
                        "frame",
                        "track_id",
                        "class_name",
                        "confidence",
                        "x1",
                        "y1",
                        "x2",
                        "y2",
                    }
                    with tracks_path.open(newline="", encoding="utf-8-sig") as source:
                        reader = csv.DictReader(source)
                        if not required.issubset(reader.fieldnames or ()):
                            raise ValueError("Run tracking CSV is missing required columns")
                        observations = []
                        for row in reader:
                            if any(value is None for value in row.values()):
                                raise ValueError("Run tracking CSV contains a malformed row")
                            try:
                                stored_frame = int(row["frame"])
                            except (TypeError, ValueError) as error:
                                raise ValueError("Run tracking CSV contains an invalid frame index") from error
                            if stored_frame != frame_index:
                                continue
                            try:
                                track_id = int(row["track_id"])
                                confidence = float(row["confidence"])
                                bbox = {
                                    key: float(row[key])
                                    for key in ("x1", "y1", "x2", "y2")
                                }
                            except (TypeError, ValueError) as error:
                                raise ValueError(
                                    "Run tracking CSV contains an invalid observation"
                                ) from error
                            if (
                                track_id < 0
                                or not row["class_name"]
                                or not math.isfinite(confidence)
                                or not 0 <= confidence <= 1
                                or not all(math.isfinite(value) for value in bbox.values())
                            ):
                                raise ValueError(
                                    "Run tracking CSV contains an invalid observation"
                                )
                            observations.append(
                                {
                                    "track_id": track_id,
                                    "class": row["class_name"],
                                    "confidence": confidence,
                                    "bbox": bbox,
                                }
                            )
                    raw_fps = metadata.get("fps")
                    timestamp = (
                        (frame_index - 1) / raw_fps
                        if isinstance(raw_fps, (int, float))
                        and not isinstance(raw_fps, bool)
                        and math.isfinite(raw_fps)
                        and raw_fps > 0
                        else None
                    )
                    self._respond(
                        200,
                        {
                            "run_id": run_id,
                            "frame_index": frame_index,
                            "total_frames": total_frames,
                            "timestamp": timestamp,
                            "observations": observations,
                        },
                    )
                    return
                run_match = re.fullmatch(
                    r"/api/runs/([^/]+)/(overview|tracks|fusion/summary|report|tracks/([^/]+)(?:/(evidence|vlm|modalities|fusion))?)",
                    path,
                )
                if run_match:
                    run_id, endpoint, raw_track_id, resource = run_match.groups()
                    run_data = self._run_data(run_id)
                    if endpoint == "overview":
                        result = run_data.overview()
                    elif endpoint == "tracks":
                        result = {"tracks": run_data.track_list(), "count": len(run_data.tracks)}
                    elif endpoint == "fusion/summary":
                        result = run_data.fusion_summary()
                    elif endpoint == "report":
                        # Phase 10: structured report from persisted final_results.json
                        record = self._owned_run(run_id)
                        final_path = record.root / "final_results.json"
                        if not final_path.is_file():
                            raise RunError(404, "report_not_ready", "Final results report is not yet available for this run")
                        report_data = json.loads(final_path.read_text(encoding="utf-8"))
                        report_data["run_id"] = run_id
                        report_data["source_video_id"] = record.source_video_id
                        report_data["run_status"] = record.status
                        report_data["completed_at"] = record.completed_at
                        if report_data.get("analysis_mode") == "thermal_detection_tracking_only":
                            stored_track_ids = report_data.get("alert_track_ids")
                            if not isinstance(stored_track_ids, list):
                                stored_track_ids = [
                                    str(track["track_id"])
                                    for track in report_data.get("tracks", [])
                                    if str(
                                        track.get("detector_class", track.get("class", ""))
                                    ).strip().casefold() in {"drone", "uav"}
                                ]
                            report_data["alert_track_ids"] = stored_track_ids
                            report_data["uav_alert"] = bool(stored_track_ids)
                            report_data["alert_status"] = "detector_only"
                            report_data["alert_level"] = "REVIEW" if stored_track_ids else None
                            report_data["alert_message"] = (
                                "The thermal detector classified one or more tracks as Drone. "
                                "This is an unverified detector output; review the evidence before acting."
                                if stored_track_ids
                                else "The thermal detector completed without any Drone-class tracks."
                            )
                        if report_data.get("uav_alert"):
                            stored_track_ids = report_data.get("alert_track_ids")
                            positive_track_ids = (
                                [str(track_id) for track_id in stored_track_ids]
                                if isinstance(stored_track_ids, list)
                                else [
                                    str(track["track_id"])
                                    for track in report_data.get("tracks", [])
                                    if track.get("final_label") == "UAV"
                                ]
                            )
                            timing_fields = {}
                            early_alert_path = record.root / "early_alert.json"
                            if early_alert_path.is_file():
                                try:
                                    saved_early_alert = json.loads(
                                        early_alert_path.read_text(encoding="utf-8")
                                    )
                                except (OSError, json.JSONDecodeError):
                                    saved_early_alert = None
                                if (
                                    isinstance(saved_early_alert, dict)
                                    and saved_early_alert.get("uav_alert") is True
                                    and str(saved_early_alert.get("track_id", "")) in positive_track_ids
                                ):
                                    timing_fields = {
                                        "timestamp": saved_early_alert.get("published_at"),
                                        "first_detection_frame": saved_early_alert.get("first_detection_frame"),
                                        "published_frame": saved_early_alert.get("published_frame"),
                                    }
                            report_data["alert"] = {
                                **timing_fields,
                                "detector_only": report_data.get("alert_status") == "detector_only",
                                "level": report_data.get("alert_level") or "REVIEW",
                                "message": report_data.get("alert_message")
                                or "The detector/temporal pipeline flagged a possible UAV. This result has not been independently verified; review the evidence before acting.",
                                "evidence": [
                                    {
                                        "track_id": int(track_id),
                                        "observation": _resolve_alert_evidence(record, track_id),
                                    }
                                    for track_id in positive_track_ids
                                    if track_id.isdecimal()
                                ],
                            }
                        else:
                            report_data["alert"] = None
                        result = report_data
                    else:
                        if not raw_track_id or not raw_track_id.isdecimal():
                            raise RunError(400, "malformed_track_id", "track_id must be a non-negative integer")
                        track_id = str(int(raw_track_id))
                        if resource == "evidence":
                            result = run_data.evidence(track_id)
                            for item in result:
                                item["crop_path"] = (
                                    f"/api/runs/{run_id}/evidence/"
                                    + quote(item["crop_path"], safe="/")
                                )
                        elif resource == "vlm":
                            run_data._track(track_id)
                            result = run_data._vlm_result(track_id)
                        elif resource == "modalities":
                            result = run_data.modalities(track_id)
                        elif resource == "fusion":
                            result = run_data.fusion_result(track_id)
                            result["assessment"] = (
                                {
                                    "assessment": "NOT_RUN",
                                    "reason": "Assessment is not implemented for thermal runs.",
                                }
                                if getattr(run_data, "thermal_only", False)
                                else self._assessment(result["final_label"])
                            )
                        else:
                            result = run_data.track_detail(track_id)
                            result["assessment"] = (
                                {
                                    "assessment": "NOT_RUN",
                                    "reason": "Assessment is not implemented for thermal runs.",
                                }
                                if getattr(run_data, "thermal_only", False)
                                else self._assessment(result["fusion"]["final_label"])
                            )
                            for item in result["selected_evidence"]:
                                item["crop_path"] = (
                                    f"/api/runs/{run_id}/evidence/"
                                    + quote(item["crop_path"], safe="/")
                                )
                    self._respond(200, result)
                    return
                if path == "/api/health":
                    self._respond(200, {"status": "ok"})
                    return
                if path == "/api/overview":
                    self._respond(200, data.overview())
                    return
                if path == "/api/tracks":
                    self._respond(200, {"tracks": data.track_list(), "count": len(data.tracks)})
                    return
                if path == "/api/modalities":
                    self._respond(200, data.modalities())
                    return
                if path == "/api/fusion/summary":
                    self._respond(200, data.fusion_summary())
                    return

                match = re.fullmatch(r"/api/tracks/([^/]+)(?:/(evidence|vlm|modalities|fusion))?", path)
                if not match:
                    raise APIError(404, "not_found", "API endpoint does not exist")
                raw_track_id, resource = match.groups()
                if not raw_track_id.isdecimal():
                    raise APIError(400, "malformed_track_id", "track_id must be a non-negative integer")
                track_id = str(int(raw_track_id))
                if resource == "evidence":
                    result = data.evidence(track_id)
                elif resource == "vlm":
                    data._track(track_id)
                    result = data._vlm_result(track_id)
                elif resource == "modalities":
                    result = data.modalities(track_id)
                elif resource == "fusion":
                    result = data.fusion_result(track_id)
                elif resource is None:
                    result = data.track_detail(track_id)
                else:
                    raise APIError(404, "not_found", "API endpoint does not exist")
                self._respond(200, result)
            except APIError as error:
                self._error(error.status, error.error, error.message)
            except RunError as error:
                self._error(error.status, error.error, error.message)
            except (BrokenPipeError, ConnectionResetError):
                LOGGER.debug("Client disconnected while receiving an API response")
            except Exception:
                LOGGER.exception("Unhandled backend API request error")
                self._error(500, "internal_error", "The backend could not complete the request")

        def do_POST(self) -> None:
            try:
                parsed = urlsplit(self.path)
                if parsed.query:
                    raise RunError(400, "malformed_request", "Query parameters are not supported")
                path = unquote(parsed.path)
                if path == "/api/sector-registry/geocode":
                    payload = self._auth_request_payload(max_bytes=4_096)
                    try:
                        result = sector_registry.geocode_address(payload.get("address"))
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond(200, result); return
                if path == "/api/sector-registry/requests":
                    payload = self._auth_request_payload(max_bytes=22_000_000)
                    try:
                        result = sector_registry.submit_request(
                            data.settings.project_root, payload, auth.headquarters_admin_id(),
                            payload.get("filename"), payload.get("content_type"), payload.get("document_base64"),
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond(201, result); return
                if path == "/api/auth/login":
                    payload = self._auth_request_payload()
                    username = payload.get("username")
                    password = payload.get("password")
                    sector_number = payload.get("sector_number")
                    account_type = payload.get("account_type")
                    user = (
                        auth.authenticate(username, password, sector_number, account_type)
                        if isinstance(username, str)
                        and isinstance(password, str)
                        and (sector_number is None or isinstance(sector_number, str))
                        and (account_type is None or account_type in {"headquarters_admin", "sector"})
                        and (account_type != "sector" or (isinstance(sector_number, str) and bool(sector_number.strip())))
                        and len(username) <= 256
                        and len(password) <= 256
                        and (sector_number is None or len(sector_number) <= 32)
                        else None
                    )
                    if user is None:
                        self._error(401, "invalid_credentials", "Invalid username or password.")
                        return
                    profile = auth.account_profile(user[0]) or {}
                    if profile.get("role") == "sector" and not is_sector_active(
                        data.settings.project_root, str(profile.get("sector_number") or "")
                    ):
                        self._error(401, "sector_decommissioned", "This sector is inactive. Ask Headquarters for a new registration approval.")
                        return
                    token = auth.create_session(user[0], user[1], self._session_token())
                    input_preparer.stop_cctv_for_session(self._session_token())
                    frame_manager.close_session(self._session_token())
                    profile = auth.account_profile(user[0]) or {}
                    self._respond(
                        200,
                        {"authenticated": True, **profile},
                        {"Set-Cookie": cookie_header(token, secure_cookies)},
                    )
                    return
                if path == "/api/sector-registry/password-reset":
                    if not self._require_authentication(): return
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    if not profile or profile.get("role") != "sector":
                        self._error(403, "sector_account_required", "A signed-in sector account is required."); return
                    payload = self._auth_request_payload()
                    try:
                        result = sector_registry.request_reset(
                            data.settings.project_root, payload.get("request_id"),
                            str(profile.get("sector_number") or ""), user[0], payload.get("head_member_id", ""),
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond(201, result); return
                if path == "/api/auth/logout":
                    user = self._authenticated_user()
                    if user is not None:
                        input_preparer.stop_cctv(user[0])
                        frame_manager.close_user(user[0])
                    self._reset_phase_state(self._session_token())
                    auth.invalidate_session(self._session_token())
                    self._respond(
                        200,
                        {"authenticated": False},
                        {"Set-Cookie": expired_cookie_header(secure_cookies)},
                    )
                    return
                if path == "/api/operator/location":
                    if not self._require_authentication(): return
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    payload = self._auth_request_payload()
                    try:
                        result = operator_locations.save_location(data.settings.project_root, user[0], str(profile.get("role") if profile else ""), profile.get("sector_number") if profile else None, payload)
                    except ValueError as error:
                        raise RunError(400, "invalid_operator_location", str(error)) from error
                    self._respond(200, result); return
                if path == "/api/auth/change-password":
                    if self._authenticated_user() is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    current_password = payload.get("current_password")
                    new_password = payload.get("new_password")
                    if not isinstance(current_password, str) or not isinstance(new_password, str):
                        raise RunError(400, "invalid_password_change", "Current and new passwords are required.")
                    try:
                        changed = auth.change_password(user[0], current_password, new_password)
                    except ValueError as error:
                        raise RunError(400, "invalid_password_change", str(error)) from error
                    if not changed:
                        raise RunError(401, "invalid_current_password", "Current password is incorrect.")
                    self._respond(200, {"changed": True})
                    return
                if path.startswith("/api/") and not self._require_authentication():
                    return
                sector_operational_post = (
                    path in {
                        "/api/input/prepare",
                        "/api/input/cctv/stop",
                        "/api/phase3/frame",
                        "/api/phase3/stop",
                        "/api/phase4/detect",
                        "/api/phase5/track",
                        "/api/phase5/reset",
                        "/api/phase6/temporal",
                        "/api/phase8/verify",
                        "/api/phase9/fuse",
                        "/api/runs",
                    }
                    or re.fullmatch(
                        r"/api/runs/[^/]+/(?:reanalyze|resume|cancel|start|resume-checkpoint)",
                        path,
                    ) is not None
                    or re.fullmatch(r"/api/videos/V-[1-9][0-9]*/delete", path) is not None
                )
                if (
                    path.startswith("/api/")
                    and path != "/api/authorization/telemetry"
                    and not sector_operational_post
                    and not self._require_headquarters_admin()
                ):
                    return
                personnel_match = re.fullmatch(
                    r"/api/headquarters/sectors/(S[1-9][0-9]*)/personnel",
                    path,
                )
                if personnel_match:
                    payload = self._auth_request_payload(max_bytes=8 * 1024 * 1024)
                    user = self._authenticated_user()
                    try:
                        result = sector_registry.replace_sector_personnel(
                            data.settings.project_root,
                            personnel_match.group(1),
                            payload,
                            user[0],
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond(200, result)
                    return
                decommission_match = re.fullmatch(r"/api/headquarters/sectors/([A-Za-z0-9_-]{1,32})/decommission", path)
                if decommission_match:
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        result = sector_registry.decommission_sector(
                            data.settings.project_root, decommission_match.group(1), user[0], payload.get("reason")
                        )
                        account_locked = auth.decommission_sector_account(result["sector_id"], str(payload.get("reason") or ""))
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    self._respond(200, {**result, "account_locked": account_locked})
                    return
                registry_decision = re.fullmatch(r"/api/headquarters/registry/(REG_[0-9a-f]{32})/decision", path)
                if registry_decision:
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        result = sector_registry.decide_request(
                            data.settings.project_root, auth, registry_decision.group(1), user[0],
                            payload.get("decision"), payload.get("note", ""), payload.get("geometry"),
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    except ValueError as error:
                        raise RunError(400, "invalid_registry_decision", str(error)) from error
                    # Never persist or repeat plaintext credentials; local fallback returns one one-time HQ display only.
                    if result.get("account"):
                        account = result["account"]
                        result["account"] = {k:v for k,v in account.items() if k != "temporary_password"}
                        if not result.get("delivery", {}).get("sent"):
                            result["delivery"]["temporary_password"] = account.get("temporary_password")
                    self._respond(200, result); return
                reset_decision = re.fullmatch(r"/api/headquarters/password-reset/(RST_[0-9a-f]{32})/decision", path)
                if reset_decision:
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        result = sector_registry.decide_reset(
                            data.settings.project_root, auth, reset_decision.group(1), user[0],
                            payload.get("decision") == "approved",
                        )
                    except sector_registry.RegistryError as error:
                        self._error(error.status, error.code, str(error)); return
                    if result.get("account"):
                        account=result["account"]
                        result["account"]={k:v for k,v in account.items() if k!="temporary_password"}
                        if not result.get("delivery",{}).get("sent"):
                            result["delivery"]["temporary_password"]=account.get("temporary_password")
                    self._respond(200,result); return
                if path == "/api/headquarters/sectors":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        result = save_headquarters_sectors(data.settings.project_root, payload, user[0])
                    except ValueError as error:
                        raise RunError(400, "invalid_headquarters_sectors", str(error)) from error
                    self._respond(200, result)
                    return
                if path == "/api/headquarters/drones":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        result = register_approved_drone(data.settings.project_root, payload, user[0])
                    except ValueError as error:
                        raise RunError(400, "invalid_authorized_drone", str(error)) from error
                    self._respond(201, {"drone": result, "simulation_only": True})
                    return
                review_match = re.fullmatch(r"/api/headquarters/tracks/([1-9][0-9]*)/([^/]{1,240})/review", path)
                if review_match:
                    payload = self._auth_request_payload(max_bytes=4096)
                    user = self._authenticated_user()
                    try:
                        detail = review_track(data.settings.project_root, int(review_match.group(1)), unquote(review_match.group(2)), user[0], payload.get("decision"), payload.get("reason"))
                    except ValueError as error:
                        raise RunError(400, "invalid_track_review", str(error)) from error
                    except LookupError as error:
                        raise RunError(404, "track_not_found", str(error)) from error
                    self._respond(200, {"track": detail})
                    return
                if path == "/api/authorization/telemetry":
                    payload = self._auth_request_payload(max_bytes=64000)
                    user = self._authenticated_user()
                    profile = auth.account_profile(user[0]) if user else None
                    scope = str(profile.get("sector_number") or "") if profile and profile.get("role") == "sector" else None
                    try:
                        result = classify_telemetry(data.settings.project_root, payload, scope)
                    except PermissionError as error:
                        raise RunError(403, "sector_scope_denied", str(error)) from error
                    except ValueError as error:
                        raise RunError(400, "invalid_telemetry", str(error)) from error
                    self._respond(200, result)
                    return
                revoke_match = re.fullmatch(r"/api/headquarters/drones/([1-9][0-9]*)/revoke", path)
                if revoke_match:
                    self._auth_request_payload()
                    try:
                        revoked = revoke_authorized_drone(data.settings.project_root, int(revoke_match.group(1)))
                    except ValueError as error:
                        raise RunError(400, "invalid_registry_id", str(error)) from error
                    if not revoked:
                        raise RunError(404, "authorized_drone_not_found", "Approved drone record was not found.")
                    self._respond(200, {"revoked": True})
                    return
                if path == "/api/authorization/config":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    try:
                        config = save_authorization_config(data.settings.project_root, user[0], payload)
                    except ValueError as error:
                        raise RunError(400, "invalid_sector_config", str(error)) from error
                    self._respond(200, {"config": config, "simulation_only": True})
                    return
                if path == "/api/authorization/evaluate":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    config = load_authorization_config(data.settings.project_root, user[0])
                    try:
                        result = evaluate_authorization(payload.get("drone_id"), payload.get("lat"), payload.get("lon"), payload.get("observed_at"), config)
                    except ValueError as error:
                        raise RunError(400, "invalid_telemetry", str(error)) from error
                    self._respond(200, result)
                    return
                if path == "/api/photo/detect":
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise RunError(415, "unsupported_image_type", "Choose a JPEG, PNG, or WebP image.")
                    try:
                        content_length = int(self.headers.get("Content-Length", ""))
                    except ValueError as error:
                        raise RunError(411, "content_length_required", "Image size is required.") from error
                    max_image_bytes = 20 * 1024 * 1024
                    if content_length <= 0 or content_length > max_image_bytes:
                        raise RunError(413, "image_too_large", "Choose an image smaller than 20 MiB.")
                    content = self.rfile.read(content_length)
                    if len(content) != content_length:
                        raise RunError(400, "invalid_image", "The uploaded image was incomplete.")
                    import cv2
                    import numpy as np

                    frame = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is None or frame.size == 0:
                        raise RunError(422, "invalid_image", "The uploaded file is not a readable image.")
                    height, width = frame.shape[:2]
                    if width * height > 40_000_000:
                        raise RunError(413, "image_dimensions_too_large", "Image dimensions must not exceed 40 megapixels.")
                    prepared = PreparedFrame(
                        frame=frame,
                        source_type="photo",
                        input_type="rgb",
                        run_id=None,
                        live_reference_id=None,
                        frame_index=None,
                        timestamp_sec=None,
                        width=width,
                        height=height,
                        channels=3,
                        dtype=str(frame.dtype),
                        validation_status="valid",
                        preprocessing_status="decoded_photo",
                    )
                    detection_result, annotated = detector_service.detect(prepared)
                    encoded_ok, encoded = cv2.imencode(".jpg", annotated)
                    if not encoded_ok:
                        raise RunError(422, "image_encoding_failed", "The detected image could not be prepared for download.")
                    self._respond(200, {
                        "ok": True,
                        **detection_result.as_dict(),
                        "frame_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                    })
                    return
                if path == "/api/phase4/detect":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    source_type = payload.get("source_type")
                    input_type = payload.get("input_type")
                    if source_type == "upload" and input_type not in {"rgb", "thermal"}:
                        raise RunError(
                            415,
                            "modality_not_supported",
                            "Select a supported RGB or Thermal upload.",
                        )
                    if source_type == "cctv" and input_type not in (None, "rgb"):
                        raise RunError(400, "invalid_frame_source", "Select a live CCTV source.")
                    prepared = frame_manager.acquire_next(
                        user[0],
                        self._session_token(),
                        source_type=source_type,
                        input_type=input_type,
                        run_id=payload.get("run_id"),
                        live_reference_id=payload.get("live_reference_id"),
                    )
                    if prepared is None:
                        self._respond(
                            200,
                            {
                                "ok": True,
                                "end_of_video": True,
                                "source_type": source_type,
                                "input_type": input_type,
                                "run_id": payload.get("run_id"),
                                "detection_count": 0,
                                "detections": [],
                            },
                        )
                        return
                    detection_result, annotated = detector_service.detect(prepared)
                    try:
                        import cv2

                        encoded_ok, encoded = cv2.imencode(".jpg", annotated)
                    except Exception as error:
                        raise RunError(
                            422,
                            "frame_encoding_failed",
                            "The detected frame could not be encoded for transfer.",
                        ) from error
                    if not encoded_ok:
                        raise RunError(
                            422,
                            "frame_encoding_failed",
                            "The detected frame could not be encoded for transfer.",
                        )
                    self._respond(
                        200,
                        {
                            "ok": True,
                            "end_of_video": False,
                            **detection_result.as_dict(),
                            "validation_status": prepared.validation_status,
                            "preprocessing_status": prepared.preprocessing_status,
                            "frame_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                        },
                    )
                    return
                if path == "/api/phase3/frame":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    prepared = frame_manager.acquire_next(
                        user[0],
                        self._session_token(),
                        source_type=payload.get("source_type"),
                        input_type=payload.get("input_type"),
                        run_id=payload.get("run_id"),
                        live_reference_id=payload.get("live_reference_id"),
                    )
                    if prepared is None:
                        self._respond(
                            200,
                            {
                                "ok": True,
                                "end_of_video": True,
                                "source_type": payload.get("source_type"),
                                "input_type": payload.get("input_type"),
                                "run_id": payload.get("run_id"),
                                "frame": None,
                            },
                        )
                        return
                    try:
                        import cv2

                        encoded_ok, encoded = cv2.imencode(".jpg", prepared.frame)
                    except Exception as error:
                        raise RunError(
                            422,
                            "frame_encoding_failed",
                            "The prepared frame could not be encoded for transfer.",
                        ) from error
                    if not encoded_ok:
                        raise RunError(
                            422,
                            "frame_encoding_failed",
                            "The prepared frame could not be encoded for transfer.",
                        )
                    self._respond(
                        200,
                        {
                            "ok": True,
                            "end_of_video": False,
                            **prepared.metadata(),
                            "frame_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
                        },
                    )
                    return
                if path == "/api/phase5/track":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    token = self._session_token()
                    source_type = payload.get("source_type")
                    input_type = payload.get("input_type")
                    if source_type == "upload" and input_type not in {"rgb", "thermal"}:
                        self._reset_phase_state(token)
                        raise RunError(
                            415,
                            "modality_not_supported",
                            "Select a supported RGB or Thermal upload.",
                        )
                    if source_type == "cctv" and input_type not in (None, "rgb"):
                        self._reset_phase_state(token)
                        raise RunError(400, "invalid_frame_source", "Select a live CCTV source.")
                    if source_type not in {"upload", "cctv"}:
                        self._reset_phase_state(token)
                        raise RunError(400, "invalid_frame_source", "Select an RGB or Thermal upload, or live CCTV source.")
                    with tracking_service.frame_sequence():
                        try:
                            prepared = frame_manager.acquire_next(
                                user[0],
                                token,
                                source_type=source_type,
                                input_type=input_type,
                                run_id=payload.get("run_id"),
                                live_reference_id=payload.get("live_reference_id"),
                            )
                            if prepared is None:
                                tracking_service.reset_owner(token)
                                self._respond(
                                    200,
                                    {
                                        "ok": True,
                                        "end_of_video": True,
                                        "source_type": source_type,
                                        "input_type": input_type,
                                        "run_id": payload.get("run_id"),
                                        "live_reference_id": payload.get("live_reference_id"),
                                        "active_track_count": 0,
                                        "tracks": [],
                                    },
                                )
                                return
                            detection_result, _ = detector_service.detect(prepared)
                            if not tracking_service.source_is_active(token or "", detection_result):
                                temporal_service.reset_owner(token)
                            track_result = tracking_service.track(token or "", detection_result)
                        except RunError:
                            self._reset_phase_state(token)
                            raise
                        self._respond(
                            200,
                            {
                                "ok": True,
                                "end_of_video": False,
                                **track_result.as_dict(),
                            },
                        )
                    return
                if path == "/api/phase6/temporal":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    source_type = payload.get("source_type")
                    input_type = payload.get("input_type")
                    fps = None
                    if source_type == "upload" and input_type == "rgb":
                        run_id = payload.get("run_id")
                        if not isinstance(run_id, str) or not run_id:
                            raise RunError(400, "missing_run_id", "A prepared RGB run ID is required.")
                        record = runs.get_owned(run_id, user[0])
                        if (
                            record.status != "prepared"
                            or record.input_type != "rgb"
                            or record.input_status != "validated"
                        ):
                            raise RunError(
                                415,
                                "temporal_source_not_supported",
                                "Phase 6 currently supports prepared RGB uploads only.",
                            )
                        raw_fps = record.input_metadata.get("fps")
                        if isinstance(raw_fps, (int, float)) and not isinstance(raw_fps, bool):
                            fps = float(raw_fps)
                    elif source_type == "cctv" and input_type in (None, "rgb"):
                        reference_id = payload.get("live_reference_id")
                        if not isinstance(reference_id, str) or not reference_id:
                            raise RunError(400, "missing_live_reference", "A live CCTV reference is required.")
                        handoff = input_preparer.get_cctv_handoff(user[0], reference_id)
                        if handoff.get("connection_status") != "connected":
                            raise RunError(409, "live_input_not_ready", "The live CCTV input is not connected.")
                    else:
                        raise RunError(
                            415,
                            "temporal_source_not_supported",
                            "Phase 6 currently supports RGB uploads and visible-light CCTV only.",
                        )
                    temporal_result = temporal_service.update(
                        self._session_token() or "",
                        payload,
                        fps=fps,
                        end_of_video=payload.get("end_of_video") is True,
                    )
                    self._respond(200, {"ok": True, **temporal_result})
                    return
                if path == "/api/phase8/verify":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    run_id = payload.get("run_id")
                    if not isinstance(run_id, str) or not run_id:
                        raise RunError(400, "missing_run_id", "A prepared run ID is required.")
                    record = runs.get_owned(run_id, user[0])
                    video_path = record.input_path
                    tracks = payload.get("tracks", [])
                    reliability_statuses = payload.get("reliability_statuses", {})
                    output_dir = record.root / "evidence"
                    selected, results = acquire_evidence_for_tracks(
                        video_path,
                        tracks,
                        reliability_statuses,
                        output_dir,
                        vlm=MockVLM(),
                    )
                    self._respond(
                        200,
                        {
                            "ok": True,
                            "selected_evidence": selected,
                            "vlm_results": results,
                        },
                    )
                    return
                if path == "/api/phase9/fuse":
                    payload = self._auth_request_payload()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    run_id = payload.get("run_id")
                    if not isinstance(run_id, str) or not run_id:
                        raise RunError(400, "missing_run_id", "A prepared run ID is required.")
                    record = runs.get_owned(run_id, user[0])
                    tracks = payload.get("tracks", [])
                    if not isinstance(tracks, list) or not tracks:
                        raise RunError(422, "invalid_fusion_request", "tracks must be a non-empty list.")

                    results = []
                    for track in tracks:
                        if not isinstance(track, dict):
                            raise RunError(422, "invalid_fusion_request", "Each track entry must be an object.")
                        track_id = str(track.get("track_id", ""))
                        if not track_id:
                            raise RunError(422, "invalid_fusion_request", "Each track entry requires a track_id.")

                        det_class = str(track.get("detector_class", track.get("class", ""))).strip()
                        raw_det_score = track.get("detector_score", track.get("confidence", 0.0))
                        try:
                            det_score = float(raw_det_score)
                        except (TypeError, ValueError):
                            raise RunError(422, "invalid_fusion_request", f"Invalid detector score for track {track_id}")
                        if not 0 <= det_score <= 1:
                            raise RunError(422, "invalid_fusion_request", f"detector_score out of range for track {track_id}")

                        det_key = det_class.casefold()
                        if det_key in ("drone", "uav"):
                            det_label = "UAV"
                            det_display = "Drone"
                        elif det_key in ("bird",):
                            det_label = "Bird"
                            det_display = "Bird"
                        else:
                            raise RunError(422, "invalid_fusion_request", f"Unsupported detector class {det_class!r} for track {track_id}")

                        detector_scores = {det_label: det_score}

                        rel_info = track.get("reliability", {})
                        if isinstance(rel_info, dict):
                            raw_rel_score = rel_info.get("reliability_score", track.get("reliability_score", 0.0))
                            rel_status = str(rel_info.get("status", track.get("status", "UNCERTAIN"))).strip().upper()
                        else:
                            raw_rel_score = track.get("reliability_score", 0.0)
                            rel_status = str(track.get("status", "UNCERTAIN")).strip().upper()

                        try:
                            rel_score = float(raw_rel_score)
                        except (TypeError, ValueError):
                            raise RunError(422, "invalid_fusion_request", f"Invalid reliability score for track {track_id}")
                        if not 0 <= rel_score <= 1:
                            raise RunError(422, "invalid_fusion_request", f"reliability_score out of range for track {track_id}")

                        vlm_info = track.get("vlm", {})
                        if isinstance(vlm_info, dict):
                            vlm_label = str(vlm_info.get("label", track.get("vlm_label", ""))).strip()
                            raw_vlm_conf = vlm_info.get("confidence", track.get("vlm_confidence", ""))
                        else:
                            vlm_label = str(track.get("vlm_label", "")).strip()
                            raw_vlm_conf = track.get("vlm_confidence", "")

                        vlm_scores = None
                        vlm_conf_val: float | str = ""
                        if vlm_label and vlm_label.casefold() != "unknown":
                            try:
                                vlm_conf = float(raw_vlm_conf)
                                if 0 <= vlm_conf <= 1:
                                    vlm_conf_val = vlm_conf
                                    mapped_label = {"drone": "UAV", "uav": "UAV", "bird": "Bird", "other": "Other"}.get(vlm_label.casefold())
                                    if mapped_label:
                                        vlm_scores = {mapped_label: vlm_conf}
                            except (TypeError, ValueError):
                                pass
                        elif vlm_label.casefold() == "unknown":
                            vlm_label = "Unknown"
                            try:
                                vlm_conf_val = float(raw_vlm_conf)
                            except (TypeError, ValueError):
                                vlm_conf_val = 0.0

                        final_label, decision_confidence, fusion_mode = fuse_track(
                            detector_scores, rel_score, vlm_scores
                        )

                        temp_sum = track.get("temporal_summary", {})
                        frames_seen = int(track.get("frames_seen", temp_sum.get("frames_seen", 1) if isinstance(temp_sum, dict) else 1))

                        results.append(
                            {
                                "track_id": track_id,
                                "detector_class": det_display,
                                "detector_score": det_score,
                                "reliability_score": rel_score,
                                "vlm_label": vlm_label,
                                "vlm_confidence": vlm_conf_val,
                                "fusion_mode": fusion_mode,
                                "final_label": final_label,
                                "decision_confidence": decision_confidence,
                                "frames_seen": frames_seen,
                                "status": rel_status,
                            }
                        )

                    self._respond(200, {"ok": True, "results": results})
                    return
                if path == "/api/phase5/reset":
                    self._reset_phase_state(self._session_token())
                    self._respond(200, {"ok": True, "tracking_status": "reset"})
                    return
                if path == "/api/phase3/stop":
                    payload = self._auth_request_payload()
                    run_id = payload.get("run_id")
                    if not isinstance(run_id, str) or not run_id:
                        raise RunError(400, "missing_run_id", "A prepared run ID is required.")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._owned_run(run_id)
                    self._respond(
                        200,
                        {
                            "ok": True,
                            "source_type": "upload",
                            "status": "stopped",
                            "released": frame_manager.close_run(user[0], run_id),
                        },
                    )
                    self._reset_phase_state(self._session_token())
                    return
                if path == "/api/input/prepare":
                    content_type = self.headers.get("Content-Type", "").casefold()
                    if content_type.startswith("application/json"):
                        payload = self._auth_request_payload()
                        if payload.get("source") != "cctv":
                            raise RunError(400, "unsupported_source", "Select CCTV or upload a supported input.")
                        action = payload.get("action", "test")
                        user = self._authenticated_user()
                        if user is None:
                            self._error(401, "authentication_required", "Authentication is required.")
                            return
                        if action == "test":
                            result = input_preparer.test_cctv(user[0], payload.get("rtsp_url"))
                        elif action == "start":
                            self._reset_phase_state(self._session_token())
                            result = input_preparer.start_cctv(
                                user[0], payload.get("rtsp_url"), self._session_token()
                            )
                        else:
                            raise RunError(400, "invalid_action", "Use the CCTV test or start action.")
                        self._respond(200, result)
                        return
                    source, input_type, filename, content = self._multipart_input()
                    if source != "upload":
                        raise RunError(400, "unsupported_source", "Select CCTV or upload a supported input.")
                    if not input_type:
                        raise RunError(400, "missing_input_type", "Select RGB, Thermal, or Radar.")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._reset_phase_state(self._session_token())
                    record = input_preparer.prepare_upload(input_type, filename, content, user[0])
                    self._respond(
                        201,
                        {
                            "ok": True,
                            "source": record.source_type,
                            "input_type": record.input_type,
                            "status": record.input_status,
                            "run_id": record.run_id,
                            "created_at": record.created_at,
                            "source_video_id": record.source_video_id,
                            "input_reference": record.input_reference,
                            "input_filename": record.input_filename,
                            "original_filename": record.original_filename,
                            "metadata": record.input_metadata,
                        },
                    )
                    return
                if path == "/api/input/cctv/stop":
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._reset_phase_state(self._session_token())
                    self._respond(200, input_preparer.stop_cctv(user[0]))
                    return
                if path == "/api/runs":
                    filename, payload = self._multipart_video()
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    record = runs.create_run(filename, payload, owner_id=user[0])
                    self._respond(201, {"run_id": record.run_id, "status": record.status})
                    return
                delete_video_match = re.fullmatch(r"/api/videos/(V-[1-9][0-9]*)/delete", path)
                if delete_video_match:
                    if self.headers.get("Content-Length", "0") != "0":
                        raise RunError(400, "malformed_request", "Video deletion requests must not include a body.")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    deleted_run_ids = runs.delete_video_runs(delete_video_match.group(1), user[0])
                    self._respond(200, {"source_video_id": delete_video_match.group(1), "deleted_runs": deleted_run_ids, "deleted_count": len(deleted_run_ids)})
                    return
                reanalyze_match = re.fullmatch(r"/api/runs/([^/]+)/reanalyze", path)
                if reanalyze_match:
                    if self.headers.get("Content-Length", "0") != "0":
                        raise RunError(400, "malformed_request", "Fresh re-analysis requests must not include a body")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    source_record = runs.get_owned(reanalyze_match.group(1), user[0])
                    if (
                        source_record.source_type != "upload"
                        or source_record.input_type not in {"rgb", "thermal"}
                        or not source_record.input_path.is_file()
                    ):
                        raise RunError(
                            409,
                            "source_video_unavailable",
                            "Fresh analysis is available only when the original RGB or Thermal upload is stored.",
                        )
                    record = runs.create_run(
                        source_record.input_filename,
                        source_record.input_path.read_bytes(),
                        original_filename=source_record.original_filename or source_record.input_filename,
                        source_type="upload",
                        input_type=source_record.input_type,
                        input_status="validated",
                        input_metadata=source_record.input_metadata,
                        prepared=True,
                        owner_id=user[0],
                    )
                    self._respond(201, {"run_id": record.run_id, "status": record.status})
                    return
                resume_match = re.fullmatch(r"/api/runs/([^/]+)/resume", path)
                if resume_match:
                    if self.headers.get("Content-Length", "0") != "0":
                        raise RunError(400, "malformed_request", "Resume requests must not include a body")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    record = runs.resume_from_tracking(resume_match.group(1), user[0])
                    self._respond(
                        201,
                        {
                            "run_id": record.run_id,
                            "status": record.status,
                            "resumed_from_run_id": resume_match.group(1),
                            "resume_after_stage": "tracking",
                        },
                    )
                    return
                cancel_match = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
                if cancel_match:
                    if self.headers.get("Content-Length", "0") != "0":
                        raise RunError(400, "malformed_request", "Run cancellation requests must not include a body")
                    if self._authenticated_user() is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    self._owned_run(cancel_match.group(1))
                    record = runs.cancel(cancel_match.group(1))
                    self._respond(200, record.as_json())
                    return
                start_match = re.fullmatch(r"/api/runs/([^/]+)/start", path)
                if start_match:
                    if self.headers.get("Content-Length", "0") != "0":
                        raise RunError(400, "malformed_request", "Run start requests must not include a body")
                    user = self._authenticated_user()
                    if user is None:
                        self._error(401, "authentication_required", "Authentication is required.")
                        return
                    run_id = start_match.group(1)
                    frame_manager.close_run(user[0], run_id)
                    self._reset_phase_state(self._session_token())
                    self._owned_run(run_id)
                    record = runs.start(run_id)
                    self._respond(202, {"run_id": record.run_id, "status": record.status})
                    return
                self._method_not_allowed()
            except APIError as error:
                self._error(error.status, error.error, error.message)
            except RunError as error:
                self._error(error.status, error.error, error.message)
            except (BrokenPipeError, ConnectionResetError):
                LOGGER.debug("Client disconnected while receiving an API response")
            except Exception:
                LOGGER.exception("Unhandled backend upload request error")
                self._error(500, "internal_error", "The backend could not complete the request")

        def _method_not_allowed(self) -> None:
            body = _json_bytes(
                {
                    "error": "method_not_allowed",
                    "message": "Only GET requests are supported",
                }
            )
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed

        def log_message(self, format_string: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), format_string % args)

    return APIHandler


class APIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self) -> None:
        input_preparer = getattr(self, "input_preparer", None)
        frame_manager = getattr(self, "frame_manager", None)
        detector_service = getattr(self, "detector_service", None)
        tracking_service = getattr(self, "tracking_service", None)
        temporal_service = getattr(self, "temporal_service", None)
        stop_purger = getattr(self, "registry_purger_stop", None)
        purger = getattr(self, "registry_purger_thread", None)
        if stop_purger is not None:
            stop_purger.set()
        try:
            if purger is not None and purger is not threading.current_thread():
                purger.join(timeout=2)
            if tracking_service is not None:
                tracking_service.reset_all()
            if temporal_service is not None:
                temporal_service.reset_all()
            if detector_service is not None:
                detector_service.close()
            if frame_manager is not None:
                frame_manager.close_all()
            if input_preparer is not None:
                input_preparer.close_all()
        finally:
            super().server_close()


def create_server(
    project_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    run_runner: Any = None,
    max_upload_bytes: int | None = None,
    runs_root: Path | None = None,
    auth_db_path: Path | None = None,
    auth_username: str | None = None,
    auth_password: str | None = None,
    capture_factory: Any = None,
    detector_service: Any = None,
    tracking_service: TrackingService | None = None,
    temporal_service: TemporalAnalysisService | None = None,
    project_data: ProjectData | None = None,
    enable_registry_purger: bool = False,
) -> APIServer:
    settings = get_settings(project_root)
    data = project_data or ProjectData(settings)
    auth = AuthManager(
        auth_db_path or settings.auth_database_path,
        bootstrap_username=auth_username,
        bootstrap_password=auth_password,
    )
    runs = RunManager(
        settings.project_root,
        max_upload_bytes if max_upload_bytes is not None else settings.max_upload_bytes,
        run_runner or run_video_pipeline,
        runs_root=runs_root,
    )
    input_preparer = InputPreparer(runs, capture_factory=capture_factory)
    frame_manager = Phase3FrameManager(runs, input_preparer, capture_factory=capture_factory)
    if detector_service is None:
        from backend.detection import DetectionService

        detector_service = DetectionService(settings.project_root)
    tracking_service = tracking_service or TrackingService()
    temporal_service = temporal_service or TemporalAnalysisService()
    server = APIServer(
        (host, port),
        create_handler(
            data,
            runs,
            auth,
            input_preparer,
            frame_manager,
            detector_service,
            tracking_service,
            temporal_service,
            secure_cookies_enabled(),
        ),
    )
    stop_purger = None
    purger = None
    if enable_registry_purger:
        # Production launcher opts in. Test-created servers never run document cleanup
        # against a developer project root as a side effect of fixture setup.
        stop_purger = threading.Event()
        def _purge_loop():
            while not stop_purger.is_set():
                try:
                    sector_registry.purge_documents(settings.project_root)
                except Exception:
                    LOGGER.exception("Registry document retention purge failed")
                stop_purger.wait(3600)
        purger = threading.Thread(target=_purge_loop, name="registry-document-purger", daemon=True)
        purger.start()
    server.registry_purger_stop = stop_purger
    server.registry_purger_thread = purger
    server.run_manager = runs
    server.auth_manager = auth
    server.input_preparer = input_preparer
    server.frame_manager = frame_manager
    server.detector_service = detector_service
    server.tracking_service = tracking_service
    server.temporal_service = temporal_service
    return server
