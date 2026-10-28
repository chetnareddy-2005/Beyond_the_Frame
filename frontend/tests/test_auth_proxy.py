from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import torch

from backend.app import create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.detection import _YoloRuntime
from frontend.server import DashboardServer


class AuthenticationProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.backend = create_server(
            port=0,
            run_runner=lambda *_args: None,
            runs_root=root / "runs",
            auth_db_path=root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
        )
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        backend_host, backend_port = cls.backend.server_address
        cls.dashboard = DashboardServer(
            ("127.0.0.1", 0),
            f"http://{backend_host}:{backend_port}",
        )
        cls.dashboard_thread = threading.Thread(target=cls.dashboard.serve_forever, daemon=True)
        cls.dashboard_thread.start()
        dashboard_host, dashboard_port = cls.dashboard.server_address
        cls.base_url = f"http://{dashboard_host}:{dashboard_port}"

    @classmethod
    def tearDownClass(cls):
        cls.dashboard.shutdown()
        cls.dashboard.server_close()
        cls.dashboard_thread.join(timeout=5)
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.backend_thread.join(timeout=5)
        cls.temp_dir.cleanup()

    def request(self, path, method="GET", payload=None, cookie=None):
        headers = {}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if cookie:
            headers["Cookie"] = cookie
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def test_dashboard_entry_and_session_cookie_proxy(self):
        status, page, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="splash-view"', page)
        self.assertIn(b'id="login-view"', page)
        self.assertEqual(self.request("/dashboard")[0], 200)
        self.assertEqual(self.request("/api/overview")[0], 401)

        status, body, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["authenticated"], True)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, body, _ = self.request("/api/auth/session", cookie=cookie)
        self.assertEqual((status, json.loads(body)["authenticated"]), (200, True))
        self.assertEqual(self.request("/api/overview", cookie=cookie)[0], 200)

        self.assertEqual(self.request("/api/auth/logout", "POST", cookie=cookie)[0], 200)
        status, body, _ = self.request("/api/auth/session", cookie=cookie)
        self.assertEqual((status, json.loads(body)["authenticated"]), (200, False))
        self.assertEqual(self.request("/api/overview", cookie=cookie)[0], 401)

    def test_registry_phone_otp_routes_are_removed(self):
        status, body, _ = self.request(
            "/api/sector-registry/phone-otp/send",
            "POST",
            {"sector_id": "Sector 1 (S1)", "phone": "+15551234567"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_phase5_reset_endpoint_is_proxied(self):
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, body, _ = self.request("/api/phase5/reset", "POST", {}, cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["tracking_status"], "reset")

    def test_run_cancellation_post_is_proxied_with_session(self):
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        record = self.backend.run_manager.create_run("cancel.mp4", b"video")
        record.status = "processing"
        record.stage = "detection"
        self.backend.run_manager._persist(record)

        request = Request(
            f"{self.base_url}/api/runs/{record.run_id}/cancel",
            data=b"",
            headers={"Cookie": cookie},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                proxy_status = response.status
                result = json.loads(response.read())
        except HTTPError as error:
            proxy_status = error.code
            result = json.loads(error.read())

        self.assertEqual(proxy_status, 200)
        self.assertEqual(result["run_id"], record.run_id)
        self.assertEqual(result["status"], "cancelled")

    def test_fresh_reanalysis_post_is_proxied_with_session(self):
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        source = self.backend.run_manager.create_run(
            "fresh.mp4",
            b"fresh-video",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            prepared=True,
        )

        request = Request(
            f"{self.base_url}/api/runs/{source.run_id}/reanalyze",
            data=b"",
            headers={"Cookie": cookie},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                proxy_status = response.status
                result = json.loads(response.read())
        except HTTPError as error:
            proxy_status = error.code
            result = json.loads(error.read())

        self.assertEqual(proxy_status, 201)
        self.assertNotEqual(result["run_id"], source.run_id)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(source.status, "prepared")

    def test_stored_video_range_and_resume_post_are_proxied(self):
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        record = self.backend.run_manager.create_run(
            "stored.mp4",
            b"0123456789",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            input_metadata={"fps": 10},
            prepared=True,
        )

        ranged_request = Request(
            f"{self.base_url}/api/runs/{record.run_id}/video",
            headers={"Cookie": cookie, "Range": "bytes=2-5"},
        )
        with urlopen(ranged_request, timeout=5) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b"2345")
            self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")

        record.status = "failed"
        self.backend.run_manager._persist(record)
        resume_request = Request(
            f"{self.base_url}/api/runs/{record.run_id}/resume",
            data=b"",
            headers={"Cookie": cookie},
            method="POST",
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(resume_request, timeout=5)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(
            json.loads(error.exception.read())["error"],
            "checkpoint_unavailable",
        )

    def test_phase6_temporal_endpoint_is_proxied(self):
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        record = self.backend.run_manager.create_run(
            "temporal.mp4",
            b"prepared",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            input_metadata={"fps": 10},
            prepared=True,
        )
        result = {
            "source_type": "upload",
            "input_type": "rgb",
            "run_id": record.run_id,
            "frame_index": 0,
            "sequence_index": 0,
            "tracks": [{
                "track_id": 1,
                "class_id": 0,
                "class_name": "drone",
                "confidence": 0.9,
                "x1": 1,
                "y1": 2,
                "x2": 10,
                "y2": 12,
                "timestamp_sec": None,
            }],
        }

        status, body, _ = self.request(
            "/api/phase6/temporal", "POST", result, cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["frames_processed"], 1)

    def test_cctv_handoff_frame_headers_are_proxied(self):
        class Capture:
            def __init__(self):
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                return True, np.zeros((4, 5, 3), dtype=np.uint8)

            def release(self):
                self.released = True

        captures = []

        def capture_factory(_source):
            capture = Capture()
            captures.append(capture)
            return capture

        self.backend.input_preparer.capture_factory = capture_factory
        status, body, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        status, body, _ = self.request(
            "/api/input/prepare",
            "POST",
            {
                "source": "cctv",
                "action": "start",
                "rtsp_url": "rtsp://camera.test/live",
            },
            cookie,
        )
        self.assertEqual(status, 200)
        reference_id = json.loads(body)["live_reference_id"]
        status, frame, headers = self.request(
            f"/api/input/cctv/{reference_id}/frame",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "image/jpeg")
        self.assertEqual(headers["X-Input-Source"], "cctv")
        self.assertEqual(headers["X-Live-Reference-ID"], reference_id)
        self.assertEqual(headers["X-Frame-Available"], "true")
        self.assertTrue(frame.startswith(b"\xff\xd8"))
        self.assertFalse(captures[-1].released)

        self.assertEqual(self.request("/api/auth/logout", "POST", cookie=cookie)[0], 200)
        self.assertTrue(captures[-1].released)

    def test_phase3_frame_acquisition_is_proxied(self):
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
                return True, np.zeros((3, 4, 3), dtype=np.uint8)

            def get(self, prop):
                import cv2

                if prop == cv2.CAP_PROP_FRAME_COUNT:
                    return 1
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    return self.position
                return 0

            def release(self):
                self.released = True

        captures = []

        def capture_factory(_path):
            capture = Capture()
            captures.append(capture)
            return capture

        self.backend.frame_manager.capture_factory = capture_factory
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        record = self.backend.run_manager.create_run(
            "phase3.mp4",
            b"video",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            input_metadata={"frame_count": 1},
            prepared=True,
        )
        status, body, _ = self.request(
            "/api/phase3/frame",
            "POST",
            {
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": record.run_id,
            },
            cookie,
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["source_type"], "upload")
        self.assertEqual(result["frame_index"], 0)
        self.assertEqual(result["validation_status"], "valid")
        self.assertIn("frame_base64", result)
        self.assertFalse(captures[-1].released)
        status, _, _ = self.request(
            "/api/phase3/stop",
            "POST",
            {"run_id": record.run_id},
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(captures[-1].released)
        class Model:
            def __call__(self, _tensor, **_kwargs):
                return "raw-predictions"

        self.backend.detector_service._runtime_loader = lambda: _YoloRuntime(
            torch=torch,
            model=Model(),
            letterbox=lambda frame, _size, **_kwargs: (frame, None, None),
            non_max_suppression=lambda _raw, **_kwargs: [
                torch.tensor([[1, 1, 3, 3, 0.9, 0]], dtype=torch.float32)
            ],
            scale_boxes=lambda _input, boxes, _shape: boxes,
            device=torch.device("cpu"),
            image_size=(640, 640),
            stride=32,
            names={0: "drone"},
            checkpoint_name="test.pt",
            confidence_threshold=0.25,
            iou_threshold=0.45,
        )
        self.backend.detector_service._uses_default_runtime_loader = False
        status, body, _ = self.request(
            "/api/phase4/detect",
            "POST",
            {
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": record.run_id,
            },
            cookie,
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["detection_count"], 1)
        self.assertEqual(result["detections"][0]["class_name"], "drone")
        self.assertIn("frame_base64", result)


if __name__ == "__main__":
    unittest.main()
