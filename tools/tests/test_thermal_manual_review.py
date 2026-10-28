import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tools.thermal_manual_review import (
    ANNOTATION_FIELDS,
    build_development_index,
    check_leakage,
    import_annotations,
    prepare_review_bundle,
)


class ThermalManualReviewTests(unittest.TestCase):
    def _manifest(self, root, *, video_hash="a" * 64, frame_hash="b" * 64):
        path = root / "private" / "curator.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "evaluation_eligibility": "exploratory_not_independent",
                    "frame_representation": "unmodified decoded frame PNG",
                    "review_bundle": str(root / "reviewer-bundle"),
                    "records": [
                        {
                            "blind_id": "RV-a1",
                            "source_video_id": "VIDEO-a",
                            "source_video_path": "C:/private/thermal/secret_clip.mp4",
                            "source_video_name": "secret_clip.mp4",
                            "source_video_sha256": video_hash,
                            "source_frame_index": 17,
                            "timestamp_sec": 0.567,
                            "timestamp_basis": "video_reader_position",
                            "frame_sha256": frame_hash,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_annotation_import_requires_exact_ids_and_valid_review_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            reviewer = root / "reviewer.csv"
            reviewer.write_text(
                "blind_id,reviewer,review_class,review_status\n"
                "RV-a1,reviewer-01,Bird,reviewed\n",
                encoding="utf-8",
            )
            output = root / "annotations.csv"

            annotations = import_annotations(manifest, reviewer, output)

            self.assertEqual(tuple(annotations[0]), ANNOTATION_FIELDS)
            self.assertEqual(annotations[0]["reviewer"], "reviewer-01")
            self.assertEqual(annotations[0]["review_class"], "Bird")
            self.assertEqual(annotations[0]["source_frame_index"], 17)
            self.assertEqual(annotations[0]["timestamp_sec"], 0.567)
            self.assertEqual(
                annotations[0]["timestamp_basis"],
                "video_reader_position",
            )
            self.assertEqual(
                annotations[0]["frame_representation"],
                "unmodified decoded frame PNG",
            )
            self.assertEqual(
                annotations[0]["evaluation_eligibility"],
                "exploratory_not_independent",
            )
            with self.assertRaises(FileExistsError):
                import_annotations(manifest, reviewer, output)

    def test_annotation_import_rejects_missing_duplicate_unknown_and_invalid_rows(self):
        cases = (
            ("RV-a1,,Bird,reviewed\n", "requires a reviewer"),
            ("RV-a1,r1,Vehicle,reviewed\n", "Invalid review class"),
            ("RV-a1,r1,Uncertain,pending\n", "Pending annotation"),
            ("RV-a1,r1,Bird,unknown\n", "Invalid review status"),
            ("RV-unknown,r1,Bird,reviewed\n", "Unknown blind ID"),
            (
                "RV-a1,r1,Bird,reviewed\nRV-a1,r2,Drone,reviewed\n",
                "Duplicate annotation",
            ),
            ("", "Missing annotations"),
        )
        for rows, expected in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self._manifest(root)
                reviewer = root / "reviewer.csv"
                reviewer.write_text(
                    "blind_id,reviewer,review_class,review_status\n" + rows,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, expected):
                    import_annotations(manifest, reviewer, root / "annotations.csv")

    def test_annotation_import_refuses_to_place_private_data_in_review_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            reviewer = root / "reviewer.csv"
            reviewer.write_text(
                "blind_id,reviewer,review_class,review_status\n"
                "RV-a1,r1,Bird,reviewed\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "stored outside the reviewer bundle"):
                import_annotations(
                    manifest,
                    reviewer,
                    root / "reviewer-bundle" / "annotations.csv",
                )

    def test_review_bundle_blinds_source_identity_and_predictions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "secret_bird_clip.mp4"
            video.write_bytes(b"video")
            bundle = root / "share" / "blind-review"
            curator_path = root / "private" / "curator.json"
            encoded = type("Encoded", (), {"tobytes": lambda self: b"png-data"})()
            capture = type(
                "Capture",
                (),
                {
                    "isOpened": lambda self: True,
                    "set": lambda self, *_args: True,
                    "read": lambda self: (True, type("Frame", (), {"size": 12, "shape": (2, 3, 3)})()),
                    "get": lambda self, prop: (
                        31.0 if prop == cv2.CAP_PROP_POS_FRAMES else 30.0
                    ),
                    "release": lambda self: None,
                },
            )()
            with (
                patch("cv2.VideoCapture", return_value=capture),
                patch("cv2.imencode", return_value=(True, encoded)),
                patch("tools.thermal_manual_review._sha256_frame", return_value="b" * 64),
                patch("tools.thermal_manual_review._sha256_file", return_value="a" * 64),
                patch(
                    "tools.thermal_manual_review._timestamp_seconds",
                    return_value=(1.0, "video_reader_position"),
                ),
            ):
                manifest = prepare_review_bundle([(video, 30)], bundle, curator_path)

            template = (bundle / "annotations_template.csv").read_text(encoding="utf-8")
            instructions = (bundle / "review_instructions.txt").read_text(encoding="utf-8")
            reviewer_files = [path.name for path in (bundle / "images").iterdir()]
            self.assertEqual(len(reviewer_files), 1)
            self.assertEqual(
                {path.name for path in bundle.iterdir()},
                {"images", "annotations_template.csv", "review_instructions.txt"},
            )
            self.assertNotIn(video.name, " ".join(reviewer_files) + template + instructions)
            self.assertNotIn("Drone", template)
            self.assertNotIn("Bird", template)
            self.assertNotIn("videohash", template)
            self.assertEqual(manifest["evaluation_eligibility"], "exploratory_not_independent")
            self.assertEqual(manifest["records"][0]["source_frame_index"], 30)
            self.assertEqual(manifest["records"][0]["timestamp_sec"], 1.0)
            self.assertIn(video.name, curator_path.read_text(encoding="utf-8"))
            self.assertTrue((bundle / "images" / reviewer_files[0]).is_file())

    def test_development_index_records_source_and_decoded_pixel_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "dev_frame.png"
            image_path.write_bytes(b"image")
            index_path = root / "development.csv"
            with (
                patch("cv2.imread", return_value=np.zeros((2, 3), dtype=np.uint8)),
                patch(
                    "tools.thermal_manual_review._sha256_file",
                    return_value="a" * 64,
                ),
                patch(
                    "tools.thermal_manual_review._sha256_frame",
                    return_value="b" * 64,
                ),
            ):
                indexed_rows = build_development_index(
                    [(image_path, "train", "image")],
                    index_path,
                )

            self.assertEqual(indexed_rows, 1)
            with index_path.open(newline="", encoding="utf-8") as source:
                row = next(csv.DictReader(source))
            self.assertEqual(row["source_sha256"], "a" * 64)
            self.assertEqual(row["frame_sha256"], "b" * 64)
            self.assertEqual(row["split"], "train")
            self.assertEqual(row["media_type"], "image")

    def test_leakage_check_detects_video_and_exact_frame_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            annotation_path = root / "annotations.csv"
            with annotation_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=ANNOTATION_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "blind_id": "RV-a1",
                        "source_video_id": "VIDEO-a",
                        "source_video_sha256": "a" * 64,
                        "source_frame_index": 1,
                        "timestamp_sec": 0.1,
                        "timestamp_basis": "video_reader_position",
                        "frame_sha256": "b" * 64,
                        "frame_representation": "unmodified decoded frame PNG",
                        "review_class": "Bird",
                        "reviewer": "r1",
                        "review_status": "reviewed",
                        "evaluation_eligibility": "exploratory_not_independent",
                    }
                )
            index_path = root / "development.csv"
            index_path.write_text(
                "source_sha256,frame_sha256,split,media_type\n"
                + "a" * 64
                + ","
                + "c" * 64
                + ",train,video\n"
                + "d" * 64
                + ","
                + "b" * 64
                + ",validation,image\n",
                encoding="utf-8",
            )

            result = check_leakage(annotation_path, index_path)

            self.assertEqual(result["status"], "leakage_detected")
            self.assertEqual(result["matched_source_video_ids"], ["VIDEO-a"])
            self.assertEqual(result["matched_blind_ids"], ["RV-a1"])
            self.assertFalse(result["independence_established"])

    def test_leakage_check_fails_closed_for_missing_or_empty_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            reviewer = root / "reviewer.csv"
            reviewer.write_text(
                "blind_id,reviewer,review_class,review_status\n"
                "RV-a1,r1,Uncertain,reviewed\n",
                encoding="utf-8",
            )
            annotation_path = root / "annotations.csv"
            import_annotations(manifest, reviewer, annotation_path)
            index_path = root / "development.csv"
            index_path.write_text(
                "source_sha256,frame_sha256,split,media_type\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Development index is empty"):
                check_leakage(annotation_path, index_path)

    def test_leakage_check_rejects_malformed_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            reviewer = root / "reviewer.csv"
            reviewer.write_text(
                "blind_id,reviewer,review_class,review_status\n"
                "RV-a1,r1,Uncertain,reviewed\n",
                encoding="utf-8",
            )
            annotations = root / "annotations.csv"
            import_annotations(manifest, reviewer, annotations)
            development = root / "development.csv"
            development.write_text(
                "source_sha256,frame_sha256,split,media_type\n"
                + "not-a-hash"
                + ","
                + "d" * 64
                + ",train,image\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ValueError, "Development row 1 source hash must be a 64-character"
            ):
                check_leakage(annotations, development)


if __name__ == "__main__":
    unittest.main()
