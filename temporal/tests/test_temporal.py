import csv
import tempfile
import unittest
from pathlib import Path

from temporal.temporal_features import compute_temporal_features, process_tracks_csv
from temporal.tracklet_memory import TrackletMemory


def observation(track_id, frame, confidence, box, class_name):
    return {
        "track_id": str(track_id),
        "frame": str(frame),
        "class_id": "0" if class_name == "Bird" else "1",
        "class_name": class_name,
        "confidence": str(confidence),
        "x1": str(box[0]),
        "y1": str(box[1]),
        "x2": str(box[2]),
        "y2": str(box[3]),
    }


class TrackletMemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = TrackletMemory()
        self.memory.add_observation(observation(7, 1, 0.6, (0, 0, 10, 10), "Bird"))
        self.memory.add_observation(observation(8, 1, 0.5, (100, 100, 110, 110), "Drone"))
        self.memory.add_observation(observation(7, 2, 0.8, (3, 4, 13, 14), "Bird"))
        self.memory.add_observation(observation(8, 2, 0.7, (101, 102, 111, 112), "Drone"))
        self.memory.add_observation(observation(7, 4, 1.0, (6, 8, 16, 18), "Bird"))

    def test_groups_tracks_and_stores_observations(self):
        bird = self.memory.get_track(7)
        drone = self.memory.get_track(8)

        self.assertEqual(bird["frames"], [1, 2, 4])
        self.assertEqual(bird["centers"], [(5, 5), (8, 9), (11, 13)])
        self.assertEqual(bird["widths"], [10, 10, 10])
        self.assertEqual(bird["heights"], [10, 10, 10])
        self.assertEqual(drone["frames"], [1, 2])
        self.assertEqual([track["track_id"] for track in self.memory.get_all_tracks()], [7, 8])
        self.assertEqual([track["track_id"] for track in self.memory.get_active_tracks()], [7])

    def test_computes_temporal_and_motion_features(self):
        summary, motion = compute_temporal_features(self.memory.get_track(7))

        self.assertEqual(summary["frames_seen"], 3)
        self.assertEqual((summary["start_frame"], summary["end_frame"]), (1, 4))
        self.assertAlmostEqual(summary["persistence"], 0.75)
        self.assertAlmostEqual(summary["mean_confidence"], 0.8)
        self.assertAlmostEqual(summary["confidence_std"], (0.08 / 3) ** 0.5)
        self.assertEqual((summary["first_center_x"], summary["first_center_y"]), (5, 5))
        self.assertEqual((summary["last_center_x"], summary["last_center_y"]), (11, 13))
        self.assertEqual([row["frame"] for row in motion], [2, 4])
        self.assertEqual([(row["delta_x"], row["delta_y"]) for row in motion], [(3, 4), (3, 4)])
        self.assertEqual([row["displacement"] for row in motion], [5, 5])
        self.assertEqual(summary["total_displacement"], 10)
        self.assertEqual(summary["mean_displacement"], 5)
        self.assertEqual(summary["mean_width"], 10)
        self.assertEqual(summary["mean_height"], 10)
        self.assertEqual(summary["width_variation"], 0)
        self.assertEqual(summary["height_variation"], 0)

    def test_reads_tracks_csv_and_writes_feature_csv_files(self):
        with tempfile.TemporaryDirectory() as directory:
            tracks_csv = Path(directory) / "tracks.csv"
            with tracks_csv.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "source", "frame", "track_id", "class_id", "class_name",
                        "confidence", "x1", "y1", "x2", "y2",
                    ),
                )
                writer.writeheader()
                for row in (
                    observation(7, 1, 0.6, (0, 0, 10, 10), "Bird"),
                    observation(8, 1, 0.5, (100, 100, 110, 110), "Drone"),
                    observation(7, 2, 0.8, (3, 4, 13, 14), "Bird"),
                    observation(8, 2, 0.7, (101, 102, 111, 112), "Drone"),
                    observation(7, 4, 1.0, (6, 8, 16, 18), "Bird"),
                ):
                    writer.writerow({"source": "clip.mp4", **row})

            summaries, motions = process_tracks_csv(tracks_csv)
            with (Path(directory) / "temporal_features.csv").open(
                newline="", encoding="utf-8"
            ) as source:
                saved_summaries = list(csv.DictReader(source))
            with (Path(directory) / "track_motion.csv").open(
                newline="", encoding="utf-8"
            ) as source:
                saved_motion = list(csv.DictReader(source))

            self.assertEqual(len(summaries), 2)
            self.assertEqual(len(saved_summaries), 2)
            self.assertEqual(len(motions), 3)
            self.assertEqual(len(saved_motion), 3)
            self.assertEqual({int(row["track_id"]) for row in saved_summaries}, {7, 8})


if __name__ == "__main__":
    unittest.main()
