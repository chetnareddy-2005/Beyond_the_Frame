"""Timestamp-aware Radar-to-camera projection using official pyboreas conventions."""

import csv
from bisect import bisect_left
from pathlib import Path

import numpy as np

try:
    from pyboreas.utils.odometry import interpolate_poses
    from pyboreas.utils.utils import get_transform
except ModuleNotFoundError:  # pragma: no cover - exercised when the optional devkit is absent.
    interpolate_poses = None
    get_transform = None

from .radar_data import RadarCandidate


def interpolate_lidar_poses(sequence_root: Path, query_times_us: list[int]):
    """Interpolate ENU-from-LiDAR poses with the official pyboreas utility."""
    if interpolate_poses is None or get_transform is None:
        raise ModuleNotFoundError(
            "The optional pyboreas devkit is required for BOREAS projection utilities. "
            "Install asrl-pyboreas and ensure it is on PYTHONPATH."
        )
    if not query_times_us:
        return {}

    pose_path = sequence_root / "applanix" / "lidar_poses.csv"
    rows: list[list[str]] = []
    with pose_path.open("r", newline="", encoding="utf-8") as pose_file:
        for row in csv.reader(pose_file):
            if row and row[0].isdigit():
                rows.append(row)
    if len(rows) < 2:
        raise ValueError(f"Insufficient LiDAR pose rows in {pose_path}")

    timestamps = [int(row[0]) for row in rows]
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError(f"LiDAR pose timestamps are not strictly increasing: {pose_path}")

    covered_queries = [
        timestamp
        for timestamp in query_times_us
        if timestamps[0] <= timestamp <= timestamps[-1]
    ]
    if not covered_queries:
        return {}
    start = bisect_left(timestamps, min(covered_queries))
    end = bisect_left(timestamps, max(covered_queries))
    first = max(0, start - 1)
    last = min(len(rows), end + 2)
    selected = rows[first:last]
    pose_times = [int(row[0]) for row in selected]
    poses = [get_transform([float(value) for value in row]) for row in selected]
    query_times = sorted(set(covered_queries))
    interpolated = interpolate_poses(
        poses,
        pose_times,
        query_times,
        solver=False,
    )
    return dict(zip(query_times, interpolated))


def project_candidate_to_camera(
    candidate: RadarCandidate,
    lidar_pose_at_azimuth: np.ndarray,
    lidar_pose_at_camera: np.ndarray,
    T_radar_lidar: np.ndarray,
    T_camera_lidar: np.ndarray,
    P_camera: np.ndarray,
    width: int,
    height: int,
) -> tuple[str, tuple[float, float] | None, np.ndarray | None]:
    """Project one polar candidate via Radar -> LiDAR/ENU -> camera.

    Official pyboreas localization uses T_enu_radar = T_enu_lidar @
    inverse(T_radar_lidar), and T_enu_camera = T_enu_lidar @
    inverse(T_camera_lidar). Thus candidate points enter LiDAR with the
    inverse radar calibration and reach camera coordinates with T_camera_lidar.
    """
    T_lidar_radar = np.linalg.inv(T_radar_lidar)
    T_camera_world = T_camera_lidar @ np.linalg.inv(lidar_pose_at_camera)
    point_world = lidar_pose_at_azimuth @ T_lidar_radar @ candidate.point_radar
    point_camera = T_camera_world @ point_world
    if not np.isfinite(point_camera).all() or point_camera[2] <= 0:
        return "INVALID_PROJECTION", None, point_camera[:3]

    homogeneous_pixel = P_camera @ point_camera
    depth = homogeneous_pixel[2]
    if not np.isfinite(homogeneous_pixel).all() or depth <= 0:
        return "INVALID_PROJECTION", None, point_camera[:3]
    u = float(homogeneous_pixel[0] / depth)
    v = float(homogeneous_pixel[1] / depth)
    if not np.isfinite([u, v]).all():
        return "INVALID_PROJECTION", None, point_camera[:3]
    if not (0 <= u <= width - 1 and 0 <= v <= height - 1):
        return "OUT_OF_FOV", (u, v), point_camera[:3]
    return "VALID", (u, v), point_camera[:3]


def project_ground_truth_box(
    bounding_box,
    P_camera: np.ndarray,
    width: int,
    height: int,
) -> tuple[str, tuple[float, float, float, float] | None]:
    """Project official pyboreas GT box corners already transformed to camera."""
    corners = np.asarray(bounding_box.pc.points, dtype=np.float64)
    if corners.shape != (8, 3) or not np.isfinite(corners).all():
        return "INVALID_PROJECTION", None
    if np.any(corners[:, 2] <= 0):
        return "INVALID_PROJECTION", None

    points = np.column_stack((corners, np.ones(len(corners))))
    projected = points @ P_camera.T
    if not np.isfinite(projected).all() or np.any(projected[:, 2] <= 0):
        return "INVALID_PROJECTION", None
    uv = projected[:, :2] / projected[:, 2:3]
    if not np.isfinite(uv).all():
        return "INVALID_PROJECTION", None

    x1, y1 = uv.min(axis=0)
    x2, y2 = uv.max(axis=0)
    if x2 < 0 or y2 < 0 or x1 > width - 1 or y1 > height - 1:
        return "OUT_OF_FOV", None
    return (
        "VALID",
        (
            float(np.clip(x1, 0, width - 1)),
            float(np.clip(y1, 0, height - 1)),
            float(np.clip(x2, 0, width - 1)),
            float(np.clip(y2, 0, height - 1)),
        ),
    )
