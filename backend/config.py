"""Backend paths for the existing project result files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_UPLOAD_BYTES = 250 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    project_root: Path

    @property
    def results_root(self) -> Path:
        return self.project_root / "results" / "caltech_rgb_tracking"

    @property
    def dashboard_runs_root(self) -> Path:
        return self.project_root / "results" / "dashboard_runs"

    @property
    def auth_database_path(self) -> Path:
        return self.project_root / "results" / "auth" / "users.sqlite3"

    @property
    def max_upload_bytes(self) -> int:
        value = int(os.environ.get("BTF_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))
        if value <= 0:
            raise ValueError("BTF_MAX_UPLOAD_BYTES must be a positive integer")
        return value

    @property
    def paths(self) -> dict[str, Path]:
        results = self.results_root
        phase4_rgb = results / "phase4_uncertain_evidence"
        phase4_thermal = results / "phase4_thermal_evidence"
        phase4_radar = results / "phase4_radar_evidence"
        phase5 = results / "phase5_fusion"
        return {
            "tracks": results / "tracks.csv",
            "temporal": results / "temporal_features.csv",
            "motion": results / "track_motion.csv",
            "reliability": results / "reliability_results.csv",
            "baseline": results / "fusion_baseline.csv",
            "exploratory": results / "fusion_vlm_exploratory.csv",
            "selected_evidence": phase4_rgb / "selected_evidence.csv",
            "vlm": phase4_rgb / "vlm_results_17track.csv",
            "thermal_coverage": phase4_thermal / "thermal_coverage.csv",
            "thermal_manifest": phase4_thermal / "thermal_frame_manifest.csv",
            "radar_summary": phase4_radar / "radar_validation_summary.csv",
            "radar_report": phase4_radar / "radar_validation_report.txt",
            "fusion": phase5 / "phase5_fusion_results.csv",
            "availability": phase5 / "phase5_evidence_availability.csv",
            "fusion_report": phase5 / "phase5_fusion_report.txt",
            "evidence_root": phase4_rgb,
        }


def get_settings(project_root: str | Path | None = None) -> Settings:
    """Resolve one project root, optionally overridden for deployments/tests."""
    configured_root = project_root or os.environ.get("BTF_PROJECT_ROOT")
    root = Path(configured_root).expanduser() if configured_root else Path(__file__).resolve().parents[1]
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project root does not exist: {root}")
    return Settings(project_root=root)
