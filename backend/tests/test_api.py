from __future__ import annotations

import json
import csv
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app import ProjectData, create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.config import get_settings
from temporal.motion_trajectory import process_tracks_csv as process_motion_trajectories


class BackendAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.auth_temp_dir = tempfile.TemporaryDirectory()
        cls.server = create_server(
            port=0,
            auth_db_path=Path(cls.auth_temp_dir.name) / "auth.sqlite3",
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        login = Request(
            cls.base_url + "/api/auth/login",
            data=json.dumps(
                {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(login, timeout=5) as response:
            cls.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.auth_temp_dir.cleanup()

    def get_json(self, path: str, expected_status: int = 200):
        try:
            request = Request(self.base_url + path, headers={"Cookie": self.auth_cookie})
            with urlopen(request, timeout=5) as response:
                status = response.status
                payload = json.loads(response.read())
        except HTTPError as error:
            status = error.code
            payload = json.loads(error.read())
        self.assertEqual(status, expected_status)
        json.dumps(payload, allow_nan=False)
        return payload

    def load_with_vlm_rows(self, rows: list[dict[str, str]]) -> ProjectData:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        vlm_path = Path(temporary.name) / "vlm_results.csv"
        fieldnames = ("track_id", "frame", "crop_path", "label", "confidence", "reason")
        with vlm_path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return ProjectData(get_settings(), paths_override={"vlm": vlm_path})

    @staticmethod
    def vlm_row(frame: int, label: str, confidence: float, reason: str = "test") -> dict[str, str]:
        return {
            "track_id": "24",
            "frame": str(frame),
            "crop_path": f"selected/track_24/frame_{frame:06d}.jpg",
            "label": label,
            "confidence": str(confidence),
            "reason": reason,
        }

    def test_project_data_loads_single_vlm_row_with_legacy_result_shape(self) -> None:
        row = self.vlm_row(214, "Bird", 0.72)
        data = self.load_with_vlm_rows([row])

        self.assertEqual(data.vlm["24"], [{**row, "track_id": "24"}])
        vlm = data._vlm_result("24")
        self.assertEqual(vlm["result"]["label"], "Bird")
        self.assertEqual(vlm["results"], [vlm["result"]])

    def test_project_data_preserves_multiple_vlm_rows_with_same_label(self) -> None:
        rows = [
            self.vlm_row(214, "Bird", 0.72),
            self.vlm_row(215, "Bird", 0.68),
        ]
        data = self.load_with_vlm_rows(rows)

        self.assertEqual(len(data.vlm["24"]), 2)
        self.assertEqual(
            [(row["label"], row["confidence"]) for row in data.vlm["24"]],
            [("Bird", "0.72"), ("Bird", "0.68")],
        )
        self.assertIsNone(data._vlm_result("24")["result"])
        self.assertEqual([row["frame"] for row in data._vlm_result("24")["results"]], [214, 215])

    def test_project_data_preserves_conflicting_vlm_labels(self) -> None:
        rows = [
            self.vlm_row(214, "Bird", 0.72),
            self.vlm_row(215, "Drone", 0.61),
            self.vlm_row(216, "Bird", 0.68),
        ]
        data = self.load_with_vlm_rows(rows)

        self.assertEqual(
            [row["label"] for row in data._vlm_result("24")["results"]],
            ["Bird", "Drone", "Bird"],
        )

    def test_project_data_accepts_empty_vlm_results(self) -> None:
        data = self.load_with_vlm_rows([])

        self.assertEqual(data.vlm, {})
        self.assertEqual(data._vlm_result("24")["results"], [])
        self.assertFalse(data._vlm_result("24")["available"])

    def test_health_and_overview(self) -> None:
        self.assertEqual(self.get_json("/api/health"), {"status": "ok"})
        overview = self.get_json("/api/overview")
        self.assertEqual(overview["total_tracks"], 69)
        self.assertEqual(overview["total_observations"], 2210)
        self.assertEqual(overview["vlm_enabled_tracks"], 17)
        self.assertEqual(overview["synchronized_thermal_evidence_count"], 1)
        self.assertEqual(overview["spatially_associated_thermal_evidence_count"], 0)
        self.assertEqual(overview["genuine_radar_sample_count"], 20)
        self.assertEqual(overview["caltech_associated_radar_count"], 0)

    def test_tracks_preserve_all_unique_track_ids(self) -> None:
        result = self.get_json("/api/tracks")
        self.assertEqual(result["count"], 69)
        track_ids = [track["track_id"] for track in result["tracks"]]
        self.assertEqual(len(set(track_ids)), 69)
        self.assertEqual(result["tracks"][0]["source"], "caltech_rgb_square.mp4")
        self.assertNotIn("C:\\", json.dumps(result))
        self.assertNotIn("OneDrive", json.dumps(result))

    def test_track_source_is_safe_filename_in_detail_and_temporal_summary(self) -> None:
        result = self.get_json("/api/tracks/24")
        self.assertEqual(result["source"], "caltech_rgb_square.mp4")
        self.assertEqual(result["temporal_summary"]["source"], "caltech_rgb_square.mp4")
        serialized = json.dumps(result)
        self.assertNotIn("C:\\", serialized)
        self.assertNotIn("OneDrive", serialized)

    def test_track_212_existing_values(self) -> None:
        result = self.get_json("/api/tracks/212")
        self.assertEqual(result["frames_seen"], 516)
        self.assertEqual(result["reliability"]["status"], "CONFIDENT")
        self.assertAlmostEqual(result["reliability"]["reliability_score"], 0.7733768698051167)
        self.assertEqual(result["fusion"]["fusion_mode"], "detector_temporal")
        self.assertEqual(result["fusion"]["final_label"], "UAV")
        self.assertAlmostEqual(result["fusion"]["detector_evidence"]["mean_confidence"], 0.4864030494246372)
        self.assertAlmostEqual(result["fusion"]["final_confidence"], 0.4864030494246372)
        self.assertEqual(result["thermal"]["available"], None)

    def test_track_24_existing_values_and_modality_gates(self) -> None:
        result = self.get_json("/api/tracks/24")
        self.assertEqual(result["frames_seen"], 4)
        self.assertEqual(result["reliability"]["status"], "UNCERTAIN")
        self.assertAlmostEqual(result["reliability"]["reliability_score"], 0.5940800564992955)
        self.assertEqual(len(result["selected_evidence"]), 3)
        self.assertEqual(result["vlm"]["result"]["label"], "Bird")
        self.assertAlmostEqual(result["vlm"]["result"]["confidence"], 0.9655035734176636)
        self.assertTrue(result["thermal"]["synchronized"])
        self.assertFalse(result["thermal"]["spatially_associated"])
        self.assertIsNone(result["radar"]["track_associated"])
        self.assertEqual(result["fusion"]["fusion_mode"], "detector_vlm")
        self.assertEqual(result["fusion"]["final_label"], "Bird")
        self.assertAlmostEqual(result["fusion"]["detector_evidence"]["mean_confidence"], 0.4275702089071274)
        self.assertAlmostEqual(result["fusion"]["final_confidence"], 0.3919171559714263)

    def test_track_397_existing_values(self) -> None:
        result = self.get_json("/api/tracks/397")
        self.assertEqual(result["temporal_summary"]["frames_seen"], 3)
        self.assertEqual(result["reliability"]["status"], "UNCERTAIN")
        self.assertAlmostEqual(result["reliability"]["reliability_score"], 0.555121077668867)
        self.assertEqual(result["vlm"]["result"]["label"], "Bird")
        self.assertAlmostEqual(result["vlm"]["result"]["confidence"], 0.9338164925575256)
        self.assertFalse(result["thermal"]["synchronized"])
        self.assertIsNone(result["thermal"]["spatially_associated"])
        self.assertIsNone(result["radar"]["track_associated"])
        self.assertEqual(result["fusion"]["final_label"], "Bird")
        self.assertAlmostEqual(result["fusion"]["detector_evidence"]["mean_confidence"], 0.593534787495931)
        self.assertAlmostEqual(result["fusion"]["final_confidence"], 0.4154352748640305)

    def test_evidence_vlm_modalities_and_fusion_endpoints(self) -> None:
        evidence = self.get_json("/api/tracks/24/evidence")
        self.assertEqual(len(evidence), 3)
        self.assertEqual(evidence[0]["selection_stage"], "phase4_selective_rgb")
        self.assertFalse(evidence[0]["crop_path"].startswith(("/", "\\")))
        self.assertNotIn("..", evidence[0]["crop_path"].split("/"))
        vlm = self.get_json("/api/tracks/24/vlm")
        self.assertEqual(vlm["result"]["label"], "Bird")
        self.assertIn("not calibrated", vlm["confidence_interpretation"])
        modality = self.get_json("/api/tracks/24/modalities")
        self.assertTrue(modality["thermal"]["synchronized"])
        self.assertFalse(modality["thermal"]["spatially_associated"])
        self.assertIsNone(modality["radar"]["synchronized_with_caltech"])
        fusion = self.get_json("/api/tracks/24/fusion")
        self.assertEqual(fusion["fusion_mode"], "detector_vlm")
        self.assertEqual(self.get_json("/api/modalities")["radar"]["track_association_status"], "not_established")
        self.assertEqual(self.get_json("/api/fusion/summary")["fusion_mode_counts"]["detector_vlm"], 17)

    def test_evidence_path_traversal_is_rejected(self) -> None:
        data = ProjectData(get_settings())
        for traversal in ("../tracks.csv", "..\\tracks.csv", "C:\\Windows\\win.ini"):
            with self.subTest(path=traversal):
                with self.assertRaises(ValueError):
                    data._safe_evidence_path(traversal)
        evidence = self.get_json("/api/tracks/24/evidence")
        self.assertTrue(all(".." not in row["crop_path"].split("/") for row in evidence))

    def test_motion_trajectory_is_available_without_changing_historical_outputs(self) -> None:
        settings = get_settings()
        with tempfile.TemporaryDirectory() as temporary:
            process_motion_trajectories(
                settings.paths["tracks"],
                temporary,
                fps=None,
            )
            trajectory_path = Path(temporary) / "trajectories.json"
            data = ProjectData(
                settings,
                paths_override={"motion_trajectory_json": trajectory_path},
            )
            detail = data.track_detail("24")
            trajectory = detail["motion_trajectory"]
            self.assertEqual(trajectory["coordinate_system"], "image_plane_pixels")
            self.assertEqual(trajectory["summary"]["observations"], 4)
            self.assertIn("motion_state", data.track_list()[0])
            self.assertNotIn("C:\\", json.dumps(trajectory))
            self.assertNotIn("OneDrive", json.dumps(trajectory))

    def test_current_phase5_semantics_load_without_matching_historical_scores(self) -> None:
        source_settings = get_settings()
        with tempfile.TemporaryDirectory() as temporary_root:
            target_root = Path(temporary_root)
            target_settings = get_settings(target_root)
            for name, source_path in source_settings.paths.items():
                if source_path.suffix != ".csv":
                    continue
                target_path = target_settings.paths[name]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, target_path)

            selected_path = target_settings.paths["selected_evidence"]
            with selected_path.open("w", newline="", encoding="utf-8") as output:
                csv.writer(output).writerow(
                    (
                        "track_id",
                        "frame",
                        "source",
                        "x1",
                        "y1",
                        "x2",
                        "y2",
                        "confidence",
                        "crop_path",
                        "selection_reason",
                    )
                )

            fusion_path = target_settings.paths["fusion"]
            with fusion_path.open(newline="", encoding="utf-8-sig") as source:
                fusion_rows = list(csv.DictReader(source))
                fusion_fields = fusion_rows[0].keys()
            reliability_path = target_settings.paths["reliability"]
            with reliability_path.open(newline="", encoding="utf-8-sig") as source:
                reliability = {
                    row["track_id"]: row for row in csv.DictReader(source)
                }
            track = next(row for row in fusion_rows if row["track_id"] == "3")
            track["final_confidence"] = str(
                float(track["detector_confidence"])
                * float(reliability["3"]["reliability_score"])
            )
            for row in fusion_rows:
                row["radar_evidence_available"] = "false"
            with fusion_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=fusion_fields)
                writer.writeheader()
                writer.writerows(fusion_rows)

            for row in reliability.values():
                row.update(
                    {
                        "legacy_motion_score": row["motion_score"],
                        "trajectory_motion_score": "0.75",
                        "trajectory_direction_consistency": "0.8",
                        "trajectory_straightness_ratio": "0.7",
                        "trajectory_speed_stability": "0.6",
                        "trajectory_acceleration_stability": "0.5",
                        "trajectory_continuity": "1.0",
                        "trajectory_motion_state": "STEADY_MOVEMENT",
                        "trajectory_motion_quality": "USABLE_IMAGE_PLANE",
                    }
                )
            with reliability_path.open("w", newline="", encoding="utf-8") as output:
                fieldnames = tuple(reliability["3"].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(reliability.values())

            data = ProjectData(target_settings)
            self.assertAlmostEqual(
                data.fusion_result("3")["final_confidence"],
                float(track["final_confidence"]),
            )
            self.assertEqual(
                data.fusion_result("3")["radar"]["track_association_status"],
                "not_established",
            )
            reliability_api = data.track_detail("3")["reliability"]
            self.assertAlmostEqual(reliability_api["trajectory_motion_score"], 0.75)
            self.assertEqual(reliability_api["legacy_motion_score"], reliability_api["motion_score"])
            self.assertEqual(
                reliability_api["trajectory_motion_state"], "STEADY_MOVEMENT"
            )
            self.assertIn("not included in the historical reliability score", reliability_api["trajectory_motion_interpretation"])

    def test_unknown_and_malformed_track_ids(self) -> None:
        missing = self.get_json("/api/tracks/999", expected_status=404)
        self.assertEqual(missing["error"], "track_not_found")
        malformed = self.get_json("/api/tracks/nope", expected_status=400)
        self.assertEqual(malformed["error"], "malformed_track_id")

    def test_unsupported_methods_and_query_parameters_are_rejected(self) -> None:
        request = Request(
            self.base_url + "/api/health",
            headers={"Cookie": self.auth_cookie},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 405)
        query = self.get_json("/api/tracks?limit=1", expected_status=400)
        self.assertEqual(query["error"], "malformed_request")

    def test_config_uses_single_project_root(self) -> None:
        settings = get_settings()
        self.assertEqual(settings.paths["tracks"], settings.results_root / "tracks.csv")


if __name__ == "__main__":
    unittest.main()
