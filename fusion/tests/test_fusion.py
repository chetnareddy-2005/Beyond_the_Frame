import csv
import tempfile
import unittest
from pathlib import Path

from fusion.evidence_fusion import fuse_evidence, fuse_track


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class EvidenceFusionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.tracks = self.directory / "tracks.csv"
        self.temporal = self.directory / "temporal_features.csv"
        self.reliability = self.directory / "reliability_results.csv"
        write_csv(
            self.tracks,
            ("track_id", "class_name", "confidence"),
            (
                {"track_id": 1, "class_name": "Drone", "confidence": 0.8},
                {"track_id": 1, "class_name": "Drone", "confidence": 0.6},
                {"track_id": 1, "class_name": "Bird", "confidence": 0.2},
                {"track_id": 2, "class_name": "Bird", "confidence": 0.7},
            ),
        )
        write_csv(
            self.temporal,
            ("track_id", "frames_seen"),
            ({"track_id": 1, "frames_seen": 3}, {"track_id": 2, "frames_seen": 1}),
        )
        write_csv(
            self.reliability,
            ("track_id", "reliability_score", "status"),
            (
                {"track_id": 1, "reliability_score": 0.9, "status": "CONFIDENT"},
                {"track_id": 2, "reliability_score": 0.4, "status": "UNCERTAIN"},
            ),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detector_temporal_high_reliability_scales_confidence(self):
        label, score, mode = fuse_track({"UAV": 0.9, "Bird": 0.1}, 0.8)
        self.assertEqual((label, mode), ("UAV", "detector_temporal"))
        self.assertAlmostEqual(score, 0.72)

    def test_detector_temporal_low_reliability_scales_confidence(self):
        label, score, mode = fuse_track({"UAV": 0.9, "Bird": 0.1}, 0.2)
        self.assertEqual((label, mode), ("UAV", "detector_temporal"))
        self.assertAlmostEqual(score, 0.18)

    def test_detector_temporal_confidence_is_bounded_and_label_uses_detector_scores(self):
        label, score, mode = fuse_track({"UAV": 0.3, "Bird": 0.8}, 0.5)
        self.assertEqual((label, mode), ("Bird", "detector_temporal"))
        self.assertAlmostEqual(score, 0.4)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_vlm_has_more_influence_when_reliability_is_low(self):
        label, score, mode = fuse_track(
            {"UAV": 0.45, "Bird": 0.55}, 0.35, {"Bird": 0.85}
        )
        self.assertEqual((label, mode), ("Bird", "detector_vlm"))
        self.assertAlmostEqual(score, 0.745)

    def test_detector_temporal_confidence_is_clipped_to_valid_range(self):
        _, score, _ = fuse_track({"UAV": 1.5}, 0.8)
        self.assertEqual(score, 1.0)

    def test_missing_vlm_uses_detector_without_fabricating_vlm_values(self):
        output = self.directory / "no_vlm.csv"
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability, output_csv=output)
        self.assertEqual(rows[0]["fusion_mode"], "detector_temporal")
        self.assertEqual(rows[0]["vlm_label"], "")
        self.assertEqual(rows[0]["vlm_confidence"], "")
        self.assertAlmostEqual(rows[0]["decision_confidence"], 0.7)
        with output.open(newline="", encoding="utf-8") as source:
            saved = list(csv.DictReader(source))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["final_label"], "UAV")

    def test_unknown_vlm_does_not_become_other(self):
        vlm = self.directory / "vlm.csv"
        write_csv(
            vlm,
            ("track_id", "vlm_label", "vlm_confidence"),
            ({"track_id": 1, "vlm_label": "Unknown", "vlm_confidence": 0.0},),
        )
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability, vlm)
        self.assertEqual(rows[0]["final_label"], "UAV")
        self.assertEqual(rows[0]["fusion_mode"], "detector_temporal")
        self.assertEqual(rows[0]["vlm_label"], "Unknown")
        self.assertNotEqual(rows[0]["final_label"], "Other")

    def test_vlm_csv_evidence_is_joined_and_mapped(self):
        vlm = self.directory / "vlm_drone.csv"
        write_csv(
            vlm,
            ("track_id", "vlm_label", "vlm_confidence"),
            ({"track_id": 2, "vlm_label": "Drone", "vlm_confidence": 0.9},),
        )
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability, vlm)
        bird_track = rows[1]
        self.assertEqual(bird_track["detector_class"], "Bird")
        self.assertEqual(bird_track["vlm_label"], "Drone")
        self.assertEqual(bird_track["fusion_mode"], "detector_vlm")
        self.assertEqual(bird_track["final_label"], "UAV")

    def test_vlm_other_maps_to_other(self):
        vlm = self.directory / "vlm_other.csv"
        write_csv(
            vlm,
            ("track_id", "vlm_label", "vlm_confidence"),
            ({"track_id": 2, "vlm_label": "Other", "vlm_confidence": 0.9},),
        )
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability, vlm)
        self.assertEqual(rows[1]["final_label"], "Other")

    def test_multiple_tracks_are_fused_independently(self):
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability)
        self.assertEqual([row["track_id"] for row in rows], ["1", "2"])
        self.assertEqual([row["final_label"] for row in rows], ["UAV", "Bird"])
        self.assertAlmostEqual(rows[1]["detector_score"], 0.7)

    def test_reliability_weight_changes_fused_score(self):
        detector_trust, detector_score, _ = fuse_track({"UAV": 0.8}, 0.8, {"UAV": 0.2})
        vlm_trust, vlm_score, _ = fuse_track({"UAV": 0.8}, 0.2, {"UAV": 0.2})
        self.assertEqual(detector_trust, "UAV")
        self.assertEqual(vlm_trust, "UAV")
        self.assertGreater(detector_score, vlm_score)

    def test_final_labels_are_limited_to_supported_classes(self):
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability)
        self.assertTrue({row["final_label"] for row in rows} <= {"UAV", "Bird", "Other"})

    def test_decision_scores_remain_in_bounds(self):
        rows = fuse_evidence(self.tracks, self.temporal, self.reliability)
        self.assertTrue(
            all(0 <= float(row["decision_confidence"]) <= 1 for row in rows)
        )
        self.assertAlmostEqual(rows[0]["detector_score"], 0.7 / (0.7 + 0.2))

    def test_phase9_live_boundary_cases(self):
        # CASE 1: CONFIDENT track + no VLM -> detector_temporal
        final, conf, mode = fuse_track({"UAV": 0.9}, 0.85, None)
        self.assertEqual(mode, "detector_temporal")
        self.assertEqual(final, "UAV")
        self.assertAlmostEqual(conf, 0.765)

        # CASE 2 & CASE 5: UNCERTAIN track + valid VLM ("Drone" -> "UAV") -> detector_vlm
        final_vlm, conf_vlm, mode_vlm = fuse_track({"UAV": 0.4}, 0.3, {"UAV": 0.9})
        self.assertEqual(mode_vlm, "detector_vlm")
        self.assertEqual(final_vlm, "UAV")
        self.assertAlmostEqual(conf_vlm, 0.3 * 0.4 + 0.7 * 0.9)

        # CASE 3: UNCERTAIN track + no VLM -> fallback to detector_temporal
        final_fb, conf_fb, mode_fb = fuse_track({"Bird": 0.8}, 0.3, None)
        self.assertEqual(mode_fb, "detector_temporal")
        self.assertEqual(final_fb, "Bird")
        self.assertAlmostEqual(conf_fb, 0.24)

        # CASE 4: VLM label = Unknown -> fuse_track called without vlm_scores
        final_unk, conf_unk, mode_unk = fuse_track({"UAV": 0.7}, 0.4, {})
        self.assertEqual(mode_unk, "detector_temporal")
        self.assertEqual(final_unk, "UAV")

        # CASE 6: Multiple tracks fused independently
        t1_label, t1_score, _ = fuse_track({"UAV": 0.9}, 0.9, None)
        t2_label, t2_score, _ = fuse_track({"Bird": 0.8}, 0.2, {"Bird": 0.95})
        self.assertEqual((t1_label, t2_label), ("UAV", "Bird"))
        self.assertNotEqual(t1_score, t2_score)

        # CASE 7: Invalid bounds validation
        with self.assertRaises(ValueError):
            fuse_track({"UAV": 0.9}, 1.5)


if __name__ == "__main__":
    unittest.main()
