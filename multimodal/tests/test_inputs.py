import unittest

from multimodal.inputs import ModalityInputs


class ModalityInputsTests(unittest.TestCase):
    def test_rgb_only_is_a_valid_default(self):
        inputs = ModalityInputs(rgb_source="rgb.mp4")
        self.assertTrue(inputs.rgb_available)
        self.assertFalse(inputs.thermal_available)
        self.assertFalse(inputs.radar_available)
        self.assertEqual(inputs.synchronization_status, "rgb_only")

    def test_thermal_input_is_available_but_unsynchronized_by_default(self):
        inputs = ModalityInputs(
            rgb_source="V_DRONE_005.mp4",
            thermal_source="IR_DRONE_005.mp4",
        )
        self.assertTrue(inputs.thermal_available)
        self.assertFalse(inputs.thermal_frame_mapping_verified)
        self.assertEqual(inputs.synchronization_status, "unsynchronized")

    def test_radar_unavailable_is_valid(self):
        inputs = ModalityInputs(rgb_source="rgb.mp4")
        self.assertFalse(inputs.radar_available)

    def test_empty_rgb_source_is_rejected(self):
        with self.assertRaises(ValueError):
            ModalityInputs(rgb_source=" ")

    def test_mapping_cannot_be_verified_without_thermal_input(self):
        with self.assertRaises(ValueError):
            ModalityInputs(
                rgb_source="rgb.mp4",
                thermal_frame_mapping_verified=True,
            )


if __name__ == "__main__":
    unittest.main()
