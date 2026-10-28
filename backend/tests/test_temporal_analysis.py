from __future__ import annotations

import unittest

from backend.runs import RunError
from backend.temporal_analysis import TemporalAnalysisService


def track_result(
    *,
    source_type: str = "upload",
    run_id: str | None = "run-a",
    live_reference_id: str | None = None,
    frame_index: int | None = 0,
    sequence_index: int = 0,
    tracks: list[dict] | None = None,
) -> dict:
    return {
        "source_type": source_type,
        "input_type": "rgb" if source_type == "upload" else None,
        "run_id": run_id if source_type == "upload" else None,
        "live_reference_id": live_reference_id if source_type == "cctv" else None,
        "frame_index": frame_index,
        "sequence_index": sequence_index,
        "tracks": tracks if tracks is not None else [
            {
                "track_id": 7,
                "class_id": 1,
                "class_name": "drone",
                "confidence": 0.9,
                "x1": 10 + sequence_index,
                "y1": 10,
                "x2": 30 + sequence_index,
                "y2": 30,
                "timestamp_sec": None,
            }
        ],
    }


class TemporalAnalysisServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = TemporalAnalysisService()

    def test_rgb_history_reuses_temporal_and_motion_algorithms(self):
        first = self.service.update("session-a", track_result(frame_index=0), fps=10)
        second = self.service.update("session-a", track_result(frame_index=1, sequence_index=1), fps=10)

        self.assertEqual(first["status"], "ACCUMULATING")
        self.assertEqual(second["frames_processed"], 2)
        self.assertEqual(second["timing_basis"], "video_fps")
        track = second["tracks"][0]
        self.assertEqual(track["temporal_summary"]["frames_seen"], 2)
        self.assertTrue(track["class_consistent"])
        self.assertEqual(track["motion_summary"]["observations"], 2)
        self.assertIsNotNone(track["trajectory"]["current_speed_px_per_second"])
        self.assertEqual(second["physical_world_calibration"], "not_available")

    def test_live_cctv_uses_observation_sequence_without_claiming_time(self):
        first = track_result(
            source_type="cctv",
            run_id=None,
            live_reference_id="live-a",
            frame_index=None,
            sequence_index=0,
        )
        second = track_result(
            source_type="cctv",
            run_id=None,
            live_reference_id="live-a",
            frame_index=None,
            sequence_index=1,
        )
        first["tracks"][0]["timestamp_sec"] = 1.0
        second["tracks"][0]["timestamp_sec"] = 2.0
        self.service.update("session-a", first)
        result = self.service.update("session-a", second, fps=30)

        self.assertEqual(result["timing_basis"], "unavailable")
        self.assertIsNone(result["current_frame_index"])
        self.assertEqual(result["current_sequence_index"], 1)
        self.assertIsNone(result["tracks"][0]["trajectory"]["current_speed_px_per_second"])

    def test_empty_frame_advances_history_and_finalization_returns_completed_state(self):
        self.service.update("session-a", track_result(frame_index=0))
        empty = track_result(frame_index=1, sequence_index=1, tracks=[])
        current = self.service.update("session-a", empty)
        completed = self.service.update(
            "session-a",
            {**empty, "end_of_video": True},
            end_of_video=True,
        )

        self.assertEqual(current["frames_processed"], 2)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["tracks"][0]["temporal_summary"]["frames_seen"], 1)
        self.assertEqual(self.service._sources, {})

    def test_duplicate_or_out_of_order_source_frame_is_rejected(self):
        self.service.update("session-a", track_result(frame_index=1))

        with self.assertRaisesRegex(RunError, "new TrackResult"):
            self.service.update("session-a", track_result(frame_index=1))

    def test_malformed_geometry_and_unsupported_modalities_fail_closed(self):
        malformed = track_result()
        malformed["tracks"][0]["x2"] = float("nan")
        with self.assertRaisesRegex(RunError, "finite"):
            self.service.update("session-a", malformed)

        unsupported = track_result()
        unsupported["input_type"] = "thermal"
        with self.assertRaisesRegex(RunError, "supports RGB"):
            self.service.update("session-a", unsupported)

    def test_source_switch_and_reset_clear_session_temporal_state(self):
        self.service.update("session-a", track_result(frame_index=0))
        other_source = track_result(run_id="run-b", frame_index=0)
        self.service.update("session-a", other_source)

        self.assertEqual(len(self.service._sources), 1)
        self.assertEqual(next(iter(self.service._sources))[2], "run-b")
        self.service.reset_owner("session-a")
        self.assertEqual(self.service._sources, {})

    def test_temporal_state_is_isolated_by_authenticated_owner(self):
        result = track_result(frame_index=0)
        self.service.update("session-a", result)
        other_session = self.service.update("session-b", result)

        self.assertEqual(other_session["frames_processed"], 1)
        self.assertEqual(len(self.service._sources), 2)

    def test_phase7_reliability_integration_and_contract(self):
        first = self.service.update("session-a", track_result(frame_index=0), fps=10)
        second = self.service.update("session-a", track_result(frame_index=1, sequence_index=1), fps=10)

        track = second["tracks"][0]
        self.assertIn("reliability", track)
        reliability = track["reliability"]

        expected_keys = (
            "track_id",
            "confidence_score",
            "consistency_score",
            "persistence_score",
            "motion_score",
            "reliability_score",
            "status",
            "frames_seen",
            "mean_confidence",
            "persistence",
        )
        for key in expected_keys:
            self.assertIn(key, reliability)

        self.assertGreaterEqual(reliability["reliability_score"], 0.0)
        self.assertLessEqual(reliability["reliability_score"], 1.0)
        expected_status = "CONFIDENT" if reliability["reliability_score"] >= 0.6 else "UNCERTAIN"
        self.assertEqual(reliability["status"], expected_status)

        # Confirm Phase 6 fields remain untouched
        self.assertIn("temporal_summary", track)
        self.assertIn("motion_summary", track)
        self.assertIn("trajectory", track)
        self.assertEqual(track["temporal_summary"]["frames_seen"], 2)


if __name__ == "__main__":
    unittest.main()
