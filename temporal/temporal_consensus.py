"""Uncertainty-aware temporal consensus decision layer for object tracks.

Aggregates per-frame detector observations for each track, evaluates class
consistency, persistence, gaps, and confidence, and assigns a track-level
decision: Bird, Drone, or Uncertain (Needs Review). Raw detector predictions
remain unmodified.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

TEMPORAL_CONSENSUS_VERSION = "temporal-consensus-v1"
CONSENSUS_SCORE_INTERPRETATION = "heuristic decision score; not calibrated probability"


@dataclass(frozen=True)
class TemporalConsensusConfig:
    """Configurable thresholds and rules for temporal consensus.

    Rationale:
    - min_observations: A track with fewer than 3 frames lacks sufficient temporal
      support to distinguish transient false positives from real targets.
    - consistency_threshold: If dominant class observations constitute less than 70%
      of the track, conflicting class detections indicate identity ambiguity or flipping.
    - min_mean_confidence: Mean confidence below 0.35 indicates weak detector evidence
      and warrants manual or multimodal review.
    - max_class_switch_ratio: Switching between Bird and Drone more than 25% of steps
      indicates severe classifier oscillation.
    - max_gap_ratio: If missing frames exceed 50% of the span, track continuity is degraded.
    """

    min_observations: int = 3
    consistency_threshold: float = 0.70
    min_mean_confidence: float = 0.35
    max_class_switch_ratio: float = 0.25
    max_gap_ratio: float = 0.50
    confidence_weight: float = 0.40
    consistency_weight: float = 0.40
    continuity_weight: float = 0.20

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_observations, bool)
            or not isinstance(self.min_observations, int)
            or self.min_observations < 1
        ):
            raise ValueError("min_observations must be a positive integer")
        if any(
            not math.isfinite(value) or not 0 <= value <= 1
            for value in (
                self.consistency_threshold,
                self.min_mean_confidence,
                self.max_class_switch_ratio,
                self.max_gap_ratio,
            )
        ):
            raise ValueError("Temporal consensus thresholds must be finite values in [0, 1]")
        weights = (
            self.confidence_weight,
            self.consistency_weight,
            self.continuity_weight,
        )
        if any(not math.isfinite(weight) or weight < 0 for weight in weights):
            raise ValueError("Temporal consensus weights must be finite and non-negative")
        if not math.isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("Temporal consensus weights must sum to 1")


CONSENSUS_FIELDS = (
    "track_id",
    "consensus_label",
    "decision_status",
    "dominant_class",
    "dominant_class_ratio",
    "consensus_confidence",
    "observation_count",
    "class_switch_count",
    "gap_count",
    "max_gap",
    "reasons",
    "class_counts",
    "mean_confidences",
    "supporting_frame_indexes",
    "contradictory_frame_indexes",
    "unclassified_frame_indexes",
    "score_interpretation",
)
LEGACY_CONSENSUS_FIELDS = CONSENSUS_FIELDS[:11]
LEGACY_SCORE_INTERPRETATION = "legacy heuristic score; not calibrated probability"


def normalize_class_name(name: str | int | None) -> str:
    """Normalize class identifiers to canonical Bird or Drone names."""
    if name is None:
        return "Unknown"
    text = str(name).strip().casefold()
    if text in {"0", "bird"}:
        return "Bird"
    if text in {"1", "drone", "uav"}:
        return "Drone"
    return "Unknown"


def evaluate_track_consensus(
    observations: Sequence[dict[str, Any]],
    config: TemporalConsensusConfig | None = None,
    causal_up_to_frame: int | None = None,
) -> dict[str, Any]:
    """Evaluate temporal consensus for a single track.

    Args:
        observations: Sequence of observation dicts, each having at least:
                      'frame', 'confidence', and ('class' or 'class_name' or 'class_id').
        config: Configuration instance with thresholds.
        causal_up_to_frame: If provided, excludes observations beyond this frame
                            to prevent future-frame leakage.

    Returns:
        Structured consensus decision dict.
    """
    cfg = config or TemporalConsensusConfig()
    if causal_up_to_frame is not None:
        causal_up_to_frame = _validated_frame(causal_up_to_frame, "causal_up_to_frame")

    valid_obs: list[dict[str, Any]] = []
    for obs in observations:
        if not isinstance(obs, dict):
            raise ValueError("Temporal observations must be mappings")
        frame_value = obs.get("frame", obs.get("source_frame_index"))
        frame = _validated_frame(frame_value, "observation frame")
        if causal_up_to_frame is not None and frame > causal_up_to_frame:
            continue
        conf_raw = obs.get("confidence")
        try:
            conf = float(conf_raw)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid detector confidence: {conf_raw!r}")
        if not math.isfinite(conf) or not 0 <= conf <= 1:
            raise ValueError("Detector confidence must be finite and in [0, 1]")
        raw_class = next(
            (
                obs[key]
                for key in ("class", "class_name", "class_id")
                if key in obs and obs[key] is not None and str(obs[key]).strip()
            ),
            None,
        )
        norm_class = normalize_class_name(raw_class)
        valid_obs.append(
            {
                "frame": frame,
                "confidence": conf,
                "class": norm_class,
            }
        )

    valid_obs.sort(key=lambda o: o["frame"])
    if len({observation["frame"] for observation in valid_obs}) != len(valid_obs):
        raise ValueError("A track cannot contain multiple observations for the same frame")
    n = len(valid_obs)
    reasons: list[str] = []

    bird_confs = [o["confidence"] for o in valid_obs if o["class"] == "Bird"]
    drone_confs = [o["confidence"] for o in valid_obs if o["class"] == "Drone"]
    bird_count = len(bird_confs)
    drone_count = len(drone_confs)
    unknown_count = n - bird_count - drone_count

    if drone_count > bird_count:
        dominant = "Drone"
        dominant_count = drone_count
        dominant_confs = drone_confs
    elif bird_count > drone_count:
        dominant = "Bird"
        dominant_count = bird_count
        dominant_confs = bird_confs
    elif bird_count or drone_count:
        bird_mean = sum(bird_confs) / bird_count if bird_count else 0.0
        drone_mean = sum(drone_confs) / drone_count if drone_count else 0.0
        dominant = "Drone" if drone_mean >= bird_mean else "Bird"
        dominant_count = drone_count if dominant == "Drone" else bird_count
        dominant_confs = drone_confs if dominant == "Drone" else bird_confs
    else:
        dominant = "Unknown"
        dominant_count = 0
        dominant_confs = []

    dominant_ratio = dominant_count / n if n else 0.0
    supporting_frames = [
        o["frame"] for o in valid_obs if dominant != "Unknown" and o["class"] == dominant
    ]
    contradictory_frames = [
        o["frame"]
        for o in valid_obs
        if o["class"] in {"Bird", "Drone"} and o["class"] != dominant
    ]
    unclassified_frames = [o["frame"] for o in valid_obs if o["class"] == "Unknown"]
    if unknown_count:
        reasons.append("unrecognized_class_observation")

    if n < cfg.min_observations:
        reasons.append("insufficient_observations")
        return {
            "consensus_label": "Uncertain",
            "decision_status": "uncertain",
            "dominant_class": dominant,
            "dominant_class_ratio": round(dominant_ratio, 4),
            "consensus_confidence": (
                sum(o["confidence"] for o in valid_obs) / n if n > 0 else 0.0
            ),
            "observation_count": n,
            "class_switch_count": 0,
            "gap_count": 0,
            "max_gap": 0,
            "reasons": reasons,
            "class_counts": {"Bird": bird_count, "Drone": drone_count, "Unknown": unknown_count},
            "mean_confidences": {
                "Bird": round(sum(bird_confs) / bird_count, 4) if bird_count else 0.0,
                "Drone": round(sum(drone_confs) / drone_count, 4) if drone_count else 0.0,
            },
            "supporting_frame_indexes": supporting_frames,
            "contradictory_frame_indexes": contradictory_frames,
            "unclassified_frame_indexes": unclassified_frames,
            "score_interpretation": CONSENSUS_SCORE_INTERPRETATION,
            "evidence_references_available": True,
        }

    class_switches = 0
    known_observations = [o for o in valid_obs if o["class"] in {"Bird", "Drone"}]
    for i in range(1, len(known_observations)):
        if known_observations[i]["class"] != known_observations[i - 1]["class"]:
            class_switches += 1

    frames = [o["frame"] for o in valid_obs]
    gaps = [frames[i] - frames[i - 1] - 1 for i in range(1, n) if frames[i] - frames[i - 1] > 1]
    gap_count = len(gaps)
    max_gap = max(gaps) if gaps else 0
    total_gap_frames = sum(gaps)
    span = (frames[-1] - frames[0] + 1) if frames[-1] >= frames[0] else n
    gap_ratio = total_gap_frames / span if span > 0 else 0.0
    continuity = n / span if span > 0 else 1.0

    mean_dominant_conf = sum(dominant_confs) / dominant_count if dominant_count else 0.0
    switch_ratio = (
        class_switches / (len(known_observations) - 1)
        if len(known_observations) > 1
        else 0.0
    )

    is_uncertain = False
    if dominant == "Unknown":
        reasons.append("no_recognized_class_evidence")
        is_uncertain = True
    if dominant_ratio < cfg.consistency_threshold:
        reasons.append("class_switching_conflict")
        is_uncertain = True

    if switch_ratio > cfg.max_class_switch_ratio:
        if "class_switching_conflict" not in reasons:
            reasons.append("frequent_class_switching")
        is_uncertain = True

    if mean_dominant_conf < cfg.min_mean_confidence:
        reasons.append("low_detector_confidence")
        is_uncertain = True

    if gap_ratio > cfg.max_gap_ratio:
        reasons.append("excessive_frame_gaps")
        if dominant_ratio < cfg.consistency_threshold + 0.10:
            is_uncertain = True

    consensus_confidence = max(
        0.0,
        min(
            1.0,
                cfg.confidence_weight * mean_dominant_conf
            + cfg.consistency_weight * dominant_ratio
            + cfg.continuity_weight * continuity,
        ),
    )

    if is_uncertain:
        consensus_label = "Uncertain"
        decision_status = "uncertain"
    else:
        consensus_label = dominant
        decision_status = "confident"
        reasons.append(f"consistent_{dominant.lower()}_observations")

    return {
        "consensus_label": consensus_label,
        "decision_status": decision_status,
        "dominant_class": dominant,
        "dominant_class_ratio": round(dominant_ratio, 4),
        "consensus_confidence": round(consensus_confidence, 4),
        "observation_count": n,
        "class_switch_count": class_switches,
        "gap_count": gap_count,
        "max_gap": max_gap,
        "reasons": reasons,
        "class_counts": {"Bird": bird_count, "Drone": drone_count, "Unknown": unknown_count},
        "mean_confidences": {
            "Bird": round(sum(bird_confs) / bird_count, 4) if bird_count else 0.0,
            "Drone": round(sum(drone_confs) / drone_count, 4) if drone_count else 0.0,
        },
        "supporting_frame_indexes": supporting_frames,
        "contradictory_frame_indexes": contradictory_frames,
        "unclassified_frame_indexes": unclassified_frames,
        "score_interpretation": CONSENSUS_SCORE_INTERPRETATION,
        "evidence_references_available": True,
    }


def _validated_frame(value: Any, description: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{description} must be a non-negative integer")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{description} must be a non-negative integer") from error
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError(f"{description} must be a non-negative integer")
    return int(number)


def process_tracks_consensus(
    tracks_csv_path: str | Path,
    output_csv_path: str | Path,
    config: TemporalConsensusConfig | None = None,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Process a tracks.csv file and produce track-level temporal consensus rows."""
    tracks_path = Path(tracks_csv_path)
    output_path = Path(output_csv_path)

    if not tracks_path.is_file():
        raise FileNotFoundError(f"tracks.csv not found at: {tracks_path}")

    from collections import defaultdict
    tracks: dict[str, list[dict[str, Any]]] = defaultdict(list)

    with tracks_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"frame", "track_id", "class_name", "confidence"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("tracks.csv is missing temporal consensus columns")
        for row in reader:
            if any(value is None for value in row.values()):
                raise ValueError("tracks.csv contains a malformed temporal consensus row")
            if not row["track_id"].isdecimal():
                raise ValueError(f"Invalid track_id in tracks.csv: {row['track_id']!r}")
            tid = str(int(row["track_id"]))
            tracks[tid].append(row)

    results = []
    total = len(tracks)
    if progress_callback is not None and not total:
        progress_callback(0, 0)
    for tid in sorted(tracks.keys(), key=int):
        if check_cancelled is not None:
            check_cancelled()
        obs = tracks[tid]
        decision = evaluate_track_consensus(obs, config=config)
        decision["track_id"] = tid
        results.append(decision)
        if progress_callback is not None:
            progress_callback(len(results), total)
    if check_cancelled is not None:
        check_cancelled()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CONSENSUS_FIELDS,
        )
        writer.writeheader()
        for res in results:
            writer.writerow(
                {
                    "track_id": res["track_id"],
                    "consensus_label": res["consensus_label"],
                    "decision_status": res["decision_status"],
                    "dominant_class": res["dominant_class"],
                    "dominant_class_ratio": res["dominant_class_ratio"],
                    "consensus_confidence": res["consensus_confidence"],
                    "observation_count": res["observation_count"],
                    "class_switch_count": res["class_switch_count"],
                    "gap_count": res["gap_count"],
                    "max_gap": res["max_gap"],
                    "reasons": ";".join(res["reasons"]),
                    "class_counts": json.dumps(res["class_counts"], separators=(",", ":")),
                    "mean_confidences": json.dumps(
                        res["mean_confidences"], separators=(",", ":")
                    ),
                    "supporting_frame_indexes": json.dumps(
                        res["supporting_frame_indexes"], separators=(",", ":")
                    ),
                    "contradictory_frame_indexes": json.dumps(
                        res["contradictory_frame_indexes"], separators=(",", ":")
                    ),
                    "unclassified_frame_indexes": json.dumps(
                        res["unclassified_frame_indexes"], separators=(",", ":")
                    ),
                    "score_interpretation": res["score_interpretation"],
                }
            )

    return results


def load_tracks_consensus(consensus_csv_path: str | Path) -> dict[str, dict[str, Any]]:
    """Load and validate persisted track-level decisions without altering observations."""
    path = Path(consensus_csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Temporal consensus output not found at: {path}")
    required = set(LEGACY_CONSENSUS_FIELDS)
    results: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("Temporal consensus CSV is missing required columns")
        available_fields = set(reader.fieldnames or ())
        has_frame_references = {
            "supporting_frame_indexes",
            "contradictory_frame_indexes",
            "unclassified_frame_indexes",
        }.issubset(available_fields)
        for line_number, row in enumerate(reader, start=2):
            if any(value is None for value in row.values()):
                raise ValueError(f"Temporal consensus CSV has a malformed row at line {line_number}")
            track_id = row["track_id"]
            if not track_id.isdecimal():
                raise ValueError(f"Invalid temporal consensus track_id at line {line_number}")
            track_id = str(int(track_id))
            if track_id in results:
                raise ValueError(f"Duplicate temporal consensus track_id: {track_id}")
            if row["consensus_label"] not in {"Bird", "Drone", "Uncertain"}:
                raise ValueError(f"Invalid temporal consensus label for track {track_id}")
            if row["decision_status"] not in {"confident", "uncertain"}:
                raise ValueError(f"Invalid temporal consensus status for track {track_id}")
            try:
                result = {
                    "consensus_label": row["consensus_label"],
                    "decision_status": row["decision_status"],
                    "dominant_class": row["dominant_class"],
                    "dominant_class_ratio": float(row["dominant_class_ratio"]),
                    "consensus_confidence": float(row["consensus_confidence"]),
                    "observation_count": int(row["observation_count"]),
                    "class_switch_count": int(row["class_switch_count"]),
                    "gap_count": int(row["gap_count"]),
                    "max_gap": int(row["max_gap"]),
                    "reasons": [item for item in row["reasons"].split(";") if item],
                    "class_counts": (
                        json.loads(row["class_counts"])
                        if "class_counts" in available_fields
                        else {}
                    ),
                    "mean_confidences": (
                        json.loads(row["mean_confidences"])
                        if "mean_confidences" in available_fields
                        else {}
                    ),
                    "supporting_frame_indexes": (
                        json.loads(row["supporting_frame_indexes"])
                        if "supporting_frame_indexes" in available_fields
                        else []
                    ),
                    "contradictory_frame_indexes": (
                        json.loads(row["contradictory_frame_indexes"])
                        if "contradictory_frame_indexes" in available_fields
                        else []
                    ),
                    "unclassified_frame_indexes": (
                        json.loads(row["unclassified_frame_indexes"])
                        if "unclassified_frame_indexes" in available_fields
                        else []
                    ),
                    "score_interpretation": (
                        row["score_interpretation"]
                        if "score_interpretation" in available_fields
                        else LEGACY_SCORE_INTERPRETATION
                    ),
                    "evidence_references_available": has_frame_references,
                }
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"Invalid temporal consensus values for track {track_id}"
                ) from error
            numeric_values = (
                result["dominant_class_ratio"],
                result["consensus_confidence"],
            )
            frame_references = (
                result["supporting_frame_indexes"],
                result["contradictory_frame_indexes"],
                result["unclassified_frame_indexes"],
            )
            if (
                not all(math.isfinite(value) and 0 <= value <= 1 for value in numeric_values)
                or any(
                    not isinstance(references, list)
                    or any(
                        isinstance(frame, bool)
                        or not isinstance(frame, int)
                        or frame < 0
                        for frame in references
                    )
                    for references in frame_references
                )
                or not isinstance(result["class_counts"], dict)
                or not isinstance(result["mean_confidences"], dict)
                or not result["score_interpretation"]
            ):
                raise ValueError(f"Invalid temporal consensus values for track {track_id}")
            results[track_id] = result
    return results
