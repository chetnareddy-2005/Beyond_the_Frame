"""Fail-closed primitives for Caltech RGB-Thermal spatial mapping validation.

Timestamp matching and stereo rectification do not identify a Thermal pixel
region for an RGB box. This module keeps coordinate conversion explicit and
requires valid calibration provenance plus a measured registration confidence
before a Thermal crop can be emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


REGISTRATION_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class CalibrationCheck:
    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.x1, self.y1, self.x2, self.y2


def validate_calibration_provenance(
    *,
    rgb_declared_topic: str,
    thermal_declared_topic: str,
    observed_topics: Sequence[str],
    rgb_calibration_size: tuple[int, int],
    thermal_calibration_size: tuple[int, int],
    rgb_source_size: tuple[int, int],
    thermal_source_size: tuple[int, int],
) -> CalibrationCheck:
    """Check that calibration declarations and native image sizes match inputs."""
    observed = set(observed_topics)
    reasons = []
    if rgb_declared_topic not in observed:
        reasons.append("RGB_CALIBRATION_TOPIC_NOT_IN_RECORDING")
    if thermal_declared_topic not in observed:
        reasons.append("THERMAL_CALIBRATION_TOPIC_NOT_IN_RECORDING")
    if rgb_calibration_size != rgb_source_size:
        reasons.append("RGB_NATIVE_SIZE_DIFFERS_FROM_CALIBRATION")
    if thermal_calibration_size != thermal_source_size:
        reasons.append("THERMAL_NATIVE_SIZE_DIFFERS_FROM_CALIBRATION")
    return CalibrationCheck(valid=not reasons, reasons=tuple(reasons))


def padded_box_to_source(
    box: Box,
    *,
    source_size: tuple[int, int],
    padded_size: tuple[int, int],
) -> Optional[Box]:
    """Undo centered square padding from (width, height) video coordinates."""
    source_width, source_height = source_size
    padded_width, padded_height = padded_size
    if min(source_width, source_height, padded_width, padded_height) <= 0:
        return None
    if padded_width != padded_height or padded_width < source_width or padded_height < source_height:
        return None
    left = (padded_width - source_width) // 2
    top = (padded_height - source_height) // 2
    source_box = Box(
        box.x1 - left,
        box.y1 - top,
        box.x2 - left,
        box.y2 - top,
    )
    if (
        source_box.x1 < 0
        or source_box.y1 < 0
        or source_box.x2 > source_width
        or source_box.y2 > source_height
        or source_box.x2 <= source_box.x1
        or source_box.y2 <= source_box.y1
    ):
        return None
    return source_box


def source_box_to_padded(
    box: Box,
    *,
    source_size: tuple[int, int],
    padded_size: tuple[int, int],
) -> Optional[Box]:
    """Apply centered square padding to a source-image box."""
    source_width, source_height = source_size
    padded_width, padded_height = padded_size
    if min(source_width, source_height, padded_width, padded_height) <= 0:
        return None
    if padded_width != padded_height or padded_width < source_width or padded_height < source_height:
        return None
    left = (padded_width - source_width) // 2
    top = (padded_height - source_height) // 2
    padded_box = Box(
        box.x1 + left,
        box.y1 + top,
        box.x2 + left,
        box.y2 + top,
    )
    if (
        padded_box.x1 < left
        or padded_box.y1 < top
        or padded_box.x2 > left + source_width
        or padded_box.y2 > top + source_height
        or padded_box.x2 <= padded_box.x1
        or padded_box.y2 <= padded_box.y1
    ):
        return None
    return padded_box


def confidence_gated_thermal_crop(
    thermal_image: np.ndarray,
    thermal_box: Optional[Box],
    confidence: Optional[float],
    *,
    threshold: float = REGISTRATION_CONFIDENCE_THRESHOLD,
) -> Optional[np.ndarray]:
    """Return a Thermal crop only for a measured, confident, in-bounds mapping."""
    if (
        not isinstance(thermal_image, np.ndarray)
        or thermal_image.size == 0
        or thermal_image.ndim not in (2, 3)
        or thermal_box is None
        or confidence is None
        or not np.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
        or confidence < threshold
    ):
        return None
    height, width = thermal_image.shape[:2]
    x1, y1, x2, y2 = thermal_box.as_tuple()
    if (
        not np.isfinite([x1, y1, x2, y2]).all()
        or x1 < 0
        or y1 < 0
        or x2 > width
        or y2 > height
        or x2 <= x1
        or y2 <= y1
    ):
        return None
    ix1, iy1 = int(np.floor(x1)), int(np.floor(y1))
    ix2, iy2 = int(np.ceil(x2)), int(np.ceil(y2))
    return thermal_image[iy1:iy2, ix1:ix2].copy()


__all__ = [
    "Box",
    "CalibrationCheck",
    "REGISTRATION_CONFIDENCE_THRESHOLD",
    "confidence_gated_thermal_crop",
    "padded_box_to_source",
    "source_box_to_padded",
    "validate_calibration_provenance",
]
