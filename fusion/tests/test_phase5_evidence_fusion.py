import csv
import tempfile
import unittest
from pathlib import Path

from fusion.phase5_evidence_fusion import (
    _radar_associations_by_track,
    run_phase5_fusion,
)


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class Phase5EvidenceFusionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths = {
            name: self.root / f"{name}.csv"
            for name in (
                "tracks",
                "temporal",
                "reliability",
                "vlm",
                "thermal",
                "radar",
                "baseline",
                "exploratory",
                "consensus",
            )
        }
        write_csv(
            self.paths["tracks"],
            ("source", "track_id", "class_name", "confidence"),
            (
                {"source": "caltech.mp4", "track_id": 1, "class_name": "Drone", "confidence": 0.8},
                {"source": "caltech.mp4", "track_id": 2, "class_name": "Drone", "confidence": 0.5},
            ),
        )
        write_csv(
            self.paths["temporal"],
            ("track_id", "persistence", "frames_seen"),
            ({"track_id": 1, "persistence": 0.75, "frames_seen": 4},
             {"track_id": 2, "persistence": 0.5, "frames_seen": 2}),
        )
        write_csv(
            self.paths["reliability"],
            ("track_id", "reliability_score", "status"),
            ({"track_id": 1, "reliability_score": 0.6, "status": "UNCERTAIN"},
             {"track_id": 2, "reliability_score": 0.8, "status": "CONFIDENT"}),
        )
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            ({"track_id": 1, "label": "Bird", "confidence": 0.9},),
        )
        write_csv(
            self.paths["thermal"],
            ("track_id", "thermal_frame_available", "thermal_spatial_crop_available"),
            ({"track_id": 1, "thermal_frame_available": "true", "thermal_spatial_crop_available": "false"},
             {"track_id": 2, "thermal_frame_available": "false", "thermal_spatial_crop_available": "false"}),
        )
        write_csv(
            self.paths["radar"],
            (
                "sample_index",
                "rd_matrix_available",
                "radar_timestamp_available",
                "caltech_recording_id_available",
                "synchronization_metadata_available",
                "track24_association",
            ),
            ({"sample_index": 1, "rd_matrix_available": "YES",
              "radar_timestamp_available": "NO", "caltech_recording_id_available": "NO",
              "synchronization_metadata_available": "NO", "track24_association": "NO"},),
        )
        write_csv(
            self.paths["baseline"],
            ("track_id", "final_label", "decision_confidence"),
            ({"track_id": 1, "final_label": "UAV", "decision_confidence": 0.8},
             {"track_id": 2, "final_label": "UAV", "decision_confidence": 0.4}),
        )
        write_csv(
            self.paths["exploratory"],
            ("track_id", "final_label", "decision_confidence", "fusion_mode"),
            ({"track_id": 1, "final_label": "UAV", "decision_confidence": 0.48,
              "fusion_mode": "detector_vlm"},
             {"track_id": 2, "final_label": "UAV", "decision_confidence": 0.4,
              "fusion_mode": "detector_temporal"}),
        )
        self.fusion_out = self.root / "phase5.csv"
        self.availability_out = self.root / "availability.csv"
        self.report_out = self.root / "report.txt"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_fusion(self, *, compare_historical_outputs=True):
        return run_phase5_fusion(
            self.paths["tracks"],
            self.paths["temporal"],
            self.paths["reliability"],
            self.paths["vlm"],
            self.paths["thermal"],
            self.paths["radar"],
            self.fusion_out,
            self.availability_out,
            self.report_out,
            baseline_csv=self.paths["baseline"] if compare_historical_outputs else None,
            exploratory_csv=self.paths["exploratory"] if compare_historical_outputs else None,
        )

    def test_only_associated_evidence_contributes_and_outputs_are_complete(self):
        rows, availability, report = self.run_fusion()
        self.assertEqual([row["track_id"] for row in rows], ["1", "2"])
        self.assertEqual([row["fusion_mode"] for row in rows], ["detector_vlm", "detector_temporal"])
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertAlmostEqual(rows[0]["final_confidence"], 0.48)
        self.assertEqual(rows[0]["thermal_evidence_available"], "true")
        self.assertEqual(rows[0]["thermal_evidence_associated"], "false")
        self.assertEqual(rows[0]["radar_evidence_available"], "false")
        self.assertEqual(rows[0]["radar_evidence_associated"], "false")
        self.assertTrue(
            all(
                row["radar_evidence_associated"] == "false"
                and row["radar_evidence_available"] == "false"
                for row in rows
            )
        )
        self.assertEqual(availability[0]["thermal_spatially_associated"], "evidence_not_associated")
        self.assertEqual(availability[0]["radar_caltech_associated"], "evidence_not_associated")
        self.assertTrue(
            all(
                row["radar_caltech_associated"] == "evidence_not_associated"
                for row in availability
            )
        )
        self.assertIn("Missing evidence is not used as negative evidence", report)
        with self.fusion_out.open(newline="", encoding="utf-8") as source:
            saved = list(csv.DictReader(source))
        self.assertEqual(len(saved), 2)

    def test_radar_association_mapping_is_per_track(self):
        associations = _radar_associations_by_track(
            {"24", "25", "26"},
            [
                {
                    "rd_matrix_available": "YES",
                    "radar_timestamp_available": "YES",
                    "caltech_recording_id_available": "YES",
                    "synchronization_metadata_available": "YES",
                    "track24_association": "YES",
                }
            ],
        )
        self.assertEqual(associations, {"24": True, "25": False, "26": False})

    def test_missing_radar_is_neutral_to_fusion_result(self):
        rows_with_unassociated_radar, _, _ = self.run_fusion()
        expected_decisions = [
            (row["final_label"], row["final_confidence"])
            for row in rows_with_unassociated_radar
        ]

        write_csv(
            self.paths["radar"],
            (
                "sample_index",
                "rd_matrix_available",
                "radar_timestamp_available",
                "caltech_recording_id_available",
                "synchronization_metadata_available",
                "track24_association",
            ),
            (),
        )
        rows_without_radar, availability, _ = self.run_fusion()
        self.assertEqual(
            [
                (row["final_label"], row["final_confidence"])
                for row in rows_without_radar
            ],
            expected_decisions,
        )
        self.assertTrue(
            all(
                row["radar_evidence_associated"] == "false"
                and row["radar_evidence_available"] == "false"
                for row in rows_without_radar
            )
        )
        self.assertTrue(
            all(row["radar_genuine"] == "evidence_not_available" for row in availability)
        )

    def test_radar_outputs_do_not_create_persistent_identity(self):
        rows, _, _ = self.run_fusion()
        for row in rows:
            self.assertNotIn("radar_identity", row)
            self.assertNotIn("radar_candidate_index", row)

    def test_boreas_uuid_association_is_not_a_caltech_track_association(self):
        associations = _radar_associations_by_track(
            {"24", "25"},
            [
                {
                    "visual_track_or_uuid": "3923f2b0-8273-4fb0-becf-af6e0998eb22",
                    "association_status": "ASSOCIATED",
                    "radar_candidate_index": "21",
                }
            ],
        )
        self.assertEqual(associations, {"24": False, "25": False})

    def test_invalid_confidence_is_rejected(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            ({"track_id": 1, "label": "Bird", "confidence": "nan"},),
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            self.run_fusion()

    def test_single_vlm_row_preserves_existing_fusion_result(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            ({"track_id": 1, "label": "Bird", "confidence": 0.9},),
        )

        rows, _, _ = self.run_fusion()

        self.assertEqual(rows[0]["vlm_label"], "Bird")
        self.assertEqual(rows[0]["vlm_confidence"], 0.9)
        self.assertEqual(rows[0]["fusion_mode"], "detector_vlm")
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertAlmostEqual(rows[0]["final_confidence"], 0.48)

    def test_multiple_consistent_vlm_rows_are_averaged_by_label(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            (
                {"track_id": 1, "label": "Bird", "confidence": 0.8},
                {"track_id": 1, "label": "Bird", "confidence": 0.6},
            ),
        )

        rows, _, _ = self.run_fusion(compare_historical_outputs=False)

        self.assertEqual(rows[0]["vlm_label"], "Bird")
        self.assertAlmostEqual(rows[0]["vlm_confidence"], 0.7)
        self.assertEqual(rows[0]["fusion_mode"], "detector_vlm")

    def test_unknown_vlm_rows_remain_metadata_and_do_not_fuse(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            (
                {"track_id": 1, "label": "Unknown", "confidence": 0.2},
                {"track_id": 1, "label": "Unknown", "confidence": 0.4},
            ),
        )

        rows, _, _ = self.run_fusion(compare_historical_outputs=False)

        self.assertEqual(rows[0]["vlm_label"], "Unknown")
        self.assertAlmostEqual(rows[0]["vlm_confidence"], 0.3)
        self.assertEqual(rows[0]["fusion_mode"], "detector_temporal")
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertAlmostEqual(rows[0]["final_confidence"], 0.48)

    def test_conflicting_vlm_labels_contribute_their_per_label_means(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            (
                {"track_id": 1, "label": "Bird", "confidence": 0.9},
                {"track_id": 1, "label": "Drone", "confidence": 0.7},
            ),
        )

        rows, _, _ = self.run_fusion(compare_historical_outputs=False)

        self.assertEqual(rows[0]["vlm_label"], "Bird")
        self.assertAlmostEqual(rows[0]["vlm_confidence"], 0.9)
        self.assertEqual(rows[0]["fusion_mode"], "detector_vlm")
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertAlmostEqual(rows[0]["final_confidence"], 0.76)

    def test_malformed_or_invalid_vlm_rows_are_rejected(self):
        invalid_rows = (
            {"track_id": "", "label": "Bird", "confidence": 0.9},
            {"track_id": 1, "label": "unrecognized", "confidence": 0.9},
            {"track_id": 1, "label": "Bird", "confidence": "nan"},
            {"track_id": 1, "label": "Bird", "confidence": 1.1},
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                write_csv(
                    self.paths["vlm"],
                    ("track_id", "label", "confidence"),
                    (row,),
                )
                with self.assertRaises(ValueError):
                    self.run_fusion()

    def test_no_vlm_rows_use_detector_temporal_fusion(self):
        write_csv(
            self.paths["vlm"],
            ("track_id", "label", "confidence"),
            (),
        )

        rows, availability, _ = self.run_fusion(compare_historical_outputs=False)

        self.assertEqual(rows[0]["vlm_label"], "")
        self.assertEqual(rows[0]["vlm_confidence"], "")
        self.assertEqual(rows[0]["fusion_mode"], "detector_temporal")
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertAlmostEqual(rows[0]["final_confidence"], 0.48)
        self.assertEqual(availability[0]["vlm"], "evidence_not_available")

    def test_temporal_consensus_prevents_rare_high_confidence_class_from_winning(self):
        write_csv(
            self.paths["tracks"],
            ("source", "track_id", "class_name", "confidence"),
            [
                {"source": "birds.mp4", "track_id": 1, "class_name": "Bird", "confidence": 0.52}
                for _ in range(4)
            ]
            + [
                {"source": "birds.mp4", "track_id": 1, "class_name": "Drone", "confidence": 0.99},
                {"source": "birds.mp4", "track_id": 2, "class_name": "Bird", "confidence": 0.6},
                {"source": "birds.mp4", "track_id": 2, "class_name": "Drone", "confidence": 0.8},
            ],
        )
        write_csv(
            self.paths["consensus"],
            ("track_id", "consensus_label", "decision_status", "dominant_class"),
            (
                {"track_id": 1, "consensus_label": "Bird", "decision_status": "confident", "dominant_class": "Bird"},
                {"track_id": 2, "consensus_label": "Uncertain", "decision_status": "uncertain", "dominant_class": "Bird"},
            ),
        )

        rows, _, report = run_phase5_fusion(
            self.paths["tracks"],
            self.paths["temporal"],
            self.paths["reliability"],
            self.paths["vlm"],
            self.paths["thermal"],
            self.paths["radar"],
            self.fusion_out,
            self.availability_out,
            self.report_out,
            temporal_consensus_csv=self.paths["consensus"],
        )

        by_id = {row["track_id"]: row for row in rows}
        self.assertEqual(by_id["1"]["detector_label"], "Bird")
        self.assertEqual(by_id["1"]["final_label"], "Bird")
        self.assertEqual(by_id["2"]["final_label"], "Other")
        self.assertEqual(by_id["2"]["fusion_mode"], "detector_temporal_abstain")
        self.assertIn("uncertain tracks abstain as Other", report)

    def test_mixed_detector_labels_use_temporal_consensus_without_consensus_file(self):
        observations = [
            {"source": "birds.mp4", "frame": frame, "track_id": 1,
             "class_name": "Bird", "confidence": 0.52}
            for frame in range(1, 5)
        ] + [
            {"source": "birds.mp4", "frame": 5, "track_id": 1,
             "class_name": "Drone", "confidence": 0.99},
            {"source": "birds.mp4", "frame": 10, "track_id": 2,
             "class_name": "Bird", "confidence": 0.6},
            {"source": "birds.mp4", "frame": 11, "track_id": 2,
             "class_name": "Drone", "confidence": 0.8},
        ]
        write_csv(
            self.paths["tracks"],
            ("source", "frame", "track_id", "class_name", "confidence"),
            observations,
        )

        rows, _, report = self.run_fusion()

        by_id = {row["track_id"]: row for row in rows}
        self.assertEqual(by_id["1"]["detector_label"], "Bird")
        self.assertEqual(by_id["1"]["final_label"], "Bird")
        self.assertEqual(by_id["2"]["detector_label"], "Drone")
        self.assertEqual(by_id["2"]["fusion_mode"], "detector_temporal_abstain")
        self.assertEqual(by_id["2"]["final_label"], "Other")
        self.assertIn("computed from frame-level observations", report)
        with self.paths["tracks"].open(newline="", encoding="utf-8") as source:
            saved_observations = list(csv.DictReader(source))
        self.assertEqual(len(saved_observations), len(observations))
        self.assertEqual(
            [row["class_name"] for row in saved_observations],
            [row["class_name"] for row in observations],
        )


if __name__ == "__main__":
    unittest.main()
