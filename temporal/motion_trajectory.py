"""Per-track image-plane motion and trajectory metrics from detector tracks."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any

REQUIRED_TRACK_FIELDS = (
    "frame",
    "track_id",
    "class_name",
    "confidence",
    "x1",
    "y1",
    "x2",
    "y2",
)
TIMESTAMP_FIELDS = ("timestamp_sec", "timestamp")
MOTION_OBSERVATION_FIELDS = (
    "track_id",
    "frame",
    "timestamp_sec",
    "timestamp_source",
    "class",
    "confidence",
    "frame_width",
    "frame_height",
    "x1",
    "y1",
    "x2",
    "y2",
    "center_x",
    "center_y",
    "smoothed_center_x",
    "smoothed_center_y",
    "width",
    "height",
    "area",
    "aspect_ratio",
    "frame_gap_before",
    "dx",
    "dy",
    "displacement",
    "smoothed_dx",
    "smoothed_dy",
    "smoothed_displacement",
    "raw_speed_px_per_frame",
    "raw_speed_px_per_second",
    "speed_px_per_frame",
    "speed_px_per_second",
    "heading_deg",
    "raw_heading_deg",
    "raw_acceleration_px_per_frame2",
    "raw_acceleration_px_per_second2",
    "acceleration_px_per_frame2",
    "acceleration_px_per_second2",
    "turn_angle_deg",
    "motion_state",
)
TRACK_SUMMARY_FIELDS = (
    "track_id",
    "class",
    "first_frame",
    "last_frame",
    "observations",
    "duration_sec",
    "path_length_px",
    "net_displacement_px",
    "straightness_ratio",
    "mean_speed_px_per_second",
    "median_speed_px_per_second",
    "max_speed_px_per_second",
    "speed_std",
    "mean_acceleration_px_per_second2",
    "acceleration_std",
    "direction_consistency",
    "mean_turn_angle_deg",
    "max_turn_angle_deg",
    "consecutive_frame_ratio",
    "longest_consecutive_run",
    "missing_frame_count",
    "dominant_motion_state",
    "motion_quality",
)


@dataclass(frozen=True)
class MotionConfig:
    """Transparent image-plane thresholds; speed units are pixels, never metres."""

    smoothing_alpha: float = 0.35
    stationary_max_px_per_frame: float = 0.5
    slow_max_px_per_frame: float = 2.0
    high_motion_min_px_per_frame: float = 20.0
    turning_mean_angle_deg: float = 35.0
    turning_direction_consistency_max: float = 0.85
    irregular_speed_cv_min: float = 0.75
    irregular_direction_consistency_max: float = 0.35
    max_acceleration_gap_frames: int = 1
    min_observations_for_state: int = 2
    min_observations_for_good_quality: int = 3
    max_missing_frame_ratio_for_good_quality: float = 0.5

    def __post_init__(self) -> None:
        numeric_thresholds = (
            self.smoothing_alpha,
            self.stationary_max_px_per_frame,
            self.slow_max_px_per_frame,
            self.high_motion_min_px_per_frame,
            self.turning_mean_angle_deg,
            self.turning_direction_consistency_max,
            self.irregular_speed_cv_min,
            self.irregular_direction_consistency_max,
            self.max_missing_frame_ratio_for_good_quality,
        )
        if any(not math.isfinite(value) for value in numeric_thresholds):
            raise ValueError("motion configuration values must be finite")
        if not 0 < self.smoothing_alpha <= 1:
            raise ValueError("smoothing_alpha must be in (0, 1]")
        if min(
            self.stationary_max_px_per_frame,
            self.slow_max_px_per_frame,
            self.high_motion_min_px_per_frame,
            self.turning_mean_angle_deg,
            self.irregular_speed_cv_min,
        ) < 0:
            raise ValueError("motion thresholds must be non-negative")
        if self.slow_max_px_per_frame < self.stationary_max_px_per_frame:
            raise ValueError("slow threshold must be >= stationary threshold")
        if self.high_motion_min_px_per_frame < self.slow_max_px_per_frame:
            raise ValueError("high-motion threshold must be >= slow threshold")
        if not 0 <= self.turning_direction_consistency_max <= 1:
            raise ValueError("turning_direction_consistency_max must be in [0, 1]")
        if not 0 <= self.irregular_direction_consistency_max <= 1:
            raise ValueError("irregular_direction_consistency_max must be in [0, 1]")
        if not 0 <= self.max_missing_frame_ratio_for_good_quality <= 1:
            raise ValueError("max missing-frame ratio must be in [0, 1]")
        if self.max_acceleration_gap_frames < 1:
            raise ValueError("max_acceleration_gap_frames must be positive")
        if self.min_observations_for_state < 2:
            raise ValueError("min_observations_for_state must be at least 2")


def _finite_number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"{field} is required")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric: {value!r}") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite: {value!r}")
    return result


def _optional_positive(value: Any) -> float | None:
    try:
        parsed = _finite_number(value, "metadata", optional=True)
    except ValueError:
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _round(value: float | None, places: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    result = round(value, places)
    return 0.0 if result == 0 else result


def _round_heading(value: float | None) -> float | None:
    rounded = _round(value)
    if rounded is None:
        return None
    return 0.0 if rounded >= 360 else rounded


def _timestamp_column(fieldnames: list[str] | None) -> str | None:
    fields = set(fieldnames or ())
    return next((field for field in TIMESTAMP_FIELDS if field in fields), None)


def _read_observations(
    tracks_csv: Path,
    fps: float | None,
    frame_width: float | None,
    frame_height: float | None,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[int, tuple[int, dict[str, Any]]]] = defaultdict(dict)
    with tracks_csv.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = set(REQUIRED_TRACK_FIELDS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "tracks.csv is missing required columns: " + ", ".join(sorted(missing))
            )
        timestamp_field = _timestamp_column(reader.fieldnames)
        for order, row in enumerate(reader):
            try:
                frame = int(row["frame"])
                track_id = str(int(row["track_id"]))
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid frame or track_id at CSV row {order + 2}") from error
            if frame < 0:
                raise ValueError(f"frame must be non-negative at CSV row {order + 2}")
            if not row["class_name"]:
                raise ValueError(f"class_name is empty at CSV row {order + 2}")
            coords = {
                key: _finite_number(row[key], key)
                for key in ("x1", "y1", "x2", "y2")
            }
            confidence = _finite_number(row["confidence"], "confidence")
            if not 0 <= confidence <= 1:
                raise ValueError(f"confidence must be in [0, 1] at CSV row {order + 2}")
            try:
                timestamp = (
                    _finite_number(row.get(timestamp_field), timestamp_field, optional=True)
                    if timestamp_field
                    else None
                )
            except ValueError:
                timestamp = None
            observation = {
                "track_id": track_id,
                "frame": frame,
                "class": row["class_name"],
                "confidence": confidence,
                **coords,
                "timestamp_input": timestamp,
                "timestamp_column": timestamp_field,
                "frame_width": frame_width,
                "frame_height": frame_height,
            }
            previous = grouped[track_id].get(frame)
            if previous is None or confidence > previous[1]["confidence"]:
                grouped[track_id][frame] = (order, observation)

    result: dict[str, list[dict[str, Any]]] = {}
    for track_id, frames in grouped.items():
        ordered = [frames[frame][1] for frame in sorted(frames)]
        supplied = [row["timestamp_input"] for row in ordered]
        supplied_is_valid = (
            all(value is not None for value in supplied)
            and all(supplied[index] > supplied[index - 1] for index in range(1, len(supplied)))
        )
        if supplied_is_valid:
            for row, timestamp in zip(ordered, supplied):
                row["timestamp_sec"] = timestamp
                row["timestamp_source"] = "track_csv_timestamp"
        elif fps is not None:
            for row in ordered:
                row["timestamp_sec"] = max(0, row["frame"] - 1) / fps
                row["timestamp_source"] = "video_fps"
        else:
            for row in ordered:
                row["timestamp_sec"] = None
                row["timestamp_source"] = "unavailable"
        result[track_id] = ordered
    return result


def _smooth_centers(
    centers: list[tuple[float, float]], alpha: float
) -> list[tuple[float, float]]:
    smoothed = [centers[0]]
    for center_x, center_y in centers[1:]:
        previous_x, previous_y = smoothed[-1]
        smoothed.append(
            (
                alpha * center_x + (1 - alpha) * previous_x,
                alpha * center_y + (1 - alpha) * previous_y,
            )
        )
    return smoothed


def _heading(dx: float, dy: float, displacement: float) -> float | None:
    if displacement <= 1e-12:
        return None
    return math.degrees(math.atan2(dy, dx)) % 360


def _turn_angle(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None:
        return None
    return abs((current - previous + 180) % 360 - 180)


def _classify(speed: float | None, turn: float | None, config: MotionConfig) -> str:
    if speed is None:
        return "INSUFFICIENT_DATA"
    if speed <= config.stationary_max_px_per_frame:
        return "STATIONARY"
    if speed >= config.high_motion_min_px_per_frame:
        return "HIGH_MOTION"
    if turn is not None and turn >= config.turning_mean_angle_deg:
        return "TURNING"
    if speed <= config.slow_max_px_per_frame:
        return "SLOW_MOVEMENT"
    return "STEADY_MOVEMENT"


def _dominant_state(
    speeds: list[float | None],
    turns: list[float | None],
    config: MotionConfig,
    direction_consistency: float | None,
) -> str:
    valid_count = sum(speed is not None for speed in speeds)
    if valid_count < config.min_observations_for_state - 1:
        return "INSUFFICIENT_DATA"
    counts = Counter(
        _classify(speed, turn, config)
        for speed, turn in zip(speeds, turns)
        if speed is not None
    )
    if not counts:
        return "INSUFFICIENT_DATA"
    speed_values = [speed for speed in speeds if speed is not None]
    average_speed = fmean(speed_values)
    speed_cv = pstdev(speed_values) / average_speed if len(speed_values) > 1 and average_speed > 0 else 0.0
    if (
        speed_cv >= config.irregular_speed_cv_min
        or (
            direction_consistency is not None
            and direction_consistency <= config.irregular_direction_consistency_max
        )
    ):
        return "IRREGULAR_MOTION"
    valid_turns = [turn for turn in turns if turn is not None]
    if (
        valid_turns
        and fmean(valid_turns) >= config.turning_mean_angle_deg
        and direction_consistency is not None
        and direction_consistency <= config.turning_direction_consistency_max
    ):
        return "TURNING"
    return sorted(counts, key=lambda state: (-counts[state], state))[0]


def _trajectory_quality(
    observations: list[dict[str, Any]],
    frame_gaps: list[int],
    config: MotionConfig,
) -> str:
    if len(observations) < config.min_observations_for_good_quality:
        return "INSUFFICIENT_DATA"
    span = observations[-1]["frame"] - observations[0]["frame"] + 1
    missing = max(0, span - len(observations))
    if span <= 0 or missing / span > config.max_missing_frame_ratio_for_good_quality:
        return "LOW_CONTINUITY"
    if all(row["timestamp_sec"] is None for row in observations):
        return "TIME_UNAVAILABLE"
    if any(gap > 0 for gap in frame_gaps):
        return "GAPPED"
    return "USABLE_IMAGE_PLANE"


def compute_track_trajectory(
    observations: list[dict[str, Any]],
    *,
    fps: float | None,
    config: MotionConfig = MotionConfig(),
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Compute motion rows, a summary, and a dashboard-ready trajectory."""
    if not observations:
        raise ValueError("At least one track observation is required")
    observations = sorted(observations, key=lambda item: item["frame"])
    centers = [
        ((row["x1"] + row["x2"]) / 2, (row["y1"] + row["y2"]) / 2)
        for row in observations
    ]
    smoothed_centers = _smooth_centers(centers, config.smoothing_alpha)
    count = len(observations)
    raw_deltas: list[tuple[float | None, float | None]] = [(None, None)]
    smoothed_deltas: list[tuple[float | None, float | None]] = [(None, None)]
    gaps: list[int | None] = [None]
    raw_speeds: list[float | None] = [None]
    smooth_speeds: list[float | None] = [None]
    raw_speeds_per_second: list[float | None] = [None]
    smooth_speeds_per_second: list[float | None] = [None]
    raw_headings: list[float | None] = [None]
    smooth_headings: list[float | None] = [None]
    accelerations_per_frame: list[float | None] = [None]
    accelerations_per_second: list[float | None] = [None]
    raw_accelerations_per_frame: list[float | None] = [None]
    raw_accelerations_per_second: list[float | None] = [None]
    turns: list[float | None] = [None]
    for index in range(1, count):
        previous = observations[index - 1]
        current = observations[index]
        frame_gap = current["frame"] - previous["frame"]
        gaps.append(max(0, frame_gap - 1))
        raw_dx = centers[index][0] - centers[index - 1][0]
        raw_dy = centers[index][1] - centers[index - 1][1]
        smooth_dx = smoothed_centers[index][0] - smoothed_centers[index - 1][0]
        smooth_dy = smoothed_centers[index][1] - smoothed_centers[index - 1][1]
        raw_displacement = math.hypot(raw_dx, raw_dy)
        smooth_displacement = math.hypot(smooth_dx, smooth_dy)
        dt = (
            current["timestamp_sec"] - previous["timestamp_sec"]
            if current["timestamp_sec"] is not None and previous["timestamp_sec"] is not None
            else None
        )
        raw_speed = raw_displacement / frame_gap if frame_gap > 0 else None
        smooth_speed = smooth_displacement / frame_gap if frame_gap > 0 else None
        raw_speed_sec = raw_displacement / dt if dt is not None and dt > 0 else None
        smooth_speed_sec = smooth_displacement / dt if dt is not None and dt > 0 else None
        raw_heading = _heading(raw_dx, raw_dy, raw_displacement)
        smooth_heading = _heading(smooth_dx, smooth_dy, smooth_displacement)
        raw_deltas.append((raw_dx, raw_dy))
        smoothed_deltas.append((smooth_dx, smooth_dy))
        raw_speeds.append(raw_speed)
        smooth_speeds.append(smooth_speed)
        raw_speeds_per_second.append(raw_speed_sec)
        smooth_speeds_per_second.append(smooth_speed_sec)
        raw_headings.append(raw_heading)
        smooth_headings.append(smooth_heading)
        turns.append(_turn_angle(smooth_headings[index - 1], smooth_heading))
        if (
            frame_gap <= config.max_acceleration_gap_frames
            and index >= 2
            and smooth_speeds[index - 1] is not None
            and smooth_speed is not None
            and raw_speed_sec is not None
            and smooth_speed_sec is not None
            and dt is not None
            and dt > 0
            and current["timestamp_sec"] is not None
            and observations[index - 1]["timestamp_sec"] is not None
        ):
            previous_dt = (
                observations[index - 1]["timestamp_sec"] - observations[index - 2]["timestamp_sec"]
                if observations[index - 2]["timestamp_sec"] is not None
                else None
            )
            if previous_dt is not None and previous_dt > 0:
                accelerations_per_frame.append(
                    (smooth_speed - smooth_speeds[index - 1]) / frame_gap
                )
                previous_speed_sec = smooth_speeds_per_second[index - 1]
                accelerations_per_second.append(
                    (smooth_speed_sec - previous_speed_sec) / dt
                    if previous_speed_sec is not None
                    else None
                )
                previous_raw_speed = raw_speeds[index - 1]
                previous_raw_speed_sec = raw_speeds_per_second[index - 1]
                raw_accelerations_per_frame.append(
                    (raw_speed - previous_raw_speed) / frame_gap
                    if previous_raw_speed is not None
                    else None
                )
                raw_accelerations_per_second.append(
                    (raw_speed_sec - previous_raw_speed_sec) / dt
                    if previous_raw_speed_sec is not None
                    else None
                )
            else:
                accelerations_per_frame.append(None)
                accelerations_per_second.append(None)
                raw_accelerations_per_frame.append(None)
                raw_accelerations_per_second.append(None)
        else:
            accelerations_per_frame.append(None)
            accelerations_per_second.append(None)
            raw_accelerations_per_frame.append(None)
            raw_accelerations_per_second.append(None)

    output_rows: list[dict[str, Any]] = []
    for index, observation in enumerate(observations):
        dx, dy = raw_deltas[index]
        smoothed_dx, smoothed_dy = smoothed_deltas[index]
        raw_displacement = math.hypot(dx, dy) if dx is not None and dy is not None else None
        smoothed_displacement = (
            math.hypot(smoothed_dx, smoothed_dy)
            if smoothed_dx is not None and smoothed_dy is not None
            else None
        )
        width = max(0.0, observation["x2"] - observation["x1"])
        height = max(0.0, observation["y2"] - observation["y1"])
        row = {
            "track_id": observation["track_id"],
            "frame": observation["frame"],
            "timestamp_sec": _round(observation["timestamp_sec"]),
            "timestamp_source": observation["timestamp_source"],
            "class": observation["class"],
            "confidence": _round(observation["confidence"]),
            "frame_width": _round(observation["frame_width"]),
            "frame_height": _round(observation["frame_height"]),
            "x1": _round(observation["x1"]),
            "y1": _round(observation["y1"]),
            "x2": _round(observation["x2"]),
            "y2": _round(observation["y2"]),
            "center_x": _round(centers[index][0]),
            "center_y": _round(centers[index][1]),
            "smoothed_center_x": _round(smoothed_centers[index][0]),
            "smoothed_center_y": _round(smoothed_centers[index][1]),
            "width": _round(width),
            "height": _round(height),
            "area": _round(width * height),
            "aspect_ratio": _round(width / height) if height > 0 else None,
            "frame_gap_before": gaps[index],
            "dx": _round(dx),
            "dy": _round(dy),
            "displacement": _round(raw_displacement),
            "smoothed_dx": _round(smoothed_dx),
            "smoothed_dy": _round(smoothed_dy),
            "smoothed_displacement": _round(smoothed_displacement),
            "raw_speed_px_per_frame": _round(raw_speeds[index]),
            "raw_speed_px_per_second": _round(raw_speeds_per_second[index]),
            "speed_px_per_frame": _round(smooth_speeds[index]),
            "speed_px_per_second": _round(smooth_speeds_per_second[index]),
            "heading_deg": _round_heading(smooth_headings[index]),
            "raw_heading_deg": _round_heading(raw_headings[index]),
            "raw_acceleration_px_per_frame2": _round(raw_accelerations_per_frame[index]),
            "raw_acceleration_px_per_second2": _round(raw_accelerations_per_second[index]),
            "acceleration_px_per_frame2": _round(accelerations_per_frame[index]),
            "acceleration_px_per_second2": _round(accelerations_per_second[index]),
            "turn_angle_deg": _round(turns[index]),
            "motion_state": _classify(smooth_speeds[index], turns[index], config),
        }
        output_rows.append(row)

    valid_speeds = [value for value in smooth_speeds_per_second if value is not None]
    valid_accelerations = [value for value in accelerations_per_second if value is not None]
    valid_turns = [value for value in turns if value is not None]
    valid_headings = [value for value in smooth_headings if value is not None]
    frame_values = [row["frame"] for row in observations]
    frame_gaps = [max(0, frame_values[i] - frame_values[i - 1] - 1) for i in range(1, count)]
    frame_span = frame_values[-1] - frame_values[0] + 1
    consecutive_intervals = sum(gap == 0 for gap in frame_gaps)
    consecutive_ratio = (
        consecutive_intervals / len(frame_gaps) if frame_gaps else None
    )
    longest_run = current_run = 1
    for gap in frame_gaps:
        current_run = current_run + 1 if gap == 0 else 1
        longest_run = max(longest_run, current_run)
    path_length = sum(
        math.hypot(smoothed_deltas[i][0], smoothed_deltas[i][1])
        for i in range(1, count)
    )
    net_displacement = (
        math.hypot(
            smoothed_centers[-1][0] - smoothed_centers[0][0],
            smoothed_centers[-1][1] - smoothed_centers[0][1],
        )
        if count > 1
        else 0.0
    )
    straightness = min(1.0, net_displacement / path_length) if path_length > 0 else None
    mean_heading_vector_x = fmean(math.cos(math.radians(value)) for value in valid_headings) if valid_headings else None
    mean_heading_vector_y = fmean(math.sin(math.radians(value)) for value in valid_headings) if valid_headings else None
    direction_consistency = (
        min(1.0, math.hypot(mean_heading_vector_x, mean_heading_vector_y))
        if mean_heading_vector_x is not None and mean_heading_vector_y is not None
        else None
    )
    dominant_state = _dominant_state(
        smooth_speeds, turns, config, direction_consistency
    )
    duration = (
        observations[-1]["timestamp_sec"] - observations[0]["timestamp_sec"]
        if observations[0]["timestamp_sec"] is not None
        and observations[-1]["timestamp_sec"] is not None
        else None
    )
    quality = _trajectory_quality(observations, frame_gaps, config)
    summary = {
        "track_id": observations[0]["track_id"],
        "class": observations[0]["class"],
        "first_frame": frame_values[0],
        "last_frame": frame_values[-1],
        "observations": count,
        "duration_sec": _round(duration),
        "path_length_px": _round(path_length),
        "net_displacement_px": _round(net_displacement),
        "straightness_ratio": _round(straightness),
        "mean_speed_px_per_second": _round(fmean(valid_speeds) if valid_speeds else None),
        "median_speed_px_per_second": _round(median(valid_speeds) if valid_speeds else None),
        "max_speed_px_per_second": _round(max(valid_speeds) if valid_speeds else None),
        "speed_std": _round(pstdev(valid_speeds) if len(valid_speeds) > 1 else (0.0 if valid_speeds else None)),
        "mean_acceleration_px_per_second2": _round(fmean(valid_accelerations) if valid_accelerations else None),
        "acceleration_std": _round(pstdev(valid_accelerations) if len(valid_accelerations) > 1 else (0.0 if valid_accelerations else None)),
        "direction_consistency": _round(direction_consistency),
        "mean_turn_angle_deg": _round(fmean(valid_turns) if valid_turns else None),
        "max_turn_angle_deg": _round(max(valid_turns) if valid_turns else None),
        "consecutive_frame_ratio": _round(consecutive_ratio),
        "longest_consecutive_run": longest_run,
        "missing_frame_count": max(0, frame_span - count),
        "dominant_motion_state": dominant_state,
        "motion_quality": quality,
    }
    trajectory = {
        "class": observations[0]["class"],
        "coordinate_system": "image_plane_pixels",
        "smoothing": {
            "method": "exponential_moving_average",
            "alpha": config.smoothing_alpha,
            "used_for_motion_state_classification": True,
        },
        "current_position": {
            "frame": frame_values[-1],
            "timestamp_sec": _round(observations[-1]["timestamp_sec"]),
            "x_px": _round(smoothed_centers[-1][0]),
            "y_px": _round(smoothed_centers[-1][1]),
        },
        "trajectory_points": [
            {
                "frame": row["frame"],
                "timestamp_sec": row["timestamp_sec"],
                "x_px": row["center_x"],
                "y_px": row["center_y"],
                "smoothed_x_px": row["smoothed_center_x"],
                "smoothed_y_px": row["smoothed_center_y"],
                "speed_px_per_frame": row["speed_px_per_frame"],
                "speed_px_per_second": row["speed_px_per_second"],
                "heading_deg": row["heading_deg"],
                "motion_state": row["motion_state"],
            }
            for row in output_rows
        ],
        "current_speed_px_per_frame": _round(smooth_speeds[-1]),
        "current_speed_px_per_second": _round(smooth_speeds_per_second[-1]),
        "current_heading_deg": _round(smooth_headings[-1]),
        "current_acceleration_px_per_frame2": _round(accelerations_per_frame[-1]),
        "current_acceleration_px_per_second2": _round(accelerations_per_second[-1]),
        "motion_state": dominant_state,
        "summary": summary,
    }
    return output_rows, summary, trajectory


def process_tracks_csv(
    tracks_csv: str | Path,
    output_dir: str | Path | None = None,
    *,
    fps: float | None = None,
    frame_width: float | None = None,
    frame_height: float | None = None,
    config: MotionConfig = MotionConfig(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Write run-local observation/summary CSVs and dashboard trajectories JSON."""
    tracks_csv = Path(tracks_csv)
    output_dir = Path(output_dir) if output_dir is not None else tracks_csv.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_fps = _optional_positive(fps)
    safe_width = _optional_positive(frame_width)
    safe_height = _optional_positive(frame_height)
    grouped = _read_observations(tracks_csv, safe_fps, safe_width, safe_height)
    if not grouped:
        raise ValueError("tracks.csv contains no observations for motion analysis")

    observation_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    trajectories: dict[str, Any] = {}
    for track_id in sorted(grouped, key=int):
        motion_rows, summary, trajectory = compute_track_trajectory(
            grouped[track_id], fps=safe_fps, config=config
        )
        observation_rows.extend(motion_rows)
        summary_rows.append(summary)
        trajectories[track_id] = trajectory
    metadata = {
        "schema_version": 1,
        "coordinate_system": "image_plane_pixels",
        "physical_world_calibration": "not_available",
        "fps": safe_fps,
        "frame_width": safe_width,
        "frame_height": safe_height,
        "configuration": asdict(config),
        "threshold_units": "image-plane pixels per frame unless otherwise specified",
        "heading_range_degrees": "[0, 360); undefined for zero displacement",
        "tracks": trajectories,
    }
    with (output_dir / "motion_observations.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=MOTION_OBSERVATION_FIELDS)
        writer.writeheader()
        writer.writerows(observation_rows)
    with (output_dir / "motion_track_summary.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=TRACK_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)
    with (output_dir / "trajectories.json").open("w", encoding="utf-8") as output:
        json.dump(metadata, output, allow_nan=False, indent=2)
        output.write("\n")
    return observation_rows, summary_rows, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracks_csv")
    parser.add_argument("--output-dir")
    parser.add_argument("--fps", type=float)
    parser.add_argument("--frame-width", type=float)
    parser.add_argument("--frame-height", type=float)
    parser.add_argument("--smoothing-alpha", type=float, default=MotionConfig.smoothing_alpha)
    args = parser.parse_args()
    config = MotionConfig(smoothing_alpha=args.smoothing_alpha)
    observations, summaries, _ = process_tracks_csv(
        args.tracks_csv,
        args.output_dir,
        fps=args.fps,
        frame_width=args.frame_width,
        frame_height=args.frame_height,
        config=config,
    )
    print(f"Wrote {len(observations)} image-plane observation rows for {len(summaries)} tracks.")


if __name__ == "__main__":
    main()
