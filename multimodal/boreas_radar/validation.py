"""Run the isolated BOREAS Radar-candidate to visual-GT validation."""

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

import cv2
import numpy as np

try:
    from pyboreas.data.sequence import Sequence
except ModuleNotFoundError:  # pragma: no cover - exercised when the optional devkit is absent.
    Sequence = None

from .association import ProjectedCandidate, associate_candidates
from .candidate_extraction import extract_candidates
from .config import AssociationConfig, CandidateExtractionConfig
from .projection import (
    interpolate_lidar_poses,
    project_candidate_to_camera,
    project_ground_truth_box,
)
from .radar_data import RadarScan


START_TIMESTAMP_US = 1598986289111738
END_TIMESTAMP_US = 1598986292111738
SEQUENCE_ID = "boreas-objects-v1"
CSV_FIELDS = [
    "camera_timestamp",
    "radar_timestamp",
    "timestamp_delta",
    "object_uuid",
    "object_class",
    "object_center_3d",
    "object_bbox_2d",
    "radar_candidate_count",
    "temporally_valid_candidate_count",
    "associated_radar_count",
    "nearest_radar_range_m",
    "nearest_radar_azimuth_deg",
    "association_distance_px",
    "association_status",
    "association_method",
]
METHOD = (
    "polar_intensity_local_maxima; pose-compensated calibrated projection; "
    "projected candidate proximity to official GT bbox (not Radar detection)"
)


def _nearest_radar(camera_timestamp_us: int, radar_frames: list, max_delta_us: int):
    frame = min(
        radar_frames,
        key=lambda item: (abs(item.timestamp_micro - camera_timestamp_us), item.timestamp_micro),
    )
    delta = frame.timestamp_micro - camera_timestamp_us
    if abs(delta) > max_delta_us:
        return None, delta
    return frame, delta


def _box_row(
    camera_frame,
    radar_timestamp_us,
    timestamp_delta_us,
    bounding_box,
    box_status,
    box,
    candidates,
    scan_candidate_count,
    temporal_candidate_count,
    config,
):
    status = box_status
    associated = []
    nearest = None
    distance = None
    if status == "VALID":
        associated, nearest, distance = associate_candidates(
            candidates, box, config.bounding_box_margin_pixels
        )
        status = "ASSOCIATED" if associated else "NO_RADAR_ASSOCIATION"
    if radar_timestamp_us is None:
        status = "TIMESTAMP_MISMATCH"
    camera_center = np.asarray(bounding_box.pos).reshape(3).tolist()
    if box is not None:
        bbox_values = [round(value, 3) for value in box]
    else:
        bbox_values = []
    candidate = nearest.candidate if nearest is not None else None
    return {
        "camera_timestamp": camera_frame.timestamp_micro,
        "radar_timestamp": "" if radar_timestamp_us is None else radar_timestamp_us,
        "timestamp_delta": "" if timestamp_delta_us is None else timestamp_delta_us / 1e6,
        "object_uuid": bounding_box.uuid,
        "object_class": bounding_box.label,
        "object_center_3d": json.dumps([round(value, 4) for value in camera_center]),
        "object_bbox_2d": json.dumps(bbox_values),
        "radar_candidate_count": scan_candidate_count,
        "temporally_valid_candidate_count": temporal_candidate_count,
        "associated_radar_count": len(associated),
        "nearest_radar_range_m": "" if candidate is None else round(candidate.range_m, 4),
        "nearest_radar_azimuth_deg": (
            "" if candidate is None else round(math.degrees(candidate.azimuth_rad), 4)
        ),
        "association_distance_px": "" if distance is None else round(distance, 3),
        "association_status": status,
        "association_method": METHOD,
    }


def _render_visualization(
    image_rgb: np.ndarray,
    boxes: list[tuple[str, tuple[float, float, float, float]]],
    projected_candidates: list[ProjectedCandidate],
    camera_timestamp_us: int,
    radar_timestamp_us: int,
    output_path: Path,
) -> None:
    image = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    for uuid, (x1, y1, x2, y2) in boxes:
        cv2.rectangle(
            image,
            (int(round(x1)), int(round(y1))),
            (int(round(x2)), int(round(y2))),
            (40, 220, 40),
            2,
        )
        cv2.putText(
            image,
            f"GT {uuid[:8]}",
            (int(round(x1)), max(18, int(round(y1)) - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (40, 220, 40),
            1,
            cv2.LINE_AA,
        )
    for item in projected_candidates:
        cv2.circle(
            image,
            (int(round(item.pixel[0])), int(round(item.pixel[1]))),
            4,
            (0, 140, 255),
            -1,
        )
    cv2.putText(
        image,
        "GREEN: projected GT boxes | ORANGE: polar-intensity candidates (not detections)",
        (16, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"camera={camera_timestamp_us} radar={radar_timestamp_us}",
        (16, 52),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), image):
        raise OSError(f"Could not write visualization to {output_path}")


def run_validation(
    dataset_root: Path,
    output_dir: Path,
    candidate_config: CandidateExtractionConfig = CandidateExtractionConfig(),
    association_config: AssociationConfig = AssociationConfig(),
) -> dict:
    if Sequence is None:
        raise ModuleNotFoundError(
            "The optional pyboreas devkit is required for BOREAS validation. "
            "Install asrl-pyboreas and ensure it is on PYTHONPATH."
        )
    sequence = Sequence(
        str(dataset_root),
        [
            SEQUENCE_ID,
            START_TIMESTAMP_US,
            END_TIMESTAMP_US,
        ],
        labelFolder="labels_tracking",
    )
    camera_frames = sequence.camera_frames
    radar_frames = sequence.radar_frames
    if not camera_frames or not radar_frames or not sequence.labelFiles:
        raise ValueError("The selected BOREAS interval is missing required input frames")

    loaded_scans = {}
    scan_candidates = {}
    for scan_index, frame in enumerate(radar_frames):
        scan_frame = sequence.get_radar(scan_index)
        scan = RadarScan.from_pyboreas_frame(scan_frame)
        candidates = extract_candidates(scan, candidate_config)
        loaded_scans[scan.frame_timestamp_us] = scan
        scan_candidates[scan.frame_timestamp_us] = candidates

    camera_matches = []
    max_scan_delta_us = round(association_config.maximum_scan_delta_seconds * 1e6)
    for frame in camera_frames:
        match, delta = _nearest_radar(frame.timestamp_micro, radar_frames, max_scan_delta_us)
        camera_matches.append((frame, match, delta))

    candidate_query_times = [
        candidate.azimuth_timestamp_us
        for candidates in scan_candidates.values()
        for candidate in candidates
    ]
    query_times = candidate_query_times + [
        frame.timestamp_micro for frame, _, _ in camera_matches
    ] + [frame.timestamp_micro for frame in radar_frames]
    lidar_poses = interpolate_lidar_poses(Path(sequence.seq_root), query_times)
    missing_camera_poses = [
        frame.timestamp_micro
        for frame, _, _ in camera_matches
        if frame.timestamp_micro not in lidar_poses
    ]
    if missing_camera_poses:
        raise ValueError(
            "LiDAR pose table does not cover camera timestamps: "
            + ", ".join(str(timestamp) for timestamp in missing_camera_poses)
        )

    T_radar_lidar = np.asarray(sequence.calib.T_radar_lidar, dtype=np.float64)
    T_camera_lidar = np.asarray(sequence.calib.T_camera_lidar, dtype=np.float64)
    P_camera = np.asarray(sequence.calib.P0, dtype=np.float64)
    if any(matrix.shape != (4, 4) for matrix in (T_radar_lidar, T_camera_lidar, P_camera)):
        raise ValueError("Expected 4x4 official BOREAS calibration matrices")

    def pose_residuals(frames, calibration, sensor_name):
        translations = []
        rotations = []
        for frame in frames:
            expected_pose = lidar_poses[frame.timestamp_micro] @ np.linalg.inv(calibration)
            relative_rotation = expected_pose[:3, :3].T @ frame.pose[:3, :3]
            angle_cosine = np.clip((np.trace(relative_rotation) - 1.0) / 2.0, -1.0, 1.0)
            translations.append(
                float(np.linalg.norm(expected_pose[:3, 3] - frame.pose[:3, 3]))
            )
            rotations.append(float(np.arccos(angle_cosine)))
        return {
            f"{sensor_name}_POSE_RESIDUAL_TRANSLATION_MEDIAN_M": statistics.median(
                translations
            ),
            f"{sensor_name}_POSE_RESIDUAL_TRANSLATION_MAX_M": max(translations),
            f"{sensor_name}_POSE_RESIDUAL_ROTATION_MAX_DEG": math.degrees(max(rotations)),
        }

    pose_consistency = {}
    pose_consistency.update(
        pose_residuals(camera_frames, T_camera_lidar, "CAMERA")
    )
    pose_consistency.update(
        pose_residuals(radar_frames, T_radar_lidar, "RADAR")
    )

    result_rows = []
    all_projected_by_camera = {}
    visible_gt_by_camera = {}
    temporal_delta_us = round(association_config.maximum_candidate_delta_seconds * 1e6)
    counts = {
        "valid_projections": 0,
        "radar_candidate_projections_out_of_fov": 0,
        "invalid_radar_projections": 0,
        "candidates_without_pose_coverage": 0,
        "objects_with_radar_association": 0,
        "objects_without_radar_association": 0,
        "no_radar_association": 0,
        "timestamp_mismatch_objects": 0,
        "out_of_fov_objects": 0,
        "invalid_object_projections": 0,
    }
    candidate_delta_us: list[int] = []

    for camera_frame, radar_frame, scan_delta_us in camera_matches:
        camera_frame.load_data()
        boxes = camera_frame.get_bounding_boxes(
            sequence.labelFiles,
            sequence.labelTimes,
            sequence.labelPoses,
        )
        if boxes is None:
            raise ValueError(
                f"No official tracking labels available for camera frame {camera_frame.frame}"
            )

        projected_candidates: list[ProjectedCandidate] = []
        scan_candidate_count = 0
        radar_timestamp_us = None if radar_frame is None else radar_frame.timestamp_micro
        if radar_frame is not None:
            scan_candidate_count = len(scan_candidates[radar_timestamp_us])
            eligible_candidates = [
                candidate
                for candidate in scan_candidates[radar_timestamp_us]
                if abs(candidate.azimuth_timestamp_us - camera_frame.timestamp_micro)
                <= temporal_delta_us
            ]
            candidate_delta_us.extend(
                abs(candidate.azimuth_timestamp_us - camera_frame.timestamp_micro)
                for candidate in eligible_candidates
            )
            for candidate in eligible_candidates:
                if candidate.azimuth_timestamp_us not in lidar_poses:
                    counts["candidates_without_pose_coverage"] = (
                        counts.get("candidates_without_pose_coverage", 0) + 1
                    )
                    counts["invalid_radar_projections"] += 1
                    continue
                state, pixel, camera_xyz = project_candidate_to_camera(
                    candidate,
                    lidar_poses[candidate.azimuth_timestamp_us],
                    lidar_poses[camera_frame.timestamp_micro],
                    T_radar_lidar,
                    T_camera_lidar,
                    P_camera,
                    camera_frame.img.shape[1],
                    camera_frame.img.shape[0],
                )
                if state == "VALID":
                    counts["valid_projections"] += 1
                    projected_candidates.append(
                        ProjectedCandidate(
                            candidate,
                            pixel,
                            tuple(float(value) for value in camera_xyz),
                        )
                    )
                elif state == "OUT_OF_FOV":
                    counts["radar_candidate_projections_out_of_fov"] += 1
                else:
                    counts["invalid_radar_projections"] += 1

        all_projected_by_camera[camera_frame.timestamp_micro] = projected_candidates
        visible_boxes = []
        for bounding_box in boxes.bbs:
            box_status, box = project_ground_truth_box(
                bounding_box,
                P_camera,
                camera_frame.img.shape[1],
                camera_frame.img.shape[0],
            )
            if box_status == "VALID" and box is not None:
                visible_boxes.append((bounding_box.uuid, box))

            temporally_valid_count = (
                0 if radar_frame is None else len(eligible_candidates)
            )
            row = _box_row(
                camera_frame,
                radar_timestamp_us,
                scan_delta_us,
                bounding_box,
                "TIMESTAMP_MISMATCH" if radar_frame is None else box_status,
                box,
                projected_candidates,
                scan_candidate_count,
                temporally_valid_count,
                association_config,
            )
            status = row["association_status"]
            if status == "ASSOCIATED":
                counts["objects_with_radar_association"] += 1
            else:
                counts["objects_without_radar_association"] += 1
                if status == "NO_RADAR_ASSOCIATION":
                    counts["no_radar_association"] += 1
                elif status == "TIMESTAMP_MISMATCH":
                    counts["timestamp_mismatch_objects"] += 1
                elif status == "OUT_OF_FOV":
                    counts["out_of_fov_objects"] += 1
                elif status == "INVALID_PROJECTION":
                    counts["invalid_object_projections"] += 1
            result_rows.append(row)
        visible_gt_by_camera[camera_frame.timestamp_micro] = visible_boxes

    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "boreas_radar_association_results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as results_file:
        writer = csv.DictWriter(results_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(result_rows)

    matched_deltas_ms = [
        abs(delta) / 1000.0
        for _, radar, delta in camera_matches
        if radar is not None
    ]
    frame_delta_ms = [
        delta / 1000.0
        for _, radar, delta in camera_matches
        if radar is not None
    ]
    if not matched_deltas_ms:
        raise ValueError("No camera frames passed the configured Radar timestamp tolerance")

    first_camera, first_radar, _ = camera_matches[0]
    if first_radar is not None:
        _render_visualization(
            first_camera.img,
            visible_gt_by_camera[first_camera.timestamp_micro],
            all_projected_by_camera[first_camera.timestamp_micro],
            first_camera.timestamp_micro,
            first_radar.timestamp_micro,
            output_dir / "boreas_radar_projection_visualization.png",
        )

    summary = {
        "TOTAL_CAMERA_FRAMES": len(camera_frames),
        "TOTAL_RADAR_SCANS": len(radar_frames),
        "TOTAL_OBJECT_LABEL_FRAMES": len(sequence.labelFiles),
        "TIMESTAMP_PAIRS": len(matched_deltas_ms),
        "TIMESTAMP_MISMATCH_CAMERA_FRAMES": sum(
            radar is None for _, radar, _ in camera_matches
        ),
        "TIMESTAMP_DELTA_MIN": min(matched_deltas_ms),
        "TIMESTAMP_DELTA_MEDIAN": statistics.median(matched_deltas_ms),
        "TIMESTAMP_DELTA_MAX": max(matched_deltas_ms),
        "TIMESTAMP_SIGNED_DELTA_MIN": min(frame_delta_ms),
        "TIMESTAMP_SIGNED_DELTA_MAX": max(frame_delta_ms),
        "CANDIDATE_TIME_DELTA_MIN": (
            min(candidate_delta_us) / 1000.0 if candidate_delta_us else None
        ),
        "CANDIDATE_TIME_DELTA_MEDIAN": (
            statistics.median(candidate_delta_us) / 1000.0
            if candidate_delta_us
            else None
        ),
        "CANDIDATE_TIME_DELTA_MAX": (
            max(candidate_delta_us) / 1000.0 if candidate_delta_us else None
        ),
        "OBJECTS_WITH_RADAR_ASSOCIATION": counts["objects_with_radar_association"],
        "OBJECTS_WITHOUT_RADAR_ASSOCIATION": counts["objects_without_radar_association"],
        "NO_RADAR_ASSOCIATION": counts["no_radar_association"],
        "TIMESTAMP_MISMATCH_OBJECTS": counts["timestamp_mismatch_objects"],
        "VALID_PROJECTIONS": counts["valid_projections"],
        "RADAR_CANDIDATE_PROJECTIONS_OUT_OF_FOV": counts[
            "radar_candidate_projections_out_of_fov"
        ],
        "INVALID_RADAR_PROJECTIONS": counts["invalid_radar_projections"],
        "CANDIDATES_WITHOUT_POSE_COVERAGE": counts[
            "candidates_without_pose_coverage"
        ],
        "OUT_OF_FOV_COUNT": counts["out_of_fov_objects"],
        "INVALID_PROJECTION_COUNT": counts["invalid_object_projections"],
        "ASSOCIATION_ROWS": len(result_rows),
        "TOTAL_EXTRACTED_POLAR_INTENSITY_CANDIDATES": sum(
            len(items) for items in scan_candidates.values()
        ),
        "EXTRACTION_INTENSITY_THRESHOLD": candidate_config.intensity_threshold,
        "EXTRACTION_MINIMUM_RANGE_M": candidate_config.minimum_range_m,
        "MAXIMUM_SCAN_DELTA_SECONDS": association_config.maximum_scan_delta_seconds,
        "MAXIMUM_CANDIDATE_DELTA_SECONDS": association_config.maximum_candidate_delta_seconds,
        "BBOX_MARGIN_PIXELS": association_config.bounding_box_margin_pixels,
        "RESULTS_CSV": results_path.name,
        "VISUALIZATION": "boreas_radar_projection_visualization.png",
        "VALIDATION_PIPELINE_EXECUTION": (
            "PASS"
            if matched_deltas_ms and result_rows and counts["valid_projections"] > 0
            else "FAIL"
        ),
        "BOREAS_RADAR_ASSOCIATION": "INCONCLUSIVE",
    }
    summary.update(pose_consistency)

    report_path = output_dir / "boreas_radar_validation_report.txt"
    report_lines = [
        "BOREAS Objects-V1 Radar-to-visual-ground-truth validation",
        f"Selected interval (inclusive, microseconds): {START_TIMESTAMP_US} - {END_TIMESTAMP_US}",
        f"Sequence: {SEQUENCE_ID}",
        f"Input root: {dataset_root}",
        "",
        "Official calibration convention verified from pyboreas localization:",
        "  T_enu_radar = T_enu_lidar @ inverse(T_radar_lidar)",
        "  T_enu_camera = T_enu_lidar @ inverse(T_camera_lidar)",
        "For an azimuth acquired at time t_r and an RGB frame at t_c, the implemented "
        "point chain is p_enu = T_enu_lidar(t_r) @ inverse(T_radar_lidar) @ p_radar, "
        "then p_camera = T_camera_lidar @ inverse(T_enu_lidar(t_c)) @ p_enu.",
        "Camera projection uses official P_camera.txt.",
        "Static calibration direction is also checked numerically by comparing "
        "the calibrated camera/Radar world poses against their independent "
        "sensor pose-table poses; residuals follow in the summary.",
        "",
        "Candidate extraction is a deterministic 2D local-maximum filter over the "
        "official normalized polar intensity scan, gated by valid azimuth rows and "
        "the stated intensity/range thresholds. Output candidates are NOT Radar "
        "object detections. Range is (range_bin + 0.5) * official pyboreas resolution, "
        "following the bin-center convention in radar_polar_to_cartesian; the "
        "official scan loader's near-range zeroing is retained and no scaling or "
        "unavailable sequence-specific range correction is invented.",
        "Radar candidate beam timestamps are used individually; candidates outside "
        "the configured temporal tolerance are excluded for each camera frame. "
        "Camera/Radar scan timestamp proximity is only a pairing gate, not proof of "
        "spatial association.",
        "Tracking boxes are loaded and time/pose-associated by the official "
        "pyboreas Camera.get_bounding_boxes path. The comparison uses their projected "
        "2D ground-truth boxes, not YOLO output or ByteTrack tracks.",
        "",
        "Counts and settings:",
    ]
    report_lines.extend(f"{key} = {value}" for key, value in summary.items())
    report_lines.extend(
        [
            "",
            "Metric definitions:",
            "  TIMESTAMP_DELTA_* are absolute camera-frame to matched Radar-scan filename timestamp deltas (ms).",
            "  CANDIDATE_TIME_DELTA_* are absolute candidate beam acquisition to camera-frame deltas (ms), after the configured temporal gate.",
            "  VALID_PROJECTIONS counts temporally accepted extracted candidate projections inside the camera image, summed over camera frames.",
            "  OUT_OF_FOV_COUNT and INVALID_PROJECTION_COUNT count ground-truth object-frame boxes.",
            "  OBJECTS_WITHOUT_RADAR_ASSOCIATION includes all non-ASSOCIATED rows; statuses are separately tallied.",
            "  The pipeline execution passes, but the overall Radar-to-object association is INCONCLUSIVE: the provided interval has no Radar object-detection/association ground truth with which to verify that an intensity candidate belongs to the nearby persistent object UUID.",
            "",
            "Limitations:",
            "  BOREAS supplies raw Radar intensity, not per-object Radar detection ground truth. "
            "This validates deterministic candidate extraction, timestamp-aware calibrated "
            "projection, and geometric comparison to official RGB-frame GT boxes; it does "
            "not establish Radar detector accuracy, identity correctness, or ByteTrack "
            "track association performance.",
            "  The selected pose-table coverage does not extend to the earliest azimuth "
            "timestamps in the first Radar scan. Candidate points without a supplied "
            "LiDAR pose are retained as invalid projections; no pose extrapolation is used.",
            "  No YOLO, ByteTrack, VLM, Thermal, or fusion model was run.",
            "  No project pipeline files were changed.",
        ]
    )
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    project_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=project_root / "datasets" / "boreas_radar_validation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "outputs",
    )
    parser.add_argument("--intensity-threshold", type=float, default=0.4)
    parser.add_argument("--minimum-range-m", type=float, default=2.5)
    parser.add_argument("--maximum-scan-delta-seconds", type=float, default=0.15)
    parser.add_argument("--maximum-candidate-delta-seconds", type=float, default=0.15)
    parser.add_argument("--bbox-margin-pixels", type=float, default=8.0)
    args = parser.parse_args()

    summary = run_validation(
        args.dataset_root,
        args.output_dir,
        CandidateExtractionConfig(
            intensity_threshold=args.intensity_threshold,
            minimum_range_m=args.minimum_range_m,
        ),
        AssociationConfig(
            maximum_scan_delta_seconds=args.maximum_scan_delta_seconds,
            maximum_candidate_delta_seconds=args.maximum_candidate_delta_seconds,
            bounding_box_margin_pixels=args.bbox_margin_pixels,
        ),
    )
    print("\n".join(f"{key} = {value}" for key, value in summary.items()))


if __name__ == "__main__":
    main()
