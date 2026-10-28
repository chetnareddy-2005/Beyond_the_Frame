"""Deterministic, source-scoped track-association helpers for multimodal evidence."""

from __future__ import annotations

from typing import Optional, Sequence

from .contracts import EvidenceRecord


def track_key(source_video: str, track_id: int) -> tuple[str, int]:
    return (source_video, int(track_id))


def associate_track(
    source_video: str,
    track_id: int,
    modalities: Sequence[EvidenceRecord],
    *,
    frame_index: Optional[int] = None,
    timestamp_sec: Optional[float] = None,
) -> list[EvidenceRecord]:
    """Return matching multimodal evidence for the same source-scoped track.

    Association is intentionally simple and deterministic: same source_video and same
    track_id are required. Frame/timestamp matches are accepted when explicit.
    """
    results: list[EvidenceRecord] = []
    for record in modalities:
        if record.source_video != source_video:
            continue
        if record.track_id != track_id:
            continue
        if not record.available:
            continue
        if frame_index is not None and record.frame_index is not None:
            if record.frame_index != frame_index:
                continue
        if timestamp_sec is not None and record.timestamp_sec is not None:
            if abs(record.timestamp_sec - timestamp_sec) > 0.1:
                continue
        results.append(record)
    return results


def associate_multimodal_tracks(
    rgb_track_id: int,
    source_video: str,
    rgb_evidence: Sequence[EvidenceRecord],
    thermal_evidence: Optional[Sequence[EvidenceRecord]] = None,
    radar_evidence: Optional[Sequence[EvidenceRecord]] = None,
) -> dict[str, list[EvidenceRecord]]:
    """Minimal, transparent association contract for future multimodal evidence."""
    thermal_evidence = [] if thermal_evidence is None else list(thermal_evidence)
    radar_evidence = [] if radar_evidence is None else list(radar_evidence)
    return {
        "rgb": [
            record
            for record in rgb_evidence
            if record.source_video == source_video and record.track_id == rgb_track_id
        ],
        "thermal": [
            record
            for record in thermal_evidence
            if record.source_video == source_video and record.track_id == rgb_track_id
        ],
        "radar": [
            record
            for record in radar_evidence
            if record.source_video == source_video and record.track_id == rgb_track_id
        ],
    }


__all__ = ["associate_multimodal_tracks", "associate_track", "track_key"]
