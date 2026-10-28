"""Reproduce a frozen RGB detector baseline on Phase 4 signed sample frames.

The script reads, but never edits, source videos, MATLAB labels, or review CSVs.
It writes derived ledgers/reports to datasets/processed/phase5_detection_baseline_2026-10-22/.
Requires the project runtime plus the documented optional ``mcos-decoder`` package.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "datasets/processed/phase5_detection_baseline_2026-10-22"
REVIEW = ROOT / "datasets/processed/phase4_quick_audit/phase4_sample_review_signed.csv"
CROSSWALK = ROOT / "datasets/processed/phase4_quick_audit/phase4_frame_annotation_crosswalk.csv"
MANIFEST = ROOT / "datasets/processed/phase4_quick_audit/phase4_pilot_manifest.json"
CHECKPOINT = ROOT / "artifacts/checkpoints/best.pt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    x1, y1 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"])
    x2, y2 = min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a["x2"] - a["x1"]) * max(0.0, a["y2"] - a["y1"])
    area_b = max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def greedy_matches(
    ground_truth: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float, class_aware: bool
) -> list[tuple[int, int, float]]:
    candidates = []
    for gi, gt in enumerate(ground_truth):
        for pi, pred in enumerate(predictions):
            if class_aware and gt["class_name"] != pred["class_name"]:
                continue
            overlap = iou(gt, pred)
            if overlap >= threshold:
                candidates.append((overlap, gi, pi))
    candidates.sort(reverse=True)
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches = []
    for overlap, gi, pi in candidates:
        if gi not in used_gt and pi not in used_pred:
            used_gt.add(gi)
            used_pred.add(pi)
            matches.append((gi, pi, overlap))
    return matches


def average_precision(frame_records: dict[str, dict[str, Any]], class_name: str, threshold: float) -> dict[str, Any]:
    gt_total = sum(sum(gt["class_name"] == class_name for gt in entry["gt"]) for entry in frame_records.values())
    if gt_total == 0:
        return {"gt": 0, "predictions": 0, "ap": None}
    ranked = [(pred["confidence"], frame_key, pred) for frame_key, entry in frame_records.items() for pred in entry["pred_ap"] if pred["class_name"] == class_name]
    ranked.sort(key=lambda item: item[0], reverse=True)
    used: dict[str, set[int]] = defaultdict(set)
    cumulative_tp = cumulative_fp = 0
    points: list[tuple[float, float]] = []
    for _, frame_key, pred in ranked:
        available = [(iou(gt, pred), index) for index, gt in enumerate(frame_records[frame_key]["gt"]) if gt["class_name"] == class_name and index not in used[frame_key]]
        best_iou, best_index = max(available, default=(0.0, -1))
        if best_index >= 0 and best_iou >= threshold:
            used[frame_key].add(best_index)
            cumulative_tp += 1
        else:
            cumulative_fp += 1
        points.append((cumulative_tp / gt_total, cumulative_tp / (cumulative_tp + cumulative_fp)))
    ap = sum(max((precision for recall, precision in points if recall >= level / 100), default=0.0) for level in range(101)) / 101
    return {"gt": gt_total, "predictions": len(ranked), "ap": ap}


def size_recall(frame_records: dict[str, dict[str, Any]], class_name: str, threshold: float) -> dict[str, Any]:
    buckets: dict[str, list[bool]] = {"small_le_32px_side": [], "medium_32_to_96px_side": [], "large_gt_96px_side": []}
    for entry in frame_records.values():
        gt_class = [gt for gt in entry["gt"] if gt["class_name"] == class_name]
        pred_class = [pred for pred in entry["pred"] if pred["class_name"] == class_name]
        matched = {gi for gi, _, _ in greedy_matches(gt_class, pred_class, threshold, True)}
        for index, gt in enumerate(gt_class):
            side = max(gt["x2"] - gt["x1"], gt["y2"] - gt["y1"])
            bucket = "small_le_32px_side" if side <= 32 else "medium_32_to_96px_side" if side <= 96 else "large_gt_96px_side"
            buckets[bucket].append(index in matched)
    return {name: {"gt": len(values), "matched": sum(values), "recall": sum(values) / len(values) if values else None} for name, values in buckets.items()}


def main() -> None:
    try:
        from mcos_decoder import load_groundtruth
    except ImportError as error:
        raise SystemExit("Install the optional documented label reader first: python -m pip install mcos-decoder") from error

    import cv2
    sys.path.insert(0, str(ROOT))
    from backend.detection import DetectionService
    from backend.frames import PreparedFrame

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    audit_validation = json.loads((ROOT / "datasets/processed/phase4_quick_audit/phase4_audit_validation.json").read_text(encoding="utf-8"))
    verified_hashes = {item["path"]: item["sha256"] for item in audit_validation["verified_source_and_evidence_hashes"]}
    signed_rows = read_csv(REVIEW)
    crosswalk_rows = read_csv(CROSSWALK)
    accepted = {
        (row["case_id"], int(row["video_frame_1based"]))
        for row in signed_rows
        if row["modality"] == "RGB" and row["review_status"] == "HUMAN_REVIEWED" and row["review_decision"] == "ACCEPT"
    }
    crosswalk = {
        (row["case_id"], int(row["video_frame_1based"])): row
        for row in crosswalk_rows
        if not row["case_id"].startswith("THERMAL_") and row["review_decision"] == "ACCEPT" and row["qualified_human_signoff"] == "RECORDED"
    }
    if accepted != set(crosswalk):
        raise SystemExit("Accepted RGB sample rows and signed crosswalk do not match exactly")
    clips = {row["case_id"]: row for row in manifest["clips"]}
    for row in manifest["clips"]:
        if row["case_id"].startswith("V3_SMOT4SB_"):
            clips[row["case_id"].removeprefix("V3_")] = row
    selected = sorted(accepted)
    output_frame_rows: list[dict[str, Any]] = []
    ground_truth_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    ap_prediction_rows: list[dict[str, Any]] = []
    frame_records: dict[str, dict[str, Any]] = {}
    overlay_paths: list[Path] = []

    detector = DetectionService(ROOT)
    try:
        for case_id, frame_1based in selected:
            if case_id.startswith("SMOT4SB_") and case_id not in clips:
                sequence_id = case_id.removeprefix("SMOT4SB_")
                clip = {"scenario": "multiple-bird flock development diagnostic", "video": f"datasets/processed/phase2/SMOT4SB_sequence_{sequence_id}.mp4"}
            else:
                clip = clips[case_id]
            video_path = ROOT / clip["video"]
            actual_video_hash = sha256(video_path)
            expected_video_hash = clip.get("video_sha256") or verified_hashes.get(clip["video"])
            if not expected_video_hash or actual_video_hash != expected_video_hash:
                raise SystemExit(f"Video hash changed since Phase 4 sign-off: {clip['video']}")
            video = cv2.VideoCapture(str(video_path))
            if not video.isOpened():
                raise SystemExit(f"Cannot open signed RGB video: {clip['video']}")
            video.set(cv2.CAP_PROP_POS_FRAMES, frame_1based - 1)
            ok, image = video.read()
            video.release()
            if not ok or image is None:
                raise SystemExit(f"Cannot decode frame {frame_1based} from {clip['video']}")
            height, width = image.shape[:2]
            cross = crosswalk[(case_id, frame_1based)]
            gt_boxes: list[dict[str, Any]] = []
            gt_path_rel = cross["annotation_source"]
            gt_path = ROOT / gt_path_rel
            if case_id.startswith("SMOT4SB"):
                source_csv = ROOT / cross["annotation_source"]
                rows = [
                    row for row in read_csv(source_csv)
                    if int(row["frame_id"]) == int(cross["annotation_frame_or_slot"])
                ]
                for row in rows:
                    x, y, w, h = (float(row[key]) for key in ("x", "y", "width", "height"))
                    gt_boxes.append({"class_name": "Bird", "class_id": 0, "x1": x, "y1": y, "x2": x + w, "y2": y + h, "source_track_id": row.get("source_track_id", "")})
                gt_hash = sha256(source_csv)
                if gt_hash != verified_hashes.get(gt_path_rel):
                    raise SystemExit(f"SMOT4SB label hash changed since Phase 4 sign-off: {gt_path_rel}")
            else:
                expected_hash = clip["annotation_sha256"]
                if sha256(gt_path) != expected_hash:
                    raise SystemExit(f"MAT label hash changed since Phase 4 sign-off: {gt_path_rel}")
                label_slot = int(cross["annotation_frame_or_slot"])
                bboxes = load_groundtruth(gt_path)
                if len(bboxes) != int(clip["label_slots"]):
                    raise SystemExit(f"Decoder label-slot count mismatch for {case_id}: {len(bboxes)}")
                bbox = bboxes[label_slot - 1]
                if bbox is not None:
                    x, y, w, h = (float(value) for value in bbox)
                    gt_boxes.append({"class_name": "Bird" if "BIRD" in case_id else "Drone", "class_id": 0 if "BIRD" in case_id else 1, "x1": x, "y1": y, "x2": x + w, "y2": y + h, "source_track_id": "single_target_annotation"})
                gt_hash = expected_hash
            for gt in gt_boxes:
                if not all(math.isfinite(gt[key]) for key in ("x1", "y1", "x2", "y2")) or gt["x1"] < 0 or gt["y1"] < 0 or gt["x2"] > width or gt["y2"] > height or gt["x2"] <= gt["x1"] or gt["y2"] <= gt["y1"]:
                    raise SystemExit(f"Invalid out-of-bounds GT box in {case_id} frame {frame_1based}: {gt}")

            prepared = PreparedFrame(
                frame=image, source_type="photo", input_type="rgb", run_id=None, live_reference_id=None,
                frame_index=frame_1based - 1, timestamp_sec=None, width=width, height=height,
                channels=3, dtype=str(image.dtype), validation_status="valid", preprocessing_status="phase5_rgb_baseline",
            )
            result, annotated = detector.detect(prepared)
            operational_runtime = detector._runtime_instance("rgb")
            detector._runtime = replace(operational_runtime, confidence_threshold=0.001)
            try:
                ap_result, _ = detector.detect(prepared)
            finally:
                detector._runtime = operational_runtime
            frame_key = f"{case_id}:{frame_1based}"
            record = {
                "frame_key": frame_key, "case_id": case_id, "scenario": clip["scenario"],
                "video": clip["video"], "video_sha256": actual_video_hash,
                "video_frame_1based": frame_1based, "video_frame_0based": frame_1based - 1,
                "annotation_source": gt_path_rel, "annotation_frame_or_slot": cross["annotation_frame_or_slot"],
                "annotation_sha256": gt_hash, "human_reviewer": cross["reviewer"], "review_date": cross["review_date"],
                "width": width, "height": height, "gt_count": len(gt_boxes),
                "prediction_count": len(result.detections), "inference_time_ms": round(result.inference_time_ms, 3),
                "checkpoint": "artifacts/checkpoints/best.pt", "checkpoint_sha256": sha256(CHECKPOINT),
            }
            frame_records[frame_key] = {"record": record, "gt": gt_boxes, "pred": [], "pred_ap": []}
            output_frame_rows.append(record)
            for gt in gt_boxes:
                ground_truth_rows.append({**record, **gt})
            for index, detection in enumerate(result.detections, start=1):
                pred = {"class_id": detection.class_id, "class_name": detection.class_name,
                        "confidence": detection.confidence, "x1": detection.x1, "y1": detection.y1,
                        "x2": detection.x2, "y2": detection.y2, "prediction_index": index}
                frame_records[frame_key]["pred"].append(pred)
                prediction_rows.append({**record, **pred})
            for index, detection in enumerate(ap_result.detections, start=1):
                pred = {"class_id": detection.class_id, "class_name": detection.class_name,
                        "confidence": detection.confidence, "x1": detection.x1, "y1": detection.y1,
                        "x2": detection.x2, "y2": detection.y2, "prediction_index": index}
                frame_records[frame_key]["pred_ap"].append(pred)
                ap_prediction_rows.append({**record, **pred, "confidence_floor": 0.001})
            # Persist a reviewable, source-size overlay for every signed input frame.
            for gt in gt_boxes:
                cv2.rectangle(annotated, (round(gt["x1"]), round(gt["y1"])), (round(gt["x2"]), round(gt["y2"])), (0, 255, 0), max(1, round(width / 640)))
                cv2.putText(annotated, f"GT {gt['class_name']}", (max(0, round(gt["x1"])), max(14, round(gt["y1"]) - 4)), cv2.FONT_HERSHEY_SIMPLEX, max(0.4, width / 1600), (0, 255, 0), max(1, round(width / 640)), cv2.LINE_AA)
            OUTPUT.mkdir(parents=True, exist_ok=True)
            overlay_path = OUTPUT / f"{case_id}_frame_{frame_1based:04d}_overlay.jpg"
            if not cv2.imwrite(str(overlay_path), annotated):
                raise SystemExit(f"Could not save overlay: {overlay_path}")
            overlay_paths.append(overlay_path)
    finally:
        detector.close()

    summary: dict[str, Any] = {"thresholds": {}, "frames": len(frame_records), "classes": {}}
    confusion_rows = []
    for threshold in (0.50, 0.25):
        class_metrics = {}
        for class_name in ("Bird", "Drone"):
            tp = fp = fn = 0
            for entry in frame_records.values():
                gt_all, pred_all = entry["gt"], entry["pred"]
                matches = greedy_matches([g for g in gt_all if g["class_name"] == class_name], [p for p in pred_all if p["class_name"] == class_name], threshold, True)
                gt_count = sum(g["class_name"] == class_name for g in gt_all)
                pred_count = sum(p["class_name"] == class_name for p in pred_all)
                tp += len(matches)
                fp += pred_count - len(matches)
                fn += gt_count - len(matches)
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else None
            f1 = (2 * precision * recall / (precision + recall)) if recall is not None and precision + recall else 0.0 if recall is not None else None
            class_metrics[class_name] = {"gt": tp + fn, "predictions": tp + fp, "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
        clip_metrics = {}
        for case_id in sorted({entry["record"]["case_id"] for entry in frame_records.values()}):
            clip_entries = [entry for entry in frame_records.values() if entry["record"]["case_id"] == case_id]
            clip_classes = {}
            for class_name in ("Bird", "Drone"):
                tp = fp = fn = 0
                for entry in clip_entries:
                    gt_class = [g for g in entry["gt"] if g["class_name"] == class_name]
                    pred_class = [p for p in entry["pred"] if p["class_name"] == class_name]
                    matched = greedy_matches(gt_class, pred_class, threshold, True)
                    tp += len(matched)
                    fp += len(pred_class) - len(matched)
                    fn += len(gt_class) - len(matched)
                precision = tp / (tp + fp) if tp + fp else 0.0
                recall = tp / (tp + fn) if tp + fn else None
                f1 = (2 * precision * recall / (precision + recall)) if recall is not None and precision + recall else 0.0 if recall is not None else None
                clip_classes[class_name] = {"gt": tp + fn, "predictions": tp + fp, "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
            clip_metrics[case_id] = {"frames": len(clip_entries), "classes": clip_classes}
        confusion = {"Bird_to_Drone": 0, "Drone_to_Bird": 0}
        for entry in frame_records.values():
            for gi, pi, overlap in greedy_matches(entry["gt"], entry["pred"], threshold, False):
                gt_name, pred_name = entry["gt"][gi]["class_name"], entry["pred"][pi]["class_name"]
                if gt_name != pred_name:
                    confusion[f"{gt_name}_to_{pred_name}"] += 1
                    confusion_rows.append({"iou_threshold": threshold, "frame_key": entry["record"]["frame_key"], "gt_class": gt_name, "predicted_class": pred_name, "confidence": entry["pred"][pi]["confidence"], "iou": overlap})
        summary["thresholds"][str(threshold)] = {
            "per_class": class_metrics, "per_clip": clip_metrics, "class_confusions": confusion,
            "ap_101_point_interpolated": {name: average_precision(frame_records, name, threshold) for name in ("Bird", "Drone")},
        }

    for class_name in ("Bird", "Drone"):
        relevant = [record for record in output_frame_rows if any(g["class_name"] == class_name for g in frame_records[f"{record['case_id']}:{record['video_frame_1based']}"]["gt"])]
        summary["classes"][class_name] = {"ground_truth_frames": len(relevant), "note": "Class recall is not measurable if no GT boxes exist; selected-frame scope only."}
    summary.update({
        "evaluation_name": "Phase 5 frozen RGB detector diagnostic on Phase 4 accepted samples",
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "split": "known development/regression clips and SMOT4SB training-split frames; NOT held out",
        "modality": "RGB only", "checkpoint": "artifacts/checkpoints/best.pt",
        "checkpoint_sha256": sha256(CHECKPOINT), "class_mapping": {"0": "Bird", "1": "Drone"},
        "inference_settings": {"image_size": 640, "operational_confidence_threshold": 0.25, "ap_score_sweep_floor": 0.001, "nms_iou_threshold": 0.45, "device": "CPU as resolved by project runtime", "preprocessing": "project DetectionService letterbox, BGR-to-RGB, CHW, float32 [0,1]"},
        "match_method": "per-frame, class-aware greedy one-to-one IoU matching; thresholds 0.50 primary and 0.25 diagnostic",
        "ap_method": "101-point interpolated AP from post-NMS candidates with confidence >= 0.001; same checkpoint, frames, preprocessing, and NMS IoU; separate from operational metrics at confidence 0.25",
        "small_object_recall_iou50": {name: size_recall(frame_records, name, 0.50) for name in ("Bird", "Drone")},
        "source_review": {"reviewer": "Chetna Reddy", "accepted_signed_frames": len(selected), "rgb_clip_frames": sum(not case.startswith("SMOT4SB") for case, _ in selected), "smot4sb_frames": sum(case.startswith("SMOT4SB") for case, _ in selected)},
        "limitations": ["Sample is not held out; V_BIRD_029 and V_DRONE_007 are known regression clips and SMOT4SB frames are from its training split.", "Only five signed frames per MATLAB clip; label mapping was visually reviewed at these samples, not certified for every frame.", "Report is a small diagnostic, not a paper-grade estimate. Do not use to select thresholds or claim generalization.", "No thermal, V1 mixed-scene, or V2 multi-drone conclusion is supported."],
    })
    OUTPUT.mkdir(parents=True, exist_ok=True)
    gt_fields = list(ground_truth_rows[0]) if ground_truth_rows else ["frame_key"]
    pred_fields = list(prediction_rows[0]) if prediction_rows else ["frame_key"]
    write_csv(OUTPUT / "ground_truth.csv", ground_truth_rows, gt_fields)
    write_csv(OUTPUT / "predictions.csv", prediction_rows, pred_fields)
    write_csv(OUTPUT / "ap_score_sweep_predictions.csv", ap_prediction_rows, list(ap_prediction_rows[0]) if ap_prediction_rows else ["frame_key"])
    write_csv(OUTPUT / "frames.csv", output_frame_rows, list(output_frame_rows[0]))
    write_csv(OUTPUT / "class_confusions.csv", confusion_rows, ["iou_threshold", "frame_key", "gt_class", "predicted_class", "confidence", "iou"])
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    overlay_manifest = [{"path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(path)} for path in overlay_paths]
    source_manifest = {str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in (REVIEW, CROSSWALK, MANIFEST, CHECKPOINT)}
    (OUTPUT / "manifest.json").write_text(json.dumps({"summary": "summary.json", "frames": "frames.csv", "ground_truth": "ground_truth.csv", "predictions": "predictions.csv", "ap_score_sweep_predictions": "ap_score_sweep_predictions.csv", "confusions": "class_confusions.csv", "source_hashes": source_manifest, "overlay_hashes": overlay_manifest}, indent=2), encoding="utf-8")
    report = [
        "# Phase 5 — Frozen RGB detection baseline",
        "",
        f"Generated UTC: {summary['evaluation_timestamp_utc']}",
        "",
        "## Scope and settings",
        "",
        f"- Evaluated {len(selected)} human-accepted RGB frames: {summary['source_review']['smot4sb_frames']} SMOT4SB development frames and {summary['source_review']['rgb_clip_frames']} sampled frames from known Bird/Drone clips.",
        f"- Checkpoint `artifacts/checkpoints/best.pt` (SHA-256 `{summary['checkpoint_sha256']}`).",
        "- 640 input; confidence 0.25; NMS IoU 0.45; CPU; matching uses per-frame greedy one-to-one IoU.",
        "- Primary localization threshold IoU 0.50; IoU 0.25 is diagnostic only.",
        "- Source files, labels, thresholds, and model weights were not modified. No training or tuning was performed.",
        "",
        "## Results",
        "",
        "| IoU | Class | GT | Predictions | TP | FP | FN | Precision | Recall | F1 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for threshold, block in summary["thresholds"].items():
        for class_name, metrics in block["per_class"].items():
            fmt = lambda value: "N/A" if value is None else f"{value:.3f}"
            report.append(f"| {threshold} | {class_name} | {metrics['gt']} | {metrics['predictions']} | {metrics['tp']} | {metrics['fp']} | {metrics['fn']} | {fmt(metrics['precision'])} | {fmt(metrics['recall'])} | {fmt(metrics['f1'])} |")
    report += ["", "### 101-point interpolated AP from the confidence sweep", "", "| IoU | Class | GT | Candidates (score ≥ 0.001) | AP |", "| ---: | --- | ---: | ---: | ---: |"]
    for threshold, block in summary["thresholds"].items():
        for class_name, metrics in block["ap_101_point_interpolated"].items():
            ap_text = "N/A" if metrics["ap"] is None else f"{metrics['ap']:.3f}"
            report.append(f"| {threshold} | {class_name} | {metrics['gt']} | {metrics['predictions']} | {ap_text} |")
    report += ["", "Class-confusion counts from class-agnostic localization matches:", "", "| IoU | Bird GT predicted Drone | Drone GT predicted Bird |", "| ---: | ---: | ---: |"]
    for threshold, block in summary["thresholds"].items():
        confusion = block["class_confusions"]
        report.append(f"| {threshold} | {confusion['Bird_to_Drone']} | {confusion['Drone_to_Bird']} |")
    report += ["", "### Clip-level metrics at IoU 0.50", "", "| Clip | Frames | Class | GT | Predictions | TP | FP | FN | Precision | Recall | F1 |", "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for case_id, clip in summary["thresholds"]["0.5"]["per_clip"].items():
        for class_name, metrics in clip["classes"].items():
            fmt = lambda value: "N/A" if value is None else f"{value:.3f}"
            report.append(f"| {case_id} | {clip['frames']} | {class_name} | {metrics['gt']} | {metrics['predictions']} | {metrics['tp']} | {metrics['fp']} | {metrics['fn']} | {fmt(metrics['precision'])} | {fmt(metrics['recall'])} | {fmt(metrics['f1'])} |")
    report += ["", "### Small-object recall at IoU 0.50", "", "Size bins use the larger box side in source-image pixels: small ≤32; medium >32–96; large >96.", ""]
    for class_name, buckets in summary["small_object_recall_iou50"].items():
        for bucket, metrics in buckets.items():
            recall_text = "N/A" if metrics["recall"] is None else f"{metrics['recall']:.3f}"
            report.append(f"- {class_name} / {bucket}: {metrics['matched']}/{metrics['gt']} matched; recall {recall_text}.")
    report += ["", "## Interpretation and limits", ""]
    report.extend(f"- {item}" for item in summary["limitations"])
    report += ["- Precision/recall/F1 and AP are finite-sample diagnostics only; they are not benchmark or generalization estimates.", "- Full per-frame GT and confidence-ranked prediction ledgers are retained to make this exact diagnostic auditable and reproducible.", "", "## Reproduction", "", "Install the optional decoder documented in `datasets/external/drone_detection_thesis_v1_official_data/UPSTREAM_README.md` (`python -m pip install mcos-decoder`), then run `python tools/phase5_detection_baseline.py` from the repository root.", "", "Raw outputs: `frames.csv`, `ground_truth.csv`, `predictions.csv`, `ap_score_sweep_predictions.csv`, `class_confusions.csv`, `summary.json`, `manifest.json`; annotated frame previews are saved alongside them."]
    (OUTPUT / "PHASE5_DETECTION_BASELINE_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "frames": len(selected), "ground_truth_boxes": len(ground_truth_rows), "predictions": len(prediction_rows), "metrics": summary["thresholds"]}, indent=2))


if __name__ == "__main__":
    main()
