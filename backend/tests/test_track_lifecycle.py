from __future__ import annotations
import sqlite3
import pytest
from backend.authorized_drones import (
    _trajectory_schema, classify_telemetry, get_track_detail, list_tracks, register_approved_drone, review_track,
)
from backend.headquarters_sectors import headquarters_database, save_headquarters_sectors
from backend.sensor_adapters import normalize_json_window, replay_csv
from backend.trajectory_classifier import classify_points
from backend.trajectory_examples import synthetic_examples

@pytest.fixture
def root(tmp_path):
    save_headquarters_sectors(tmp_path,{"rows":2,"columns":2,"west":0,"east":4,"south":0,"north":4},updated_by=7)
    register_approved_drone(str(tmp_path),{"device_id":"FC-TRACK-1","device_id_type":"flight_controller_serial","sector_id":"S1","valid_from":"2026-01-01T00:00:00Z","valid_until":"2027-01-01T00:00:00Z"},7)
    return tmp_path

def _inside(points):
    # Project the deterministic example into S1's configured [2,4] x [0,2] bounds.
    shifted=[]
    for item in points:
        shifted.append({**item,"latitude":3+(item["latitude"]-3),"longitude":1+(item["longitude"]-1)})
    return shifted

def _payload(points, **extra):
    return {"device_id":"FC-TRACK-1","device_id_type":"flight_controller_serial","track_id":"T-01","source_type":"gps_tracker","source_id":"test-fixture",**extra,"points":_inside(points)}

def test_reproducible_synthetic_classifier_examples():
    for name, example in synthetic_examples().items():
        result,_=classify_points(example["points"])
        assert result["derived_classification"]==example["expected"],name
        assert result["explanation"]
    assert classify_points(synthetic_examples()["insufficient"]["points"])[0]["reason_code"]=="INSUFFICIENT_DATA"

def test_track_event_review_scope_and_decommission_history(root):
    points=synthetic_examples()["drone_like_straight"]["points"]
    first=classify_telemetry(str(root),_payload(points),"S1")
    assert first["derived_classification"]=="drone"
    assert first["authorization_state"]=="authorized"
    assert first["classifier_version"]
    with pytest.raises(PermissionError):
        classify_telemetry(str(root),_payload(points),"S2")
    track=list_tracks(str(root),"S1")[0]
    detail=review_track(str(root),track["registry_id"],"T-01",11,"cleared","HQ completed manual review")
    assert detail["review_status"]=="cleared"
    assert any(event["event_type"]=="hq_review" for event in detail["events"])
    assert list_tracks(str(root),"S2")==[]
    with sqlite3.connect(headquarters_database(root)) as db:
        db.execute("UPDATE headquarters_sectors SET is_active=0 WHERE sector_id='S1'")
        db.execute("UPDATE authorized_drones SET status='revoked' WHERE device_id='FC-TRACK-1'")
    assert list_tracks(str(root))
    assert get_track_detail(str(root),track["registry_id"],"T-01")["review_status"]=="cleared"
    assert get_track_detail(str(root),track["registry_id"],"T-01","S1") is None

def test_replay_source_provenance_and_duplicate_idempotence(root):
    rows=synthetic_examples()["drone_like_straight"]["points"]
    observations=normalize_json_window([{**p,"source_type":"mock","source_id":"demo-node","metadata":{"replay":True}} for p in rows])
    result=classify_telemetry(str(root),{"device_id":"FC-TRACK-1","device_id_type":"flight_controller_serial","track_id":"T-02","observations":observations})
    assert result["source_type"]=="mock"
    assert get_track_detail(str(root),1,"T-02")["points"][0]["metadata"]=={"replay":True}
    again=classify_telemetry(str(root),{"device_id":"FC-TRACK-1","device_id_type":"flight_controller_serial","track_id":"T-02","observations":observations})
    assert again["stale_window"] is True
    older_window=[{**point,"timestamp":point["timestamp"].replace("2026-10-24","2026-10-23")} for point in rows]
    stale=classify_telemetry(str(root),_payload(older_window,track_id="T-02"))
    assert stale["stale_window"] is True
    detail=get_track_detail(str(root),1,"T-02")
    assert detail["first_seen_at"].startswith("2026-10-23")
    assert all(event["device_id"]=="FC-TRACK-1" for event in detail["events"])
    assert len([e for e in detail["events"] if e["event_type"]=="track_opened"])==1
    assert len([e for e in detail["events"] if e["event_type"]=="observation_ingested"])==2*len(rows)

def test_csv_replay_is_explicitly_labeled():
    parsed=replay_csv("timestamp,latitude,longitude,altitude_m\n2026-10-24T10:00:00Z,3,1,100\n")
    assert parsed[0]["source_type"]=="csv_replay"
    assert parsed[0]["source_id"]=="csv-replay"
    with pytest.raises(ValueError,match="requires"):
        replay_csv("latitude,longitude\n3,1\n")


def test_track_schema_migration_is_idempotent_and_adds_event_device_identity(tmp_path):
    database=tmp_path/"legacy.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE trajectory_events(
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id INTEGER NOT NULL,
            track_id TEXT NOT NULL,
            sector_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            actor_user_id INTEGER,
            previous_value TEXT NOT NULL DEFAULT '',
            new_value TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            event_key TEXT UNIQUE)""")
        _trajectory_schema(db)
        _trajectory_schema(db)
        columns={row[1] for row in db.execute("PRAGMA table_info(trajectory_events)")}
    assert "device_id" in columns
