import unittest

from multimodal.contracts import EvidenceRecord
from multimodal.track_association import associate_multimodal_tracks, associate_track


class TrackAssociationTests(unittest.TestCase):
    def test_source_scoped_identity_prevents_cross_video_merge(self):
        evidence = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "rgb",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"label": "Drone"},
                available=True,
            ),
            EvidenceRecord(
                "V_DRONE_013.mp4",
                1,
                "rgb",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"label": "Drone"},
                available=True,
            ),
        ]
        self.assertEqual(len(associate_track("V_DRONE_005.mp4", 1, evidence)), 1)
        self.assertEqual(len(associate_track("V_DRONE_013.mp4", 1, evidence)), 1)

    def test_same_source_and_compatible_frame_associates(self):
        evidence = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "thermal",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"intensity_mean": 0.8},
                available=True,
            ),
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "radar",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"range": 12.0},
                available=True,
            ),
        ]
        matched = associate_track("V_DRONE_005.mp4", 1, evidence, frame_index=10, timestamp_sec=1.0)
        self.assertEqual(len(matched), 2)

    def test_mismatched_source_is_not_associated(self):
        evidence = [
            EvidenceRecord(
                "V_DRONE_013.mp4",
                1,
                "thermal",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"intensity_mean": 0.8},
                available=True,
            )
        ]
        self.assertEqual(associate_track("V_DRONE_005.mp4", 1, evidence), [])

    def test_unavailable_modality_stays_unavailable(self):
        evidence = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "thermal",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={},
                available=False,
            )
        ]
        self.assertEqual(associate_track("V_DRONE_005.mp4", 1, evidence), [])

    def test_multimodal_template_returns_all_available_counts(self):
        rgb = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "rgb",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"label": "Drone"},
                available=True,
            )
        ]
        thermal = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "thermal",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"intensity_mean": 0.8},
                available=True,
            )
        ]
        radar = [
            EvidenceRecord(
                "V_DRONE_005.mp4",
                1,
                "radar",
                frame_index=10,
                timestamp_sec=1.0,
                evidence={"range": 12.0},
                available=True,
            )
        ]
        grouped = associate_multimodal_tracks(1, "V_DRONE_005.mp4", rgb, thermal, radar)
        self.assertEqual(len(grouped["rgb"]), 1)
        self.assertEqual(len(grouped["thermal"]), 1)
        self.assertEqual(len(grouped["radar"]), 1)


if __name__ == "__main__":
    unittest.main()
