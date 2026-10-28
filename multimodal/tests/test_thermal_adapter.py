import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from multimodal.thermal_adapter import ThermalAdapter


class ThermalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ThermalAdapter(
            enabled=True,
            thermal_source="IR_DRONE_005.mp4",
        )

    def test_disabled_adapter_has_no_evidence(self):
        adapter = ThermalAdapter(enabled=False)
        self.assertFalse(adapter.available)
        self.assertIsNone(adapter.get_frame_reference("V_DRONE_005.mp4", 0, 0.0))
        self.assertIsNone(
            adapter.extract_evidence(
                "V_DRONE_005.mp4",
                1,
                frame_index=0,
                timestamp_sec=0.0,
                thermal_frame=None,
            )
        )

    def test_enabled_without_supplied_source_is_unavailable(self):
        adapter = ThermalAdapter(enabled=True)
        self.assertFalse(adapter.available)
        self.assertIsNone(adapter.get_frame_reference("V_DRONE_005.mp4", 0, 0.0))

    def test_supplied_thermal_source_can_be_represented_without_sync_claim(self):
        adapter = ThermalAdapter(
            enabled=True,
            thermal_source="IR_DRONE_005.mp4",
        )
        self.assertTrue(adapter.available)
        self.assertIsNotNone(adapter.get_frame_reference("V_DRONE_005.mp4", 0, 0.0))
        self.assertIsNone(
            adapter.extract_evidence(
                "V_DRONE_005.mp4",
                1,
                frame_index=0,
                timestamp_sec=0.0,
                thermal_frame=None,
            )
        )
        evidence = adapter.extract_evidence(
            "V_DRONE_005.mp4",
            1,
            frame_index=0,
            thermal_frame=object(),
        )
        self.assertTrue(evidence.available)
        self.assertEqual(evidence.evidence["thermal_source"], "IR_DRONE_005.mp4")
        self.assertNotIn("synchronization_status", evidence.evidence)

    def test_preprocess_grayscale_frame(self):
        frame = np.array([[0, 10], [20, 30]], dtype=np.uint16)
        processed = self.adapter.preprocess_frame(frame)
        np.testing.assert_array_equal(
            processed,
            np.array([[0, 85], [170, 255]], dtype=np.uint8),
        )

    def test_preprocess_three_channel_frame_converts_bgr_to_grayscale(self):
        frame = np.array(
            [[[0, 0, 0], [255, 255, 255]]],
            dtype=np.uint8,
        )
        processed = self.adapter.preprocess_frame(frame)
        self.assertEqual(processed.shape, (1, 2))
        self.assertEqual(processed.dtype, np.uint8)
        self.assertEqual(int(processed[0, 0]), 0)
        self.assertEqual(int(processed[0, 1]), 255)

    def test_normalization_is_uint8_in_zero_to_255_range(self):
        frame = np.array([[3.5, 5.0, 9.5]], dtype=np.float32)
        processed = self.adapter.preprocess_frame(frame)
        self.assertEqual(processed.dtype, np.uint8)
        self.assertGreaterEqual(int(processed.min()), 0)
        self.assertLessEqual(int(processed.max()), 255)
        self.assertEqual(int(processed.min()), 0)
        self.assertEqual(int(processed.max()), 255)

    def test_resize_uses_requested_width_and_height(self):
        frame = np.arange(16, dtype=np.uint8).reshape(4, 4)
        processed = self.adapter.preprocess_frame(frame, target_size=(3, 2))
        self.assertEqual(processed.shape, (2, 3))

    def test_invalid_or_empty_frame_returns_none(self):
        self.assertIsNone(self.adapter.preprocess_frame(None))
        self.assertIsNone(self.adapter.preprocess_frame(np.array([])))
        self.assertIsNone(self.adapter.preprocess_frame(np.zeros((2, 2, 4), dtype=np.uint8)))
        self.assertIsNone(self.adapter.preprocess_frame(np.full((2, 2), np.nan)))

    def test_constant_frame_normalizes_to_zero(self):
        processed = self.adapter.preprocess_frame(np.full((2, 2), 7, dtype=np.uint8))
        np.testing.assert_array_equal(processed, np.zeros((2, 2), dtype=np.uint8))

    def test_frame_statistics_are_json_serializable(self):
        processed = self.adapter.preprocess_frame(np.array([[0, 10], [20, 30]], dtype=np.uint8))
        statistics = self.adapter.build_frame_statistics(processed)
        self.assertEqual(statistics["shape"], [2, 2])
        self.assertEqual(statistics["dtype"], "uint8")
        self.assertEqual(statistics["min"], 0.0)
        self.assertEqual(statistics["max"], 255.0)
        json.dumps(statistics)

    def test_video_frame_read_and_preprocess(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            video_path = Path(temporary_directory) / "thermal_test.mp4"
            writer = cv2.VideoWriter(
                str(video_path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                5.0,
                (16, 16),
                isColor=True,
            )
            if not writer.isOpened():
                self.skipTest("OpenCV MP4 writer is unavailable")
            try:
                first = np.zeros((16, 16, 3), dtype=np.uint8)
                second = np.full((16, 16, 3), 200, dtype=np.uint8)
                writer.write(first)
                writer.write(second)
            finally:
                writer.release()

            read = self.adapter.read_frame(str(video_path), 1)
            self.assertIsNotNone(read)
            self.assertEqual(read.shape[:2], (16, 16))

            processed = self.adapter.process_video_frame(
                str(video_path),
                1,
                target_size=(8, 6),
            )
            self.assertIsNotNone(processed)
            self.assertEqual(processed.shape, (6, 8))
            self.assertEqual(processed.dtype, np.uint8)

    def test_video_reader_returns_none_for_unreadable_source_or_index(self):
        self.assertIsNone(self.adapter.read_frame("missing_video.mp4", 0))
        self.assertIsNone(self.adapter.read_frame("missing_video.mp4", -1))


if __name__ == "__main__":
    unittest.main()
