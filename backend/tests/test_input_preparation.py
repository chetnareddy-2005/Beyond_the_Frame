from __future__ import annotations

import json
import hashlib
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import numpy as np

from backend.app import create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.runs import RunError


class FakeFrame:
    size = 18
    shape = (2, 3, 3)

    def copy(self):
        return self


class FakeCapture:
    def __init__(self, opened=True, readable=True, frame=None):
        self.opened = opened
        self.readable = readable
        self.released = False
        self.frame = frame

    def isOpened(self):
        return self.opened

    def read(self):
        frame = self.frame if self.frame is not None else FakeFrame()
        return (self.readable, frame if self.readable else None)

    def get(self, _property):
        return 10

    def release(self):
        self.released = True


class InputPreparationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.captures = []
        cls.runner_calls = []

        def capture_factory(_source):
            capture = cls.next_capture()
            cls.captures.append(capture)
            return capture

        cls.capture_state = {"opened": True, "readable": True}

        def runner(*args):
            cls.runner_calls.append(args)
            raise AssertionError("Phase 2 must not invoke the analysis pipeline")

        cls.server = create_server(
            port=0,
            run_runner=runner,
            max_upload_bytes=16,
            runs_root=root / "runs",
            auth_db_path=root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
            capture_factory=capture_factory,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        login = Request(
            cls.base_url + "/api/auth/login",
            data=json.dumps(
                {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(login, timeout=5) as response:
            cls.cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def next_capture(cls):
        return FakeCapture(
            opened=cls.capture_state["opened"],
            readable=cls.capture_state["readable"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp_dir.cleanup()

    def request(self, path, method="GET", payload=None, body=None, content_type=None, authenticated=True):
        headers = {}
        if authenticated:
            headers["Cookie"] = self.cookie
        if payload is not None:
            body = json.dumps(payload).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    def upload_body(self, filename="sample.mp4", content=b"valid"):
        boundary = "phase2-input-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="source"\r\n\r\n'
            "upload\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="input_type"\r\n\r\n'
            "rgb\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return body, f"multipart/form-data; boundary={boundary}"

    def set_capture_factory(self, opened=True, readable=True):
        def capture_factory(_source):
            capture = FakeCapture(opened, readable)
            self.captures.append(capture)
            return capture

        self.server.input_preparer.capture_factory = capture_factory

    @staticmethod
    def http_request(base_url, path, method="GET", payload=None, cookie=None):
        headers = {}
        body = None
        if cookie:
            headers["Cookie"] = cookie
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = Request(base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            return error.code, error.read(), error.headers

    @staticmethod
    @contextmanager
    def isolated_live_server():
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        captures = []
        runner_calls = []

        def capture_factory(_source):
            capture = FakeCapture(frame=np.zeros((2, 3, 3), dtype=np.uint8))
            captures.append(capture)
            return capture

        def forbidden_runner(*_args):
            runner_calls.append(_args)
            raise AssertionError("CCTV Phase 2 handoff must not run the analysis pipeline")

        server = create_server(
            port=0,
            run_runner=forbidden_runner,
            runs_root=root / "runs",
            auth_db_path=root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
            capture_factory=capture_factory,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        server.test_runner_calls = runner_calls
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        status, body, headers = InputPreparationAPITests.http_request(
            base_url,
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        if status != 200:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            temp_dir.cleanup()
            raise AssertionError(f"Could not create isolated CCTV test session: {body!r}")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        try:
            yield server, base_url, cookie, captures
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            temp_dir.cleanup()

    def test_auth_required_and_rgb_prepare_does_not_start_pipeline(self):
        status, body, _ = self.request(
            "/api/input/prepare", "POST", authenticated=False, payload={"source": "cctv"}
        )
        self.assertEqual(status, 401)

        body, content_type = self.upload_body()
        status, raw, _ = self.request(
            "/api/input/prepare",
            "POST",
            body=body,
            content_type=content_type,
        )
        result = json.loads(raw)
        self.assertEqual(status, 201)
        self.assertEqual((result["source"], result["input_type"], result["status"]), ("upload", "rgb", "validated"))
        self.assertEqual(result["metadata"]["fps"], 10)
        self.assertEqual(result["metadata"]["width"], 10)
        self.assertEqual(result["metadata"]["frame_count"], 10)
        self.assertEqual(result["input_filename"], "input_video.mp4")
        self.assertEqual(result["original_filename"], "sample.mp4")
        self.assertTrue(result["created_at"])
        self.assertRegex(result["source_video_id"], r"^V-[1-9][0-9]*$")
        self.assertEqual(self.runner_calls, [])

        record = self.server.run_manager.get(result["run_id"])
        manifest = json.loads((record.root / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_type"], "upload")
        self.assertEqual(manifest["input_type"], "rgb")
        self.assertEqual(manifest["input_status"], "validated")
        self.assertEqual(manifest["input_filename"], "input_video.mp4")
        self.assertEqual(manifest["original_filename"], "sample.mp4")
        self.assertEqual(manifest["input_metadata"]["decoded_frame_width"], 3)
        self.assertEqual(record.status, "prepared")

    def test_upload_validation_and_radar_fail_closed(self):
        cases = (
            ("sample.exe", b"valid", "unsupported_video_type", 415),
            ("sample.mp4", b"", "empty_upload", 400),
            ("sample.mp4", b"x" * 17, "upload_too_large", 413),
        )
        for filename, content, code, expected_status in cases:
            with self.subTest(code=code):
                body, content_type = self.upload_body(filename, content)
                status, raw, _ = self.request(
                    "/api/input/prepare", "POST", body=body, content_type=content_type
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(raw)["error"], code)

        body, content_type = self.upload_body()
        body = body.replace(
            b'name="input_type"\r\n\r\nrgb',
            b'name="input_type"\r\n\r\nradar',
        )
        status, raw, _ = self.request(
            "/api/input/prepare", "POST", body=body, content_type=content_type
        )
        self.assertEqual(status, 415)
        self.assertEqual(json.loads(raw)["error"], "unsupported_radar_format")

    def test_corrupt_rgb_video_is_rejected_before_preparation(self):
        self.set_capture_factory(readable=False)
        body, content_type = self.upload_body(content=b"not video")
        status, raw, _ = self.request(
            "/api/input/prepare", "POST", body=body, content_type=content_type
        )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(raw)["error"], "invalid_video")
        self.assertEqual(self.runner_calls, [])

    def test_thermal_video_uses_existing_frame_adapter(self):
        self.server.input_preparer.capture_factory = lambda _source: FakeCapture()
        body, content_type = self.upload_body()
        body = body.replace(
            b'name="input_type"\r\n\r\nrgb',
            b'name="input_type"\r\n\r\nthermal',
        )
        with patch(
            "multimodal.thermal_adapter.ThermalAdapter.read_frame",
            return_value=np.array([[0, 255], [40, 80]], dtype=np.uint8),
        ):
            status, raw, _ = self.request(
                "/api/input/prepare", "POST", body=body, content_type=content_type
            )
        result = json.loads(raw)
        self.assertEqual(status, 201)
        self.assertEqual(result["input_type"], "thermal")
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["metadata"]["thermal_first_frame_intensity"]["dtype"], "uint8")
        self.assertIn("not temperature-calibrated", result["metadata"]["thermal_preprocessing"])
        record = self.server.run_manager.get(result["run_id"])
        self.assertEqual(record.input_filename, "input_video.mp4")
        self.assertEqual(record.original_filename, "sample.mp4")
        self.assertEqual(result["source_video_id"], record.source_video_id)
        record.input_status = "unvalidated"
        status, raw, _ = self.request(f"/api/runs/{record.run_id}/start", "POST", body=b"")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw)["error"], "input_not_supported")
        self.assertEqual(record.status, "prepared")
        self.assertEqual(self.runner_calls, [])
        record.input_status = "validated"
        status, raw, _ = self.request(f"/api/runs/{record.run_id}/start", "POST", body=b"")
        self.assertEqual(status, 202)
        self.assertEqual(record.status, "processing")

        with patch("multimodal.thermal_adapter.ThermalAdapter.read_frame", return_value=None):
            status, raw, _ = self.request(
                "/api/input/prepare", "POST", body=body, content_type=content_type
            )
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(raw)["error"], "invalid_video")
        self.assertEqual(self.runner_calls, [])

    def test_cctv_url_validation_probe_start_stop_and_secret_redaction(self):
        self.set_capture_factory()
        status, raw, _ = self.request(
            "/api/input/prepare",
            "POST",
            payload={"source": "cctv", "rtsp_url": "https://invalid"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"], "invalid_rtsp_url")

        private_url = "rtsp://operator:sensitive-value@camera.test:554/live?token=also-secret"
        status, raw, _ = self.request(
            "/api/input/prepare",
            "POST",
            payload={"source": "cctv", "action": "test", "rtsp_url": private_url},
        )
        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["input_reference"], "rtsp://camera.test:554")
        self.assertNotIn("sensitive-value", raw.decode())
        self.assertNotIn("also-secret", raw.decode())
        self.assertTrue(self.captures[-1].released)

        status, raw, _ = self.request(
            "/api/input/prepare",
            "POST",
            payload={"source": "cctv", "action": "start", "rtsp_url": private_url},
        )
        result = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "connected")
        self.assertTrue(result["input_reference"].startswith("cctv:"))
        self.assertEqual(result["source_type"], "cctv")
        self.assertIsNone(result["input_type"])
        self.assertEqual(result["connection_status"], "connected")
        self.assertTrue(result["frame_available"])
        self.assertEqual(result["readiness"], "READY_FOR_PHASE_3")
        self.assertNotIn("fps", result["metadata"])
        state_path = next((self.server.run_manager.runs_root / "input_states").glob("*.json"))
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["source_type"], "cctv")
        self.assertEqual(persisted["source_url"], "rtsp://camera.test:554")
        self.assertNotIn("sensitive-value", state_path.read_text(encoding="utf-8"))
        self.assertNotIn("also-secret", state_path.read_text(encoding="utf-8"))

        capture = self.captures[-1]
        status, raw, _ = self.request("/api/input/cctv/stop", "POST", body=b"")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["status"], "stopped")
        self.assertTrue(json.loads(raw)["live_reference_invalidated"])
        self.assertTrue(capture.released)
        persisted_after_stop = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(persisted_after_stop["live_reference_id"])
        self.assertFalse(persisted_after_stop["frame_available"])
        self.assertEqual(persisted_after_stop["readiness"], "STOPPED")
        stale_status, stale_body, _ = self.request(
            f"/api/input/cctv/{result['live_reference_id']}/frame"
        )
        self.assertEqual(stale_status, 404)
        self.assertEqual(json.loads(stale_body)["error"], "live_input_not_found")
        self.assertEqual(self.runner_calls, [])

    def test_cctv_failures_release_capture(self):
        for opened, readable, error in (
            (False, False, "stream_unavailable"),
            (True, False, "frame_read_failure"),
        ):
            with self.subTest(error=error):
                self.set_capture_factory(opened, readable)
                status, raw, _ = self.request(
                    "/api/input/prepare",
                    "POST",
                    payload={"source": "cctv", "action": "test", "rtsp_url": "rtsp://camera.test/live"},
                )
                self.assertEqual(status, 502)
                self.assertEqual(json.loads(raw)["error"], error)
                self.assertTrue(self.captures[-1].released)

    def test_cctv_handoff_exposes_current_frame_and_unavailable_state(self):
        with self.isolated_live_server() as (server, base_url, cookie, captures):
            with self.assertLogs("backend.app", level="INFO") as captured_logs:
                status, raw, _ = self.http_request(
                    base_url,
                    "/api/input/prepare",
                    "POST",
                    {
                        "source": "cctv",
                        "action": "start",
                        "rtsp_url": "rtsp://user:credential-secret@camera.test:554/live?token=query-secret",
                    },
                    cookie,
                )
            self.assertEqual(status, 200)
            start_result = json.loads(raw)
            reference_id = start_result["live_reference_id"]
            self.assertEqual(start_result["source_type"], "cctv")
            self.assertIsNone(start_result["input_type"])
            self.assertEqual(start_result["connection_status"], "connected")
            self.assertTrue(start_result["frame_available"])
            self.assertEqual(start_result["readiness"], "READY_FOR_PHASE_3")
            self.assertEqual(start_result["metadata"]["width"], 3)
            self.assertEqual(start_result["metadata"]["height"], 2)
            self.assertNotIn("fps", start_result["metadata"])
            self.assertNotIn("credential-secret", raw.decode())
            self.assertNotIn("query-secret", raw.decode())
            self.assertNotIn("credential-secret", "\n".join(captured_logs.output))
            self.assertNotIn("query-secret", "\n".join(captured_logs.output))

            status, raw, _ = self.http_request(
                base_url,
                f"/api/input/cctv/{reference_id}/handoff",
                cookie=cookie,
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["frame_available"])

            status, frame_bytes, headers = self.http_request(
                base_url,
                f"/api/input/cctv/{reference_id}/frame",
                cookie=cookie,
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get_content_type(), "image/jpeg")
            self.assertEqual(headers["X-Input-Source"], "cctv")
            self.assertEqual(headers["X-Live-Reference-ID"], reference_id)
            self.assertEqual(headers["X-Frame-Available"], "true")
            self.assertTrue(frame_bytes.startswith(b"\xff\xd8"))
            self.assertEqual(server.input_preparer.get_cctv_handoff(1, reference_id)["source_type"], "cctv")

            captures[-1].readable = False
            status, raw, _ = self.http_request(
                base_url,
                f"/api/input/cctv/{reference_id}/frame",
                cookie=cookie,
            )
            self.assertEqual(status, 502)
            status, raw, _ = self.http_request(
                base_url,
                f"/api/input/cctv/{reference_id}/handoff",
                cookie=cookie,
            )
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(raw)["frame_available"])
            self.assertEqual(json.loads(raw)["readiness"], "FRAME_UNAVAILABLE")
            self.assertEqual(server.test_runner_calls, [])

    def test_cctv_logout_and_expired_session_release_capture(self):
        with self.isolated_live_server() as (_server, base_url, cookie, captures):
            status, raw, _ = self.http_request(
                base_url,
                "/api/input/prepare",
                "POST",
                {"source": "cctv", "action": "start", "rtsp_url": "rtsp://camera.test/live"},
                cookie,
            )
            self.assertEqual(status, 200)
            status, _, _ = self.http_request(
                base_url, "/api/auth/logout", "POST", cookie=cookie
            )
            self.assertEqual(status, 200)
            self.assertTrue(captures[-1].released)

        with self.isolated_live_server() as (server, base_url, cookie, captures):
            status, raw, _ = self.http_request(
                base_url,
                "/api/input/prepare",
                "POST",
                {"source": "cctv", "action": "start", "rtsp_url": "rtsp://camera.test/live"},
                cookie,
            )
            self.assertEqual(status, 200)
            reference_id = json.loads(raw)["live_reference_id"]
            token = cookie.split("=", 1)[1]
            session_key = hashlib.sha256(token.encode("ascii")).hexdigest()
            user_id, username, _ = server.auth_manager._sessions[session_key]
            server.auth_manager._sessions[session_key] = (user_id, username, time.time() - 1)
            status, raw, _ = self.http_request(
                base_url,
                f"/api/input/cctv/{reference_id}/frame",
                cookie=cookie,
            )
            self.assertEqual(status, 401)
            self.assertTrue(captures[-1].released)
            self.assertEqual(json.loads(raw)["error"], "authentication_required")

    def test_cctv_backend_shutdown_releases_and_invalidates_capture(self):
        with self.isolated_live_server() as (server, base_url, cookie, captures):
            status, raw, _ = self.http_request(
                base_url,
                "/api/input/prepare",
                "POST",
                {"source": "cctv", "action": "start", "rtsp_url": "rtsp://camera.test/live"},
                cookie,
            )
            self.assertEqual(status, 200)
            reference_id = json.loads(raw)["live_reference_id"]
            state_path = next((server.run_manager.runs_root / "input_states").glob("*.json"))
            server.shutdown()
            server.server_close()
            self.assertTrue(captures[-1].released)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["input_status"], "stopped")
            self.assertIsNone(persisted["live_reference_id"])
            with self.assertRaises(RunError) as raised:
                server.input_preparer.get_current_cctv_frame(1, reference_id)
            self.assertEqual(raised.exception.error, "live_input_not_found")


if __name__ == "__main__":
    unittest.main()
