"""Audit aligned RGB Bird/Drone videos for a source-disjoint Phase 6 candidate split.

Uses the upstream mcos-decoder package installed into the dated Phase 6 runtime.
Existing media and labels are read-only; the script writes only Phase 6 audit
outputs. It does not authorize training by itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PHASE6 = ROOT / "datasets/processed/phase6_readiness_2026-10-22"
DATA = ROOT / "datasets/external/drone_detection_thesis_v1_official_data"
INVENTORY = ROOT / "datasets/processed/phase2/drone_detection_thesis_inventory_reviewed.csv"
RUNTIME = PHASE6 / "runtime"
SPLIT_SEED = 20261022
PHASE5_QUARANTINED_VIDEO_IDS = {"V_BIRD_029", "V_DRONE_007", "V_DRONE_024"}

sys.path.insert(0, str(RUNTIME))
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from mcos_decoder import load_groundtruth  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def decoded_frame_count(path: Path) -> tuple[int, int, int, int]:
    cap = cv2.VideoCapture(str(path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(round(cap.get(cv2.CAP_PROP_FPS)))
    count = 0
    try:
        while True:
            ok, _frame = cap.read()
            if not ok:
                break
            count += 1
    finally:
        cap.release()
    return count, width, height, fps


def inspect(row: dict[str, str]) -> dict[str, Any]:
    video = DATA / row["video_path"]
    label = DATA / row["label_path"]
    frames, width, height, fps = decoded_frame_count(video)
    boxes = load_groundtruth(label)
    nonempty = sum(box is not None for box in boxes)
    invalid = 0
    for box in boxes:
        if box is None:
            continue
        x, y, w, h = map(float, box[:4])
        if not (w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width + 1 and y + h <= height + 1):
            invalid += 1
    prefix_empty = bool(boxes) and boxes[0] is None
    suffix_empty = bool(boxes) and boxes[-1] is None
    if len(boxes) == frames and invalid == 0:
        mapping = "direct_count_match_candidate"
    elif len(boxes) == frames + 1 and prefix_empty and invalid == 0:
        mapping = "leading_empty_plus1_candidate"
    else:
        mapping = "alignment_review_required"
    return {
        "sensor": row["sensor"], "class": row["class"], "video_id": row["video_id"],
        "video_path": row["video_path"], "label_path": row["label_path"],
        "video_sha256": sha256(video), "label_sha256": sha256(label),
        "decoded_frames": frames, "label_slots": len(boxes), "annotated_slots": nonempty,
        "width": width, "height": height, "fps": fps,
        "first_slot_empty": prefix_empty, "last_slot_empty": suffix_empty,
        "invalid_boxes": invalid, "mapping_candidate": mapping,
    }


def split_ids(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Deterministically assign whole recording IDs, stratified by object class."""
    rng = random.Random(SPLIT_SEED)
    assignments: dict[str, str] = {}
    for cls in ("BIRD", "DRONE"):
        ids = sorted(r["video_id"] for r in rows if r["class"] == cls)
        rng.shuffle(ids)
        n = len(ids)
        n_train = max(1, int(n * 0.70))
        n_val = max(1, int(n * 0.15))
        for i, video_id in enumerate(ids):
            assignments[video_id] = "train" if i < n_train else "validation" if i < n_train + n_val else "test_quarantined"
    return assignments


def create_alignment_contact_sheets(records: list[dict[str, Any]]) -> list[str]:
    """Create five-timepoint review sheets for every structurally aligned clip."""
    selected = [r for r in records if r["mapping_candidate"] in {"direct_count_match_candidate", "leading_empty_plus1_candidate"}]
    selected.sort(key=lambda r: (r["class"], r["video_id"]))
    sheet_dir = PHASE6 / "thesis_rgb_alignment_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    frame_w, frame_h, header_h, cols, rows_per_sheet = 192, 154, 26, 5, 10
    sample_fractions = (0.0, 0.25, 0.5, 0.75, 1.0)
    names = []
    for page_start in range(0, len(selected), rows_per_sheet):
        page_records = selected[page_start:page_start + rows_per_sheet]
        canvas = np.full((rows_per_sheet * (frame_h + header_h), cols * frame_w, 3), 247, dtype=np.uint8)
        for row_idx, record in enumerate(page_records):
            video = DATA / record["video_path"]
            label = DATA / record["label_path"]
            boxes = load_groundtruth(label)
            nframes = int(record["decoded_frames"])
            cap = cv2.VideoCapture(str(video))
            for col_idx, fraction in enumerate(sample_fractions):
                frame_idx = round(fraction * max(0, nframes - 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, frame = cap.read()
                x0, y0 = col_idx * frame_w, row_idx * (frame_h + header_h)
                title = f"{record['class']} {record['video_id']}  f{frame_idx + 1}"
                cv2.putText(canvas, title[:28], (x0 + 3, y0 + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (16, 32, 48), 1, cv2.LINE_AA)
                if ok:
                    slot_idx = frame_idx + (1 if record["mapping_candidate"] == "leading_empty_plus1_candidate" else 0)
                    bbox = boxes[slot_idx] if slot_idx < len(boxes) else None
                    if bbox is not None:
                        x, y, w, h = map(float, bbox[:4])
                        crop_w = max(48.0, w * 5.0)
                        crop_h = max(38.0, h * 5.0)
                        crop_w = max(crop_w, crop_h * frame_w / frame_h)
                        crop_h = max(crop_h, crop_w * frame_h / frame_w)
                        cx, cy = x + w / 2, y + h / 2
                        left = max(0, round(cx - crop_w / 2))
                        top = max(0, round(cy - crop_h / 2))
                        right = min(frame.shape[1], max(left + 1, round(cx + crop_w / 2)))
                        bottom = min(frame.shape[0], max(top + 1, round(cy + crop_h / 2)))
                        crop = frame[top:bottom, left:right].copy()
                        cv2.rectangle(crop, (max(0, round(x - left)), max(0, round(y - top))), (min(crop.shape[1] - 1, round(x + w - left)), min(crop.shape[0] - 1, round(y + h - top))), (0, 215, 255), 1)
                    else:
                        crop = frame
                    thumbnail = cv2.resize(crop, (frame_w, frame_h), interpolation=cv2.INTER_AREA)
                    canvas[y0 + header_h:y0 + header_h + frame_h, x0:x0 + frame_w] = thumbnail
                    if bbox is None:
                        cv2.putText(canvas, "empty annotation", (x0 + 4, y0 + header_h + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (0, 80, 200), 1, cv2.LINE_AA)
                else:
                    cv2.putText(canvas, "DECODE FAILED", (x0 + 4, y0 + header_h + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)
            cap.release()
        name = f"alignment_review_{page_start // rows_per_sheet + 1:02d}.jpg"
        cv2.imwrite(str(sheet_dir / name), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
        names.append(f"thesis_rgb_alignment_sheets/{name}")
    return names


def main() -> None:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as stream:
        inventory = list(csv.DictReader(stream))
    candidates = [row for row in inventory if row["sensor"] == "visible" and row["class"] in {"BIRD", "DRONE"}]
    records = [inspect(row) for row in candidates]
    counts = Counter(f"{r['class']}:{r['mapping_candidate']}" for r in records)
    eligible = [r for r in records if r["mapping_candidate"] in {"direct_count_match_candidate", "leading_empty_plus1_candidate"} and r["annotated_slots"] > 0]
    phase5_quarantined = {r["video_id"] for r in eligible} & PHASE5_QUARANTINED_VIDEO_IDS
    split_candidates = [r for r in eligible if r["video_id"] not in PHASE5_QUARANTINED_VIDEO_IDS]
    assignments = split_ids(split_candidates)
    for record in records:
        record["provisional_split"] = (
            "phase5_quarantined" if record["video_id"] in phase5_quarantined
            else assignments.get(record["video_id"], "excluded_until_alignment_review")
        )
    sheets = create_alignment_contact_sheets(records)
    PHASE6.mkdir(parents=True, exist_ok=True)
    out_csv = PHASE6 / "drone_detection_thesis_rgb_video_audit.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as stream:
        fields = list(records[0].keys()) if records else []
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    result = {
        "audit_scope": "RGB Bird/Drone video candidate audit; every candidate video sequentially decoded, every paired MCOS label decoded; no training performed.",
        "upstream": "DroneDetectionThesis/Drone-detection-dataset; CC0-1.0 per upstream license and README.",
        "dataset_root": "datasets/external/drone_detection_thesis_v1_official_data",
        "decoder": "mcos-decoder 0.1.0, isolated under the Phase 6 output folder",
        "candidate_videos": len(records),
        "candidates_by_class": dict(Counter(r["class"] for r in records)),
        "mapping_candidate_counts": dict(counts),
        "eligible_for_visual_mapping_review": len(eligible),
        "phase5_video_quarantine": sorted(phase5_quarantined),
        "provisional_split_counts": {split: dict(Counter(r["class"] for r in split_candidates if assignments[r["video_id"]] == split)) for split in ("train", "validation", "test_quarantined")},
        "split_seed": SPLIT_SEED,
        "split_unit": "whole source video_id; no frames from one video cross splits",
        "release_gate": "Not yet cleared: candidate mapping rows require visual confirmation per RGB sequence before export/training; architecture/training config/acceptance rule also not frozen.",
        "csv": "drone_detection_thesis_rgb_video_audit.csv",
        "alignment_contact_sheets": sheets,
    }
    (PHASE6 / "drone_detection_thesis_rgb_video_audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
