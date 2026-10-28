import unittest

from multimodal.caltech_evidence import (
    CaltechRecordingSources,
    CaltechTrackObservation,
    build_caltech_track_evidence,
)
from multimodal.contracts import FrameReference
from multimodal.track_association import associate_track
from multimodal.thermal_adapter import ThermalAdapter


RGB_SOURCE = "caltech-bag:/eo/color/image_color/compressed"
THERMAL_SOURCE = "caltech-bag:/boson/thermal/image_raw"
RECORDING_ID = "2022-05-08-11-44-33.bag"


class CaltechEvidenceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.recording_sources = CaltechRecordingSources(
            recording_id=RECORDING_ID,
            rgb_source=RGB_SOURCE,
            thermal_source=THERMAL_SOURCE,
        )
        self.rgb_frame = FrameReference(RGB_SOURCE, 30, 12.0)
        self.thermal_frame = FrameReference(THERMAL_SOURCE, 7, 12.012)
        self.track = CaltechTrackObservation(
            recording_id=RECORDING_ID,
            source_video=RGB_SOURCE,
            frame_index=30,
            track_id=4,
        )

    def build(self, **kwargs):
        return build_caltech_track_evidence(
            kwargs.get("rgb_frame", self.rgb_frame),
            kwargs.get("track_observation", self.track),
            kwargs.get("recording_sources", self.recording_sources),
            kwargs.get("thermal_frame", self.thermal_frame),
            kwargs.get("max_time_delta_sec", 0.02),
        )

    def test_builds_linked_rgb_thermal_evidence_for_same_track(self):
        records = self.build()

        self.assertEqual([record.modality for record in records], ["rgb", "thermal"])
        self.assertEqual({record.track_id for record in records}, {4})
        self.assertTrue(all(record.available for record in records))
        self.assertEqual(
            associate_track(RGB_SOURCE, 4, records, frame_index=30, timestamp_sec=12.0),
            records,
        )
        linked = records[1].evidence
        self.assertEqual(linked["recording_id"], RECORDING_ID)
        self.assertEqual(linked["rgb_source_video"], RGB_SOURCE)
        self.assertEqual(linked["rgb_frame_index"], 30)
        self.assertEqual(linked["rgb_timestamp_sec"], 12.0)
        self.assertEqual(linked["thermal_source_video"], THERMAL_SOURCE)
        self.assertEqual(linked["thermal_frame_index"], 7)
        self.assertEqual(linked["thermal_timestamp_sec"], 12.012)
        self.assertAlmostEqual(linked["synchronization_delta_sec"], 0.012)
        self.assertEqual(linked["synchronization_status"], "rgb_thermal")
        self.assertTrue(linked["rgb_available"])
        self.assertTrue(linked["thermal_available"])

    def test_rejects_timestamp_mismatch_beyond_tolerance(self):
        with self.assertRaisesRegex(ValueError, "not timestamp-synchronized"):
            self.build(thermal_frame=FrameReference(THERMAL_SOURCE, 7, 12.1))

    def test_rejects_wrong_rgb_source(self):
        with self.assertRaisesRegex(ValueError, "RGB frame source"):
            self.build(rgb_frame=FrameReference("other.bag:/rgb", 30, 12.0))

    def test_rejects_unrelated_thermal_source(self):
        with self.assertRaisesRegex(ValueError, "Thermal frame source"):
            self.build(thermal_frame=FrameReference("other.bag:/thermal", 7, 12.012))

    def test_rejects_missing_track_identity(self):
        with self.assertRaisesRegex(ValueError, "track observation"):
            self.build(track_observation=None)
        missing_id = CaltechTrackObservation(RECORDING_ID, RGB_SOURCE, 30, None)
        with self.assertRaisesRegex(ValueError, "track observation"):
            self.build(track_observation=missing_id)

    def test_rejects_track_reused_from_another_recording(self):
        other_track = CaltechTrackObservation("other.bag", RGB_SOURCE, 30, 4)
        with self.assertRaisesRegex(ValueError, "different recording"):
            self.build(track_observation=other_track)

    def test_rejects_unsynchronized_thermal_without_timestamps(self):
        with self.assertRaisesRegex(ValueError, "not timestamp-synchronized"):
            self.build(thermal_frame=FrameReference(THERMAL_SOURCE, 7))

    def test_rgb_only_record_remains_available(self):
        records = build_caltech_track_evidence(
            self.rgb_frame,
            self.track,
            self.recording_sources,
            thermal_frame=None,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].modality, "rgb")
        self.assertTrue(records[0].available)
        self.assertEqual(records[0].evidence["synchronization_status"], "rgb_only")
        self.assertFalse(records[0].evidence["thermal_available"])

    def test_existing_thermal_adapter_evidence_contract_is_unchanged(self):
        adapter = ThermalAdapter(enabled=True, thermal_source=THERMAL_SOURCE)
        record = adapter.extract_evidence(
            source_video=RGB_SOURCE,
            track_id=4,
            frame_index=30,
            timestamp_sec=12.0,
            thermal_frame=object(),
            rgb_reference=self.rgb_frame,
        )

        self.assertEqual(record.modality, "thermal")
        self.assertEqual(record.source_video, RGB_SOURCE)
        self.assertEqual(record.track_id, 4)
        self.assertEqual(record.evidence["thermal_source"], THERMAL_SOURCE)
        self.assertEqual(record.evidence["rgb_source"], RGB_SOURCE)


if __name__ == "__main__":
    unittest.main()
