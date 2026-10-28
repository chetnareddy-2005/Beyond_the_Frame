"""Validate Radar polar-intensity candidates against BOREAS 3D object ground truth."""

import argparse
import csv
import json
import math
import statistics
from bisect import bisect_right
from pathlib import Path

import cv2
import numpy as np

try:
    from pyboreas.data.bounding_boxes import BoundingBoxes
    from pyboreas.data.sequence import Sequence
except ModuleNotFoundError:  # pragma: no cover - exercised when the optional devkit is absent.
    BoundingBoxes = None
    Sequence = None

from .candidate_extraction import extract_candidates
from .config import CandidateExtractionConfig
from .geometry import (
    ObjectPolarGeometry,
    object_polar_geometry,
    object_overlaps_radar_range,
    polar_candidate_match,
)
from .projection import interpolate_lidar_poses
from .radar_data import RadarCandidate, RadarScan
from .validation import END_TIMESTAMP_US, SEQUENCE_ID, START_TIMESTAMP_US


RESULT_FIELDS = [
    "object_uuid",
    "object_class",
    "camera_timestamp",
    "label_timestamp",
    "radar_timestamp",
    "timestamp_delta_ms",
    "label_radar_timestamp_delta_ms",
    "expected_range_m",
    "expected_azimuth_deg",
    "expected_range_at_radar_timestamp_m",
    "expected_azimuth_at_radar_timestamp_deg",
    "radar_candidate_range_m",
    "radar_candidate_azimuth_deg",
    "radar_candidate_index",
    "range_error_m",
    "azimuth_error_deg",
    "candidate_intensity",
    "object_dimensions_m",
    "object_location_lidar_m",
    "object_orientation_yaw_rad",
    "associated_radar_count",
    "association_distance_px",
    "association_status",
    "association_method",
    "radar_candidate_count",
    "candidates_compared",
]
METHOD = (
    "timestamp-interpolated 3D GT center and transformed box extent; "
    "Radar polar intensity candidate within physical range/azimuth gate"
)


class ObjectTrajectory:
    """Timestamp-indexed ENU centers and LiDAR-frame boxes from official labels."""

    def __init__(self, sequence: Sequence, lidar_poses: dict[int, np.ndarray]):
        if BoundingBoxes is None or Sequence is None:
            raise ModuleNotFoundError(
                "The optional pyboreas devkit is required for BOREAS geometric validation. "
                "Install asrl-pyboreas and ensure it is on PYTHONPATH."
            )
        self.sequence = sequence
        self.lidar_poses = lidar_poses
        self.timestamps = [
            int(Path(path).stem) for path in sequence.labelFiles
        ]
        self.boxes_by_time: dict[int, dict[str, object]] = {}
        self.centers_enu: dict[str, dict[int, np.ndarray]] = {}

        for timestamp_us, path, lidar_pose in zip(
            self.timestamps, sequence.labelFiles, sequence.labelPoses
        ):
            boxes = BoundingBoxes()
            boxes.load_from_file(path)
            self.boxes_by_time[timestamp_us] = {
                box.uuid: box for box in boxes.bbs
            }
            for box in boxes.bbs:
                center = np.append(np.asarray(box.pos).reshape(3), 1.0)
                center_enu = lidar_pose @ center
                self.centers_enu.setdefault(box.uuid, {})[timestamp_us] = (
                    center_enu[:3]
                )

    def center_at(self, object_uuid: str, timestamp_us: int) -> np.ndarray | None:
        observations = self.centers_enu.get(object_uuid, {})
        if timestamp_us in self.timestamps:
            center = observations.get(timestamp_us)
            return None if center is None else center.copy()
        upper_index = bisect_right(self.timestamps, timestamp_us)
        if upper_index == 0 or upper_index >= len(self.timestamps):
            return None
        t0 = self.timestamps[upper_index - 1]
        t1 = self.timestamps[upper_index]
        if t0 not in observations or t1 not in observations:
            return None
        fraction = (timestamp_us - t0) / (t1 - t0)
        return observations[t0] * (1.0 - fraction) + observations[t1] * fraction


def _nearest_scan(label_timestamp_us: int, radar_frames: list, maximum_delta_us: int):
    if not radar_frames:
        return None, None
    frame = min(
        radar_frames,
        key=lambda item: (
            abs(item.timestamp_micro - label_timestamp_us),
            item.timestamp_micro,
        ),
    )
    delta = frame.timestamp_micro - label_timestamp_us
    if abs(delta) > maximum_delta_us:
        return None, delta
    return frame, delta


def _object_box_corners_enu(box, lidar_pose: np.ndarray) -> np.ndarray:
    corners = np.asarray(box.pc.points, dtype=np.float64)
    if corners.shape != (8, 3) or not np.isfinite(corners).all():
        raise ValueError("Tracking label box has invalid 3D corners")
    homogeneous = np.column_stack((corners, np.ones(len(corners))))
    return (lidar_pose @ homogeneous.T).T[:, :3]


def _box_yaw_rad(box) -> float:
    return float(math.atan2(box.rot[1, 0], box.rot[0, 0]))


def _object_geometry_at_time(
    box,
    query_timestamp_us: int,
    label_lidar_pose: np.ndarray,
    trajectory: ObjectTrajectory,
    lidar_poses: dict[int, np.ndarray],
    T_radar_lidar: np.ndarray,
) -> ObjectPolarGeometry | None:
    object_center_enu = trajectory.center_at(box.uuid, query_timestamp_us)
    radar_lidar_pose = lidar_poses.get(query_timestamp_us)
    if object_center_enu is None or radar_lidar_pose is None:
        return None
    corners_enu_at_label = _object_box_corners_enu(box, label_lidar_pose)
    label_center_enu = (
        label_lidar_pose @ np.append(np.asarray(box.pos).reshape(3), 1.0)
    )[:3]
    corners_enu = corners_enu_at_label + (object_center_enu - label_center_enu)
    T_enu_radar = radar_lidar_pose @ np.linalg.inv(T_radar_lidar)
    T_radar_enu = np.linalg.inv(T_enu_radar)
    center_radar = T_radar_enu @ np.append(object_center_enu, 1.0)
    corners_radar = (
        T_radar_enu @ np.column_stack((corners_enu, np.ones(len(corners_enu)))).T
    ).T[:, :3]
    try:
        return object_polar_geometry(center_radar, corners_radar)
    except ValueError:
        return None


def _status_row(
    *,
    object_uuid,
    object_class,
    camera_timestamp,
    label_timestamp,
    radar_timestamp,
    timestamp_delta_us,
    expected_range,
    expected_azimuth,
    expected_scan_range,
    expected_scan_azimuth,
    candidate,
    range_error,
    azimuth_error,
    dimensions,
    location,
    orientation_yaw_rad,
    status,
    radar_candidate_count,
    candidates_compared,
    candidate_index=None,
):
    return {
        "object_uuid": object_uuid,
        "object_class": object_class,
        "camera_timestamp": camera_timestamp,
        "label_timestamp": label_timestamp,
        "radar_timestamp": "" if radar_timestamp is None else radar_timestamp,
        "timestamp_delta_ms": (
            ""
            if radar_timestamp is None
            else round(abs(radar_timestamp - camera_timestamp) / 1000, 3)
        ),
        "label_radar_timestamp_delta_ms": (
            "" if timestamp_delta_us is None else round(abs(timestamp_delta_us) / 1000, 3)
        ),
        "expected_range_m": (
            "" if expected_range is None else round(expected_range, 4)
        ),
        "expected_azimuth_deg": (
            "" if expected_azimuth is None else round(math.degrees(expected_azimuth), 4)
        ),
        "expected_range_at_radar_timestamp_m": (
            "" if expected_scan_range is None else round(expected_scan_range, 4)
        ),
        "expected_azimuth_at_radar_timestamp_deg": (
            ""
            if expected_scan_azimuth is None
            else round(math.degrees(expected_scan_azimuth), 4)
        ),
        "radar_candidate_range_m": (
            "" if candidate is None else round(candidate.range_m, 4)
        ),
        "radar_candidate_azimuth_deg": (
            "" if candidate is None else round(math.degrees(candidate.azimuth_rad), 4)
        ),
        "radar_candidate_index": (
            "" if candidate is None else candidate_index
        ),
        "range_error_m": "" if range_error is None else round(range_error, 4),
        "azimuth_error_deg": (
            "" if azimuth_error is None else round(math.degrees(azimuth_error), 4)
        ),
        "candidate_intensity": (
            "" if candidate is None else round(candidate.intensity, 6)
        ),
        "object_dimensions_m": json.dumps(
            [round(float(value), 4) for value in np.asarray(dimensions).reshape(3)]
        ),
        "object_location_lidar_m": json.dumps(
            [round(float(value), 4) for value in np.asarray(location).reshape(3)]
        ),
        "object_orientation_yaw_rad": round(float(orientation_yaw_rad), 6),
        "associated_radar_count": int(status == "ASSOCIATED"),
        "association_distance_px": "",
        "association_status": status,
        "association_method": METHOD,
        "radar_candidate_count": radar_candidate_count,
        "candidates_compared": candidates_compared,
    }


def _polar_pixel(
    range_m: float,
    azimuth_rad: float,
    resolution_m: float,
    azimuths: np.ndarray,
) -> tuple[int, int]:
    range_bin = int(round(range_m / resolution_m - 0.5))
    differences = np.abs(
        (azimuths - azimuth_rad + math.pi) % (2.0 * math.pi) - math.pi
    )
    row = int(np.argmin(differences))
    return max(0, range_bin), row


def _render_polar_scan(
    scan: RadarScan,
    candidates: list[RadarCandidate],
    expected_objects: list[dict],
    associated_candidates: set[tuple[int, int]],
    associated_uuids: set[str],
    label_timestamp_us: int,
    output_path: Path,
) -> None:
    normalized = np.clip(scan.polar_intensity * 255.0, 0, 255).astype(np.uint8)
    image = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
    for candidate in candidates:
        x, y = candidate.range_bin, candidate.azimuth_index
        color = (255, 255, 0) if (candidate.azimuth_index, candidate.range_bin) in associated_candidates else (0, 145, 255)
        cv2.circle(image, (x, y), 3, color, -1)

    for item in expected_objects:
        x, y = _polar_pixel(
            item["range_m"],
            item["azimuth_rad"],
            scan.resolution_m,
            scan.azimuths_rad,
        )
        if 0 <= x < image.shape[1]:
            cv2.drawMarker(
                image,
                (x, y),
                (0, 255, 0),
                cv2.MARKER_CROSS,
                11,
                1,
            )
            if item["uuid"] in associated_uuids:
                cv2.putText(
                    image,
                    f"{item['class']} {item['uuid'][:8]}",
                    (min(x + 4, image.shape[1] - 140), max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

    legend = np.zeros((64, image.shape[1], 3), dtype=np.uint8)
    cv2.putText(
        legend,
        "GREEN cross: expected 3D GT center | ORANGE: extracted intensity candidate | CYAN: associated candidate",
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        legend,
        "Radar polar scan: x=range bin, y=azimuth row; candidates are not Radar detections",
        (8, 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        legend,
        f"Expected GT centers at scan timestamp {scan.frame_timestamp_us}; label frame {label_timestamp_us}",
        (8, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), np.vstack((image, legend))):
        raise OSError(f"Could not write Radar polar visualization: {output_path}")


def run_geometric_validation(
    dataset_root: Path,
    output_dir: Path,
    candidate_config: CandidateExtractionConfig = CandidateExtractionConfig(),
    maximum_scan_delta_seconds: float = 0.15,
    maximum_candidate_delta_seconds: float = 0.4,
    range_padding_m: float = 2.0,
    azimuth_padding_degrees: float = 1.0,
) -> dict:
    if Sequence is None:
        raise ModuleNotFoundError(
            "The optional pyboreas devkit is required for BOREAS geometric validation. "
            "Install asrl-pyboreas and ensure it is on PYTHONPATH."
        )
    if maximum_scan_delta_seconds < 0 or maximum_candidate_delta_seconds < 0:
        raise ValueError("Timestamp tolerances must be non-negative")
    if range_padding_m < 0 or azimuth_padding_degrees < 0:
        raise ValueError("Polar association padding must be non-negative")

    sequence = Sequence(
        str(dataset_root),
        [SEQUENCE_ID, START_TIMESTAMP_US, END_TIMESTAMP_US],
        labelFolder="labels_tracking",
    )
    radar_frames = sequence.radar_frames
    camera_frames = sequence.camera_frames
    label_entries = list(
        zip(
            [round(time * 1e6) for time in sequence.labelTimes],
            sequence.labelFiles,
            sequence.labelPoses,
        )
    )
    if not radar_frames or not camera_frames or not label_entries:
        raise ValueError("The selected interval lacks camera, Radar, or label frames")

    scan_by_timestamp: dict[int, RadarScan] = {}
    candidates_by_timestamp: dict[int, list[RadarCandidate]] = {}
    for scan_index, frame in enumerate(radar_frames):
        scan = RadarScan.from_pyboreas_frame(sequence.get_radar(scan_index))
        scan_by_timestamp[scan.frame_timestamp_us] = scan
        candidates_by_timestamp[scan.frame_timestamp_us] = extract_candidates(
            scan, candidate_config
        )

    matched_labels = []
    timestamp_gate_us = round(maximum_scan_delta_seconds * 1e6)
    candidate_gate_us = round(maximum_candidate_delta_seconds * 1e6)
    for label_timestamp, label_path, label_pose in label_entries:
        radar_frame, delta_us = _nearest_scan(
            label_timestamp, radar_frames, timestamp_gate_us
        )
        camera_frame = min(
            camera_frames,
            key=lambda item: (
                abs(item.timestamp_micro - label_timestamp),
                item.timestamp_micro,
            ),
        )
        matched_labels.append(
            (
                label_timestamp,
                label_path,
                label_pose,
                radar_frame,
                delta_us,
                camera_frame.timestamp_micro,
            )
        )

    selected_candidate_times = [
        candidate.azimuth_timestamp_us
        for label_timestamp, _, _, radar_frame, _, _ in matched_labels
        if radar_frame is not None
        for candidate in candidates_by_timestamp[radar_frame.timestamp_micro]
        if abs(candidate.azimuth_timestamp_us - label_timestamp) <= candidate_gate_us
    ]
    query_times = [timestamp for timestamp, _, _ in label_entries]
    query_times += [
        radar_frame.timestamp_micro
        for _, _, _, radar_frame, _, _ in matched_labels
        if radar_frame is not None
    ]
    query_times += selected_candidate_times
    lidar_poses = interpolate_lidar_poses(Path(sequence.seq_root), query_times)
    trajectory = ObjectTrajectory(sequence, lidar_poses)
    T_radar_lidar = np.asarray(sequence.calib.T_radar_lidar, dtype=np.float64)
    azimuth_padding_rad = math.radians(azimuth_padding_degrees)
    minimum_range_m = candidate_config.minimum_range_m
    max_range_m = min(
        (scan.polar_intensity.shape[1] - 0.5) * scan.resolution_m
        for scan in scan_by_timestamp.values()
    )

    output_rows = []
    frame_records = []
    matched_radar_timestamps = set()
    all_timestamp_deltas_ms = []
    camera_radar_deltas_ms = []
    range_errors = []
    azimuth_errors = []
    objects_evaluated = 0
    objects_in_fov = 0
    associated_objects = 0
    timestamp_mismatch_objects = 0
    invalid_geometry_objects = 0
    out_of_fov_objects = 0
    candidates_without_gt_trajectory = 0
    objects_without_candidate_geometry = 0

    for (
        label_timestamp,
        label_path,
        label_lidar_pose,
        radar_frame,
        scan_delta_us,
        camera_timestamp,
    ) in matched_labels:
        labels = BoundingBoxes()
        labels.load_from_file(label_path)
        if radar_frame is None:
            timestamp_mismatch_objects += len(labels.bbs)
            for box in labels.bbs:
                objects_evaluated += 1
                output_rows.append(
                    _status_row(
                        object_uuid=box.uuid,
                        object_class=box.label,
                        camera_timestamp=camera_timestamp,
                        label_timestamp=label_timestamp,
                        radar_timestamp=None,
                        timestamp_delta_us=scan_delta_us,
                        expected_range=None,
                        expected_azimuth=None,
                        expected_scan_range=None,
                        expected_scan_azimuth=None,
                        candidate=None,
                        range_error=None,
                        azimuth_error=None,
                        dimensions=box.extent,
                        location=box.pos,
                        orientation_yaw_rad=_box_yaw_rad(box),
                        status="TIMESTAMP_MISMATCH",
                        radar_candidate_count=0,
                        candidates_compared=0,
                    )
                )
            continue

        radar_timestamp = radar_frame.timestamp_micro
        matched_radar_timestamps.add(radar_timestamp)
        scan = scan_by_timestamp[radar_timestamp]
        candidates = candidates_by_timestamp[radar_timestamp]
        timestamp_delta_ms = abs(scan_delta_us) / 1000.0
        all_timestamp_deltas_ms.append(timestamp_delta_ms)
        camera_radar_deltas_ms.append(
            abs(radar_timestamp - camera_timestamp) / 1000.0
        )
        eligible_candidates = [
            (candidate_index, candidate)
            for candidate_index, candidate in enumerate(candidates)
            if abs(candidate.azimuth_timestamp_us - label_timestamp) <= candidate_gate_us
        ]

        object_states = []
        proposals = []
        for box in labels.bbs:
            objects_evaluated += 1
            geometry_at_scan = _object_geometry_at_time(
                box,
                radar_timestamp,
                label_lidar_pose,
                trajectory,
                lidar_poses,
                T_radar_lidar,
            )
            if geometry_at_scan is None:
                invalid_geometry_objects += 1
                object_states.append(
                    {
                        "box": box,
                        "status": "INVALID_GEOMETRY",
                        "geometry": None,
                        "camera_timestamp": camera_timestamp,
                        "label_timestamp": label_timestamp,
                        "radar_timestamp": radar_timestamp,
                        "timestamp_delta_us": scan_delta_us,
                        "candidates_compared": 0,
                    }
                )
                continue

            if not object_overlaps_radar_range(
                geometry_at_scan, minimum_range_m, max_range_m
            ):
                out_of_fov_objects += 1
                object_states.append(
                    {
                        "box": box,
                        "status": "OUT_OF_RADAR_FOV",
                        "geometry": geometry_at_scan,
                        "camera_timestamp": camera_timestamp,
                        "label_timestamp": label_timestamp,
                        "radar_timestamp": radar_timestamp,
                        "timestamp_delta_us": scan_delta_us,
                        "candidates_compared": 0,
                    }
                )
                continue

            objects_in_fov += 1
            state = {
                "box": box,
                "status": "NO_RADAR_ASSOCIATION",
                "geometry": geometry_at_scan,
                "camera_timestamp": camera_timestamp,
                "label_timestamp": label_timestamp,
                "radar_timestamp": radar_timestamp,
                "timestamp_delta_us": scan_delta_us,
                "candidates_compared": 0,
                "candidate": None,
                "range_error": None,
                "azimuth_error": None,
                "matched_geometry": None,
            }
            object_states.append(state)
            valid_candidate_geometry_count = 0

            for candidate_index, candidate in eligible_candidates:
                geometry_at_candidate = _object_geometry_at_time(
                    box,
                    candidate.azimuth_timestamp_us,
                    label_lidar_pose,
                    trajectory,
                    lidar_poses,
                    T_radar_lidar,
                )
                if geometry_at_candidate is None:
                    candidates_without_gt_trajectory += 1
                    continue
                valid_candidate_geometry_count += 1
                state["candidates_compared"] += 1
                match = polar_candidate_match(
                    geometry_at_candidate,
                    candidate,
                    range_padding_m,
                    azimuth_padding_rad,
                )
                if match is not None:
                    score, range_error, azimuth_error = match
                    proposals.append(
                        (
                            score,
                            -candidate.intensity,
                            box.uuid,
                            candidate.azimuth_index,
                            candidate.range_bin,
                            candidate_index,
                            state,
                            candidate,
                            range_error,
                            azimuth_error,
                            geometry_at_candidate,
                        )
                    )
            if eligible_candidates and valid_candidate_geometry_count == 0:
                state["status"] = "INVALID_GEOMETRY"
                invalid_geometry_objects += 1
                objects_without_candidate_geometry += 1

        assigned_objects = set()
        assigned_candidates = set()
        for proposal in sorted(proposals):
            (
                _score,
                _negative_intensity,
                object_uuid,
                _azimuth_index,
                _range_bin,
                candidate_index,
                state,
                candidate,
                range_error,
                azimuth_error,
                geometry_at_candidate,
            ) = proposal
            if object_uuid in assigned_objects or candidate_index in assigned_candidates:
                continue
            state["status"] = "ASSOCIATED"
            state["candidate"] = candidate
            state["candidate_index"] = candidate_index
            state["range_error"] = range_error
            state["azimuth_error"] = azimuth_error
            state["matched_geometry"] = geometry_at_candidate
            assigned_objects.add(object_uuid)
            assigned_candidates.add(candidate_index)
            associated_objects += 1
            range_errors.append(range_error)
            azimuth_errors.append(math.degrees(azimuth_error))

        expected_for_visualization = []
        associated_keys = set()
        associated_uuids = set()
        for state in object_states:
            box = state["box"]
            geometry_at_scan = state["geometry"]
            candidate = state.get("candidate")
            if geometry_at_scan is not None and state["status"] != "INVALID_GEOMETRY":
                expected_for_visualization.append(
                    {
                        "uuid": box.uuid,
                        "class": box.label,
                        "range_m": geometry_at_scan.range_m,
                        "azimuth_rad": geometry_at_scan.azimuth_rad,
                    }
                )
            if candidate is not None:
                associated_keys.add((candidate.azimuth_index, candidate.range_bin))
                associated_uuids.add(box.uuid)
            comparison_geometry = state.get("matched_geometry") or geometry_at_scan
            output_rows.append(
                _status_row(
                    object_uuid=box.uuid,
                    object_class=box.label,
                    camera_timestamp=state["camera_timestamp"],
                    label_timestamp=state["label_timestamp"],
                    radar_timestamp=state["radar_timestamp"],
                    timestamp_delta_us=state["timestamp_delta_us"],
                    expected_range=(
                        None
                        if comparison_geometry is None
                        else comparison_geometry.range_m
                    ),
                    expected_azimuth=(
                        None
                        if comparison_geometry is None
                        else comparison_geometry.azimuth_rad
                    ),
                    expected_scan_range=(
                        None if geometry_at_scan is None else geometry_at_scan.range_m
                    ),
                    expected_scan_azimuth=(
                        None
                        if geometry_at_scan is None
                        else geometry_at_scan.azimuth_rad
                    ),
                    candidate=candidate,
                    range_error=state.get("range_error"),
                    azimuth_error=state.get("azimuth_error"),
                    dimensions=box.extent,
                    location=box.pos,
                    orientation_yaw_rad=_box_yaw_rad(box),
                    status=state["status"],
                    radar_candidate_count=len(candidates),
                    candidates_compared=state["candidates_compared"],
                    candidate_index=state.get("candidate_index"),
                )
            )

        frame_records.append(
            (
                len(associated_keys),
                radar_timestamp,
                scan,
                candidates,
                expected_for_visualization,
                associated_keys,
                associated_uuids,
                label_timestamp,
            )
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "boreas_radar_geometric_validation.csv"
    with results_path.open("w", newline="", encoding="utf-8") as result_file:
        writer = csv.DictWriter(result_file, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    if frame_records:
        visual_record = max(
            frame_records,
            key=lambda item: (item[0], -item[1]),
        )
        (
            _,
            _,
            visual_scan,
            visual_candidates,
            expected,
            associated,
            associated_uuids,
            visual_label_timestamp,
        ) = visual_record
        _render_polar_scan(
            visual_scan,
            visual_candidates,
            expected,
            associated,
            associated_uuids,
            visual_label_timestamp,
            output_dir / "boreas_radar_geometric_polar_visualization.png",
        )

    non_associated_in_fov = objects_in_fov - associated_objects
    association_rate = (
        associated_objects / objects_in_fov if objects_in_fov else None
    )
    summary = {
        "OBJECTS_EVALUATED": objects_evaluated,
        "OBJECTS_IN_RADAR_FOV": objects_in_fov,
        "ASSOCIATED_OBJECTS": associated_objects,
        "NON_ASSOCIATED_OBJECTS": non_associated_in_fov,
        "ASSOCIATION_RATE": association_rate,
        "ASSOCIATION_RATE_PERCENT": (
            None if association_rate is None else association_rate * 100.0
        ),
        "MEDIAN_RANGE_ERROR_M": (
            statistics.median(range_errors) if range_errors else None
        ),
        "MAX_RANGE_ERROR_M": max(range_errors) if range_errors else None,
        "MEDIAN_AZIMUTH_ERROR_DEG": (
            statistics.median(azimuth_errors) if azimuth_errors else None
        ),
        "MAX_AZIMUTH_ERROR_DEG": max(azimuth_errors) if azimuth_errors else None,
        "TIMESTAMP_MISMATCH_OBJECTS": timestamp_mismatch_objects,
        "OUT_OF_RADAR_FOV_OBJECTS": out_of_fov_objects,
        "INVALID_GEOMETRY_OBJECTS": invalid_geometry_objects,
        "TIMESTAMP_PAIRS": len(all_timestamp_deltas_ms),
        "TIMESTAMP_DELTA_MIN_MS": (
            min(camera_radar_deltas_ms) if camera_radar_deltas_ms else None
        ),
        "TIMESTAMP_DELTA_MEDIAN_MS": (
            statistics.median(camera_radar_deltas_ms)
            if camera_radar_deltas_ms
            else None
        ),
        "TIMESTAMP_DELTA_MAX_MS": (
            max(camera_radar_deltas_ms) if camera_radar_deltas_ms else None
        ),
        "LABEL_RADAR_TIMESTAMP_DELTA_MIN_MS": (
            min(all_timestamp_deltas_ms) if all_timestamp_deltas_ms else None
        ),
        "LABEL_RADAR_TIMESTAMP_DELTA_MEDIAN_MS": (
            statistics.median(all_timestamp_deltas_ms)
            if all_timestamp_deltas_ms
            else None
        ),
        "LABEL_RADAR_TIMESTAMP_DELTA_MAX_MS": (
            max(all_timestamp_deltas_ms) if all_timestamp_deltas_ms else None
        ),
        "TOTAL_CAMERA_FRAMES": len(camera_frames),
        "TOTAL_RADAR_SCANS": len(radar_frames),
        "TOTAL_OBJECT_LABEL_FRAMES": len(sequence.labelFiles),
        "TOTAL_EXTRACTED_INTENSITY_CANDIDATES_IN_MATCHED_SCANS": sum(
            len(candidates_by_timestamp[timestamp])
            for timestamp in matched_radar_timestamps
        ),
        "CANDIDATE_GT_TRAJECTORY_UNAVAILABLE_COUNT": candidates_without_gt_trajectory,
        "OBJECTS_WITHOUT_CANDIDATE_GEOMETRY": objects_without_candidate_geometry,
        "NO_RADAR_ASSOCIATION_OBJECTS": sum(
            row["association_status"] == "NO_RADAR_ASSOCIATION"
            for row in output_rows
        ),
        "RANGE_PADDING_M": range_padding_m,
        "AZIMUTH_PADDING_DEGREES": azimuth_padding_degrees,
        "MAXIMUM_SCAN_DELTA_SECONDS": maximum_scan_delta_seconds,
        "MAXIMUM_CANDIDATE_DELTA_SECONDS": maximum_candidate_delta_seconds,
        "MINIMUM_RADAR_RANGE_M": minimum_range_m,
        "MAXIMUM_RADAR_RANGE_M": max_range_m,
        "INTENSITY_THRESHOLD": candidate_config.intensity_threshold,
        "CANDIDATE_ID_AVAILABLE": (
            "YES - deterministic zero-based index within each scan's ordered candidate list; "
            "not a persistent object identity"
        ),
        "ASSOCIATION_DISTANCE_PX": (
            "NOT_COMPUTED - this validator uses Radar polar gates"
        ),
        "RESULTS_CSV": results_path.name,
        "VISUALIZATION": "boreas_radar_geometric_polar_visualization.png",
        "VALIDATION_SCOPE": "Geometric consistency against BOREAS 3D object ground truth",
        "RADAR_TRACK_ASSOCIATION": "INCONCLUSIVE",
        "BOREAS_RADAR_GEOMETRIC_VALIDATION": (
            "PASS" if objects_evaluated and objects_in_fov else "INCONCLUSIVE"
        ),
    }

    report = [
        "BOREAS Radar geometric consistency validation",
        f"Sequence: {SEQUENCE_ID}",
        f"Inclusive interval (microseconds): {START_TIMESTAMP_US} - {END_TIMESTAMP_US}",
        f"Data root: {dataset_root}",
        "",
        "Ground truth: official BOREAS Objects-V1 timestamped 3D tracking labels; UUID, "
        "class, dimensions, location, yaw/orientation, and point-count fields are read via the "
        "unmodified official pyboreas BoundingBoxes parser.",
        "Coordinate chain: p_enu(label time) = T_enu_lidar(label time) @ p_lidar; "
        "T_enu_radar(t) = T_enu_lidar(t) @ inverse(T_radar_lidar); "
        "p_radar(t) = inverse(T_enu_radar(t)) @ p_enu(t). Object centers are linearly "
        "interpolated in ENU between adjacent same-UUID tracking labels only; no "
        "extrapolation is performed, and interpolation requires the UUID to be present "
        "in both adjacent label frames. The box extent is transformed using the official "
        "label box corners, with label-time orientation held fixed and its center "
        "translated by interpolated GT motion.",
        "Radar candidate range and azimuth are the actual extracted peak's bin-center "
        "range and per-azimuth value/time from the official Radar loader. Objects are "
        "within Radar FOV when their radial box extent overlaps [minimum detectable "
        "range, maximum scan range]; the rotating BOREAS Radar is treated as 360-degree.",
        "Association requires BOTH candidate-center range error <= the projected 3D "
        "box range radius + configured range padding, AND wrapped azimuth error <= "
        "the box angular half-extent + configured angular padding. Candidate assignment "
        "is one-to-one per label frame, prioritizing normalized polar error then "
        "intensity and stable identifiers.",
        "CSV timestamp_delta_ms is absolute camera-to-Radar filename timestamp "
        "difference; label_radar_timestamp_delta_ms is the label-to-Radar difference "
        "used for scan selection. Both are reported separately.",
        "Associated CSV rows preserve expected and candidate polar coordinates, "
        "range/azimuth errors, associated_radar_count, and the extracted candidate's "
        "deterministic zero-based index in that Radar scan's ordered candidate list. "
        "The index is scan-local, not a persistent Radar object identity. "
        "association_distance_px is blank because this geometric polar validator does "
        "not compute image-pixel distance.",
        "These are geometric consistency associations against object ground truth, "
        "not Radar detection ground truth. No Radar precision/recall or verified "
        "Radar-to-ByteTrack identity is claimed.",
        "",
        "Statistics and configuration:",
    ]
    report.extend(f"{key} = {value}" for key, value in summary.items())
    report.extend(
        [
            "",
            "Association status meanings:",
            "  ASSOCIATED: an extracted Radar intensity candidate met both polar gates.",
            "  NO_RADAR_ASSOCIATION: object lies in Radar FOV, but no candidate met both gates.",
            "  OUT_OF_RADAR_FOV: the 3D object extent lies outside the scan's radial coverage.",
            "  TIMESTAMP_MISMATCH: no Radar scan falls within the configured label-time gate.",
            "  INVALID_GEOMETRY: required GT trajectory or timestamped sensor pose was unavailable/non-finite.",
            "",
            "Interpretation: successful geometric overlap is not independent Radar object "
            "detection truth. Radar-track association remains INCONCLUSIVE until an "
            "independent Radar detection/identity reference is available.",
        ]
    )
    report_path = output_dir / "boreas_radar_geometric_validation_report.txt"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--maximum-candidate-delta-seconds", type=float, default=0.4)
    parser.add_argument("--range-padding-m", type=float, default=2.0)
    parser.add_argument("--azimuth-padding-degrees", type=float, default=1.0)
    args = parser.parse_args()
    summary = run_geometric_validation(
        args.dataset_root,
        args.output_dir,
        CandidateExtractionConfig(
            intensity_threshold=args.intensity_threshold,
            minimum_range_m=args.minimum_range_m,
        ),
        args.maximum_scan_delta_seconds,
        args.maximum_candidate_delta_seconds,
        args.range_padding_m,
        args.azimuth_padding_degrees,
    )
    print("\n".join(f"{key} = {value}" for key, value in summary.items()))


if __name__ == "__main__":
    main()
