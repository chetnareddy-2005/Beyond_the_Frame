import base64
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import numpy as np
import torch

from backend.app import create_server
from backend.detection import DetectionService, _YoloRuntime
from backend.frames import PreparedFrame
from backend.runs import RunError
from backend.tests.test_auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME


def prepared(frame, *, input_type="rgb", source_type="upload"):
    height = frame.shape[0] if frame.ndim >= 2 else 0
    width = frame.shape[1] if frame.ndim >= 2 else 0
    return PreparedFrame(
        frame=frame,
        source_type=source_type,
        input_type=input_type,
        run_id="RUN_20261002_000000_" + "a" * 24 if source_type == "upload" else None,
        live_reference_id="live-reference" if source_type == "cctv" else None,
        frame_index=7 if source_type == "upload" else None,
        timestamp_sec=None,
        width=width,
        height=height,
        channels=frame.shape[2] if frame.ndim == 3 else 1,
        dtype=str(frame.dtype),
        validation_status="valid",
        preprocessing_status="validated_contiguous_copy",
    )


class FakeModel:
    def __init__(self, error=None):
        self.error = error
        self.inputs = []

    def __call__(self, tensor, **kwargs):
        self.inputs.append((tensor.detach().clone(), kwargs))
        if self.error is not None:
            raise self.error
        return "raw-predictions"


def fake_runtime(rows=None, *, model_error=None, loader_name="best.pt", names=None):
    model = FakeModel(model_error)
    nms_calls = []

    def nms(predictions, **kwargs):
        nms_calls.append((predictions, kwargs))
        values = rows or []
        detections = torch.tensor(values, dtype=torch.float32).reshape(-1, 6)
        return [detections]

    runtime = _YoloRuntime(
        torch=torch,
        model=model,
        letterbox=lambda frame, _size, **_kwargs: (frame, None, None),
        non_max_suppression=nms,
        scale_boxes=lambda _input_shape, boxes, _frame_shape: boxes,
        device=torch.device("cpu"),
        image_size=(640, 640),
        stride=32,
        names=names or {0: "drone", 1: "bird"},
        checkpoint_name=loader_name,
        confidence_threshold=0.25,
        iou_threshold=0.45,
    )
    return runtime, nms_calls


class Phase4DetectionTests(unittest.TestCase):
    def test_afno_preserves_square_and_rectangular_spatial_dimensions(self):
        detector_root = str(Path(__file__).resolve().parents[2] / "base_detector" / "yolov5_ghostnet_AFNO")
        previous_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "models"
            or name.startswith("models.")
            or name == "utils"
            or name.startswith("utils.")
        }
        sys.path.insert(0, detector_root)
        try:
            from models.common import AFNO2D
        finally:
            sys.path.remove(detector_root)
            for name in tuple(sys.modules):
                if (
                    name == "models"
                    or name.startswith("models.")
                    or name == "utils"
                    or name.startswith("utils.")
                ) and name not in previous_modules:
                    sys.modules.pop(name, None)
            sys.modules.update(previous_modules)

        afno = AFNO2D(hidden_size=256, num_blocks=8).eval()
        for height, width in ((20, 20), (16, 20)):
            with self.subTest(height=height, width=width), torch.inference_mode():
                result = afno(torch.randn(1, 256, height, width))
            self.assertEqual(tuple(result.shape), (1, 256, height, width))
            self.assertTrue(torch.isfinite(result).all())

    def test_checkpoint_architecture_requires_ghostnet_and_afno_components(self):
        class GhostConv:
            pass

        class GhostBottleneck:
            pass

        class AFNO2D:
            pass

        class CustomModel:
            def modules(self):
                return [self, GhostConv(), GhostBottleneck(), AFNO2D()]

        class GenericModel:
            def modules(self):
                return [self]

        DetectionService._validate_custom_architecture(CustomModel())
        with self.assertRaisesRegex(ValueError, "AFNO2D, GhostBottleneck, GhostConv"):
            DetectionService._validate_custom_architecture(GenericModel())

    def test_detector_initialization_is_lazy_and_reused(self):
        runtime, _ = fake_runtime()
        loader_calls = []
        service = DetectionService(
            Path("."),
            runtime_loader=lambda: loader_calls.append(True) or runtime,
        )
        frame = prepared(np.zeros((8, 10, 3), dtype=np.uint8))
        service.detect(frame)
        service.detect(frame)
        self.assertEqual(len(loader_calls), 1)
        self.assertEqual(len(runtime.model.inputs), 2)

    def test_valid_prepared_rgb_frame_reaches_yolo_with_expected_tensor(self):
        runtime, _ = fake_runtime([[1, 2, 8, 7, 0.8, 0]])
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        pixels = np.zeros((8, 10, 3), dtype=np.uint8)
        pixels[:] = [10, 20, 30]
        result, _ = service.detect(prepared(pixels))
        tensor, options = runtime.model.inputs[0]
        self.assertEqual(tuple(tensor.shape), (1, 3, 8, 10))
        self.assertTrue(torch.allclose(tensor[0, :, 0, 0], torch.tensor([30, 20, 10]) / 255))
        self.assertEqual(options, {"augment": False, "visualize": False})
        self.assertEqual(result.frame_index, 7)

    def test_live_cctv_prepared_frame_uses_same_detector_boundary(self):
        runtime, _ = fake_runtime([[0, 0, 4, 4, 0.8, 0]])
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, _ = service.detect(
            prepared(
                np.zeros((8, 10, 3), dtype=np.uint8),
                input_type=None,
                source_type="cctv",
            )
        )
        self.assertEqual(result.source_type, "cctv")
        self.assertIsNone(result.frame_index)
        self.assertEqual(result.live_reference_id, "live-reference")

    def test_non_prepared_input_is_rejected(self):
        service = DetectionService(Path("."), runtime_loader=lambda: fake_runtime()[0])
        with self.assertRaises(RunError) as error:
            service.detect(np.zeros((8, 10, 3), dtype=np.uint8))
        self.assertEqual(error.exception.error, "invalid_prepared_frame")

    def test_invalid_empty_and_malformed_prepared_frames_are_rejected(self):
        service = DetectionService(Path("."), runtime_loader=lambda: fake_runtime()[0])
        empty = prepared(np.empty((0, 0, 3), dtype=np.uint8))
        malformed = prepared(np.zeros((8, 10, 2), dtype=np.uint8))
        with self.assertRaises(RunError):
            service.detect(empty)
        with self.assertRaises(RunError):
            service.detect(malformed)
        self.assertIsNone(service._runtime)

    def test_nonfinite_and_boolean_frames_are_rejected(self):
        service = DetectionService(Path("."), runtime_loader=lambda: fake_runtime()[0])
        with self.assertRaises(RunError):
            service.detect(prepared(np.full((4, 4, 3), np.nan, dtype=np.float32)))
        with self.assertRaises(RunError):
            service.detect(prepared(np.zeros((4, 4, 3), dtype=np.bool_)))

    def test_thermal_frames_use_separate_runtime_and_grayscale_expansion(self):
        runtime, _ = fake_runtime(
            [[1, 2, 8, 7, 0.8, 0]],
            loader_name="thermal_bird_drone_best.pt",
            names={0: "Bird", 1: "Drone"},
        )
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        thermal = prepared(np.full((8, 10), 64, dtype=np.uint8), input_type="thermal")

        result, annotated = service.detect(thermal)

        tensor, _ = runtime.model.inputs[0]
        self.assertEqual(tuple(tensor.shape), (1, 3, 8, 10))
        self.assertTrue(torch.allclose(tensor[0, :, 0, 0], torch.full((3,), 64 / 255)))
        self.assertEqual(result.detections[0].class_name, "Bird")
        self.assertEqual(result.model_name, "YOLOv5 + GhostNet + AFNO (thermal_bird_drone_best.pt)")
        self.assertEqual(annotated.shape, (8, 10, 3))
        self.assertIsNone(service._runtime)
        self.assertIs(service._thermal_runtime, runtime)

    def test_radar_modality_remains_unsupported(self):
        service = DetectionService(Path("."), runtime_loader=lambda: fake_runtime()[0])
        with self.assertRaises(RunError) as error:
            service.detect(prepared(np.zeros((4, 4, 3), dtype=np.uint8), input_type="radar"))
        self.assertEqual(error.exception.status, 415)
        self.assertIsNone(service._runtime)
        self.assertIsNone(service._thermal_runtime)

    def test_confidence_filtering_and_configured_nms_arguments(self):
        runtime, nms_calls = fake_runtime(
            [
                [0, 0, 2, 2, 0.249, 0],
                [1, 1, 3, 3, 0.25, 1],
                [2, 2, 4, 4, 0.91, 0],
            ]
        )
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, _ = service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        self.assertEqual(len(result.detections), 2)
        self.assertEqual(nms_calls[0][1]["conf_thres"], 0.25)
        self.assertEqual(nms_calls[0][1]["iou_thres"], 0.45)
        self.assertIsNone(nms_calls[0][1]["classes"])
        self.assertFalse(nms_calls[0][1]["agnostic"])

    def test_project_yolov5_nms_suppresses_same_class_overlap(self):
        detector_root = str(Path(__file__).resolve().parents[2] / "base_detector" / "yolov5_ghostnet_AFNO")
        previous_utils_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "utils" or name.startswith("utils.")
        }
        sys.path.insert(0, detector_root)
        try:
            from utils.general import non_max_suppression
        finally:
            sys.path.remove(detector_root)
            for name in tuple(sys.modules):
                if (name == "utils" or name.startswith("utils.")) and name not in previous_utils_modules:
                    sys.modules.pop(name, None)
            sys.modules.update(previous_utils_modules)

        raw_predictions = torch.tensor(
            [[
                [5, 5, 4, 4, 0.9, 0.9, 0.1],
                [5, 5, 4, 4, 0.8, 0.9, 0.1],
                [5, 5, 4, 4, 0.9, 0.1, 0.9],
            ]],
            dtype=torch.float32,
        )

        class PredictionModel:
            def __call__(self, _tensor, **_kwargs):
                return raw_predictions

        runtime, _ = fake_runtime()
        runtime = _YoloRuntime(
            **{
                **runtime.__dict__,
                "model": PredictionModel(),
                "non_max_suppression": non_max_suppression,
            }
        )
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, _ = service.detect(prepared(np.zeros((10, 10, 3), dtype=np.uint8)))
        self.assertEqual(len(result.detections), 2)
        self.assertEqual({detection.class_id for detection in result.detections}, {0, 1})

    def test_class_id_mapping_and_result_contract(self):
        runtime, _ = fake_runtime([[1, 1, 5, 6, 0.9, 1]])
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, _ = service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        contract = result.as_dict()
        self.assertEqual(contract["model_name"], "YOLOv5 + GhostNet + AFNO (best.pt)")
        self.assertEqual(contract["detection_count"], 1)
        self.assertEqual(contract["detections"][0]["class_id"], 1)
        self.assertEqual(contract["detections"][0]["class_name"], "bird")
        self.assertNotIn("track_id", contract["detections"][0])
        self.assertNotIn("reliability", contract["detections"][0])

    def test_bounding_boxes_are_clipped_and_invalid_boxes_discarded(self):
        runtime, _ = fake_runtime(
            [
                [-3, -1, 20, 50, 0.8, 0],
                [5, 5, 3, 6, 0.9, 0],
                [float("nan"), 0, 2, 2, 0.7, 1],
            ]
        )
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, _ = service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        self.assertEqual(len(result.detections), 1)
        detection = result.detections[0]
        self.assertEqual((detection.x1, detection.y1, detection.x2, detection.y2), (0, 0, 10, 8))

    def test_empty_prediction_produces_successful_empty_result(self):
        runtime, _ = fake_runtime([])
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        result, annotated = service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        self.assertEqual(result.detections, ())
        self.assertEqual(result.as_dict()["detection_count"], 0)
        self.assertEqual(annotated.shape, (8, 10, 3))

    def test_detector_exception_returns_safe_frame_error(self):
        runtime, _ = fake_runtime(model_error=RuntimeError("private model detail"))
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        with self.assertRaises(RunError) as error:
            service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        self.assertEqual(error.exception.error, "detector_inference_failed")
        self.assertNotIn("private model detail", error.exception.message)

    def test_initialization_error_is_explicit_and_safe(self):
        service = DetectionService(
            Path("."),
            runtime_loader=lambda: (_ for _ in ()).throw(FileNotFoundError("private path")),
        )
        with self.assertRaises(RunError) as error:
            service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        self.assertEqual(error.exception.error, "detector_unavailable")
        self.assertNotIn("private path", error.exception.message)

    def test_service_shutdown_releases_runtime_model(self):
        class ClosableModel(FakeModel):
            def __init__(self):
                super().__init__()
                self.closed = False

            def close(self):
                self.closed = True

        runtime, _ = fake_runtime()
        model = ClosableModel()
        runtime = _YoloRuntime(**{**runtime.__dict__, "model": model})
        service = DetectionService(Path("."), runtime_loader=lambda: runtime)
        service.detect(prepared(np.zeros((8, 10, 3), dtype=np.uint8)))
        service.close()
        self.assertTrue(model.closed)
        self.assertIsNone(service._runtime)


class Phase4DetectionAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.captures = []
        cls.pipeline_calls = []

        class Capture:
            def __init__(self):
                self.position = 0
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                if self.position:
                    return False, None
                self.position += 1
                return True, np.full((8, 10, 3), 60, dtype=np.uint8)

            def get(self, prop):
                import cv2

                if prop == cv2.CAP_PROP_FRAME_COUNT:
                    return 1
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    return self.position
                return 0

            def release(self):
                self.released = True

        def capture_factory(_path):
            capture = Capture()
            cls.captures.append(capture)
            return capture

        runtime, _ = fake_runtime([[1, 2, 8, 7, 0.8, 0]])
        detector = DetectionService(cls.root, runtime_loader=lambda: runtime)
        cls.server = create_server(
            project_root=Path(__file__).resolve().parents[2],
            port=0,
            run_runner=lambda *args: cls.pipeline_calls.append(args),
            runs_root=cls.root / "runs",
            auth_db_path=cls.root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
            capture_factory=capture_factory,
            detector_service=detector,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        status, _, headers = cls.request(
            "/api/auth/login",
            method="POST",
            payload={"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        if status != 200:
            raise AssertionError("Could not create Phase 4 API test session.")
        cls.cookie = headers["Set-Cookie"].split(";", 1)[0]
        cls.run_record = cls.server.run_manager.create_run(
            "detector-test.mp4",
            b"stored",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            input_metadata={"frame_count": 1},
            prepared=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    @classmethod
    def request(cls, path, method="GET", payload=None, cookie=None):
        headers = {}
        body = None
        if cookie:
            headers["Cookie"] = cookie
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = Request(cls.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_detection_api_requires_authentication(self):
        status, raw, _ = self.request(
            "/api/phase4/detect",
            method="POST",
            payload={"source_type": "upload", "input_type": "rgb", "run_id": self.run_record.run_id},
        )
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "authentication_required")
        self.assertEqual(self.captures, [])

    def test_phase3_prepared_frame_flows_to_detector_without_pipeline(self):
        status, raw, _ = self.request(
            "/api/phase4/detect",
            method="POST",
            payload={"source_type": "upload", "input_type": "rgb", "run_id": self.run_record.run_id},
            cookie=self.cookie,
        )
        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["source_type"], "upload")
        self.assertEqual(body["input_type"], "rgb")
        self.assertEqual(body["run_id"], self.run_record.run_id)
        self.assertEqual(body["frame_index"], 0)
        self.assertEqual(body["validation_status"], "valid")
        self.assertEqual(body["preprocessing_status"], "validated_contiguous_copy")
        self.assertEqual(body["detection_count"], 1)
        self.assertEqual(body["detections"][0]["class_name"], "drone")
        self.assertNotIn("track_id", body["detections"][0])
        self.assertTrue(base64.b64decode(body["frame_base64"], validate=True).startswith(b"\xff\xd8"))
        self.assertEqual(self.pipeline_calls, [])

    def test_thermal_api_uses_thermal_detector_without_changing_rgb_detector(self):
        runtime, _ = fake_runtime(
            [[1, 2, 8, 7, 0.8, 0]],
            loader_name="thermal_bird_drone_best.pt",
            names={0: "Bird", 1: "Drone"},
        )
        self.server.detector_service._thermal_runtime = runtime
        rgb_runtime = self.server.detector_service._runtime
        thermal_frame = prepared(
            np.full((8, 10), 64, dtype=np.uint8),
            input_type="thermal",
        )
        with patch.object(
            self.server.frame_manager,
            "acquire_next",
            return_value=thermal_frame,
        ) as acquire_next:
            status, raw, _ = self.request(
                "/api/phase4/detect",
                method="POST",
                payload={
                    "source_type": "upload",
                    "input_type": "thermal",
                    "run_id": self.run_record.run_id,
                },
                cookie=self.cookie,
            )

        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(body["input_type"], "thermal")
        self.assertEqual(body["model_name"], "YOLOv5 + GhostNet + AFNO (thermal_bird_drone_best.pt)")
        self.assertEqual(body["detections"][0]["class_id"], 0)
        self.assertEqual(body["detections"][0]["class_name"], "Bird")
        self.assertEqual(body["preprocessing_status"], "validated_contiguous_copy")
        self.assertTrue(base64.b64decode(body["frame_base64"], validate=True).startswith(b"\xff\xd8"))
        self.assertEqual(acquire_next.call_args.kwargs["input_type"], "thermal")
        self.assertIs(self.server.detector_service._runtime, rgb_runtime)
        self.assertIsNot(self.server.detector_service._runtime, runtime)


if __name__ == "__main__":
    unittest.main()
