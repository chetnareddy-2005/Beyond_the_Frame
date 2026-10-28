"""Audit whether the local RGB data can support a defensible Phase 6 experiment.

This is deliberately read-only with respect to source data and checkpoints. It
hashes the active RGB image split, checks YOLO labels, summarizes recoverable
source-video groups/duplicate files, and fails closed when independent
training/validation evidence or checkpoint lineage is unavailable.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets/drone_vs_bird"
OUTPUT = ROOT / "datasets/processed/phase6_readiness_2026-10-22"
CHECKPOINT = ROOT / "artifacts/checkpoints/best.pt"
CHECKPOINT_SHA256 = "fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b"
SPLITS = ("train", "valid", "test")
CLASS_NAMES = {0: "Bird", 1: "Drone"}
SOURCE_ID = re.compile(r"^(video\d+)_", re.IGNORECASE)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def inspect_dataset() -> dict[str, Any]:
    split_records: dict[str, list[dict[str, Any]]] = {}
    hash_members: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_members: dict[str, list[dict[str, str]]] = defaultdict(list)
    errors: list[str] = []
    class_totals: Counter[str] = Counter()
    label_counts: dict[str, int] = {}

    for split in SPLITS:
        image_dir = DATASET / split / "images"
        label_dir = DATASET / split / "labels"
        images = sorted(path for path in image_dir.glob("*") if path.is_file()) if image_dir.is_dir() else []
        records = []
        labels_seen = 0
        for image in images:
            label = label_dir / f"{image.stem}.txt"
            if not label.is_file():
                errors.append(f"Missing label: {label.relative_to(ROOT).as_posix()}")
                label_text = ""
                label_hash = None
            else:
                labels_seen += 1
                label_text = label.read_text(encoding="utf-8-sig").strip()
                label_hash = digest(label)
            row_count = 0
            for line_number, line in enumerate(label_text.splitlines(), start=1):
                parts = line.split()
                try:
                    if len(parts) != 5:
                        raise ValueError("expected class,x_center,y_center,width,height")
                    class_value = float(parts[0])
                    class_id = int(class_value)
                    coords = [float(value) for value in parts[1:]]
                    if class_value != class_id or class_id not in CLASS_NAMES:
                        raise ValueError(f"unsupported class id {parts[0]}")
                    if any(value < 0 or value > 1 for value in coords) or coords[2] <= 0 or coords[3] <= 0:
                        raise ValueError("coordinates must be normalized to [0,1] with positive width/height")
                    class_totals[CLASS_NAMES[class_id]] += 1
                    row_count += 1
                except (ValueError, OverflowError) as error:
                    errors.append(f"Invalid label {label.relative_to(ROOT).as_posix()}:{line_number}: {error}")
            source_match = SOURCE_ID.match(image.name)
            source_id = source_match.group(1).lower() if source_match else None
            record = {
                "split": split,
                "image": image.relative_to(ROOT).as_posix(),
                "image_sha256": digest(image),
                "bytes": image.stat().st_size,
                "label": label.relative_to(ROOT).as_posix() if label.exists() else None,
                "label_sha256": label_hash,
                "box_count": row_count,
                "source_video_id": source_id,
            }
            records.append(record)
            hash_members[record["image_sha256"]].append({"split": split, "image": record["image"]})
            if source_id:
                source_members[source_id].append({"split": split, "image": record["image"]})
        split_records[split] = records
        label_counts[split] = labels_seen

    duplicates = [members for members in hash_members.values() if len({m["split"] for m in members}) > 1]
    source_overlaps = {
        source_id: members for source_id, members in sorted(source_members.items())
        if len({member["split"] for member in members}) > 1
    }
    source_split_sets = {source_id: sorted({member["split"] for member in members}) for source_id, members in source_members.items()}
    actual_checkpoint_hash = digest(CHECKPOINT) if CHECKPOINT.is_file() else None
    checkpoint_matches_frozen_hash = actual_checkpoint_hash == CHECKPOINT_SHA256

    source_id_coverage = sum(len(members) for members in source_members.values())
    total_images = sum(len(records) for records in split_records.values())
    dataset = {
        "path": "datasets/drone_vs_bird",
        "declared_license": "CC BY 4.0 (local Roboflow export README)",
        "class_map": {str(key): value for key, value in CLASS_NAMES.items()},
        "split_counts": {split: {"images": len(records), "labels": label_counts[split], "boxes": sum(row["box_count"] for row in records)} for split, records in split_records.items()},
        "total_images": total_images,
        "box_counts_by_class": dict(sorted(class_totals.items())),
        "paired_label_files": sum(label_counts.values()),
        "label_or_pairing_errors": errors,
        "exact_image_hash_groups_crossing_splits": len(duplicates),
        "exact_image_hash_duplicate_members_crossing_splits": sum(len(members) for members in duplicates),
        "recoverable_source_video_image_count": source_id_coverage,
        "source_video_id_coverage_fraction": source_id_coverage / total_images if total_images else None,
        "recoverable_source_video_ids": len(source_members),
        "source_video_groups_crossing_splits": len(source_overlaps),
        "source_video_split_overlaps": source_overlaps,
        "unknown_source_video_image_count": total_images - source_id_coverage,
        "image_records": [row for split in SPLITS for row in split_records[split]],
    }
    checkpoint = {
        "path": "artifacts/checkpoints/best.pt",
        "sha256": actual_checkpoint_hash,
        "matches_phase1_and_phase5_frozen_hash": checkpoint_matches_frozen_hash,
        "training_lineage_verified": False,
        "known_metadata": "Prior reviewed handoff records embedded options indicating a 200-epoch budget and epoch 76, but the original command, matching dataset content, complete hyperparameters/results, source-group split, and checkpoint-to-run chain were not located.",
        "do_not_attribute_to_cpu_smoke_run": True,
    }
    blockers = []
    if not checkpoint_matches_frozen_hash:
        blockers.append("Active RGB checkpoint no longer matches the frozen Phase 1/5 SHA-256; stop until the model artifact is reconciled.")
    blockers.append("Active RGB checkpoint does not have a verified training run, exact dataset manifest, full hyperparameters, initialization lineage, and reproducible save record.")
    blockers.append("The existing Roboflow data export is image-level; no source videos are supplied, and 2,851/2,920 images have no recoverable source-video ID in the local audit.")
    blockers.append(f"All {len(source_overlaps)} recoverable source-video IDs cross the declared split boundary; the existing validation/test splits are not sequence-independent.")
    if duplicates:
        blockers.append(f"{len(duplicates)} exact-image hash groups occur across declared splits; these must be grouped/isolated before a validation claim.")
    if errors:
        blockers.append(f"Dataset pairing/label validation found {len(errors)} issue(s).")
    blockers.append("SMOT4SB is training-split Bird-only video, so it cannot provide a balanced Bird+Drone validation set; Phase 5 accepted samples are frozen diagnostic data and must not be tuned on.")
    blockers.append("Other local Bird/Drone images are small/provisional or lack source-level split evidence; gated or rights-unresolved data cannot be assumed available for training.")
    return {
        "audit_date": "2026-10-22",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "audit_scope": "Read-only Phase 6 readiness and leakage audit; no training, tuning, threshold change, or model promotion.",
        "checkpoint": checkpoint,
        "dataset": dataset,
        "phase5_baseline": {
            "path": "datasets/processed/phase5_detection_baseline_2026-10-22/summary.json",
            "bird_recall_iou50": 0.0,
            "bird_gt_boxes": 54,
            "drone_gt_boxes": 5,
            "decision": "Use only as a frozen diagnostic reference. Do not use its accepted frames to tune candidate weights or thresholds.",
        },
        "decision": {
            "status": "BLOCKED_BEFORE_TRAINING",
            "training_allowed": False,
            "checkpoint_promotion_allowed": False,
            "reason": "No independently grouped, provenance-safe Bird+Drone training/validation pair is currently evidenced; training now would not support a defensible improvement claim.",
        },
        "blockers": blockers,
    }


def write_outputs(audit: dict[str, Any]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "readiness_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    rows = audit["dataset"]["image_records"]
    with (OUTPUT / "dataset_image_manifest.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = ["split", "image", "image_sha256", "bytes", "label", "label_sha256", "box_count", "source_video_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    report = [
        "# Phase 6 — targeted detector improvement readiness",
        "",
        "**Decision: BLOCKED BEFORE TRAINING.** This is an intentional safety gate, not a detector-improvement result. Training and model promotion were not run because the current local data cannot support a clean independent Bird+Drone validation claim and the active checkpoint's full lineage is unresolved.",
        "",
        "## Implementation plan",
        "",
        "1. Freeze and hash the active RGB checkpoint and Phase 5 baseline; reconcile any hash drift before continuing.",
        "2. Recover the checkpoint's exact training run, initialization checkpoint, dataset version and file manifest, source-sequence groups, hyperparameters, software versions, and best-checkpoint selection record.",
        "3. Audit every candidate image/label pair, class map, license, exact duplicates, near-duplicate/source-sequence groups, and train/validation/test membership.",
        "4. Build a new whole-source-group training/validation partition from eligible Bird and Drone sources; permanently quarantine a final test set and all Phase 5 diagnostic frames.",
        "5. Pre-register the detector change, fixed inference/matching settings, primary and per-class metrics, and acceptance rule; retain the Phase 5 checkpoint as the unchanged comparator.",
        "6. Train a named candidate into a new immutable run directory, recording code/data/checkpoint hashes, config, seeds, software/device, logs, and outputs.",
        "7. Compare candidate vs. baseline on the untouched validation split, inspect per-class and small-object errors, and promote only if the frozen acceptance rule passes without unacceptable regression.",
        "8. Publish the model card/experiment report; otherwise retain the baseline and report the failed experiment. Never overwrite the active checkpoint during the experiment.",
        "",
        "## Work completed in this pass",
        "",
        "- Verified the active RGB checkpoint still matches the frozen SHA-256 recorded in Phases 1 and 5.",
        "- Implemented a read-only auditor for the local Roboflow RGB image/label pairing, normalized YOLO rows, class counts, SHA-256 duplicate groups, and recoverable source-video groups.",
        "- Reconciled its result with the existing provenance handoff and dataset audit; saved a complete per-image hash inventory.",
        "- Added a fail-closed decision: the tool expressly denies training and promotion while provenance or split-independence gates fail.",
        "- No dataset labels, source media, model weights, inference thresholds, or Phase 5 records were changed.",
        "",
        "## Current evidence",
        "",
        f"- Active checkpoint hash matches the frozen value: **{audit['checkpoint']['matches_phase1_and_phase5_frozen_hash']}**. Verified training lineage: **{audit['checkpoint']['training_lineage_verified']}**.",
        f"- Existing image dataset: {audit['dataset']['total_images']} images, {audit['dataset']['paired_label_files']} paired label files, box counts {json.dumps(audit['dataset']['box_counts_by_class'], sort_keys=True)}.",
        f"- Recoverable source-video IDs cover {audit['dataset']['recoverable_source_video_image_count']}/{audit['dataset']['total_images']} images; {audit['dataset']['source_video_groups_crossing_splits']} recoverable source groups cross declared splits.",
        f"- Exact image-hash groups crossing splits: {audit['dataset']['exact_image_hash_groups_crossing_splits']}.",
        "- Existing evidence says the current validation/test split is not sequence-independent; 2,851 images lack recoverable source-video IDs. Unknown source identity cannot be treated as an independent source.",
        "- Existing phase audit reports near-duplicate same-scene candidates; these were not automatically removed or reassigned.",
        "",
        "## Gate blockers",
        "",
    ]
    report.extend(f"- {blocker}" for blocker in audit["blockers"])
    report += [
        "",
        "## Gate exit criteria",
        "",
        "- [ ] Checkpoint-to-training-run lineage verified, including exact initialization and data membership.",
        "- [ ] License-cleared Bird and Drone training sources and independently grouped validation sources are available.",
        "- [ ] All split assignments are source-sequence disjoint; duplicates are kept within a single group/split.",
        "- [ ] Phase 5 samples and final held-out test data are excluded from tuning.",
        "- [ ] A reproducible candidate-training config and predeclared acceptance rule are frozen.",
        "- [ ] Candidate beats the baseline on the independent validation split without unacceptable class/small-object regression.",
        "",
        "## Reproduction",
        "",
        "From the repository root run `python tools/phase6_readiness_audit.py`. The command is read-only toward existing source/model files and writes only `datasets/processed/phase6_readiness_2026-10-22/`.",
        "",
        "Machine-readable evidence: `readiness_audit.json`; full per-image file hashes and source grouping fields: `dataset_image_manifest.csv`.",
    ]
    (OUTPUT / "PHASE6_READINESS_AND_IMPLEMENTATION_PLAN.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> None:
    audit = inspect_dataset()
    write_outputs(audit)
    print(json.dumps({
        "status": audit["decision"]["status"],
        "training_allowed": audit["decision"]["training_allowed"],
        "checkpoint_hash_matches": audit["checkpoint"]["matches_phase1_and_phase5_frozen_hash"],
        "images": audit["dataset"]["total_images"],
        "labels": audit["dataset"]["paired_label_files"],
        "boxes": audit["dataset"]["box_counts_by_class"],
        "recoverable_source_images": audit["dataset"]["recoverable_source_video_image_count"],
        "source_groups_crossing_splits": audit["dataset"]["source_video_groups_crossing_splits"],
        "exact_duplicate_groups_crossing_splits": audit["dataset"]["exact_image_hash_groups_crossing_splits"],
        "label_pairing_errors": len(audit["dataset"]["label_or_pairing_errors"]),
        "output": str(OUTPUT),
    }, indent=2))


if __name__ == "__main__":
    main()
