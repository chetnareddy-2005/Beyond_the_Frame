"""Write a square-padded Caltech RGB video and its bag-timestamp frame manifest."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import numpy as np

from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BAG = (
    PROJECT_ROOT
    / "datasets"
    / "caltech_aerial_rgbt_sync"
    / "raw"
    / "2022-05-08-11-44-33.bag"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "caltech_rgb_full"
RGB_TOPIC = "/eo/color/image_color/compressed"
RGB_MESSAGE_TYPE = "sensor_msgs/msg/CompressedImage"
MANIFEST_FIELDS = (
    "video_frame",
    "rgb_message_index",
    "rgb_timestamp_sec",
    "original_width",
    "original_height",
    "padded_width",
    "padded_height",
)


def pad_to_square(frame: np.ndarray, color: tuple[int, int, int] = (114, 114, 114)) -> np.ndarray:
    """Pad a BGR frame to a square canvas without resizing its image content."""
    if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim != 3:
        raise ValueError("frame must be a non-empty color image")
    if frame.shape[2] != 3:
        raise ValueError("frame must have exactly three color channels")

    height, width = frame.shape[:2]
    side = max(height, width)
    top = (side - height) // 2
    bottom = side - height - top
    left = (side - width) // 2
    right = side - width - left
    return cv2.copyMakeBorder(
        frame,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=color,
    )


def iter_indexed_messages(
    messages: Iterable[tuple[int, object]],
) -> Iterator[tuple[int, int, object]]:
    """Add a zero-based source-message index without changing bag iteration order."""
    for rgb_message_index, (timestamp_ns, raw_data) in enumerate(messages):
        yield rgb_message_index, timestamp_ns, raw_data


def timestamp_ns_to_seconds(timestamp_ns: int) -> str:
    """Format a ROS bag timestamp in seconds while preserving nanosecond digits."""
    seconds, nanoseconds = divmod(int(timestamp_ns), 1_000_000_000)
    return f"{seconds}.{nanoseconds:09d}"


def build_manifest_row(
    rgb_message_index: int,
    timestamp_ns: int,
    original_frame: np.ndarray,
    padded_frame: np.ndarray,
) -> dict[str, object]:
    """Describe the output frame mapped to one source RGB bag message."""
    if rgb_message_index < 0:
        raise ValueError("rgb_message_index must be non-negative")
    original_height, original_width = original_frame.shape[:2]
    padded_height, padded_width = padded_frame.shape[:2]
    if padded_width != padded_height:
        raise ValueError("padded_frame must be square")

    return {
        "video_frame": rgb_message_index + 1,
        "rgb_message_index": rgb_message_index,
        "rgb_timestamp_sec": timestamp_ns_to_seconds(timestamp_ns),
        "original_width": original_width,
        "original_height": original_height,
        "padded_width": padded_width,
        "padded_height": padded_height,
    }


def validate_frame_counts(
    output_frame_count: int,
    manifest_row_count: int,
    rgb_message_count: int,
) -> None:
    """Reject outputs whose video, manifest, and processed source counts differ."""
    if not (
        output_frame_count == manifest_row_count == rgb_message_count
    ):
        raise ValueError(
            "Frame count mismatch: "
            f"video={output_frame_count}, manifest={manifest_row_count}, "
            f"RGB messages={rgb_message_count}"
        )


def validate_video(video_path: Path, expected_frame_count: int) -> int:
    """Decode the completed video and validate sampled frame dimensions."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise OSError(f"Could not open output video: {video_path}")

    decoded_count = 0
    sample_indices = {0, expected_frame_count // 2, expected_frame_count - 1}
    try:
        while True:
            success, frame = capture.read()
            if not success:
                break
            if decoded_count in sample_indices:
                height, width = frame.shape[:2]
                if width != height:
                    raise ValueError(
                        f"Output frame {decoded_count + 1} is not square: "
                        f"{width}x{height}"
                    )
            decoded_count += 1
    finally:
        capture.release()

    if decoded_count != expected_frame_count:
        raise ValueError(
            f"Decoded output frame count {decoded_count} does not match "
            f"expected count {expected_frame_count}"
        )
    return decoded_count


def extract_caltech_rgb_video(
    bag_path: Path,
    video_path: Path,
    manifest_path: Path,
    fps: float = 30.0,
) -> int:
    """Extract every RGB message in bag order, writing video frames and timestamps."""
    bag_path = Path(bag_path)
    video_path = Path(video_path)
    manifest_path = Path(manifest_path)
    if not bag_path.is_file():
        raise FileNotFoundError(f"Caltech ROS bag does not exist: {bag_path}")
    if fps <= 0:
        raise ValueError("fps must be positive")
    if video_path.exists() or manifest_path.exists():
        raise FileExistsError("Refusing to overwrite an existing video or manifest")
    video_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_video = video_path.with_name(f"{video_path.stem}.tmp{video_path.suffix}")
    temporary_manifest = manifest_path.with_name(f"{manifest_path.name}.tmp")
    typestore = get_typestore(Stores.ROS1_NOETIC)
    writer = None
    rgb_message_count = 0
    manifest_row_count = 0
    original_shape: tuple[int, int] | None = None

    try:
        with temporary_manifest.open("w", newline="", encoding="utf-8") as manifest_file:
            manifest_writer = csv.DictWriter(
                manifest_file,
                fieldnames=MANIFEST_FIELDS,
            )
            manifest_writer.writeheader()

            with Reader(bag_path) as reader:
                connections = [
                    connection
                    for connection in reader.connections
                    if connection.topic == RGB_TOPIC
                ]
                if not connections:
                    raise ValueError(f"RGB topic not found in ROS bag: {RGB_TOPIC}")

                messages = (
                    (timestamp_ns, raw_data)
                    for _, timestamp_ns, raw_data in reader.messages(
                        connections=connections
                    )
                )
                for rgb_message_index, timestamp_ns, raw_data in iter_indexed_messages(messages):
                    message = typestore.deserialize_ros1(
                        raw_data,
                        RGB_MESSAGE_TYPE,
                    )
                    encoded = np.frombuffer(message.data, dtype=np.uint8)
                    original_frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    if original_frame is None or original_frame.size == 0:
                        raise ValueError(
                            "Could not decode RGB message "
                            f"{rgb_message_index} from {bag_path}"
                        )

                    height, width = original_frame.shape[:2]
                    if original_shape is None:
                        original_shape = (height, width)
                        side = max(height, width)
                        writer = cv2.VideoWriter(
                            str(temporary_video),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            fps,
                            (side, side),
                        )
                        if not writer.isOpened():
                            raise OSError(
                                f"Could not open output video writer: {temporary_video}"
                            )
                    elif original_shape != (height, width):
                        raise ValueError(
                            "RGB message dimensions changed within the recording: "
                            f"expected {original_shape[1]}x{original_shape[0]}, "
                            f"got {width}x{height} at message {rgb_message_index}"
                        )

                    padded_frame = pad_to_square(original_frame)
                    writer.write(padded_frame)
                    manifest_writer.writerow(
                        build_manifest_row(
                            rgb_message_index,
                            timestamp_ns,
                            original_frame,
                            padded_frame,
                        )
                    )
                    rgb_message_count += 1
                    manifest_row_count += 1

        if writer is not None:
            writer.release()
            writer = None
        if rgb_message_count == 0:
            raise ValueError(f"No RGB messages found on topic {RGB_TOPIC}")

        output_frame_count = validate_video(temporary_video, rgb_message_count)
        validate_frame_counts(
            output_frame_count,
            manifest_row_count,
            rgb_message_count,
        )
        os.replace(temporary_video, video_path)
        os.replace(temporary_manifest, manifest_path)
        return rgb_message_count
    finally:
        if writer is not None:
            writer.release()
        for temporary_path in (temporary_video, temporary_manifest):
            if temporary_path.exists():
                temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bag", type=Path, default=DEFAULT_BAG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="constant playback rate only; timestamps come from ROS bag messages",
    )
    args = parser.parse_args()

    video_path = args.output_dir / "caltech_rgb_square.mp4"
    manifest_path = args.output_dir / "rgb_frame_manifest.csv"
    count = extract_caltech_rgb_video(
        args.bag,
        video_path,
        manifest_path,
        fps=args.fps,
    )
    print(f"RGB messages processed : {count}")
    print(f"Output video           : {video_path}")
    print(f"Frame manifest         : {manifest_path}")


if __name__ == "__main__":
    main()
