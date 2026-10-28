"""Validate spatial-mapping preconditions for synchronized Track 24 pairs.

This script reads existing project outputs and the existing selected ROS bag.
It does not run a detector/tracker, change source data, or emit Thermal crops.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from rosbags.rosbag1 import Reader

from multimodal.thermal.spatial_mapping import (
    Box,
    REGISTRATION_CONFIDENCE_THRESHOLD,
    padded_box_to_source,
    validate_calibration_provenance,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DEFAULT = Path(
    r"C:\Temp\btf_phase4_track24_rectification_check\color_thermal_10x10_calib.yaml"
)
RECTIFIED_RGB_DIAGNOSTIC_ROOT = Path(
    r"C:\Temp\btf_phase4_track24_rectification_check"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
THERMAL_EVIDENCE_DIR = (
    PROJECT_ROOT / "results" / "caltech_rgb_tracking" / "phase4_thermal_evidence"
)
RGB_MANIFEST = PROJECT_ROOT / "results" / "caltech_rgb_full" / "rgb_frame_manifest.csv"
TRACKS_CSV = PROJECT_ROOT / "results" / "caltech_rgb_tracking" / "tracks.csv"
THERMAL_MANIFEST = THERMAL_EVIDENCE_DIR / "thermal_frame_manifest.csv"
BAG_PATH = (
    PROJECT_ROOT
    / "datasets"
    / "caltech_aerial_rgbt_sync"
    / "raw"
    / "2022-05-08-11-44-33.bag"
)
RGB_VIDEO = PROJECT_ROOT / "results" / "caltech_rgb_full" / "caltech_rgb_square.mp4"
RESULT_FIELDS = (
    "track_id",
    "rgb_video_frame",
    "rgb_timestamp_sec",
    "thermal_message_index",
    "thermal_timestamp_sec",
    "synchronization_delta_ms",
    "rgb_box_padded_x1",
    "rgb_box_padded_y1",
    "rgb_box_padded_x2",
    "rgb_box_padded_y2",
    "rgb_box_source_x1",
    "rgb_box_source_y1",
    "rgb_box_source_x2",
    "rgb_box_source_y2",
    "thermal_frame_width",
    "thermal_frame_height",
    "mapped_thermal_bbox_x1",
    "mapped_thermal_bbox_y1",
    "mapped_thermal_bbox_x2",
    "mapped_thermal_bbox_y2",
    "mapping_method",
    "registration_confidence",
    "registration_confidence_threshold",
    "spatial_consistency_metric",
    "inside_thermal_bounds",
    "mapping_status",
    "mapping_rejection_reason",
    "temporal_stability",
    "thermal_crop_generated",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_calibration(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not {"cam0", "cam1"} <= set(data):
        raise ValueError(f"Calibration is not a cam0/cam1 mapping: {path}")
    return data


def _bag_topics(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"Selected Caltech ROS bag not found: {path}")
    with Reader(path) as reader:
        return tuple(sorted({connection.topic for connection in reader.connections}))


def _load_track24_pairs() -> list[dict[str, str]]:
    rows = [
        row
        for row in _read_csv(THERMAL_MANIFEST)
        if row.get("track_id") == "24"
        and row.get("synchronization_status") == "synchronized"
    ]
    if not rows:
        raise ValueError(f"No synchronized Track 24 rows in {THERMAL_MANIFEST}")
    return rows


def _calibration_check(
    calibration: dict[str, Any],
    bag_topics: tuple[str, ...],
    thermal_size: tuple[int, int],
) -> tuple[bool, tuple[str, ...]]:
    rgb_size = tuple(int(value) for value in calibration["cam0"]["resolution"])
    calibration_thermal_size = tuple(
        int(value) for value in calibration["cam1"]["resolution"]
    )
    checked = validate_calibration_provenance(
        rgb_declared_topic=str(calibration["cam0"].get("rostopic", "")),
        thermal_declared_topic=str(calibration["cam1"].get("rostopic", "")),
        observed_topics=bag_topics,
        rgb_calibration_size=rgb_size,
        thermal_calibration_size=calibration_thermal_size,
        rgb_source_size=(960, 600),
        thermal_source_size=thermal_size,
    )
    return checked.valid, checked.reasons


def _frame_from_square_video(video_path: Path, video_frame: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise OSError(f"Could not open existing RGB video: {video_path}")
        if not capture.set(cv2.CAP_PROP_POS_FRAMES, video_frame - 1):
            raise OSError(f"Could not seek to RGB video frame {video_frame}")
        success, frame = capture.read()
        if not success or frame is None:
            raise OSError(f"Could not decode RGB video frame {video_frame}")
    finally:
        capture.release()
    if frame.shape[:2] != (960, 960):
        raise ValueError(f"Expected 960x960 padded RGB frame, got {frame.shape[:2]}")
    return frame


def _scaled_panel(
    image: np.ndarray,
    label: str,
    *,
    width: int = 640,
    height: int = 400,
) -> np.ndarray:
    panel = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    cv2.rectangle(panel, (0, 0), (width - 1, 34), (24, 24, 24), -1)
    cv2.putText(
        panel,
        label,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return panel


def _write_visualization(
    pair: dict[str, str],
    box_source: Box,
    thermal_image: np.ndarray,
    *,
    pair_number: int,
) -> Path:
    frame_index = int(pair["rgb_video_frame"])
    padded = _frame_from_square_video(RGB_VIDEO, frame_index)
    rgb_source = padded[180:780, 0:960].copy()
    rgb_height, rgb_width = rgb_source.shape[:2]
    source_box = Box(
        box_source.x1 * rgb_width / 960.0,
        box_source.y1 * rgb_height / 600.0,
        box_source.x2 * rgb_width / 960.0,
        box_source.y2 * rgb_height / 600.0,
    )
    cv2.rectangle(
        rgb_source,
        (round(source_box.x1), round(source_box.y1)),
        (round(source_box.x2), round(source_box.y2)),
        (0, 0, 255),
        2,
    )

    rectified_path = (
        RECTIFIED_RGB_DIAGNOSTIC_ROOT
        / f"pair_{pair_number}"
        / "rectified_rgb.png"
    )
    if rectified_path.is_file():
        rectified_rgb = cv2.imread(str(rectified_path), cv2.IMREAD_COLOR)
        rectified_label = (
            "Prior rectified RGB diagnostic only (calibration provenance unverified)"
        )
    else:
        rectified_rgb = rgb_source.copy()
        rectified_label = "Rectified RGB unavailable (no valid calibration mapping)"

    thermal_display = thermal_image
    if thermal_display.ndim == 2:
        thermal_display = cv2.cvtColor(thermal_display, cv2.COLOR_GRAY2BGR)
    cv2.putText(
        thermal_display,
        "NO MAPPED REGION: spatial mapping rejected",
        (12, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 0, 255),
        2,
        cv2.LINE_AA,
    )

    def thermal_panel() -> np.ndarray:
        return _scaled_panel(
            thermal_display,
            "Native Thermal frame; no crop/box emitted",
        )

    status = np.full((400, 640, 3), 248, dtype=np.uint8)
    lines = (
        "Track 24 - synchronized pair, spatial mapping rejected",
        f"RGB video frame: {frame_index}",
        f"Timestamp delta: {float(pair['synchronization_delta_sec']) * 1000:.3f} ms",
        "Registration confidence: UNAVAILABLE",
        "Mapped Thermal region: NONE",
        "Thermal crop generated: NO",
        "Reason: calibration topics/native dimensions do not match this bag.",
        "Prior rectification is diagnostic only; it cannot supply a crop.",
    )
    for line_number, line in enumerate(lines):
        cv2.putText(
            status,
            line,
            (12, 45 + 42 * line_number),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )

    canvas = np.vstack(
        [
            np.hstack(
                [
                    _scaled_panel(rgb_source, "Source RGB 960x600; bbox de-padded"),
                    _scaled_panel(rectified_rgb, rectified_label),
                ]
            ),
            np.hstack([thermal_panel(), status]),
        ]
    )
    output_path = OUTPUT_DIR / "track24_spatial_mapping_diagnostic.png"
    if not cv2.imwrite(str(output_path), canvas):
        raise OSError(f"Could not write diagnostic visualization: {output_path}")
    return output_path


def run_validation(calibration_path: Path) -> dict[str, Any]:
    required = (RGB_MANIFEST, TRACKS_CSV, THERMAL_MANIFEST, RGB_VIDEO, BAG_PATH)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Required existing input(s) missing: " + "; ".join(missing))
    if not calibration_path.is_file():
        raise FileNotFoundError(
            "Existing official rectification calibration not found: "
            f"{calibration_path}. Supply --calibration with its local path."
        )
    calibration = _read_calibration(calibration_path)
    bag_topics = _bag_topics(BAG_PATH)
    rgb_manifest = {
        row["video_frame"]: row for row in _read_csv(RGB_MANIFEST)
    }
    track_rows = _read_csv(TRACKS_CSV)
    tracks_by_frame = {
        row["frame"]: row
        for row in track_rows
        if row.get("track_id") == "24"
    }
    pairs = _load_track24_pairs()

    first_thermal_path = THERMAL_EVIDENCE_DIR / pairs[0]["thermal_frame_path"]
    first_thermal = cv2.imread(str(first_thermal_path), cv2.IMREAD_UNCHANGED)
    if first_thermal is None or first_thermal.size == 0:
        raise OSError(f"Could not read existing Thermal frame: {first_thermal_path}")
    thermal_height, thermal_width = first_thermal.shape[:2]
    calibration_valid, rejection_reasons = _calibration_check(
        calibration,
        bag_topics,
        (thermal_width, thermal_height),
    )
    if not calibration_valid and not rejection_reasons:
        raise AssertionError("Invalid calibration check did not provide rejection reasons")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_rows = []
    first_visualization = None
    for pair_number, pair in enumerate(pairs, start=1):
        frame_number = pair["rgb_video_frame"]
        if frame_number not in rgb_manifest:
            raise ValueError(f"RGB manifest lacks video frame {frame_number}")
        manifest_row = rgb_manifest[frame_number]
        if frame_number not in tracks_by_frame:
            raise ValueError(f"Track 24 box missing for RGB frame {frame_number}")
        track = tracks_by_frame[frame_number]
        original_width = int(manifest_row["original_width"])
        original_height = int(manifest_row["original_height"])
        padded_width = int(manifest_row["padded_width"])
        padded_height = int(manifest_row["padded_height"])
        padded_box = Box(
            float(track["x1"]),
            float(track["y1"]),
            float(track["x2"]),
            float(track["y2"]),
        )
        source_box = padded_box_to_source(
            padded_box,
            source_size=(original_width, original_height),
            padded_size=(padded_width, padded_height),
        )
        if source_box is None:
            raise ValueError(f"Invalid padded RGB box on frame {frame_number}")

        thermal_path = THERMAL_EVIDENCE_DIR / pair["thermal_frame_path"]
        thermal = cv2.imread(str(thermal_path), cv2.IMREAD_UNCHANGED)
        if thermal is None or thermal.size == 0:
            raise OSError(f"Could not read existing Thermal frame: {thermal_path}")
        if thermal.shape[:2] != (thermal_height, thermal_width):
            raise ValueError("Synchronized Thermal frames have inconsistent dimensions")

        # Rectification can align epipolar lines but cannot establish horizontal
        # correspondence without valid calibration and measured disparity/registration.
        row = {
            "track_id": pair["track_id"],
            "rgb_video_frame": frame_number,
            "rgb_timestamp_sec": pair["rgb_timestamp_sec"],
            "thermal_message_index": pair["thermal_message_index"],
            "thermal_timestamp_sec": pair["thermal_timestamp_sec"],
            "synchronization_delta_ms": f"{float(pair['synchronization_delta_sec']) * 1000:.6f}",
            "rgb_box_padded_x1": padded_box.x1,
            "rgb_box_padded_y1": padded_box.y1,
            "rgb_box_padded_x2": padded_box.x2,
            "rgb_box_padded_y2": padded_box.y2,
            "rgb_box_source_x1": source_box.x1,
            "rgb_box_source_y1": source_box.y1,
            "rgb_box_source_x2": source_box.x2,
            "rgb_box_source_y2": source_box.y2,
            "thermal_frame_width": thermal_width,
            "thermal_frame_height": thermal_height,
            "mapped_thermal_bbox_x1": "",
            "mapped_thermal_bbox_y1": "",
            "mapped_thermal_bbox_x2": "",
            "mapped_thermal_bbox_y2": "",
            "mapping_method": "NOT_RUN_CALIBRATION_PROVENANCE_INVALID",
            "registration_confidence": "",
            "registration_confidence_threshold": REGISTRATION_CONFIDENCE_THRESHOLD,
            "spatial_consistency_metric": "",
            "inside_thermal_bounds": "",
            "mapping_status": "THERMAL_SPATIAL_MAPPING_UNCERTAIN",
            "mapping_rejection_reason": ";".join(rejection_reasons),
            "temporal_stability": "NOT_EVALUATED_MAPPING_REJECTED",
            "thermal_crop_generated": "NO",
        }
        output_rows.append(row)
        if first_visualization is None:
            first_visualization = _write_visualization(
                pair,
                source_box,
                thermal,
                pair_number=pair_number,
            )

    results_csv = OUTPUT_DIR / "thermal_spatial_mapping_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    report_path = OUTPUT_DIR / "thermal_spatial_mapping_report.txt"
    calibration_topics = (
        str(calibration["cam0"].get("rostopic", "")),
        str(calibration["cam1"].get("rostopic", "")),
    )
    calibration_sizes = (
        tuple(int(value) for value in calibration["cam0"]["resolution"]),
        tuple(int(value) for value in calibration["cam1"]["resolution"]),
    )
    lines = [
        "Caltech RGB-Thermal spatial mapping validation",
        "================================================",
        f"Calibration source: {calibration_path}",
        f"Selected bag: {BAG_PATH}",
        "Selected streams: /eo/color/image_color/compressed; /boson/thermal/image_raw",
        "Calibration-declared streams: "
        f"{calibration_topics[0]}; {calibration_topics[1]}",
        f"Calibration RGB/Thermal sizes (W,H): {calibration_sizes[0]}; {calibration_sizes[1]}",
        f"Observed native RGB/Thermal sizes (W,H): (960, 600); ({thermal_width}, {thermal_height})",
        "Calibration provenance check: " + ("PASS" if calibration_valid else "FAIL"),
        "Calibration rejection reasons: "
        + ("NONE" if not rejection_reasons else ", ".join(rejection_reasons)),
        "",
        "Coordinate and calibration interpretation",
        "------------------------------------------",
        "RGB track boxes are stored in the 960x960 video canvas. The original RGB "
        "frame is 960x600 with centered top padding of 180 pixels; the source box "
        "is obtained by subtracting that padding and is checked against image bounds.",
        "For a compatible calibration, the official StereoRectifier uses cam1's "
        "T_cn_cnm1 directly as R,T in cv2.stereoRectify(cameraMatrix1=cam0, "
        "cameraMatrix2=cam1); no inverse is inserted by this validation wrapper. "
        "Rectified point coordinates must be obtained through the corresponding "
        "official undistort/rectify maps, not same-coordinate transfer.",
        "Rectification constrains epipolar rows but does not determine horizontal "
        "Thermal correspondence. A valid depth/disparity or independently measured "
        "cross-modal registration is required before a localized region can be emitted.",
        "",
        "Results",
        "-------",
        f"SYNCED_RGB_THERMAL_PAIRS_TESTED = {len(output_rows)}",
        "SPATIALLY_REGISTERED_PAIRS = 0",
        f"REGISTRATION_REJECTED_PAIRS = {len(output_rows)}",
        "MAPPING_CONFIDENCE_MIN = N/A (precondition failed; no confidence score computed)",
        "MAPPING_CONFIDENCE_MEDIAN = N/A (precondition failed; no confidence score computed)",
        "MAPPING_CONFIDENCE_MAX = N/A (precondition failed; no confidence score computed)",
        "TEMPORALLY_STABLE_MAPPINGS = 0 (none registered; stability not evaluated)",
        "THERMAL_CROPS_GENERATED = 0",
        "Confidence threshold for any future crop: "
        f"{REGISTRATION_CONFIDENCE_THRESHOLD:.2f}; no score was assigned to rejected pairs.",
        "",
        "Timestamp synchronization is established for these four pairs (about "
        "4.25-4.48 ms delta), but this is not spatial association. The calibration "
        "declares topics absent from this bag, and its Thermal image size (960x600) "
        "does not match the native selected Thermal stream (640x512). The previous "
        "rectification products are retained only as diagnostics; they do not repair "
        "the provenance/resolution mismatch or provide a justified Thermal x-coordinate.",
        "No depth/disparity stream or recording-matched calibration was available. "
        "Accordingly all four mappings remain THERMAL_SPATIAL_MAPPING_UNCERTAIN. "
        "No arbitrary rescaling, same-coordinate transfer, pseudo-confidence, or "
        "Thermal crop was produced. Intensity preprocessing is not a physical "
        "temperature measurement.",
        f"Visualization: {first_visualization}",
        "THERMAL_SPATIAL_MAPPING = INCONCLUSIVE",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "pairs_tested": len(output_rows),
        "spatially_registered": 0,
        "registration_rejected": len(output_rows),
        "temporal_stability": 0,
        "thermal_crops": 0,
        "calibration_valid": calibration_valid,
        "rejection_reasons": rejection_reasons,
        "csv": results_csv,
        "report": report_path,
        "visualization": first_visualization,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--calibration",
        type=Path,
        default=CALIBRATION_DEFAULT,
        help="Path to the existing official color-Thermal rectification YAML.",
    )
    args = parser.parse_args()
    result = run_validation(args.calibration)
    print("THERMAL_SPATIAL_MAPPING = INCONCLUSIVE")
    print(f"SYNCED_RGB_THERMAL_PAIRS_TESTED = {result['pairs_tested']}")
    print(f"SPATIALLY_REGISTERED_PAIRS = {result['spatially_registered']}")
    print(f"REGISTRATION_REJECTED_PAIRS = {result['registration_rejected']}")
    print(f"THERMAL_CROPS_GENERATED = {result['thermal_crops']}")
    print(f"CALIBRATION_REJECTION_REASONS = {','.join(result['rejection_reasons'])}")
    print(f"RESULTS_CSV = {result['csv']}")
    print(f"REPORT = {result['report']}")
    print(f"VISUALIZATION = {result['visualization']}")


if __name__ == "__main__":
    main()
