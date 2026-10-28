import csv, json
from pathlib import Path
import pytest


def _make_fusion_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["track_id", "detector_label", "final_label", "final_confidence", "fusion_mode", "reliability_status"]
    with path.open("w", newline="", encoding="utf-8") as f:
        import csv as _csv
        w = _csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


class TestWriteFinalResults:
    def test_uav_alert_true(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [{"track_id": "1", "final_label": "UAV", "final_confidence": "0.9",
                               "detector_label": "Drone", "fusion_mode": "detector_temporal", "reliability_status": "CONFIDENT"}])
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        assert d["uav_alert"] is True
        assert d["track_count"] == 1
        assert "generated_at" in d

    def test_no_uav_no_alert(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [{"track_id": "1", "final_label": "Bird", "final_confidence": "0.8",
                               "detector_label": "Bird", "fusion_mode": "detector_temporal", "reliability_status": "CONFIDENT"}])
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        assert d["uav_alert"] is False

    def test_temporal_abstention_requires_review_without_uav_alert(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [{"track_id": "1", "final_label": "Other", "final_confidence": "0.42",
                               "detector_label": "Drone", "fusion_mode": "detector_temporal_abstain",
                               "reliability_status": "UNCERTAIN"}])
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        assert d["uav_alert"] is False
        assert d["tracks"][0]["final_label"] == "Other"
        assert d["tracks"][0]["review_status"] == "review_recommended"

    def test_missing_csv_empty_result(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        assert d["tracks"] == []
        assert d["uav_alert"] is False

    def test_atomic_no_tmp_left(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [])
        _write_final_results(tmp_path, fc)
        assert not (tmp_path / "final_results.json.tmp").exists()


class TestListRuns:
    def _mgr(self, tmp_path):
        from backend.runs import RunManager
        return RunManager(tmp_path, max_upload_bytes=1024 * 1024, runner=lambda *a: None)

    def test_newest_first(self, tmp_path):
        m = self._mgr(tmp_path)
        v = b"x" * 100
        r1 = m.create_run("a.mp4", v)
        r2 = m.create_run("b.mp4", v)
        r3 = m.create_run("c.mp4", v)
        ids = [r.run_id for r in m.list_runs()]
        assert ids == [r3.run_id, r2.run_id, r1.run_id]

    def test_empty(self, tmp_path):
        m = self._mgr(tmp_path)
        assert m.list_runs() == []

    def test_count(self, tmp_path):
        m = self._mgr(tmp_path)
        for _ in range(3):
            m.create_run("v.mp4", b"y" * 100)
        assert len(m.list_runs()) == 3


class TestAlertContract:
    def test_uav_alert_message(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [{"track_id": "1", "final_label": "UAV", "final_confidence": "0.93",
                               "detector_label": "Drone", "fusion_mode": "detector_temporal", "reliability_status": "CONFIDENT"}])
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        alert = {"level": "HIGH", "message": "One or more tracks have been classified as UAV. Immediate review recommended."} if d["uav_alert"] else None
        assert alert is not None
        assert alert["level"] == "HIGH"

    def test_no_uav_alert_none(self, tmp_path):
        from backend.run_pipeline import _write_final_results
        fc = tmp_path / "fusion" / "fusion_results.csv"
        _make_fusion_csv(fc, [{"track_id": "1", "final_label": "Bird", "final_confidence": "0.75",
                               "detector_label": "Bird", "fusion_mode": "detector_temporal", "reliability_status": "UNCERTAIN"}])
        _write_final_results(tmp_path, fc)
        d = json.loads((tmp_path / "final_results.json").read_text())
        alert = {"level": "HIGH"} if d["uav_alert"] else None
        assert alert is None
