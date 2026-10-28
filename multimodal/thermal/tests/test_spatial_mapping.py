import unittest

import numpy as np

from multimodal.thermal.spatial_mapping import (
    Box,
    confidence_gated_thermal_crop,
    padded_box_to_source,
    source_box_to_padded,
    validate_calibration_provenance,
)


class SpatialMappingTests(unittest.TestCase):
    def test_padding_conversion_uses_existing_960_square_video_convention(self):
        padded = Box(645.0, 602.0, 672.0, 636.0)
        source = padded_box_to_source(
            padded,
            source_size=(960, 600),
            padded_size=(960, 960),
        )
        self.assertEqual(source, Box(645.0, 422.0, 672.0, 456.0))
        self.assertEqual(
            source_box_to_padded(
                source,
                source_size=(960, 600),
                padded_size=(960, 960),
            ),
            padded,
        )

    def test_padding_conversion_rejects_box_in_padded_border(self):
        self.assertIsNone(
            padded_box_to_source(
                Box(5, 20, 25, 60),
                source_size=(960, 600),
                padded_size=(960, 960),
            )
        )

    def test_provenance_rejects_missing_topics_and_native_resolution_mismatch(self):
        checked = validate_calibration_provenance(
            rgb_declared_topic="/eo/color/image_mono/invert",
            thermal_declared_topic="/boson/thermal/image_raw/image_8bit",
            observed_topics=(
                "/eo/color/image_color/compressed",
                "/boson/thermal/image_raw",
            ),
            rgb_calibration_size=(960, 600),
            thermal_calibration_size=(960, 600),
            rgb_source_size=(960, 600),
            thermal_source_size=(640, 512),
        )
        self.assertFalse(checked.valid)
        self.assertIn("RGB_CALIBRATION_TOPIC_NOT_IN_RECORDING", checked.reasons)
        self.assertIn("THERMAL_CALIBRATION_TOPIC_NOT_IN_RECORDING", checked.reasons)
        self.assertIn("THERMAL_NATIVE_SIZE_DIFFERS_FROM_CALIBRATION", checked.reasons)

    def test_valid_topic_and_dimension_provenance_passes(self):
        checked = validate_calibration_provenance(
            rgb_declared_topic="/rgb",
            thermal_declared_topic="/thermal",
            observed_topics=("/rgb", "/thermal"),
            rgb_calibration_size=(960, 600),
            thermal_calibration_size=(640, 512),
            rgb_source_size=(960, 600),
            thermal_source_size=(640, 512),
        )
        self.assertTrue(checked.valid)
        self.assertEqual(checked.reasons, ())

    def test_crop_is_rejected_without_confidence_or_below_threshold(self):
        image = np.arange(100, dtype=np.uint8).reshape(10, 10)
        box = Box(2, 2, 6, 6)
        self.assertIsNone(confidence_gated_thermal_crop(image, box, None))
        self.assertIsNone(confidence_gated_thermal_crop(image, box, 0.74))

    def test_crop_is_emitted_only_for_confident_in_bounds_mapping(self):
        image = np.arange(100, dtype=np.uint8).reshape(10, 10)
        crop = confidence_gated_thermal_crop(
            image,
            Box(2, 3, 6, 7),
            0.75,
        )
        self.assertIsNotNone(crop)
        self.assertEqual(crop.shape, (4, 4))
        self.assertIsNone(
            confidence_gated_thermal_crop(image, Box(8, 8, 11, 11), 0.99)
        )


if __name__ == "__main__":
    unittest.main()
