import unittest

import numpy as np

from tools.extract_caltech_rgb_video import (
    build_manifest_row,
    iter_indexed_messages,
    pad_to_square,
    validate_frame_counts,
)


class CaltechRgbVideoTests(unittest.TestCase):
    def test_square_padding_preserves_aspect_ratio_and_pixels(self):
        frame = np.zeros((2, 4, 3), dtype=np.uint8)
        frame[:, :, 0] = np.arange(4, dtype=np.uint8)

        padded = pad_to_square(frame)

        self.assertEqual(padded.shape, (4, 4, 3))
        self.assertEqual(padded[1:3, :, :].tolist(), frame.tolist())
        self.assertEqual(padded[0, 0].tolist(), [114, 114, 114])
        self.assertEqual(padded[3, 0].tolist(), [114, 114, 114])

    def test_video_frame_numbering_starts_at_one(self):
        frame = np.zeros((2, 4, 3), dtype=np.uint8)
        row = build_manifest_row(0, 1_234_567_890, frame, pad_to_square(frame))

        self.assertEqual(row["video_frame"], 1)
        self.assertEqual(row["rgb_message_index"], 0)

    def test_manifest_contains_required_fields_and_exact_ros_timestamp(self):
        original = np.zeros((3, 5, 3), dtype=np.uint8)
        padded = pad_to_square(original)

        row = build_manifest_row(6, 1_234_567_890, original, padded)

        self.assertEqual(
            set(row),
            {
                "video_frame",
                "rgb_message_index",
                "rgb_timestamp_sec",
                "original_width",
                "original_height",
                "padded_width",
                "padded_height",
            },
        )
        self.assertEqual(row["video_frame"], 7)
        self.assertEqual(row["rgb_timestamp_sec"], "1.234567890")
        self.assertEqual(row["original_width"], 5)
        self.assertEqual(row["original_height"], 3)
        self.assertEqual(row["padded_width"], 5)
        self.assertEqual(row["padded_height"], 5)

    def test_mismatched_video_manifest_and_message_counts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Frame count mismatch"):
            validate_frame_counts(3, 2, 3)

        validate_frame_counts(3, 3, 3)

    def test_indexing_does_not_reorder_or_drop_source_messages(self):
        messages = [
            (30, b"third-in-time"),
            (10, b"first-in-time"),
            (20, b"second-in-time"),
        ]

        indexed = list(iter_indexed_messages(messages))

        self.assertEqual(
            indexed,
            [
                (0, 30, b"third-in-time"),
                (1, 10, b"first-in-time"),
                (2, 20, b"second-in-time"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
