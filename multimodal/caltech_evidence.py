"""Build source-scoped Caltech RGB/Thermal evidence for one RGB track observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .contracts import EvidenceRecord, FrameReference
from .sync import DEFAULT_MAX_TIME_DELTA_SEC, synchronize_frames


@dataclass(frozen=True)
class CaltechRecordingSources:
    """Explicitly identify RGB and optional Thermal sources from one recording."""

    recording_id: str
    rgb_source: str
    thermal_source: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.recording_id.strip():
            raise ValueError("recording_id must be non-empty")
        if not self.rgb_source.strip():
            raise ValueError("rgb_source must be non-empty")
        if self.thermal_source is not None and not self.thermal_source.strip():
            raise ValueError("thermal_source must be non-empty when provided")


@dataclass(frozen=True)
class CaltechTrackObservation:
    """A track observation explicitly tied to its source recording and RGB frame."""

    recording_id: str
    source_video: str
    frame_index: int
    track_id: Optional[int]


def build_caltech_track_evidence(
    rgb_frame: Optional[FrameReference],
    track_observation: Optional[CaltechTrackObservation],
    recording_sources: Optional[CaltechRecordingSources],
    thermal_frame: Optional[FrameReference] = None,
    max_time_delta_sec: float = DEFAULT_MAX_TIME_DELTA_SEC,
) -> list[EvidenceRecord]:
    """Return RGB evidence and, only when timestamp-matched, linked Thermal evidence.

    The recording source declaration is the caller's explicit provenance contract;
    this function does not infer that separate files belong to one recording.
    """
    if rgb_frame is None:
        raise ValueError("rgb_frame is required")
    if track_observation is None or track_observation.track_id is None:
        raise ValueError("a track observation with a track_id is required")
    if recording_sources is None:
        raise ValueError("recording_sources is required")

    if rgb_frame.source_video != recording_sources.rgb_source:
        raise ValueError("RGB frame source does not match the declared recording source")
    if track_observation.source_video != recording_sources.rgb_source:
        raise ValueError("track source does not match the declared RGB recording source")
    if track_observation.recording_id != recording_sources.recording_id:
        raise ValueError("track observation belongs to a different recording")
    if track_observation.frame_index != rgb_frame.frame_index:
        raise ValueError("track observation does not reference the supplied RGB frame")

    if thermal_frame is None:
        return [
            EvidenceRecord(
                source_video=rgb_frame.source_video,
                track_id=track_observation.track_id,
                modality="rgb",
                frame_index=rgb_frame.frame_index,
                timestamp_sec=rgb_frame.timestamp_sec,
                evidence={
                    "recording_id": recording_sources.recording_id,
                    "synchronization_status": "rgb_only",
                    "thermal_available": False,
                },
                available=True,
            )
        ]

    if recording_sources.thermal_source is None:
        raise ValueError("Thermal frame supplied without a declared Thermal source")
    if thermal_frame.source_video != recording_sources.thermal_source:
        raise ValueError("Thermal frame source does not match the declared recording source")

    synchronized = synchronize_frames(
        [rgb_frame],
        thermal_frames=[thermal_frame],
        max_time_delta_sec=max_time_delta_sec,
        index_mapping_verified=False,
    )[0]
    if synchronized.synchronization_status != "rgb_thermal" or synchronized.thermal is None:
        raise ValueError("Thermal frame is not timestamp-synchronized with the RGB frame")

    if rgb_frame.timestamp_sec is None or synchronized.thermal.timestamp_sec is None:
        raise ValueError("RGB and Thermal timestamps are required for linked evidence")
    synchronization_delta_sec = abs(
        rgb_frame.timestamp_sec - synchronized.thermal.timestamp_sec
    )

    linked_evidence = {
        "recording_id": recording_sources.recording_id,
        "rgb_source_video": rgb_frame.source_video,
        "rgb_frame_index": rgb_frame.frame_index,
        "rgb_timestamp_sec": rgb_frame.timestamp_sec,
        "thermal_source_video": synchronized.thermal.source_video,
        "thermal_frame_index": synchronized.thermal.frame_index,
        "thermal_timestamp_sec": synchronized.thermal.timestamp_sec,
        "synchronization_delta_sec": synchronization_delta_sec,
        "synchronization_status": synchronized.synchronization_status,
        "rgb_available": True,
        "thermal_available": True,
    }
    return [
        EvidenceRecord(
            source_video=rgb_frame.source_video,
            track_id=track_observation.track_id,
            modality=modality,
            frame_index=rgb_frame.frame_index,
            timestamp_sec=rgb_frame.timestamp_sec,
            evidence=dict(linked_evidence),
            available=True,
        )
        for modality in ("rgb", "thermal")
    ]


__all__ = [
    "CaltechRecordingSources",
    "CaltechTrackObservation",
    "build_caltech_track_evidence",
]
