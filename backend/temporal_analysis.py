"""Phase 6 temporal analysis over Phase 5 tracked observations."""

from __future__ import annotations

import math
import threading
from typing import Any

from backend.runs import RunError
from reliability.reliability_score import estimate_reliability
from temporal.motion_trajectory import compute_track_trajectory
from temporal.temporal_features import compute_temporal_features
from temporal.tracklet_memory import TrackletMemory


def _finite_number(value: Any, name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(422, "invalid_track_result", f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise RunError(422, "invalid_track_result", f"{name} must be finite.")
    return number


class TemporalAnalysisService:
    """Maintain source-scoped Phase 6 history using the existing temporal algorithms."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._active_source: dict[str, tuple[str, str, str]] = {}

    @staticmethod
    def _source_key(
        owner: str,
        result: dict[str, Any],
    ) -> tuple[str, str, str]:
        if not owner:
            raise RunError(401, "authentication_required", "Authentication is required.")
        source_type = result.get("source_type")
        if source_type == "upload" and result.get("input_type") == "rgb":
            source_id = result.get("run_id")
        elif source_type == "cctv" and result.get("input_type") in (None, "rgb"):
            source_id = result.get("live_reference_id")
        else:
            raise RunError(
                415,
                "temporal_source_not_supported",
                "Phase 6 currently supports RGB uploads and visible-light CCTV only.",
            )
        if not isinstance(source_id, str) or not source_id:
            raise RunError(422, "invalid_track_result", "A valid input source reference is required.")
        return owner, source_type, source_id

    @staticmethod
    def _validate_tracks(result: dict[str, Any]) -> list[dict[str, Any]]:
        tracks = result.get("tracks")
        if not isinstance(tracks, list) or len(tracks) > 1000:
            raise RunError(422, "invalid_track_result", "tracks must be a valid observation list.")
        validated: list[dict[str, Any]] = []
        for track in tracks:
            if not isinstance(track, dict):
                raise RunError(422, "invalid_track_result", "Each track observation must be an object.")
            track_id = track.get("track_id")
            class_id = track.get("class_id")
            class_name = track.get("class_name")
            if (
                isinstance(track_id, bool)
                or not isinstance(track_id, int)
                or track_id < 0
                or isinstance(class_id, bool)
                or not isinstance(class_id, int)
                or class_id < 0
                or not isinstance(class_name, str)
                or not class_name
                or len(class_name) > 256
            ):
                raise RunError(422, "invalid_track_result", "Track identity fields are invalid.")
            confidence = _finite_number(track.get("confidence"), "confidence")
            timestamp = _finite_number(track.get("timestamp_sec"), "timestamp_sec", optional=True)
            if confidence < 0 or confidence > 1 or (timestamp is not None and timestamp < 0):
                raise RunError(422, "invalid_track_result", "Track confidence or timestamp is out of range.")
            item = {
                "track_id": track_id,
                "class_id": class_id,
                "class_name": class_name,
                "confidence": confidence,
                "timestamp_sec": timestamp,
            }
            for field in ("x1", "y1", "x2", "y2"):
                item[field] = _finite_number(track.get(field), field)
            if item["x2"] < item["x1"] or item["y2"] < item["y1"]:
                raise RunError(422, "invalid_track_result", "Track bounding boxes are invalid.")
            validated.append(item)
        return validated

    def update(
        self,
        owner: str,
        result: dict[str, Any],
        *,
        fps: float | None = None,
        end_of_video: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise RunError(422, "invalid_track_result", "A Phase 5 TrackResult is required.")
        source_key = self._source_key(owner, result)
        source_type = source_key[1]
        validated_tracks = [] if end_of_video else self._validate_tracks(result)
        frame_index = result.get("frame_index")
        sequence_index = result.get("sequence_index")
        if end_of_video:
            if source_type == "upload" and result.get("input_type") != "rgb":
                raise RunError(415, "temporal_source_not_supported", "Only RGB uploads are supported.")
        elif source_type == "upload":
            if isinstance(frame_index, bool) or not isinstance(frame_index, int) or frame_index < 0:
                raise RunError(422, "invalid_track_result", "RGB observations require a valid frame_index.")
        elif isinstance(sequence_index, bool) or not isinstance(sequence_index, int) or sequence_index < 0:
            raise RunError(422, "invalid_track_result", "CCTV observations require a valid sequence_index.")

        safe_fps = _finite_number(fps, "fps", optional=True)
        if safe_fps is not None and safe_fps <= 0:
            safe_fps = None
        with self._lock:
            previous_key = self._active_source.get(owner)
            if previous_key is not None and previous_key != source_key:
                self._sources.pop(previous_key, None)
            state = self._sources.get(source_key)
            if end_of_video:
                if state is None:
                    return self._empty_result(result, "COMPLETED")
                response = self._result_payload(result, state, state["fps"], "COMPLETED")
                self._sources.pop(source_key, None)
                if self._active_source.get(owner) == source_key:
                    self._active_source.pop(owner, None)
                return response

            source_frame = frame_index if source_type == "upload" else sequence_index
            if state is None:
                state = {
                    "memory": TrackletMemory(),
                    "last_source_frame": None,
                    "frames_processed": 0,
                    "fps": safe_fps,
                }
                self._sources[source_key] = state
            last_frame = state["last_source_frame"]
            if last_frame is not None and source_frame <= last_frame:
                raise RunError(
                    409,
                    "out_of_order_track_result",
                    "Phase 6 requires a new TrackResult for each source frame.",
                )
            state["last_source_frame"] = source_frame
            state["frames_processed"] += 1
            state["fps"] = safe_fps or state["fps"]
            self._active_source[owner] = source_key
            for track in validated_tracks:
                timestamp = track["timestamp_sec"] if source_type == "upload" else None
                if timestamp is None and source_type == "upload" and state["fps"]:
                    timestamp = frame_index / state["fps"]
                state["memory"].add_observation(
                    {
                        **track,
                        "frame": source_frame,
                        "source": source_key[1] + ":" + source_key[2],
                        "timestamp_sec": timestamp,
                    }
                )
            return self._result_payload(result, state, state["fps"], "ACCUMULATING")

    @staticmethod
    def _empty_result(result: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "source_type": result.get("source_type"),
            "input_type": result.get("input_type"),
            "run_id": result.get("run_id"),
            "live_reference_id": result.get("live_reference_id"),
            "status": status,
            "frames_processed": 0,
            "timing_basis": "unavailable",
            "physical_world_calibration": "not_available",
            "tracks": [],
        }

    @classmethod
    def _result_payload(
        cls,
        result: dict[str, Any],
        state: dict[str, Any],
        fps: float | None,
        status: str,
    ) -> dict[str, Any]:
        source_type = result["source_type"]
        temporal_tracks = []
        for track in state["memory"].get_all_tracks():
            temporal, _ = compute_temporal_features(track)
            observations = []
            for position, frame in enumerate(track["frames"]):
                timestamp = track["timestamps_sec"][position]
                timestamp_source = "track_result_timestamp" if timestamp is not None else "unavailable"
                if timestamp is None and source_type == "upload" and fps:
                    timestamp = frame / fps
                    timestamp_source = "video_fps"
                x1, y1, x2, y2 = track["bounding_boxes"][position]
                observations.append(
                    {
                        "frame": frame,
                        "track_id": track["track_id"],
                        "class_id": track["class_ids"][position],
                        "class_name": track["class_names"][position],
                        "class": track["class_names"][position],
                        "confidence": track["confidences"][position],
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "timestamp_sec": timestamp,
                        "timestamp_source": timestamp_source,
                        "frame_width": None,
                        "frame_height": None,
                    }
                )
            _, motion_summary, trajectory = compute_track_trajectory(
                observations,
                fps=fps if source_type == "upload" else None,
            )
            classes = list(dict.fromkeys(track["class_names"]))
            reliability = estimate_reliability(temporal)
            temporal_tracks.append(
                {
                    "track_id": track["track_id"],
                    "class_history": classes,
                    "class_consistent": len(classes) == 1,
                    "temporal_summary": temporal,
                    "motion_summary": motion_summary,
                    "trajectory": trajectory,
                    "reliability": reliability,
                }
            )
        return {
            "source_type": source_type,
            "input_type": result.get("input_type"),
            "run_id": result.get("run_id"),
            "live_reference_id": result.get("live_reference_id"),
            "status": status,
            "current_frame_index": result.get("frame_index"),
            "current_sequence_index": result.get("sequence_index"),
            "frame_basis": (
                "source_frame_index"
                if source_type == "upload"
                else "phase5_observation_sequence"
            ),
            "frames_processed": state["frames_processed"],
            "timing_basis": "video_fps" if source_type == "upload" and fps else "unavailable",
            "coordinate_system": "image_plane_pixels",
            "physical_world_calibration": "not_available",
            "tracks": temporal_tracks,
        }

    def reset_owner(self, owner: str | None) -> None:
        if not owner:
            return
        with self._lock:
            self._active_source.pop(owner, None)
            for source_key in [key for key in self._sources if key[0] == owner]:
                self._sources.pop(source_key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._sources.clear()
            self._active_source.clear()
