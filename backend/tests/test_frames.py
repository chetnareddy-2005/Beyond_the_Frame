from __future__ import annotations

import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from backend.app import create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.frames import (
    Phase3FrameManager,
    PreparedFrame,
    RGBVideoFrameSource,
    ThermalVideoFrameSource,
)
from backend.input_preparation import InputPreparer
from backend.runs import RunError, RunManager


class FakeCapture:
    def __init__(self, frames, frame_count=None, opened=True):
        self.frames = list(frames)
        self.frame_count = len(self.frames) if frame_count is None else frame_count
        self.position = 0
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.position >= len(self.frames):
            return False, None
        frame = self.frames[self.position]
        self.position += 1
        return True, frame

    def get(self, prop):
        import cv2

        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.frame_count
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return self.position
        return 0

    def release(self):
        self.released = True


class Phase3FrameTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runner_calls = []
        self.runs = RunManager(
            self.root,
            1024,
            lambda *args: self.runner_calls.append(args),
            runs_root=self.root / "runs",
        )
        self.input_preparer = InputPreparer(self.runs)
        self.manager = Phase3FrameManager(self.runs, self.input_preparer)

    def tearDown(self):
        self.manager.close_all()
        self.input_preparer.close_all()
        self.temp_dir.cleanup()

    def prepared_run(self, input_type="rgb", frame_count=2):
        return self.runs.create_run(
            "source.mp4",
            b"stored-video",
            source_type="upload",
            input_type=input_type,
            input_status="validated",
            input_metadata={"frame_count": frame_count, "fps": 25.0},
            prepared=True,
        )

    def test_prepared_frame_contract_has_required_and_optional_metadata(self):
        capture = FakeCapture([np.zeros((4, 6, 3), dtype=np.uint8)])
        source = RGBVideoFrameSource(
            self.root / "unused.mp4",
            "RUN_TEST",
            capture_factory=lambda _path: capture,
        )
        prepared = source.read_next()
        self.assertIsInstance(prepared, PreparedFrame)
        self.assertEqual(
            prepared.metadata(),
            {
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": "RUN_TEST",
                "live_reference_id": None,
                "frame_index": 0,
                "timestamp_sec": None,
                "width": 6,
                "height": 4,
                "channels": 3,
                "dtype": "uint8",
                "validation_status": "valid",
                "preprocessing_status": "validated_contiguous_copy",
            },
        )
        source.close()
        self.assertTrue(capture.released)

    def test_rgb_acquires_sequential_frames_and_releases_at_end(self):
        record = self.prepared_run()
        frames = [
            np.full((3, 5, 3), index, dtype=np.uint8)
            for index in range(2)
        ]
        capture = FakeCapture(frames)
        self.manager.capture_factory = lambda _path: capture
        first = self.manager.acquire_next(
            7,
            "session-token",
            source_type="upload",
            input_type="rgb",
            run_id=record.run_id,
        )
        second = self.manager.acquire_next(
            7,
            "session-token",
            source_type="upload",
            input_type="rgb",
            run_id=record.run_id,
        )
        end = self.manager.acquire_next(
            7,
            "session-token",
            source_type="upload",
            input_type="rgb",
            run_id=record.run_id,
        )
        self.assertEqual([first.frame_index, second.frame_index], [0, 1])
        self.assertEqual(first.source_type, "upload")
        self.assertEqual(first.input_type, "rgb")
        self.assertEqual(first.timestamp_sec, None)
        np.testing.assert_array_equal(first.frame, frames[0])
        np.testing.assert_array_equal(second.frame, frames[1])
        self.assertIsNone(end)
        self.assertTrue(capture.released)
        self.assertEqual(self.runner_calls, [])

    def test_rgb_rejects_failed_reads_and_releases_capture(self):
        record = self.prepared_run(frame_count=3)
        capture = FakeCapture([], frame_count=3)
        self.manager.capture_factory = lambda _path: capture
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="upload",
                input_type="rgb",
                run_id=record.run_id,
            )
        self.assertEqual(raised.exception.error, "frame_read_failed")
        self.assertTrue(capture.released)

    def test_rgb_rejects_invalid_arrays_and_releases_capture(self):
        record = self.prepared_run()
        capture = FakeCapture([np.zeros((2, 3, 2), dtype=np.uint8)])
        self.manager.capture_factory = lambda _path: capture
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="upload",
                input_type="rgb",
                run_id=record.run_id,
            )
        self.assertEqual(raised.exception.error, "invalid_frame")
        self.assertTrue(capture.released)

    def test_general_preprocessing_copies_to_contiguous_memory(self):
        record = self.prepared_run()
        frame = np.zeros((4, 6, 3), dtype=np.uint8)[:, ::2]
        self.assertFalse(frame.flags.c_contiguous)
        capture = FakeCapture([frame])
        self.manager.capture_factory = lambda _path: capture
        prepared = self.manager.acquire_next(
            7,
            "session-token",
            source_type="upload",
            input_type="rgb",
            run_id=record.run_id,
        )
        self.assertTrue(prepared.frame.flags.c_contiguous)
        self.assertIsNot(prepared.frame, frame)

    def test_thermal_frames_are_normalized_and_indexed_without_temperature_claim(self):
        record = self.prepared_run("thermal", frame_count=2)
        raw_frames = [
            np.array([[0, 10], [20, 30]], dtype=np.uint16),
            np.array([[5, 5], [5, 5]], dtype=np.uint16),
        ]

        def read_frame(_adapter, _path, frame_index):
            if frame_index >= len(raw_frames):
                return None
            return raw_frames[frame_index]

        with patch(
            "multimodal.thermal_adapter.ThermalAdapter.read_frame",
            autospec=True,
            side_effect=read_frame,
        ):
            source = ThermalVideoFrameSource(
                record.root / "input" / record.input_filename,
                record.run_id,
                frame_count=2,
            )
            first = source.read_next()
            second = source.read_next()
            end = source.read_next()

        self.assertEqual(first.frame_index, 0)
        self.assertEqual(second.frame_index, 1)
        self.assertEqual(first.frame.dtype, np.uint8)
        self.assertEqual(first.frame.ndim, 2)
        self.assertEqual(first.frame.shape, (2, 2))
        self.assertIn("not_temperature_calibrated", first.preprocessing_status)
        self.assertIsNone(end)

    def test_thermal_invalid_input_fails_closed(self):
        record = self.prepared_run("thermal")
        with patch(
            "multimodal.thermal_adapter.ThermalAdapter.read_frame",
            return_value=None,
        ):
            source = ThermalVideoFrameSource(
                record.root / "input" / record.input_filename,
                record.run_id,
                frame_count=2,
            )
            with self.assertRaises(RunError) as raised:
                source.read_next()
        self.assertEqual(raised.exception.error, "frame_read_failed")
        self.assertTrue(source.closed)

    def test_cctv_produces_successive_frames_and_rejects_stopped_reference(self):
        frames = [
            np.full((3, 4, 3), index, dtype=np.uint8)
            for index in range(3)
        ]
        capture = FakeCapture(frames)
        self.input_preparer.capture_factory = lambda _source: capture
        started = self.input_preparer.start_cctv(7, "rtsp://camera.test/live", "session-token")
        reference = started["live_reference_id"]
        first = self.manager.acquire_next(
            7,
            "session-token",
            source_type="cctv",
            live_reference_id=reference,
        )
        second = self.manager.acquire_next(
            7,
            "session-token",
            source_type="cctv",
            live_reference_id=reference,
        )
        self.assertEqual(first.source_type, "cctv")
        self.assertEqual(first.live_reference_id, reference)
        self.assertIsNone(first.frame_index)
        self.assertEqual(first.width, 4)
        self.assertEqual(first.height, 3)
        np.testing.assert_array_equal(first.frame, frames[1])
        np.testing.assert_array_equal(second.frame, frames[2])
        self.input_preparer.stop_cctv(7)
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="cctv",
                live_reference_id=reference,
            )
        self.assertEqual(raised.exception.error, "live_input_not_found")
        self.assertTrue(capture.released)

    def test_cctv_invalid_reference_and_invalid_frame_are_rejected(self):
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="cctv",
                live_reference_id="cctv:invalid",
            )
        self.assertEqual(raised.exception.error, "live_input_not_found")

        capture = FakeCapture(
            [
                np.zeros((2, 3, 3), dtype=np.uint8),
                np.zeros((2, 3, 2), dtype=np.uint8),
            ]
        )
        self.input_preparer.capture_factory = lambda _source: capture
        reference = self.input_preparer.start_cctv(
            7, "rtsp://camera.test/live", "session-token"
        )["live_reference_id"]
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="cctv",
                live_reference_id=reference,
            )
        self.assertEqual(raised.exception.error, "invalid_frame")
        handoff = self.input_preparer.get_cctv_handoff(7, reference)
        self.assertFalse(handoff["frame_available"])
        self.input_preparer.stop_cctv(7)

    def test_cctv_read_failure_is_not_prepared(self):
        capture = FakeCapture([np.zeros((2, 3, 3), dtype=np.uint8)])
        self.input_preparer.capture_factory = lambda _source: capture
        reference = self.input_preparer.start_cctv(
            7, "rtsp://camera.test/live", "session-token"
        )["live_reference_id"]
        with self.assertRaises(RunError) as raised:
            self.manager.acquire_next(
                7,
                "session-token",
                source_type="cctv",
                live_reference_id=reference,
            )
        self.assertEqual(raised.exception.error, "frame_read_failure")
        handoff = self.input_preparer.get_cctv_handoff(7, reference)
        self.assertFalse(handoff["frame_available"])
        self.input_preparer.stop_cctv(7)
        self.assertTrue(capture.released)

    def test_reader_cleanup_is_bound_to_session_and_close_all(self):
        record = self.prepared_run()
        capture = FakeCapture([np.zeros((2, 2, 3), dtype=np.uint8)] * 4)
        self.manager.capture_factory = lambda _path: capture
        self.manager.acquire_next(
            7, "session-token", source_type="upload", input_type="rgb", run_id=record.run_id
        )
        self.manager.close_session("another-session")
        self.assertFalse(capture.released)
        self.manager.close_session("session-token")
        self.assertTrue(capture.released)

        second_capture = FakeCapture([np.zeros((2, 2, 3), dtype=np.uint8)] * 4)
        self.manager.capture_factory = lambda _path: second_capture
        self.manager.acquire_next(
            7, "session-token", source_type="upload", input_type="rgb", run_id=record.run_id
        )
        self.manager.close_all()
        self.assertTrue(second_capture.released)


class Phase3FrameAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name)
        cls.runner_calls = []
        cls.capture_frames = [
            np.full((3, 5, 3), value, dtype=np.uint8)
            for value in (20, 80)
        ]
        cls.captures = []

        def capture_factory(_path):
            capture = FakeCapture(cls.capture_frames)
            cls.captures.append(capture)
            return capture

        cls.server = create_server(
            port=0,
            run_runner=lambda *args: cls.runner_calls.append(args),
            runs_root=cls.root / "runs",
            auth_db_path=cls.root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
            capture_factory=capture_factory,
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
            raise AssertionError("Could not create Phase 3 API test session.")
        cls.cookie = headers["Set-Cookie"].split(";", 1)[0]
        cls.run_record = cls.server.run_manager.create_run(
            "api-video.mp4",
            b"stored",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            input_metadata={"frame_count": 2},
            prepared=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp_dir.cleanup()

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

    def test_authenticated_api_returns_prepared_frames_then_end_of_video(self):
        payload = {
            "source_type": "upload",
            "input_type": "rgb",
            "run_id": self.run_record.run_id,
        }
        status, raw, _ = self.request(
            "/api/phase3/frame",
            method="POST",
            payload=payload,
            cookie=self.cookie,
        )
        first = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(first["frame_index"], 0)
        self.assertEqual(first["validation_status"], "valid")
        self.assertEqual(first["preprocessing_status"], "validated_contiguous_copy")
        self.assertEqual(first["source_type"], "upload")
        self.assertEqual(first["run_id"], self.run_record.run_id)
        decoded = base64.b64decode(first["frame_base64"], validate=True)
        self.assertTrue(decoded.startswith(b"\xff\xd8"))

        status, raw, _ = self.request(
            "/api/phase3/frame",
            method="POST",
            payload=payload,
            cookie=self.cookie,
        )
        self.assertEqual(json.loads(raw)["frame_index"], 1)
        status, raw, _ = self.request(
            "/api/phase3/frame",
            method="POST",
            payload=payload,
            cookie=self.cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["end_of_video"])
        self.assertTrue(self.captures[-1].released)
        self.assertEqual(self.runner_calls, [])

    def test_api_rejects_unauthorized_or_unsupported_phase3_inputs(self):
        status, raw, _ = self.request(
            "/api/phase3/frame",
            method="POST",
            payload={
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": self.run_record.run_id,
            },
        )
        self.assertEqual(status, 401)
        status, raw, _ = self.request(
            "/api/phase3/frame",
            method="POST",
            payload={
                "source_type": "upload",
                "input_type": "radar",
                "run_id": self.run_record.run_id,
            },
            cookie=self.cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"], "invalid_frame_source")


if __name__ == "__main__":
    unittest.main()
