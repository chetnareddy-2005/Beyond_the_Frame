import unittest

import numpy as np

from multimodal.boreas_radar.association import (
    ProjectedCandidate,
    associate_candidates,
    distance_to_box,
)
from multimodal.boreas_radar.candidate_extraction import extract_candidates
from multimodal.boreas_radar.config import (
    AssociationConfig,
    CandidateExtractionConfig,
)
from multimodal.boreas_radar.geometry import (
    ObjectPolarGeometry,
    angular_error_rad,
    associate_polar_candidates,
    cartesian_to_polar,
    object_overlaps_radar_range,
    polar_candidate_match,
    transform_lidar_point_to_radar,
)
from multimodal.boreas_radar.geometric_validation import (
    RESULT_FIELDS,
    _status_row,
)
from multimodal.boreas_radar.projection import (
    project_candidate_to_camera,
    project_ground_truth_box,
)
from multimodal.boreas_radar.radar_data import RadarCandidate, RadarScan


class CandidateExtractionTests(unittest.TestCase):
    def test_local_peak_keeps_original_timestamp_range_angle_and_intensity(self):
        polar = np.zeros((8, 20), dtype=np.float32)
        polar[3, 10] = 0.8
        scan = RadarScan(
            frame_timestamp_us=1000,
            polar_intensity=polar,
            azimuths_rad=np.arange(8, dtype=np.float64) * 0.2,
            azimuth_timestamps_us=np.arange(8, dtype=np.int64) + 900,
            valid_azimuths=np.ones(8, dtype=bool),
            resolution_m=0.5,
        )
        config = CandidateExtractionConfig(
            intensity_threshold=0.7,
            minimum_range_m=2.5,
            azimuth_nms_radius=1,
            range_nms_radius=1,
        )
        first = extract_candidates(scan, config)
        second = extract_candidates(scan, config)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].azimuth_timestamp_us, 903)
        self.assertEqual(first[0].range_m, 5.25)
        self.assertAlmostEqual(first[0].intensity, 0.8)
        np.testing.assert_allclose(first[0].point_radar, [5.25 * np.cos(0.6), 5.25 * np.sin(0.6), 0, 1])

    def test_invalid_azimuth_rows_are_excluded(self):
        polar = np.zeros((4, 12), dtype=np.float32)
        polar[2, 8] = 0.9
        scan = RadarScan(1, polar, np.zeros(4), np.arange(4), np.array([1, 1, 0, 1]), 0.5)
        candidates = extract_candidates(
            scan,
            CandidateExtractionConfig(
                intensity_threshold=0.5,
                minimum_range_m=2.5,
                azimuth_nms_radius=0,
                range_nms_radius=0,
            ),
        )
        self.assertEqual(candidates, [])


class ProjectionAndAssociationTests(unittest.TestCase):
    def test_projection_uses_calibration_direction_and_camera_projection(self):
        candidate = RadarCandidate(1000, 1000, 0, 10, 10.0, 0.0, 0.8)
        identity = np.eye(4)
        radar_to_camera = np.eye(4)
        radar_to_camera[:3, :3] = np.array(
            [
                [0.0, 0.0, -1.0],
                [0.0, 1.0, 0.0],
                [1.0, 0.0, 0.0],
            ]
        )
        projection = np.eye(4)
        projection[0, 0] = 100
        projection[1, 1] = 100
        projection[0, 2] = 50
        projection[1, 2] = 50
        projection[2, 2] = 1
        state, pixel, xyz = project_candidate_to_camera(
            candidate,
            identity,
            identity,
            identity,
            radar_to_camera,
            projection,
            100,
            100,
        )
        self.assertEqual(state, "VALID")
        self.assertEqual(pixel, (50.0, 50.0))
        np.testing.assert_allclose(xyz, [0.0, 0.0, 10.0])

    def test_candidate_outside_fov_and_behind_camera_are_explicit(self):
        candidate = RadarCandidate(1, 1, 0, 10, 10.0, 0.0, 0.8)
        identity = np.eye(4)
        projection = np.eye(4)
        projection[0, 0] = 100
        projection[1, 1] = 100
        projection[0, 2] = 50
        projection[1, 2] = 50
        lidar_to_camera = identity.copy()
        lidar_to_camera[2, 3] = -20
        state, _, _ = project_candidate_to_camera(
            candidate, identity, identity, identity, lidar_to_camera, projection, 100, 100
        )
        self.assertEqual(state, "INVALID_PROJECTION")

    def test_box_projection_rejects_crossing_camera_plane(self):
        class FakeBox:
            pc = type("PC", (), {"points": np.array([
                [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
            ])})

        state, box = project_ground_truth_box(FakeBox(), np.eye(4), 100, 100)
        self.assertEqual(state, "INVALID_PROJECTION")
        self.assertIsNone(box)

    def test_association_is_bbox_proximity_and_empty_is_no_match(self):
        candidate = RadarCandidate(1, 1, 1, 20, 4.0, 0.2, 0.7)
        point = ProjectedCandidate(candidate, (12.0, 15.0), (1.0, 2.0, 3.0))
        box = (10.0, 10.0, 20.0, 20.0)
        self.assertEqual(distance_to_box(point.pixel, box), 0.0)
        associated, nearest, distance = associate_candidates([point], box, 8.0)
        self.assertEqual(associated, [point])
        self.assertIs(nearest, point)
        self.assertEqual(distance, 0.0)
        self.assertEqual(associate_candidates([], box, 8.0), ([], None, None))

    def test_config_rejects_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            CandidateExtractionConfig(intensity_threshold=1.1)
        with self.assertRaises(ValueError):
            AssociationConfig(maximum_scan_delta_seconds=-0.1)


class RadarGeometryTests(unittest.TestCase):
    def test_associated_csv_row_preserves_polar_values_and_scan_candidate_index(self):
        candidate = RadarCandidate(
            frame_timestamp_us=2000,
            azimuth_timestamp_us=1990,
            azimuth_index=14,
            range_bin=70,
            range_m=4.25,
            azimuth_rad=np.deg2rad(-179.5),
            intensity=0.82,
        )
        row = _status_row(
            object_uuid="uuid-1",
            object_class="Car",
            camera_timestamp=2001,
            label_timestamp=2000,
            radar_timestamp=2000,
            timestamp_delta_us=0,
            expected_range=5.5,
            expected_azimuth=np.deg2rad(179.0),
            expected_scan_range=5.4,
            expected_scan_azimuth=np.deg2rad(178.9),
            candidate=candidate,
            range_error=1.25,
            azimuth_error=np.deg2rad(1.5),
            dimensions=np.array([4.0, 2.0, 1.5]),
            location=np.array([1.0, 2.0, 3.0]),
            orientation_yaw_rad=0.2,
            status="ASSOCIATED",
            radar_candidate_count=8,
            candidates_compared=4,
            candidate_index=3,
        )
        self.assertEqual(row["object_uuid"], "uuid-1")
        self.assertEqual(row["object_class"], "Car")
        self.assertEqual(row["expected_range_m"], 5.5)
        self.assertEqual(row["expected_azimuth_deg"], 179.0)
        self.assertEqual(row["radar_candidate_range_m"], 4.25)
        self.assertEqual(row["radar_candidate_azimuth_deg"], -179.5)
        self.assertEqual(row["radar_candidate_index"], 3)
        self.assertEqual(row["range_error_m"], 1.25)
        self.assertEqual(row["azimuth_error_deg"], 1.5)
        self.assertEqual(row["associated_radar_count"], 1)
        self.assertEqual(row["association_status"], "ASSOCIATED")
        self.assertIn("association_distance_px", RESULT_FIELDS)
        self.assertEqual(row["association_distance_px"], "")

    def test_non_associated_csv_row_preserves_empty_match_fields_and_status(self):
        row = _status_row(
            object_uuid="uuid-2",
            object_class="Car",
            camera_timestamp=2001,
            label_timestamp=2000,
            radar_timestamp=2000,
            timestamp_delta_us=0,
            expected_range=5.5,
            expected_azimuth=0.3,
            expected_scan_range=5.4,
            expected_scan_azimuth=0.3,
            candidate=None,
            range_error=None,
            azimuth_error=None,
            dimensions=np.array([4.0, 2.0, 1.5]),
            location=np.array([1.0, 2.0, 3.0]),
            orientation_yaw_rad=0.2,
            status="NO_RADAR_ASSOCIATION",
            radar_candidate_count=0,
            candidates_compared=0,
        )
        self.assertEqual(row["association_status"], "NO_RADAR_ASSOCIATION")
        self.assertEqual(row["associated_radar_count"], 0)
        self.assertEqual(row["radar_candidate_range_m"], "")
        self.assertEqual(row["radar_candidate_azimuth_deg"], "")
        self.assertEqual(row["radar_candidate_index"], "")
        self.assertEqual(row["range_error_m"], "")
        self.assertEqual(row["azimuth_error_deg"], "")

    def test_lidar_point_transform_uses_official_radar_extrinsic_direction(self):
        lidar_point = np.array([3.0, 4.0, 1.0, 1.0])
        identity = np.eye(4)
        T_radar_lidar = np.eye(4)
        T_radar_lidar[0, 3] = 2.0
        transformed = transform_lidar_point_to_radar(
            lidar_point, identity, identity, T_radar_lidar
        )
        np.testing.assert_allclose(transformed, [5.0, 4.0, 1.0, 1.0])

    def test_range_and_azimuth_are_computed_from_radar_xy(self):
        range_m, azimuth_rad = cartesian_to_polar(np.array([3.0, 4.0, 8.0]))
        self.assertEqual(range_m, 5.0)
        self.assertAlmostEqual(azimuth_rad, np.arctan2(4.0, 3.0))

    def test_angular_error_wraps_across_pi(self):
        self.assertAlmostEqual(
            angular_error_rad(np.deg2rad(179.0), np.deg2rad(-179.0)),
            np.deg2rad(2.0),
        )

    def test_candidate_within_3d_box_gate_is_associated(self):
        geometry = ObjectPolarGeometry(
            range_m=20.0,
            azimuth_rad=np.deg2rad(179.5),
            range_extent_m=1.5,
            horizontal_extent_m=2.0,
        )
        candidate = RadarCandidate(
            1000, 1000, 12, 100, 20.5, np.deg2rad(-179.7), 0.83
        )
        match = polar_candidate_match(
            geometry,
            candidate,
            range_padding_m=0.5,
            azimuth_padding_rad=np.deg2rad(0.5),
        )
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match[1], 0.5)
        self.assertAlmostEqual(match[2], np.deg2rad(0.8))

    def test_candidate_outside_range_or_azimuth_gate_is_rejected(self):
        geometry = ObjectPolarGeometry(20.0, 0.0, 1.0, 1.0)
        too_far = RadarCandidate(1000, 1000, 12, 100, 25.0, 0.0, 0.9)
        too_wide = RadarCandidate(
            1000, 1000, 12, 100, 20.0, np.deg2rad(20), 0.9
        )
        self.assertIsNone(polar_candidate_match(geometry, too_far, 1.0, 0.0))
        self.assertIsNone(polar_candidate_match(geometry, too_wide, 1.0, 0.0))

    def test_object_outside_radar_range_is_out_of_fov(self):
        geometry = ObjectPolarGeometry(250.0, 0.0, 2.0, 1.0)
        self.assertFalse(object_overlaps_radar_range(geometry, 2.5, 200.0))
        crossing = ObjectPolarGeometry(201.0, 0.0, 2.0, 1.0)
        self.assertTrue(object_overlaps_radar_range(crossing, 2.5, 200.0))

    def test_missing_candidates_produce_no_association(self):
        geometry = ObjectPolarGeometry(20.0, 0.2, 1.0, 1.0)
        self.assertEqual(
            associate_polar_candidates(geometry, [], 2.0, np.deg2rad(1)),
            (None, None, None),
        )


if __name__ == "__main__":
    unittest.main()
