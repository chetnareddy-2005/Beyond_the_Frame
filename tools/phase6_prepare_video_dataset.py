"""Prepare a balanced, whole-video split from audited RGB thesis sequences.

Source media and labels are read-only. Outputs are isolated beneath
datasets/processed/phase6_candidate_dataset_2026-10-22/.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "datasets/processed/phase6_readiness_2026-10-22/drone_detection_thesis_rgb_video_audit.csv"
DATA = ROOT / "datasets/external/drone_detection_thesis_v1_official_data"
OUT = ROOT / "datasets/processed/phase6_candidate_dataset_2026-10-22"
RUNTIME = ROOT / "datasets/processed/phase6_readiness_2026-10-22/runtime"

sys.path.insert(0, str(RUNTIME))
import cv2  # noqa: E402
from mcos_decoder import load_groundtruth  # noqa: E402


def sample_indices(frame_count: int, split: str, cls: str) -> list[int]:
    if split == "train":
        target_count = 20 if cls == "BIRD" else 7
        return sorted({round(i * (frame_count - 1) / (target_count - 1)) for i in range(target_count)})
    # Dense deterministic sampling across each held-out video; the same video's
    # adjacent frames never appear in a different split.
    indices = list(range(0, frame_count, 10))
    if frame_count - 1 not in indices:
        indices.append(frame_count - 1)
    return indices


def export_video(record: dict[str, str]) -> dict[str, Any]:
    split = record["provisional_split"]
    cls = record["class"]
    class_id = 0 if cls == "BIRD" else 1
    video_path = DATA / record["video_path"]
    label_path = DATA / record["label_path"]
    boxes = load_groundtruth(label_path)
    frame_count = int(record["decoded_frames"])
    mapping = record["mapping_candidate"]
    frame_indices = sample_indices(frame_count, split, cls)
    cap = cv2.VideoCapture(str(video_path))
    rows = []
    written = 0
    try:
        for frame_idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"Decode failed: {record['video_id']} frame {frame_idx + 1}")
            label_idx = frame_idx + (1 if mapping == "leading_empty_plus1_candidate" else 0)
            bbox = boxes[label_idx] if label_idx < len(boxes) else None
            if bbox is not None:
                x, y, w, h = map(float, bbox[:4])
                width, height = frame.shape[1], frame.shape[0]
                x1, y1 = max(0.0, x), max(0.0, y)
                x2, y2 = min(float(width), x + w), min(float(height), y + h)
                if x2 <= x1 or y2 <= y1:
                    raise RuntimeError(f"Invalid bbox: {record['video_id']} frame {frame_idx + 1}")
                xc, yc = ((x1 + x2) / 2 / width, (y1 + y2) / 2 / height)
                bw, bh = ((x2 - x1) / width, (y2 - y1) / height)
                yolo_row = f"{class_id} {xc:.8f} {yc:.8f} {bw:.8f} {bh:.8f}\n"
            else:
                yolo_row = ""
            basename = f"{record['video_id']}_frame_{frame_idx + 1:04d}"
            image_out = OUT / split / "images" / f"{basename}.jpg"
            label_out = OUT / split / "labels" / f"{basename}.txt"
            image_out.parent.mkdir(parents=True, exist_ok=True)
            label_out.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(image_out), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise RuntimeError(f"Could not write {image_out}")
            label_out.write_text(yolo_row, encoding="utf-8")
            written += 1
            rows.append({
                "split": split, "video_id": record["video_id"], "class": cls,
                "source_frame_1based": frame_idx + 1, "source_label_slot_1based": label_idx + 1,
                "source_video_sha256": record["video_sha256"],
                "source_label_sha256": record["label_sha256"],
                "image": image_out.relative_to(ROOT).as_posix(),
                "label": label_out.relative_to(ROOT).as_posix(),
                "has_box": bool(yolo_row), "class_id": class_id if yolo_row else "",
            })
    finally:
        cap.release()
    return {"video_id": record["video_id"], "split": split, "class": cls, "frames_exported": written, "rows": rows}


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {OUT}")
    with AUDIT.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    selected = [r for r in records if r["provisional_split"] in {"train", "validation", "test_quarantined"}]
    if not selected:
        raise SystemExit("No eligible videos in the audit manifest")
    if set(r["provisional_split"] for r in selected) != {"train", "validation", "test_quarantined"}:
        raise SystemExit("Expected train, validation, and quarantined test video groups")
    # The Phase 5 clips must never become training or validation inputs.
    forbidden = {"V_BIRD_029", "V_DRONE_007", "V_DRONE_024"}
    if any(r["video_id"] in forbidden for r in selected):
        raise SystemExit("Phase 5 diagnostic videos leaked into the candidate split")
    video_sets = {split: {r["video_id"] for r in selected if r["provisional_split"] == split} for split in ("train", "validation", "test_quarantined")}
    if (video_sets["train"] & video_sets["validation"] or video_sets["train"] & video_sets["test_quarantined"] or video_sets["validation"] & video_sets["test_quarantined"]):
        raise SystemExit("Source video IDs overlap between splits")
    all_rows = []
    video_summaries = []
    for record in selected:
        result = export_video(record)
        all_rows.extend(result.pop("rows"))
        video_summaries.append(result)
    with (OUT / "frame_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    yaml_text = (
        f"path: {OUT.resolve().as_posix()}\n"
        "train: train/images\n"
        "val: validation/images\n"
        "test: test_quarantined/images\n"
        "nc: 2\n"
        "names: [Bird, Drone]\n"
    )
    (OUT / "data.yaml").write_text(yaml_text, encoding="utf-8")
    summary = {
        "source_dataset": "DroneDetectionThesis/Drone-detection-dataset",
        "license": "CC0-1.0 per upstream",
        "class_map": {"0": "Bird", "1": "Drone"},
        "split_unit": "whole RGB source video; source_video_id sets are disjoint",
        "train_video_ids": sorted(video_sets["train"]),
        "validation_video_ids": sorted(video_sets["validation"]),
        "quarantined_test_video_ids": sorted(video_sets["test_quarantined"]),
        "phase5_quarantine": sorted(forbidden),
        "exported_frame_counts": {split: sum(r["split"] == split for r in all_rows) for split in ("train", "validation", "test_quarantined")},
        "box_counts_by_split_and_class": {
            split: {cls: sum(r["split"] == split and r["class"] == cls and r["has_box"] for r in all_rows) for cls in ("BIRD", "DRONE")}
            for split in ("train", "validation", "test_quarantined")
        },
        "source_group_caveat": "Whole-video disjointness is enforced. Upstream does not expose camera/site IDs; cross-video same-scene independence cannot be certified from current metadata.",
        "video_summaries": video_summaries,
        "manifest": "frame_manifest.csv",
        "dataset_yaml": "data.yaml",
    }
    (OUT / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"train_video_ids", "validation_video_ids", "quarantined_test_video_ids", "video_summaries"}}, indent=2))


if __name__ == "__main__":
    main()
