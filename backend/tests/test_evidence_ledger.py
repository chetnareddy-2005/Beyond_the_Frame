"""Unit tests for the Evidence Ledger data contracts and provenance integrity."""

import tempfile
import unittest
from pathlib import Path

from backend.app import ProjectData, ThermalRunData
from backend.config import Settings
from temporal.temporal_consensus import evaluate_track_consensus


class EvidenceLedgerTests(unittest.TestCase):
    def test_thermal_evidence_ledger_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracks_file = Path(tmp) / "tracks.csv"
            header = "source,frame,source_frame_index,timestamp_sec,track_id,class_id,class_name,confidence,x1,y1,x2,y2\n"
            rows = (
                "thermal.mp4,1,0,0.0,10,1,Drone,0.85,10,10,50,50\n"
                "thermal.mp4,2,1,0.04,10,1,Drone,0.88,12,12,52,52\n"
                "thermal.mp4,5,4,0.16,10,1,Drone,0.82,15,15,55,55\n"
            )
            tracks_file.write_text(header + rows, encoding="utf-8")

            thermal_data = ThermalRunData(
                tracks_file,
                run_manifest={"run_id": "RUN_TEST_THERMAL"},
                detector_manifest={
                    "detector_checkpoint_name": "thermal_bird_drone_best.pt",
                    "detector_checkpoint_sha256": "ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477",
                    "detector_model_architecture": "yv5_gh (GhostNet + AFNO2D)",
                    "detector_image_size": [640, 640],
                    "detector_confidence_threshold": 0.25,
                    "detector_iou_threshold": 0.45,
                    "detector_device": "cpu",
                    "detector_preprocessing": "thermal_adapter_grayscale_minmax",
                },
            )
            thermal_data.run_status = "completed"

            detail = thermal_data.track_detail("10")
            self.assertIn("evidence_ledger", detail)
            ledger = detail["evidence_ledger"]

            self.assertEqual(ledger["run_id"], "RUN_TEST_THERMAL")
            self.assertEqual(ledger["track_id"], "10")
            self.assertEqual(ledger["observation_count"], 3)
            self.assertEqual(ledger["class_mapping"], {"0": "Bird", "1": "Drone"})
            self.assertEqual(ledger["model_provenance"]["checkpoint_sha256"], "ad2edb7b6a310a42df86262ebbf5d06736cb1fbe0efe028d9b1b92bd4eb73477")
            self.assertEqual(len(ledger["observation_gaps"]), 1)
            self.assertEqual(ledger["observation_gaps"][0]["missing_source_frame_count"], 2)
            self.assertEqual(ledger["temporal_consensus"]["status"], "completed")
            self.assertEqual(ledger["temporal_consensus"]["result"]["consensus_label"], "Drone")
            self.assertEqual(
                ledger["temporal_consensus"]["source"],
                "derived_from_detector_observations",
            )
            self.assertEqual(
                ledger["temporal_consensus"]["result"]["supporting_frame_indexes"],
                [1, 2, 5],
            )
            self.assertEqual(ledger["selective_verification"]["status"], "not_run")

    def test_rgb_evidence_ledger_contract(self):
        settings = Settings(project_root=Path(__file__).resolve().parents[2])
        data = ProjectData(settings)
        detail = data.track_detail("212")
        self.assertIn("evidence_ledger", detail)
        ledger = detail["evidence_ledger"]

        self.assertEqual(ledger["track_id"], "212")
        self.assertGreater(ledger["observation_count"], 0)
        self.assertEqual(ledger["class_mapping"], {"0": "Bird", "1": "Drone"})
        self.assertEqual(ledger["temporal_consensus"]["status"], "completed")
        self.assertIn(ledger["temporal_consensus"]["result"]["consensus_label"], {"Drone", "Bird", "Uncertain"})
        self.assertIn(ledger["selective_verification"]["status"], {"completed", "not_run", "skipped"})

    def test_ledger_preserves_raw_detector_observations(self):
        obs = [
            {"frame": 1, "source_frame_index": 0, "class_id": 1, "class": "Drone", "confidence": 0.90},
            {"frame": 2, "source_frame_index": 1, "class_id": 0, "class": "Bird", "confidence": 0.85},
            {"frame": 3, "source_frame_index": 2, "class_id": 1, "class": "Drone", "confidence": 0.88},
        ]
        consensus = evaluate_track_consensus(obs)
        self.assertEqual(consensus["consensus_label"], "Uncertain")
        # Raw inputs remain completely unchanged
        self.assertEqual(obs[0]["class"], "Drone")
        self.assertEqual(obs[1]["class"], "Bird")
        self.assertEqual(obs[2]["class"], "Drone")


if __name__ == "__main__":
    unittest.main()
