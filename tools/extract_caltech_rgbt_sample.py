from pathlib import Path
from bisect import bisect_left
import cv2
import numpy as np

from rosbags.rosbag1 import Reader
from rosbags.typesys import get_typestore, Stores


BAG = Path(
    r"datasets\caltech_aerial_rgbt_sync\raw\2022-05-08-11-44-33.bag"
)

OUT = Path(r"results\caltech_rgbt_sync_sample")
RGB_OUT = OUT / "rgb"
THERMAL_OUT = OUT / "thermal"

RGB_OUT.mkdir(parents=True, exist_ok=True)
THERMAL_OUT.mkdir(parents=True, exist_ok=True)

RGB_TOPIC = "/eo/color/image_color/compressed"
THERMAL_TOPIC = "/boson/thermal/image_raw"

typestore = get_typestore(Stores.ROS1_NOETIC)

rgb_messages = []
thermal_messages = []

with Reader(BAG) as reader:
    connections = [
        c for c in reader.connections
        if c.topic in {RGB_TOPIC, THERMAL_TOPIC}
    ]

    for connection, timestamp, rawdata in reader.messages(
        connections=connections
    ):
        if connection.topic == RGB_TOPIC:
            rgb_messages.append((timestamp, rawdata))
        elif connection.topic == THERMAL_TOPIC:
            thermal_messages.append((timestamp, rawdata))

print(f"RGB messages: {len(rgb_messages)}")
print(f"Thermal messages: {len(thermal_messages)}")


# Select 10 evenly spaced thermal frames.
sample_indices = [
    round(i * (len(thermal_messages) - 1) / 9)
    for i in range(10)
]

rgb_timestamps = [x[0] for x in rgb_messages]

print("\nExtracting synchronized RGB-Thermal samples...\n")

for sample_no, thermal_index in enumerate(sample_indices, start=1):

    thermal_timestamp, thermal_raw = thermal_messages[thermal_index]

    # Find nearest RGB timestamp.
    position = bisect_left(rgb_timestamps, thermal_timestamp)

    candidates = []

    if position > 0:
        candidates.append(rgb_messages[position - 1])

    if position < len(rgb_messages):
        candidates.append(rgb_messages[position])

    rgb_timestamp, rgb_raw = min(
        candidates,
        key=lambda x: abs(x[0] - thermal_timestamp)
    )

    delta_ms = abs(rgb_timestamp - thermal_timestamp) / 1e6

    # Deserialize ROS messages.
    rgb_msg = typestore.deserialize_ros1(
        rgb_raw,
        "sensor_msgs/msg/CompressedImage"
    )

    thermal_msg = typestore.deserialize_ros1(
        thermal_raw,
        "sensor_msgs/msg/Image"
    )

    # RGB CompressedImage -> OpenCV image.
    rgb_array = np.frombuffer(rgb_msg.data, dtype=np.uint8)
    rgb_image = cv2.imdecode(rgb_array, cv2.IMREAD_COLOR)

    # Thermal Image -> NumPy array.
    thermal_dtype = np.dtype(
        {
            "8UC1": np.uint8,
            "mono8": np.uint8,
            "16UC1": np.uint16,
            "mono16": np.uint16,
        }.get(
            thermal_msg.encoding,
            np.uint8
        )
    )

    thermal_image = np.frombuffer(
        thermal_msg.data,
        dtype=thermal_dtype
    ).reshape(
        thermal_msg.height,
        thermal_msg.width
    )

    # Convert thermal image to viewable 8-bit PNG.
    if thermal_image.dtype != np.uint8:
        thermal_view = cv2.normalize(
            thermal_image,
            None,
            0,
            255,
            cv2.NORM_MINMAX
        ).astype(np.uint8)
    else:
        thermal_view = thermal_image

    # Save images.
    rgb_path = RGB_OUT / f"sample_{sample_no:02d}.jpg"
    thermal_path = THERMAL_OUT / f"sample_{sample_no:02d}.png"

    cv2.imwrite(str(rgb_path), rgb_image)
    cv2.imwrite(str(thermal_path), thermal_view)

    print(
        f"{sample_no:02d} | "
        f"thermal index={thermal_index:3d} | "
        f"delta={delta_ms:8.3f} ms | "
        f"RGB={rgb_image.shape if rgb_image is not None else None} | "
        f"Thermal={thermal_image.shape}"
    )

print("\nDone.")
print(f"RGB images     : {RGB_OUT}")
print(f"Thermal images : {THERMAL_OUT}")