"""Opt-in, approximate operator app-use locations for the Phase 10 HQ map."""
from __future__ import annotations
import math, sqlite3
from datetime import datetime, timezone
from backend.headquarters_sectors import headquarters_database

def _connect(root):
    path = headquarters_database(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("""CREATE TABLE IF NOT EXISTS operator_locations(
        user_id INTEGER PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('headquarters_admin','sector')),
        sector_number TEXT, latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
        longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180), updated_at TEXT NOT NULL)""")
    return connection

def save_location(root, user_id, role, sector_number, payload):
    if role not in {"headquarters_admin", "sector"}:
        raise ValueError("This account cannot share an operator location.")
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        raise ValueError("Choose whether to share the current device location.")
    with _connect(root) as connection:
        if not payload["enabled"]:
            connection.execute("DELETE FROM operator_locations WHERE user_id=?", (user_id,))
            return {"sharing": False}
        try:
            lat, lon = float(payload.get("latitude")), float(payload.get("longitude"))
        except (TypeError, ValueError) as error:
            raise ValueError("The browser did not provide valid coordinates.") from error
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("The browser did not provide valid coordinates.")
        lat, lon = round(lat, 3), round(lon, 3)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        connection.execute("""INSERT INTO operator_locations(user_id,role,sector_number,latitude,longitude,updated_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,
            sector_number=excluded.sector_number,latitude=excluded.latitude,
            longitude=excluded.longitude,updated_at=excluded.updated_at""",
            (user_id, role, sector_number if role == "sector" else None, lat, lon, stamp))
    return {"sharing": True, "latitude": lat, "longitude": lon, "updated_at": stamp, "precision": "approximately 100 m"}

def list_locations(root):
    with _connect(root) as connection:
        rows = connection.execute("SELECT user_id,role,sector_number,latitude,longitude,updated_at FROM operator_locations ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_location(root, user_id):
    with _connect(root) as connection:
        row = connection.execute("SELECT latitude,longitude,updated_at FROM operator_locations WHERE user_id=?", (user_id,)).fetchone()
        return ({"sharing": True, "latitude": row["latitude"], "longitude": row["longitude"], "updated_at": row["updated_at"]} if row else {"sharing": False})
