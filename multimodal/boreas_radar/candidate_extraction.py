"""Deterministic local-maximum extraction from raw BOREAS polar intensity scans."""

import cv2
import numpy as np

from .config import CandidateExtractionConfig
from .radar_data import RadarCandidate, RadarScan


def extract_candidates(
    scan: RadarScan, config: CandidateExtractionConfig
) -> list[RadarCandidate]:
    """Extract intensity peaks; these are candidates, not Radar object detections."""
    polar = scan.polar_intensity
    height, width = polar.shape
    kernel = np.ones(
        (
            2 * config.azimuth_nms_radius + 1,
            2 * config.range_nms_radius + 1,
        ),
        dtype=np.uint8,
    )

    # Azimuth is circular; duplicate rows before the local-maximum operation.
    circular = np.concatenate((polar, polar, polar), axis=0)
    local_max = cv2.dilate(circular, kernel)[height : 2 * height]
    bins = np.arange(width)
    range_centers_m = (bins + 0.5) * scan.resolution_m
    range_mask = range_centers_m >= config.minimum_range_m
    eligible = (
        (polar >= config.intensity_threshold)
        & (polar == local_max)
        & scan.valid_azimuths[:, None]
        & range_mask[None, :]
    )

    row_indices, bin_indices = np.nonzero(eligible)
    ordered = sorted(
        zip(row_indices.tolist(), bin_indices.tolist()),
        key=lambda item: (
            -float(polar[item[0], item[1]]),
            item[0],
            item[1],
        ),
    )

    selected: list[tuple[int, int]] = []
    for row, range_bin in ordered:
        suppressed = False
        for selected_row, selected_bin in selected:
            angular_distance = min(
                (row - selected_row) % height,
                (selected_row - row) % height,
            )
            if (
                angular_distance <= config.azimuth_nms_radius
                and abs(range_bin - selected_bin) <= config.range_nms_radius
            ):
                suppressed = True
                break
        if not suppressed:
            selected.append((row, range_bin))

    return [
        RadarCandidate(
            frame_timestamp_us=scan.frame_timestamp_us,
            azimuth_timestamp_us=int(scan.azimuth_timestamps_us[row]),
            azimuth_index=row,
            range_bin=range_bin,
            range_m=float((range_bin + 0.5) * scan.resolution_m),
            azimuth_rad=float(scan.azimuths_rad[row]),
            intensity=float(polar[row, range_bin]),
        )
        for row, range_bin in selected
    ]
