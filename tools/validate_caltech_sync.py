from pathlib import Path
from bisect import bisect_left
from rosbags.rosbag1 import Reader

BAG = Path(
    r"datasets\caltech_aerial_rgbt_sync\raw\2022-05-08-11-44-33.bag"
)

RGB_TOPIC = "/eo/color/image_color/compressed"
THERMAL_TOPIC = "/boson/thermal/image_raw"

MAX_DELTA_SEC = 0.020  # 20 ms


with Reader(BAG) as reader:
    connections = [
        c for c in reader.connections
        if c.topic in {RGB_TOPIC, THERMAL_TOPIC}
    ]

    rgb_timestamps = []
    thermal_timestamps = []

    for connection, timestamp, _ in reader.messages(
        connections=connections
    ):
        if connection.topic == RGB_TOPIC:
            rgb_timestamps.append(timestamp)
        elif connection.topic == THERMAL_TOPIC:
            thermal_timestamps.append(timestamp)


rgb_timestamps.sort()
thermal_timestamps.sort()

differences = []

for thermal_ts in thermal_timestamps:
    index = bisect_left(rgb_timestamps, thermal_ts)

    candidates = []

    if index > 0:
        candidates.append(rgb_timestamps[index - 1])

    if index < len(rgb_timestamps):
        candidates.append(rgb_timestamps[index])

    nearest_rgb = min(
        candidates,
        key=lambda x: abs(x - thermal_ts)
    )

    differences.append(
        abs(nearest_rgb - thermal_ts) / 1e9
    )


within_20ms = sum(
    delta <= MAX_DELTA_SEC
    for delta in differences
)

within_50ms = sum(
    delta <= 0.050
    for delta in differences
)

print("Caltech RGB-Thermal Synchronization Validation")
print("-----------------------------------------------")
print(f"RGB messages       : {len(rgb_timestamps)}")
print(f"Thermal messages   : {len(thermal_timestamps)}")
print(f"20 ms threshold    : {MAX_DELTA_SEC * 1000:.0f} ms")
print()
print(
    f"Within 20 ms       : "
    f"{within_20ms}/{len(differences)} "
    f"({within_20ms / len(differences) * 100:.2f}%)"
)
print(
    f"Within 50 ms       : "
    f"{within_50ms}/{len(differences)} "
    f"({within_50ms / len(differences) * 100:.2f}%)"
)
print(
    f"Outside 20 ms      : "
    f"{len(differences) - within_20ms}"
)

assert len(rgb_timestamps) == 3580
assert len(thermal_timestamps) == 676
assert within_20ms == 671

print()
print("PASS: Caltech RGB-Thermal synchronization validation.")