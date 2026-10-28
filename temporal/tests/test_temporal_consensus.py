"""Unit tests for uncertainty-aware temporal consensus."""

import csv
import tempfile
import unittest
from pathlib import Path

from temporal.temporal_consensus import (
    CONSENSUS_SCORE_INTERPRETATION,
    TemporalConsensusConfig,
    evaluate_track_consensus,
    load_tracks_consensus,
    normalize_class_name,
    process_tracks_consensus,
)


class TemporalConsensusTests(unittest.TestCase):
    def test_normalize_class_name(self):
        self.assertEqual(normalize_class_name("0"), "Bird")
        self.assertEqual(normalize_class_name("Bird"), "Bird")
        self.assertEqual(normalize_class_name("bird"), "Bird")
        self.assertEqual(normalize_class_name(0), "Bird")
        self.assertEqual(normalize_class_name("1"), "Drone")
        self.assertEqual(normalize_class_name("Drone"), "Drone")
        self.assertEqual(normalize_class_name("uav"), "Drone")
        self.assertEqual(normalize_class_name("UAV"), "Drone")
        self.assertEqual(normalize_class_name(1), "Drone")
        self.assertEqual(normalize_class_name("unknown_object"), "Unknown")
        self.assertEqual(normalize_class_name(None), "Unknown")

    def test_consistent_drone_observations(self):
        observations = [
            {"frame": i, "confidence": 0.85, "class": "Drone"}
            for i in range(1, 10)
        ]
        decision = evaluate_track_consensus(observations)
        self.assertEqual(decision["consensus_label"], "Drone")
        self.assertEqual(decision["decision_status"], "confident")
        self.assertEqual(decision["dominant_class"], "Drone")
        self.assertEqual(decision["dominant_class_ratio"], 1.0)
        self.assertEqual(decision["class_switch_count"], 0)
        self.assertIn("consistent_drone_observations", decision["reasons"])

    def test_consistent_bird_observations(self):
        observations = [
            {"frame": i, "confidence": 0.75, "class_name": "Bird"}
            for i in range(1, 8)
        ]
        decision = evaluate_track_consensus(observations)
        self.assertEqual(decision["consensus_label"], "Bird")
        self.assertEqual(decision["decision_status"], "confident")
        self.assertEqual(decision["dominant_class"], "Bird")
        self.assertEqual(decision["dominant_class_ratio"], 1.0)
        self.assertEqual(decision["class_switch_count"], 0)
        self.assertIn("consistent_bird_observations", decision["reasons"])

    def test_insufficient_observations_returns_uncertain(self):
        # 1 observation
        obs1 = [{"frame": 1, "confidence": 0.90, "class": "Drone"}]
        dec1 = evaluate_track_consensus(obs1)
        self.assertEqual(dec1["consensus_label"], "Uncertain")
        self.assertEqual(dec1["decision_status"], "uncertain")
        self.assertIn("insufficient_observations", dec1["reasons"])

        # 2 observations (default min is 3)
        obs2 = [
            {"frame": 1, "confidence": 0.80, "class": "Drone"},
            {"frame": 2, "confidence": 0.85, "class": "Drone"},
        ]
        dec2 = evaluate_track_consensus(obs2)
        self.assertEqual(dec2["consensus_label"], "Uncertain")
        self.assertIn("insufficient_observations", dec2["reasons"])

    def test_class_conflict_and_switching_returns_uncertain(self):
        # Oscillating between Bird and Drone
        observations = [
            {"frame": 1, "confidence": 0.60, "class": "Drone"},
            {"frame": 2, "confidence": 0.60, "class": "Bird"},
            {"frame": 3, "confidence": 0.60, "class": "Drone"},
            {"frame": 4, "confidence": 0.60, "class": "Bird"},
            {"frame": 5, "confidence": 0.60, "class": "Drone"},
        ]
        decision = evaluate_track_consensus(observations)
        self.assertEqual(decision["consensus_label"], "Uncertain")
        self.assertEqual(decision["decision_status"], "uncertain")
        self.assertIn("class_switching_conflict", decision["reasons"])
        self.assertGreaterEqual(decision["class_switch_count"], 3)

    def test_low_detector_confidence_returns_uncertain(self):
        observations = [
            {"frame": i, "confidence": 0.20, "class": "Drone"}
            for i in range(1, 8)
        ]
        decision = evaluate_track_consensus(observations)
        self.assertEqual(decision["consensus_label"], "Uncertain")
        self.assertIn("low_detector_confidence", decision["reasons"])

    def test_track_gaps_and_continuity(self):
        # Observations with large gaps: frame 1, 10, 20
        observations = [
            {"frame": 1, "confidence": 0.70, "class": "Drone"},
            {"frame": 10, "confidence": 0.70, "class": "Drone"},
            {"frame": 20, "confidence": 0.70, "class": "Drone"},
        ]
        decision = evaluate_track_consensus(observations)
        self.assertEqual(decision["gap_count"], 2)
        self.assertEqual(decision["max_gap"], 9)
        self.assertIn("excessive_frame_gaps", decision["reasons"])

    def test_causal_up_to_frame_prevents_future_leakage(self):
        observations = [
            {"frame": 1, "confidence": 0.80, "class": "Drone"},
            {"frame": 2, "confidence": 0.85, "class": "Drone"},
            {"frame": 3, "confidence": 0.82, "class": "Drone"},
            {"frame": 4, "confidence": 0.88, "class": "Drone"},
            {"frame": 5, "confidence": 0.30, "class": "Bird"},
        ]
        # At frame 3, we only have 3 drone observations
        dec_at_3 = evaluate_track_consensus(observations, causal_up_to_frame=3)
        self.assertEqual(dec_at_3["observation_count"], 3)
        self.assertEqual(dec_at_3["consensus_label"], "Drone")
        self.assertEqual(dec_at_3["class_switch_count"], 0)

    def test_configurable_thresholds(self):
        custom_config = TemporalConsensusConfig(
            min_observations=5,
            consistency_threshold=0.85,
            min_mean_confidence=0.50,
        )
        observations = [
            {"frame": 1, "confidence": 0.70, "class": "Drone"},
            {"frame": 2, "confidence": 0.70, "class": "Drone"},
            {"frame": 3, "confidence": 0.70, "class": "Drone"},
            {"frame": 4, "confidence": 0.70, "class": "Drone"},
        ]
        # With min_observations=5, 4 observations is insufficient
        decision = evaluate_track_consensus(observations, config=custom_config)
        self.assertEqual(decision["consensus_label"], "Uncertain")
        self.assertIn("insufficient_observations", decision["reasons"])

    def test_class_id_zero_is_bird_and_consensus_keeps_frame_references(self):
        observations = [
            {"frame": index, "class_id": 0, "confidence": 0.8}
            for index in range(3)
        ]

        decision = evaluate_track_consensus(observations)

        self.assertEqual(decision["consensus_label"], "Bird")
        self.assertEqual(decision["supporting_frame_indexes"], [0, 1, 2])
        self.assertEqual(decision["contradictory_frame_indexes"], [])
        self.assertEqual(
            decision["score_interpretation"],
            CONSENSUS_SCORE_INTERPRETATION,
        )
        self.assertEqual(observations[0]["class_id"], 0)

    def test_unknown_classes_and_conflicting_frames_are_explicit(self):
        unknown = evaluate_track_consensus(
            [
                {"frame": index, "class_name": "unmapped", "confidence": 0.9}
                for index in range(3)
            ]
        )
        self.assertEqual(unknown["consensus_label"], "Uncertain")
        self.assertEqual(unknown["dominant_class"], "Unknown")
        self.assertIn("no_recognized_class_evidence", unknown["reasons"])
        self.assertEqual(unknown["unclassified_frame_indexes"], [0, 1, 2])

        conflict = evaluate_track_consensus(
            [
                {"frame": 1, "class_name": "Bird", "confidence": 0.8},
                {"frame": 2, "class_name": "Drone", "confidence": 0.8},
                {"frame": 3, "class_name": "Bird", "confidence": 0.8},
            ]
        )
        self.assertEqual(conflict["supporting_frame_indexes"], [1, 3])
        self.assertEqual(conflict["contradictory_frame_indexes"], [2])

    def test_invalid_observations_and_configuration_fail_explicitly(self):
        for observations in (
            [{"frame": -1, "class": "Bird", "confidence": 0.8}],
            [{"frame": 1, "class": "Bird", "confidence": 1.1}],
            [
                {"frame": 1, "class": "Bird", "confidence": 0.8},
                {"frame": 1, "class": "Drone", "confidence": 0.8},
            ],
        ):
            with self.subTest(observations=observations), self.assertRaises(ValueError):
                evaluate_track_consensus(observations)
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            TemporalConsensusConfig(confidence_weight=0.5)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            TemporalConsensusConfig(min_observations=0)

    def test_process_tracks_consensus_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_csv = Path(tmp) / "tracks.csv"
            output_csv = Path(tmp) / "consensus.csv"
            with tracks_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "frame",
                        "track_id",
                        "class_name",
                        "confidence",
                        "x1",
                        "y1",
                        "x2",
                        "y2",
                    ],
                )
                writer.writeheader()
                # Track 1: confident Drone (4 observations)
                for i in range(1, 5):
                    writer.writerow(
                        {
                            "frame": i,
                            "track_id": 1,
                            "class_name": "Drone",
                            "confidence": 0.80,
                            "x1": 10,
                            "y1": 10,
                            "x2": 50,
                            "y2": 50,
                        }
                    )
                # Track 2: uncertain (conflict)
                writer.writerow(
                    {
                        "frame": 1,
                        "track_id": 2,
                        "class_name": "Bird",
                        "confidence": 0.60,
                        "x1": 10,
                        "y1": 10,
                        "x2": 50,
                        "y2": 50,
                    }
                )
                writer.writerow(
                    {
                        "frame": 2,
                        "track_id": 2,
                        "class_name": "Drone",
                        "confidence": 0.60,
                        "x1": 10,
                        "y1": 10,
                        "x2": 50,
                        "y2": 50,
                    }
                )
                writer.writerow(
                    {
                        "frame": 3,
                        "track_id": 2,
                        "class_name": "Bird",
                        "confidence": 0.60,
                        "x1": 10,
                        "y1": 10,
                        "x2": 50,
                        "y2": 50,
                    }
                )

            progress = []
            results = process_tracks_consensus(
                tracks_csv,
                output_csv,
                progress_callback=lambda current, total: progress.append((current, total)),
            )
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["consensus_label"], "Drone")
            self.assertEqual(results[1]["consensus_label"], "Uncertain")
            self.assertEqual(progress, [(1, 2), (2, 2)])
            self.assertTrue(output_csv.is_file())
            loaded = load_tracks_consensus(output_csv)
            self.assertEqual(loaded["1"]["consensus_label"], "Drone")
            self.assertEqual(loaded["1"]["supporting_frame_indexes"], [1, 2, 3, 4])
            self.assertEqual(loaded["2"]["contradictory_frame_indexes"], [2])

    def test_process_tracks_consensus_honors_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_csv = Path(tmp) / "tracks.csv"
            output_csv = Path(tmp) / "consensus.csv"
            tracks_csv.write_text(
                "frame,track_id,class_name,confidence\n"
                "1,1,Bird,0.8\n2,1,Bird,0.8\n3,1,Bird,0.8\n",
                encoding="utf-8",
            )

            def cancel():
                raise RuntimeError("cancelled by test")

            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                process_tracks_consensus(
                    tracks_csv, output_csv, check_cancelled=cancel
                )
            self.assertFalse(output_csv.exists())

    def test_legacy_consensus_csv_remains_readable_without_invented_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy_csv = Path(tmp) / "legacy_consensus.csv"
            legacy_csv.write_text(
                "track_id,consensus_label,decision_status,dominant_class,"
                "dominant_class_ratio,consensus_confidence,observation_count,"
                "class_switch_count,gap_count,max_gap,reasons\n"
                "4,Drone,confident,Drone,1.0,0.8,4,0,0,0,consistent_drone_observations\n",
                encoding="utf-8",
            )

            loaded = load_tracks_consensus(legacy_csv)["4"]

            self.assertEqual(loaded["consensus_label"], "Drone")
            self.assertFalse(loaded["evidence_references_available"])
            self.assertEqual(loaded["supporting_frame_indexes"], [])
            self.assertIn("legacy", loaded["score_interpretation"])


if __name__ == "__main__":
    unittest.main()
