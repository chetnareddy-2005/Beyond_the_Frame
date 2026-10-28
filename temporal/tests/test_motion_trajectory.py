import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from temporal.motion_trajectory import (
    MotionConfig,
    MOTION_OBSERVATION_FIELDS,
    TRACK_SUMMARY_FIELDS,
    compute_track_trajectory,
    process_tracks_csv,
)


def row(track_id, frame, box, *, confidence=0.8, label="Drone", timestamp=None):
    result = {
        "source": "local-video.mp4",
        "frame": str(frame),
        "track_id": str(track_id),
        "class_id": "1",
        "class_name": label,
        "confidence": str(confidence),
        "x1": str(box[0]),
        "y1": str(box[1]),
        "x2": str(box[2]),
        "y2": str(box[3]),
    }
    if timestamp is not None:
        result["timestamp_sec"] = str(timestamp)
    return result


class MotionTrajectoryTests(unittest.TestCase):
    def write_tracks(self, path, rows):
        fields = list(rows[0])
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_geometry_raw_and_smoothed_image_plane_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            tracks = Path(directory) / "tracks.csv"
            self.write_tracks(
                tracks,
                [
                    row(24, 1, (0, 0, 10, 20)),
                    row(24, 2, (3, 4, 13, 24)),
                    row(24, 3, (6, 8, 16, 28)),
                ],
            )
            motion_rows, summaries, payload = process_tracks_csv(
                tracks, fps=30, frame_width=640, frame_height=480
            )
            first, second, third = motion_rows
            self.assertEqual((first["center_x"], first["center_y"]), (5.0, 10.0))
            self.assertEqual((first["width"], first["height"], first["area"]), (10.0, 20.0, 200.0))
            self.assertEqual(first["aspect_ratio"], 0.5)
            self.assertIsNone(first["heading_deg"])
            self.assertEqual(first["motion_state"], "INSUFFICIENT_DATA")
            self.assertAlmostEqual(second["dx"], 3.0)
            self.assertAlmostEqual(second["dy"], 4.0)
            self.assertAlmostEqual(second["displacement"], 5.0)
            self.assertAlmostEqual(second["raw_speed_px_per_frame"], 5.0)
            self.assertAlmostEqual(second["raw_speed_px_per_second"], 150.0)
            self.assertAlmostEqual(second["heading_deg"], 53.130102)
            self.assertAlmostEqual(second["smoothed_center_x"], 6.05)
            self.assertAlmostEqual(second["smoothed_center_y"], 11.4)
            self.assertEqual(second["timestamp_sec"], 0.033333)
            self.assertEqual(second["timestamp_source"], "video_fps")
            self.assertEqual(summaries[0]["observations"], 3)
            self.assertEqual(summaries[0]["missing_frame_count"], 0)
            self.assertEqual(summaries[0]["longest_consecutive_run"], 3)
            self.assertEqual(payload["coordinate_system"], "image_plane_pixels")
            self.assertEqual(payload["physical_world_calibration"], "not_available")

    def test_duplicate_frame_keeps_highest_confidence_and_gaps_suppress_acceleration(self):
        with tempfile.TemporaryDirectory() as directory:
            tracks = Path(directory) / "tracks.csv"
            self.write_tracks(
                tracks,
                [
                    row(3, 1, (0, 0, 4, 4), confidence=0.5),
                    row(3, 1, (10, 10, 14, 14), confidence=0.9),
                    row(3, 2, (13, 14, 17, 18)),
                    row(3, 5, (25, 26, 29, 30)),
                ],
            )
            motion_rows, summaries, _ = process_tracks_csv(tracks, fps=25)
            self.assertEqual(len(motion_rows), 3)
            self.assertEqual(motion_rows[0]["center_x"], 12.0)
            self.assertEqual(motion_rows[1]["frame_gap_before"], 0)
            self.assertEqual(motion_rows[2]["frame_gap_before"], 2)
            self.assertIsNone(motion_rows[2]["acceleration_px_per_second2"])
            self.assertEqual(summaries[0]["missing_frame_count"], 2)
            self.assertEqual(summaries[0]["motion_quality"], "GAPPED")

    def test_invalid_fps_and_nonmonotonic_timestamps_are_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            tracks = Path(directory) / "tracks.csv"
            self.write_tracks(
                tracks,
                [
                    row(8, 1, (0, 0, 2, 2), timestamp=1.0),
                    row(8, 2, (2, 0, 4, 2), timestamp=0.5),
                ],
            )
            motion_rows, summaries, payload = process_tracks_csv(
                tracks, fps=0, frame_width=float("nan")
            )
            self.assertIsNone(motion_rows[0]["timestamp_sec"])
            self.assertIsNone(motion_rows[1]["timestamp_sec"])
            self.assertEqual(motion_rows[0]["timestamp_source"], "unavailable")
            self.assertIsNone(summaries[0]["duration_sec"])
            self.assertIsNone(payload["fps"])
            self.assertIsNone(payload["frame_width"])

    def test_short_track_zero_box_and_no_nan_json(self):
        observation = {
            "track_id": "9",
            "frame": 4,
            "class": "Bird",
            "confidence": 0.5,
            "x1": 2.0,
            "y1": 2.0,
            "x2": 2.0,
            "y2": 2.0,
            "timestamp_sec": None,
            "timestamp_source": "unavailable",
            "frame_width": None,
            "frame_height": None,
        }
        rows, summary, trajectory = compute_track_trajectory([observation], fps=None)
        self.assertEqual(rows[0]["width"], 0.0)
        self.assertEqual(rows[0]["height"], 0.0)
        self.assertIsNone(rows[0]["aspect_ratio"])
        self.assertEqual(rows[0]["motion_state"], "INSUFFICIENT_DATA")
        self.assertEqual(summary["dominant_motion_state"], "INSUFFICIENT_DATA")
        self.assertIsNone(summary["straightness_ratio"])
        self.assertEqual(trajectory["coordinate_system"], "image_plane_pixels")
        encoded = json.dumps(trajectory, allow_nan=False)
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)

    def test_motion_configuration_validation_and_output_schemas(self):
        with self.assertRaises(ValueError):
            MotionConfig(smoothing_alpha=0)
        with tempfile.TemporaryDirectory() as directory:
            tracks = Path(directory) / "tracks.csv"
            self.write_tracks(tracks, [row(5, 1, (0, 0, 1, 1))])
            process_tracks_csv(tracks, fps=30)
            with (Path(directory) / "motion_observations.csv").open(newline="", encoding="utf-8") as source:
                observation_rows = list(csv.DictReader(source))
            with (Path(directory) / "motion_track_summary.csv").open(newline="", encoding="utf-8") as source:
                summary_rows = list(csv.DictReader(source))
            with (Path(directory) / "trajectories.json").open(encoding="utf-8") as source:
                json_payload = json.load(source, parse_constant=lambda value: self.fail(value))
            self.assertEqual(tuple(observation_rows[0]), MOTION_OBSERVATION_FIELDS)
            self.assertEqual(tuple(summary_rows[0]), TRACK_SUMMARY_FIELDS)
            self.assertIn("5", json_payload["tracks"])
            self.assertNotIn("local-video.mp4", json.dumps(json_payload))

    def test_rule_based_motion_states_are_transparent_and_image_plane_only(self):
        config = MotionConfig(smoothing_alpha=1.0)

        def trajectory(centers):
            observations = []
            for frame, (center_x, center_y) in enumerate(centers, start=1):
                observations.append(
                    {
                        "track_id": "1",
                        "frame": frame,
                        "class": "Drone",
                        "confidence": 0.8,
                        "x1": center_x - 1,
                        "y1": center_y - 1,
                        "x2": center_x + 1,
                        "y2": center_y + 1,
                        "timestamp_sec": (frame - 1) / 30,
                        "timestamp_source": "video_fps",
                        "frame_width": 640,
                        "frame_height": 480,
                    }
                )
            return compute_track_trajectory(
                observations, fps=30, config=config
            )[1]["dominant_motion_state"]

        self.assertEqual(trajectory([(0, 0)] * 4), "STATIONARY")
        self.assertEqual(trajectory([(index, 0) for index in range(4)]), "SLOW_MOVEMENT")
        self.assertEqual(trajectory([(index * 5, 0) for index in range(4)]), "STEADY_MOVEMENT")
        self.assertEqual(trajectory([(index * 25, 0) for index in range(4)]), "HIGH_MOTION")
        self.assertEqual(trajectory([(0, 0), (5, 0), (5, 5)]), "TURNING")
        self.assertEqual(trajectory([(0, 0), (5, 0), (0, 0)]), "IRREGULAR_MOTION")


if __name__ == "__main__":
    unittest.main()
