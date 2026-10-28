"""A small injectable VLM interface with a deterministic development mock."""

import csv
from pathlib import Path

from vlm.prompts import ALLOWED_LABELS

VLM_FIELDS = ("track_id", "frame", "crop_path", "vlm_label", "vlm_confidence", "reason")


class MockVLM:
    """Return explicit unknown evidence without interpreting image content."""

    def verify(self, image_path):
        if not Path(image_path).is_file():
            raise FileNotFoundError(f"Evidence crop does not exist: {image_path}")
        return {
            "label": "Unknown",
            "confidence": 0.0,
            "reason": "Mock VLM does not inspect image content.",
        }


def write_vlm_results(selected_rows, vlm, output_csv, crop_root):
    """Verify each selected crop and save raw semantic evidence to CSV."""
    results = []
    for row in selected_rows:
        crop_path = (Path(crop_root) / row["crop_path"]).resolve()
        response = vlm.verify(crop_path)
        label = response["label"]
        confidence = float(response["confidence"])
        reason = str(response["reason"])
        if label not in ALLOWED_LABELS:
            raise ValueError(f"Unsupported VLM label: {label}")
        if not 0 <= confidence <= 1:
            raise ValueError("VLM confidence must be between 0 and 1")
        results.append(
            {
                "track_id": row["track_id"],
                "frame": row["frame"],
                "crop_path": row["crop_path"],
                "vlm_label": label,
                "vlm_confidence": confidence,
                "reason": reason,
            }
        )

    with Path(output_csv).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=VLM_FIELDS)
        writer.writeheader()
        writer.writerows(results)
    return results
