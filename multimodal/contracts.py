"""Simple dataclass contracts for optional multimodal evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


VALID_SYNCHRONIZATION_STATUS = {
    "rgb_only",
    "rgb_thermal",
    "rgb_thermal_radar",
    "rgb_radar",
    "unsynchronized",
}
VALID_MODALITIES = {"rgb", "thermal", "radar", "motion", "temporal"}


@dataclass(frozen=True)
class FrameReference:
    """Reference to a source-specific frame or timestamped observation."""

    source_video: str
    frame_index: int
    timestamp_sec: Optional[float] = None

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be >= 0")
        if self.timestamp_sec is not None and self.timestamp_sec < 0:
            raise ValueError("timestamp_sec must be >= 0 when provided")


@dataclass(frozen=True)
class SynchronizedFrame:
    """Explicitly synchronized RGB/thermal/radar frame association."""

    rgb: FrameReference
    thermal: Optional[FrameReference] = None
    radar_timestamp_sec: Optional[float] = None
    synchronization_status: str = "rgb_only"
    thermal_available: bool = False
    radar_available: bool = False

    def __post_init__(self) -> None:
        if self.synchronization_status not in VALID_SYNCHRONIZATION_STATUS:
            raise ValueError(
                f"synchronization_status must be one of: {sorted(VALID_SYNCHRONIZATION_STATUS)}"
            )
        if self.radar_timestamp_sec is not None and self.radar_timestamp_sec < 0:
            raise ValueError("radar_timestamp_sec must be >= 0 when provided")
        if self.thermal is not None and not self.thermal_available:
            object.__setattr__(self, "thermal_available", True)
        if self.radar_timestamp_sec is not None and not self.radar_available:
            object.__setattr__(self, "radar_available", True)


@dataclass(frozen=True)
class TrackIdentity:
    """Source-scoped track identity; preserves the existing track_id column."""

    source_video: str
    track_id: int

    @property
    def composite_key(self) -> tuple[str, int]:
        return (self.source_video, self.track_id)

    def as_tuple(self) -> tuple[str, int]:
        return self.composite_key


@dataclass(frozen=True)
class EvidenceRecord:
    """A serializable evidence row for optional modalities."""

    source_video: str
    track_id: int
    modality: str
    frame_index: Optional[int] = None
    timestamp_sec: Optional[float] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    available: bool = True

    def __post_init__(self) -> None:
        if self.modality not in VALID_MODALITIES:
            raise ValueError(f"modality must be one of: {sorted(VALID_MODALITIES)}")
        if self.frame_index is not None and self.frame_index < 0:
            raise ValueError("frame_index must be >= 0 when provided")
        if self.timestamp_sec is not None and self.timestamp_sec < 0:
            raise ValueError("timestamp_sec must be >= 0 when provided")
