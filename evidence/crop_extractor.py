"""Extract padded crops from selected original-video frames.

The current AFNO2D pipeline is validated only on square-padded sources. The evidence
contract therefore requires that every crop is extracted from the same padded source
that produced the detections/tracks; otherwise the coordinate system is inconsistent.
"""

import math
from pathlib import Path

import cv2


def _source_consistency_error(video_path, source_name=None):
    source_name = source_name or Path(video_path).name
    return ValueError(
        "Source mismatch: evidence extraction requires the same padded source video "
        f"used for detection/tracking. Expected padded source metadata, got {source_name}."
    )


def extract_crops(
    video_path,
    observations,
    output_dir,
    padding_fraction=0.1,
    source_name=None,
    progress_callback=None,
):
    """Save selected bounding-box crops and return rows with relative crop paths."""
    if not math.isfinite(padding_fraction) or padding_fraction < 0:
        raise ValueError("padding_fraction must be a finite non-negative number")
    if not observations:
        return []

    source_name = source_name or str(video_path)
    if "source" in observations[0]:
        expected_source = observations[0]["source"]
        if Path(expected_source).name.casefold() != Path(source_name).name.casefold():
            raise _source_consistency_error(video_path, source_name)

    output_dir = Path(output_dir)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise OSError(f"Could not open video: {video_path}")

    result = []
    try:
        for row in sorted(observations, key=lambda item: item["frame"]):
            frame_number = int(row["frame"])
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number - 1)
            success, frame = capture.read()
            if not success:
                raise ValueError(f"Could not read frame {frame_number} from {video_path}")

            frame_height, frame_width = frame.shape[:2]
            box_width = row["x2"] - row["x1"]
            box_height = row["y2"] - row["y1"]
            pad_x = math.ceil(box_width * padding_fraction)
            pad_y = math.ceil(box_height * padding_fraction)
            x1 = max(0, math.floor(row["x1"]) - pad_x)
            y1 = max(0, math.floor(row["y1"]) - pad_y)
            x2 = min(frame_width, math.ceil(row["x2"]) + pad_x)
            y2 = min(frame_height, math.ceil(row["y2"]) + pad_y)
            if x2 <= x1 or y2 <= y1:
                raise ValueError(
                    f"Bounding box for track {row['track_id']} frame {frame_number} "
                    "is outside the video frame"
                )

            track_dir = output_dir / "selected" / f"track_{row['track_id']}"
            track_dir.mkdir(parents=True, exist_ok=True)
            crop_path = track_dir / f"frame_{frame_number:06d}.jpg"
            if not cv2.imwrite(str(crop_path), frame[y1:y2, x1:x2]):
                raise OSError(f"Could not write evidence crop: {crop_path}")

            result.append(
                {
                    **row,
                    "crop_path": str(crop_path.relative_to(output_dir)),
                }
            )
            if progress_callback is not None:
                progress_callback(len(result), len(observations))
    finally:
        capture.release()

    return result
