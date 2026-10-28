import builtins
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import torch
from PIL import Image

from evidence.frame_selector import acquire_evidence, acquire_evidence_for_tracks, select_representative_frames
from vlm.prompts import ALLOWED_LABELS, CLASSIFICATION_PROMPT
from vlm.vlm_adapter import MockVLM, SmolVLM, write_vlm_results


class RecordingVLM:
    def __init__(self):
        self.paths = []

    def verify(self, image_path):
        self.paths.append(Path(image_path))
        return {"label": "Bird", "confidence": 0.8, "reason": "Synthetic test response."}


class BatchRecordingVLM:
    def __init__(self):
        self.batches = []

    def verify_batch(self, image_paths):
        self.batches.append(list(image_paths))
        return [
            {
                "label": "Bird",
                "confidence": (len(self.batches) - 1) * 0.2 + index * 0.1 + 0.5,
                "reason": f"Result {len(self.batches)}-{index}.",
            }
            for index, _ in enumerate(image_paths)
        ]


class FakeBatchInputs(dict):
    def to(self, device):
        return self


class FakeBatchProcessor:
    def apply_chat_template(self, conversations, **kwargs):
        self.conversations = conversations
        return FakeBatchInputs({"input_ids": torch.tensor([[1, 2], [1, 2]])})

    def decode(self, generated_ids, skip_special_tokens=True):
        return "Bird."


class FakeBatchModel:
    def __init__(self):
        self.generate_calls = 0

    def generate(self, **kwargs):
        self.generate_calls += 1
        return SimpleNamespace(
            sequences=torch.tensor([[1, 2, 3], [1, 2, 3]]),
            scores=(
                torch.tensor(
                    [
                        [0.0, 0.0, 0.0, 3.0],
                        [3.0, 0.0, 0.0, 0.0],
                    ]
                ),
            ),
        )


class PhaseFiveTests(unittest.TestCase):
    def test_adapter_import_does_not_require_transformers_until_model_initialization(self):
        adapter_path = Path(__file__).resolve().parents[1] / "vlm_adapter.py"
        spec = importlib.util.spec_from_file_location(
            "_vlm_adapter_without_transformers",
            adapter_path,
        )
        self.assertIsNotNone(spec)
        adapter = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__

        def import_without_transformers(name, *args, **kwargs):
            if name == "transformers" or name.startswith("transformers."):
                raise ModuleNotFoundError("transformers is unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_transformers):
            spec.loader.exec_module(adapter)
            self.assertTrue(callable(adapter.MockVLM))
            with self.assertRaises(ModuleNotFoundError):
                adapter.SmolVLM(model_id="test-model", device="cpu")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video_path = self.root / "sample.mp4"
        writer = cv2.VideoWriter(
            str(self.video_path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (64, 64)
        )
        if not writer.isOpened():
            self.temp_dir.cleanup()
            self.fail("OpenCV could not create the small synthetic test video")
        for frame_number in range(1, 10):
            frame = np.full((64, 64, 3), (frame_number * 10, 40, 100), dtype=np.uint8)
            writer.write(frame)
        writer.release()

        self.tracks_csv = self.root / "tracks.csv"
        self.reliability_csv = self.root / "reliability_results.csv"
        self._write_inputs()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_inputs(self):
        fields = (
            "source", "frame", "track_id", "class_id", "class_name",
            "confidence", "x1", "y1", "x2", "y2",
        )
        rows = []
        for frame in range(1, 10):
            rows.append(
                {
                    "source": str(self.video_path),
                    "frame": frame,
                    "track_id": 1,
                    "class_id": 0,
                    "class_name": "Bird",
                    "confidence": {3: 0.95, 5: 0.9, 9: 0.98}.get(frame, 0.6),
                    "x1": 10, "y1": 10, "x2": 30, "y2": 30,
                }
            )
        for frame in (1, 9):
            rows.append(
                {
                    "source": str(self.video_path),
                    "frame": frame,
                    "track_id": 2,
                    "class_id": 1,
                    "class_name": "Drone",
                    "confidence": 0.7,
                    "x1": 32, "y1": 32, "x2": 48, "y2": 48,
                }
            )
        rows.append(
            {
                "source": str(self.video_path),
                "frame": 5,
                "track_id": 3,
                "class_id": 0,
                "class_name": "Bird",
                "confidence": 0.99,
                "x1": 10, "y1": 10, "x2": 30, "y2": 30,
            }
        )
        with self.tracks_csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

        with self.reliability_csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=("track_id", "status"))
            writer.writeheader()
            writer.writerows(
                (
                    {"track_id": 1, "status": "UNCERTAIN"},
                    {"track_id": 2, "status": "UNCERTAIN"},
                    {"track_id": 3, "status": "CONFIDENT"},
                )
            )

    def test_confident_track_does_not_trigger_vlm(self):
        vlm = RecordingVLM()
        selected, results = acquire_evidence(
            self.video_path,
            self.tracks_csv,
            self.reliability_csv,
            self.root / "output",
            vlm=vlm,
        )

        self.assertNotIn("3", {row["track_id"] for row in selected})
        self.assertNotIn("3", {row["track_id"] for row in results})
        self.assertEqual(len(vlm.paths), len(selected))

    def test_drone_candidate_gets_one_vlm_frame_even_when_reliability_is_confident(self):
        reliability = self.root / "all_confident.csv"
        reliability.write_text(
            "track_id,status\n1,CONFIDENT\n2,CONFIDENT\n3,CONFIDENT\n",
            encoding="utf-8",
        )
        selected = select_representative_frames(
            self.tracks_csv,
            reliability,
            self.video_path,
            max_frames_per_track=1,
            include_drone_candidates=True,
        )

        # Track 2 is detector-classified Drone and must get one semantic check
        # even though temporal/reliability confidence says the class is stable.
        self.assertEqual([row["track_id"] for row in selected], ["2"])
        self.assertEqual(selected[0]["selection_reason"], "track representative")
        self.assertEqual(selected[0]["frame"], 1)

    def test_one_frame_per_track_bounds_vlm_evidence_for_uncertain_tracks(self):
        selected = select_representative_frames(
            self.tracks_csv,
            self.reliability_csv,
            self.video_path,
            max_frames_per_track=1,
            include_drone_candidates=True,
        )
        counts = {}
        for row in selected:
            counts[row["track_id"]] = counts.get(row["track_id"], 0) + 1
        self.assertTrue(counts)
        self.assertTrue(all(count == 1 for count in counts.values()))

    def test_uncertain_tracks_select_at_most_three_ordered_regions(self):
        selected = select_representative_frames(
            self.tracks_csv, self.reliability_csv, self.video_path
        )
        track_one = [row for row in selected if row["track_id"] == "1"]

        self.assertEqual(len(track_one), 3)
        self.assertEqual([row["selection_reason"] for row in track_one], [
            "early segment", "middle segment", "late segment"
        ])
        self.assertEqual([row["frame"] for row in track_one], [3, 5, 9])
        self.assertLessEqual(
            max(sum(row["track_id"] == str(track_id) for row in selected) for track_id in (1, 2)),
            3,
        )

    def test_temporal_uncertainty_selects_evidence_even_when_reliability_is_confident(self):
        reliability = self.root / "confident_reliability.csv"
        with reliability.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=("track_id", "status"))
            writer.writeheader()
            writer.writerows(
                {"track_id": track_id, "status": "CONFIDENT"}
                for track_id in (1, 2, 3)
            )
        consensus = self.root / "temporal_consensus.csv"
        consensus.write_text(
            "track_id,consensus_label,decision_status\n"
            "3,Uncertain,uncertain\n",
            encoding="utf-8",
        )

        selected = select_representative_frames(
            self.tracks_csv,
            reliability,
            self.video_path,
            consensus_csv=consensus,
        )

        self.assertEqual({row["track_id"] for row in selected}, {"3"})
        self.assertEqual([row["frame"] for row in selected], [5])

    def test_crop_extraction_saves_padded_object_crop(self):
        output = self.root / "crop_output"
        selected, _ = acquire_evidence(
            self.video_path, self.tracks_csv, self.reliability_csv, output
        )
        row = next(row for row in selected if row["track_id"] == "1")
        crop_path = output / row["crop_path"]
        crop = cv2.imread(str(crop_path))

        self.assertTrue(crop_path.is_file())
        self.assertEqual(crop.shape[:2], (24, 24))

    def test_mock_vlm_returns_structured_allowed_evidence(self):
        crop = self.root / "crop.jpg"
        self.assertTrue(cv2.imwrite(str(crop), np.zeros((8, 8, 3), dtype=np.uint8)))

        result = MockVLM().verify(crop)

        self.assertIn(result["label"], ALLOWED_LABELS)
        self.assertEqual(result["label"], "Unknown")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(result["reason"])
        self.assertIn("Unknown", CLASSIFICATION_PROMPT)

    def test_vlm_progress_reports_verified_evidence_count(self):
        rows = [
            {"track_id": str(index), "frame": index, "crop_path": f"crop_{index}.jpg"}
            for index in (1, 2)
        ]
        progress = []

        results = write_vlm_results(
            rows,
            RecordingVLM(),
            self.root / "vlm_results.csv",
            self.root,
            progress_callback=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(progress, [(2, 2)])

    def test_two_selected_evidence_rows_use_one_batch_and_keep_output_contract(self):
        rows = [
            {"track_id": str(index), "frame": index, "crop_path": f"crop_{index}.jpg"}
            for index in (1, 2)
        ]
        vlm = BatchRecordingVLM()
        progress = []

        results = write_vlm_results(
            rows,
            vlm,
            self.root / "two_batch_results.csv",
            self.root,
            progress_callback=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual([len(batch) for batch in vlm.batches], [2])
        self.assertEqual(
            [row["crop_path"] for row in results],
            ["crop_1.jpg", "crop_2.jpg"],
        )
        self.assertEqual(
            tuple(results[0]),
            ("track_id", "frame", "crop_path", "label", "confidence", "reason"),
        )
        self.assertEqual(
            [row["confidence"] for row in results],
            [0.5, 0.6],
        )
        self.assertEqual(progress, [(2, 2)])

    def test_three_selected_evidence_rows_use_batch_of_two_then_one(self):
        rows = [
            {"track_id": str(index), "frame": index, "crop_path": f"crop_{index}.jpg"}
            for index in (1, 2, 3)
        ]
        vlm = BatchRecordingVLM()
        progress = []

        results = write_vlm_results(
            rows,
            vlm,
            self.root / "odd_batch_results.csv",
            self.root,
            progress_callback=lambda current, total: progress.append((current, total)),
        )

        self.assertEqual([len(batch) for batch in vlm.batches], [2, 1])
        self.assertEqual(
            [row["crop_path"] for row in results],
            ["crop_1.jpg", "crop_2.jpg", "crop_3.jpg"],
        )
        self.assertEqual(
            [row["confidence"] for row in results],
            [0.5, 0.6, 0.7],
        )
        self.assertEqual(progress, [(2, 3), (3, 3)])

    def test_smolvlm_verify_batch_uses_one_generate_and_row_specific_scores(self):
        crops = [self.root / "batch_1.png", self.root / "batch_2.png"]
        for crop in crops:
            self.assertTrue(
                cv2.imwrite(str(crop), np.zeros((8, 8, 3), dtype=np.uint8))
            )
        vlm = SmolVLM.__new__(SmolVLM)
        vlm.device = "cpu"
        vlm.processor = FakeBatchProcessor()
        vlm.model = FakeBatchModel()

        results = vlm.verify_batch(crops)

        score_rows = torch.tensor(
            [[0.0, 0.0, 0.0, 3.0], [3.0, 0.0, 0.0, 0.0]]
        )
        expected_first = torch.softmax(score_rows[0], dim=-1)[3].item()
        expected_second = torch.softmax(score_rows[1], dim=-1)[3].item()
        self.assertEqual(vlm.model.generate_calls, 1)
        self.assertEqual(
            [result["label"] for result in results],
            ["Bird", "Bird"],
        )
        self.assertNotEqual(results[0]["confidence"], results[1]["confidence"])
        self.assertAlmostEqual(results[0]["confidence"], expected_first)
        self.assertAlmostEqual(results[1]["confidence"], expected_second)
        self.assertTrue(all(result["reason"] == "Bird." for result in results))
        self.assertEqual(
            [len(conversation) for conversation in vlm.processor.conversations],
            [1, 1],
        )
        self.assertTrue(
            all(
                isinstance(conversation[0]["content"][0]["path"], Image.Image)
                for conversation in vlm.processor.conversations
            )
        )

    def test_smolvlm_verify_preserves_single_conversation_shape_and_result(self):
        crop = self.root / "single.png"
        self.assertTrue(
            cv2.imwrite(str(crop), np.zeros((8, 8, 3), dtype=np.uint8))
        )
        vlm = SmolVLM.__new__(SmolVLM)
        vlm.device = "cpu"
        vlm.processor = FakeBatchProcessor()
        vlm.model = FakeBatchModel()

        result = vlm.verify(crop)

        self.assertEqual(vlm.model.generate_calls, 1)
        self.assertEqual(len(vlm.processor.conversations), 1)
        self.assertIsInstance(vlm.processor.conversations[0], dict)
        self.assertIsInstance(
            vlm.processor.conversations[0]["content"][0]["path"],
            Image.Image,
        )
        self.assertEqual(
            set(result),
            {"label", "confidence", "reason"},
        )
        self.assertEqual(result["label"], "Bird")

    def test_parse_response_uses_batch_row_for_confidence_fallback(self):
        output = SimpleNamespace(
            scores=(
                torch.tensor(
                    [
                        [0.0, 3.0, 0.0],
                        [3.0, 0.0, 0.0],
                    ]
                ),
            )
        )
        generated_ids = torch.tensor([1])

        first = SmolVLM._parse_response(
            "Bird.", output, generated_ids
        )
        second = SmolVLM._parse_response(
            "Bird.", output, generated_ids, batch_index=1
        )

        expected_first = torch.softmax(output.scores[0][0], dim=-1)[1].item()
        expected_second = torch.softmax(output.scores[0][1], dim=-1)[1].item()
        self.assertEqual(first, ("Bird", expected_first, "Bird."))
        self.assertEqual(second, ("Bird", expected_second, "Bird."))
        self.assertNotEqual(first[1], second[1])

    def test_parse_response_default_batch_index_preserves_single_image_behavior(self):
        output = SimpleNamespace(
            scores=(torch.tensor([[0.0, 2.0], [2.0, 0.0]]),)
        )
        generated_ids = torch.tensor([1])

        parsed_default = SmolVLM._parse_response(
            "Bird.", output, generated_ids
        )
        parsed_first_row = SmolVLM._parse_response(
            "Bird.", output, generated_ids, batch_index=0
        )

        self.assertEqual(parsed_default, parsed_first_row)

    def test_parse_response_structured_fallback_uses_batch_row_for_confidence(self):
        output = SimpleNamespace(
            scores=(torch.tensor([[0.0, 3.0], [3.0, 0.0]]),)
        )
        generated_ids = torch.tensor([1])

        parsed = SmolVLM._parse_response(
            "Bird|invalid|Bird.", output, generated_ids, batch_index=1
        )

        expected = torch.softmax(output.scores[0][1], dim=-1)[1].item()
        self.assertEqual(parsed, ("Bird", expected, "Bird."))

    def test_tracks_and_outputs_remain_independent(self):
        output = self.root / "output"
        selected, results = acquire_evidence(
            self.video_path, self.tracks_csv, self.reliability_csv, output
        )
        paths_by_track = {
            track_id: {row["crop_path"] for row in selected if row["track_id"] == track_id}
            for track_id in ("1", "2")
        }

        self.assertTrue(paths_by_track["1"])
        self.assertTrue(paths_by_track["2"])
        self.assertTrue(paths_by_track["1"].isdisjoint(paths_by_track["2"]))
        self.assertEqual(
            {row["track_id"] for row in results},
            {row["track_id"] for row in selected},
        )
        with (output / "selected_evidence.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            selected_csv = list(csv.DictReader(source))
        with (output / "vlm_results.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            vlm_csv = list(csv.DictReader(source))
        self.assertEqual(len(selected_csv), len(selected))
        self.assertEqual(len(vlm_csv), len(results))

    def test_acquire_evidence_for_tracks_bridge(self):
        output = self.root / "bridge_output"

        observations = []
        with self.tracks_csv.open(newline="", encoding="utf-8") as source:
            observations = list(csv.DictReader(source))

        reliability_statuses = {
            "1": "UNCERTAIN",
            "2": "UNCERTAIN",
            "3": "CONFIDENT",
        }

        vlm = RecordingVLM()
        selected, results = acquire_evidence_for_tracks(
            self.video_path,
            observations,
            reliability_statuses,
            output,
            vlm=vlm,
        )

        track_ids = {row["track_id"] for row in selected}

        # CASE 1: CONFIDENT track (3) is excluded from evidence and VLM invocation
        self.assertNotIn("3", track_ids)
        self.assertNotIn("3", {row["track_id"] for row in results})

        # CASE 2 & CASE 5: UNCERTAIN tracks (1 & 2) trigger bridge; tracks are isolated
        self.assertIn("1", track_ids)
        self.assertIn("2", track_ids)

        # CASE 3: UNCERTAIN track with multiple observations uses early/middle/late (max 3 frames)
        track_one_selected = [row for row in selected if row["track_id"] == "1"]
        self.assertEqual(len(track_one_selected), 3)
        self.assertEqual(
            [row["selection_reason"] for row in track_one_selected],
            ["early segment", "middle segment", "late segment"],
        )

        # CASE 4: VLM result follows exact VLM_FIELDS contract
        for res in results:
            for field in ("track_id", "frame", "crop_path", "label", "confidence", "reason"):
                self.assertIn(field, res)
            self.assertIn(res["label"], ALLOWED_LABELS)
            self.assertGreaterEqual(res["confidence"], 0.0)
            self.assertLessEqual(res["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
