"""Optional multimodal interface layer for RGB + Thermal/IR + Radar evidence.

This package is intentionally isolated and does not modify the existing RGB-first
execution pipeline. It defines the contracts, synchronization helpers, and adapters
needed for future real multimodal data integration.
"""

from .contracts import EvidenceRecord, FrameReference, SynchronizedFrame, TrackIdentity
from .inputs import ModalityInputs
from .radar_adapter import RadarAdapter
from .thermal_adapter import ThermalAdapter

__all__ = [
    "EvidenceRecord",
    "FrameReference",
    "ModalityInputs",
    "SynchronizedFrame",
    "TrackIdentity",
    "ThermalAdapter",
    "RadarAdapter",
]
