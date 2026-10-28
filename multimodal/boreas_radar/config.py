"""Explicit, reproducible parameters for the BOREAS validation experiment."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateExtractionConfig:
    intensity_threshold: float = 0.4
    minimum_range_m: float = 2.5
    azimuth_nms_radius: int = 2
    range_nms_radius: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.intensity_threshold <= 1.0:
            raise ValueError("intensity_threshold must be in [0, 1]")
        if self.minimum_range_m < 0:
            raise ValueError("minimum_range_m must be non-negative")
        if self.azimuth_nms_radius < 0 or self.range_nms_radius < 0:
            raise ValueError("NMS radii must be non-negative")


@dataclass(frozen=True)
class AssociationConfig:
    maximum_scan_delta_seconds: float = 0.15
    maximum_candidate_delta_seconds: float = 0.15
    bounding_box_margin_pixels: float = 8.0

    def __post_init__(self) -> None:
        if self.maximum_scan_delta_seconds < 0:
            raise ValueError("maximum_scan_delta_seconds must be non-negative")
        if self.maximum_candidate_delta_seconds < 0:
            raise ValueError("maximum_candidate_delta_seconds must be non-negative")
        if self.bounding_box_margin_pixels < 0:
            raise ValueError("bounding_box_margin_pixels must be non-negative")
