import unittest

from multimodal.boreas_radar.temporal_visual_track_validation import (
    consecutive_runs,
    deduplicate_associations,
)


class TemporalVisualTrackValidationTests(unittest.TestCase):
    def test_consecutive_runs_use_radar_scan_order_indices(self):
        self.assertEqual(
            consecutive_runs([4, 2, 3, 8, 8, 10]),
            [[2, 3, 4], [8], [10]],
        )
        self.assertEqual(consecutive_runs([]), [])

    def test_duplicate_label_rows_collapse_to_one_uuid_scan_pair(self):
        first = {
            "object_uuid": "uuid-1",
            "radar_timestamp": "100",
            "association_status": "ASSOCIATED",
            "radar_candidate_index": "4",
            "radar_candidate_range_m": "20.5",
            "radar_candidate_azimuth_deg": "180.9",
            "candidate_intensity": "0.8",
            "label_radar_timestamp_delta_ms": "10",
            "label_timestamp": "90",
            "camera_timestamp": "91",
        }
        second = dict(
            first,
            label_radar_timestamp_delta_ms="20",
            label_timestamp="80",
            camera_timestamp="81",
        )
        result = deduplicate_associations([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label_timestamp"], "90")

    def test_conflicting_candidate_for_same_uuid_scan_is_rejected(self):
        first = {
            "object_uuid": "uuid-1",
            "radar_timestamp": "100",
            "association_status": "ASSOCIATED",
            "radar_candidate_index": "4",
            "radar_candidate_range_m": "20.5",
            "radar_candidate_azimuth_deg": "180.9",
            "candidate_intensity": "0.8",
        }
        second = dict(first, radar_candidate_index="5")
        with self.assertRaisesRegex(ValueError, "Conflicting associated candidates"):
            deduplicate_associations([first, second])

    def test_non_associated_rows_do_not_enter_radar_evidence_pairs(self):
        row = {
            "object_uuid": "uuid-1",
            "radar_timestamp": "100",
            "association_status": "NO_RADAR_ASSOCIATION",
        }
        self.assertEqual(deduplicate_associations([row]), [])


if __name__ == "__main__":
    unittest.main()
