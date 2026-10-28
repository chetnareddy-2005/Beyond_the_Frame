"""Conservative synchronization helpers for optional RGB/thermal/radar evidence.

Timestamp matching is used only when both inputs carry timestamps. Frame-index matching
requires an explicit verified mapping; equal indices alone are not evidence of sync.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .contracts import FrameReference, SynchronizedFrame

DEFAULT_MAX_TIME_DELTA_SEC = 0.1


def _match_by_index(
    rgb_frame: FrameReference,
    thermal_frames: Sequence[FrameReference],
    max_time_delta_sec: float,
) -> Optional[FrameReference]:
    for thermal in thermal_frames:
        if thermal.frame_index != rgb_frame.frame_index:
            continue
        if (
            rgb_frame.timestamp_sec is not None
            and thermal.timestamp_sec is not None
            and abs(thermal.timestamp_sec - rgb_frame.timestamp_sec) > max_time_delta_sec
        ):
            continue
        return thermal
    return None


def _match_by_timestamp(
    rgb_frame: FrameReference,
    thermal_frames: Sequence[FrameReference],
    max_time_delta_sec: float,
) -> Optional[FrameReference]:
    if rgb_frame.timestamp_sec is None:
        return None
    best_match = None
    best_delta = None
    for thermal in thermal_frames:
        if thermal.timestamp_sec is None:
            continue
        delta = abs(thermal.timestamp_sec - rgb_frame.timestamp_sec)
        if delta <= max_time_delta_sec and (best_delta is None or delta < best_delta):
            best_delta = delta
            best_match = thermal
    return best_match


def _match_radar_timestamp(
    rgb_frame: FrameReference,
    radar_timestamps: Sequence[float],
    max_time_delta_sec: float,
) -> Optional[float]:
    if rgb_frame.timestamp_sec is None:
        return None
    best_match = None
    best_delta = None
    for radar_time in radar_timestamps:
        delta = abs(radar_time - rgb_frame.timestamp_sec)
        if delta <= max_time_delta_sec and (best_delta is None or delta < best_delta):
            best_delta = delta
            best_match = radar_time
    return best_match


def synchronize_frames(
    rgb_frames: Sequence[FrameReference],
    thermal_frames: Optional[Sequence[FrameReference]] = None,
    radar_timestamps: Optional[Sequence[float]] = None,
    max_time_delta_sec: float = DEFAULT_MAX_TIME_DELTA_SEC,
    *,
    index_mapping_verified: bool = False,
) -> List[SynchronizedFrame]:
    """Synchronize RGB frames with optional thermal and radar streams.

    RGB is the reference timeline. Thermal is matched by timestamp proximity unless
    frame-index mapping has explicitly been verified. Radar is matched by timestamp
    proximity. Supplied but unmatched thermal input is marked unsynchronized.
    """
    thermal_frames = [] if thermal_frames is None else list(thermal_frames)
    radar_timestamps = [] if radar_timestamps is None else list(radar_timestamps)
    if max_time_delta_sec < 0:
        raise ValueError("max_time_delta_sec must be non-negative")
    results: List[SynchronizedFrame] = []

    for rgb in rgb_frames:
        thermal = None
        if index_mapping_verified:
            thermal = _match_by_index(rgb, thermal_frames, max_time_delta_sec)
        if thermal is None:
            thermal = _match_by_timestamp(rgb, thermal_frames, max_time_delta_sec)

        radar_match = _match_radar_timestamp(rgb, radar_timestamps, max_time_delta_sec)

        thermal_unmatched = bool(thermal_frames) and thermal is None
        radar_unmatched = bool(radar_timestamps) and radar_match is None
        if thermal_unmatched or radar_unmatched:
            status = "unsynchronized"
        elif thermal is not None and radar_match is not None:
            status = "rgb_thermal_radar"
        elif thermal is not None:
            status = "rgb_thermal"
        elif radar_match is not None:
            status = "rgb_radar"
        else:
            status = "rgb_only"

        results.append(
            SynchronizedFrame(
                rgb=rgb,
                thermal=thermal,
                radar_timestamp_sec=radar_match,
                synchronization_status=status,
                thermal_available=bool(thermal_frames),
                radar_available=bool(radar_timestamps),
            )
        )
    return results


__all__ = ["DEFAULT_MAX_TIME_DELTA_SEC", "synchronize_frames"]
