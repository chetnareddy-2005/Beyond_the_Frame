"""Recompute SMOT4SB GT-box oracle ByteTrack metrics and retain frame ledger."""

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
PILOT = DATA / "pilot_baseline_eval"
SEQUENCES = ("0003", "0011", "0034", "0040")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    x1, y1 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    x2, y2 = min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a["x2"] - a["x1"]) * max(0.0, a["y2"] - a["y1"])
    area_b = max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def tracking_metrics(gt_by_frame, pred_by_frame, frame_count: int) -> dict[str, Any]:
    from scipy.optimize import linear_sum_assignment

    gt_total = sum(map(len, gt_by_frame.values()))
    pred_total = sum(map(len, pred_by_frame.values()))
    matches: list[tuple[int, str, str]] = []
    gt_matched: dict[str, set[int]] = defaultdict(set)
    gt_visible: dict[str, list[int]] = defaultdict(list)
    for frame in range(frame_count):
        gts, preds = gt_by_frame.get(frame, []), pred_by_frame.get(frame, [])
        for gt in gts:
            gt_visible[str(gt["track_id"])].append(frame)
        if not gts or not preds:
            continue
        overlaps = np.zeros((len(gts), len(preds)), dtype=np.float64)
        for gi, gt in enumerate(gts):
            for pi, pred in enumerate(preds):
                if str(gt["class_name"]).strip().lower() == str(pred["class_name"]).strip().lower():
                    overlaps[gi, pi] = iou(gt, pred)
        costs = np.where(overlaps >= 0.5, 1.0 - overlaps, 1_000_000.0)
        gt_indices, pred_indices = linear_sum_assignment(costs)
        for gi, pi in zip(gt_indices, pred_indices):
            if overlaps[gi, pi] >= 0.5:
                gt_id, pred_id = str(gts[gi]["track_id"]), str(preds[pi]["track_id"])
                matches.append((frame, gt_id, pred_id))
                gt_matched[gt_id].add(frame)

    gt_ids = sorted({gt_id for _, gt_id, _ in matches})
    pred_ids = sorted({pred_id for _, _, pred_id in matches})
    idtp = 0
    if gt_ids and pred_ids:
        counts = np.zeros((len(gt_ids), len(pred_ids)), dtype=np.int64)
        gi = {track_id: index for index, track_id in enumerate(gt_ids)}
        pi = {track_id: index for index, track_id in enumerate(pred_ids)}
        for _, gt_id, pred_id in matches:
            counts[gi[gt_id], pi[pred_id]] += 1
        rows, cols = linear_sum_assignment(-counts)
        idtp = int(counts[rows, cols].sum())
    idfn, idfp = gt_total - idtp, pred_total - idtp
    denom = 2 * idtp + idfn + idfp

    observations_by_gt: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for frame, gt_id, pred_id in matches:
        observations_by_gt[gt_id].append((frame, pred_id))
    switches = sum(
        int(previous_id != current_id)
        for observations in observations_by_gt.values()
        for (_, previous_id), (_, current_id) in zip(sorted(observations), sorted(observations)[1:])
    )
    fragments = 0
    for gt_id, visible in gt_visible.items():
        visible.sort()
        had_match = missed_after_match = False
        previous_frame = None
        for frame in visible:
            if previous_frame is not None and frame != previous_frame + 1:
                missed_after_match = False
            if frame in gt_matched.get(gt_id, set()):
                if had_match and missed_after_match:
                    fragments += 1
                had_match, missed_after_match = True, False
            elif had_match:
                missed_after_match = True
            previous_frame = frame
    matched = len(matches)
    return {
        "frames": frame_count,
        "ground_truth_detections": gt_total,
        "predicted_observations": pred_total,
        "matched_observations_iou50_class_aware": matched,
        "false_negatives": gt_total - matched,
        "false_positives": pred_total - matched,
        "idf1_iou50": 2 * idtp / denom if denom else 0.0,
        "idtp": idtp,
        "idfp": idfp,
        "idfn": idfn,
        "id_switches_between_consecutive_matched_observations": switches,
        "track_fragments": fragments,
        "predicted_track_count": len({str(row["track_id"]) for rows in pred_by_frame.values() for row in rows}),
        "gt_track_count": len(gt_visible),
        "metric_definition": "Class-aware per-frame Hungarian IoU match at 0.50; IDF1 uses global maximum-duration identity assignment. ID switches compare consecutive matched observations; fragments count a matched-to-unmatched-to-matched interruption while GT is visible.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output_path = args.output or (
        PILOT / f"phase7_oracle_tracking_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    sys.path.insert(0, str(ROOT))
    from backend.detection import Detection, DetectionResult
    from backend.tracking import TrackingService

    old = json.loads((PILOT / "oracle_tracker_baseline.json").read_text(encoding="utf-8"))
    frame_counts = {item["sequence"]: int(item["frames"]) for item in old["sequences"]}
    tracker = TrackingService()
    all_gt: dict[str, dict[int, list[dict[str, Any]]]] = {}
    all_pred: dict[str, dict[int, list[dict[str, Any]]]] = {}
    ledger: list[dict[str, Any]] = []
    per_sequence = {}

    for sequence in SEQUENCES:
        frame_count = frame_counts[sequence]
        gt_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in read_csv(DATA / f"SMOT4SB_sequence_{sequence}_gt.csv"):
            frame_index = int(row["frame_id"]) - 1
            x, y = float(row["x"]), float(row["y"])
            gt_by_frame[frame_index].append({
                "sequence": sequence,
                "frame_index": frame_index,
                "track_id": str(row["track_id"]),
                "class_name": row.get("class", "bird"),
                "x1": x,
                "y1": y,
                "x2": x + float(row["width"]),
                "y2": y + float(row["height"]),
            })
        pred_by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
        tracker.reset_owner("phase7-oracle")
        for frame_index in range(frame_count):
            detections = tuple(
                Detection(
                    frame_index=frame_index,
                    timestamp_sec=None,
                    source_type="upload",
                    class_id=0,
                    class_name="bird",
                    confidence=1.0,
                    x1=row["x1"], y1=row["y1"], x2=row["x2"], y2=row["y2"],
                )
                for row in gt_by_frame.get(frame_index, [])
            )
            result = DetectionResult(
                source_type="upload", input_type="rgb", run_id=f"phase7-oracle-{sequence}",
                live_reference_id=None, frame_index=frame_index, timestamp_sec=None,
                width=3840, height=2160, model_name="ground-truth-oracle",
                inference_time_ms=0.0, detections=detections,
            )
            tracked = tracker.track("phase7-oracle", result)
            for observation in tracked.tracks:
                row = observation.as_dict()
                row.update(sequence=sequence, frame_index=frame_index)
                pred_by_frame[frame_index].append(row)
                ledger.append(row)

        all_gt[sequence], all_pred[sequence] = gt_by_frame, pred_by_frame
        per_sequence[sequence] = tracking_metrics(gt_by_frame, pred_by_frame, frame_count)

    # Merge clips with sequence-scoped IDs and frame indexes to avoid identity
    # or frame collisions. The pilot sequences are separate videos.
    gt_combined: dict[int, list[dict[str, Any]]] = defaultdict(list)
    pred_combined: dict[int, list[dict[str, Any]]] = defaultdict(list)
    offset = 0
    for sequence in SEQUENCES:
        for frame, rows in all_gt[sequence].items():
            gt_combined[offset + frame].extend({**r, "track_id": f"{sequence}:{r['track_id']}"} for r in rows)
        for frame, rows in all_pred[sequence].items():
            pred_combined[offset + frame].extend({**r, "track_id": f"{sequence}:{r['track_id']}"} for r in rows)
        offset += frame_counts[sequence]
    aggregate = tracking_metrics(gt_combined, pred_combined, offset)

    output = output_path.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fields = ["sequence", "frame_index", "track_id", "class_id", "class_name", "confidence", "x1", "y1", "x2", "y2"]
    with (output / "oracle_track_observations.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ledger)
    summary = {
        "evaluation": "GT boxes fed to the production ByteTrack adapter; tracker-only oracle diagnostic",
        "source_data": "SMOT4SB initial training-pilot sequences 0003, 0011, 0034, 0040; development-only",
        "iou_threshold": 0.5,
        "class_aware": True,
        "idf1_method": "Per-frame Hungarian IoU matching then global maximum-duration identity assignment",
        "tracker_source_sha256": sha256(ROOT / "backend" / "tracking.py"),
        "supervision_version": __import__("supervision").__version__,
        "inputs": {
            sequence: {
                "ground_truth": f"datasets/processed/phase2/SMOT4SB_sequence_{sequence}_gt.csv",
                "ground_truth_sha256": sha256(DATA / f"SMOT4SB_sequence_{sequence}_gt.csv"),
                "frames": frame_counts[sequence],
            }
            for sequence in SEQUENCES
        },
        "per_sequence": per_sequence,
        "aggregate": aggregate,
        "limitations": [
            "The four sequences are from the training partition and are not an independent benchmark.",
            "This oracle excludes detector misses; it must be interpreted separately from detector-plus-tracker results.",
            "V2 multi-drone acceptance remains pending because no eligible labeled V2 video is available.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "aggregate": aggregate, "per_sequence": per_sequence}, indent=2))


if __name__ == "__main__":
    main()
