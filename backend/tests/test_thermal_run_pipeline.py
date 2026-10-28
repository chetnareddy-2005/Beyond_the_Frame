from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from backend.app import ThermalRunData
from backend.run_pipeline import (
    _run_thermal_detector_and_tracker,
    load_run_project_data,
    run_video_pipeline,
)
from backend.runs import RunCancelled, RunManager


class FakeCapture:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.opened = True
        self.released = False
        self.position_ms = 0

    def isOpened(self):
        return self.opened

    def read(self):
        try:
            frame, self.position_ms = next(self.frames)
        except StopIteration:
            return False, None
        return True, frame

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return self.position_ms
        return 0

    def release(self):
        self.released = True


class FakeDetector:
    def __init__(self):
        self.frames = []
        self.closed = False

    def detect(self, frame):
        self.frames.append(frame)
        return {"frame_index": frame.frame_index}, None

    def close(self):
        self.closed = True


class FakeTracker:
    def __init__(self):
        self.reset_owners = []

    def track(self, owner, detection_result):
        class_id = 1
        return SimpleNamespace(
            tracks=(
                SimpleNamespace(
                    track_id=7,
                    timestamp_sec=detection_result["frame_index"] / 25,
                    class_id=class_id,
                    class_name="Drone",
                    confidence=0.8,
                    x1=1.0,
                    y1=2.0,
                    x2=5.0,
                    y2=6.0,
                ),
            )
        )

    def reset_owner(self, owner):
        self.reset_owners.append(owner)


class ThermalRunPipelineTests(unittest.TestCase):
    def test_prepared_thermal_run_uses_existing_run_manager_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def runner(_project_root, _run_root, _input_video, set_stage):
                for phase in ("detection", "tracking"):
                    set_stage.update_phase_progress(phase, "completed")
                set_stage.update_phase_progress(
                    "temporal",
                    "completed",
                    current=1,
                    total=1,
                    message="1 / 1 tracks evaluated",
                )
                for phase in (
                    "reliability",
                    "evidence",
                    "vlm",
                    "fusion",
                    "assessment",
                ):
                    set_stage.update_phase_progress(
                        phase,
                        "skipped",
                        message=f"{phase} not run for thermal",
                    )
                return {"analysis_mode": "thermal_detection_tracking_only"}

            manager = RunManager(root, 1024, runner, runs_root=root / "runs")
            record = manager.create_run(
                "thermal.mp4",
                b"thermal-video",
                source_type="upload",
                input_type="thermal",
                input_status="validated",
                input_metadata={"frame_count": 2},
                prepared=True,
            )
            with patch(
                "backend.run_pipeline.run_reproducibility_metadata",
                return_value={"detector_preprocessing": "thermal adapter"},
            ) as reproducibility:
                started = manager.start(record.run_id)
                started.thread.join(timeout=5)

            self.assertFalse(started.thread.is_alive())
            self.assertEqual(started.status, "completed")
            self.assertEqual(started.vlm_status, "skipped")
            self.assertEqual(started.project_data["analysis_mode"], "thermal_detection_tracking_only")
            self.assertEqual(
                started.phase_progress["assessment"]["status"],
                "skipped",
            )
            self.assertEqual(
                started.phase_progress["temporal"]["status"],
                "completed",
            )
            self.assertEqual(reproducibility.call_args.kwargs["input_type"], "thermal")

    def test_sequential_run_normalizes_frames_tracks_and_persists_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_video = root / "thermal.mp4"
            input_video.write_bytes(b"video")
            run_root = root / "run"
            run_root.mkdir()
            record = SimpleNamespace(
                run_id="RUN_20261015_120000_" + "a" * 24,
                input_metadata={"frame_count": 2, "fps": 25.0},
            )
            stage_updates = []
            progress_updates = []

            def set_stage(stage, progress):
                stage_updates.append((stage, progress))

            set_stage.record = record
            detector = FakeDetector()
            tracker = FakeTracker()
            capture = FakeCapture(
                [
                    (np.full((3, 4, 3), 30, dtype=np.uint8), 0),
                    (np.full((3, 4, 3), 60, dtype=np.uint8), 40),
                ]
            )

            with (
                patch(
                    "backend.run_pipeline.resolve_detector_checkpoint",
                    return_value=(Path("thermal_bird_drone_best.pt"), "a" * 64),
                ),
                patch("backend.run_pipeline.resolve_device", return_value="cpu"),
                patch("backend.detection.DetectionService", return_value=detector),
                patch("backend.tracking.TrackingService", return_value=tracker),
                patch("cv2.VideoCapture", return_value=capture),
                patch(
                    "multimodal.thermal_adapter.ThermalAdapter.preprocess_frame",
                    side_effect=[
                        np.full((3, 4), 10, dtype=np.uint8),
                        np.full((3, 4), 20, dtype=np.uint8),
                    ],
                ) as preprocess,
            ):
                tracks_path = _run_thermal_detector_and_tracker(
                    root,
                    run_root,
                    input_video,
                    set_stage,
                    lambda *args, **kwargs: progress_updates.append((args, kwargs)),
                    lambda: None,
                )

            self.assertTrue(capture.released)
            self.assertTrue(detector.closed)
            self.assertEqual(tracker.reset_owners, [f"thermal-run:{record.run_id}"])
            self.assertEqual(preprocess.call_count, 2)
            self.assertEqual(
                [frame.frame_index for frame in detector.frames],
                [0, 1],
            )
            self.assertTrue(all(frame.input_type == "thermal" for frame in detector.frames))
            self.assertTrue(all(frame.frame.ndim == 2 for frame in detector.frames))
            self.assertIn(("detection", 8), stage_updates)
            self.assertEqual(stage_updates[-1], ("tracking", 25))

            with tracks_path.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                [(row["frame"], row["source_frame_index"]) for row in rows],
                [("1", "0"), ("2", "1")],
            )
            self.assertEqual(
                [(row["class_id"], row["class_name"]) for row in rows],
                [("1", "Drone"), ("1", "Drone")],
            )
            self.assertEqual(
                [row["timestamp_sec"] for row in rows],
                ["0.0", "0.04"],
            )
            manifest = (run_root / "detector_manifest.json").read_text(encoding="utf-8")
            self.assertIn('"0": "Bird"', manifest)
            self.assertIn('"1": "Drone"', manifest)
            self.assertEqual(
                [
                    args[1]
                    for args, _ in progress_updates
                    if len(args) > 1 and args[1] == "completed"
                ],
                ["completed", "completed"],
            )

    def test_thermal_pipeline_persists_and_exposes_temporal_consensus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            tracks_path = run_root / "tracking" / "detector" / "tracks.csv"
            tracks_path.parent.mkdir(parents=True)
            (run_root / "run_manifest.json").write_text(
                '{"input_type":"thermal","run_id":"RUN_THERMAL_CONSENSUS"}',
                encoding="utf-8",
            )
            tracks_path.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
                "thermal.mp4,1,0,0.0,7,0,Bird,0.8,1,2,5,6\n"
                "thermal.mp4,2,1,0.04,7,0,Bird,0.8,1,2,5,6\n"
                "thermal.mp4,3,2,0.08,7,0,Bird,0.8,1,2,5,6\n",
                encoding="utf-8",
            )
            phase_updates = []
            stage_updates = []

            def set_stage(stage, progress):
                stage_updates.append((stage, progress))

            set_stage.record = SimpleNamespace(input_type="thermal")
            set_stage.update_phase_progress = lambda *args, **kwargs: phase_updates.append(
                (args, kwargs)
            )
            set_stage.check_cancelled = lambda: None

            with patch(
                "backend.run_pipeline._run_thermal_detector_and_tracker",
                return_value=tracks_path,
            ):
                data = run_video_pipeline(root, run_root, root / "thermal.mp4", set_stage)

            consensus_path = run_root / "temporal" / "temporal_consensus.csv"
            self.assertTrue(consensus_path.is_file())
            self.assertTrue(
                (run_root / "temporal" / "temporal_consensus_manifest.json").is_file()
            )
            self.assertIn(("temporal", 34), stage_updates)
            self.assertTrue(
                any(
                    args[0] == "temporal" and args[1] == "completed"
                    for args, _ in phase_updates
                )
            )
            detail = data.track_detail("7")
            ledger_consensus = detail["evidence_ledger"]["temporal_consensus"]
            self.assertEqual(ledger_consensus["status"], "completed")
            self.assertEqual(
                ledger_consensus["source"],
                "persisted_temporal_consensus",
            )
            self.assertEqual(
                ledger_consensus["result"]["consensus_label"],
                "Bird",
            )
            final_results = json.loads(
                (run_root / "final_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                final_results["tracks"][0]["temporal_consensus"]["consensus_label"],
                "Bird",
            )
            self.assertNotIn("temporal", final_results["unsupported_stages"])
            self.assertIn("reliability", final_results["unsupported_stages"])
            self.assertEqual(final_results["alert_status"], "detector_only")
            self.assertFalse(final_results["uav_alert"])
            self.assertEqual(final_results["alert_track_ids"], [])
            self.assertNotIn("alerting", final_results["unsupported_stages"])

    def test_thermal_drone_track_produces_detector_only_review_alert(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            tracks_path = run_root / "tracking" / "detector" / "tracks.csv"
            tracks_path.parent.mkdir(parents=True)
            (run_root / "run_manifest.json").write_text(
                '{"input_type":"thermal","run_id":"RUN_THERMAL_DRONE_ALERT"}',
                encoding="utf-8",
            )
            tracks_path.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
                "thermal.mp4,1,0,0.0,12,1,Drone,0.82,10,20,30,40\n"
                "thermal.mp4,2,1,0.04,12,1,Drone,0.84,11,20,31,40\n",
                encoding="utf-8",
            )
            set_stage = lambda *_args: None
            set_stage.record = SimpleNamespace(input_type="thermal")
            set_stage.update_phase_progress = lambda *_args, **_kwargs: None
            set_stage.check_cancelled = lambda: None

            with patch(
                "backend.run_pipeline._run_thermal_detector_and_tracker",
                return_value=tracks_path,
            ):
                run_video_pipeline(root, run_root, root / "thermal.mp4", set_stage)

            report = json.loads(
                (run_root / "final_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["alert_status"], "detector_only")
            self.assertTrue(report["uav_alert"])
            self.assertEqual(report["alert_track_ids"], ["12"])
            self.assertEqual(report["alert_level"], "REVIEW")
            self.assertIn("unverified detector output", report["alert_message"])

    def test_thermal_pipeline_completes_empty_detection_run_without_fabricating_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            tracks_path = run_root / "tracking" / "detector" / "tracks.csv"
            tracks_path.parent.mkdir(parents=True)
            (run_root / "run_manifest.json").write_text(
                '{"input_type":"thermal","run_id":"RUN_THERMAL_EMPTY"}',
                encoding="utf-8",
            )
            tracks_path.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n",
                encoding="utf-8",
            )
            set_stage = lambda *_args: None
            set_stage.record = SimpleNamespace(input_type="thermal")
            set_stage.update_phase_progress = lambda *_args, **_kwargs: None
            set_stage.check_cancelled = lambda: None

            with patch(
                "backend.run_pipeline._run_thermal_detector_and_tracker",
                return_value=tracks_path,
            ):
                data = run_video_pipeline(root, run_root, root / "thermal.mp4", set_stage)

            data.run_status = "completed"
            self.assertEqual(data.track_list(), [])
            self.assertEqual(data.overview()["evidence_ledger"]["status"], "empty")
            self.assertEqual(
                (run_root / "temporal" / "temporal_consensus.csv").read_text(
                    encoding="utf-8"
                ).strip(),
                ",".join(
                    (
                        "track_id",
                        "consensus_label",
                        "decision_status",
                        "dominant_class",
                        "dominant_class_ratio",
                        "consensus_confidence",
                        "observation_count",
                        "class_switch_count",
                        "gap_count",
                        "max_gap",
                        "reasons",
                        "class_counts",
                        "mean_confidences",
                        "supporting_frame_indexes",
                        "contradictory_frame_indexes",
                        "unclassified_frame_indexes",
                        "score_interpretation",
                    )
                ),
            )
            result = json.loads(
                (run_root / "final_results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["track_count"], 0)
            self.assertEqual(result["tracks"], [])

    def test_thermal_read_model_exposes_detection_tracking_and_unsupported_stages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            tracking_root = run_root / "tracking" / "detector"
            tracking_root.mkdir(parents=True)
            manifest = run_root / "run_manifest.json"
            manifest.write_text('{"input_type":"thermal"}', encoding="utf-8")
            tracks = tracking_root / "tracks.csv"
            tracks.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
                "thermal.mp4,1,0,0.0,7,1,Drone,0.8,1,2,5,6\n",
                encoding="utf-8",
            )

            data = load_run_project_data(root, run_root)

            self.assertIsInstance(data, ThermalRunData)
            self.assertEqual(data.overview()["analysis_mode"], "thermal_detection_tracking_only")
            self.assertEqual(data.track_list()[0]["class"], "Drone")
            self.assertNotIn("temporal", data.overview()["unsupported_stages"])
            self.assertIsNone(data.track_detail("7")["fusion"]["final_label"])
            self.assertEqual(data.fusion_summary()["status"], "not_run")

    def test_thermal_evidence_ledger_uses_persisted_observations_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "run"
            tracking_root = run_root / "tracking" / "detector"
            tracking_root.mkdir(parents=True)
            (run_root / "run_manifest.json").write_text(
                '{"input_type":"thermal","run_id":"RUN_LEDGER"}',
                encoding="utf-8",
            )
            (run_root / "detector_manifest.json").write_text(
                (
                    '{"detector_checkpoint_name":"thermal.pt",'
                    '"detector_checkpoint_sha256":"'
                    + "a" * 64
                    + '","detector_model_architecture":"YOLOv5+GhostNet+AFNO2D",'
                    '"detector_preprocessing":"per-frame grayscale normalization"}'
                ),
                encoding="utf-8",
            )
            tracks = tracking_root / "tracks.csv"
            tracks.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
                "thermal.mp4,1,0,0.0,7,0,Bird,0.7,1,2,5,6\n"
                "thermal.mp4,4,3,0.12,7,1,Drone,0.6,2,3,6,7\n"
                "thermal.mp4,4,3,0.12,8,1,Drone,0.5,8,9,12,13\n",
                encoding="utf-8",
            )

            data = load_run_project_data(root, run_root)
            data.run_status = "completed"
            first_track = data.track_detail("7")
            ledger = first_track["evidence_ledger"]

            self.assertEqual(ledger["schema_version"], 1)
            self.assertEqual(ledger["status"], "available")
            self.assertEqual(ledger["run_id"], "RUN_LEDGER")
            self.assertEqual(ledger["track_id"], "7")
            self.assertEqual(ledger["observation_source"], "detector_observations")
            self.assertEqual(ledger["observation_count"], 2)
            self.assertEqual(ledger["class_mapping"], {"0": "Bird", "1": "Drone"})
            self.assertEqual(
                [row["class_id"] for row in first_track["detector_observations"]],
                [0, 1],
            )
            self.assertEqual(
                ledger["class_changes"],
                [
                    {
                        "observation_index": 1,
                        "frame": 4,
                        "source_frame_index": 3,
                        "previous_class_id": 0,
                        "previous_class": "Bird",
                        "predicted_class_id": 1,
                        "predicted_class": "Drone",
                    }
                ],
            )
            self.assertEqual(
                ledger["observation_gaps"],
                [
                    {
                        "after_observation_index": 0,
                        "before_observation_index": 1,
                        "first_missing_source_frame_index": 1,
                        "last_missing_source_frame_index": 2,
                        "missing_source_frame_count": 2,
                    }
                ],
            )
            self.assertEqual(
                ledger["model_provenance"]["checkpoint_sha256"],
                "a" * 64,
            )
            self.assertEqual(
                data.track_detail("8")["evidence_ledger"]["observation_count"],
                1,
            )

    def test_thermal_evidence_ledger_marks_empty_and_failed_run_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracks = root / "tracks.csv"
            header = (
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,"
                "class_name,confidence,x1,y1,x2,y2\n"
            )
            tracks.write_text(header, encoding="utf-8")

            empty_data = ThermalRunData(tracks)
            empty_data.run_status = "completed"
            self.assertEqual(empty_data.track_list(), [])
            self.assertEqual(
                empty_data.overview()["evidence_ledger"]["status"],
                "empty",
            )

            tracks.write_text(
                header + "thermal.mp4,1,0,0.0,7,1,Drone,0.8,1,2,5,6\n",
                encoding="utf-8",
            )
            failed_data = ThermalRunData(
                tracks,
                run_manifest={"run_id": "RUN_FAILED"},
            )
            failed_data.run_status = "failed"
            failed_ledger = failed_data.track_detail("7")["evidence_ledger"]
            self.assertEqual(failed_ledger["status"], "partial")
            self.assertEqual(failed_ledger["run_id"], "RUN_FAILED")
            self.assertFalse(failed_ledger["frame_review"]["ground_truth_available"])
            self.assertEqual(failed_ledger["frame_review"]["supporting_frame_indexes"], [])
            self.assertEqual(failed_ledger["temporal_consensus"]["status"], "not_run")
            self.assertEqual(failed_ledger["selective_verification"]["status"], "not_run")

    def test_thermal_read_model_rejects_wrong_class_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            tracks = Path(temporary) / "tracks.csv"
            tracks.write_text(
                "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
                "thermal.mp4,1,0,0.0,7,0,Drone,0.8,1,2,5,6\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Bird=0 and Drone=1"):
                ThermalRunData(tracks)

    def test_cancellation_and_premature_eof_release_thermal_resources(self):
        for failure in ("cancelled", "premature_eof"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_video = root / "thermal.mp4"
                input_video.write_bytes(b"video")
                run_root = root / "run"
                run_root.mkdir()
                record = SimpleNamespace(
                    run_id="RUN_20261015_120000_" + "b" * 24,
                    input_metadata={"frame_count": 2, "fps": 25.0},
                )
                capture = FakeCapture(
                    [(np.full((3, 4, 3), 30, dtype=np.uint8), 40)]
                )
                detector = FakeDetector()
                tracker = FakeTracker()
                checks = 0

                def check_cancelled():
                    nonlocal checks
                    checks += 1
                    if failure == "cancelled" and checks == 2:
                        raise RunCancelled("test cancellation")

                def set_stage(_stage, _progress):
                    pass

                set_stage.record = record
                with (
                    patch(
                        "backend.run_pipeline.resolve_detector_checkpoint",
                        return_value=(Path("thermal_bird_drone_best.pt"), "b" * 64),
                    ),
                    patch("backend.run_pipeline.resolve_device", return_value="cpu"),
                    patch("backend.detection.DetectionService", return_value=detector),
                    patch("backend.tracking.TrackingService", return_value=tracker),
                    patch("cv2.VideoCapture", return_value=capture),
                    patch(
                        "multimodal.thermal_adapter.ThermalAdapter.preprocess_frame",
                        side_effect=lambda frame: np.zeros(frame.shape[:2], dtype=np.uint8),
                    ),
                ):
                    expected_error = RunCancelled if failure == "cancelled" else RuntimeError
                    with self.assertRaises(expected_error):
                        _run_thermal_detector_and_tracker(
                            root,
                            run_root,
                            input_video,
                            set_stage,
                            lambda *_args, **_kwargs: None,
                            check_cancelled,
                        )

                self.assertTrue(capture.released)
                self.assertTrue(detector.closed)
                self.assertEqual(tracker.reset_owners, [f"thermal-run:{record.run_id}"])


if __name__ == "__main__":
    unittest.main()
