from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.runs import RunManager


class SourceVideoIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runs_root = self.root / "dashboard_runs"
        self.manager = RunManager(
            self.root,
            max_upload_bytes=1024 * 1024,
            runner=lambda *args: None,
            runs_root=self.runs_root,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, filename: str, content: bytes):
        return self.manager.create_run(filename, content)

    def test_repeated_and_renamed_identical_uploads_share_stable_video_id(self):
        first = self.create("source-a.mp4", b"source video A")
        second = self.create("source-a-copy.mp4", b"source video A")
        third = self.create("source-a-third.mp4", b"source video A")

        self.assertEqual(
            [run.as_json()["source_video_id"] for run in (first, second, third)],
            ["V-1", "V-1", "V-1"],
        )

    def test_distinct_sources_get_distinct_ids_and_survive_manager_restart(self):
        source_a = self.create("a.mp4", b"source A")
        source_b = self.create("b.mp4", b"source B")
        source_c = self.create("c.mp4", b"source C")
        repeat_a = self.create("renamed-a.mov", b"source A")
        repeat_b = self.create("renamed-b.avi", b"source B")
        repeat_c = self.create("renamed-c.mkv", b"source C")
        expected = {
            source_a.run_id: "V-1",
            source_b.run_id: "V-2",
            source_c.run_id: "V-3",
            repeat_a.run_id: "V-1",
            repeat_b.run_id: "V-2",
            repeat_c.run_id: "V-3",
        }

        restarted = RunManager(
            self.root,
            max_upload_bytes=1024 * 1024,
            runner=lambda *args: None,
            runs_root=self.runs_root,
        )

        self.assertEqual(
            {run.run_id: run.as_json()["source_video_id"] for run in restarted.list_runs()},
            expected,
        )
        index = json.loads((self.runs_root / "source_video_ids.json").read_text())
        self.assertEqual(len(index["videos"]), 3)

    def test_historical_runs_without_index_are_mapped_from_persisted_input_hashes(self):
        source_a = self.create("a.mp4", b"old source A")
        source_b = self.create("b.mp4", b"old source B")
        (self.runs_root / "source_video_ids.json").unlink()
        for run in (source_a, source_b):
            manifest_path = run.root / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("source_video_sha256", None)
            manifest.pop("source_video_id", None)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        restarted = RunManager(
            self.root,
            max_upload_bytes=1024 * 1024,
            runner=lambda *args: None,
            runs_root=self.runs_root,
        )

        self.assertEqual(restarted.get(source_a.run_id).source_video_id, "V-1")
        self.assertEqual(restarted.get(source_b.run_id).source_video_id, "V-2")
        self.assertTrue((self.runs_root / "source_video_ids.json").is_file())


if __name__ == "__main__":
    unittest.main()
