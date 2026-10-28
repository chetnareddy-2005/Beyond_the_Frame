"""Run the frozen Phase 6 YOLO experiment without mutating Python packages."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YOLO_ROOT = ROOT / "base_detector/yolov5_ghostnet_AFNO"
PYTHON_ROOT = Path(sys.executable).parent
for package_path in (
    PYTHON_ROOT / "Lib/site-packages",
    PYTHON_ROOT / "Lib/site-packages/win32",
    PYTHON_ROOT / "Lib/site-packages/win32/lib",
    PYTHON_ROOT / "Lib/site-packages/Pythonwin",
):
    sys.path.append(str(package_path))
sys.path.insert(0, str(YOLO_ROOT))

import train  # noqa: E402
from utils import dataloaders  # noqa: E402


def _center_crop_without_warp(image, targets=(), segments=(), border=(0, 0), **kwargs):
    """Apply YOLO mosaic's center crop without OpenCV's failing warpAffine."""
    if segments:
        raise ValueError("Phase 6 source annotations are boxes only; polygon segments are unsupported")
    height, width = image.shape[:2]
    out_h = max(1, height + 2 * int(border[0]))
    out_w = max(1, width + 2 * int(border[1]))
    top = max(0, (height - out_h) // 2)
    left = max(0, (width - out_w) // 2)
    image = image[top : top + out_h, left : left + out_w]

    if len(targets):
        targets = targets.copy()
        targets[:, [1, 3]] -= left
        targets[:, [2, 4]] -= top
        targets[:, [1, 3]] = targets[:, [1, 3]].clip(0, out_w)
        targets[:, [2, 4]] = targets[:, [2, 4]].clip(0, out_h)
        keep = (targets[:, 3] - targets[:, 1] > 2) & (targets[:, 4] - targets[:, 2] > 2)
        targets = targets[keep]
    return image, targets


def main() -> None:
    # The upstream entry point performs a Git fetch and auto-installs optional
    # packages on every run. Keep this reproducible run offline and read-only.
    train.check_git_status = lambda *args, **kwargs: None
    train.check_requirements = lambda *args, **kwargs: None
    # On this Windows CPU setup, OpenCV and PyTorch each start Intel OpenMP
    # pools from different bundled runtimes. Let PyTorch own CPU parallelism.
    import cv2
    import torch

    cv2.setNumThreads(0)
    torch.set_num_threads(2)
    # cv2.warpAffine in YOLOv5's random_perspective initializes a second
    # libiomp5md.dll beside PyTorch's copy on this machine and aborts training.
    # Preserve crop geometry for mosaic and safely bypass only affine warping.
    dataloaders.random_perspective = _center_crop_without_warp
    train.main(train.parse_opt())


if __name__ == "__main__":
    main()
