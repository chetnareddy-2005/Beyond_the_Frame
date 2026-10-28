"""Simulation-only configurable sectors and authorization checks for Phase 10."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
import math
import re
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {"rows":2,"columns":2,"west":78.4800,"east":78.5000,"south":17.3700,"north":17.3900}
SIMULATION_TIME = "2026-10-24T12:00:00Z"

def validate_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Sector settings must be an object.")
    config = {}
    for key, default in DEFAULT_CONFIG.items():
        raw = value.get(key, default)
        if key in ("rows", "columns"):
            if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 8:
                raise ValueError("Rows and columns must each be between 1 and 8.")
            config[key] = raw
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                raise ValueError(f"{key} must be a finite number.")
            config[key] = float(raw)
    if not (-90 <= config["south"] < config["north"] <= 90):
        raise ValueError("Latitude bounds must satisfy -90 ≤ south < north ≤ 90.")
    if not (-180 <= config["west"] < config["east"] <= 180):
        raise ValueError("Longitude bounds must satisfy -180 ≤ west < east ≤ 180.")
    location = value.get("headquarters_location")
    if location is not None:
        if not isinstance(location, dict):
            raise ValueError("Headquarters location must include latitude and longitude.")
        lat, lon = location.get("lat"), location.get("lon")
        if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) for number in (lat, lon)) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Headquarters location is outside valid latitude/longitude bounds.")
        config["headquarters_location"] = {"lat": float(lat), "lon": float(lon)}
    raw_areas = value.get("sector_areas")
    if raw_areas is None:
        if config["rows"] * config["columns"] < 2:
            raise ValueError("Define at least two sectors to enable cross-sector checks.")
        return config
    if not isinstance(raw_areas, list) or not 1 <= len(raw_areas) <= 64:
        raise ValueError("Draw between one and 64 sector areas.")
    areas = []
    seen = set()
    for index, item in enumerate(raw_areas):
        if not isinstance(item, dict):
            raise ValueError("Each sector area must include an ID, name, and rectangular bounds.")
        sector_id = item.get("id")
        name = item.get("name")
        if not isinstance(sector_id, str) or not re.fullmatch(r"S[1-9][0-9]*", sector_id):
            raise ValueError("Sector IDs must use the format S1, S2, and so on.")
        if sector_id in seen:
            raise ValueError("Sector IDs must be unique.")
        seen.add(sector_id)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("Sector names must contain 1 to 80 characters.")
        bounds = item.get("bounds")
        if not isinstance(bounds, dict):
            raise ValueError("Sector bounds are required.")
        checked = {}
        for key in ("west", "east", "south", "north"):
            raw = bounds.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                raise ValueError(f"Sector {sector_id} {key} bound must be finite.")
            checked[key] = float(raw)
        if not (config["west"] <= checked["west"] < checked["east"] <= config["east"]):
            raise ValueError(f"Sector {sector_id} longitude bounds must stay inside the monitored area.")
        if not (config["south"] <= checked["south"] < checked["north"] <= config["north"]):
            raise ValueError(f"Sector {sector_id} latitude bounds must stay inside the monitored area.")
        coverage = item.get("coverage", "")
        if not isinstance(coverage, str) or len(coverage.strip()) > 240:
            raise ValueError(f"Sector {sector_id} coverage must be 240 characters or fewer.")
        areas.append({"id": sector_id, "name": name.strip(), "coverage": coverage.strip(), "row": index // 8, "column": index % 8, "bounds": checked})
    for index, first in enumerate(areas):
        a = first["bounds"]
        for second in areas[index + 1:]:
            b = second["bounds"]
            overlaps_lon = max(a["west"], b["west"]) < min(a["east"], b["east"])
            overlaps_lat = max(a["south"], b["south"]) < min(a["north"], b["north"])
            if overlaps_lon and overlaps_lat:
                raise ValueError(f"Sector areas {first['id']} and {second['id']} overlap.")
    config["sector_areas"] = areas
    return config

def sectors_for(config: dict[str, Any]) -> list[dict[str, Any]]:
    config = validate_config(config)
    if "sector_areas" in config:
        return [dict(item) for item in config["sector_areas"]]
    rows, columns = config["rows"], config["columns"]
    lat_step = (config["north"] - config["south"]) / rows
    lon_step = (config["east"] - config["west"]) / columns
    sectors = []
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column + 1
            sectors.append({"id":f"S{index}","name":f"Sector {index}","row":row,"column":column,"bounds":{"west":config["west"]+column*lon_step,"east":config["west"]+(column+1)*lon_step,"south":config["north"]-(row+1)*lat_step,"north":config["north"]-row*lat_step}})
    return sectors

def _center(sector: dict[str, Any]) -> dict[str, float]:
    b = sector["bounds"]
    return {"lat":(b["south"]+b["north"])/2,"lon":(b["west"]+b["east"])/2}

def simulation_payload(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = validate_config(config or DEFAULT_CONFIG)
    sectors = sectors_for(config)
    home, other = sectors[0], sectors[-1]
    home_point, other_point = _center(home), _center(other)
    demo_registry = [
        {"drone_id":"SIM-UNIT-001","label":"Demo patrol unit 01","home_sector":home["id"],"valid_from":"2026-01-01T00:00:00Z","valid_until":"2027-01-01T00:00:00Z","demo_location":home_point,"synthetic":True},
        {"drone_id":"SIM-UNIT-002","label":"Demo patrol unit 02","home_sector":other["id"],"valid_from":"2026-01-01T00:00:00Z","valid_until":"2027-01-01T00:00:00Z","demo_location":other_point,"synthetic":True},
        {"drone_id":"SIM-UNIT-003","label":"Demo unit with expired permission","home_sector":other["id"],"valid_from":"2026-01-01T00:00:00Z","valid_until":"2026-09-01T00:00:00Z","demo_location":other_point,"synthetic":True},
    ]
    trail = [other_point, {"lat":(other_point["lat"]+home_point["lat"])/2,"lon":(other_point["lon"]+home_point["lon"])/2}, home_point]
    scenarios = [
        {"id":"home-sector","title":"Authorized in home sector","drone_id":"SIM-UNIT-001",**home_point,"time":SIMULATION_TIME,"trail":[home_point]},
        {"id":"cross-sector","title":f"{other['name']} unit enters {home['name']}","drone_id":"SIM-UNIT-002",**home_point,"time":SIMULATION_TIME,"trail":trail},
        {"id":"unknown-id","title":"Unknown aircraft ID","drone_id":"SIM-UNKNOWN-900",**home_point,"time":SIMULATION_TIME,"trail":[]},
        {"id":"missing-gps","title":"GPS unavailable","drone_id":"SIM-UNIT-001","lat":None,"lon":None,"time":SIMULATION_TIME,"trail":[]},
        {"id":"expired-permission","title":"Expired permission","drone_id":"SIM-UNIT-003",**other_point,"time":SIMULATION_TIME,"trail":[other_point]},
    ]
    return {"simulation_only":True,"simulation_time":SIMULATION_TIME,"config":config,"sectors":sectors,"registry":demo_registry,"scenarios":scenarios}

def _point_in_ring(lon: float, lat: float, ring: Any) -> bool:
    """Return whether a lon/lat point is inside a GeoJSON linear ring (edge-inclusive)."""
    if not isinstance(ring, list) or len(ring) < 4:
        return False
    try:
        points = [(float(point[0]), float(point[1])) for point in ring]
    except (TypeError, ValueError, IndexError):
        return False
    inside = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = (lon - x1) * (y2 - y1) - (lat - y1) * (x2 - x1)
        tolerance = 1e-12 * max(1.0, abs(x2 - x1), abs(y2 - y1))
        if abs(cross) <= tolerance and min(x1, x2) - tolerance <= lon <= max(x1, x2) + tolerance and min(y1, y2) - tolerance <= lat <= max(y1, y2) + tolerance:
            return True
        if (y1 > lat) != (y2 > lat):
            intersection_lon = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < intersection_lon:
                inside = not inside
    return inside
def _sector_for(lat: float, lon: float, sectors: list[dict[str, Any]], config: dict[str, Any]) -> str | None:
    if lat == config["north"]:
        lat = math.nextafter(lat, -math.inf)
    if lon == config["east"]:
        lon = math.nextafter(lon, -math.inf)
    for sector in sectors:
        geometry = sector.get("geometry")
        if geometry is not None:
            # HQ-approved geometry is authoritative; malformed geometry fails closed.
            if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
                continue
            rings = geometry.get("coordinates")
            if not isinstance(rings, list) or not rings or not _point_in_ring(lon, lat, rings[0]):
                continue
            if any(_point_in_ring(lon, lat, ring) for ring in rings[1:]):
                continue
            return sector["id"]
        b = sector["bounds"]
        if b["west"] <= lon < b["east"] and b["south"] <= lat < b["north"]:
            return sector["id"]
    return None
def _valid_at(value: str, start: str, end: str) -> bool:
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        begins = datetime.fromisoformat(start.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if at.tzinfo is None or begins.tzinfo is None or expires.tzinfo is None:
        return False
    return begins <= at.astimezone(timezone.utc) < expires

def evaluate(drone_id: Any, lat: Any, lon: Any, observed_at: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = validate_config(config or DEFAULT_CONFIG)
    sectors = sectors_for(config)
    payload = simulation_payload(config)
    if not isinstance(drone_id, str) or not drone_id.strip() or len(drone_id) > 80:
        drone_id = ""
    registry = next((item for item in payload["registry"] if item["drone_id"] == drone_id.strip()), None)
    current_sector = None
    if lat is None or lon is None:
        status, message = "gps_unavailable", "GPS position is missing; sector permission cannot be checked."
    else:
        if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)):
            raise ValueError("Latitude and longitude must be numeric or null.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Latitude or longitude is outside valid GPS bounds.")
        current_sector = _sector_for(float(lat), float(lon), sectors, config)
        if current_sector is None:
            status, message = "outside_monitored_area", "Position is outside the configured sector area; needs review."
        elif registry is None:
            status, message = "unknown_id", "No registry match; needs operator review."
        elif not isinstance(observed_at, str) or not _valid_at(observed_at, registry["valid_from"], registry["valid_until"]):
            status, message = "permission_expired", "Registry record exists, but permission is not valid at this timestamp."
        elif current_sector == registry["home_sector"]:
            status, message = "authorized_home_sector", "Registered unit is in its home sector during its permission window."
        else:
            status, message = "registered_other_sector", "Registered unit is in another sector; verify cross-sector permission."
    return {"drone_id":drone_id.strip() or None,"status":status,"message":message,"home_sector":registry["home_sector"] if registry else None,"current_sector":current_sector,"registry_match":registry is not None,"registry_label":registry["label"] if registry else None,"authorization_is_hostility_judgment":False,"simulation_only":True}

def load_config(project_root: str | Path, user_id: int) -> dict[str, Any]:
    """Load the authenticated user's sector bounds; default to a demo grid."""
    database = Path(project_root) / "results" / "authorization" / "settings.sqlite3"
    if not database.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with sqlite3.connect(database, timeout=10) as connection:
            row = connection.execute("SELECT config_json FROM sector_config WHERE user_id = ?", (user_id,)).fetchone()
        return validate_config(json.loads(row[0])) if row else dict(DEFAULT_CONFIG)
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError):
        return dict(DEFAULT_CONFIG)

def save_config(project_root: str | Path, user_id: int, value: Any) -> dict[str, Any]:
    """Persist a validated grid configuration for one authenticated account."""
    config = validate_config(value)
    database = Path(project_root) / "results" / "authorization" / "settings.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database, timeout=10) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS sector_config (user_id INTEGER PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO sector_config (user_id, config_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(user_id) DO UPDATE SET config_json=excluded.config_json, updated_at=CURRENT_TIMESTAMP", (user_id, json.dumps(config, allow_nan=False, separators=(",", ":"))))
    return config