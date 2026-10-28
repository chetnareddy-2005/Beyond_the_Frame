"""Radar-frame geometry and polar gates for BOREAS object-ground-truth checks."""

from dataclasses import dataclass
import math

import numpy as np

from .radar_data import RadarCandidate


@dataclass(frozen=True)
class ObjectPolarGeometry:
    range_m: float
    azimuth_rad: float
    range_extent_m: float
    horizontal_extent_m: float

    @property
    def azimuth_extent_rad(self) -> float:
        if self.range_m <= self.horizontal_extent_m:
            return math.pi
        return math.asin(
            min(1.0, self.horizontal_extent_m / self.range_m)
        )


def transform_lidar_point_to_radar(
    point_lidar: np.ndarray,
    T_enu_lidar_at_point: np.ndarray,
    T_enu_lidar_at_radar: np.ndarray,
    T_radar_lidar: np.ndarray,
) -> np.ndarray:
    """Transform a LiDAR-frame point at its label time into Radar coordinates.

    pyboreas defines T_radar_lidar as the LiDAR-from-Radar extrinsic; therefore
    T_enu_radar = T_enu_lidar @ inverse(T_radar_lidar).
    """
    point = np.asarray(point_lidar, dtype=np.float64).reshape(-1)
    if point.shape == (3,):
        point = np.append(point, 1.0)
    if point.shape != (4,) or not np.isfinite(point).all():
        raise ValueError("point_lidar must contain three or four finite values")
    if not np.isclose(point[3], 1.0):
        raise ValueError("point_lidar homogeneous coordinate must equal 1")
    for name, matrix in (
        ("T_enu_lidar_at_point", T_enu_lidar_at_point),
        ("T_enu_lidar_at_radar", T_enu_lidar_at_radar),
        ("T_radar_lidar", T_radar_lidar),
    ):
        if np.asarray(matrix).shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError(f"{name} must be a finite 4x4 matrix")

    point_enu = T_enu_lidar_at_point @ point
    T_enu_radar = T_enu_lidar_at_radar @ np.linalg.inv(T_radar_lidar)
    point_radar = np.linalg.inv(T_enu_radar) @ point_enu
    if not np.isfinite(point_radar).all():
        raise ValueError("Radar-frame object point is non-finite")
    return point_radar


def cartesian_to_polar(point_radar: np.ndarray) -> tuple[float, float]:
    """Return planar range and BOREAS azimuth in radians in [-pi, pi]."""
    point = np.asarray(point_radar, dtype=np.float64).reshape(-1)
    if point.size < 2 or not np.isfinite(point[:2]).all():
        raise ValueError("Radar point must contain finite x and y coordinates")
    return float(math.hypot(point[0], point[1])), float(math.atan2(point[1], point[0]))


def angular_error_rad(first_rad: float, second_rad: float) -> float:
    """Return the smallest absolute angular difference, including wrap-around."""
    return abs((first_rad - second_rad + math.pi) % (2.0 * math.pi) - math.pi)


def object_polar_geometry(
    center_radar: np.ndarray,
    corners_radar: np.ndarray,
) -> ObjectPolarGeometry:
    """Bound the object's radar-plane extent with its transformed 3D box corners."""
    center = np.asarray(center_radar, dtype=np.float64).reshape(-1)
    corners = np.asarray(corners_radar, dtype=np.float64)
    if center.size < 3 or not np.isfinite(center[:3]).all():
        raise ValueError("Object center must be finite 3D Radar coordinates")
    if corners.ndim != 2 or corners.shape[1] not in (3, 4) or not len(corners):
        raise ValueError("Object corners must be a non-empty Nx3 or Nx4 array")
    xyz = corners[:, :3]
    if not np.isfinite(xyz).all():
        raise ValueError("Object corners contain non-finite coordinates")

    range_m, azimuth_rad = cartesian_to_polar(center)
    horizontal_extent = float(
        np.max(np.linalg.norm(xyz[:, :2] - center[:2], axis=1))
    )
    range_extent = float(np.max(np.linalg.norm(xyz - center[:3], axis=1)))
    return ObjectPolarGeometry(range_m, azimuth_rad, range_extent, horizontal_extent)


def object_overlaps_radar_range(
    geometry: ObjectPolarGeometry,
    minimum_range_m: float,
    maximum_range_m: float,
) -> bool:
    if minimum_range_m < 0 or maximum_range_m < minimum_range_m:
        raise ValueError("Radar range bounds are invalid")
    object_near = geometry.range_m - geometry.range_extent_m
    object_far = geometry.range_m + geometry.range_extent_m
    return object_far >= minimum_range_m and object_near <= maximum_range_m


def associate_polar_candidates(
    geometry: ObjectPolarGeometry,
    candidates: list[RadarCandidate],
    range_padding_m: float,
    azimuth_padding_rad: float,
) -> tuple[RadarCandidate | None, float | None, float | None]:
    """Choose the in-gate intensity candidate with lowest normalized polar error."""
    if range_padding_m < 0 or azimuth_padding_rad < 0:
        raise ValueError("Association padding must be non-negative")
    range_gate = geometry.range_extent_m + range_padding_m
    azimuth_gate = geometry.azimuth_extent_rad + azimuth_padding_rad
    range_scale = max(range_gate, 1e-9)
    azimuth_scale = max(azimuth_gate, 1e-9)

    matches = []
    for candidate in candidates:
        range_error = abs(candidate.range_m - geometry.range_m)
        azimuth_error = angular_error_rad(candidate.azimuth_rad, geometry.azimuth_rad)
        if range_error > range_gate or azimuth_error > azimuth_gate:
            continue
        score = (range_error / range_scale) ** 2 + (azimuth_error / azimuth_scale) ** 2
        matches.append(
            (score, range_error, azimuth_error, -candidate.intensity,
             candidate.azimuth_index, candidate.range_bin, candidate)
        )
    if not matches:
        return None, None, None

    _, range_error, azimuth_error, _, _, _, candidate = min(matches)
    return candidate, float(range_error), float(azimuth_error)


def polar_candidate_match(
    geometry: ObjectPolarGeometry,
    candidate: RadarCandidate,
    range_padding_m: float,
    azimuth_padding_rad: float,
) -> tuple[float, float, float] | None:
    """Return normalized score and errors when a candidate falls inside the box gate."""
    if range_padding_m < 0 or azimuth_padding_rad < 0:
        raise ValueError("Association padding must be non-negative")
    range_error = abs(candidate.range_m - geometry.range_m)
    azimuth_error = angular_error_rad(candidate.azimuth_rad, geometry.azimuth_rad)
    range_gate = geometry.range_extent_m + range_padding_m
    azimuth_gate = geometry.azimuth_extent_rad + azimuth_padding_rad
    if range_error > range_gate or azimuth_error > azimuth_gate:
        return None
    score = (
        (range_error / max(range_gate, 1e-9)) ** 2
        + (azimuth_error / max(azimuth_gate, 1e-9)) ** 2
    )
    return float(score), float(range_error), float(azimuth_error)
