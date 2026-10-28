"""Phase 4 single-frame YOLOv5 detection over prepared Phase 3 frames."""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
import time
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from backend.device import resolve_device
from backend.frames import PreparedFrame
from backend.runs import RunError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    frame_index: int | None
    timestamp_sec: float | None
    source_type: str
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "source_type": self.source_type,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": self.confidence,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
        }


@dataclass(frozen=True)
class DetectionResult:
    source_type: str
    input_type: str | None
    run_id: str | None
    live_reference_id: str | None
    frame_index: int | None
    timestamp_sec: float | None
    width: int
    height: int
    model_name: str
    inference_time_ms: float
    detections: tuple[Detection, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "input_type": self.input_type,
            "run_id": self.run_id,
            "live_reference_id": self.live_reference_id,
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "width": self.width,
            "height": self.height,
            "model_name": self.model_name,
            "inference_time_ms": self.inference_time_ms,
            "detection_count": len(self.detections),
            "detections": [detection.as_dict() for detection in self.detections],
        }


@dataclass(frozen=True)
class _YoloRuntime:
    torch: Any
    model: Any
    letterbox: Callable[..., Any]
    non_max_suppression: Callable[..., Any]
    scale_boxes: Callable[..., Any]
    device: Any
    image_size: tuple[int, int]
    stride: int
    names: dict[int, str]
    checkpoint_name: str
    confidence_threshold: float
    iou_threshold: float


class DetectionService:
    """Loads one configured detector and processes one prepared frame per call."""

    def __init__(
        self,
        project_root: Path,
        runtime_loader: Callable[..., _YoloRuntime] | None = None,
    ):
        self.project_root = Path(project_root)
        self._runtime_loader = runtime_loader or self._load_runtime
        self._uses_default_runtime_loader = runtime_loader is None
        self._runtime: _YoloRuntime | None = None
        self._thermal_runtime: _YoloRuntime | None = None
        self._lock = threading.RLock()

    def _load_runtime(self, *, input_type: str | None = None) -> _YoloRuntime:
        from backend.run_pipeline import (
            DETECTOR_CONFIDENCE_THRESHOLD,
            DETECTOR_IMAGE_SIZE,
            DETECTOR_IOU_THRESHOLD,
            resolve_detector_checkpoint,
        )

        detector_root = self.project_root / "base_detector" / "yolov5_ghostnet_AFNO"
        if not detector_root.is_dir():
            raise FileNotFoundError("The configured YOLOv5 detector is unavailable")
        detector_root_text = str(detector_root)
        if detector_root_text not in sys.path:
            sys.path.insert(0, detector_root_text)

        import torch
        from models.common import DetectMultiBackend
        from utils.augmentations import letterbox
        from utils.general import check_img_size, non_max_suppression, scale_boxes

        weights, _ = resolve_detector_checkpoint(self.project_root, input_type=input_type)
        device_name = resolve_device()
        LOGGER.info("Detector device: %s", device_name)
        device = torch.device(device_name)
        with self._windows_checkpoint_compatibility():
            model = DetectMultiBackend(
                weights,
                device=device,
                data=str(detector_root / "data.yaml"),
                fp16=False,
            )
        model.eval()
        self._validate_custom_architecture(model)
        model_stride = model.stride
        stride = int(model_stride.max()) if hasattr(model_stride, "max") else int(model_stride)
        stride = max(stride, 32)
        size = check_img_size(DETECTOR_IMAGE_SIZE, s=stride)
        raw_names = model.names
        if isinstance(raw_names, dict):
            names = {int(class_id): str(name) for class_id, name in raw_names.items()}
        else:
            names = {index: str(name) for index, name in enumerate(raw_names)}
        if not names:
            raise ValueError("The configured detector has no class names")
        if input_type == "thermal" and names != {0: "Bird", 1: "Drone"}:
            raise ValueError(
                "The thermal detector checkpoint must declare class 0=Bird and class 1=Drone"
            )
        LOGGER.info(
            "Loaded Phase 4 detector %s with %d classes",
            weights.name,
            len(names),
        )
        return _YoloRuntime(
            torch=torch,
            model=model,
            letterbox=letterbox,
            non_max_suppression=non_max_suppression,
            scale_boxes=scale_boxes,
            device=device,
            image_size=(size, size),
            stride=stride,
            names=names,
            checkpoint_name=weights.name,
            confidence_threshold=DETECTOR_CONFIDENCE_THRESHOLD,
            iou_threshold=DETECTOR_IOU_THRESHOLD,
        )

    @staticmethod
    def _validate_custom_architecture(model: Any) -> None:
        required = {"GhostConv", "GhostBottleneck", "AFNO2D"}
        present = {type(module).__name__ for module in model.modules()}
        missing = required - present
        if missing:
            raise ValueError(
                "The configured detector checkpoint is missing required custom components: "
                + ", ".join(sorted(missing))
            )

    @staticmethod
    @contextmanager
    def _windows_checkpoint_compatibility():
        if os.name != "nt":
            yield
            return
        missing = object()
        original_posix_path = pathlib.PosixPath
        original_local_attr = getattr(pathlib, "_local", missing)
        original_local_module = sys.modules.get("pathlib._local", missing)
        pathlib.PosixPath = pathlib.WindowsPath
        compatibility_module = types.ModuleType("pathlib._local")
        compatibility_module.PosixPath = pathlib.WindowsPath
        compatibility_module.WindowsPath = pathlib.WindowsPath
        compatibility_module.PurePosixPath = pathlib.PureWindowsPath
        compatibility_module.PureWindowsPath = pathlib.PureWindowsPath
        sys.modules["pathlib._local"] = compatibility_module
        pathlib._local = compatibility_module
        try:
            yield
        finally:
            pathlib.PosixPath = original_posix_path
            if original_local_module is missing:
                sys.modules.pop("pathlib._local", None)
            else:
                sys.modules["pathlib._local"] = original_local_module
            if original_local_attr is missing:
                delattr(pathlib, "_local")
            else:
                pathlib._local = original_local_attr

    def detect(self, prepared: PreparedFrame) -> tuple[DetectionResult, np.ndarray]:
        if not isinstance(prepared, PreparedFrame):
            raise RunError(422, "invalid_prepared_frame", "A Phase 3 prepared frame is required.")

        if prepared.input_type == "radar":
            raise RunError(
                415,
                "modality_not_supported",
                "The configured YOLOv5 detector supports RGB and thermal frames only.",
            )

        if prepared.source_type not in {"upload", "cctv", "photo"}:
            raise RunError(
                415,
                "modality_not_supported",
                "The configured YOLOv5 detector supports photo, upload, and CCTV inputs only.",
            )

        allowed_input_types = {None, "rgb", "thermal"}
        if prepared.input_type not in allowed_input_types:
            raise RunError(
                415,
                "modality_not_supported",
                "The configured YOLOv5 detector supports RGB and thermal frames only.",
            )

        if (
            (prepared.source_type == "upload" and (prepared.input_type not in {"rgb", "thermal"} or not prepared.run_id))
            or (prepared.source_type == "cctv" and (prepared.input_type is not None or not prepared.live_reference_id))
            or (prepared.source_type == "photo" and (prepared.input_type != "rgb" or prepared.run_id is not None or prepared.live_reference_id is not None))
        ):
            raise RunError(422, "invalid_prepared_frame", "The prepared frame source metadata is invalid.")
        frame = prepared.frame
        if (
            prepared.validation_status != "valid"
            or not isinstance(frame, np.ndarray)
            or frame.size == 0
            or frame.ndim not in (2, 3)
            or frame.dtype != np.uint8
            or frame.shape[1] != prepared.width
            or frame.shape[0] != prepared.height
        ):
            raise RunError(422, "invalid_prepared_frame", "The prepared frame is malformed or empty.")
        if prepared.input_type == "rgb":
            if frame.ndim != 3 or frame.shape[2] != 3 or prepared.channels != 3 or prepared.dtype != str(frame.dtype):
                raise RunError(422, "invalid_prepared_frame", "RGB frames must be three-channel uint8 arrays.")
        elif prepared.input_type == "thermal":
            if frame.ndim == 3 and frame.shape[2] not in (1, 3):
                raise RunError(422, "invalid_prepared_frame", "Thermal frames must be single-channel or 3-channel uint8 arrays.")
            if prepared.channels not in (1, 3) or prepared.dtype != str(frame.dtype):
                raise RunError(422, "invalid_prepared_frame", "Thermal frame metadata is malformed.")

        with self._lock:
            try:
                runtime = self._runtime_instance(prepared.input_type)
                result, annotated = self._infer(runtime, prepared)
                return result, annotated
            except RunError:
                raise
            except Exception as error:
                LOGGER.exception("Phase 4 detector failed to process one prepared frame")
                raise RunError(
                    503,
                    "detector_inference_failed",
                    "The configured detector could not process this frame.",
                ) from error

    def _runtime_instance(self, input_type: str | None = None) -> _YoloRuntime:
        runtime_name = "_thermal_runtime" if input_type == "thermal" else "_runtime"
        runtime = getattr(self, runtime_name, None)
        if runtime is None:
            try:
                if self._uses_default_runtime_loader:
                    runtime = self._load_runtime(input_type=input_type)
                else:
                    runtime = self._runtime_loader()
                setattr(self, runtime_name, runtime)
            except Exception as error:
                LOGGER.exception("Phase 4 YOLOv5 detector initialization failed")
                raise RunError(
                    503,
                    "detector_unavailable",
                    "The configured YOLOv5 detector is unavailable.",
                ) from error
        return runtime

    def _infer(self, runtime: _YoloRuntime, prepared: PreparedFrame) -> tuple[DetectionResult, np.ndarray]:
        import cv2

        frame = prepared.frame
        if prepared.input_type == "thermal" and frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif prepared.input_type == "thermal" and frame.ndim == 3 and frame.shape[2] == 1:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        letterboxed = runtime.letterbox(
            frame,
            runtime.image_size,
            stride=runtime.stride,
            auto=True,
        )[0]
        model_image = letterboxed.transpose((2, 0, 1))[::-1]
        model_image = np.ascontiguousarray(model_image)
        tensor = runtime.torch.from_numpy(model_image).to(runtime.device).float().div_(255.0)
        if tensor.ndim == 3:
            tensor = tensor.unsqueeze(0)

        start = time.perf_counter()
        with runtime.torch.inference_mode():
            raw_predictions = runtime.model(tensor, augment=False, visualize=False)
            predictions = runtime.non_max_suppression(
                raw_predictions,
                conf_thres=runtime.confidence_threshold,
                iou_thres=runtime.iou_threshold,
                classes=None,
                agnostic=False,
                max_det=300,
            )
        inference_time_ms = max(0.0, (time.perf_counter() - start) * 1000.0)
        if len(predictions) != 1:
            raise ValueError("Detector returned an invalid batch result")

        detections: list[Detection] = []
        prediction = predictions[0]
        if prediction is not None and len(prediction):
            with runtime.torch.inference_mode():
                prediction[:, :4] = runtime.scale_boxes(
                    tensor.shape[2:],
                    prediction[:, :4],
                    frame.shape,
                ).round()
                rows = prediction.detach().cpu().tolist()
        else:
            rows = []
        for row in rows:
            if len(row) < 6:
                continue
            x1, y1, x2, y2, confidence, raw_class_id = map(float, row[:6])
            class_id = int(raw_class_id)
            if (
                not np.isfinite([x1, y1, x2, y2, confidence, raw_class_id]).all()
                or raw_class_id != class_id
                or confidence < runtime.confidence_threshold
                or confidence > 1
                or class_id not in runtime.names
            ):
                continue
            x1 = min(max(x1, 0.0), float(prepared.width))
            y1 = min(max(y1, 0.0), float(prepared.height))
            x2 = min(max(x2, 0.0), float(prepared.width))
            y2 = min(max(y2, 0.0), float(prepared.height))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(
                Detection(
                    frame_index=prepared.frame_index,
                    timestamp_sec=prepared.timestamp_sec,
                    source_type=prepared.source_type,
                    class_id=class_id,
                    class_name=runtime.names[class_id],
                    confidence=confidence,
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                )
            )

        annotated = prepared.frame.copy()
        if prepared.input_type == "thermal" and annotated.ndim == 2:
            annotated = cv2.cvtColor(annotated, cv2.COLOR_GRAY2BGR)
        for object_index, detection in enumerate(detections, start=1):
            top_left = (int(detection.x1), int(detection.y1))
            bottom_right = (int(detection.x2), int(detection.y2))
            class_key = detection.class_name.strip().lower()
            color = (65, 205, 110) if "bird" in class_key else (235, 155, 35) if "drone" in class_key or "uav" in class_key else (210, 90, 210)
            frame_scale = max(prepared.width, prepared.height) / 960
            line_width = max(2, int(round(frame_scale * 2)))
            padding = max(5, int(round(frame_scale * 5)))
            font_scale = max(0.55, min(1.5, frame_scale * 0.72))
            font_width = max(1, int(round(frame_scale * 1.7)))
            source_label = "Object" if prepared.source_type == "photo" else "Frame object"
            label = f"{source_label} {object_index}  |  {detection.class_name.upper()}  |  {detection.confidence:.1%}"
            # Dark outer stroke keeps thin boxes visible against both sky and clouds.
            cv2.rectangle(annotated, top_left, bottom_right, (12, 20, 28), line_width + 2)
            cv2.rectangle(annotated, top_left, bottom_right, color, line_width)
            (text_width, text_height), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_width
            )
            tag_x = min(max(0, top_left[0]), max(0, prepared.width - text_width - padding * 2))
            tag_y = top_left[1] - text_height - baseline - padding * 2
            if tag_y < 0:
                tag_y = min(prepared.height - text_height - baseline - padding * 2, top_left[1])
            tag_y = max(0, tag_y)
            cv2.rectangle(
                annotated,
                (tag_x, tag_y),
                (min(prepared.width - 1, tag_x + text_width + padding * 2), min(prepared.height - 1, tag_y + text_height + baseline + padding * 2)),
                color,
                -1,
            )
            cv2.putText(
                annotated,
                label,
                (tag_x + padding, tag_y + padding + text_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (255, 255, 255),
                font_width,
                cv2.LINE_AA,
            )

        result = DetectionResult(
            source_type=prepared.source_type,
            input_type=prepared.input_type,
            run_id=prepared.run_id,
            live_reference_id=prepared.live_reference_id,
            frame_index=prepared.frame_index,
            timestamp_sec=prepared.timestamp_sec,
            width=prepared.width,
            height=prepared.height,
            model_name=f"YOLOv5 + GhostNet + AFNO ({runtime.checkpoint_name})",
            inference_time_ms=inference_time_ms,
            detections=tuple(detections),
        )
        return result, annotated

    def close(self) -> None:
        with self._lock:
            rgb_runtime, self._runtime = self._runtime, None
            thermal_runtime, self._thermal_runtime = self._thermal_runtime, None
        for runtime in (rgb_runtime, thermal_runtime):
            if runtime is not None:
                close = getattr(runtime.model, "close", None)
                if callable(close):
                    close()
