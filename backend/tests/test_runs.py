from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from backend.app import ThermalRunData, create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.config import get_settings
from backend.run_pipeline import (
    _prepare_tracking_checkpoint,
    _parse_detector_frame_progress,
    _publish_early_uav_alert,
    _require_track_observations,
)
from backend.runs import RunManager


class FakeRunData:
    def overview(self):
        return {"total_tracks": 1}

    def track_list(self):
        return [{"track_id": "1", "source": "input_video.mp4"}]

    @property
    def tracks(self):
        return {"1": [{}]}

    def fusion_summary(self):
        return {"track_count": 1}

    def evidence(self, track_id):
        return [
            {
                "track_id": track_id,
                "crop_path": "selected/track_1/frame_000001.jpg",
            }
        ]

    def _vlm_result(self, track_id):
        return {"available": False, "result": None}

    def _track(self, track_id):
        if track_id != "1":
            raise KeyError(track_id)
        return {}

    def modalities(self, track_id):
        return {"track_id": track_id, "radar": {"track_association_status": "not_established"}}

    def fusion_result(self, track_id):
        return {"track_id": track_id, "final_label": "UAV", "final_confidence": 0.5}

    def track_detail(self, track_id):
        return {
            "track_id": track_id,
            "fusion": self.fusion_result(track_id),
            "selected_evidence": self.evidence(track_id),
        }


class DynamicRunAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.runs_root = Path(cls.temp_dir.name) / "dashboard_runs"
        cls.release_runner = threading.Event()
        cls.runner_started = threading.Event()
        cls.process_started = threading.Event()
        cls.active_process = None
        cls.server = create_server(
            port=0,
            run_runner=cls.fake_runner,
            max_upload_bytes=16,
            runs_root=cls.runs_root,
            auth_db_path=Path(cls.temp_dir.name) / "auth.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
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
            cls.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        cls.historical_hashes = cls._historical_hashes()

    @classmethod
    def tearDownClass(cls):
        cls.release_runner.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        for record in list(cls.server.run_manager._runs.values()):
            if record.thread:
                record.thread.join(timeout=5)
        cls.assert_historical_hashes()
        shutil.rmtree(cls.runs_root, ignore_errors=True)
        cls.temp_dir.cleanup()

    @staticmethod
    def _historical_hashes():
        root = get_settings().results_root
        paths = (
            root / "tracks.csv",
            root / "fusion_baseline.csv",
            root / "fusion_vlm_exploratory.csv",
            root / "phase5_fusion" / "phase5_fusion_results.csv",
            root / "phase5_fusion" / "phase5_evidence_availability.csv",
        )
        return {
            path: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        }

    @classmethod
    def assert_historical_hashes(cls):
        for path, expected in cls.historical_hashes.items():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise AssertionError(f"Historical result changed: {path.name}")

    @classmethod
    def fake_runner(cls, project_root, run_root, input_video, set_stage):
        payload = input_video.read_bytes()
        set_stage("temporal", 38)
        evidence = run_root / "evidence" / "selected" / "track_1"
        evidence.mkdir(parents=True)
        if payload == b"evidence":
            (evidence / "frame_000001.jpg").write_bytes(b"test image")
        if payload == b"cancel-process":
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            cls.active_process = process
            set_stage.set_process(process)
            cls.process_started.set()
            while process.poll() is None:
                set_stage.check_cancelled()
                time.sleep(0.01)
            set_stage.check_cancelled()
        if payload in (b"block", b"alert-block"):
            cls.runner_started.set()
            while not cls.release_runner.wait(timeout=0.02):
                set_stage.check_cancelled()
        return FakeRunData()

    def request(
        self, path, method="GET", body=None, content_type=None,
        authenticated=True, extra_headers=None,
    ):
        headers = {}
        if authenticated and getattr(self, "auth_cookie", None):
            headers["Cookie"] = self.auth_cookie
        if content_type:
            headers["Content-Type"] = content_type
        headers.update(extra_headers or {})
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers.get_content_type()
        except HTTPError as error:
            return error.code, error.read(), error.headers.get_content_type()

    def upload(self, filename="sample.mp4", payload=b"video"):
        boundary = "----boundary-test-6f82"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="video"; filename="{filename}"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        status, raw, _ = self.request(
            "/api/runs",
            method="POST",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return status, json.loads(raw)

    def test_run_video_ownership_and_cached_result_discovery(self):
        foreign = self.server.run_manager.create_run(
            "foreign.mp4", b"private-video", owner_id=2
        )
        self.assertEqual(
            self.request(f"/api/runs/{foreign.run_id}/video")[0],
            404,
        )

        cached = self.server.run_manager.create_run(
            "cached.mp4", b"same-video", owner_id=1
        )
        cached.status = "completed"
        (cached.root / "final_results.json").write_text("{}", encoding="utf-8")
        tracks = cached.root / "tracking" / "detector"
        tracks.mkdir(parents=True)
        (tracks / "tracks.csv").write_text("frame,track_id\n", encoding="utf-8")
        current = self.server.run_manager.create_run(
            "current.mp4", b"same-video", owner_id=1
        )
        status, raw, _ = self.request(
            f"/api/runs/{current.run_id}/reuse-candidates"
        )
        self.assertEqual(status, 200)
        candidates = json.loads(raw)["candidates"]
        self.assertEqual([item["run_id"] for item in candidates], [cached.run_id])
        self.assertEqual(candidates[0]["reuse_mode"], "read_only_completed_results")
        status, raw, content_type = self.request(f"/api/runs/{cached.run_id}/video")
        self.assertEqual((status, content_type, raw), (200, "video/mp4", b"same-video"))
        status, raw, content_type = self.request(
            f"/api/runs/{cached.run_id}/video",
            extra_headers={"Range": "bytes=2-5"},
        )
        self.assertEqual((status, content_type, raw), (206, "video/mp4", b"me-v"))
        self.assertNotIn(
            foreign.run_id,
            [item["run_id"] for item in json.loads(self.request("/api/runs")[1])["runs"]],
        )

    def test_fresh_reanalysis_creates_a_distinct_prepared_run(self):
        source = self.server.run_manager.create_run(
            "source.mp4",
            b"original-source",
            original_filename="site_capture.mp4",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            prepared=True,
            owner_id=1,
        )
        status, raw, _ = self.request(
            f"/api/runs/{source.run_id}/reanalyze",
            method="POST",
        )
        self.assertEqual(status, 201)
        created = json.loads(raw)
        self.assertNotEqual(created["run_id"], source.run_id)
        self.assertEqual(created["status"], "prepared")
        fresh = self.server.run_manager.get_owned(created["run_id"], 1)
        self.assertEqual(fresh.input_path.read_bytes(), b"original-source")
        self.assertEqual(fresh.input_filename, "input_video.mp4")
        self.assertEqual(fresh.original_filename, "site_capture.mp4")
        self.assertEqual(source.status, "prepared")

    def test_tracking_checkpoint_resume_requires_compatible_validated_artifacts(self):
        source = self.server.run_manager.create_run(
            "resume.mp4",
            b"resume-video",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            prepared=True,
            owner_id=1,
        )
        configuration = {
            "source_video_sha256": hashlib.sha256(b"resume-video").hexdigest(),
            "source_video_fps": 25.0,
            "source_video_width": 640.0,
            "source_video_height": 360.0,
            "detector_checkpoint_name": "best.pt",
            "detector_checkpoint_sha256": "a" * 64,
            "detector_model_architecture": "yv5_gh (GhostNet + AFNO2D)",
            "detector_image_size": [640, 640],
            "detector_confidence_threshold": 0.25,
            "detector_iou_threshold": 0.45,
            "detector_device": "cpu",
            "detector_preprocessing": "validated test configuration",
            "tracking_checkpoint_contract": "detector-tracker-v1",
        }
        self.server.run_manager._update_run_manifest(source, **configuration)
        source.status = "failed"
        source.stage = "failed"
        source.phase_progress = {
            "detection": {"status": "completed"},
            "tracking": {"status": "completed"},
        }
        self.server.run_manager._persist(source)
        tracks = source.root / "tracking" / "detector"
        tracks.mkdir(parents=True)
        (tracks / "tracks.csv").write_text(
            "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
            "0,3,Bird,0.8,1,2,10,12\n",
            encoding="utf-8",
        )
        detector_manifest = {
            key: configuration[key]
            for key in (
                "detector_checkpoint_sha256",
                "detector_model_architecture",
                "detector_image_size",
                "detector_confidence_threshold",
                "detector_iou_threshold",
                "detector_device",
            )
        }
        (source.root / "detector_manifest.json").write_text(
            json.dumps(detector_manifest),
            encoding="utf-8",
        )
        with patch(
            "backend.run_pipeline.run_reproducibility_metadata",
            return_value=configuration,
        ):
            status, raw, _ = self.request(
                f"/api/runs/{source.run_id}/resume-checkpoint"
            )
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)["available"])
            status, raw, _ = self.request(
                f"/api/runs/{source.run_id}/resume",
                method="POST",
            )
            self.assertEqual(status, 201)
            resumed = self.server.run_manager.get(json.loads(raw)["run_id"])
        self.assertNotEqual(resumed.run_id, source.run_id)
        self.assertTrue(resumed.resume_after_tracking)
        self.assertEqual(
            (resumed.root / "tracking" / "detector" / "tracks.csv").read_bytes(),
            (tracks / "tracks.csv").read_bytes(),
        )
        self.assertEqual(source.status, "failed")
        manifest_path = source.root / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        incompatible_configs = (
            ("source_video_sha256", "0" * 64),
            ("detector_checkpoint_sha256", "0" * 64),
        )
        for key, value in incompatible_configs:
            with self.subTest(incompatible_key=key):
                incompatible = dict(configuration)
                incompatible[key] = value
                with patch(
                    "backend.run_pipeline.run_reproducibility_metadata",
                    return_value=incompatible,
                ):
                    status, raw, _ = self.request(
                        f"/api/runs/{source.run_id}/resume-checkpoint"
                    )
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(raw)["available"])

        manifest["run_id"] = "RUN_20260101_000000_" + "f" * 24
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch(
            "backend.run_pipeline.run_reproducibility_metadata",
            return_value=configuration,
        ):
            status, raw, _ = self.request(
                f"/api/runs/{source.run_id}/resume-checkpoint"
            )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["available"])

        manifest["run_id"] = source.run_id
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        tracks_file = tracks / "tracks.csv"
        tracks_file.write_text(
            "frame,track_id,class_name,confidence,x1,y1,x2,y2\nmalformed\n",
            encoding="utf-8",
        )
        with patch(
            "backend.run_pipeline.run_reproducibility_metadata",
            return_value=configuration,
        ):
            status, raw, _ = self.request(
                f"/api/runs/{source.run_id}/resume-checkpoint"
            )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["available"])

        tracks_file.unlink()
        with patch(
            "backend.run_pipeline.run_reproducibility_metadata",
            return_value=configuration,
        ):
            status, raw, _ = self.request(
                f"/api/runs/{source.run_id}/resume-checkpoint"
            )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["available"])

    def test_pipeline_uses_validated_tracking_checkpoint_without_starting_detector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "resumed"
            input_video = run_root / "input" / "video.mp4"
            input_video.parent.mkdir(parents=True)
            input_video.write_bytes(b"resume-video")
            tracks = run_root / "tracking" / "detector" / "tracks.csv"
            tracks.parent.mkdir(parents=True)
            tracks.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "0,3,Bird,0.8,1,2,10,12\n",
                encoding="utf-8",
            )
            configuration = {
                "source_video_sha256": hashlib.sha256(b"resume-video").hexdigest(),
                "detector_checkpoint_sha256": "b" * 64,
                "detector_model_architecture": "yv5_gh (GhostNet + AFNO2D)",
                "detector_image_size": [640, 640],
                "detector_confidence_threshold": 0.25,
                "detector_iou_threshold": 0.45,
                "detector_device": "cpu",
                "tracking_checkpoint_contract": "detector-tracker-v1",
            }
            (run_root / "run_manifest.json").write_text(
                json.dumps({"resume_configuration": configuration}),
                encoding="utf-8",
            )
            (run_root / "detector_manifest.json").write_text(
                json.dumps(configuration),
                encoding="utf-8",
            )

            class ResumeStage:
                record = type("RunState", (), {"resume_after_tracking": True})()

                def __call__(self, _stage, _progress):
                    raise AssertionError("A validated resume must not re-run detection")

            completed_phases = []
            with (
                patch(
                    "backend.run_pipeline.run_reproducibility_metadata",
                    return_value=configuration,
                ),
                patch(
                    "backend.run_pipeline._run_detector_and_tracker",
                    side_effect=AssertionError("Detector must be skipped"),
                ),
            ):
                result = _prepare_tracking_checkpoint(
                    root,
                    run_root,
                    input_video,
                    ResumeStage(),
                    lambda phase, status, **_kwargs: completed_phases.append((phase, status)),
                    lambda: None,
                )
            self.assertEqual(result, tracks)
            self.assertEqual(
                completed_phases,
                [("detection", "completed"), ("tracking", "completed")],
            )

    def create_frame_lookup_run(self):
        record = self.server.run_manager.create_run(
            "frame-lookup.mp4",
            b"video",
            input_type="rgb",
            input_metadata={"frame_count": 4, "fps": 25.0},
        )
        tracking_dir = record.root / "tracking" / "detector"
        tracking_dir.mkdir(parents=True)
        with (tracking_dir / "tracks.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "frame",
                    "track_id",
                    "class_name",
                    "confidence",
                    "x1",
                    "y1",
                    "x2",
                    "y2",
                ),
            )
            writer.writeheader()
            writer.writerows(
                (
                    {
                        "frame": 2,
                        "track_id": 7,
                        "class_name": "Drone",
                        "confidence": 0.875,
                        "x1": 1,
                        "y1": 2,
                        "x2": 30,
                        "y2": 40,
                    },
                    {
                        "frame": 4,
                        "track_id": 8,
                        "class_name": "Bird",
                        "confidence": 0.625,
                        "x1": 3,
                        "y1": 4,
                        "x2": 50,
                        "y2": 60,
                    },
                )
            )
        return record

    def test_thermal_result_apis_return_detector_only_alert_and_skip_unsupported_stages(self):
        record = self.server.run_manager.create_run(
            "thermal.mp4",
            b"thermal-video",
            source_type="upload",
            input_type="thermal",
            input_status="validated",
            prepared=True,
            owner_id=1,
        )
        tracks_dir = record.root / "tracking" / "detector"
        tracks_dir.mkdir(parents=True)
        (tracks_dir / "tracks.csv").write_text(
            "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
            "thermal.mp4,1,0,0.0,7,1,Drone,0.8,1,2,5,6\n",
            encoding="utf-8",
        )
        record.project_data = ThermalRunData(tracks_dir / "tracks.csv")
        record.status = "completed"
        record.stage = "completed"
        report_path = record.root / "final_results.json"
        report_path.write_text(
            json.dumps(
                {
                    "analysis_mode": "thermal_detection_tracking_only",
                    "alert_status": "not_run",
                    "uav_alert": False,
                    "track_count": 1,
                    "tracks": [{"track_id": "7", "detector_class": "Drone"}],
                    "unsupported_stages": ["reliability", "evidence", "vlm", "fusion", "assessment"],
                }
            ),
            encoding="utf-8",
        )

        status, raw, _ = self.request(
            f"/api/runs/{record.run_id}/tracks/7"
        )
        self.assertEqual(status, 200)
        detail = json.loads(raw)
        self.assertEqual(detail["detector_label"], "Drone")
        self.assertEqual(detail["evidence_ledger"]["schema_version"], 1)
        self.assertEqual(detail["evidence_ledger"]["status"], "available")
        self.assertEqual(detail["evidence_ledger"]["run_id"], record.run_id)
        self.assertEqual(detail["assessment"]["assessment"], "NOT_RUN")
        self.assertIsNone(detail["fusion"]["final_label"])

        status, raw, _ = self.request(
            f"/api/runs/{record.run_id}/fusion/summary"
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["status"], "not_run")

        status, raw, _ = self.request(
            f"/api/runs/{record.run_id}/report"
        )
        self.assertEqual(status, 200)
        report = json.loads(raw)
        self.assertEqual(report["alert_status"], "detector_only")
        self.assertEqual(report["alert_level"], "REVIEW")
        self.assertEqual(report["alert_track_ids"], ["7"])
        self.assertEqual(
            report["alert"]["evidence"],
            [
                {
                    "track_id": 7,
                    "observation": {
                        "frame_index": 1,
                        "track_id": 7,
                        "detector_class": "Drone",
                        "detector_confidence": 0.8,
                        "bbox": {"x1": 1.0, "y1": 2.0, "x2": 5.0, "y2": 6.0},
                        "total_frames": None,
                        "timestamp": None,
                        "reliability_score": None,
                        "reliability_status": None,
                    },
                }
            ],
        )

        status, raw, _ = self.request(
            f"/api/runs/{record.run_id}/early-alert"
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["alert_status"], "detector_only")

    def test_failed_thermal_run_does_not_expose_partial_results_as_completed(self):
        record = self.server.run_manager.create_run(
            "thermal.mp4",
            b"thermal-video",
            source_type="upload",
            input_type="thermal",
            input_status="validated",
            prepared=True,
            owner_id=1,
        )
        tracks_dir = record.root / "tracking" / "detector"
        tracks_dir.mkdir(parents=True)
        (tracks_dir / "tracks.csv").write_text(
            "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
            "thermal.mp4,1,0,0.0,7,1,Drone,0.8,1,2,5,6\n",
            encoding="utf-8",
        )
        record.status = "failed"
        record.stage = "failed"
        status, raw, _ = self.request(f"/api/runs/{record.run_id}/tracks/7")

        self.assertEqual(status, 409)
        self.assertEqual(json.loads(raw)["error"], "run_results_unavailable")

    def test_frame_lookup_requires_authentication_and_existing_run(self):
        record = self.create_frame_lookup_run()
        status, raw, _ = self.request(
            f"/api/runs/{record.run_id}/frames/2",
            authenticated=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"], "authentication_required")

        missing_id = "RUN_20260101_000000_" + "0" * 24
        status, raw, _ = self.request(f"/api/runs/{missing_id}/frames/2")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(raw)["error"], "run_not_found")

    def test_frame_lookup_returns_persisted_observations_and_metadata(self):
        record = self.create_frame_lookup_run()
        status, raw, _ = self.request(f"/api/runs/{record.run_id}/frames/2")

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(raw),
            {
                "run_id": record.run_id,
                "frame_index": 2,
                "total_frames": 4,
                "timestamp": 0.04,
                "observations": [
                    {
                        "track_id": 7,
                        "class": "Drone",
                        "confidence": 0.875,
                        "bbox": {"x1": 1.0, "y1": 2.0, "x2": 30.0, "y2": 40.0},
                    }
                ],
            },
        )

    def test_frame_lookup_returns_empty_observations_for_untracked_frame(self):
        record = self.create_frame_lookup_run()
        status, raw, _ = self.request(f"/api/runs/{record.run_id}/frames/3")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["observations"], [])

    def test_frame_lookup_rejects_invalid_frame_indices(self):
        record = self.create_frame_lookup_run()
        for frame_index in ("abc", "0", "5", "-1"):
            with self.subTest(frame_index=frame_index):
                status, raw, _ = self.request(
                    f"/api/runs/{record.run_id}/frames/{frame_index}"
                )
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw)["error"], "invalid_frame_index")

    def test_frame_lookup_never_invokes_detector(self):
        record = self.create_frame_lookup_run()
        with patch.object(
            self.server.detector_service,
            "detect",
            side_effect=AssertionError("frame lookup must not run inference"),
        ) as detect:
            status, _, _ = self.request(f"/api/runs/{record.run_id}/frames/2")

        self.assertEqual(status, 200)
        detect.assert_not_called()

    def test_detector_frame_progress_uses_loader_frame_and_total(self):
        self.assertEqual(
            _parse_detector_frame_progress(
                "INFO: video 1/1 (480/800) input_video.mp4: 640x640"
            ),
            (480, 800),
        )
        self.assertIsNone(_parse_detector_frame_progress("Speed: 5.0ms inference"))

    def test_early_alert_is_authenticated_and_available_while_processing(self):
        self.release_runner.clear()
        self.runner_started.clear()
        status, created = self.upload(payload=b"alert-block")
        self.assertEqual(status, 201)
        run_id = created["run_id"]
        start_status, _, _ = self.request(
            f"/api/runs/{run_id}/start", method="POST", body=b""
        )
        self.assertEqual(start_status, 202)
        self.assertTrue(self.runner_started.wait(timeout=2))
        record = self.server.run_manager.get(run_id)
        pending_status, pending_raw, _ = self.request(
            f"/api/runs/{run_id}/early-alert"
        )
        self.assertEqual(pending_status, 200)
        self.assertIsNone(json.loads(pending_raw)["early_alert"])
        expected_alert = {
            "uav_alert": True,
            "level": "HIGH",
            "track_id": "7",
            "message": "One or more tracks have been classified as UAV. Immediate review recommended.",
            "published_at": "2026-01-01T00:00:00+00:00",
        }
        (record.root / "early_alert.json").write_text(
            json.dumps(expected_alert), encoding="utf-8"
        )

        unauthorized_status, _, _ = self.request(
            f"/api/runs/{run_id}/early-alert", authenticated=False
        )
        self.assertEqual(unauthorized_status, 401)
        response_status, raw, _ = self.request(f"/api/runs/{run_id}/early-alert")
        self.assertEqual(response_status, 200)
        result = json.loads(raw)
        self.assertEqual(result["run_status"], "processing")
        self.assertEqual(result["early_alert"], expected_alert)
        self.assertEqual(result["alert"]["level"], "HIGH")
        self.assertEqual(result["alert"]["timestamp"], expected_alert["published_at"])
        self.assertEqual(
            result["alert"]["evidence"],
            [{"track_id": 7, "observation": None}],
        )

        missing_status, missing, _ = self.request(
            "/api/runs/RUN_20260101_010101_012345678901234567890123/early-alert"
        )
        self.assertEqual(missing_status, 404)
        self.assertEqual(json.loads(missing)["error"], "run_not_found")
        self.release_runner.set()
        self.wait_for_status(run_id, "completed")
        self.release_runner.clear()

    def test_early_alert_evidence_uses_highest_confidence_stored_observation(self):
        record = self.create_frame_lookup_run()
        tracks_path = record.root / "tracking" / "detector" / "tracks.csv"
        with tracks_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "frame", "track_id", "class_name", "confidence",
                    "x1", "y1", "x2", "y2",
                ),
            )
            writer.writeheader()
            writer.writerows(
                (
                    {"frame": 2, "track_id": 7, "class_name": "Drone", "confidence": 0.91, "x1": 1, "y1": 2, "x2": 30, "y2": 40},
                    {"frame": 1, "track_id": 7, "class_name": "Drone", "confidence": 0.91, "x1": 5, "y1": 6, "x2": 35, "y2": 45},
                    {"frame": 3, "track_id": 8, "class_name": "Bird", "confidence": 0.99, "x1": 7, "y1": 8, "x2": 37, "y2": 47},
                )
            )
        reliability_dir = record.root / "reliability"
        reliability_dir.mkdir()
        with (reliability_dir / "reliability_results.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            writer = csv.DictWriter(
                output, fieldnames=("track_id", "reliability_score", "status")
            )
            writer.writeheader()
            writer.writerow(
                {"track_id": 7, "reliability_score": 0.8, "status": "CONFIDENT"}
            )
        (record.root / "early_alert.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "level": "HIGH",
                    "track_id": "7",
                    "message": "UAV alert",
                    "published_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            self.server.detector_service,
            "detect",
            side_effect=AssertionError("alert evidence lookup must not infer"),
        ) as detect:
            status, raw, _ = self.request(
                f"/api/runs/{record.run_id}/early-alert"
            )

        self.assertEqual(status, 200)
        evidence = json.loads(raw)["alert"]["evidence"][0]["observation"]
        self.assertEqual(
            evidence,
            {
                "frame_index": 1,
                "track_id": 7,
                "detector_class": "Drone",
                "detector_confidence": 0.91,
                "bbox": {"x1": 5.0, "y1": 6.0, "x2": 35.0, "y2": 45.0},
                "total_frames": 4,
                "timestamp": 0.0,
                "reliability_score": 0.8,
                "reliability_status": "CONFIDENT",
            },
        )
        detect.assert_not_called()

    def test_provisional_early_alert_evidence_uses_published_trigger_frame(self):
        record = self.create_frame_lookup_run()
        record.status = "processing"
        tracks_path = record.root / "tracking" / "detector" / "tracks.csv"
        with tracks_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "frame", "track_id", "class_name", "confidence",
                    "x1", "y1", "x2", "y2",
                ),
            )
            writer.writeheader()
            writer.writerows(
                (
                    {"frame": 1, "track_id": 7, "class_name": "Drone", "confidence": 0.55, "x1": 1, "y1": 2, "x2": 30, "y2": 40},
                    {"frame": 3, "track_id": 7, "class_name": "Drone", "confidence": 0.56, "x1": 2, "y1": 3, "x2": 31, "y2": 41},
                    {"frame": 4, "track_id": 7, "class_name": "Drone", "confidence": 0.99, "x1": 3, "y1": 4, "x2": 32, "y2": 42},
                )
            )
        (record.root / "early_alert.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "state": "provisional",
                    "level": "REVIEW",
                    "track_id": "7",
                    "first_detection_frame": 1,
                    "published_frame": 3,
                    "message": "Preliminary review",
                    "published_at": "2026-01-01T00:00:01+00:00",
                }
            ),
            encoding="utf-8",
        )

        status, raw, _ = self.request(f"/api/runs/{record.run_id}/early-alert")

        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result["alert"]["first_detection_frame"], 1)
        self.assertEqual(result["alert"]["published_frame"], 3)
        self.assertEqual(result["alert"]["timestamp"], "2026-01-01T00:00:01+00:00")
        observation = result["alert"]["evidence"][0]["observation"]
        self.assertEqual(observation["frame_index"], 3)
        self.assertAlmostEqual(observation["timestamp"], 0.08)
        self.assertEqual(observation["detector_confidence"], 0.56)

    def test_completed_report_keeps_representative_evidence_after_trigger_frame(self):
        record = self.create_frame_lookup_run()
        record.status = "completed"
        record.project_data = FakeRunData()
        record.input_metadata = {"frame_count": 60, "fps": 30.0}
        tracks_path = record.root / "tracking" / "detector" / "tracks.csv"
        with tracks_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "frame", "track_id", "class_name", "confidence",
                    "x1", "y1", "x2", "y2",
                ),
            )
            writer.writeheader()
            writer.writerows(
                (
                    {"frame": 3, "track_id": 7, "class_name": "Drone", "confidence": 0.56, "x1": 2, "y1": 3, "x2": 31, "y2": 41},
                    {"frame": 55, "track_id": 7, "class_name": "Drone", "confidence": 0.95, "x1": 3, "y1": 4, "x2": 32, "y2": 42},
                )
            )
        (record.root / "final_results.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "tracks": [{"track_id": "7", "final_label": "UAV"}],
                }
            ),
            encoding="utf-8",
        )
        (record.root / "early_alert.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "state": "confirmed_review",
                    "track_id": "7",
                    "first_detection_frame": 1,
                    "published_frame": 3,
                    "published_at": "2026-01-01T00:00:01+00:00",
                }
            ),
            encoding="utf-8",
        )

        status, raw, _ = self.request(f"/api/runs/{record.run_id}/report")

        self.assertEqual(status, 200)
        alert = json.loads(raw)["alert"]
        self.assertEqual(alert["first_detection_frame"], 1)
        self.assertEqual(alert["published_frame"], 3)
        self.assertEqual(alert["evidence"][0]["observation"]["frame_index"], 55)
        self.assertAlmostEqual(alert["evidence"][0]["observation"]["timestamp"], 1.8)

    def test_early_alert_evidence_is_unavailable_when_track_has_no_observations(self):
        record = self.create_frame_lookup_run()
        (record.root / "early_alert.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "level": "HIGH",
                    "track_id": "99",
                    "message": "UAV alert",
                    "published_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

        status, raw, _ = self.request(f"/api/runs/{record.run_id}/early-alert")

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(raw)["alert"]["evidence"],
            [{"track_id": 99, "observation": None}],
        )

    def test_final_report_keeps_evidence_for_each_uav_track(self):
        record = self.create_frame_lookup_run()
        record.status = "completed"
        record.project_data = FakeRunData()
        record.input_metadata = {"frame_count": 4, "fps": 25.0}
        (record.root / "final_results.json").write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "tracks": [
                        {"track_id": "7", "final_label": "UAV"},
                        {"track_id": "8", "final_label": "UAV"},
                        {"track_id": "9", "final_label": "Bird"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        reliability_dir = record.root / "reliability"
        reliability_dir.mkdir()
        (reliability_dir / "reliability_results.csv").write_text(
            "track_id,reliability_score,status\n7,0.8,CONFIDENT\n",
            encoding="utf-8",
        )

        status, raw, _ = self.request(f"/api/runs/{record.run_id}/report")

        self.assertEqual(status, 200)
        result = json.loads(raw)
        self.assertEqual(result["source_video_id"], record.source_video_id)
        self.assertEqual(result["alert"]["level"], "REVIEW")
        self.assertIn("not been independently verified", result["alert"]["message"])
        self.assertEqual(
            [entry["track_id"] for entry in result["alert"]["evidence"]],
            [7, 8],
        )
        self.assertIsNotNone(result["alert"]["evidence"][0]["observation"])
        self.assertEqual(
            result["alert"]["evidence"][1]["observation"]["detector_class"],
            "Bird",
        )
        self.assertIsNone(
            result["alert"]["evidence"][1]["observation"]["reliability_score"]
        )

    def test_early_alert_uses_fusion_for_confident_tracks_and_is_monotonic(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            tracks_csv = run_root / "tracks.csv"
            reliability_csv = run_root / "reliability.csv"
            with tracks_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "frame",
                        "track_id",
                        "class_name",
                        "confidence",
                        "x1",
                        "y1",
                        "x2",
                        "y2",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "frame": 1,
                        "track_id": 7,
                        "class_name": "Drone",
                        "confidence": 0.9,
                        "x1": 1,
                        "y1": 2,
                        "x2": 3,
                        "y2": 4,
                    }
                )
            with reliability_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=("track_id", "reliability_score", "status"),
                )
                writer.writeheader()
                writer.writerow(
                    {"track_id": 7, "reliability_score": 0.8, "status": "CONFIDENT"}
                )
                writer.writerow(
                    {"track_id": 8, "reliability_score": 0.95, "status": "UNCERTAIN"}
                )

            alert = _publish_early_uav_alert(run_root, tracks_csv, reliability_csv)
            self.assertIsNotNone(alert)
            self.assertTrue(alert["uav_alert"])
            self.assertEqual(alert["track_id"], "7")
            tracks_csv.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "1,7,Bird,0.9,1,2,3,4\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _publish_early_uav_alert(run_root, tracks_csv, reliability_csv),
                alert,
            )

    def test_early_alert_is_not_published_for_uncertain_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            tracks_csv = run_root / "tracks.csv"
            reliability_csv = run_root / "reliability.csv"
            tracks_csv.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "1,7,Drone,0.9,1,2,3,4\n",
                encoding="utf-8",
            )
            reliability_csv.write_text(
                "track_id,reliability_score,status\n7,0.95,UNCERTAIN\n",
                encoding="utf-8",
            )

            self.assertIsNone(
                _publish_early_uav_alert(run_root, tracks_csv, reliability_csv)
            )
            self.assertFalse((run_root / "early_alert.json").exists())
            tracks_csv.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "1,7,Bird,0.9,1,2,3,4\n",
                encoding="utf-8",
            )
            reliability_csv.write_text(
                "track_id,reliability_score,status\n7,0.95,CONFIDENT\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                _publish_early_uav_alert(run_root, tracks_csv, reliability_csv)
            )
            self.assertFalse((run_root / "early_alert.json").exists())

    def test_early_alert_uses_temporal_consensus_for_mixed_class_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            tracks_csv = run_root / "tracks.csv"
            reliability_csv = run_root / "reliability.csv"
            consensus_csv = run_root / "consensus.csv"
            tracks_csv.write_text(
                "frame,track_id,class_name,confidence,x1,y1,x2,y2\n"
                "1,7,Bird,0.52,1,2,3,4\n"
                "2,7,Bird,0.52,1,2,3,4\n"
                "3,7,Bird,0.52,1,2,3,4\n"
                "4,7,Bird,0.52,1,2,3,4\n"
                "5,7,Drone,0.99,1,2,3,4\n",
                encoding="utf-8",
            )
            reliability_csv.write_text(
                "track_id,reliability_score,status\n7,0.9,CONFIDENT\n",
                encoding="utf-8",
            )
            consensus_csv.write_text(
                "track_id,consensus_label,decision_status,dominant_class\n"
                "7,Bird,confident,Bird\n",
                encoding="utf-8",
            )

            self.assertIsNone(
                _publish_early_uav_alert(
                    run_root, tracks_csv, reliability_csv, consensus_csv
                )
            )
            self.assertFalse((run_root / "early_alert.json").exists())

            tracks_csv.write_text(
                tracks_csv.read_text(encoding="utf-8")
                + "1,8,Drone,0.9,1,2,3,4\n"
                + "2,8,Drone,0.9,1,2,3,4\n"
                + "3,8,Drone,0.9,1,2,3,4\n",
                encoding="utf-8",
            )
            reliability_csv.write_text(
                "track_id,reliability_score,status\n"
                "7,0.9,CONFIDENT\n8,0.9,CONFIDENT\n",
                encoding="utf-8",
            )
            consensus_csv.write_text(
                "track_id,consensus_label,decision_status,dominant_class\n"
                "7,Bird,confident,Bird\n8,Drone,confident,Drone\n",
                encoding="utf-8",
            )
            alert = _publish_early_uav_alert(
                run_root, tracks_csv, reliability_csv, consensus_csv
            )
            self.assertIsNotNone(alert)
            self.assertEqual(alert["track_id"], "8")

    def wait_for_status(self, run_id, expected, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, raw, _ = self.request(f"/api/runs/{run_id}")
            self.assertEqual(status, 200)
            result = json.loads(raw)
            if result["status"] == expected:
                return result
            time.sleep(0.02)
        self.fail(f"Run {run_id} did not reach {expected}")

    def test_upload_uses_safe_generated_run_id_and_isolates_storage(self):
        status_a, first = self.upload(payload=b"first")
        status_b, second = self.upload(payload=b"second")
        self.assertEqual((status_a, status_b), (201, 201))
        self.assertRegex(first["run_id"], r"^RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}$")
        self.assertNotEqual(first["run_id"], second["run_id"])
        record_a = self.server.run_manager.get(first["run_id"])
        record_b = self.server.run_manager.get(second["run_id"])
        input_a = record_a.root / "input" / record_a.input_filename
        input_b = record_b.root / "input" / record_b.input_filename
        self.assertEqual(record_a.input_filename, "input_video.mp4")
        self.assertEqual(input_a.read_bytes(), b"first")
        self.assertEqual(input_b.read_bytes(), b"second")
        self.assertNotIn(str(self.runs_root), json.dumps(first))

    def test_upload_rejects_unsafe_type_empty_and_oversized_payloads(self):
        status, _ = self.upload(filename="../escape.mp4")
        self.assertEqual(status, 400)
        status, _ = self.upload(filename="C:\\temp\\escape.mp4")
        self.assertEqual(status, 400)
        status, _ = self.upload(filename="payload.exe")
        self.assertEqual(status, 415)
        status, _ = self.upload(payload=b"")
        self.assertEqual(status, 400)
        status, _ = self.upload(payload=b"x" * 17)
        self.assertEqual(status, 413)

    def test_upload_rejects_multiple_file_parts(self):
        boundary = "----boundary-test-extra-file"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="video"; filename="sample.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\nvideo\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="extra"; filename="extra.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\nextra\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        status, _, _ = self.request(
            "/api/runs",
            method="POST",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        self.assertEqual(status, 400)

    def test_unknown_run_and_duplicate_start(self):
        status, payload, _ = self.request(
            "/api/runs/RUN_20260101_010101_012345678901234567890123"
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["error"], "run_not_found")
        status, created = self.upload(payload=b"block")
        self.assertEqual(status, 201)
        start_status, started, _ = self.request(
            f"/api/runs/{created['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(start_status, 202)
        self.assertEqual(json.loads(started)["status"], "processing")
        self.assertTrue(self.runner_started.wait(timeout=2))
        _, queued = self.upload(payload=b"second-run")
        concurrent_status, concurrent, _ = self.request(
            f"/api/runs/{queued['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(concurrent_status, 409)
        self.assertEqual(json.loads(concurrent)["error"], "another_run_processing")
        duplicate_status, duplicate, _ = self.request(
            f"/api/runs/{created['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(duplicate_status, 409)
        self.assertEqual(json.loads(duplicate)["error"], "run_already_started")
        self.release_runner.set()
        self.wait_for_status(created["run_id"], "completed")
        self.release_runner.clear()

    def test_cancel_stops_process_and_unblocks_the_next_run(self):
        self.process_started.clear()
        status, active = self.upload(payload=b"cancel-process")
        self.assertEqual(status, 201)
        unauthenticated_status, _, _ = self.request(
            f"/api/runs/{active['run_id']}/cancel",
            method="POST",
            authenticated=False,
        )
        self.assertEqual(unauthenticated_status, 401)
        start_status, _, _ = self.request(
            f"/api/runs/{active['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(start_status, 202)
        self.assertTrue(self.process_started.wait(timeout=3))

        early_alert_path = self.runs_root / active["run_id"] / "early_alert.json"
        early_alert_path.write_text(
            json.dumps(
                {
                    "uav_alert": True,
                    "level": "HIGH",
                    "track_id": "7",
                    "message": "One or more tracks have been classified as UAV. Immediate review recommended.",
                    "published_at": "2026-10-04T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        next_record = self.server.run_manager.create_run(
            "next-video.mp4",
            b"next-video",
            source_type="upload",
            input_type="rgb",
            input_status="validated",
            prepared=True,
        )
        next_run = {"run_id": next_record.run_id}
        self.assertEqual(next_record.status, "prepared")
        blocked_start_status, blocked_start_raw, _ = self.request(
            f"/api/runs/{next_run['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(blocked_start_status, 409)
        self.assertEqual(
            json.loads(blocked_start_raw)["error"],
            "another_run_processing",
        )
        self.assertEqual(
            json.loads(self.request(f"/api/runs/{next_run['run_id']}")[1])["status"],
            "prepared",
        )
        cancel_status, cancel_raw, _ = self.request(
            f"/api/runs/{active['run_id']}/cancel", method="POST", body=b""
        )
        self.assertEqual(cancel_status, 200)
        cancelled = json.loads(cancel_raw)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["stage"], "cancelled")
        self.assertIsNotNone(self.active_process)
        self.assertIsNotNone(self.active_process.poll())
        self.assertFalse(
            (self.runs_root / active["run_id"] / "final_results.json").exists()
        )
        self.assertTrue(early_alert_path.is_file())
        early_alert_status, early_alert_raw, _ = self.request(
            f"/api/runs/{active['run_id']}/early-alert"
        )
        self.assertEqual(early_alert_status, 200)
        self.assertTrue(json.loads(early_alert_raw)["early_alert"]["uav_alert"])
        self.assertEqual(
            json.loads(self.request(f"/api/runs/{next_run['run_id']}")[1])["status"],
            "prepared",
        )

        repeat_status, repeated, _ = self.request(
            f"/api/runs/{active['run_id']}/cancel", method="POST", body=b""
        )
        self.assertEqual(repeat_status, 200)
        self.assertEqual(json.loads(repeated)["status"], "cancelled")
        next_start_status, _, _ = self.request(
            f"/api/runs/{next_run['run_id']}/start", method="POST", body=b""
        )
        self.assertEqual(next_start_status, 202)
        self.assertEqual(
            self.wait_for_status(next_run["run_id"], "completed")["status"],
            "completed",
        )

        history_status, history_raw, _ = self.request("/api/runs")
        self.assertEqual(history_status, 200)
        history = {item["run_id"]: item for item in json.loads(history_raw)["runs"]}
        self.assertEqual(history[active["run_id"]]["status"], "cancelled")
        self.assertEqual(history[next_run["run_id"]]["status"], "completed")

        completed_cancel_status, _, _ = self.request(
            f"/api/runs/{next_run['run_id']}/cancel", method="POST", body=b""
        )
        self.assertEqual(completed_cancel_status, 409)
        self.assertEqual(
            self.wait_for_status(next_run["run_id"], "completed")["status"],
            "completed",
        )

    def test_successful_worker_completion_is_persisted(self):
        manager = self.server.run_manager
        record = manager.create_run("success.mp4", b"success")
        manager.start(record.run_id)
        status = self.wait_for_status(record.run_id, "completed")
        self.assertEqual(status["stage"], "completed")
        self.assertEqual(status["progress"], 100)
        self.assertIsNotNone(status["completed_at"])

    def test_result_apis_assessment_and_evidence_are_run_scoped(self):
        status, created = self.upload(payload=b"evidence")
        self.assertEqual(status, 201)
        run_id = created["run_id"]
        self.request(f"/api/runs/{run_id}/start", method="POST", body=b"")
        self.assertEqual(self.wait_for_status(run_id, "completed")["progress"], 100)
        manifest = json.loads(
            (self.runs_root / run_id / "run_manifest.json").read_text(encoding="utf-8")
        )
        for field in (
            "detector_checkpoint_name",
            "detector_checkpoint_sha256",
            "detector_model_architecture",
            "detector_image_size",
            "detector_confidence_threshold",
            "detector_iou_threshold",
            "detector_device",
            "detector_preprocessing",
            "source_video_sha256",
            "source_video_fps",
            "source_video_width",
            "source_video_height",
        ):
            self.assertIn(field, manifest)
        self.assertEqual(
            manifest["detector_checkpoint_sha256"],
            "fd7851d7cb958ecfa21d2901c5f1ae16eeeca426a9b9688fa0f91ee372cfec5b",
        )
        self.assertNotIn(str(get_settings().project_root), json.dumps(manifest))
        for route in (
            "overview",
            "tracks",
            "fusion/summary",
            "tracks/1",
            "tracks/1/evidence",
            "tracks/1/vlm",
            "tracks/1/modalities",
            "tracks/1/fusion",
        ):
            with self.subTest(route=route):
                response_status, raw, _ = self.request(f"/api/runs/{run_id}/{route}")
                self.assertEqual(response_status, 200)
                result = json.loads(raw)
                if route == "tracks/1":
                    self.assertEqual(result["assessment"]["assessment"], "MANUAL_REVIEW")
                if route == "tracks/1/evidence":
                    self.assertEqual(
                        result[0]["crop_path"],
                        f"/api/runs/{run_id}/evidence/selected/track_1/frame_000001.jpg",
                    )
        image_status, image, content_type = self.request(
            f"/api/runs/{run_id}/evidence/selected/track_1/frame_000001.jpg"
        )
        self.assertEqual((image_status, image, content_type), (200, b"test image", "image/jpeg"))

    def test_evidence_rejects_traversal_and_cross_run_access(self):
        _, with_image = self.upload(payload=b"evidence")
        _, without_image = self.upload(payload=b"no-image")
        self.request(f"/api/runs/{with_image['run_id']}/start", method="POST", body=b"")
        self.wait_for_status(with_image["run_id"], "completed")
        self.request(f"/api/runs/{without_image['run_id']}/start", method="POST", body=b"")
        self.wait_for_status(without_image["run_id"], "completed")
        traversal_status, traversal, _ = self.request(
            f"/api/runs/{with_image['run_id']}/evidence/..%5C..%5Cinput%5Cinput_video.mp4"
        )
        self.assertEqual(traversal_status, 400)
        self.assertEqual(json.loads(traversal)["error"], "invalid_evidence_path")
        cross_run_status, _, _ = self.request(
            f"/api/runs/{without_image['run_id']}/evidence/selected/track_1/frame_000001.jpg"
        )
        self.assertEqual(cross_run_status, 404)

    def test_processing_failure_is_persisted_without_path_disclosure(self):
        def failing_runner(project_root, run_root, input_video, set_stage):
            raise ValueError(f"failure at {run_root}")

        record = self.server.run_manager.create_run("fail.mp4", b"video")
        self.server.run_manager.runner = failing_runner
        self.server.run_manager.start(record.run_id)
        status = self.wait_for_status(record.run_id, "failed")
        self.assertIn("processing failed", status["error"])
        self.assertNotIn(str(record.root), json.dumps(status))
        self.server.run_manager.runner = self.fake_runner

    def test_unexpected_worker_exit_is_persisted_as_lifecycle_failure(self):
        manager = self.server.run_manager
        record = manager.create_run("unexpected-exit.mp4", b"video")
        record.status = "processing"
        record.stage = "detection"
        record.progress = 8
        manager._persist(record)

        def exiting_runner(*_args):
            raise SystemExit(17)

        manager.runner = exiting_runner

        try:
            with patch("backend.run_pipeline.run_reproducibility_metadata", return_value={}):
                with self.assertRaises(SystemExit):
                    manager._execute(record)
        finally:
            manager.runner = self.fake_runner

        status = json.loads((record.root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["stage"], "failed")
        self.assertEqual(status["error"], (
            "Worker exited before reaching a terminal run state; "
            "underlying cause is unavailable"
        ))
        self.assertIsNotNone(status["completed_at"])

    def test_worker_exit_guard_preserves_terminal_states(self):
        manager = self.server.run_manager
        for terminal_state in ("completed", "failed", "cancelled"):
            with self.subTest(status=terminal_state):
                record = manager.create_run(f"{terminal_state}.mp4", b"video")
                record.status = terminal_state
                record.stage = terminal_state
                record.progress = 100 if terminal_state == "completed" else 42
                record.error = "existing terminal detail"
                record.completed_at = "2026-01-01T00:00:00+00:00"
                manager._persist(record)
                before = json.loads((record.root / "status.json").read_text(encoding="utf-8"))

                manager._fail_if_worker_exited(record)

                after = json.loads((record.root / "status.json").read_text(encoding="utf-8"))
                self.assertEqual(after, before)

    def test_stale_processing_run_is_failed_on_manager_initialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "dashboard_runs"
            manager = RunManager(
                project_root=get_settings().project_root,
                max_upload_bytes=16,
                runner=self.fake_runner,
                runs_root=runs_root,
            )
            record = manager.create_run("stale.mp4", b"video")
            record.status = "processing"
            record.stage = "detection"
            record.progress = 8
            manager._persist(record)

            restarted_manager = RunManager(
                project_root=get_settings().project_root,
                max_upload_bytes=16,
                runner=self.fake_runner,
                runs_root=runs_root,
            )

            recovered = restarted_manager.get(record.run_id)
            self.assertEqual(recovered.status, "failed")
            self.assertEqual(recovered.stage, "failed")
            self.assertEqual(
                recovered.error,
                "Backend restarted while processing; start a new run",
            )
            persisted = json.loads((record.root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "failed")

    def test_vlm_inference_failure_is_reported_as_failed(self):
        def failing_vlm_runner(project_root, run_root, input_video, set_stage):
            set_stage("vlm", 78)
            raise RuntimeError("synthetic VLM inference failure")

        manager = self.server.run_manager
        record = manager.create_run("vlm-fail.mp4", b"video")
        original_runner = manager.runner
        try:
            manager.runner = failing_vlm_runner
            manager.start(record.run_id)
            status = self.wait_for_status(record.run_id, "failed")
            self.assertEqual(status["vlm_status"], "failed")
            self.assertEqual(status["stage"], "failed")
        finally:
            manager.runner = original_runner

    def test_vlm_stage_status_requires_explicit_result_rows(self):
        manager = self.server.run_manager
        skipped = manager.create_run("skipped.mp4", b"skipped")
        skipped_vlm = skipped.root / "vlm"
        skipped_vlm.mkdir()
        (skipped_vlm / "vlm_results.csv").write_text(
            "track_id,frame,crop_path,label,confidence,reason\n",
            encoding="utf-8",
        )
        manifest_before = (skipped.root / "run_manifest.json").read_bytes()
        manager._set_stage(skipped, "vlm", 76)
        self.assertEqual(skipped.as_json()["vlm_status"], "processing")
        manager._set_stage(skipped, "vlm_skipped", 78)
        self.assertEqual(skipped.as_json()["vlm_status"], "skipped")
        self.assertEqual(
            (skipped.root / "run_manifest.json").read_bytes(), manifest_before
        )

        empty = manager.create_run("empty.mp4", b"empty")
        (empty.root / "vlm").mkdir()
        (empty.root / "vlm" / "vlm_results.csv").write_text(
            "track_id,frame,crop_path,label,confidence,reason\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "without valid result rows"):
            manager._set_stage(empty, "vlm_completed", 80)

        completed = manager.create_run("completed.mp4", b"completed")
        vlm_root = completed.root / "vlm"
        vlm_root.mkdir()
        with (vlm_root / "vlm_results.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=("track_id", "frame", "crop_path", "label", "confidence", "reason"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "track_id": "1",
                    "frame": 1,
                    "crop_path": "selected/track_1/frame_000001.jpg",
                    "label": "Drone",
                    "confidence": 0.8,
                    "reason": "test inference output",
                }
            )
        manager._set_stage(completed, "vlm", 76)
        with self.assertRaisesRegex(ValueError, "cannot be skipped"):
            manager._set_stage(completed, "vlm_skipped", 78)
        manager._set_stage(completed, "vlm_completed", 80)
        self.assertEqual(completed.as_json()["vlm_status"], "completed")

    def test_upload_does_not_modify_historical_results(self):
        self.upload(payload=b"preserve")
        self.assert_historical_hashes()

    def test_zero_track_output_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            empty_tracks = Path(temporary) / "tracks.csv"
            empty_tracks.write_text("track_id,frame\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No detections"):
                _require_track_observations(empty_tracks)
            with self.assertRaisesRegex(FileNotFoundError, "tracks.csv"):
                _require_track_observations(Path(temporary) / "missing.csv")


if __name__ == "__main__":
    unittest.main()
