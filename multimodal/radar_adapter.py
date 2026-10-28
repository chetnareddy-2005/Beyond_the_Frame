"""Interface-only radar adapter for future real radar evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .contracts import EvidenceRecord


@dataclass(frozen=True)
class RadarObservation:
    timestamp_sec: Optional[float]
    source_video: str
    target_id: Optional[str] = None
    range: Optional[float] = None
    azimuth: Optional[float] = None
    relative_velocity: Optional[float] = None
    sensor_class: Optional[str] = None
    sensor_confidence: Optional[float] = None
    frame_index: Optional[int] = None
    sample_path: Optional[str] = None
    distance_condition_m: Optional[float] = None

    def __post_init__(self) -> None:
        if self.timestamp_sec is not None and self.timestamp_sec < 0:
            raise ValueError("timestamp_sec must be >= 0")
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be >= 0")
        if self.distance_condition_m is not None and self.distance_condition_m < 0:
            raise ValueError("distance_condition_m must be >= 0")


class RadarAdapter:
    """Optional interface for real radar observations.

    Radar remains an evidence branch and is inactive when no genuine radar data exists.
    """

    def __init__(self, enabled: bool = False):
        self._enabled = bool(enabled)
        self._observations = []

    @property
    def available(self) -> bool:
        return self._enabled and bool(self._observations)

    def ingest(self, observation: RadarObservation) -> None:
        if not self._enabled:
            return
        self._observations.append(observation)

    def get_evidence(
        self,
        source_video: str,
        track_id: int,
        timestamp_sec: Optional[float] = None,
        frame_index: Optional[int] = None,
    ) -> list[EvidenceRecord]:
        if not self.available:
            return []
        if timestamp_sec is None and frame_index is None:
            return []
        rows: list[EvidenceRecord] = []
        for observation in self._observations:
            if observation.source_video != source_video:
                continue
            if (
                timestamp_sec is not None
                and (
                    observation.timestamp_sec is None
                    or abs(observation.timestamp_sec - timestamp_sec) > 0.1
                )
            ):
                continue
            if (
                frame_index is not None
                and observation.frame_index is not None
                and observation.frame_index != frame_index
            ):
                continue
            evidence = {
                "target_id": observation.target_id,
                "range": observation.range,
                "azimuth": observation.azimuth,
                "relative_velocity": observation.relative_velocity,
                "sensor_class": observation.sensor_class,
                "sensor_confidence": observation.sensor_confidence,
            }
            if observation.frame_index is not None:
                evidence["frame_index"] = observation.frame_index
            if observation.sample_path is not None:
                evidence["sample_path"] = observation.sample_path
            if observation.distance_condition_m is not None:
                evidence["distance_condition_m"] = observation.distance_condition_m
            rows.append(
                EvidenceRecord(
                    source_video=source_video,
                    track_id=track_id,
                    modality="radar",
                    frame_index=frame_index if frame_index is not None else observation.frame_index,
                    timestamp_sec=timestamp_sec if timestamp_sec is not None else observation.timestamp_sec,
                    evidence=evidence,
                    available=True,
                )
            )
        return rows


__all__ = ["RadarAdapter", "RadarObservation"]
