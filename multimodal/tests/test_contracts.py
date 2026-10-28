import unittest

from multimodal.contracts import EvidenceRecord, FrameReference, TrackIdentity


class ContractsTests(unittest.TestCase):
    def test_valid_frame_reference(self):
        frame = FrameReference("V_DRONE_005.mp4", 0, timestamp_sec=1.25)
        self.assertEqual(frame.source_video, "V_DRONE_005.mp4")
        self.assertEqual(frame.frame_index, 0)
        self.assertEqual(frame.timestamp_sec, 1.25)

    def test_negative_frame_index_rejected(self):
        with self.assertRaises(ValueError):
            FrameReference("V_DRONE_005.mp4", -1)

    def test_valid_track_identity(self):
        track = TrackIdentity("V_DRONE_005.mp4", 1)
        self.assertEqual(track.composite_key, ("V_DRONE_005.mp4", 1))

    def test_track_identity_is_source_scoped(self):
        left = TrackIdentity("V_DRONE_005.mp4", 1)
        right = TrackIdentity("V_DRONE_013.mp4", 1)
        self.assertNotEqual(left.composite_key, right.composite_key)

    def test_evidence_record_serializable(self):
        record = EvidenceRecord(
            source_video="V_DRONE_005.mp4",
            track_id=1,
            modality="thermal",
            frame_index=2,
            timestamp_sec=0.5,
            evidence={"thermal_visibility": "high"},
            available=True,
        )
        self.assertTrue(record.available)
        self.assertEqual(record.modality, "thermal")


if __name__ == "__main__":
    unittest.main()
