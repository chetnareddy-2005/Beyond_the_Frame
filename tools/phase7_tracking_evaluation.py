"""Reproducible Phase 7 detector+ByteTrack vs GT-box oracle evaluation.

Uses the four locally available SMOT4SB training-pilot sequences. These are
development data, not an independent benchmark. The script does not modify
source videos, annotations, checkpoints, or production tracker settings.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "processed" / "phase2"
CHECKPOINT = ROOT / "artifacts" / "checkpoints" / "best.pt"
SEQUENCES = ("0003", "0011", "0034", "0040")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def box_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    left, top = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    right, bottom = min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, a["x2"] - a["x1"]) * max(0.0, a["y2"] - a["y1"])
    area_b = max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def match_frame(
    ground_truth: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float
) -> list[tuple[int, int]]:
    if not ground_truth or not predictions:
        return []
    from scipy.optimize import linear_sum_assignment

    overlaps = np.zeros((len(ground_truth), len(predictions)), dtype=np.float64)
    for gi, gt in enumerate(ground_truth):
        for pi, pred in enumerate(predictions):
            if str(gt["class_name"]).strip().lower() == str(pred["class_name"]).strip().lower():
                overlaps[gi, pi] = box_iou(gt, pred)
    # Invalid edges are more costly than every valid IoU edge. Assignment is
    # one-to-one within each frame, as required for detection/identity metrics.
    cost = np.where(overlaps >= threshold, 1.0 - overlaps, 1_000_000.0)
    gt_indices, pred_indices = linear_sum_assignment(cost)
    return [
        (int(gi), int(pi))
        for gi, pi in zip(gt_indices, pred_indices)
        if overlaps[gi, pi] >= threshold
    ]


def tracking_metrics(
    gt_by_frame: dict[int, list[dict[str, Any]]],
    pred_by_frame: dict[int, list[dict[str, Any]]],
    frame_count: int,
    threshold: float = 0.5,
) -> dict[str, Any]:
    gt_total = sum(len(values) for values in gt_by_frame.values())
    pred_total = sum(len(values) for values in pred_by_frame.values())
    matched_pairs: list[tuple[int, str, str]] = []
    matched_gt_frames: dict[str, set[int]] = defaultdict(set)
    gt_seen_frames: dict[str, list[int]] = defaultdict(list)
    for frame_index in range(frame_count):
        gts = gt_by_frame.get(frame_index, [])
        preds = pred_by_frame.get(frame_index, [])
        for gt in gts:
            gt_seen_frames[str(gt["track_id"])].append(frame_index)
        for gi, pi in match_frame(gts, preds, threshold):
            gt_id, pred_id = str(gts[gi]["track_id"]), str(preds[pi]["track_id"])
            matched_pairs.append((frame_index, gt_id, pred_id))
            matched_gt_frames[gt_id].add(frame_index)

    # IDF1: first perform one-to-one IoU matching per frame, then globally
    # assign predicted identities to GT identities by maximum matched duration.
    gt_ids = sorted({pair[1] for pair in matched_pairs})
    pred_ids = sorted({pair[2] for pair in matched_pairs})
    idtp = 0
    if gt_ids and pred_ids:
        from scipy.optimize import linear_sum_assignment

        gt_index = {track_id: index for index, track_id in enumerate(gt_ids)}
        pred_index = {track_id: index for index, track_id in enumerate(pred_ids)}
        counts = np.zeros((len(gt_ids), len(pred_ids)), dtype=np.int64)
        for _, gt_id, pred_id in matched_pairs:
            counts[gt_index[gt_id], pred_index[pred_id]] += 1
        row_indices, col_indices = linear_sum_assignment(-counts)
        idtp = int(counts[row_indices, col_indices].sum())
    idfn, idfp = gt_total - idtp, pred_total - idtp
    idf1_denominator = 2 * idtp + idfn + idfp

    # ID switches compare consecutive matched observations for the same GT ID;
    # gaps are counted separately and do not erase switch history.
    matches_by_gt: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for frame_index, gt_id, pred_id in matched_pairs:
        matches_by_gt[gt_id].append((frame_index, pred_id))
    id_switches = 0
    for observations in matches_by_gt.values():
        observations.sort()
        id_switches += sum(
            previous_id != current_id
            for (_, previous_id), (_, current_id) in zip(observations, observations[1:])
        )

    # Fragment counts are interruptions between matched portions of a visible
    # GT trajectory. Unmatched leading/trailing portions are not fragments.
    fragments = 0
    for gt_id, visible_frames in gt_seen_frames.items():
        visible_frames.sort()
        matched = matched_gt_frames.get(gt_id, set())
        had_match = False
        currently_unmatched_after_match = False
        previous_frame: int | None = None
        for frame_index in visible_frames:
            if previous_frame is not None and frame_index != previous_frame + 1:
                # The GT itself was absent; this is not a fragmentation event.
                currently_unmatched_after_match = False
            if frame_index in matched:
                if had_match and currently_unmatched_after_match:
                    fragments += 1
                had_match = True
                currently_unmatched_after_match = False
            elif had_match:
                currently_unmatched_after_match = True
            previous_frame = frame_index

    fn = gt_total - len(matched_pairs)
    fp = pred_total - len(matched_pairs)
    return {
        "frames": frame_count,
        "ground_truth_detections": gt_total,
        "predicted_observations": pred_total,
        "matched_observations_iou50_class_aware": len(matched_pairs),
        "false_negatives": fn,
        "false_positives": fp,
        "detection_precision_iou50": len(matched_pairs) / pred_total if pred_total else 0.0,
        "detection_recall_iou50": len(matched_pairs) / gt_total if gt_total else 0.0,
        "idf1_iou50": 2 * idtp / idf1_denominator if idf1_denominator else 0.0,
        "idtp": idtp,
        "idfp": idfp,
        "idfn": idfn,
        "id_switches_between_consecutive_matched_observations": id_switches,
        "track_fragments": fragments,
        "predicted_track_count": len({str(row["track_id"]) for rows in pred_by_frame.values() for row in rows}),
        "gt_track_count": len(gt_seen_frames),
        "gt_observation_coverage": len(matched_pairs) / gt_total if gt_total else 0.0,
        "metric_definition": "Class-aware framewise Hungarian IoU matching at 0.50; IDF1 uses global maximum-duration GT/predicted identity assignment.",
    }


def to_gt_row(sequence: str, row: dict[str, str]) -> dict[str, Any]:
    x, y = float(row["x"]), float(row["y"])
    width, height = float(row["width"]), float(row["height"])
    return {
        "sequence": sequence,
        "frame_index": int(row["frame_id"]) - 1,
        "frame_id_1based": int(row["frame_id"]),
        "track_id": str(row["track_id"]),
        "class_name": row.get("class", "Bird"),
        "confidence": 1.0,
        "x1": x,
        "y1": y,
        "x2": x + width,
        "y2": y + height,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "experiments" / "evaluation" / "phase7_tracking_2026-10-23",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output already exists; choose a new --output path: {output}")
    if not CHECKPOINT.is_file():
        raise SystemExit(f"Frozen project checkpoint is missing: {CHECKPOINT}")

    video_paths = {seq: DATA / f"SMOT4SB_sequence_{seq}.mp4" for seq in SEQUENCES}
    gt_paths = {seq: DATA / f"SMOT4SB_sequence_{seq}_gt.csv" for seq in SEQUENCES}
    missing = [str(p) for p in [*video_paths.values(), *gt_paths.values()] if not p.is_file()]
    if missing:
        raise SystemExit("Required pilot inputs are missing:\n" + "\n".join(missing))

    import cv2

    sys.path.insert(0, str(ROOT))
    from backend.detection import Detection, DetectionResult, DetectionService
    from backend.frames import PreparedFrame
    from backend.tracking import TrackingService

    detection_service = DetectionService(ROOT)
    detected_tracker = TrackingService()
    oracle_tracker = TrackingService()
    actual_det_by_seq: dict[str, dict[int, list[dict[str, Any]]]] = {}
    actual_track_by_seq: dict[str, dict[int, list[dict[str, Any]]]] = {}
    oracle_track_by_seq: dict[str, dict[int, list[dict[str, Any]]]] = {}
    gt_by_seq: dict[str, dict[int, list[dict[str, Any]]]] = {}
    source_records: dict[str, Any] = {}
    raw_gt_rows: list[dict[str, Any]] = []
    detector_rows: list[dict[str, Any]] = []
    detector_track_rows: list[dict[str, Any]] = []
    oracle_track_rows: list[dict[str, Any]] = []
    per_sequence: dict[str, Any] = {}

    for sequence in SEQUENCES:
        video_path, gt_path = video_paths[sequence], gt_paths[sequence]
        gt_rows = [to_gt_row(sequence, row) for row in read_rows(gt_path)]
        gt_frames: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in gt_rows:
            gt_frames[row["frame_index"]].append(row)
        gt_by_seq[sequence] = gt_frames
        raw_gt_rows.extend(gt_rows)

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise SystemExit(f"Could not open video: {video_path}")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if frame_count <= 0 or any(index >= frame_count for index in gt_frames):
            capture.release()
            raise SystemExit(f"GT/video frame mismatch for sequence {sequence}")
        actual_det_by_seq[sequence] = defaultdict(list)
        actual_track_by_seq[sequence] = defaultdict(list)
        oracle_track_by_seq[sequence] = defaultdict(list)
        detected_tracker.reset_owner("phase7-detected")
        oracle_tracker.reset_owner("phase7-oracle")
        decoded = 0

        for frame_index in range(frame_count):
            ok, image = capture.read()
            if not ok or image is None:
                capture.release()
                raise SystemExit(f"Decode failed for sequence {sequence} frame {frame_index}")
            prepared = PreparedFrame(
                frame=image,
                source_type="upload",
                input_type="rgb",
                run_id=f"phase7-{sequence}",
                live_reference_id=None,
                frame_index=frame_index,
                timestamp_sec=None,
                width=width,
                height=height,
                channels=3,
                dtype=str(image.dtype),
                validation_status="valid",
                preprocessing_status="phase7_evaluation",
            )
            detections, _ = detection_service.detect(prepared)
            detected_tracks = detected_tracker.track("phase7-detected", detections)
            for index, detection in enumerate(detections.detections):
                det_row = {
                    "sequence": sequence,
                    "frame_index": frame_index,
                    "detection_index": index,
                    "track_id": "",
                    "class_name": detection.class_name,
                    "confidence": detection.confidence,
                    "x1": detection.x1,
                    "y1": detection.y1,
                    "x2": detection.x2,
                    "y2": detection.y2,
                }
                actual_det_by_seq[sequence][frame_index].append(det_row)
                detector_rows.append(det_row)
            for track in detected_tracks.tracks:
                row = track.as_dict()
                row.update(sequence=sequence, frame_index=frame_index)
                actual_track_by_seq[sequence][frame_index].append(row)
                detector_track_rows.append(row)

            oracle_detections = tuple(
                Detection(
                    frame_index=frame_index,
                    timestamp_sec=None,
                    source_type="upload",
                    class_id=0,
                    class_name="bird",
                    confidence=1.0,
                    x1=gt["x1"], y1=gt["y1"], x2=gt["x2"], y2=gt["y2"],
                )
                for gt in gt_frames.get(frame_index, [])
            )
            oracle_result = DetectionResult(
                source_type="upload", input_type="rgb", run_id=f"phase7-oracle-{sequence}",
                live_reference_id=None, frame_index=frame_index, timestamp_sec=None,
                width=width, height=height, model_name="ground-truth-oracle",
                inference_time_ms=0.0, detections=oracle_detections,
            )
            oracle_tracks = oracle_tracker.track("phase7-oracle", oracle_result)
            for track in oracle_tracks.tracks:
                row = track.as_dict()
                row.update(sequence=sequence, frame_index=frame_index)
                oracle_track_by_seq[sequence][frame_index].append(row)
                oracle_track_rows.append(row)

            decoded += 1
            if decoded % 100 == 0:
                print(f"Sequence {sequence}: {decoded}/{frame_count} frames", flush=True)

        capture.release()
        source_records[sequence] = {
            "video": str(video_path.relative_to(ROOT)).replace("\\", "/"),
            "video_sha256": sha256(video_path),
            "ground_truth": str(gt_path.relative_to(ROOT)).replace("\\", "/"),
            "ground_truth_sha256": sha256(gt_path),
            "frames": frame_count,
            "width": width,
            "height": height,
            "gt_detections": len(gt_rows),
            "gt_track_ids": len({row["track_id"] for row in gt_rows}),
            "dataset_partition": "SMOT4SB initial training pilot; development-only",
        }
        per_sequence[sequence] = {
            "detector_only": tracking_metrics(gt_frames, actual_det_by_seq[sequence], frame_count),
            "detector_plus_bytetrack": tracking_metrics(gt_frames, actual_track_by_seq[sequence], frame_count),
            "gt_box_oracle_bytetrack": tracking_metrics(gt_frames, oracle_track_by_seq[sequence], frame_count),
        }
        print(f"Sequence {sequence}: complete ({frame_count} frames)", flush=True)

    total_frames = sum(item["frames"] for item in source_records.values())
    gt_total = sum(item["gt_detections"] for item in source_records.values())
    combined_gt = defaultdict(list)
    combined_detection = defaultdict(list)
    combined_detection_track = defaultdict(list)
    combined_oracle = defaultdict(list)
    # Prefix identities by sequence so sequence-local IDs are not conflated.
    for sequence in SEQUENCES:
        for frame, rows in gt_by_seq[sequence].items():
            combined_gt[(sequence, frame)].extend({**row, "track_id": f"{sequence}:{row['track_id']}"} for row in rows)
        for frame, rows in actual_det_by_seq[sequence].items():
            combined_detection[(sequence, frame)].extend({**row, "track_id": f"{sequence}:det:{row['detection_index']}"} for row in rows)
        for frame, rows in actual_track_by_seq[sequence].items():
            combined_detection_track[(sequence, frame)].extend({**row, "track_id": f"{sequence}:pred:{row['track_id']}"} for row in rows)
        for frame, rows in oracle_track_by_seq[sequence].items():
            combined_oracle[(sequence, frame)].extend({**row, "track_id": f"{sequence}:oracle:{row['track_id']}"} for row in rows)

    def flatten_frame_scope(source: dict[tuple[str, int], list[dict[str, Any]]]):
        flattened: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for (sequence, frame), rows in source.items():
            # A large sequence-specific offset prevents collisions across clips.
            flattened[SEQUENCES.index(sequence) * 1_000_000 + frame].extend(rows)
        return flattened

    scopes = total_frames * len(SEQUENCES)
    aggregate = {
        "detector_only": tracking_metrics(flatten_frame_scope(combined_gt), flatten_frame_scope(combined_detection), scopes),
        "detector_plus_bytetrack": tracking_metrics(flatten_frame_scope(combined_gt), flatten_frame_scope(combined_detection_track), scopes),
        "gt_box_oracle_bytetrack": tracking_metrics(flatten_frame_scope(combined_gt), flatten_frame_scope(combined_oracle), scopes),
    }

    output.mkdir(parents=True, exist_ok=False)
    write_rows(output / "ground_truth_observations.csv", raw_gt_rows,
               ["sequence", "frame_index", "frame_id_1based", "track_id", "class_name", "confidence", "x1", "y1", "x2", "y2"])
    write_rows(output / "detector_observations.csv", detector_rows,
               ["sequence", "frame_index", "detection_index", "track_id", "class_name", "confidence", "x1", "y1", "x2", "y2"])
    write_rows(output / "detector_bytetrack_observations.csv", detector_track_rows,
               ["sequence", "frame_index", "track_id", "class_id", "class_name", "confidence", "x1", "y1", "x2", "y2"])
    write_rows(output / "oracle_bytetrack_observations.csv", oracle_track_rows,
               ["sequence", "frame_index", "track_id", "class_id", "class_name", "confidence", "x1", "y1", "x2", "y2"])

    report = {
        "phase": 7,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "development_evaluation_complete_v2_pending",
        "scope": "Four SMOT4SB initial-train pilot sequences; not an independent test benchmark.",
        "tracker": "Project TrackingService using supervision.ByteTrack defaults.",
        "detector": {
            "checkpoint": str(CHECKPOINT.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(CHECKPOINT),
            "inference_size": 640,
            "confidence_threshold": 0.25,
            "nms_iou_threshold": 0.45,
            "device": "resolved by project backend",
        },
        "matching": "Class-aware Hungarian IoU matching at 0.50; IDF1 globally assigns identities by maximum matched duration. ID switches compare consecutive matched observations; fragments are visible-track unmatched-to-matched interruptions.",
        "sources": source_records,
        "aggregate": aggregate,
        "sequences": per_sequence,
        "limits": [
            "All four sequences belong to the SMOT4SB initial training partition and are development-only.",
            "The pilot contains birds only; no eligible V2 multi-drone evaluation clip is available.",
            "The GT-box oracle isolates the tracker; detector-driven results include detector errors.",
            "These metrics do not establish independent-scene generalization or acceptance of V1/V2/V3.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "frames": total_frames, "gt_detections": gt_total, "aggregate": aggregate}, indent=2))


if __name__ == "__main__":
    main()
