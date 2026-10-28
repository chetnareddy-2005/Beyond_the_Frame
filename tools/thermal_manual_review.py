"""Create blinded thermal frame-review bundles and validate returned annotations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REVIEW_CLASSES = {"Bird", "Drone", "Uncertain"}
REVIEW_STATUSES = {"pending", "reviewed", "needs_adjudication"}
ANNOTATION_FIELDS = (
    "blind_id",
    "source_video_id",
    "source_video_sha256",
    "source_frame_index",
    "timestamp_sec",
    "timestamp_basis",
    "frame_sha256",
    "frame_representation",
    "review_class",
    "reviewer",
    "review_status",
    "evaluation_eligibility",
)
DEVELOPMENT_FIELDS = ("source_sha256", "frame_sha256", "split", "media_type")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_frame(frame: Any) -> str:
    import numpy as np

    contiguous = np.ascontiguousarray(frame)
    digest = hashlib.sha256()
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(contiguous.tobytes())
    return digest.hexdigest()


def _require_sha256(value: str, description: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{description} must be a 64-character SHA-256 digest")
    return normalized


def _timestamp_seconds(capture: Any, frame_index: int, fps: float) -> tuple[float, str]:
    import cv2

    timestamp_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC))
    if math.isfinite(timestamp_ms) and (timestamp_ms > 0 or frame_index == 0):
        return timestamp_ms / 1000, "video_reader_position"
    if math.isfinite(fps) and fps > 0:
        return frame_index / fps, "frame_index_over_fps_estimate"
    raise ValueError(
        f"Cannot establish a timestamp for source frame {frame_index}; "
        "video timestamp and valid FPS are unavailable"
    )


def prepare_review_bundle(
    frame_requests: Iterable[tuple[Path, int]],
    reviewer_bundle: Path,
    curator_manifest: Path,
) -> dict[str, Any]:
    """Export explicit video frames under random IDs and retain provenance privately."""
    import cv2

    requests = [(Path(video).resolve(), frame_index) for video, frame_index in frame_requests]
    if not requests:
        raise ValueError("At least one explicit video/frame selection is required")
    if any(isinstance(index, bool) or index < 0 for _, index in requests):
        raise ValueError("Source frame indexes must be non-negative integers")
    if len(set(requests)) != len(requests):
        raise ValueError("Duplicate source-video/frame selections are not allowed")
    if any(not video.is_file() for video, _ in requests):
        raise FileNotFoundError("Every selected thermal video must exist")

    bundle_path = Path(reviewer_bundle).resolve()
    manifest_path = Path(curator_manifest).resolve()
    if bundle_path == manifest_path or bundle_path in manifest_path.parents:
        raise ValueError("The curator manifest must be outside the reviewer bundle")
    if bundle_path.exists() or manifest_path.exists():
        raise FileExistsError("Reviewer bundle and curator manifest outputs must not exist")
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    private_video_ids: dict[Path, str] = {}
    private_video_hashes: dict[Path, str] = {}
    rows: list[dict[str, Any]] = []
    frame_bytes: list[tuple[str, bytes]] = []
    captures: dict[Path, Any] = {}
    stage_bundle = Path(tempfile.mkdtemp(prefix=".thermal-review-", dir=bundle_path.parent))
    stage_manifest = manifest_path.parent / f".{manifest_path.name}.{secrets.token_hex(8)}.tmp"
    published_bundle = False
    published_manifest = False
    try:
        (stage_bundle / "images").mkdir()
        for video, frame_index in requests:
            if video not in private_video_ids:
                private_video_ids[video] = f"VIDEO-{secrets.token_hex(8)}"
                private_video_hashes[video] = _sha256_file(video)
            capture = captures.get(video)
            if capture is None:
                capture = cv2.VideoCapture(str(video))
                if not capture.isOpened():
                    capture.release()
                    raise RuntimeError(f"Could not open selected thermal video: {video.name}")
                captures[video] = capture
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index):
                raise RuntimeError(f"Could not seek to source frame {frame_index}")
            success, frame = capture.read()
            if not success or frame is None or frame.size == 0:
                raise ValueError(f"Source frame {frame_index} is unavailable in {video.name}")
            actual_next_frame = float(capture.get(cv2.CAP_PROP_POS_FRAMES))
            if math.isfinite(actual_next_frame) and actual_next_frame > 0:
                if round(actual_next_frame) != frame_index + 1:
                    raise RuntimeError(
                        f"Video reader returned an unexpected frame for source index {frame_index}"
                    )
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            timestamp_sec, timestamp_basis = _timestamp_seconds(capture, frame_index, fps)
            success, encoded = cv2.imencode(".png", frame)
            if not success:
                raise RuntimeError(f"Could not encode source frame {frame_index} as PNG")
            blind_id = f"RV-{secrets.token_hex(12)}"
            while any(row["blind_id"] == blind_id for row in rows):
                blind_id = f"RV-{secrets.token_hex(12)}"
            frame_bytes.append((blind_id, encoded.tobytes()))
            rows.append(
                {
                    "blind_id": blind_id,
                    "source_video_id": private_video_ids[video],
                    "source_video_path": str(video),
                    "source_video_name": video.name,
                    "source_video_sha256": private_video_hashes[video],
                    "source_frame_index": frame_index,
                    "timestamp_sec": round(timestamp_sec, 6),
                    "timestamp_basis": timestamp_basis,
                    "frame_sha256": _sha256_frame(frame),
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                }
            )

        for blind_id, content in frame_bytes:
            (stage_bundle / "images" / f"{blind_id}.png").write_bytes(content)
        with (stage_bundle / "annotations_template.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.DictWriter(
                output,
                fieldnames=("blind_id", "reviewer", "review_class", "review_status"),
            )
            writer.writeheader()
            template_rows = [
                {
                    "blind_id": row["blind_id"],
                    "reviewer": "",
                    "review_class": "",
                    "review_status": "pending",
                }
                for row in rows
            ]
            secrets.SystemRandom().shuffle(template_rows)
            writer.writerows(template_rows)
        (stage_bundle / "review_instructions.txt").write_text(
            "Review each image independently without consulting filenames, adjacent frames, "
            "model outputs, or model-predicted labels. Classify only the visible object of "
            "interest: Bird, Drone, or Uncertain. Use Uncertain when the object is not "
            "visible, ambiguous, occluded, or cannot be distinguished from the image. "
            "Set review_status to reviewed or needs_adjudication and enter your reviewer "
            "identifier. Leave pending rows untouched until reviewed. The blind IDs are "
            "random and do not encode source order.\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "evaluation_eligibility": "exploratory_not_independent",
            "frame_representation": (
                "unmodified OpenCV-decoded source frame encoded as PNG; "
                "no model preprocessing, annotations, or inference"
            ),
            "eligibility_reason": (
                "Existing clip ground truth is absent and checkpoint training lineage is "
                "unknown; video and image leakage must be checked against a complete "
                "development hash index."
            ),
            "sampling_method": "curator_explicit_frame_selection",
            "review_bundle": str(bundle_path),
            "records": rows,
        }
        stage_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(stage_bundle, bundle_path)
        published_bundle = True
        os.replace(stage_manifest, manifest_path)
        published_manifest = True
        return manifest
    except Exception:
        if published_bundle:
            shutil.rmtree(bundle_path)
        if published_manifest:
            manifest_path.unlink(missing_ok=True)
        raise
    finally:
        for capture in captures.values():
            capture.release()
        if stage_bundle.exists():
            shutil.rmtree(stage_bundle)
        if stage_manifest.exists():
            stage_manifest.unlink()


def import_annotations(
    curator_manifest: Path,
    reviewer_annotations: Path,
    output_path: Path,
) -> list[dict[str, Any]]:
    manifest = json.loads(Path(curator_manifest).read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("evaluation_eligibility") != "exploratory_not_independent"
        or not manifest.get("frame_representation")
    ):
        raise ValueError("Curator manifest has an unsupported or unsafe evaluation status")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("Curator manifest must contain review records")
    if any(not isinstance(row, dict) for row in records):
        raise ValueError("Curator manifest contains an invalid record")
    expected = {row.get("blind_id"): row for row in records}
    if (
        None in expected
        or any(not isinstance(blind_id, str) or not blind_id for blind_id in expected)
        or len(expected) != len(records)
    ):
        raise ValueError("Curator manifest has missing or duplicate blind IDs")
    for blind_id, provenance in expected.items():
        if not provenance.get("source_video_id"):
            raise ValueError(f"Curator provenance is missing a source video ID for {blind_id}")
        _require_sha256(
            provenance.get("source_video_sha256", ""),
            f"Curator source video hash for {blind_id}",
        )
        _require_sha256(
            provenance.get("frame_sha256", ""),
            f"Curator frame hash for {blind_id}",
        )
        source_frame_index = provenance.get("source_frame_index")
        timestamp_sec = provenance.get("timestamp_sec")
        if (
            isinstance(source_frame_index, bool)
            or not isinstance(source_frame_index, int)
            or source_frame_index < 0
        ):
            raise ValueError(f"Curator source frame index is invalid for {blind_id}")
        if (
            isinstance(timestamp_sec, bool)
            or not isinstance(timestamp_sec, (int, float))
            or not math.isfinite(timestamp_sec)
            or timestamp_sec < 0
        ):
            raise ValueError(f"Curator timestamp is invalid for {blind_id}")
        if not provenance.get("timestamp_basis"):
            raise ValueError(f"Curator timestamp basis is missing for {blind_id}")
    with Path(reviewer_annotations).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"blind_id", "reviewer", "review_class", "review_status"}
        fieldnames = reader.fieldnames or []
        if not required.issubset(fieldnames) or any(
            fieldnames.count(field) != 1 for field in required
        ):
            raise ValueError("Reviewer annotations are missing required columns")
        submitted = list(reader)
    seen: set[str] = set()
    output_rows = []
    for annotation in submitted:
        if any(annotation.get(field) is None for field in required):
            raise ValueError("Reviewer annotation row is missing required values")
        blind_id = (annotation.get("blind_id") or "").strip()
        if blind_id not in expected:
            raise ValueError(f"Unknown blind ID: {blind_id or '<empty>'}")
        if blind_id in seen:
            raise ValueError(f"Duplicate annotation for blind ID {blind_id}")
        seen.add(blind_id)
        reviewer = (annotation.get("reviewer") or "").strip()
        review_class = (annotation.get("review_class") or "").strip()
        review_status = (annotation.get("review_status") or "").strip()
        if review_status not in REVIEW_STATUSES:
            raise ValueError(f"Invalid review status for {blind_id}")
        if review_status == "pending":
            if reviewer or review_class:
                raise ValueError(f"Pending annotation {blind_id} must not contain a reviewer or class")
        else:
            if not reviewer:
                raise ValueError(f"Reviewed annotation {blind_id} requires a reviewer")
            if review_class not in REVIEW_CLASSES:
                raise ValueError(f"Invalid review class for {blind_id}")
        provenance = expected[blind_id]
        output_rows.append(
            {
                "blind_id": blind_id,
                "source_video_id": provenance["source_video_id"],
                "source_video_sha256": provenance["source_video_sha256"],
                "source_frame_index": provenance["source_frame_index"],
                "timestamp_sec": provenance["timestamp_sec"],
                "timestamp_basis": provenance["timestamp_basis"],
                "frame_sha256": provenance["frame_sha256"],
                "frame_representation": manifest["frame_representation"],
                "review_class": review_class,
                "reviewer": reviewer,
                "review_status": review_status,
                "evaluation_eligibility": manifest["evaluation_eligibility"],
            }
        )
    missing = set(expected) - seen
    if missing:
        raise ValueError(f"Missing annotations for {len(missing)} blind ID(s)")
    destination = Path(output_path)
    if destination.exists():
        raise FileExistsError("Annotation output must not overwrite an existing file")
    reviewer_bundle = manifest.get("review_bundle")
    if reviewer_bundle:
        bundle_path = Path(reviewer_bundle).resolve()
        destination_path = destination.resolve()
        if destination_path == bundle_path or bundle_path in destination_path.parents:
            raise ValueError("Imported annotations must be stored outside the reviewer bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=ANNOTATION_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    return output_rows


def build_development_index(
    media_items: Iterable[tuple[Path, str, str]],
    output_path: Path,
) -> int:
    """Hash selected development videos/images and decoded pixels without inference."""
    import cv2

    items = [
        (Path(path).resolve(), split.strip(), media_type)
        for path, split, media_type in media_items
    ]
    if not items:
        raise ValueError("At least one development video or image is required")
    if any(split not in {"train", "validation", "test", "development"} for _, split, _ in items):
        raise ValueError("Development split must be train, validation, test, or development")
    if any(media_type not in {"video", "image"} for _, _, media_type in items):
        raise ValueError("Development media type must be video or image")
    if any(not path.is_file() for path, _, _ in items):
        raise FileNotFoundError("Every indexed development media file must exist")
    destination = Path(output_path)
    if destination.exists():
        raise FileExistsError("Development index must not overwrite an existing file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    temporary_path = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=DEVELOPMENT_FIELDS)
            writer.writeheader()
            for path, split, media_type in items:
                source_hash = _sha256_file(path)
                if media_type == "image":
                    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                    if image is None or image.size == 0:
                        raise ValueError(f"Could not decode development image: {path.name}")
                    writer.writerow(
                        {
                            "source_sha256": source_hash,
                            "frame_sha256": _sha256_frame(image),
                            "split": split,
                            "media_type": media_type,
                        }
                    )
                    row_count += 1
                    continue
                capture = cv2.VideoCapture(str(path))
                if not capture.isOpened():
                    capture.release()
                    raise RuntimeError(f"Could not open development video: {path.name}")
                expected_raw = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                expected_frames = (
                    round(expected_raw)
                    if math.isfinite(expected_raw) and expected_raw > 0
                    else None
                )
                decoded = 0
                try:
                    while True:
                        success, frame = capture.read()
                        if not success:
                            break
                        if frame is None or frame.size == 0:
                            raise ValueError(
                                f"Development video contains an invalid frame: {path.name}"
                            )
                        writer.writerow(
                            {
                                "source_sha256": source_hash,
                                "frame_sha256": _sha256_frame(frame),
                                "split": split,
                                "media_type": media_type,
                            }
                        )
                        decoded += 1
                        row_count += 1
                finally:
                    capture.release()
                if decoded == 0:
                    raise ValueError(f"Development video contains no decodable frames: {path.name}")
                if expected_frames is not None and decoded != expected_frames:
                    raise RuntimeError(
                        f"Development video decoded {decoded} of {expected_frames} "
                        f"reported frames: {path.name}"
                    )
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return row_count


def check_leakage(
    annotations_path: Path,
    development_index_path: Path,
) -> dict[str, Any]:
    with Path(annotations_path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not set(ANNOTATION_FIELDS).issubset(reader.fieldnames or ()):
            raise ValueError("Annotation file is missing provenance columns")
        evaluations = list(reader)
    with Path(development_index_path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not set(DEVELOPMENT_FIELDS).issubset(reader.fieldnames or ()):
            raise ValueError("Development index requires video hash, frame hash, and split")
        development = list(reader)
    if not evaluations:
        raise ValueError("Annotation file is empty; leakage cannot be checked")
    if not development:
        raise ValueError("Development index is empty; leakage cannot be checked")
    allowed_splits = {"train", "validation", "test", "development"}
    for index, row in enumerate(development, start=1):
        if any(row.get(field) is None or not row[field].strip() for field in DEVELOPMENT_FIELDS):
            raise ValueError(f"Development row {index} is missing required values")
        if row["split"] not in allowed_splits:
            raise ValueError(f"Development row {index} has an invalid split")
    for index, row in enumerate(evaluations, start=1):
        _require_sha256(row["source_video_sha256"], f"Annotation row {index} source video hash")
        _require_sha256(row["frame_sha256"], f"Annotation row {index} frame hash")
    for index, row in enumerate(development, start=1):
        _require_sha256(row["source_sha256"], f"Development row {index} source hash")
        _require_sha256(row["frame_sha256"], f"Development row {index} frame hash")
        if row["media_type"] not in {"video", "image"}:
            raise ValueError(f"Development row {index} has an invalid media type")

    development_video_hashes = {
        row["source_sha256"].strip().casefold()
        for row in development
        if row["media_type"] == "video"
    }
    development_frame_hashes = {
        row["frame_sha256"].strip().casefold()
        for row in development
        if row["frame_sha256"].strip()
    }
    video_matches = sorted(
        {
            row["source_video_id"]
            for row in evaluations
            if row["source_video_sha256"].strip().casefold() in development_video_hashes
        }
    )
    frame_matches = sorted(
        {
            row["blind_id"]
            for row in evaluations
            if row["frame_sha256"].strip().casefold() in development_frame_hashes
        }
    )
    return {
        "status": "leakage_detected" if video_matches or frame_matches else "no_exact_match_in_supplied_index",
        "matched_source_video_ids": video_matches,
        "matched_blind_ids": frame_matches,
        "development_splits_indexed": sorted({row["split"] for row in development}),
        "independence_established": False,
        "limitation": (
            "This checks exact source-video and decoded-frame hashes only against the "
            "supplied index; it cannot establish independence from unknown checkpoint "
            "training data or detect near-duplicate/cropped content."
        ),
    }


def _parse_frame_request(value: str) -> tuple[Path, int]:
    video, separator, raw_index = value.rpartition("=")
    if not separator or not video:
        raise argparse.ArgumentTypeError("Frame selection must be VIDEO_PATH=ZERO_BASED_FRAME_INDEX")
    try:
        frame_index = int(raw_index)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Frame index must be an integer") from error
    if frame_index < 0:
        raise argparse.ArgumentTypeError("Frame index must be non-negative")
    return Path(video), frame_index


def _parse_development_item(value: str, media_type: str) -> tuple[Path, str, str]:
    path, separator, split = value.rpartition("=")
    if not separator or not path:
        raise argparse.ArgumentTypeError("Development item must be PATH=SPLIT")
    return Path(path), split, media_type


def _expand_development_directories(
    items: Iterable[tuple[Path, str, str]],
    media_type: str,
) -> list[tuple[Path, str, str]]:
    extensions = (
        {".mp4", ".avi", ".mov", ".mkv"}
        if media_type == "video"
        else {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    )
    expanded = []
    for directory, split, _ in items:
        if not directory.is_dir():
            raise NotADirectoryError(f"Development {media_type} directory does not exist: {directory}")
        expanded.extend(
            (path, split, media_type)
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix.casefold() in extensions
        )
    return expanded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Export explicitly selected frames for blinded review")
    prepare.add_argument("--frame", action="append", type=_parse_frame_request, required=True)
    prepare.add_argument("--reviewer-bundle", type=Path, required=True)
    prepare.add_argument("--curator-manifest", type=Path, required=True)
    ingest = commands.add_parser("import", help="Validate reviewer annotations and join private provenance")
    ingest.add_argument("--curator-manifest", type=Path, required=True)
    ingest.add_argument("--reviewer-annotations", type=Path, required=True)
    ingest.add_argument("--output", type=Path, required=True)
    index = commands.add_parser(
        "index-development",
        help="Build an exact source/pixel hash index without model inference",
    )
    index.add_argument(
        "--video",
        action="append",
        type=lambda value: _parse_development_item(value, "video"),
        default=[],
    )
    index.add_argument(
        "--image",
        action="append",
        type=lambda value: _parse_development_item(value, "image"),
        default=[],
    )
    index.add_argument(
        "--video-dir",
        action="append",
        type=lambda value: _parse_development_item(value, "video"),
        default=[],
    )
    index.add_argument(
        "--image-dir",
        action="append",
        type=lambda value: _parse_development_item(value, "image"),
        default=[],
    )
    index.add_argument("--output", type=Path, required=True)
    leakage = commands.add_parser("check-leakage", help="Compare annotations against a development hash index")
    leakage.add_argument("--annotations", type=Path, required=True)
    leakage.add_argument("--development-index", type=Path, required=True)
    leakage.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        result = prepare_review_bundle(
            arguments.frame,
            arguments.reviewer_bundle,
            arguments.curator_manifest,
        )
    elif arguments.command == "import":
        result = import_annotations(
            arguments.curator_manifest,
            arguments.reviewer_annotations,
            arguments.output,
        )
    elif arguments.command == "index-development":
        result = {
            "indexed_rows": build_development_index(
                [
                    *arguments.video,
                    *arguments.image,
                    *_expand_development_directories(arguments.video_dir, "video"),
                    *_expand_development_directories(arguments.image_dir, "image"),
                ],
                arguments.output,
            ),
            "index_path": str(arguments.output.resolve()),
            "leakage_scope": "exact source-file and decoded-pixel SHA-256 only",
        }
    else:
        result = check_leakage(arguments.annotations, arguments.development_index)
        if arguments.output.exists():
            raise FileExistsError("Leakage report must not overwrite an existing file")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if arguments.command == "check-leakage" and result["status"] == "leakage_detected":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
