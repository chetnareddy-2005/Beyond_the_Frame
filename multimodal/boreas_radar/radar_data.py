"""BOREAS Radar frame access and lossless candidate metadata types."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RadarScan:
    frame_timestamp_us: int
    polar_intensity: np.ndarray
    azimuths_rad: np.ndarray
    azimuth_timestamps_us: np.ndarray
    valid_azimuths: np.ndarray
    resolution_m: float

    @classmethod
    def from_pyboreas_frame(cls, frame) -> "RadarScan":
        if frame.polar is None or frame.timestamps is None or frame.azimuths is None:
            raise ValueError(f"Radar frame {frame.frame} has not been loaded")

        timestamps = np.asarray(frame.timestamps).reshape(-1).astype(np.int64)
        azimuths = np.asarray(frame.azimuths).reshape(-1).astype(np.float64)
        polar = np.asarray(frame.polar, dtype=np.float32)
        valid = np.asarray(frame.chirp_type).reshape(-1).astype(bool)

        if polar.ndim != 2:
            raise ValueError(f"Expected a 2D polar scan, got {polar.shape}")
        if not (polar.shape[0] == len(timestamps) == len(azimuths) == len(valid)):
            raise ValueError("Radar azimuth arrays have inconsistent row counts")
        if not np.isfinite(polar).all() or not np.isfinite(azimuths).all():
            raise ValueError(f"Radar frame {frame.frame} contains non-finite data")
        if frame.resolution <= 0:
            raise ValueError("Radar range resolution must be positive")

        return cls(
            frame_timestamp_us=int(frame.timestamp_micro),
            polar_intensity=polar,
            azimuths_rad=azimuths,
            azimuth_timestamps_us=timestamps,
            valid_azimuths=valid,
            resolution_m=float(frame.resolution),
        )


@dataclass(frozen=True)
class RadarCandidate:
    frame_timestamp_us: int
    azimuth_timestamp_us: int
    azimuth_index: int
    range_bin: int
    range_m: float
    azimuth_rad: float
    intensity: float

    @property
    def point_radar(self) -> np.ndarray:
        return np.array(
            [
                self.range_m * np.cos(self.azimuth_rad),
                self.range_m * np.sin(self.azimuth_rad),
                0.0,
                1.0,
            ],
            dtype=np.float64,
        )
