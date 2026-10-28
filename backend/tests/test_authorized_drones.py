from __future__ import annotations

import json
import sqlite3

import pytest

from backend.authorized_drones import (
    classify_telemetry,
    list_authorized_drones,
    register_approved_drone,
    revoke_authorized_drone,
)
from backend.headquarters_sectors import load_headquarters_sectors, save_headquarters_sectors


@pytest.fixture
def hq_root(tmp_path):
    config = {"rows": 2, "columns": 2, "west": 0, "east": 4, "south": 0, "north": 4}
    saved = save_headquarters_sectors(tmp_path, config, updated_by=7)
    assert [sector["id"] for sector in saved["sectors"]] == ["S1", "S2", "S3", "S4"]
    return tmp_path


def _register(root, device_id="FC-UNIT-01"):
    return register_approved_drone(
        str(root),
        {
            "device_id": device_id,
            "device_id_type": "flight_controller_serial",
            "sector_id": "S1",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2027-01-01T00:00:00Z",
            "label": "Simulation aircraft 01",
        },
        approved_by=7,
    )


def _classify(root, **overrides):
    telemetry = {
        "device_id": "fc-unit-01",
        "device_id_type": "flight_controller_serial",
        "latitude": 3,
        "longitude": 1,
        "timestamp": "2026-10-24T10:00:00Z",
    }
    telemetry.update(overrides)
    return classify_telemetry(str(root), telemetry)


def test_classifies_authorized_other_sector_unknown_and_expired_out_of_area(hq_root):
    _register(hq_root)

    authorized = _classify(hq_root)
    assert authorized["state"] == "authorized"
    assert authorized["home_sector"] == authorized["current_sector"] == "S1"

    other_sector = _classify(hq_root, latitude=3, longitude=3)
    assert other_sector["state"] == "other-sector"
    assert other_sector["home_sector"] == "S1"
    assert other_sector["current_sector"] == "S2"

    out_of_area = _classify(hq_root, latitude=8, longitude=1)
    assert out_of_area["state"] == "expired/out-of-area"
    assert out_of_area["reason"] == "outside_active_sector_geometry"

    expired = _classify(hq_root, timestamp="2027-01-01T00:00:00Z")
    assert expired["state"] == "expired/out-of-area"
    assert expired["reason"] == "validity_window_expired_or_not_started"

    unknown = _classify(hq_root, device_id="FC-UNKNOWN")
    assert unknown["state"] == "unknown"
    assert unknown["hostility_judgment"] is False


def test_revoke_and_list_are_persisted(hq_root):
    created = _register(hq_root)
    listed = list_authorized_drones(str(hq_root))
    assert len(listed) == 1
    assert listed[0]["registry_id"] == created["registry_id"]
    assert listed[0]["sector_id"] == "S1"

    assert revoke_authorized_drone(str(hq_root), created["registry_id"])
    assert _classify(hq_root)["state"] == "unknown"
    assert not revoke_authorized_drone(str(hq_root), created["registry_id"])


def test_geometry_cannot_change_while_referenced_by_registered_drone(hq_root):
    _register(hq_root)
    changed = {"rows": 2, "columns": 2, "west": 0, "east": 5, "south": 0, "north": 4}
    with pytest.raises(ValueError, match="cannot change"):
        save_headquarters_sectors(hq_root, changed, updated_by=7)
    assert load_headquarters_sectors(hq_root)["config"]["east"] == 4.0


def test_telemetry_authorization_uses_saved_polygon_not_sector_rectangle(tmp_path):
    save_headquarters_sectors(tmp_path, {"rows": 2, "columns": 2, "west": 0, "east": 4, "south": 0, "north": 4}, updated_by=7)
    database = tmp_path / "results" / "authorization" / "headquarters.sqlite3"
    polygon = {"type": "Polygon", "coordinates": [[[0, 2], [2, 2], [0, 4], [0, 2]]]}
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE headquarters_sectors SET geometry_json=? WHERE sector_id='S1'", (json.dumps(polygon),))
    _register(tmp_path, "FC-POLYGON-01")

    outside = classify_telemetry(str(tmp_path), {
        "device_id": "FC-POLYGON-01", "device_id_type": "flight_controller_serial",
        "latitude": 2.2, "longitude": 1.9, "timestamp": "2026-10-24T10:00:00Z",
    })
    assert outside["state"] == "expired/out-of-area"
    assert outside["current_sector"] is None

    inside = classify_telemetry(str(tmp_path), {
        "device_id": "FC-POLYGON-01", "device_id_type": "flight_controller_serial",
        "latitude": 3, "longitude": 0.5, "timestamp": "2026-10-24T10:00:00Z",
    })
    assert inside["state"] == "authorized"
    assert inside["current_sector"] == "S1"

    points = [
        {"latitude": 2.2, "longitude": 1.9, "altitude_m": 100, "timestamp": f"2026-10-24T10:00:0{i}Z"}
        for i in range(3)
    ]
    tracked = classify_telemetry(str(tmp_path), {
        "device_id": "FC-POLYGON-01", "device_id_type": "flight_controller_serial",
        "track_id": "outside-approved-polygon", "points": points,
    })
    assert tracked["authorization_state"] == "expired/out-of-area"

def test_device_identifier_normalization_and_validation(hq_root):
    created = register_approved_drone(
        str(hq_root),
        {
            "device_id": "550E8400-E29B-41D4-A716-446655440000",
            "device_id_type": "gps_tracker_uuid",
            "sector_id": "S1",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_until": "2027-01-01T00:00:00+00:00",
        },
        approved_by=7,
    )
    assert created["device_id"] == "550e8400-e29b-41d4-a716-446655440000"
    with pytest.raises(ValueError, match="valid UUID"):
        register_approved_drone(
            str(hq_root),
            {"device_id": "not-a-uuid", "device_id_type": "gps_tracker_uuid", "sector_id": "S1",
             "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z"},
            approved_by=7,
        )


def test_headquarters_custom_named_areas_are_persisted_and_used_for_classification(tmp_path):
    config = {
        "rows": 2, "columns": 2, "west": 0, "east": 4, "south": 0, "north": 4,
        "sector_areas": [
            {"id": "S1", "name": "North Sector", "bounds": {"west": 0, "east": 2, "south": 2, "north": 4}},
            {"id": "S2", "name": "River Sector", "bounds": {"west": 2, "east": 4, "south": 2, "north": 4}},
            {"id": "S3", "name": "South Sector", "bounds": {"west": 0, "east": 2, "south": 0, "north": 2}},
            {"id": "S4", "name": "East Sector", "bounds": {"west": 2, "east": 4, "south": 0, "north": 2}},
        ],
    }
    saved = save_headquarters_sectors(tmp_path, config, updated_by=7)
    loaded = load_headquarters_sectors(tmp_path)
    assert [sector["name"] for sector in loaded["sectors"]] == [
        "North Sector", "River Sector", "South Sector", "East Sector"
    ]
    assert loaded["config"]["sector_areas"] == saved["config"]["sector_areas"]
    created = register_approved_drone(
        str(tmp_path),
        {
            "device_id": "FC-NORTH-01", "device_id_type": "flight_controller_serial",
            "sector_id": "S1", "valid_from": "2026-01-01T00:00:00Z",
            "valid_until": "2027-01-01T00:00:00Z", "label": "Test aircraft",
        },
        approved_by=7,
    )
    assert created["sector_id"] == "S1"
    classified = classify_telemetry(str(tmp_path), {
        "device_id": "FC-NORTH-01", "device_id_type": "flight_controller_serial",
        "latitude": 3, "longitude": 1, "timestamp": "2026-10-24T10:00:00Z",
    })
    assert classified["state"] == "authorized"
    assert classified["current_sector"] == "S1"
