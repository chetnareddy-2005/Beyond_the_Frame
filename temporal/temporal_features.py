"""Calculate per-track temporal features from a YOLOv5 tracks.csv file."""

import argparse
import csv
from math import hypot
from pathlib import Path
from statistics import fmean, pstdev

from temporal.tracklet_memory import TrackletMemory

SUMMARY_FIELDS = (
    "track_id",
    "frames_seen",
    "start_frame",
    "end_frame",
    "persistence",
    "mean_confidence",
    "confidence_std",
    "first_center_x",
    "first_center_y",
    "last_center_x",
    "last_center_y",
    "total_displacement",
    "mean_displacement",
    "mean_width",
    "mean_height",
    "width_variation",
    "height_variation",
)
MOTION_FIELDS = ("track_id", "frame", "delta_x", "delta_y", "displacement")


def compute_temporal_features(track):
    """Return a per-track summary and its frame-to-frame motion rows."""
    observations = sorted(
        zip(track["frames"], track["centers"], track["confidences"], track["widths"], track["heights"]),
        key=lambda observation: observation[0],
    )
    frames = [item[0] for item in observations]
    centers = [item[1] for item in observations]
    confidences = [item[2] for item in observations]
    widths = [item[3] for item in observations]
    heights = [item[4] for item in observations]

    motion = []
    for index in range(1, len(observations)):
        delta_x = centers[index][0] - centers[index - 1][0]
        delta_y = centers[index][1] - centers[index - 1][1]
        motion.append(
            {
                "track_id": track["track_id"],
                "frame": frames[index],
                "delta_x": delta_x,
                "delta_y": delta_y,
                "displacement": hypot(delta_x, delta_y),
            }
        )

    displacements = [item["displacement"] for item in motion]
    frame_span = frames[-1] - frames[0] + 1
    summary = {
        "track_id": track["track_id"],
        "frames_seen": len(frames),
        "start_frame": frames[0],
        "end_frame": frames[-1],
        "persistence": len(frames) / frame_span,
        "mean_confidence": fmean(confidences),
        "confidence_std": pstdev(confidences),
        "first_center_x": centers[0][0],
        "first_center_y": centers[0][1],
        "last_center_x": centers[-1][0],
        "last_center_y": centers[-1][1],
        "total_displacement": sum(displacements),
        "mean_displacement": fmean(displacements) if displacements else 0.0,
        "mean_width": fmean(widths),
        "mean_height": fmean(heights),
        "width_variation": pstdev(widths),
        "height_variation": pstdev(heights),
    }
    return summary, motion


def process_tracks_csv(tracks_csv, output_csv=None, motion_csv=None):
    """Read track observations and write per-track and per-frame CSV features."""
    tracks_csv = Path(tracks_csv)
    output_csv = Path(output_csv) if output_csv else tracks_csv.with_name("temporal_features.csv")
    motion_csv = Path(motion_csv) if motion_csv else tracks_csv.with_name("track_motion.csv")

    memory = TrackletMemory()
    with tracks_csv.open(newline="", encoding="utf-8") as source:
        for observation in csv.DictReader(source):
            memory.add_observation(observation)

    summaries = []
    motions = []
    for track in memory.get_all_tracks():
        summary, track_motion = compute_temporal_features(track)
        summaries.append(summary)
        motions.extend(track_motion)

    summaries.sort(key=lambda row: row["track_id"])
    motions.sort(key=lambda row: (row["track_id"], row["frame"]))
    with output_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summaries)
    with motion_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=MOTION_FIELDS)
        writer.writeheader()
        writer.writerows(motions)
    return summaries, motions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tracks_csv", help="Phase 2 tracks.csv input")
    parser.add_argument("--output", help="per-track summary CSV path")
    parser.add_argument("--motion-output", help="frame-to-frame motion CSV path")
    args = parser.parse_args()

    summaries, motions = process_tracks_csv(
        args.tracks_csv, args.output, args.motion_output
    )
    print(f"Wrote {len(summaries)} track summaries and {len(motions)} motion rows.")


if __name__ == "__main__":
    main()
