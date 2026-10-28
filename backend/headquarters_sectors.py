"""Headquarters-owned sector configuration and relational sector geometry."""
from __future__ import annotations

import json
import sqlite3
import math
from pathlib import Path
from typing import Any

from backend.authorization import DEFAULT_CONFIG, sectors_for, validate_config


def headquarters_database(project_root: str | Path) -> Path:
    return Path(project_root) / "results" / "authorization" / "headquarters.sqlite3"


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=15, factory=_ClosingConnection)
    connection.execute("PRAGMA busy_timeout=15000")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS headquarters_sector_config (
            config_id INTEGER PRIMARY KEY CHECK (config_id = 1),
            config_json TEXT NOT NULL,
            updated_by INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS headquarters_sectors (
            sector_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            west REAL NOT NULL,
            east REAL NOT NULL,
            south REAL NOT NULL,
            north REAL NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            decommissioned_at TEXT,
            decommission_reason TEXT,
            CHECK (west < east), CHECK (south < north)
        )"""
    )
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(headquarters_sectors)")}
    if "geometry_json" not in columns:
        connection.execute("ALTER TABLE headquarters_sectors ADD COLUMN geometry_json TEXT")
    migrations = {
        "is_active": "ALTER TABLE headquarters_sectors ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))",
        "decommissioned_at": "ALTER TABLE headquarters_sectors ADD COLUMN decommissioned_at TEXT",
        "decommission_reason": "ALTER TABLE headquarters_sectors ADD COLUMN decommission_reason TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS authorized_drones (
            registry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            device_id_type TEXT NOT NULL CHECK (device_id_type IN ('flight_controller_serial', 'gps_tracker_uuid')),
            sector_id TEXT NOT NULL REFERENCES headquarters_sectors(sector_id) ON UPDATE CASCADE ON DELETE RESTRICT,
            valid_from TEXT NOT NULL,
            valid_until TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved', 'revoked')),
            label TEXT NOT NULL DEFAULT '',
            approved_by INTEGER NOT NULL,
            approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (device_id_type, device_id),
            CHECK (length(device_id) BETWEEN 1 AND 128),
            CHECK (valid_from < valid_until)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS authorized_drones_sector_validity_idx "
        "ON authorized_drones (sector_id, status, valid_from, valid_until)"
    )


def _sector_rows(sectors: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            sector["id"], sector["name"], sector["row"], sector["column"],
            sector["bounds"]["west"], sector["bounds"]["east"],
            sector["bounds"]["south"], sector["bounds"]["north"],
        )
        for sector in sectors
    ]


def _sync_sectors(connection: sqlite3.Connection, sectors: list[dict[str, Any]]) -> None:
    desired = {row[0]: row for row in _sector_rows(sectors)}
    current_rows = connection.execute(
        "SELECT sector_id, name, row_index, column_index, west, east, south, north "
        "FROM headquarters_sectors"
    ).fetchall()
    current = {str(row[0]): tuple(row) for row in current_rows}
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def referenced(sector_id: str) -> bool:
        if connection.execute("SELECT 1 FROM authorized_drones WHERE sector_id=? LIMIT 1", (sector_id,)).fetchone():
            return True
        if "sector_registry_requests" in tables and connection.execute(
            "SELECT 1 FROM sector_registry_requests WHERE sector_id=? LIMIT 1", (sector_id,)
        ).fetchone():
            return True
        return False

    for sector_id, old in current.items():
        new = desired.get(sector_id)
        # Name or boundary changes affect the meaning of existing authorization records.
        meaning_changed = new is None or old[1] != new[1] or old[4:8] != new[4:8]
        if meaning_changed and referenced(sector_id):
            action = "delete" if new is None else "change"
            raise ValueError(
                f"Sector {sector_id} cannot {action} because it has registry history or authorized drones."
            )

    for sector_id in current.keys() - desired.keys():
        connection.execute("DELETE FROM headquarters_sectors WHERE sector_id=?", (sector_id,))

    for sector_id, row in desired.items():
        old = current.get(sector_id)
        geometry = None
        if old and old[1] == row[1] and old[4:8] == row[4:8]:
            geometry_row = connection.execute(
                "SELECT geometry_json FROM headquarters_sectors WHERE sector_id=?", (sector_id,)
            ).fetchone()
            geometry = geometry_row[0] if geometry_row else None
        connection.execute(
            """INSERT INTO headquarters_sectors
                (sector_id, name, row_index, column_index, west, east, south, north, geometry_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sector_id) DO UPDATE SET
                    name=excluded.name, row_index=excluded.row_index,
                    column_index=excluded.column_index, west=excluded.west,
                    east=excluded.east, south=excluded.south, north=excluded.north,
                    geometry_json=excluded.geometry_json,
                    updated_at=CURRENT_TIMESTAMP""",
            (*row, geometry),
        )

def _load_config_in_connection(connection: sqlite3.Connection) -> tuple[dict[str, Any], str | None]:
    row = connection.execute(
        "SELECT config_json, updated_at FROM headquarters_sector_config WHERE config_id = 1"
    ).fetchone()
    if row:
        return validate_config(json.loads(row["config_json"])), row["updated_at"]
    return dict(DEFAULT_CONFIG), None


def load_headquarters_sectors(project_root: str | Path) -> dict[str, Any]:
    """Return centrally managed grid geometry and initialize its sector rows."""
    database = headquarters_database(project_root)
    database.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database) as connection:
        _initialize(connection)
        config, updated_at = _load_config_in_connection(connection)
        sectors = sectors_for(config)
        _sync_sectors(connection, sectors)
        persisted = {row["sector_id"]: row for row in connection.execute(
            "SELECT sector_id, geometry_json, is_active, decommissioned_at, decommission_reason FROM headquarters_sectors"
        ).fetchall()}
        for sector in sectors:
            saved = persisted.get(sector["id"])
            raw = saved["geometry_json"] if saved else None
            sector["is_active"] = bool(saved["is_active"]) if saved else True
            sector["decommissioned_at"] = saved["decommissioned_at"] if saved else None
            sector["decommission_reason"] = saved["decommission_reason"] if saved else None
            if raw:
                try:
                    sector["geometry"] = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    sector["geometry"] = None
    return {
        "config": config,
        "sectors": sectors,
        "updated_at": updated_at,
        "source": "headquarters_configuration" if updated_at else "default_demo_configuration",
        "simulation_only": True,
    }


def save_headquarters_sectors(
    project_root: str | Path, value: Any, updated_by: int
) -> dict[str, Any]:
    """Persist HQ grid and sector IDs atomically; protect referenced geometry."""
    config = validate_config(value)
    sectors = sectors_for(config)
    database = headquarters_database(project_root)
    database.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database) as connection:
        _initialize(connection)
        _sync_sectors(connection, sectors)
        connection.execute(
            """INSERT INTO headquarters_sector_config
                (config_id, config_json, updated_by, updated_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(config_id) DO UPDATE SET
                    config_json=excluded.config_json,
                    updated_by=excluded.updated_by,
                    updated_at=CURRENT_TIMESTAMP""",
            (json.dumps(config, allow_nan=False, separators=(",", ":")), updated_by),
        )
        row = connection.execute(
            "SELECT updated_at FROM headquarters_sector_config WHERE config_id = 1"
        ).fetchone()
        persisted = {item["sector_id"]: item for item in connection.execute(
            "SELECT sector_id, geometry_json, is_active, decommissioned_at, decommission_reason FROM headquarters_sectors"
        ).fetchall()}
        for sector in sectors:
            saved = persisted.get(sector["id"])
            raw = saved["geometry_json"] if saved else None
            sector["is_active"] = bool(saved["is_active"]) if saved else True
            sector["decommissioned_at"] = saved["decommissioned_at"] if saved else None
            sector["decommission_reason"] = saved["decommission_reason"] if saved else None
            if raw:
                try:
                    sector["geometry"] = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    sector["geometry"] = None
    return {
        "config": config,
        "sectors": sectors,
        "updated_at": row[0] if row else None,
        "source": "headquarters_configuration",
        "simulation_only": True,
    }


def normalize_polygon(value: Any) -> dict[str, Any]:
    """Validate a Leaflet polygon and calculate a local centroid and spherical area."""
    if not isinstance(value, list) or not 3 <= len(value) <= 1000:
        raise ValueError("Draw a sector boundary with at least three points.")
    points: list[tuple[float, float]] = []
    for point in value:
        if isinstance(point, dict):
            lat, lon = point.get("lat"), point.get("lng", point.get("lon"))
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            lat, lon = point[0], point[1]
        else:
            raise ValueError("Sector boundary contains an invalid point.")
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            raise ValueError("Sector boundary coordinates must be numbers.") from None
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Sector boundary coordinates are outside valid latitude/longitude ranges.")
        points.append((lon, lat))
    if points[0] != points[-1]:
        points.append(points[0])
    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    edge_count = len(points) - 1
    for i in range(edge_count):
        for j in range(i + 1, edge_count):
            if j == i + 1 or (i == 0 and j == edge_count - 1):
                continue
            a, b, c, d = points[i], points[i + 1], points[j], points[j + 1]
            o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
            if o1 * o2 <= 0 and o3 * o4 <= 0:
                raise ValueError("Sector boundary edges must not cross or overlap.")
    lat0 = math.radians(sum(lat for _, lat in points[:-1]) / (len(points) - 1))
    projected = [(math.radians(lon) * math.cos(lat0), math.radians(lat)) for lon, lat in points]
    twice_area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(projected, projected[1:]):
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(twice_area) < 1e-12:
        raise ValueError("Sector boundary must enclose an area.")
    centroid = [math.degrees(cx / (3 * twice_area * math.cos(lat0))), math.degrees(cy / (3 * twice_area))]
    area = sum(math.radians(lon2 - lon1) * (2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
               for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]))
    return {"type": "Polygon", "coordinates": [[list(point) for point in points]], "centroid": centroid,
            "area_km2": round(abs(area) * 6371.0088 ** 2 / 2, 6)}


def set_sector_geometry(connection: sqlite3.Connection, sector_id: str, geometry: dict[str, Any]) -> None:
    """Persist freshly HQ-approved geometry and reactivate the configured area."""
    connection.execute("""UPDATE headquarters_sectors
        SET geometry_json=?, is_active=1, decommissioned_at=NULL, decommission_reason=NULL,
            updated_at=CURRENT_TIMESTAMP WHERE sector_id=?""",
        (json.dumps(geometry, allow_nan=False, separators=(",", ":")), sector_id))


def is_sector_active(project_root: str | Path, sector_id: str) -> bool:
    """Read the persisted operational state for an HQ-defined sector."""
    database = headquarters_database(project_root)
    if not database.exists():
        return False
    with _connect(database) as connection:
        _initialize(connection)
        row = connection.execute("SELECT is_active FROM headquarters_sectors WHERE sector_id=?", (sector_id.upper(),)).fetchone()
    return bool(row and row["is_active"])


def list_sector_geometries(project_root: str | Path, active_ids: set[str]) -> list[dict[str, Any]]:
    """Return active approved sectors, falling back to legacy configured rectangles."""
    payload = load_headquarters_sectors(project_root)
    result = []
    for sector in payload["sectors"]:
        if sector["id"] not in active_ids or not sector.get("is_active", True):
            continue
        geometry = sector.get("geometry")
        if not geometry:
            b = sector["bounds"]
            geometry = {"type": "Polygon", "coordinates": [[[b["west"], b["south"]], [b["east"], b["south"]],
                         [b["east"], b["north"]], [b["west"], b["north"]], [b["west"], b["south"]]]],
                         "centroid": [(b["west"] + b["east"]) / 2, (b["south"] + b["north"]) / 2], "area_km2": None}
        result.append({"id": sector["id"], "name": sector["name"], "bounds": sector["bounds"], "geometry": geometry})
    return result
