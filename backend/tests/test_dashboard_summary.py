from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.dashboard_summary import build_headquarters_trends, build_sector_dashboard_summary
from backend.headquarters_sectors import load_headquarters_sectors


def _run(root, run_id, report, completed_at="2026-10-28T12:00:00Z", status="completed"):
    run_root = root / run_id
    run_root.mkdir(parents=True)
    if report is not None:
        (run_root / "final_results.json").write_text(json.dumps(report), encoding="utf-8")
    return SimpleNamespace(
        root=run_root,
        run_id=run_id,
        status=status,
        input_type="rgb",
        created_at=completed_at,
        completed_at=completed_at,
    )


def test_sector_summary_counts_only_supplied_account_runs_and_real_report_data(tmp_path):
    load_headquarters_sectors(tmp_path)
    own = _run(tmp_path, "own", {
        "tracks": [
            {"track_id": "1", "final_label": "Drone"},
            {"track_id": "2", "final_label": "Bird"},
            {"track_id": "3", "final_label": "Unknown"},
        ],
        "uav_alert": True,
        "alert_track_ids": ["1"],
    })
    other_account = _run(tmp_path, "other", {
        "tracks": [{"track_id": "other", "final_label": "Drone"}],
        "uav_alert": True,
        "alert_track_ids": ["other"],
    })

    summary = build_sector_dashboard_summary(
        tmp_path,
        "S1",
        [own],
        now=datetime(2026, 10, 28, 13, tzinfo=timezone.utc),
    )

    assert summary["recent_tracks"] == 3
    assert summary["review_alerts"] == 1
    assert summary["completed_analyses"] == 1
    assert summary["trend_available"] is True
    assert summary["daily_detection_trend"][-1] == {
        "date": "2026-10-28",
        "drone": 1,
        "bird": 1,
        "unknown": 1,
        "alerts": 1,
    }
    assert "other" not in {item["run_id"] for item in summary["recent_activity"]}
    assert other_account.run_id != own.run_id


def test_sector_summary_reports_missing_data_without_inventing_zero_counts(tmp_path):
    load_headquarters_sectors(tmp_path)
    incomplete = _run(tmp_path, "incomplete", None)
    summary = build_sector_dashboard_summary(
        tmp_path,
        "S1",
        [incomplete],
        now=datetime(2026, 10, 28, 13, tzinfo=timezone.utc),
    )
    assert summary["recent_tracks"] is None
    assert summary["review_alerts"] is None
    assert summary["trend_available"] is False
    assert summary["daily_detection_trend"] == []


def test_headquarters_trends_aggregate_only_report_data_and_empty_state(tmp_path):
    sector_one = _run(tmp_path, "sector-one", {
        "tracks": [{"track_id": "1", "final_label": "Bird"}],
        "uav_alert": False,
    })
    sector_two = _run(tmp_path, "sector-two", {
        "tracks": [{"track_id": "2", "final_label": "Drone"}],
        "uav_alert": True,
    })
    trends = build_headquarters_trends(
        [("S1", [sector_one]), ("S2", [sector_two])],
        now=datetime(2026, 10, 28, 13, tzinfo=timezone.utc),
    )
    assert trends["available"] is True
    assert trends["daily_detection_trend"][-1]["bird"] == 1
    assert trends["daily_detection_trend"][-1]["drone"] == 1
    assert trends["daily_alert_trend"][-1]["alerts"] == 1
    empty = build_headquarters_trends([], now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    assert empty["available"] is False
    assert empty["daily_detection_trend"] == []
