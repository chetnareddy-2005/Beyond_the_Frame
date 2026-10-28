"""Run the project's YOLOv5 validation with Windows/Linux checkpoint compatibility."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YOLO_ROOT = ROOT / "base_detector/yolov5_ghostnet_AFNO"
sys.path.insert(0, str(YOLO_ROOT))


def patch_checkpoint_path_compatibility() -> None:
    # Linux-created checkpoints pickle pathlib._local.PosixPath. The app already
    # applies this compatibility shim when loading the same weights on Windows.
    compatibility = types.ModuleType("pathlib._local")
    compatibility.PosixPath = pathlib.WindowsPath
    compatibility.WindowsPath = pathlib.WindowsPath
    compatibility.PurePosixPath = pathlib.PureWindowsPath
    compatibility.PureWindowsPath = pathlib.PureWindowsPath
    pathlib.PosixPath = pathlib.WindowsPath
    pathlib._local = compatibility
    sys.modules["pathlib._local"] = compatibility


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", required=True)
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    patch_checkpoint_path_compatibility()
    import cv2  # noqa: E402
    import torch  # noqa: E402

    cv2.setNumThreads(0)
    torch.set_num_threads(2)
    import val  # noqa: E402

    out = ROOT / "datasets/processed/phase6_candidate_runs_2026-10-22"
    metrics, per_class, timing = val.run(
        data=str(ROOT / "datasets/processed/phase6_candidate_dataset_2026-10-22/data.yaml"),
        weights=[str((ROOT / args.weights).resolve())],
        batch_size=8,
        imgsz=640,
        conf_thres=0.25,
        iou_thres=0.45,
        task=args.split,
        device="cpu",
        workers=2,
        verbose=True,
        save_txt=True,
        save_conf=True,
        project=str(out),
        name=args.name,
        exist_ok=False,
        half=False,
        plots=False,
    )
    result = {"metrics": metrics, "per_class": per_class, "timing": timing}
    result_path = out / args.name / "validation_metrics.json"
    if result_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing validation record: {result_path}")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, default=lambda value: value.tolist() if hasattr(value, "tolist") else float(value)))
    print({**result, "saved_to": str(result_path)})


if __name__ == "__main__":
    main()
