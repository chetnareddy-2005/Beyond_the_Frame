"""Lightweight experiment metadata helper for reproducible local runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import torch
except Exception:  # pragma: no cover - optional runtime dependency
    torch = None

try:
    import platform
except Exception:  # pragma: no cover
    platform = None


def _runtime_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    if platform is not None:
        versions["platform"] = platform.platform()
        versions["python"] = platform.python_version()
    if torch is not None:
        versions["torch"] = getattr(torch, "__version__", "unknown")
    return versions


def make_record(
    *,
    experiment_id: str,
    source_video: str,
    checkpoint_path: str,
    configuration: str,
    command: str,
    confidence_threshold: Optional[float] = None,
    reliability_threshold: Optional[float] = None,
    preprocessing: str,
    output_paths: Iterable[str],
    notes: str = "",
    software_versions: Optional[Dict[str, str]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "experiment_id": experiment_id,
        "source_video": source_video,
        "checkpoint_path": checkpoint_path,
        "configuration": configuration,
        "command": command,
        "confidence_threshold": confidence_threshold,
        "reliability_threshold": reliability_threshold,
        "preprocessing": preprocessing,
        "output_paths": list(output_paths),
        "software_versions": software_versions or _runtime_versions(),
        "notes": notes,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    return record


def write_manifest(path: str | Path, record: Dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return target


if __name__ == "__main__":
    example = make_record(
        experiment_id="demo-run",
        source_video="datasets/videos/example.mp4",
        checkpoint_path="artifacts/checkpoints/best.pt",
        configuration="YOLOv5 + GhostNet + AFNO baseline",
        command="python detect.py --track --weights ...",
        confidence_threshold=0.25,
        reliability_threshold=0.6,
        preprocessing="square-padding applied to original video",
        output_paths=["runs/detect/exp/tracks.csv"],
        notes="Baseline smoke test record",
    )
    print(json.dumps(example, indent=2, sort_keys=True))
