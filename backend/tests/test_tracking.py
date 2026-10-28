from __future__ import annotations

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
from backend.detection import Detection, DetectionResult
from backend.frames import PreparedFrame
from backend.runs import RunError
from backend.tracking import TrackResult, TrackingService


def detection_result(
    run_id: str | None,
    frame_index: int,
    boxes: list[tuple[float, float, float, float]],
    *,
    input_type: str = "rgb",
) -> DetectionResult:
    detections = tuple(
        Detection(
            frame_index=frame_index,
            timestamp_sec=frame_index / 30,
            source_type="upload",
            class_id=index % 2,
            class_name=(
                ("Bird" if index % 2 == 0 else "Drone")
                if input_type == "thermal"
                else ("drone" if index % 2 == 0 else "bird")
            ),
            confidence=0.9,
            x1=box[0],
            y1=box[1],
            x2=box[2],
            y2=box[3],
        )
        for index, box in enumerate(boxes)
    )
    return DetectionResult(
        source_type="upload",
        input_type=input_type,
        run_id=run_id,
        live_reference_id=None,
        frame_index=frame_index,
        timestamp_sec=frame_index / 30,
        width=100,
        height=100,
        model_name="synthetic",
        inference_time_ms=0,
        detections=detections,
    )


class TrackingServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = TrackingService()

    def test_byte_track_converts_detections_and_preserves_ids_across_frames(self):
        first = self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30), (60, 60, 80, 80)]),
        )
        second = self.service.track(
            "session-a",
            detection_result("run-a", 1, [(11, 10, 31, 30), (61, 60, 81, 80)]),
        )

        self.assertEqual(len(self.service._trackers), 1)
        self.assertEqual(first.active_track_count, 2)
        self.assertEqual(second.active_track_count, 2)
        self.assertEqual(
            [track.track_id for track in first.tracks],
            [track.track_id for track in second.tracks],
        )
        self.assertEqual(len({track.track_id for track in first.tracks}), 2)
        self.assertEqual(second.tracks[0].frame_index, 1)
        self.assertEqual(second.tracks[0].timestamp_sec, 1 / 30)
        self.assertEqual(second.tracks[0].class_name, "drone")
        self.assertEqual(
            (second.tracks[0].x1, second.tracks[0].y1, second.tracks[0].x2, second.tracks[0].y2),
            (11, 10, 31, 30),
        )

    def test_byte_track_retains_id_after_a_temporary_missing_detection(self):
        first = self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )
        missing = self.service.track("session-a", detection_result("run-a", 1, []))
        returned = self.service.track(
            "session-a",
            detection_result("run-a", 2, [(11, 10, 31, 30)]),
        )

        self.assertEqual(missing.tracks, ())
        self.assertEqual(returned.tracks[0].track_id, first.tracks[0].track_id)

    def test_new_source_resets_tracker_and_run_state_is_isolated(self):
        first = self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )
        other_run = self.service.track(
            "session-a",
            detection_result("run-b", 0, [(10, 10, 30, 30)]),
        )
        other_session = self.service.track(
            "session-b",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )

        self.assertEqual(first.tracks[0].track_id, 1)
        self.assertEqual(other_run.tracks[0].track_id, 1)
        self.assertEqual(other_session.tracks[0].track_id, 1)
        self.assertEqual(len(self.service._trackers), 2)

    def test_cctv_reference_uses_same_byte_track_state_across_frames(self):
        first_frame = DetectionResult(
            source_type="cctv",
            input_type=None,
            run_id=None,
            live_reference_id="live-a",
            frame_index=None,
            timestamp_sec=None,
            width=100,
            height=100,
            model_name="synthetic",
            inference_time_ms=0,
            detections=(
                Detection(None, None, "cctv", 0, "drone", 0.9, 10, 10, 30, 30),
            ),
        )
        next_frame = DetectionResult(
            source_type="cctv",
            input_type=None,
            run_id=None,
            live_reference_id="live-a",
            frame_index=None,
            timestamp_sec=None,
            width=100,
            height=100,
            model_name="synthetic",
            inference_time_ms=0,
            detections=(
                Detection(None, None, "cctv", 0, "drone", 0.9, 11, 10, 31, 30),
            ),
        )

        first = self.service.track("session-a", first_frame)
        second = self.service.track("session-a", next_frame)

        self.assertEqual(first.tracks[0].track_id, second.tracks[0].track_id)
        self.assertEqual(second.live_reference_id, "live-a")

    def test_reset_clears_tracker_and_next_frame_starts_new_state(self):
        first = self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )

        self.service.reset_owner("session-a")
        next_result = self.service.track(
            "session-a",
            detection_result("run-a", 1, [(11, 10, 31, 30)]),
        )

        self.assertEqual(first.tracks[0].track_id, 1)
        self.assertEqual(next_result.tracks[0].track_id, 1)
        self.assertEqual(next_result.sequence_index, 0)
        self.assertEqual(len(self.service._trackers), 1)

    def test_backend_shutdown_cleanup_clears_all_trackers(self):
        self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )

        self.service.reset_all()

        self.assertEqual(self.service._trackers, {})
        self.assertEqual(self.service._active_source, {})

    def test_track_result_serializes_only_tracking_boundary_fields(self):
        result = self.service.track(
            "session-a",
            detection_result("run-a", 0, [(10, 10, 30, 30)]),
        )

        self.assertIsInstance(result, TrackResult)
        payload = result.as_dict()
        self.assertEqual(payload["active_track_count"], 1)
        self.assertEqual(payload["tracks"][0]["track_id"], 1)
        self.assertEqual(payload["tracks"][0]["source_type"], "upload")
        self.assertEqual(payload["sequence_index"], 0)
        self.assertNotIn("reliability_score", payload)
        self.assertNotIn("temporal_score", payload)
        self.assertNotIn("final_decision", payload)

    def test_thermal_tracking_is_supported_and_preserves_class_mapping(self):
        thermal = DetectionResult(
            source_type="upload",
            input_type="thermal",
            run_id="run-a",
            live_reference_id=None,
            frame_index=0,
            timestamp_sec=None,
            width=1,
            height=1,
            model_name="synthetic",
            inference_time_ms=0,
            detections=(
                Detection(
                    frame_index=0,
                    timestamp_sec=0.0,
                    source_type="upload",
                    class_id=0,
                    class_name="Bird",
                    confidence=0.9,
                    x1=10,
                    y1=10,
                    x2=30,
                    y2=30,
                ),
            ),
        )

        result = self.service.track("session-a", thermal)

        self.assertEqual(result.input_type, "thermal")
        self.assertEqual(result.active_track_count, 1)
        self.assertEqual(result.tracks[0].class_id, 0)
        self.assertEqual(result.tracks[0].class_name, "Bird")
        self.assertIn(("session-a", "upload", "thermal", "run-a"), self.service._trackers)
        self.assertNotIn(("session-a", "upload", "rgb", "run-a"), self.service._trackers)

    def test_unsupported_detection_source_fails_closed(self):
        unsupported = DetectionResult(
            source_type="upload",
            input_type="radar",
            run_id="run-a",
            live_reference_id=None,
            frame_index=0,
            timestamp_sec=None,
            width=1,
            height=1,
            model_name="synthetic",
            inference_time_ms=0,
            detections=(),
        )

        with self.assertRaisesRegex(RunError, "supports RGB or Thermal uploads"):
            self.service.track("session-a", unsupported)


class Phase5TrackingAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.captures = []
        cls.detector_calls = []
        cls.pipeline_calls = []

        class Capture:
            def __init__(self):
                self.position = 0
                self.released = False

            def isOpened(self):
                return True

            def read(self):
                if self.position >= 2:
                    return False, None
                self.position += 1
                return True, np.full((8, 10, 3), 60, dtype=np.uint8)

            def get(self, prop):
                import cv2

                if prop == cv2.CAP_PROP_FRAME_COUNT:
                    return 2
                if prop == cv2.CAP_PROP_POS_FRAMES:
                    return self.position
                return 0

            def release(self):
                self.released = True

        def capture_factory(_path):
            capture = Capture()
            cls.captures.append(capture)
            return capture

        class Detector:
            def detect(self, prepared):
                cls.detector_calls.append(prepared.frame_index)
                box = (10 + prepared.frame_index, 10, 30 + prepared.frame_index, 30)
                result = detection_result(
                    prepared.run_id,
                    prepared.frame_index,
                    [box],
                    input_type=prepared.input_type or "rgb",
                )
                return result, prepared.frame

            def close(self):
                return None

        cls.detector = Detector()
        cls.tracking = TrackingService()
        cls.server = create_server(
            project_root=Path(__file__).resolve().parents[2],
            port=0,
            runs_root=cls.root / "runs",
            auth_db_path=cls.root / "users.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
            capture_factory=capture_factory,
            detector_service=cls.detector,
            tracking_service=cls.tracking,
            run_runner=lambda *args: cls.pipeline_calls.append(args),
        )
        cls.temporal = cls.server.temporal_service
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
            raise AssertionError("Could not create Phase 5 API test session.")
        cls.cookie = headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.tracking.track(
            "server-shutdown-test",
            detection_result("shutdown-run", 0, [(10, 10, 30, 30)]),
        )
        cls.temporal.update(
            "server-shutdown-test",
            {
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": "shutdown-run",
                "frame_index": 0,
                "sequence_index": 0,
                "tracks": [],
            },
        )
        cls.server.shutdown()
        cls.server.server_close()
        if cls.tracking._trackers:
            raise AssertionError("Backend shutdown left Phase 5 tracker state active.")
        if cls.temporal._sources:
            raise AssertionError("Backend shutdown left Phase 6 temporal state active.")
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

    def create_run(self, input_type="rgb"):
        return self.server.run_manager.create_run(
            "tracking-test.mp4",
            b"stored",
            source_type="upload",
            input_type=input_type,
            input_status="validated",
            input_metadata={"frame_count": 2, "fps": 25},
            prepared=True,
        )

    def test_tracking_endpoint_requires_authentication(self):
        self.detector_calls.clear()
        record = self.create_run()
        status, raw, _ = self.request(
            "/api/phase5/track",
            method="POST",
            payload={"source_type": "upload", "input_type": "rgb", "run_id": record.run_id},
        )

        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "authentication_required")
        self.assertEqual(self.detector_calls, [])

    def test_phase6_consumes_phase5_results_and_finalizes_at_upload_end(self):
        record = self.create_run()
        source = {
            "source_type": "upload",
            "input_type": "rgb",
            "run_id": record.run_id,
        }
        for frame_index in range(2):
            status, raw, _ = self.request(
                "/api/phase5/track", "POST", source, self.cookie
            )
            tracked = json.loads(raw)
            self.assertEqual(status, 200)
            self.assertEqual(tracked["frame_index"], frame_index)
            detector_calls = len(self.detector_calls)
            source_sequences = dict(self.tracking._source_sequences)
            temporal_status, temporal_raw, _ = self.request(
                "/api/phase6/temporal", "POST", tracked, self.cookie
            )
            temporal = json.loads(temporal_raw)
            self.assertEqual(temporal_status, 200)
            self.assertEqual(temporal["frames_processed"], frame_index + 1)
            self.assertEqual(temporal["timing_basis"], "video_fps")
            self.assertEqual(temporal["frame_basis"], "source_frame_index")
            self.assertNotIn("reliability", temporal)
            self.assertNotIn("fusion", temporal)
            self.assertEqual(len(self.tracking._trackers), 1)
            self.assertEqual(len(self.detector_calls), detector_calls)
            self.assertEqual(self.tracking._source_sequences, source_sequences)

        end = {**source, "end_of_video": True}
        status, raw, _ = self.request(
            "/api/phase6/temporal", "POST", end, self.cookie
        )
        completed = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["tracks"][0]["temporal_summary"]["frames_seen"], 2)
        self.assertEqual(self.temporal._sources, {})
        self.assertEqual(self.pipeline_calls, [])

    def test_phase6_endpoint_requires_authentication(self):
        status, raw, _ = self.request(
            "/api/phase6/temporal",
            "POST",
            {"source_type": "upload", "input_type": "rgb", "run_id": "run"},
        )

        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "authentication_required")

    def test_tracking_endpoint_keeps_ids_across_frames_and_resets_at_end(self):
        record = self.create_run()
        request_body = {
            "source_type": "upload",
            "input_type": "rgb",
            "run_id": record.run_id,
        }
        first_status, first_raw, _ = self.request(
            "/api/phase5/track", "POST", request_body, self.cookie
        )
        second_status, second_raw, _ = self.request(
            "/api/phase5/track", "POST", request_body, self.cookie
        )
        end_status, end_raw, _ = self.request(
            "/api/phase5/track", "POST", request_body, self.cookie
        )
        first, second, ended = map(json.loads, (first_raw, second_raw, end_raw))

        self.assertEqual((first_status, second_status, end_status), (200, 200, 200))
        self.assertEqual(first["tracks"][0]["track_id"], second["tracks"][0]["track_id"])
        self.assertEqual(first["frame_index"], 0)
        self.assertEqual(second["frame_index"], 1)
        self.assertTrue(ended["end_of_video"])
        self.assertEqual(self.detector_calls[-2:], [0, 1])
        self.assertEqual(self.tracking._trackers, {})
        self.assertEqual(self.pipeline_calls, [])

    def test_thermal_tracking_endpoint_uses_thermal_runtime_and_keeps_bird_class(self):
        record = self.create_run(input_type="thermal")
        source = {
            "source_type": "upload",
            "input_type": "thermal",
            "run_id": record.run_id,
        }
        frame = PreparedFrame(
            frame=np.full((8, 10), 64, dtype=np.uint8),
            source_type="upload",
            input_type="thermal",
            run_id=record.run_id,
            live_reference_id=None,
            frame_index=0,
            timestamp_sec=0.0,
            width=10,
            height=8,
            channels=1,
            dtype="uint8",
            validation_status="valid",
            preprocessing_status="thermal_test_frame",
        )
        with patch.object(self.server.frame_manager, "acquire_next", return_value=frame):
            status, raw, _ = self.request("/api/phase5/track", "POST", source, self.cookie)
        body = json.loads(raw)

        self.assertEqual(status, 200)
        self.assertEqual(body["input_type"], "thermal")
        self.assertEqual(body["frame_index"], 0)
        self.assertEqual(body["active_track_count"], 1)
        self.assertEqual(body["tracks"][0]["class_id"], 0)
        self.assertEqual(body["tracks"][0]["class_name"], "Bird")
        thermal_trackers = [
            key for key in self.tracking._trackers
            if key[2] == "thermal" and key[3] == record.run_id
        ]
        self.assertEqual(len(thermal_trackers), 1)
        self.assertEqual(self.pipeline_calls, [])

    def test_phase4_endpoint_remains_tracking_free(self):
        record = self.create_run()
        before = dict(self.tracking._trackers)
        status, raw, _ = self.request(
            "/api/phase4/detect",
            "POST",
            {"source_type": "upload", "input_type": "rgb", "run_id": record.run_id},
            self.cookie,
        )

        body = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertNotIn("track_id", body["detections"][0])
        self.assertEqual(self.tracking._trackers, before)

    def test_reset_endpoint_clears_session_tracker(self):
        result = self.tracking.track(
            self.cookie.split("=", 1)[1],
            detection_result("unrelated-run", 0, [(10, 10, 30, 30)]),
        )
        self.assertEqual(result.active_track_count, 1)

        status, raw, _ = self.request(
            "/api/phase5/reset", "POST", cookie=self.cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["tracking_status"], "reset")
        self.assertEqual(self.tracking._trackers, {})

    def test_logout_clears_session_tracker(self):
        owner = self.cookie.split("=", 1)[1]
        self.tracking.track(owner, detection_result("logout-run", 0, [(10, 10, 30, 30)]))
        self.temporal.update(
            owner,
            {
                "source_type": "upload",
                "input_type": "rgb",
                "run_id": "logout-run",
                "frame_index": 0,
                "sequence_index": 0,
                "tracks": [],
            },
        )
        status, _, _ = self.request("/api/auth/logout", "POST", cookie=self.cookie)

        self.assertEqual(status, 200)
        self.assertEqual(self.tracking._trackers, {})
        self.assertEqual(self.temporal._sources, {})
        _, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        type(self).cookie = headers["Set-Cookie"].split(";", 1)[0]


if __name__ == "__main__":
    unittest.main()
