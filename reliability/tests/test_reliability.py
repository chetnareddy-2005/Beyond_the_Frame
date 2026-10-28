import csv
import tempfile
import unittest
from pathlib import Path

from reliability.reliability_score import (
    DEFAULT_THRESHOLD,
    DEFAULT_WEIGHTS,
    MOTION_DIAGNOSTIC_FIELDS,
    OUTPUT_FIELDS,
    estimate_reliability,
    process_temporal_csv,
)


def temporal_row(track_id, **overrides):
    row = {
        "track_id": track_id,
        "frames_seen": 20,
        "start_frame": 1,
        "end_frame": 20,
        "persistence": 1.0,
        "mean_confidence": 0.9,
        "confidence_std": 0.02,
        "first_center_x": 10,
        "first_center_y": 10,
        "last_center_x": 12,
        "last_center_y": 11,
        "total_displacement": 2.24,
        "mean_displacement": 0.12,
        "mean_width": 20,
        "mean_height": 10,
        "width_variation": 0,
        "height_variation": 0,
    }
    row.update(overrides)
    return row


class ReliabilityTests(unittest.TestCase):
    def test_stable_persistent_track_scores_higher_than_short_low_confidence(self):
        stable = estimate_reliability(temporal_row("stable"))
        weak = estimate_reliability(
            temporal_row(
                "weak",
                frames_seen=2,
                end_frame=2,
                persistence=1.0,
                mean_confidence=0.2,
                confidence_std=0.05,
                mean_displacement=5.0,
            )
        )

        self.assertGreater(stable["reliability_score"], weak["reliability_score"])
        self.assertEqual(stable["status"], "CONFIDENT")
        self.assertEqual(weak["status"], "UNCERTAIN")

    def test_high_confidence_inconsistent_track_is_not_maximum(self):
        stable = estimate_reliability(temporal_row("stable"))
        inconsistent = estimate_reliability(
            temporal_row(
                "inconsistent",
                confidence_std=0.5,
                mean_displacement=100,
            )
        )

        self.assertEqual(inconsistent["confidence_score"], 0.9)
        self.assertEqual(inconsistent["consistency_score"], 0.0)
        self.assertLess(inconsistent["reliability_score"], stable["reliability_score"])
        self.assertLess(inconsistent["reliability_score"], 1.0)

    def test_scores_are_bounded_and_custom_threshold_changes_status(self):
        result = estimate_reliability(temporal_row("threshold"), threshold=0.8)

        for field in (
            "confidence_score",
            "consistency_score",
            "persistence_score",
            "motion_score",
            "reliability_score",
        ):
            self.assertGreaterEqual(result[field], 0.0)
            self.assertLessEqual(result[field], 1.0)
        self.assertEqual(DEFAULT_THRESHOLD, 0.6)
        self.assertEqual(result["status"], "CONFIDENT")
        self.assertEqual(
            estimate_reliability(temporal_row("threshold"), threshold=0.99)["status"],
            "UNCERTAIN",
        )

    def test_csv_tracks_are_processed_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            input_csv = Path(directory) / "temporal_features.csv"
            with input_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=temporal_row("id").keys())
                writer.writeheader()
                writer.writerow(temporal_row("bird-track"))
                writer.writerow(temporal_row("drone-track", mean_confidence=0.3))

            results = process_temporal_csv(input_csv)
            output_csv = Path(directory) / "reliability_results.csv"
            with output_csv.open(newline="", encoding="utf-8") as source:
                saved = list(csv.DictReader(source))

            self.assertEqual([row["track_id"] for row in results], ["bird-track", "drone-track"])
            self.assertEqual(len(saved), 2)
            self.assertEqual(tuple(saved[0].keys()), OUTPUT_FIELDS)
            self.assertNotEqual(saved[0]["reliability_score"], saved[1]["reliability_score"])

    def test_optional_trajectory_diagnostics_leave_historical_reliability_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporal_csv = root / "temporal_features.csv"
            trajectory_csv = root / "motion_track_summary.csv"
            reliability_csv = root / "reliability_results.csv"
            original = temporal_row("17")
            with temporal_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=original.keys())
                writer.writeheader()
                writer.writerow(original)
            trajectory = {
                "track_id": "17",
                "direction_consistency": "0.8",
                "straightness_ratio": "0.6",
                "consecutive_frame_ratio": "0.9",
                "speed_std": "5",
                "mean_speed_px_per_second": "10",
                "acceleration_std": "2",
                "mean_acceleration_px_per_second2": "2",
                "dominant_motion_state": "STEADY_MOVEMENT",
                "motion_quality": "USABLE_IMAGE_PLANE",
            }
            with trajectory_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=trajectory.keys())
                writer.writeheader()
                writer.writerow(trajectory)

            historical = estimate_reliability(original)
            result = process_temporal_csv(
                temporal_csv,
                reliability_csv,
                trajectory_summary_csv=trajectory_csv,
            )[0]
            with reliability_csv.open(newline="", encoding="utf-8") as source:
                saved = next(csv.DictReader(source))

            expected_diagnostic = (0.8 + 0.6 + 0.9 + (1 / 1.5) + 0.5) / 5
            self.assertEqual(result["reliability_score"], historical["reliability_score"])
            self.assertEqual(result["motion_score"], historical["motion_score"])
            self.assertEqual(result["legacy_motion_score"], historical["motion_score"])
            self.assertAlmostEqual(result["trajectory_motion_score"], expected_diagnostic)
            self.assertEqual(result["trajectory_motion_state"], "STEADY_MOVEMENT")
            self.assertEqual(result["trajectory_motion_quality"], "USABLE_IMAGE_PLANE")
            self.assertEqual(tuple(saved.keys()), OUTPUT_FIELDS + MOTION_DIAGNOSTIC_FIELDS)
            self.assertEqual(float(saved["legacy_motion_score"]), historical["motion_score"])
            self.assertFalse(any(value.casefold() in {"nan", "inf", "-inf", "infinity"} for value in saved.values()))
            self.assertFalse(any("meter" in field.casefold() or "m/s" in field.casefold() for field in saved))

    def test_optional_trajectory_diagnostics_reject_nonfinite_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporal_csv = root / "temporal_features.csv"
            trajectory_csv = root / "motion_track_summary.csv"
            original = temporal_row("3")
            with temporal_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=original.keys())
                writer.writeheader()
                writer.writerow(original)
            trajectory = {
                "track_id": "3",
                "direction_consistency": "nan",
            }
            with trajectory_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=trajectory.keys())
                writer.writeheader()
                writer.writerow(trajectory)

            with self.assertRaisesRegex(ValueError, "finite"):
                process_temporal_csv(
                    temporal_csv,
                    trajectory_summary_csv=trajectory_csv,
                )


if __name__ == "__main__":
    unittest.main()
