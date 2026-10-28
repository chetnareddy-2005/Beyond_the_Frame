"""Phase 5 ByteTrack adapter for Phase 4 single-frame detections."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np

from backend.detection import DetectionResult
from backend.runs import RunError


@dataclass(frozen=True)
class TrackedObservation:
    track_id: int
    frame_index: int | None
    timestamp_sec: float | None
    source_type: str
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "source_type": self.source_type,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
        }


@dataclass(frozen=True)
class TrackResult:
    source_type: str
    input_type: str | None
    run_id: str | None
    live_reference_id: str | None
    frame_index: int | None
    sequence_index: int
    timestamp_sec: float | None
    active_track_count: int
    tracks: tuple[TrackedObservation, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "input_type": self.input_type,
            "run_id": self.run_id,
            "live_reference_id": self.live_reference_id,
            "frame_index": self.frame_index,
            "sequence_index": self.sequence_index,
            "timestamp_sec": self.timestamp_sec,
            "active_track_count": self.active_track_count,
            "tracks": [track.as_dict() for track in self.tracks],
        }


class TrackingService:
    """Keep one supervision ByteTrack instance per authenticated input source."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trackers: dict[tuple[str, str, str, str], Any] = {}
        self._active_source: dict[str, tuple[str, str, str, str]] = {}
        self._source_sequences: dict[tuple[str, str, str, str], int] = {}

    @contextmanager
    def frame_sequence(self) -> Iterator[None]:
        """Serialize frame acquisition, detection, and tracker updates."""
        with self._lock:
            yield

    @staticmethod
    def _source_key(owner: str, result: DetectionResult) -> tuple[str, str, str, str]:
        if not owner:
            raise RunError(401, "authentication_required", "Authentication is required.")
        if (
            result.source_type == "upload"
            and result.input_type in {"rgb", "thermal"}
            and result.run_id
        ):
            source_id = result.run_id
            input_type = result.input_type
        elif (
            result.source_type == "cctv"
            and result.input_type in (None, "rgb")
            and result.live_reference_id
        ):
            source_id = result.live_reference_id
            input_type = "rgb"
        else:
            raise RunError(
                415,
                "tracking_source_not_supported",
                "Phase 5 tracking supports RGB or Thermal uploads and visible-light CCTV only.",
            )
        return owner, result.source_type, input_type, source_id

    @staticmethod
    def _new_tracker() -> tuple[Any, Any]:
        try:
            import supervision as sv
        except ImportError as error:
            raise RunError(
                503,
                "tracking_unavailable",
                "The configured ByteTrack dependency is unavailable.",
            ) from error
        return sv.ByteTrack(), sv.Detections

    def track(self, owner: str, result: DetectionResult) -> TrackResult:
        if not isinstance(result, DetectionResult):
            raise RunError(422, "invalid_detection_result", "A Phase 4 DetectionResult is required.")
        source_key = self._source_key(owner, result)
        with self._lock:
            previous_key = self._active_source.get(owner)
            if previous_key is not None and previous_key != source_key:
                self._trackers.pop(previous_key, None)
                self._source_sequences.pop(previous_key, None)
            tracker = self._trackers.get(source_key)
            detections_factory = None
            if tracker is None:
                tracker, detections_factory = self._new_tracker()
                self._trackers[source_key] = tracker
            if detections_factory is None:
                try:
                    import supervision as sv
                except ImportError as error:
                    raise RunError(
                        503,
                        "tracking_unavailable",
                        "The configured ByteTrack dependency is unavailable.",
                    ) from error
                detections_factory = sv.Detections

            xyxy = np.asarray(
                [[d.x1, d.y1, d.x2, d.y2] for d in result.detections],
                dtype=np.float32,
            ).reshape((-1, 4))
            confidence = np.asarray([d.confidence for d in result.detections], dtype=np.float32)
            class_id = np.asarray([d.class_id for d in result.detections], dtype=np.int32)
            frame_detections = detections_factory(
                xyxy=xyxy,
                confidence=confidence,
                class_id=class_id,
            )
            self._active_source[owner] = source_key
            sequence_index = self._source_sequences.get(source_key, -1) + 1
            self._source_sequences[source_key] = sequence_index
            tracked = tracker.update_with_detections(frame_detections)
            tracker_ids = tracked.tracker_id
            tracks: list[TrackedObservation] = []
            if tracker_ids is not None:
                class_names = {d.class_id: d.class_name for d in result.detections}
                for box, score, category, track_id in zip(
                    tracked.xyxy,
                    tracked.confidence,
                    tracked.class_id,
                    tracker_ids,
                ):
                    if track_id is None:
                        continue
                    x1, y1, x2, y2 = (float(value) for value in box)
                    category_id = int(category)
                    tracks.append(
                        TrackedObservation(
                            track_id=int(track_id),
                            frame_index=result.frame_index,
                            timestamp_sec=result.timestamp_sec,
                            source_type=result.source_type,
                            class_id=category_id,
                            class_name=class_names.get(category_id, str(category_id)),
                            confidence=float(score),
                            x1=x1,
                            y1=y1,
                            x2=x2,
                            y2=y2,
                        )
                    )
            return TrackResult(
                source_type=result.source_type,
                input_type=result.input_type,
                run_id=result.run_id,
                live_reference_id=result.live_reference_id,
                frame_index=result.frame_index,
                sequence_index=sequence_index,
                timestamp_sec=result.timestamp_sec,
                active_track_count=len(tracks),
                tracks=tuple(tracks),
            )

    def source_is_active(self, owner: str, result: DetectionResult) -> bool:
        source_key = self._source_key(owner, result)
        with self._lock:
            return self._active_source.get(owner) == source_key

    def reset_owner(self, owner: str | None) -> None:
        if not owner:
            return
        with self._lock:
            self._active_source.pop(owner, None)
            source_keys = [key for key in self._trackers if key[0] == owner]
            for source_key in source_keys:
                self._trackers.pop(source_key, None)
                self._source_sequences.pop(source_key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._trackers.clear()
            self._active_source.clear()
            self._source_sequences.clear()
