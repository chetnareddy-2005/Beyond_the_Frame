"""Availability state for the RGB-first optional-modality input layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModalityInputs:
    """Describe supplied inputs without loading or aligning sensor data."""

    rgb_source: str
    thermal_source: Optional[str] = None
    radar_source: Optional[str] = None
    thermal_frame_mapping_verified: bool = False

    def __post_init__(self) -> None:
        if not self.rgb_source.strip():
            raise ValueError("rgb_source must identify the required RGB input")
        if self.thermal_source is not None and not self.thermal_source.strip():
            raise ValueError("thermal_source must be non-empty when provided")
        if self.radar_source is not None and not self.radar_source.strip():
            raise ValueError("radar_source must be non-empty when provided")
        if self.thermal_frame_mapping_verified and self.thermal_source is None:
            raise ValueError("thermal frame mapping cannot be verified without thermal input")

    @property
    def rgb_available(self) -> bool:
        return True

    @property
    def thermal_available(self) -> bool:
        return self.thermal_source is not None

    @property
    def radar_available(self) -> bool:
        return self.radar_source is not None

    @property
    def synchronization_status(self) -> str:
        if not self.thermal_available:
            return "rgb_only"
        return "rgb_thermal" if self.thermal_frame_mapping_verified else "unsynchronized"
