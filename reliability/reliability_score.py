"""Estimate track reliability from Phase 3 temporal feature rows."""

import argparse
import csv
import math
from math import hypot
from pathlib import Path
from statistics import fmean

DEFAULT_WEIGHTS = {
    "confidence": 0.35,
    "consistency": 0.25,
    "persistence": 0.25,
    "motion": 0.15,
}
DEFAULT_THRESHOLD = 0.6
MIN_PERSISTENT_FRAMES = 10
CONFIDENCE_STD_SCALE = 0.5
OUTPUT_FIELDS = (
    "track_id",
    "confidence_score",
    "consistency_score",
    "persistence_score",
    "motion_score",
    "reliability_score",
    "status",
    "frames_seen",
    "mean_confidence",
    "persistence",
)
MOTION_DIAGNOSTIC_FIELDS = (
    "legacy_motion_score",
    "trajectory_motion_score",
    "trajectory_direction_consistency",
    "trajectory_straightness_ratio",
    "trajectory_speed_stability",
    "trajectory_acceleration_stability",
    "trajectory_continuity",
    "trajectory_motion_state",
    "trajectory_motion_quality",
)


def _clip(value):
    return max(0.0, min(1.0, value))


def _validate_weights(weights):
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError(f"weights must define exactly: {', '.join(DEFAULT_WEIGHTS)}")
    if any(value < 0 for value in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("weights must be non-negative and have a positive sum")


def estimate_reliability(row, weights=None, threshold=DEFAULT_THRESHOLD):
    """Return component scores and a weighted reliability score for one track."""
    weights = DEFAULT_WEIGHTS if weights is None else weights
    _validate_weights(weights)
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    mean_confidence = _clip(float(row["mean_confidence"]))
    confidence_std = max(0.0, float(row["confidence_std"]))
    confidence_score = mean_confidence
    consistency_score = _clip(1.0 - confidence_std / CONFIDENCE_STD_SCALE)

    frames_seen = max(0, int(row["frames_seen"]))
    persistence = _clip(float(row["persistence"]))
    persistence_score = _clip(
        min(frames_seen / MIN_PERSISTENT_FRAMES, 1.0) * persistence
    )

    mean_displacement = max(0.0, float(row["mean_displacement"]))
    mean_width = max(0.0, float(row["mean_width"]))
    mean_height = max(0.0, float(row["mean_height"]))
    box_diagonal = hypot(mean_width, mean_height)
    motion_score = (
        1.0 / (1.0 + mean_displacement / box_diagonal)
        if box_diagonal > 0
        else float(mean_displacement == 0)
    )

    total_weight = sum(weights.values())
    reliability_score = _clip(
        sum(
            weights[name] * score
            for name, score in (
                ("confidence", confidence_score),
                ("consistency", consistency_score),
                ("persistence", persistence_score),
                ("motion", motion_score),
            )
        )
        / total_weight
    )
    return {
        "track_id": row["track_id"],
        "confidence_score": confidence_score,
        "consistency_score": consistency_score,
        "persistence_score": persistence_score,
        "motion_score": motion_score,
        "reliability_score": reliability_score,
        "status": "CONFIDENT" if reliability_score >= threshold else "UNCERTAIN",
        "frames_seen": frames_seen,
        "mean_confidence": mean_confidence,
        "persistence": persistence,
    }


def _trajectory_motion_diagnostics(summary, legacy_motion_score):
    """Return bounded image-plane motion diagnostics; never alter reliability."""
    components = {}
    for field, output_field in (
        ("direction_consistency", "trajectory_direction_consistency"),
        ("straightness_ratio", "trajectory_straightness_ratio"),
        ("consecutive_frame_ratio", "trajectory_continuity"),
    ):
        value = summary.get(field)
        if value not in (None, ""):
            parsed = float(value)
            if not math.isfinite(parsed) or not 0 <= parsed <= 1:
                raise ValueError(f"{field} must be finite and in [0, 1]")
            components[output_field] = parsed

    speed_std = summary.get("speed_std")
    mean_speed = summary.get("mean_speed_px_per_second")
    if speed_std not in (None, "") and mean_speed not in (None, ""):
        parsed_std = float(speed_std)
        parsed_mean = float(mean_speed)
        if (
            not math.isfinite(parsed_std)
            or not math.isfinite(parsed_mean)
            or parsed_std < 0
            or parsed_mean < 0
        ):
            raise ValueError("trajectory speed statistics must be finite and non-negative")
        speed_cv = parsed_std / parsed_mean if parsed_mean > 0 else (0.0 if parsed_std == 0 else math.inf)
        components["trajectory_speed_stability"] = (
            1.0 / (1.0 + speed_cv) if math.isfinite(speed_cv) else 0.0
        )

    acceleration_std = summary.get("acceleration_std")
    mean_acceleration = summary.get("mean_acceleration_px_per_second2")
    if acceleration_std not in (None, "") and mean_acceleration not in (None, ""):
        parsed_std = float(acceleration_std)
        parsed_mean = float(mean_acceleration)
        if (
            not math.isfinite(parsed_std)
            or not math.isfinite(parsed_mean)
            or parsed_std < 0
        ):
            raise ValueError("trajectory acceleration statistics must be finite")
        scale = parsed_std + abs(parsed_mean)
        components["trajectory_acceleration_stability"] = (
            abs(parsed_mean) / scale if scale > 0 else 1.0
        )

    score_values = list(components.values())
    score = fmean(score_values) if score_values else None
    return {
        "legacy_motion_score": legacy_motion_score,
        "trajectory_motion_score": score,
        "trajectory_direction_consistency": components.get(
            "trajectory_direction_consistency"
        ),
        "trajectory_straightness_ratio": components.get(
            "trajectory_straightness_ratio"
        ),
        "trajectory_speed_stability": components.get(
            "trajectory_speed_stability"
        ),
        "trajectory_acceleration_stability": components.get(
            "trajectory_acceleration_stability"
        ),
        "trajectory_continuity": components.get("trajectory_continuity"),
        "trajectory_motion_state": summary.get("dominant_motion_state") or "",
        "trajectory_motion_quality": summary.get("motion_quality") or "",
    }


def process_temporal_csv(
    temporal_csv,
    output_csv=None,
    weights=None,
    threshold=DEFAULT_THRESHOLD,
    trajectory_summary_csv=None,
    progress_callback=None,
):
    """Read Phase 3 temporal features and write track-level reliability rows."""
    temporal_csv = Path(temporal_csv)
    output_csv = (
        Path(output_csv)
        if output_csv
        else temporal_csv.with_name("reliability_results.csv")
    )
    trajectory_summaries = None
    if trajectory_summary_csv is not None:
        trajectory_summaries = {}
        with Path(trajectory_summary_csv).open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                track_id = row.get("track_id", "")
                if not track_id or track_id in trajectory_summaries:
                    raise ValueError(f"Invalid or duplicate trajectory summary track_id: {track_id!r}")
                trajectory_summaries[track_id] = row

    with temporal_csv.open(newline="", encoding="utf-8") as source:
        temporal_rows = list(csv.DictReader(source))
    if trajectory_summaries is not None and {
        row["track_id"] for row in temporal_rows
    } != set(trajectory_summaries):
        raise ValueError("Trajectory summary track IDs do not match temporal tracks")
    results = []
    total_rows = len(temporal_rows)
    for index, row in enumerate(temporal_rows, start=1):
        result = estimate_reliability(row, weights=weights, threshold=threshold)
        if trajectory_summaries is not None:
            track_id = row["track_id"]
            if track_id not in trajectory_summaries:
                raise ValueError(f"Missing trajectory summary for track {track_id}")
            diagnostics = _trajectory_motion_diagnostics(
                trajectory_summaries[track_id], result["motion_score"]
            )
            result.update(diagnostics)
        results.append(result)
        if progress_callback is not None:
            progress_callback(index, total_rows)

    with output_csv.open("w", newline="", encoding="utf-8") as output:
        fieldnames = OUTPUT_FIELDS + (
            MOTION_DIAGNOSTIC_FIELDS if trajectory_summaries is not None else ()
        )
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("temporal_csv", help="Phase 3 temporal_features.csv input")
    parser.add_argument("--output", help="reliability output CSV path")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    results = process_temporal_csv(
        args.temporal_csv, output_csv=args.output, threshold=args.threshold
    )
    print(f"Wrote reliability results for {len(results)} tracks.")


if __name__ == "__main__":
    main()
