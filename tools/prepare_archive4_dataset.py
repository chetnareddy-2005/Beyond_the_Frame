from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "archive (4).zip"
TARGET = ROOT / "datasets" / "external" / "drone_bird_archive4_clean"
SPLITS = ("train", "valid", "test")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def sha256_stream(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_detection_label(data: bytes, source_name: str):
    text = data.decode("utf-8-sig", "strict").strip()
    converted = []
    counts = Counter()
    classes = Counter()
    if not text:
        return "", counts, classes
    for line_number, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        try:
            class_value = float(parts[0])
            class_id = int(class_value)
            values = [float(value) for value in parts[1:]]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"{source_name}:{line_number}: invalid numeric label") from exc
        if class_value != class_id or class_id not in (0, 1):
            raise ValueError(f"{source_name}:{line_number}: expected class 0 or 1")
        if len(parts) == 5:
            cx, cy, width, height = values
            if width <= 0 or height <= 0:
                raise ValueError(f"{source_name}:{line_number}: non-positive box")
            if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < width <= 1 and 0 < height <= 1):
                raise ValueError(f"{source_name}:{line_number}: non-normalized box")
            if cx - width / 2 < -1e-6 or cy - height / 2 < -1e-6 or cx + width / 2 > 1 + 1e-6 or cy + height / 2 > 1 + 1e-6:
                raise ValueError(f"{source_name}:{line_number}: box extends beyond image")
            box = (cx, cy, width, height)
            counts["bbox_rows"] += 1
        elif len(parts) >= 7 and len(values) % 2 == 0:
            xs, ys = values[0::2], values[1::2]
            if len(xs) < 3 or any(value < 0 or value > 1 for value in values):
                raise ValueError(f"{source_name}:{line_number}: invalid normalized polygon")
            left, right = min(xs), max(xs)
            top, bottom = min(ys), max(ys)
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                raise ValueError(f"{source_name}:{line_number}: degenerate polygon")
            box = ((left + right) / 2, (top + bottom) / 2, width, height)
            counts["polygon_rows_converted"] += 1
        else:
            raise ValueError(f"{source_name}:{line_number}: unsupported {len(parts)}-token row")
        converted.append(f"{class_id} " + " ".join(f"{value:.8f}" for value in box))
        classes[class_id] += 1
    return "\n".join(converted) + "\n", counts, classes


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Expected source ZIP at {SOURCE}")
    if TARGET.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {TARGET}")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    staging = TARGET.with_name(TARGET.name + ".staging")
    if staging.exists():
        raise FileExistsError(f"Refusing to overwrite staging folder: {staging}")
    staging.mkdir()
    try:
        with zipfile.ZipFile(SOURCE) as archive:
            bad_entry = archive.testzip()
            if bad_entry:
                raise ValueError(f"ZIP CRC check failed at {bad_entry}")
            image_infos = {}
            label_infos = {}
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = Path(info.filename)
                parts = path.parts
                if len(parts) != 4 or parts[0] != "Dataset" or parts[1] not in SPLITS:
                    continue
                split, kind, name = parts[1], parts[2], parts[3]
                if kind == "images" and path.suffix.lower() in IMAGE_SUFFIXES:
                    image_infos[(split, path.stem)] = info
                elif kind == "labels" and path.suffix.lower() == ".txt":
                    label_infos[(split, path.stem)] = info

            if len(image_infos) != 20952 or len(label_infos) != 20952:
                raise ValueError(f"Unexpected source pair counts: {len(image_infos)} images, {len(label_infos)} labels")
            missing_labels = set(image_infos) - set(label_infos)
            orphan_labels = set(label_infos) - set(image_infos)
            if missing_labels or orphan_labels:
                raise ValueError(f"Pair mismatch: {len(missing_labels)} missing labels, {len(orphan_labels)} orphan labels")

            # Use cryptographic digests (not only ZIP CRCs) to identify exact image duplicates.
            duplicate_candidates = defaultdict(list)
            for key, info in image_infos.items():
                duplicate_candidates[(info.CRC, info.file_size)].append((key, info))
            duplicate_groups = []
            for candidates in duplicate_candidates.values():
                if len(candidates) < 2:
                    continue
                by_digest = defaultdict(list)
                for key, info in candidates:
                    with archive.open(info) as stream:
                        by_digest[sha256_stream(stream)].append((key, info))
                duplicate_groups.extend(group for group in by_digest.values() if len(group) > 1)
            excluded = {key for group in duplicate_groups for key, _ in group}

            counters = {split: Counter() for split in SPLITS}
            class_counts = {split: Counter() for split in SPLITS}
            review_rows = []
            duplicate_rows = []
            manifest_rows = []
            for group_number, group in enumerate(duplicate_groups, 1):
                for (split, stem), image_info in group:
                    label_data = archive.read(label_infos[(split, stem)])
                    duplicate_rows.append({
                        "group": group_number,
                        "split": split,
                        "image": image_info.filename,
                        "image_sha256": hashlib.sha256(archive.read(image_info)).hexdigest(),
                        "label_sha256": sha256_bytes(label_data),
                        "reason": "Exact duplicate image; excluded to prevent split leakage and conflicting duplicate labels",
                    })

            from PIL import Image
            for (split, stem), image_info in sorted(image_infos.items()):
                label_info = label_infos[(split, stem)]
                label_data = archive.read(label_info)
                label_text, row_counts, classes = parse_detection_label(label_data, label_info.filename)
                counters[split]["source_images"] += 1
                if not label_text:
                    counters[split]["empty_labels"] += 1
                for class_id, amount in classes.items():
                    class_counts[split][str(class_id)] += amount
                for key, amount in row_counts.items():
                    counters[split][key] += amount

                if split == "test" and stem.upper().startswith("BT"):
                    for row_number, line in enumerate(label_text.splitlines(), 1):
                        class_id = int(line.split()[0])
                        if class_id == 1:
                            review_rows.append({
                                "split": split,
                                "image": f"{stem}{image_info.filename[image_info.filename.rfind('.'):]}",
                                "source_label": label_info.filename,
                                "row": row_number,
                                "class_id": class_id,
                                "in_prepared_test": str((split, stem) not in excluded).lower(),
                                "issue": "BT-prefixed test image contains class 1 (Drone); verify against the image before final test evaluation",
                            })

                if (split, stem) in excluded:
                    counters[split]["excluded_duplicate_images"] += 1
                    continue

                image_rel = Path(split) / "images" / Path(image_info.filename).name
                label_rel = Path(split) / "labels" / f"{stem}.txt"
                image_out = staging / image_rel
                label_out = staging / label_rel
                image_out.parent.mkdir(parents=True, exist_ok=True)
                label_out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(image_info) as source_stream, image_out.open("wb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, 1024 * 1024)
                with Image.open(image_out) as image:
                    image.verify()
                label_out.write_text(label_text, encoding="utf-8", newline="\n")
                counters[split]["included_images"] += 1
                manifest_rows.append({
                    "split": split,
                    "image": image_rel.as_posix(),
                    "label": label_rel.as_posix(),
                    "source_image": image_info.filename,
                    "source_label": label_info.filename,
                    "empty_label": str(not bool(label_text)).lower(),
                    "bbox_rows_kept": row_counts["bbox_rows"],
                    "polygon_rows_converted": row_counts["polygon_rows_converted"],
                })

        (staging / "data.yaml").write_text(
            "path: .\ntrain: train/images\nval: valid/images\ntest: test/images\n\nnc: 2\nnames:\n  0: Bird\n  1: Drone\n",
            encoding="utf-8",
        )
        with (staging / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
            writer.writeheader()
            writer.writerows(manifest_rows)
        with (staging / "excluded_duplicates.csv").open("w", newline="", encoding="utf-8") as f:
            fields = ["group", "split", "image", "image_sha256", "label_sha256", "reason"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(duplicate_rows)
        with (staging / "review_flags.csv").open("w", newline="", encoding="utf-8") as f:
            fields = ["split", "image", "source_label", "row", "class_id", "in_prepared_test", "issue"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(review_rows)

        clean_counts = {split: Counter() for split in SPLITS}
        clean_class_counts = {split: Counter() for split in SPLITS}
        for row in manifest_rows:
            split = row["split"]
            clean_counts[split]["images"] += 1
            clean_counts[split]["bbox_rows"] += int(row["bbox_rows_kept"]) + int(row["polygon_rows_converted"])
            if row["empty_label"] == "true":
                clean_counts[split]["empty_labels"] += 1
            for line in (staging / row["label"]).read_text(encoding="utf-8").splitlines():
                clean_class_counts[split][line.split()[0]] += 1

        summary = {
            "source_zip": str(SOURCE),
            "source_zip_bytes": SOURCE.stat().st_size,
            "source_license": "CC BY 4.0; preserve attribution to the dataset authors/source page",
            "source_page": "https://universe.roboflow.com/drone-3doih/drone-76w4q",
            "class_mapping": {"0": "Bird", "1": "Drone"},
            "conversion": "YOLO segmentation polygons converted to enclosing YOLO detection boxes; existing 5-value boxes preserved",
            "duplicate_policy": "All images in exact SHA-256 duplicate groups were excluded; see excluded_duplicates.csv",
            "test_warning": "review_flags.csv identifies BT-prefixed source test labels using class 1 and states whether each image is retained in the prepared test split; do not treat test metrics as final until manually reviewed",
            "source_counts": {split: dict(counts) for split, counts in counters.items()},
            "source_class_rows": {split: dict(counts) for split, counts in class_counts.items()},
            "counts": {split: dict(counts) for split, counts in clean_counts.items()},
            "class_rows": {split: dict(counts) for split, counts in clean_class_counts.items()},
            "excluded_duplicate_groups": len(duplicate_groups),
            "excluded_duplicate_images": len(excluded),
            "test_review_flags": len(review_rows),
            "included_image_count": len(manifest_rows),
        }
        (staging / "dataset_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (staging / "README.md").write_text(
            "# Drone and Bird dataset candidate (Archive 4)\n\n"
            "Prepared from the supplied Roboflow export. Original archive was not modified.\n\n"
            "## Use\n\n"
            "- `data.yaml` uses the project mapping: class 0 = Bird, class 1 = Drone.\n"
            "- Detection bounding boxes are ready for the project's YOLO detection workflow.\n"
            "- Polygon rows were converted to their enclosing rectangles; existing boxes were kept.\n"
            "- Empty labels are preserved as empty files.\n"
            "- Exact duplicate images are omitted from the prepared splits; review `excluded_duplicates.csv`.\n"
            "- Review `review_flags.csv` before using test metrics. It flags BT-prefixed test images with class 1; labels were not guessed or changed.\n"
            "- Do not train on `test`; use it only after reviewing the flagged annotations.\n\n"
            "## Source and attribution\n\n"
            "Source: https://universe.roboflow.com/drone-3doih/drone-76w4q\n\n"
            "The supplied export states CC BY 4.0. Retain attribution and the license when sharing or uploading this dataset.\n\n"
            "See `dataset_summary.json` and `manifest.csv` for preparation counts and source-file mappings.\n",
            encoding="utf-8",
        )
        os.replace(staging, TARGET)
        print(json.dumps({"output": str(TARGET), **summary}, indent=2))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
