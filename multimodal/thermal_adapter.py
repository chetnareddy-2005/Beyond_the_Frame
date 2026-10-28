"""Interface-only thermal adapter for future real thermal evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import cv2
import numpy as np

from .contracts import EvidenceRecord, FrameReference


@dataclass(frozen=True)
class ThermalObservation:
    source_video: str
    track_id: int
    frame_index: int
    timestamp_sec: Optional[float] = None
    evidence: Dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.evidence is None:
            object.__setattr__(self, "evidence", {})


class ThermalAdapter:
    """Optional interface for standalone thermal-frame preprocessing.

    Preprocessed intensity values are not calibrated physical temperatures, and
    availability does not imply synchronization with RGB.
    """

    def __init__(
        self,
        enabled: bool = True,
        thermal_source: Optional[str] = None,
    ):
        self._enabled = bool(enabled)
        self._thermal_source = thermal_source

    @property
    def available(self) -> bool:
        return self._enabled and self._thermal_source is not None

    def read_frame(self, source_video: str, frame_index: int) -> Optional[Any]:
        """Read one zero-based frame from a thermal video."""
        if (
            not isinstance(source_video, str)
            or not source_video
            or isinstance(frame_index, bool)
            or not isinstance(frame_index, (int, np.integer))
            or frame_index < 0
        ):
            return None

        capture = cv2.VideoCapture(source_video)
        try:
            if not capture.isOpened():
                return None
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index)):
                return None
            success, frame = capture.read()
            if not success or frame is None or frame.size == 0:
                return None
            return frame
        finally:
            capture.release()

    @staticmethod
    def preprocess_frame(
        thermal_frame: Any,
        target_size: Optional[tuple[int, int]] = None,
    ) -> Optional[Any]:
        """Convert thermal image intensity to normalized grayscale uint8.

        This is an image-intensity transform only; it does not estimate temperature.
        ``target_size`` follows OpenCV's ``(width, height)`` convention.
        """
        if (
            not isinstance(thermal_frame, np.ndarray)
            or thermal_frame.size == 0
            or thermal_frame.ndim not in (2, 3)
        ):
            return None
        if thermal_frame.ndim == 3 and thermal_frame.shape[2] != 3:
            return None
        if not np.issubdtype(thermal_frame.dtype, np.number):
            return None

        frame = thermal_frame.astype(np.float32, copy=False)
        if not np.isfinite(frame).all():
            return None
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        minimum = float(frame.min())
        maximum = float(frame.max())
        if maximum > minimum:
            normalized = (frame - minimum) * (255.0 / (maximum - minimum))
        else:
            normalized = np.zeros(frame.shape, dtype=np.float32)
        processed = np.clip(normalized, 0, 255).astype(np.uint8)

        if target_size is not None:
            if (
                len(target_size) != 2
                or any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in target_size)
                or any(value <= 0 for value in target_size)
            ):
                return None
            processed = cv2.resize(
                processed,
                (int(target_size[0]), int(target_size[1])),
                interpolation=cv2.INTER_AREA,
            )
        return processed

    def process_video_frame(
        self,
        source_video: str,
        frame_index: int,
        target_size: Optional[tuple[int, int]] = None,
    ) -> Optional[Any]:
        frame = self.read_frame(source_video, frame_index)
        if frame is None:
            return None
        return self.preprocess_frame(frame, target_size=target_size)

    @staticmethod
    def build_frame_statistics(processed_frame: Any) -> Dict[str, Any]:
        """Return JSON-serializable descriptive statistics for an image array."""
        if (
            not isinstance(processed_frame, np.ndarray)
            or processed_frame.size == 0
        ):
            raise ValueError("processed_frame must be a non-empty numpy array")
        if not np.issubdtype(processed_frame.dtype, np.number):
            raise ValueError("processed_frame must have a numeric dtype")
        if not np.isfinite(processed_frame).all():
            raise ValueError("processed_frame must contain finite values")

        return {
            "shape": [int(size) for size in processed_frame.shape],
            "dtype": str(processed_frame.dtype),
            "min": float(processed_frame.min()),
            "max": float(processed_frame.max()),
            "mean": float(processed_frame.mean()),
            "std": float(processed_frame.std()),
        }

    def get_frame_reference(
        self,
        source_video: str,
        frame_index: int,
        timestamp_sec: Optional[float] = None,
    ) -> Optional[FrameReference]:
        if not self.available or not source_video:
            return None
        return FrameReference(
            source_video=source_video,
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
        )

    def extract_evidence(
        self,
        source_video: str,
        track_id: int,
        frame_index: Optional[int] = None,
        timestamp_sec: Optional[float] = None,
        thermal_frame: Optional[Any] = None,
        rgb_reference: Optional[FrameReference] = None,
        **kwargs: Any,
    ) -> Optional[EvidenceRecord]:
        if not self.available or thermal_frame is None:
            return None
        evidence = dict(kwargs)
        evidence.setdefault("thermal_source", self._thermal_source)
        if rgb_reference is not None:
            evidence.setdefault("rgb_source", rgb_reference.source_video)
        return EvidenceRecord(
            source_video=source_video,
            track_id=track_id,
            modality="thermal",
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
            evidence=evidence,
            available=True,
        )


__all__ = ["ThermalAdapter", "ThermalObservation"]
