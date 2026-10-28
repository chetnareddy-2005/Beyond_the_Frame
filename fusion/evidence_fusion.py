"""Fuse per-track detector evidence with optional VLM evidence."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

OUTPUT_FIELDS = (
    "track_id",
    "detector_class",
    "detector_score",
    "reliability_score",
    "vlm_label",
    "vlm_confidence",
    "fusion_mode",
    "final_label",
    "decision_confidence",
    "frames_seen",
    "status",
)
DETECTOR_LABELS = {"drone": "UAV", "bird": "Bird"}
VLM_LABELS = {"drone": "UAV", "bird": "Bird", "other": "Other"}
ALLOWED_VLM_LABELS = {"Bird", "Drone", "Other", "Unknown"}


def _read_csv(path, required_fields):
    with Path(path).open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        missing = set(required_fields) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
        return list(reader)


def _track_key(value):
    try:
        return str(int(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid track_id: {value!r}") from error


def _confidence(value, field_name):
    try:
        confidence = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {field_name}: {value!r}") from error
    if not 0 <= confidence <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1: {value!r}")
    return confidence


def _mean(values):
    return sum(values) / len(values)


def _normalize_detector_scores(class_confidences):
    means = {name: _mean(values) for name, values in class_confidences.items()}
    if len(means) == 1:
        return means
    total = sum(means.values())
    if total <= 0:
        raise ValueError("Detector class evidence must have a positive score sum")
    return {name: score / total for name, score in means.items()}


def fuse_track(detector_scores, reliability_score, vlm_scores=None):
    """Return a final decision from class evidence and an existing trust score."""
    if not 0 <= reliability_score <= 1:
        raise ValueError("reliability_score must be between 0 and 1")
    vlm_scores = vlm_scores or {}

    if not vlm_scores:
        final_label = max(sorted(detector_scores), key=detector_scores.get)
        decision_confidence = max(
            0.0, min(1.0, reliability_score * detector_scores[final_label])
        )
        return final_label, decision_confidence, "detector_temporal"

    classes = set(detector_scores) | set(vlm_scores)
    fused_scores = {
        label: reliability_score * detector_scores.get(label, 0.0)
        + (1.0 - reliability_score) * vlm_scores.get(label, 0.0)
        for label in classes
    }
    final_label = max(sorted(fused_scores), key=fused_scores.get)
    decision_confidence = max(0.0, min(1.0, fused_scores[final_label]))
    return final_label, decision_confidence, "detector_vlm"


def fuse_evidence(
    tracks_csv,
    temporal_csv,
    reliability_csv,
    vlm_csv=None,
    output_csv=None,
):
    """Join existing phase CSVs and write one fused decision per detector track."""
    tracks = _read_csv(tracks_csv, ("track_id", "class_name", "confidence"))
    temporal_rows = _read_csv(temporal_csv, ("track_id", "frames_seen"))
    reliability_rows = _read_csv(
        reliability_csv, ("track_id", "reliability_score", "status")
    )

    detector_evidence = defaultdict(lambda: defaultdict(list))
    for row in tracks:
        track_id = _track_key(row["track_id"])
        class_key = row["class_name"].strip().casefold()
        if class_key not in DETECTOR_LABELS:
            raise ValueError(f"Unsupported detector class: {row['class_name']!r}")
        label = DETECTOR_LABELS[class_key]
        detector_evidence[track_id][label].append(
            _confidence(row["confidence"], "confidence")
        )
    if not detector_evidence:
        raise ValueError("tracks.csv contains no detector observations")

    temporal_by_track = {}
    for row in temporal_rows:
        track_id = _track_key(row["track_id"])
        if track_id in temporal_by_track:
            raise ValueError(f"Duplicate temporal track_id: {track_id}")
        temporal_by_track[track_id] = row

    reliability_by_track = {}
    for row in reliability_rows:
        track_id = _track_key(row["track_id"])
        if track_id in reliability_by_track:
            raise ValueError(f"Duplicate reliability track_id: {track_id}")
        reliability_by_track[track_id] = row

    detector_tracks = set(detector_evidence)
    for phase_name, phase_tracks in (
        ("temporal", set(temporal_by_track)),
        ("reliability", set(reliability_by_track)),
    ):
        if detector_tracks != phase_tracks:
            raise ValueError(
                f"{phase_name} track IDs do not match detector tracks"
            )

    vlm_evidence = defaultdict(lambda: defaultdict(list))
    vlm_unknown = defaultdict(list)
    if vlm_csv is not None:
        for row in _read_csv(vlm_csv, ("track_id", "vlm_label", "vlm_confidence")):
            track_id = _track_key(row["track_id"])
            label = row["vlm_label"].strip()
            if label not in ALLOWED_VLM_LABELS:
                raise ValueError(f"Unsupported VLM label: {label!r}")
            confidence = _confidence(row["vlm_confidence"], "vlm_confidence")
            if label == "Unknown":
                vlm_unknown[track_id].append(confidence)
            else:
                vlm_evidence[track_id][VLM_LABELS[label.casefold()]].append(confidence)

    rows = []
    for track_id in sorted(detector_evidence, key=int):
        if track_id not in temporal_by_track:
            raise ValueError(f"No temporal feature row for track {track_id}")
        if track_id not in reliability_by_track:
            raise ValueError(f"No reliability row for track {track_id}")

        reliability = reliability_by_track[track_id]
        reliability_score = _confidence(
            reliability["reliability_score"], "reliability_score"
        )
        detector_scores = _normalize_detector_scores(detector_evidence[track_id])
        semantic_scores = {
            label: _mean(values)
            for label, values in vlm_evidence[track_id].items()
        }
        final_label, decision_confidence, fusion_mode = fuse_track(
            detector_scores, reliability_score, semantic_scores
        )

        detector_label = max(sorted(detector_scores), key=detector_scores.get)
        detector_class = {"UAV": "Drone", "Bird": "Bird"}[detector_label]
        detector_score = detector_scores[detector_label]
        if semantic_scores:
            vlm_final_label = max(sorted(semantic_scores), key=semantic_scores.get)
            vlm_label = {
                "UAV": "Drone",
                "Bird": "Bird",
                "Other": "Other",
            }[vlm_final_label]
            vlm_confidence = semantic_scores[vlm_final_label]
        elif vlm_unknown[track_id]:
            vlm_label = "Unknown"
            vlm_confidence = _mean(vlm_unknown[track_id])
        else:
            vlm_label = ""
            vlm_confidence = ""

        rows.append(
            {
                "track_id": track_id,
                "detector_class": detector_class,
                "detector_score": detector_score,
                "reliability_score": reliability_score,
                "vlm_label": vlm_label,
                "vlm_confidence": vlm_confidence,
                "fusion_mode": fusion_mode,
                "final_label": final_label,
                "decision_confidence": decision_confidence,
                "frames_seen": temporal_by_track[track_id]["frames_seen"],
                "status": reliability["status"],
            }
        )

    if vlm_csv is not None:
        unmatched_vlm = (set(vlm_evidence) | set(vlm_unknown)) - set(detector_evidence)
        if unmatched_vlm:
            raise ValueError(
                "VLM evidence has no matching detector track: "
                + ", ".join(sorted(unmatched_vlm, key=int))
            )

    output_csv = (
        Path(output_csv)
        if output_csv is not None
        else Path(tracks_csv).with_name("fusion_results.csv")
    )
    with output_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracks_csv", help="Phase 2 tracks.csv")
    parser.add_argument("temporal_csv", help="Phase 3 temporal_features.csv")
    parser.add_argument("reliability_csv", help="Phase 4 reliability_results.csv")
    parser.add_argument("--vlm", dest="vlm_csv", help="optional Phase 5 vlm_results.csv")
    parser.add_argument("--output", help="fusion_results.csv output path")
    args = parser.parse_args()

    results = fuse_evidence(
        args.tracks_csv,
        args.temporal_csv,
        args.reliability_csv,
        vlm_csv=args.vlm_csv,
        output_csv=args.output,
    )
    print(f"Wrote fused decisions for {len(results)} tracks.")


if __name__ == "__main__":
    main()
