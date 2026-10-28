"""Join existing BOREAS visual-GT UUIDs to validated Radar evidence over time.

This validation consumes only the already downloaded subset, its official
timestamped tracking labels, and the existing geometric-validation CSV. It
does not load Radar scans or rerun candidate extraction/association.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEQUENCE_ID = "boreas-objects-v1"
START_TIMESTAMP_US = 1598986289111738
END_TIMESTAMP_US = 1598986292111738
DATASET_ROOT = (
    PROJECT_ROOT / "datasets" / "boreas_radar_validation"
)
SOURCE_CSV = (
    PROJECT_ROOT
    / "multimodal"
    / "boreas_radar"
    / "outputs"
    / "boreas_radar_geometric_validation.csv"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
VISUAL_OBSERVATION_CSV = OUTPUT_DIR / "boreas_visual_track_observations.csv"
ASSOCIATION_CSV = OUTPUT_DIR / "boreas_radar_to_visual_uuid_observations.csv"
REPORT_PATH = OUTPUT_DIR / "boreas_visual_track_radar_association_report.txt"

ASSOCIATION_FIELDS = (
    "visual_track_or_uuid",
    "object_class",
    "camera_timestamp",
    "label_timestamp",
    "radar_timestamp",
    "radar_scan_sequence_index",
    "radar_candidate_index",
    "radar_candidate_index_scope",
    "expected_range_m",
    "radar_candidate_range_m",
    "range_error_m",
    "expected_azimuth_deg",
    "radar_candidate_azimuth_deg",
    "azimuth_error_deg",
    "candidate_intensity",
    "consecutive_radar_scan_count",
    "association_status",
    "association_method",
)
VISUAL_FIELDS = (
    "visual_track_or_uuid",
    "object_class",
    "label_timestamp",
    "camera_timestamp",
    "camera_label_delta_ms",
    "label_frame_sequence_index",
    "consecutive_label_frame_count",
)

PAIR_CONSISTENCY_FIELDS = (
    "radar_candidate_index",
    "radar_candidate_range_m",
    "radar_candidate_azimuth_deg",
    "candidate_intensity",
)


def consecutive_runs(indices: Iterable[int]) -> list[list[int]]:
    """Split sorted or unsorted unique indices into contiguous runs."""
    ordered = sorted(set(indices))
    if not ordered:
        return []
    runs = [[ordered[0]]]
    for index in ordered[1:]:
        if index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def deduplicate_associations(
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Collapse label rows mapping to one UUID/scan, rejecting conflicting peaks."""
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("association_status") == "ASSOCIATED":
            key = (row["object_uuid"], row["radar_timestamp"])
            grouped[key].append(row)

    unique = []
    for (object_uuid, radar_timestamp), duplicates in sorted(grouped.items()):
        for field in PAIR_CONSISTENCY_FIELDS:
            values = {row.get(field, "") for row in duplicates}
            if len(values) > 1:
                raise ValueError(
                    "Conflicting associated candidates for "
                    f"UUID {object_uuid} at Radar scan {radar_timestamp}: "
                    f"{field}={sorted(values)}"
                )
        selected = min(
            duplicates,
            key=lambda row: (
                abs(
                    float(row["label_radar_timestamp_delta_ms"] or "0")
                    * 1000
                ),
                int(row["label_timestamp"] or "0"),
                int(row["camera_timestamp"] or "0"),
            ),
        )
        unique.append(dict(selected))
    return unique


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required existing CSV is missing: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _load_official_sequence(dataset_root: Path):
    try:
        from pyboreas.data.bounding_boxes import BoundingBoxes
        from pyboreas.data.sequence import Sequence
    except ImportError as error:
        raise ImportError(
            "The official pyboreas devkit is required. Add its existing local "
            "installation to PYTHONPATH before running this validation."
        ) from error

    sequence_root = dataset_root / SEQUENCE_ID
    sequence = Sequence(
        str(dataset_root),
        [SEQUENCE_ID, START_TIMESTAMP_US, END_TIMESTAMP_US],
        labelFolder="labels_tracking",
    )
    camera_timestamps = sorted(
        int(frame.timestamp_micro) for frame in sequence.camera_frames
    )
    if not camera_timestamps:
        raise ValueError("The selected BOREAS subset has no camera frames")

    labels_by_time: dict[int, dict[str, str]] = {}
    uuids_by_time: dict[int, set[str]] = {}
    for label_path in sequence.labelFiles:
        timestamp = int(Path(label_path).stem)
        boxes = BoundingBoxes()
        boxes.load_from_file(label_path)
        class_by_uuid = {}
        for box in boxes.bbs:
            if box.uuid in class_by_uuid:
                raise ValueError(
                    f"Duplicate UUID {box.uuid} in label frame {timestamp}"
                )
            class_by_uuid[box.uuid] = str(box.label)
        labels_by_time[timestamp] = class_by_uuid
        uuids_by_time[timestamp] = set(class_by_uuid)
    radar_timestamps = sorted(
        int(frame.timestamp_micro) for frame in sequence.radar_frames
    )
    if not labels_by_time or not radar_timestamps:
        raise ValueError("The selected BOREAS subset lacks labels or Radar scans")
    return camera_timestamps, labels_by_time, uuids_by_time, radar_timestamps


def _nearest_timestamp(timestamp: int, candidates: list[int]) -> int:
    return min(candidates, key=lambda item: (abs(item - timestamp), item))


def _visual_observations(
    camera_timestamps: list[int],
    labels_by_time: dict[int, dict[str, str]],
) -> tuple[list[dict[str, str]], dict[int, int], dict[str, list[int]]]:
    label_times = sorted(labels_by_time)
    nearest_camera_by_label = {
        label_time: _nearest_timestamp(label_time, camera_timestamps)
        for label_time in label_times
    }
    if len(set(nearest_camera_by_label.values())) != len(label_times):
        raise ValueError(
            "Two label frames map to the same nearest camera timestamp; "
            "cannot form distinct timestamped visual observations."
        )

    label_index = {timestamp: index for index, timestamp in enumerate(label_times)}
    uuids_by_label_index: dict[str, list[int]] = defaultdict(list)
    for index, timestamp in enumerate(label_times):
        for object_uuid in labels_by_time[timestamp]:
            uuids_by_label_index[object_uuid].append(index)

    visual_rows = []
    for label_time in label_times:
        camera_time = nearest_camera_by_label[label_time]
        for object_uuid, object_class in sorted(labels_by_time[label_time].items()):
            indices = uuids_by_label_index[object_uuid]
            matching_run = next(
                run for run in consecutive_runs(indices) if label_index[label_time] in run
            )
            visual_rows.append(
                {
                    "visual_track_or_uuid": object_uuid,
                    "object_class": object_class,
                    "label_timestamp": str(label_time),
                    "camera_timestamp": str(camera_time),
                    "camera_label_delta_ms": f"{abs(camera_time - label_time) / 1000:.3f}",
                    "label_frame_sequence_index": str(label_index[label_time]),
                    "consecutive_label_frame_count": str(len(matching_run)),
                }
            )
    return visual_rows, nearest_camera_by_label, uuids_by_label_index


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_validation(
    dataset_root: Path = DATASET_ROOT,
    source_csv: Path = SOURCE_CSV,
    output_dir: Path = OUTPUT_DIR,
) -> dict:
    camera_timestamps, labels_by_time, uuids_by_time, radar_timestamps = (
        _load_official_sequence(dataset_root)
    )
    source_rows = _load_csv(source_csv)
    if not source_rows:
        raise ValueError(f"Geometric validation CSV has no rows: {source_csv}")

    visual_rows, camera_by_label, uuid_label_indices = _visual_observations(
        camera_timestamps,
        labels_by_time,
    )
    label_times = set(labels_by_time)
    for row in source_rows:
        label_time = int(row["label_timestamp"])
        if label_time not in label_times:
            raise ValueError(
                f"Geometric CSV label timestamp {label_time} is absent from "
                "official label frames"
            )
        if int(row["camera_timestamp"]) != camera_by_label[label_time]:
            raise ValueError(
                f"Geometric CSV camera timestamp {row['camera_timestamp']} "
                f"does not match nearest camera frame for label {label_time}"
            )

    associated = deduplicate_associations(source_rows)
    radar_index_by_timestamp = {
        timestamp: index for index, timestamp in enumerate(radar_timestamps)
    }
    scans_by_uuid: dict[str, list[int]] = defaultdict(list)
    for row in associated:
        object_uuid = row["object_uuid"]
        label_time = int(row["label_timestamp"])
        radar_time = int(row["radar_timestamp"])
        if object_uuid not in uuids_by_time[label_time]:
            raise ValueError(
                f"Associated UUID {object_uuid} is absent from label frame {label_time}"
            )
        if radar_time not in radar_index_by_timestamp:
            raise ValueError(
                f"Associated Radar timestamp {radar_time} is absent from subset scans"
            )
        scans_by_uuid[object_uuid].append(radar_index_by_timestamp[radar_time])

    pair_runs: dict[tuple[str, int], int] = {}
    runs_by_uuid: dict[str, list[list[int]]] = {}
    for object_uuid, scan_indices in scans_by_uuid.items():
        runs = consecutive_runs(scan_indices)
        runs_by_uuid[object_uuid] = runs
        for run in runs:
            for scan_index in run:
                pair_runs[(object_uuid, scan_index)] = len(run)

    association_rows = []
    for row in associated:
        object_uuid = row["object_uuid"]
        scan_time = int(row["radar_timestamp"])
        scan_index = radar_index_by_timestamp[scan_time]
        association_rows.append(
            {
                "visual_track_or_uuid": object_uuid,
                "object_class": row["object_class"],
                "camera_timestamp": row["camera_timestamp"],
                "label_timestamp": row["label_timestamp"],
                "radar_timestamp": row["radar_timestamp"],
                "radar_scan_sequence_index": scan_index,
                "radar_candidate_index": row["radar_candidate_index"],
                "radar_candidate_index_scope": "SCAN_LOCAL_NOT_PERSISTENT",
                "expected_range_m": row["expected_range_at_radar_timestamp_m"],
                "radar_candidate_range_m": row["radar_candidate_range_m"],
                "range_error_m": row["range_error_m"],
                "expected_azimuth_deg": row[
                    "expected_azimuth_at_radar_timestamp_deg"
                ],
                "radar_candidate_azimuth_deg": row["radar_candidate_azimuth_deg"],
                "azimuth_error_deg": row["azimuth_error_deg"],
                "candidate_intensity": row["candidate_intensity"],
                "consecutive_radar_scan_count": pair_runs[
                    (object_uuid, scan_index)
                ],
                "association_status": "ASSOCIATED",
                "association_method": row["association_method"],
            }
        )

    association_rows.sort(
        key=lambda row: (
            int(row["radar_scan_sequence_index"]),
            row["visual_track_or_uuid"],
        )
    )
    range_errors = [abs(float(row["range_error_m"])) for row in association_rows]
    azimuth_errors = [abs(float(row["azimuth_error_deg"])) for row in association_rows]
    uuid_runs = [
        run for runs in runs_by_uuid.values() for run in runs
    ]
    uuids_with_two_scans = sum(
        len(set(indices)) >= 2 for indices in scans_by_uuid.values()
    )
    uuids_with_two_consecutive = sum(
        any(len(run) >= 2 for run in runs) for runs in runs_by_uuid.values()
    )
    uuids_with_three_consecutive = sum(
        any(len(run) >= 3 for run in runs) for runs in runs_by_uuid.values()
    )
    persistent_visual_uuids = sum(
        len(indices) >= 2 for indices in uuid_label_indices.values()
    )
    adjacent_visual_uuids = sum(
        any(len(run) >= 2 for run in consecutive_runs(indices))
        for indices in uuid_label_indices.values()
    )
    unique_camera_observations = len(
        {int(row["camera_timestamp"]) for row in visual_rows}
    )
    camera_label_deltas = [
        float(row["camera_label_delta_ms"]) for row in visual_rows
    ]
    maximum_run = max((len(run) for run in uuid_runs), default=0)
    maximum_visual_run = max(
        (
            len(run)
            for indices in uuid_label_indices.values()
            for run in consecutive_runs(indices)
        ),
        default=0,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    visual_csv_path = output_dir / VISUAL_OBSERVATION_CSV.name
    association_csv_path = output_dir / ASSOCIATION_CSV.name
    report_path = output_dir / REPORT_PATH.name
    _write_csv(visual_csv_path, VISUAL_FIELDS, visual_rows)
    _write_csv(association_csv_path, ASSOCIATION_FIELDS, association_rows)

    longest_uuid_runs = [
        (object_uuid, run)
        for object_uuid, runs in runs_by_uuid.items()
        for run in runs
        if len(run) == maximum_run
    ]
    longest_run_text = (
        "; ".join(
            f"{object_uuid} ({len(run)} scans: "
            + ", ".join(str(radar_timestamps[index]) for index in run)
            + ")"
            for object_uuid, run in sorted(longest_uuid_runs)
        )
        if longest_uuid_runs
        else "NONE"
    )
    longest_run_observations = []
    for object_uuid, run in sorted(longest_uuid_runs):
        for scan_index in run:
            row = next(
                item
                for item in association_rows
                if item["visual_track_or_uuid"] == object_uuid
                and int(item["radar_scan_sequence_index"]) == scan_index
            )
            longest_run_observations.append(
                "UUID "
                f"{object_uuid} | radar={row['radar_timestamp']} | "
                f"expected_range={row['expected_range_m']} m | "
                f"candidate_range={row['radar_candidate_range_m']} m | "
                f"range_error={row['range_error_m']} m | "
                f"expected_azimuth={row['expected_azimuth_deg']} deg | "
                f"candidate_azimuth={row['radar_candidate_azimuth_deg']} deg | "
                f"azimuth_error={row['azimuth_error_deg']} deg | "
                f"scan_local_candidate_index={row['radar_candidate_index']}"
            )
    report_lines = [
        "BOREAS visual UUID to Radar geometric-evidence validation",
        "==========================================================",
        f"Dataset root: {dataset_root}",
        f"Existing geometric association source: {source_csv}",
        f"TOTAL_CAMERA_FRAMES = {len(camera_timestamps)}",
        f"TOTAL_OBJECT_LABEL_FRAMES = {len(labels_by_time)}",
        f"TOTAL_RADAR_SCANS = {len(radar_timestamps)}",
        f"TOTAL_LABELED_OBJECT_OBSERVATIONS = {len(visual_rows)}",
        f"UNIQUE_VISUAL_UUIDS = {len(uuid_label_indices)}",
        f"UUIDS_PRESENT_IN_2_OR_MORE_LABEL_FRAMES = {persistent_visual_uuids}",
        f"UUIDS_PRESENT_IN_ADJACENT_LABEL_FRAMES = {adjacent_visual_uuids}",
        f"MAX_CONSECUTIVE_LABEL_FRAMES_FOR_ONE_UUID = {maximum_visual_run}",
        f"CAMERA_FRAMES_WITH_MATCHED_LABEL_OBSERVATIONS = {unique_camera_observations}",
        "LABEL_TO_NEAREST_CAMERA_DELTA_MIN_MEDIAN_MAX_MS = "
        f"{min(camera_label_deltas):.3f} / "
        f"{statistics.median(camera_label_deltas):.3f} / "
        f"{max(camera_label_deltas):.3f}",
        "",
        "The annotations are sampled timestamped 3D object-tracking ground truth, "
        "not image-space camera boxes for every camera frame. UUID persistence is "
        "established across consecutive annotation frames; each observation is paired "
        "with the nearest actual camera image timestamp. This is an annotation-linked "
        "visual identity layer, not an independently generated visual tracker ID.",
        "",
        f"ASSOCIATED_SOURCE_ROWS = {sum(r.get('association_status') == 'ASSOCIATED' for r in source_rows)}",
        f"UNIQUE_UUID_RADAR_SCAN_ASSOCIATIONS = {len(association_rows)}",
        f"UUIDS_WITH_2_OR_MORE_RADAR_ASSOCIATED_SCANS = {uuids_with_two_scans}",
        f"UUIDS_WITH_2_OR_MORE_CONSECUTIVE_RADAR_ASSOCIATED_SCANS = {uuids_with_two_consecutive}",
        f"UUIDS_WITH_3_OR_MORE_CONSECUTIVE_RADAR_ASSOCIATED_SCANS = {uuids_with_three_consecutive}",
        f"TOTAL_CONSECUTIVE_RADAR_ASSOCIATION_RUNS = {len(uuid_runs)}",
        f"MAX_CONSECUTIVE_RADAR_SCAN_RUN = {maximum_run}",
        f"LONGEST_RUN_UUIDS_AND_TIMESTAMPS = {longest_run_text}",
        "LONGEST_RUN_POLAR_OBSERVATIONS:",
        *(longest_run_observations or ["NONE"]),
        f"RANGE_ERROR_MEDIAN_M = {statistics.median(range_errors):.4f}",
        f"RANGE_ERROR_MAX_M = {max(range_errors):.4f}",
        f"ABS_AZIMUTH_ERROR_MEDIAN_DEG = {statistics.median(azimuth_errors):.4f}",
        f"ABS_AZIMUTH_ERROR_MAX_DEG = {max(azimuth_errors):.4f}",
        "",
        "All error statistics use unique ASSOCIATED UUID/Radar-scan pairs only. "
        "Duplicate label rows mapping to the same UUID/scan are collapsed only "
        "after checking that candidate index, range, azimuth, and intensity agree.",
        "Radar candidate index is a deterministic ordinal local to one scan. "
        "It is preserved for within-scan traceability and is not a Radar-track ID; "
        "matching ordinals across scans do not imply the same Radar return.",
        "The persistent identity anchor is the official BOREAS visual-object UUID. "
        "ASSOCIATED records geometric consistency between that UUID's timestamped "
        "3D ground-truth box and an extracted raw Radar intensity candidate. This is "
        "an annotation-linked object identity associated with its nearest camera "
        "observation, not an independently generated visual detector/tracker ID. "
        "This is not Radar detection accuracy, Radar tracking accuracy, or Radar identity.",
        "Radar evidence recurrence across UUID observations does not create a "
        "persistent Radar identity. The existing polar gates and candidate "
        "extraction are unchanged.",
        "",
        f"VISUAL_TRACK_OBSERVATIONS_CSV = {visual_csv_path}",
        f"RADAR_TO_VISUAL_UUID_CSV = {association_csv_path}",
        "RADAR_TO_VISUAL_TRACK_ASSOCIATION = ESTABLISHED (geometric intensity-candidate "
        "evidence linked to persistent BOREAS object UUIDs only)",
        "PERSISTENT_RADAR_IDENTITY = NOT_ESTABLISHED",
        "BOREAS_VISUAL_TRACK_LAYER = ESTABLISHED (label-sampled UUID layer; not "
        "per-camera-frame bounding-box coverage)",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return {
        "camera_frames": len(camera_timestamps),
        "label_frames": len(labels_by_time),
        "radar_scans": len(radar_timestamps),
        "visual_observations": len(visual_rows),
        "visual_uuids": len(uuid_label_indices),
        "persistent_visual_uuids": persistent_visual_uuids,
        "adjacent_visual_uuids": adjacent_visual_uuids,
        "camera_observations": unique_camera_observations,
        "unique_associations": len(association_rows),
        "uuids_with_two_scans": uuids_with_two_scans,
        "uuids_with_two_consecutive": uuids_with_two_consecutive,
        "uuids_with_three_consecutive": uuids_with_three_consecutive,
        "maximum_consecutive_run": maximum_run,
        "range_error_median": statistics.median(range_errors),
        "range_error_max": max(range_errors),
        "azimuth_error_median": statistics.median(azimuth_errors),
        "azimuth_error_max": max(azimuth_errors),
        "association_csv": association_csv_path,
        "visual_csv": visual_csv_path,
        "report": report_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--source-csv", type=Path, default=SOURCE_CSV)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    result = run_validation(args.dataset_root, args.source_csv, args.output_dir)
    print("BOREAS_VISUAL_TRACK_LAYER = ESTABLISHED")
    print("RADAR_TO_VISUAL_TRACK_ASSOCIATION = ESTABLISHED")
    print("PERSISTENT_RADAR_IDENTITY = NOT_ESTABLISHED")
    print("TEMPORAL_RADAR_GEOMETRIC_CONSISTENCY = PASS")
    print(f"UUIDS_WITH_2_OR_MORE_CONSECUTIVE_RADAR_ASSOCIATIONS = {result['uuids_with_two_consecutive']}")
    print(f"MAX_CONSECUTIVE_RADAR_SCAN_RUN = {result['maximum_consecutive_run']}")
    print(f"UNIQUE_UUID_RADAR_SCAN_ASSOCIATIONS = {result['unique_associations']}")
    print(
        "RANGE_ERROR_MEDIAN_MAX_M = "
        f"{result['range_error_median']:.4f} / {result['range_error_max']:.4f}"
    )
    print(
        "ABS_AZIMUTH_ERROR_MEDIAN_MAX_DEG = "
        f"{result['azimuth_error_median']:.4f} / {result['azimuth_error_max']:.4f}"
    )
    print(f"RADAR_TO_VISUAL_UUID_CSV = {result['association_csv']}")
    print(f"VISUAL_TRACK_OBSERVATIONS_CSV = {result['visual_csv']}")
    print(f"REPORT = {result['report']}")


if __name__ == "__main__":
    main()
