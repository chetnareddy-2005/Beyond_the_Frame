import unittest

from multimodal.radar_adapter import RadarAdapter, RadarObservation


class RadarAdapterTests(unittest.TestCase):
    def test_disabled_adapter_has_no_evidence(self):
        adapter = RadarAdapter(enabled=False)
        self.assertFalse(adapter.available)
        self.assertEqual(adapter.get_evidence("V_DRONE_005.mp4", 1, timestamp_sec=0.5), [])

    def test_enabled_adapter_is_unavailable_until_observation_is_supplied(self):
        adapter = RadarAdapter(enabled=True)
        self.assertFalse(adapter.available)
        adapter.ingest(
            RadarObservation(
                timestamp_sec=0.5,
                source_video="V_DRONE_005.mp4",
                target_id="radar-1",
                range=12.0,
                azimuth=30.0,
            )
        )
        self.assertTrue(adapter.available)
        rows = adapter.get_evidence("V_DRONE_005.mp4", 1, timestamp_sec=0.5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].modality, "radar")
        self.assertTrue(rows[0].available)
        self.assertEqual(
            set(rows[0].evidence),
            {
                "target_id",
                "range",
                "azimuth",
                "relative_velocity",
                "sensor_class",
                "sensor_confidence",
            },
        )


if __name__ == "__main__":
    unittest.main()
