"""Build a reproducible Phase 4 sampled-audit crosswalk without editing source data.

This utility validates frozen source/evidence hashes and exports the selected
SMOT4SB sample boxes plus frame-to-annotation references for the MAT clips.
It preserves recorded human decisions and never replaces them with assistant
visual opinions when the exports are rebuilt.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "datasets/processed/phase4_quick_audit"
MANIFEST = AUDIT / "phase4_pilot_manifest.json"
QUEUE = AUDIT / "phase4_manual_review_queue.csv"
SIGNOFF = AUDIT / "phase4_human_review_signoff.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = read_csv(QUEUE)
    if len(rows) != 27:
        raise SystemExit(f"Expected 27 frozen pilot samples, found {len(rows)}")
    existing_reviews = {}
    if SIGNOFF.exists():
        for prior in read_csv(SIGNOFF):
            existing_reviews[(prior["case_id"], prior["video_frame_1based"])] = prior

    clip_by_case = {clip["case_id"]: clip for clip in manifest["clips"]}
    verified_sources = []
    for clip in manifest["clips"]:
        for path_key, hash_key in (("video", "video_sha256"), ("ground_truth", "ground_truth_sha256"), ("overlay", "overlay_sha256")):
            expected = clip.get(hash_key)
            relative = clip.get(path_key)
            if not expected or not relative:
                continue
            path = ROOT / relative
            if not path.is_file():
                raise SystemExit(f"Missing manifest source/evidence: {relative}")
            actual = sha256(path)
            if actual != expected:
                raise SystemExit(f"SHA-256 mismatch for {relative}: expected {expected}, got {actual}")
            verified_sources.append({"path": relative, "sha256": actual})
        annotation_path = clip.get("annotation_source")
        annotation_hash = clip.get("annotation_sha256")
        if annotation_path and annotation_hash:
            path = ROOT / annotation_path
            actual = sha256(path)
            if actual != annotation_hash:
                raise SystemExit(f"SHA-256 mismatch for {annotation_path}: expected {annotation_hash}, got {actual}")
            verified_sources.append({"path": annotation_path, "sha256": actual})
    for source in manifest.get("supporting_evidence", []):
        path = ROOT / source["path"]
        actual = sha256(path)
        if actual != source["sha256"]:
            raise SystemExit(f"SHA-256 mismatch for {source['path']}: expected {source['sha256']}, got {actual}")
        verified_sources.append({"path": source["path"], "sha256": actual})
    if SIGNOFF.is_file():
        verified_sources.append({"path": str(SIGNOFF.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(SIGNOFF)})

    review_rows: list[dict[str, str]] = []
    crosswalk_rows: list[dict[str, str]] = []
    derived_smot_rows: list[dict[str, str]] = []
    smot_cache: dict[str, dict[int, list[dict[str, str]]]] = {}

    for row in rows:
        case_id = row["case_id"]
        manifest_case_id = "V3_" + case_id if case_id.startswith("SMOT4SB_") else case_id
        clip = clip_by_case.get(manifest_case_id)
        if clip is None and not case_id.startswith("SMOT4SB_"):
            raise SystemExit(f"Queue case is absent from manifest: {case_id}")
        frame = int(row["video_frame_1based"])
        source_slot = int(row["annotation_frame_or_slot"])
        decision = "PLAUSIBLE_BUT_VISUALLY_AMBIGUOUS"
        note = "Sampled overlay is consistent with the annotation, but target is too small/blurred for assistant inspection alone to certify class/box identity; the recorded human ACCEPT is authoritative for this sample. Keep this sequence development-only."
        if case_id == "RGB_DRONE_007":
            decision = "VISIBLE_DRONE_CLASS_CONSISTENT"
            note = "The sampled crop visibly contains a drone-shaped target within the box; sampled review supports the +1 label-slot mapping only, not exhaustive box certification."
        elif case_id == "RGB_BIRD_029":
            decision = "VISIBLE_TARGET_BIRD_LABEL_PLAUSIBLE"
            note = "A small target is visible within the sampled box and the Bird label is plausible; visual review alone does not establish every-frame label correctness."
        elif case_id == "THERMAL_DRONE_040":
            decision = "THERMAL_TARGET_ALIGNMENT_CONSISTENT"
            note = "Bright thermal target is centered within the sampled box at all five reviewed timestamps; this is image-plane alignment evidence, not calibrated thermal or accuracy validation."

        prior = existing_reviews.get((case_id, str(frame)), {})
        human_decision = prior.get("review_decision", "").strip().upper()
        reviewer = prior.get("reviewer", "").strip()
        review_date = prior.get("review_date", "").strip()
        reviewer_notes = prior.get("reviewer_notes", "").strip()
        if human_decision not in {"", "ACCEPT", "QUESTIONABLE", "REJECT"}:
            raise SystemExit(f"Invalid human review decision {human_decision!r} for {case_id} frame {frame}")
        has_human_decision = bool(human_decision and reviewer and review_date)
        review_rows.append({
            **row,
            "assistant_visual_decision": decision,
            "assistant_visual_notes": note,
            "review_status": "HUMAN_REVIEWED" if has_human_decision else "PENDING_HUMAN",
            "review_decision": human_decision,
            "reviewer": reviewer,
            "review_date": review_date,
            "reviewer_notes": reviewer_notes,
        })
        crosswalk_rows.append({
            "case_id": case_id,
            "video_frame_1based": str(frame),
            "video_frame_0based": str(frame - 1),
            "annotation_source": row["annotation_source"],
            "annotation_frame_or_slot": str(source_slot),
            "mapping_rule": "frame_id=n -> video_index=n-1" if case_id.startswith("SMOT4SB") else ("label_slot=video_frame_1based+1 (sampled candidate only)" if case_id in {"RGB_BIRD_029", "RGB_DRONE_007"} else "label_slot=video_frame_1based (sampled candidate only)"),
            "assistant_visual_decision": decision,
            "review_decision": human_decision,
            "source_ground_truth_hash": sha256(ROOT / row["annotation_source"]),
            "reviewer": reviewer,
            "review_date": review_date,
            "qualified_human_signoff": "RECORDED" if has_human_decision else "PENDING",
        })

        if case_id.startswith("SMOT4SB"):
            gt_path = ROOT / row["annotation_source"]
            if row["annotation_source"] not in smot_cache:
                by_frame: dict[int, list[dict[str, str]]] = {}
                for gt_row in read_csv(gt_path):
                    by_frame.setdefault(int(gt_row["frame_id"]), []).append(gt_row)
                smot_cache[row["annotation_source"]] = by_frame
            objects = smot_cache[row["annotation_source"]].get(frame, [])
            if len(objects) != int(row["gt_box_count"]):
                raise SystemExit(f"GT row count mismatch for {case_id} frame {frame}")
            for obj in objects:
                derived_smot_rows.append({
                    "case_id": case_id,
                    "video_frame_1based": str(frame),
                    "video_frame_0based": str(frame - 1),
                    "class": obj["class"],
                    "source_track_id": obj["track_id"],
                    "x": obj["x"], "y": obj["y"],
                    "width": obj["width"], "height": obj["height"],
                    "confidence": obj["confidence"],
                    "annotation_source": row["annotation_source"],
                    "source_annotation_frame_1based": str(frame),
                    "review_disposition": "SOURCE_GT_COPIED_UNCHANGED_SAMPLED_VISUAL_REVIEW_AMBIGUOUS",
                })

    def write_csv(name: str, data: list[dict[str, str]]) -> Path:
        path = AUDIT / name
        if not data:
            raise SystemExit(f"No rows to write for {name}")
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
        return path

    human_review_count = sum(r["review_status"] == "HUMAN_REVIEWED" for r in review_rows)
    accepted_count = sum(r["review_status"] == "HUMAN_REVIEWED" and r["review_decision"] == "ACCEPT" for r in review_rows)
    phase_complete = human_review_count == len(rows) and accepted_count == len(rows)
    outputs = [
        write_csv("phase4_sample_review_signed.csv", review_rows),
        write_csv("phase4_frame_annotation_crosswalk.csv", crosswalk_rows),
        write_csv("phase4_smot4sb_sampled_gt_unchanged.csv", derived_smot_rows),
    ]
    result = {
        "phase": 4,
        "status": "COMPLETE_FOR_QUICK_AUDIT_SCOPE" if phase_complete else "IMPLEMENTATION_COMPLETE_HUMAN_SIGNOFF_PENDING",
        "queue_rows": len(rows),
        "assistant_visual_review_rows": len(review_rows),
        "human_reviewed_rows": human_review_count,
        "human_accepted_rows": accepted_count,
        "human_reviewers": sorted({r["reviewer"] for r in review_rows if r["reviewer"]}),
        "smot4sb_sample_frames": len([r for r in rows if r["case_id"].startswith("SMOT4SB")]),
        "smot4sb_sampled_gt_boxes_exported_unchanged": len(derived_smot_rows),
        "source_mutation": False,
        "training_or_tuning": False,
        "v1_mixed_scene": "NOT_EVALUATED",
        "v2_multi_drone": "NOT_EVALUATED",
        "independent_v3_holdout": "NOT_EVALUATED",
        "verified_source_and_evidence_hashes": verified_sources,
        "outputs": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(p)} for p in outputs],
        "phase4_complete": phase_complete,
        "completion_note": "Complete for the scoped local quick-audit pilot after recorded human review." if phase_complete else "Sampled audit outputs are reproducible, but Phase 4 remains open until a reviewer records decisions, reviewer name, and date for every row.",
    }
    result_path = AUDIT / "phase4_audit_validation.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    manifest["status"] = result["status"]
    manifest["review_checkpoint"] = {
        "reviewed_by": result["human_reviewers"],
        "date": sorted({r["review_date"] for r in review_rows if r["review_date"]}),
        "review_rows": len(rows),
        "qualified_human_signoff": "RECORDED" if phase_complete else "PARTIAL_OR_PENDING",
        "human_reviewed_rows": human_review_count,
        "human_accepted_rows": accepted_count,
        "smot4sb_sample_boxes_exported_unchanged": len(derived_smot_rows),
        "training_or_tuning_performed": False,
        "source_media_or_labels_modified": False,
        "phase_status": result["status"],
    }
    manifest["human_review_signoff"] = "datasets/processed/phase4_quick_audit/phase4_human_review_signoff.csv"
    manifest["derived_outputs"] = [
        "datasets/processed/phase4_quick_audit/phase4_human_review_signoff.csv",
        "datasets/processed/phase4_quick_audit/phase4_sample_review_signed.csv",
        "datasets/processed/phase4_quick_audit/phase4_frame_annotation_crosswalk.csv",
        "datasets/processed/phase4_quick_audit/phase4_smot4sb_sampled_gt_unchanged.csv",
        "datasets/processed/phase4_quick_audit/phase4_audit_validation.json",
    ]
    manifest["phase4_exit_gates"].update(
        human_review_signed_off=phase_complete,
        derived_audit_ground_truth_written_and_verified=True,
        phase4_complete=phase_complete,
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "queue_rows": len(rows), "human_reviewed": human_review_count, "accepted": accepted_count, "smot_sample_boxes": len(derived_smot_rows), "hashes_verified": len(verified_sources), "validation": str(result_path)}, indent=2))


if __name__ == "__main__":
    main()
