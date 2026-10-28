import json
import unittest
from pathlib import Path

import cv2

from multimodal.radar_adapter import RadarAdapter, RadarObservation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = (
    PROJECT_ROOT
    / "datasets"
    / "radar"
    / "tsms_drone"
    / "fmcw"
    / "28m"
    / "Matrice 30"
)
MANIFEST_PATH = DATA_ROOT / "metadata" / "source_manifest.json"
UNASSOCIATED_TRACK_ID = -1


class RadarRealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
            cls.manifest = json.load(manifest_file)

    def test_official_samples_are_readable_and_have_matching_raw_data(self):
        self.assertEqual(
            self.manifest["dataset_doi"],
            "10.25452/figshare.plus.30027313",
        )
        self.assertEqual(self.manifest["archive_file_id"], 57678868)
        self.assertEqual(self.manifest["drone"], "Matrice 30")
        self.assertEqual(self.manifest["distance_condition_m"], 28)
        self.assertEqual(len(self.manifest["samples"]), 20)
        self.assertIn("not temporally matched", self.manifest["note"])

        images_dir = DATA_ROOT / "images"
        raw_data_dir = DATA_ROOT / "metadata" / "raw_mat"
        for sample in self.manifest["samples"]:
            image_path = images_dir / sample["image"]
            raw_path = raw_data_dir / sample["raw_mat"]
            self.assertEqual(image_path.stem, raw_path.stem)
            self.assertTrue(image_path.is_file(), image_path)
            self.assertGreater(image_path.stat().st_size, 0)
            self.assertTrue(raw_path.is_file(), raw_path)
            self.assertGreater(raw_path.stat().st_size, 0)

            image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
            self.assertIsNotNone(image, image_path)
            self.assertEqual(image.shape, (227, 227, 3))
            self.assertEqual(image.dtype.name, "uint8")

    def test_radar_adapter_records_real_image_reference_without_fake_timing(self):
        sample = self.manifest["samples"][0]
        image_path = DATA_ROOT / "images" / sample["image"]
        source = f"TSMS-Drone:{self.manifest['drone']}:{self.manifest['distance_condition_m']}m"
        observation = RadarObservation(
            timestamp_sec=None,
            source_video=source,
            sensor_class="FMCW Range-Doppler image",
            frame_index=sample["index"],
            sample_path=str(image_path),
            distance_condition_m=self.manifest["distance_condition_m"],
        )

        adapter = RadarAdapter(enabled=True)
        adapter.ingest(observation)
        evidence = adapter.get_evidence(
            source_video=source,
            track_id=UNASSOCIATED_TRACK_ID,
            frame_index=sample["index"],
        )

        self.assertTrue(adapter.available)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].modality, "radar")
        self.assertEqual(evidence[0].track_id, UNASSOCIATED_TRACK_ID)
        self.assertEqual(evidence[0].frame_index, sample["index"])
        self.assertIsNone(evidence[0].timestamp_sec)
        self.assertEqual(
            adapter.get_evidence(
                source_video=source,
                track_id=UNASSOCIATED_TRACK_ID,
                timestamp_sec=0.0,
            ),
            [],
        )
        self.assertEqual(evidence[0].evidence["sample_path"], str(image_path))
        self.assertEqual(evidence[0].evidence["distance_condition_m"], 28)
        self.assertIsNone(evidence[0].evidence["range"])
        self.assertIsNone(evidence[0].evidence["relative_velocity"])


if __name__ == "__main__":
    unittest.main()
