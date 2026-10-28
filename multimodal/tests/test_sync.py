import unittest

from multimodal.contracts import FrameReference
from multimodal.sync import synchronize_frames


class SyncTests(unittest.TestCase):
    def test_equal_indices_do_not_imply_synchronization(self):
        rgb_frames = [
            FrameReference("V_DRONE_005.mp4", 0),
            FrameReference("V_DRONE_005.mp4", 1),
            FrameReference("V_DRONE_005.mp4", 2),
        ]
        thermal_frames = [
            FrameReference("V_DRONE_005.mp4", 0),
            FrameReference("V_DRONE_005.mp4", 1),
            FrameReference("V_DRONE_005.mp4", 2),
        ]
        synchronized = synchronize_frames(rgb_frames, thermal_frames=thermal_frames)
        self.assertEqual(synchronized[0].synchronization_status, "unsynchronized")
        self.assertTrue(synchronized[0].thermal_available)
        self.assertIsNone(synchronized[0].thermal)
        self.assertEqual(synchronized[1].synchronization_status, "unsynchronized")

    def test_cross_source_same_indices_do_not_match_by_default(self):
        rgb_frames = [FrameReference("V_DRONE_005.mp4", 7)]
        thermal_frames = [FrameReference("IR_DRONE_005.mp4", 7)]
        synchronized = synchronize_frames(rgb_frames, thermal_frames=thermal_frames)
        self.assertIsNone(synchronized[0].thermal)
        self.assertTrue(synchronized[0].thermal_available)
        self.assertEqual(synchronized[0].synchronization_status, "unsynchronized")

    def test_verified_cross_source_frame_mapping_allows_index_match(self):
        rgb_frames = [FrameReference("V_DRONE_005.mp4", 7)]
        thermal_frames = [FrameReference("IR_DRONE_005.mp4", 7)]
        synchronized = synchronize_frames(
            rgb_frames,
            thermal_frames=thermal_frames,
            index_mapping_verified=True,
        )
        self.assertEqual(synchronized[0].synchronization_status, "rgb_thermal")
        self.assertIsNotNone(synchronized[0].thermal)

    def test_timestamp_sync_works_across_different_sources(self):
        rgb_frames = [FrameReference("V_DRONE_005.mp4", 10, 2.0)]
        thermal_frames = [FrameReference("IR_DRONE_005.mp4", 26, 2.05)]
        synchronized = synchronize_frames(
            rgb_frames,
            thermal_frames=thermal_frames,
            max_time_delta_sec=0.1,
        )
        self.assertEqual(synchronized[0].synchronization_status, "rgb_thermal")
        self.assertEqual(synchronized[0].thermal, thermal_frames[0])

    def test_missing_thermal_remains_missing(self):
        rgb_frames = [
            FrameReference("V_DRONE_005.mp4", 0, 0.0),
            FrameReference("V_DRONE_005.mp4", 1, 0.5),
        ]
        thermal_frames = [FrameReference("V_DRONE_005.mp4", 0, 0.0)]
        synchronized = synchronize_frames(rgb_frames, thermal_frames=thermal_frames)
        self.assertIsNone(synchronized[1].thermal)
        self.assertEqual(synchronized[1].synchronization_status, "unsynchronized")
        self.assertTrue(synchronized[1].thermal_available)

    def test_timestamp_sync_uses_tolerance(self):
        rgb_frames = [FrameReference("V_DRONE_005.mp4", 0, 1.0)]
        thermal_frames = [FrameReference("IR_DRONE_005.mp4", 0, 1.05)]
        synchronized = synchronize_frames(rgb_frames, thermal_frames=thermal_frames, max_time_delta_sec=0.1)
        self.assertIsNotNone(synchronized[0].thermal)

    def test_distant_timestamp_remains_unmatched(self):
        rgb_frames = [FrameReference("V_DRONE_005.mp4", 0, 1.0)]
        thermal_frames = [FrameReference("IR_DRONE_005.mp4", 0, 1.5)]
        synchronized = synchronize_frames(rgb_frames, thermal_frames=thermal_frames, max_time_delta_sec=0.1)
        self.assertIsNone(synchronized[0].thermal)
        self.assertEqual(synchronized[0].synchronization_status, "unsynchronized")


if __name__ == "__main__":
    unittest.main()
