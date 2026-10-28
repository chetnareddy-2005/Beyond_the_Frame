"""Backend-derived operational summaries for authenticated dashboards."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.authorized_drones import list_authorized_drones
from backend.headquarters_sectors import headquarters_database


def _utc_day(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(timezone.utc).date().isoformat()


def _run_report(record: Any) -> dict[str, Any] | None:
    report_path = record.root / "final_results.json"
    if not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return report if isinstance(report, dict) and isinstance(report.get("tracks"), list) else None


def _report_alert_count(report: dict[str, Any]) -> int:
    return int(report.get("uav_alert") is True)


def build_sector_dashboard_summary(
    project_root: str | Path,
    sector_id: str,
    records: Iterable[Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize only the sector account's runs and its assigned approved devices."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Dashboard summary time must include a timezone.")
    today = moment.astimezone(timezone.utc).date()
    start_day = today - timedelta(days=6)
    by_day = {
        (start_day + timedelta(days=offset)).isoformat(): {
            "date": (start_day + timedelta(days=offset)).isoformat(),
            "drone": 0,
            "bird": 0,
            "unknown": 0,
            "alerts": 0,
        }
        for offset in range(7)
    }

    runs = sorted(
        list(records),
        key=lambda record: str(record.created_at or ""),
        reverse=True,
    )
    completed = [record for record in runs if record.status == "completed"]
    reports_complete = True
    total_tracks = 0
    review_alerts = 0
    for record in completed:
        report = _run_report(record)
        if report is None:
            reports_complete = False
            continue
        tracks = [track for track in report["tracks"] if isinstance(track, dict)]
        total_tracks += len(tracks)
        alert_count = _report_alert_count(report)
        review_alerts += alert_count
        day = _utc_day(record.completed_at)
        if day not in by_day:
            continue
        bucket = by_day[day]
        for track in tracks:
            label = str(track.get("final_label", "")).strip().casefold()
            key = "drone" if label in {"uav", "drone"} else "bird" if label == "bird" else "unknown"
            bucket[key] += 1
        bucket["alerts"] += alert_count

    database = headquarters_database(project_root)
    device_count: int | None = None
    if database.is_file():
        day_text = today.isoformat()
        device_count = sum(
            1
            for item in list_authorized_drones(str(project_root))
            if str(item.get("sector_id", "")).upper() == sector_id.upper()
            and item.get("status") == "approved"
            and str(item.get("valid_from", ""))[:10] <= day_text
            and str(item.get("valid_until", ""))[:10] >= day_text
        )

    recent_tracks = None if completed and not reports_complete else total_tracks
    trend_available = bool(completed) and reports_complete
    recent_activity = [
        {
            "run_id": record.run_id,
            "status": record.status,
            "input_type": record.input_type,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
        }
        for record in runs[:5]
    ]
    trend = list(by_day.values()) if trend_available else []
    return {
        "authorized_devices": device_count,
        "recent_tracks": recent_tracks,
        "review_alerts": None if completed and not reports_complete else review_alerts,
        "completed_analyses": len(completed),
        "daily_detection_trend": trend,
        "daily_alert_trend": trend,
        "trend_available": trend_available,
        "recent_activity": recent_activity,
        "window_start": start_day.isoformat(),
        "window_end": today.isoformat(),
    }


def build_headquarters_trends(
    sector_runs: Iterable[tuple[str, Iterable[Any]]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate report-derived trends without exposing per-account run data."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Dashboard summary time must include a timezone.")
    today = moment.astimezone(timezone.utc).date()
    start_day = today - timedelta(days=6)
    daily = {
        (start_day + timedelta(days=offset)).isoformat(): {
            "date": (start_day + timedelta(days=offset)).isoformat(),
            "drone": 0,
            "bird": 0,
            "unknown": 0,
            "alerts": 0,
        }
        for offset in range(7)
    }
    completed_count = 0
    available = True
    for _sector_id, records in sector_runs:
        for record in records:
            if record.status != "completed":
                continue
            completed_count += 1
            report = _run_report(record)
            if report is None:
                available = False
                continue
            day = _utc_day(record.completed_at)
            if day not in daily:
                continue
            bucket = daily[day]
            for track in report["tracks"]:
                if not isinstance(track, dict):
                    continue
                label = str(track.get("final_label", "")).strip().casefold()
                key = "drone" if label in {"uav", "drone"} else "bird" if label == "bird" else "unknown"
                bucket[key] += 1
            bucket["alerts"] += _report_alert_count(report)
    return {
        "available": bool(completed_count) and available,
        "daily_detection_trend": list(daily.values()) if completed_count and available else [],
        "daily_alert_trend": list(daily.values()) if completed_count and available else [],
        "completed_analyses": completed_count,
        "window_start": start_day.isoformat(),
        "window_end": today.isoformat(),
    }
