"""HQ-approved drone registry, GPS authorization, and trajectory storage."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from typing import Any
from backend.authorization import _sector_for, sectors_for, validate_config
from backend.headquarters_sectors import _connect, _initialize, _load_config_in_connection, _sync_sectors, headquarters_database
from backend.trajectory_classifier import CLASSIFIER_VERSION, MAX_TRAIL_POINTS, classify_points
from backend.sensor_adapters import normalize_json_window, normalize_observation

DEVICE_TYPES = {"flight_controller_serial", "gps_tracker_uuid"}


def _utc_timestamp(value: Any, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with a timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone, such as Z or +00:00.")
    utc = parsed.astimezone(timezone.utc)
    return utc.isoformat(timespec="seconds").replace("+00:00", "Z"), utc


def _normalize_device(device_id: Any, device_id_type: Any) -> tuple[str, str]:
    if not isinstance(device_id_type, str) or device_id_type not in DEVICE_TYPES:
        raise ValueError("Device ID type must be flight_controller_serial or gps_tracker_uuid.")
    if not isinstance(device_id, str):
        raise ValueError("Device ID is required.")
    normalized = device_id.strip()
    if not normalized or len(normalized) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", normalized):
        raise ValueError("Device ID must be 1–128 letters, digits, dots, underscores, colons, or hyphens.")
    if device_id_type == "gps_tracker_uuid":
        import uuid
        try:
            normalized = str(uuid.UUID(normalized.lower()))
        except (ValueError, AttributeError) as error:
            raise ValueError("GPS tracker UUID must be a valid UUID.") from error
    else:
        normalized = normalized.upper()
    return normalized, device_id_type


def register_approved_drone(project_root: str, payload: Any, approved_by: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Registry details must be an object.")
    device_id, device_id_type = _normalize_device(payload.get("device_id"), payload.get("device_id_type"))
    sector_id = payload.get("sector_id")
    if not isinstance(sector_id, str) or not re.fullmatch(r"S[1-9][0-9]*", sector_id):
        raise ValueError("Choose a valid Headquarters sector ID.")
    valid_from, begins = _utc_timestamp(payload.get("valid_from"), "valid_from")
    valid_until, expires = _utc_timestamp(payload.get("valid_until"), "valid_until")
    if begins >= expires:
        raise ValueError("valid_until must be later than valid_from.")
    label = payload.get("label", "")
    if not isinstance(label, str) or len(label.strip()) > 120:
        raise ValueError("Label must be 120 characters or fewer.")
    with _connect(headquarters_database(project_root)) as connection:
        _initialize(connection)
        config, _ = _load_config_in_connection(connection)
        sectors = sectors_for(validate_config(config))
        _sync_sectors(connection, sectors)
        if not any(sector["id"] == sector_id for sector in sectors):
            raise ValueError("The selected sector does not exist in the current Headquarters grid.")
        state = connection.execute("SELECT is_active FROM headquarters_sectors WHERE sector_id=?", (sector_id,)).fetchone()
        if state is None or not state["is_active"]:
            raise ValueError("Authorized drones cannot be assigned to an inactive sector.")
        try:
            cursor = connection.execute("""INSERT INTO authorized_drones
                (device_id,device_id_type,sector_id,valid_from,valid_until,status,label,approved_by,approved_at)
                VALUES(?,?,?,?,?,'approved',?,?,CURRENT_TIMESTAMP)""",
                (device_id,device_id_type,sector_id,valid_from,valid_until,label.strip(),approved_by))
        except sqlite3.IntegrityError as error:
            if "UNIQUE" in str(error).upper():
                raise ValueError("This device ID is already registered.") from error
            raise
        row = connection.execute("""SELECT registry_id,device_id,device_id_type,sector_id,valid_from,
            valid_until,status,label,approved_at FROM authorized_drones WHERE registry_id=?""",(cursor.lastrowid,)).fetchone()
    return dict(row)


def list_authorized_drones(project_root: str) -> list[dict[str, Any]]:
    database = headquarters_database(project_root)
    if not database.exists():
        return []
    with _connect(database) as connection:
        _initialize(connection)
        rows = connection.execute("""SELECT d.registry_id,d.device_id,d.device_id_type,d.sector_id,
            s.name sector_name,d.valid_from,d.valid_until,d.status,d.label,d.approved_at
            FROM authorized_drones d JOIN headquarters_sectors s USING(sector_id)
            ORDER BY d.created_at DESC,d.registry_id DESC""").fetchall()
    return [dict(row) for row in rows]


def _active_sectors_with_saved_geometry(connection: sqlite3.Connection, sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use HQ-approved persisted geometry and omit deactivated sectors from GPS checks."""
    rows = connection.execute("SELECT sector_id, geometry_json, is_active FROM headquarters_sectors").fetchall()
    saved = {str(row["sector_id"]).upper(): row for row in rows}
    active = []
    for sector in sectors:
        row = saved.get(str(sector["id"]).upper())
        if row is None or not row["is_active"]:
            continue
        raw_geometry = row["geometry_json"]
        if raw_geometry:
            try:
                sector["geometry"] = json.loads(raw_geometry)
            except (TypeError, json.JSONDecodeError):
                # Corrupt saved outlines must never widen authorization to rectangle bounds.
                sector["geometry"] = {"type": "invalid"}
        active.append(sector)
    return active
def _legacy_classify(project_root: str, payload: dict[str, Any], device_id: str, device_type: str) -> dict[str, Any]:
    database = headquarters_database(project_root)
    with _connect(database) as connection:
        _initialize(connection)
        config, _ = _load_config_in_connection(connection)
        sectors = sectors_for(validate_config(config))
        _sync_sectors(connection, sectors)
        sectors = _active_sectors_with_saved_geometry(connection, sectors)
        row = connection.execute("""SELECT d.registry_id,d.device_id,d.device_id_type,d.sector_id,d.valid_from,
            d.valid_until,d.status,d.label,s.is_active sector_active FROM authorized_drones d
            JOIN headquarters_sectors s USING(sector_id) WHERE d.device_id_type=? AND d.device_id=?""",
            (device_type,device_id)).fetchone()
    if row is None or row["status"] != "approved":
        return {"state":"unknown","reason":"device_not_registered_or_not_approved","device_id":device_id,
                "home_sector":None,"current_sector":None,"registry_match":False,"hostility_judgment":False,"simulation_only":True}
    observed_at, observed_dt = _utc_timestamp(payload.get("timestamp"),"timestamp")
    lat, lon = payload.get("latitude"), payload.get("longitude")
    if isinstance(lat,bool) or isinstance(lon,bool) or not isinstance(lat,(float,int)) or not isinstance(lon,(float,int)) or not (-90<=lat<=90 and -180<=lon<=180):
        raise ValueError("Latitude and longitude must be valid numeric GPS coordinates.")
    begins=datetime.fromisoformat(row["valid_from"].replace("Z","+00:00"))
    expires=datetime.fromisoformat(row["valid_until"].replace("Z","+00:00"))
    current=_sector_for(float(lat),float(lon),sectors,config)
    if not row["sector_active"]:
        state,reason="expired/out-of-area","assigned_sector_decommissioned"
    elif not begins<=observed_dt<expires:
        state,reason="expired/out-of-area","validity_window_expired_or_not_started"
    elif current is None:
        state,reason="expired/out-of-area","outside_active_sector_geometry"
    elif current!=row["sector_id"]:
        state,reason="other-sector","registered_device_outside_assigned_sector"
    else:
        state,reason="authorized","registered_device_in_assigned_sector_and_validity_window"
    return {"state":state,"reason":reason,"device_id":device_id,"registry_id":row["registry_id"],
            "home_sector":row["sector_id"],"current_sector":current,"timestamp":observed_at,
            "registry_match":True,"hostility_judgment":False,"simulation_only":True}


def _trajectory_schema(connection):
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS trajectory_points(
      registry_id INTEGER NOT NULL REFERENCES authorized_drones(registry_id) ON DELETE RESTRICT,
      track_id TEXT NOT NULL, sector_id TEXT NOT NULL REFERENCES headquarters_sectors(sector_id) ON DELETE RESTRICT,
      observed_at TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL, altitude_m REAL NOT NULL,
      source_type TEXT NOT NULL DEFAULT 'gps_tracker', source_id TEXT NOT NULL DEFAULT 'manual',
      sensor_class TEXT, sensor_confidence REAL, metadata_json TEXT NOT NULL DEFAULT '{}',
      PRIMARY KEY(registry_id,track_id,observed_at));
    CREATE INDEX IF NOT EXISTS trajectory_points_recent_idx ON trajectory_points(registry_id,track_id,observed_at DESC);
    CREATE TABLE IF NOT EXISTS trajectory_latest(
      registry_id INTEGER NOT NULL REFERENCES authorized_drones(registry_id) ON DELETE RESTRICT,
      track_id TEXT NOT NULL, sector_id TEXT NOT NULL REFERENCES headquarters_sectors(sector_id) ON DELETE RESTRICT,
      device_id TEXT NOT NULL, derived_classification TEXT NOT NULL CHECK(derived_classification IN('drone','bird','unknown')),
      reason_code TEXT NOT NULL CHECK(reason_code IN('LINEAR_MOTION_DETECTED','ERRATIC_BIOLOGICAL_FLIGHT','SUSTAINED_HOVER_SEQUENCE','INSUFFICIENT_DATA')),
      metrics_json TEXT NOT NULL, assessed_at TEXT NOT NULL, classifier_version TEXT NOT NULL,
      first_seen_at TEXT, last_seen_at TEXT, authorization_state TEXT NOT NULL DEFAULT 'unknown',
      track_status TEXT NOT NULL DEFAULT 'active', review_status TEXT NOT NULL DEFAULT 'unreviewed',
      review_reason TEXT NOT NULL DEFAULT '', reviewed_by INTEGER, reviewed_at TEXT, explanation TEXT NOT NULL DEFAULT '',
      source_type TEXT NOT NULL DEFAULT 'gps_tracker', source_id TEXT NOT NULL DEFAULT 'manual',
      sensor_class TEXT, sensor_confidence REAL, PRIMARY KEY(registry_id,track_id));
    CREATE TABLE IF NOT EXISTS trajectory_events(
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      registry_id INTEGER NOT NULL REFERENCES authorized_drones(registry_id) ON DELETE RESTRICT,
      track_id TEXT NOT NULL, sector_id TEXT NOT NULL REFERENCES headquarters_sectors(sector_id) ON DELETE RESTRICT,
      device_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, actor_user_id INTEGER,
      previous_value TEXT NOT NULL DEFAULT '', new_value TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
      event_key TEXT UNIQUE);
    CREATE INDEX IF NOT EXISTS trajectory_events_track_idx ON trajectory_events(registry_id,track_id,event_id DESC);
    """)
    additions = {
      "trajectory_points": {"source_type":"TEXT NOT NULL DEFAULT 'gps_tracker'", "source_id":"TEXT NOT NULL DEFAULT 'manual'", "sensor_class":"TEXT", "sensor_confidence":"REAL", "metadata_json":"TEXT NOT NULL DEFAULT '{}'"},
      "trajectory_latest": {"first_seen_at":"TEXT", "last_seen_at":"TEXT", "authorization_state":"TEXT NOT NULL DEFAULT 'unknown'", "track_status":"TEXT NOT NULL DEFAULT 'active'", "review_status":"TEXT NOT NULL DEFAULT 'unreviewed'", "review_reason":"TEXT NOT NULL DEFAULT ''", "reviewed_by":"INTEGER", "reviewed_at":"TEXT", "explanation":"TEXT NOT NULL DEFAULT ''", "source_type":"TEXT NOT NULL DEFAULT 'gps_tracker'", "source_id":"TEXT NOT NULL DEFAULT 'manual'", "sensor_class":"TEXT", "sensor_confidence":"REAL"},
      "trajectory_events": {"device_id":"TEXT NOT NULL DEFAULT ''"},
    }
    for table, columns in additions.items():
        existing = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def classify_telemetry(project_root: str, payload: Any, sector_scope: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Telemetry must be an object.")
    device_id, device_type = _normalize_device(payload.get("device_id"),payload.get("device_id_type"))
    if "points" not in payload and "observations" not in payload:
        if sector_scope is not None:
            with _connect(headquarters_database(project_root)) as connection:
                row=connection.execute("SELECT sector_id FROM authorized_drones WHERE device_id_type=? AND device_id=?",(device_type,device_id)).fetchone()
                if row and str(row["sector_id"]).upper()!=sector_scope.strip().upper():
                    raise PermissionError("This account can submit telemetry only for its assigned sector.")
        return _legacy_classify(project_root,payload,device_id,device_type)

    source_type=payload.get("source_type", "gps_tracker")
    source_id=payload.get("source_id", "manual")
    raw_window=payload.get("observations", payload.get("points"))
    observations=normalize_json_window(raw_window,source_type=source_type,source_id=source_id)
    classification, points = classify_points(observations)
    response={"device_id":device_id,**classification}
    if len(points)<3:
        return response
    track=payload.get("track_id","default")
    if not isinstance(track,str) or not track.strip() or len(track.strip())>80 or any(ord(c)<32 for c in track):
        raise ValueError("track_id must contain 1 to 80 valid characters.")
    database=headquarters_database(project_root)
    with _connect(database) as connection:
        _initialize(connection)
        _trajectory_schema(connection)
        config,_=_load_config_in_connection(connection)
        sectors=sectors_for(validate_config(config))
        _sync_sectors(connection,sectors)
        sectors=_active_sectors_with_saved_geometry(connection,sectors)
        row=connection.execute("""SELECT d.registry_id,d.sector_id,d.valid_from,d.valid_until,d.status,
            s.is_active sector_active FROM authorized_drones d JOIN headquarters_sectors s USING(sector_id)
            WHERE d.device_id_type=? AND d.device_id=?""",(device_type,device_id)).fetchone()
        if row is None or row["status"]!="approved" or not row["sector_active"]:
            return {**response,"derived_classification":"unknown","reason_code":"INSUFFICIENT_DATA",
                    "explanation":"The device is not currently approved in an active sector."}
        if sector_scope is not None and str(row["sector_id"]).upper()!=sector_scope.strip().upper():
            raise PermissionError("This account can submit telemetry only for its assigned sector.")
        begin=datetime.fromisoformat(str(row["valid_from"]).replace("Z","+00:00"))
        end=datetime.fromisoformat(str(row["valid_until"]).replace("Z","+00:00"))
        outside_validity=any(not begin<=datetime.fromtimestamp(point["_time"],timezone.utc)<end for point in points)
        last_point=points[-1]
        active_sectors=sectors
        current=_sector_for(last_point["latitude"],last_point["longitude"],active_sectors,config)
        if outside_validity or current is None:
            authorization_state="expired/out-of-area"
        elif current!=row["sector_id"]:
            authorization_state="other-sector"
        else:
            authorization_state="authorized"
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            old=connection.execute("SELECT derived_classification,authorization_state,first_seen_at,last_seen_at,review_status FROM trajectory_latest WHERE registry_id=? AND track_id=?",(row["registry_id"],track.strip())).fetchone()
            for obs,point in zip(observations,points):
                stamp=datetime.fromtimestamp(point["_time"],timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
                connection.execute("""INSERT INTO trajectory_points
                    (registry_id,track_id,sector_id,observed_at,latitude,longitude,altitude_m,source_type,source_id,sensor_class,sensor_confidence,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(registry_id,track_id,observed_at) DO NOTHING""",
                    (row["registry_id"],track.strip(),row["sector_id"],stamp,point["latitude"],point["longitude"],point["altitude_m"],obs["source_type"],obs["source_id"],obs["sensor_class"],obs["sensor_confidence"],json.dumps(obs["metadata"],separators=(",",":"),allow_nan=False)))
                connection.execute("""INSERT OR IGNORE INTO trajectory_events
                    (registry_id,track_id,sector_id,device_id,event_type,occurred_at,previous_value,new_value,reason,event_key)
                    VALUES(?,?,?,?,'observation_ingested',?,'','accepted','Observation accepted from declared source.',?)""",
                    (row["registry_id"],track.strip(),row["sector_id"],device_id,stamp,f"obs:{row['registry_id']}:{track.strip()}:{stamp}"))
            first_seen=min(datetime.fromtimestamp(p["_time"],timezone.utc) for p in points).isoformat(timespec="microseconds").replace("+00:00","Z")
            last_seen=datetime.fromtimestamp(points[-1]["_time"],timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
            assessed=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
            stale_window=bool(old and old["last_seen_at"] and last_seen<=old["last_seen_at"])
            if stale_window:
                connection.execute("UPDATE trajectory_latest SET first_seen_at=MIN(COALESCE(first_seen_at,?),?) WHERE registry_id=? AND track_id=?",(first_seen,first_seen,row["registry_id"],track.strip()))
            if not stale_window:
                connection.execute("""INSERT INTO trajectory_latest
                  (registry_id,track_id,sector_id,device_id,derived_classification,reason_code,metrics_json,assessed_at,classifier_version,first_seen_at,last_seen_at,authorization_state,track_status,review_status,review_reason,reviewed_by,reviewed_at,explanation,source_type,source_id,sensor_class,sensor_confidence)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?) ON CONFLICT(registry_id,track_id) DO UPDATE SET
                  sector_id=excluded.sector_id,device_id=excluded.device_id,derived_classification=excluded.derived_classification,
                  reason_code=excluded.reason_code,metrics_json=excluded.metrics_json,assessed_at=excluded.assessed_at,
                  classifier_version=excluded.classifier_version,first_seen_at=MIN(COALESCE(trajectory_latest.first_seen_at,excluded.first_seen_at),excluded.first_seen_at),
                  last_seen_at=MAX(COALESCE(trajectory_latest.last_seen_at,excluded.last_seen_at),excluded.last_seen_at),
                  authorization_state=excluded.authorization_state,track_status='active',explanation=excluded.explanation,
                  source_type=excluded.source_type,source_id=excluded.source_id,sensor_class=excluded.sensor_class,sensor_confidence=excluded.sensor_confidence""",
                  (row["registry_id"],track.strip(),row["sector_id"],device_id,classification["derived_classification"],classification["reason_code"],json.dumps(classification["metrics"],separators=(",",":"),allow_nan=False),assessed,CLASSIFIER_VERSION,first_seen,last_seen,authorization_state,"unreviewed" if old is None else old["review_status"],"",None,None,classification["explanation"],observations[-1]["source_type"],observations[-1]["source_id"],observations[-1]["sensor_class"],observations[-1]["sensor_confidence"]))
                if old is None:
                    connection.execute("INSERT INTO trajectory_events(registry_id,track_id,sector_id,device_id,event_type,occurred_at,new_value,reason) VALUES(?,?,?,?,'track_opened',?,'active','First accepted trajectory window.')",(row["registry_id"],track.strip(),row["sector_id"],device_id,assessed))
                if old is None or old["derived_classification"]!=classification["derived_classification"]:
                    connection.execute("INSERT INTO trajectory_events(registry_id,track_id,sector_id,device_id,event_type,occurred_at,previous_value,new_value,reason) VALUES(?,?,?,?,'classification_updated',?,?,?,?)",(row["registry_id"],track.strip(),row["sector_id"],device_id,assessed,"unknown" if old is None else old["derived_classification"],classification["derived_classification"],classification["explanation"]))
                if old is None or old["authorization_state"]!=authorization_state:
                    connection.execute("INSERT INTO trajectory_events(registry_id,track_id,sector_id,device_id,event_type,occurred_at,previous_value,new_value,reason) VALUES(?,?,?,?,'authorization_updated',?,?,?,?)",(row["registry_id"],track.strip(),row["sector_id"],device_id,assessed,"unknown" if old is None else old["authorization_state"],authorization_state,"Last GPS coordinate evaluated against active Headquarters sector boundaries and device validity window."))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {**response,"authorization_state":authorization_state,"track_id":track.strip(),"classifier_version":CLASSIFIER_VERSION,
            "source_type":observations[-1]["source_type"],"source_id":observations[-1]["source_id"],"stale_window":stale_window}


def list_tracks(project_root: str, sector_scope: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
    database=headquarters_database(project_root)
    if not database.exists(): return []
    with _connect(database) as connection:
        _initialize(connection); _trajectory_schema(connection)
        sql="""SELECT t.registry_id,t.track_id,t.device_id,t.sector_id,t.derived_classification,t.reason_code,
          t.metrics_json,t.assessed_at,t.classifier_version,t.first_seen_at,t.last_seen_at,t.authorization_state,
          t.track_status,t.review_status,t.review_reason,t.reviewed_by,t.reviewed_at,t.explanation,t.source_type,t.source_id,
          t.sensor_class,t.sensor_confidence,s.name sector_name FROM trajectory_latest t
          JOIN authorized_drones d USING(registry_id) JOIN headquarters_sectors s USING(sector_id)
          WHERE 1=1"""
        args=[]
        if active_only or sector_scope is not None: sql+=" AND d.status='approved' AND s.is_active=1"
        if sector_scope is not None: sql+=" AND t.sector_id=?"; args.append(sector_scope.strip().upper())
        rows=connection.execute(sql+" ORDER BY t.last_seen_at DESC LIMIT 500",args).fetchall()
    return [{**dict(row),"metrics":json.loads(row["metrics_json"])} for row in rows]


def get_track_detail(project_root: str, registry_id: Any, track_id: Any, sector_scope: str | None = None) -> dict[str, Any] | None:
    if isinstance(registry_id,bool) or not isinstance(registry_id,int) or registry_id<1 or not isinstance(track_id,str) or not track_id or len(track_id)>80:
        raise ValueError("Invalid track reference.")
    database=headquarters_database(project_root)
    if not database.exists(): return None
    with _connect(database) as connection:
        _initialize(connection); _trajectory_schema(connection)
        sql="""SELECT t.*,s.name sector_name FROM trajectory_latest t JOIN authorized_drones d USING(registry_id)
          JOIN headquarters_sectors s USING(sector_id) WHERE t.registry_id=? AND t.track_id=?"""
        args=[registry_id,track_id]
        if sector_scope is not None:
            sql+=" AND t.sector_id=? AND d.status='approved' AND s.is_active=1"; args.append(sector_scope.strip().upper())
        row=connection.execute(sql,args).fetchone()
        if row is None: return None
        item=dict(row); item["metrics"]=json.loads(item.pop("metrics_json"))
        item["points"]=[{**dict(point),"metadata":json.loads(point["metadata_json"])} for point in connection.execute("SELECT observed_at,latitude,longitude,altitude_m,source_type,source_id,sensor_class,sensor_confidence,metadata_json FROM trajectory_points WHERE registry_id=? AND track_id=? ORDER BY observed_at",(registry_id,track_id)).fetchall()]
        item["events"]=[dict(event) for event in connection.execute("SELECT event_id,device_id,event_type,occurred_at,actor_user_id,previous_value,new_value,reason FROM trajectory_events WHERE registry_id=? AND track_id=? ORDER BY occurred_at,event_id",(registry_id,track_id)).fetchall()]
        return item


def review_track(project_root: str, registry_id: Any, track_id: Any, actor_user_id: int, decision: Any, reason: Any) -> dict[str, Any]:
    allowed={"reviewed","cleared","simulated_neutralized"}
    if decision not in allowed: raise ValueError("Decision must be reviewed, cleared, or simulated_neutralized.")
    if not isinstance(reason,str) or not 5<=len(reason.strip())<=500: raise ValueError("Review reason must contain 5 to 500 characters.")
    detail=get_track_detail(project_root,registry_id,track_id)
    if detail is None: raise LookupError("Track was not found.")
    now=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
    with _connect(headquarters_database(project_root)) as connection:
        _initialize(connection); _trajectory_schema(connection); connection.execute("BEGIN IMMEDIATE")
        current=connection.execute("SELECT review_status FROM trajectory_latest WHERE registry_id=? AND track_id=?",(registry_id,track_id)).fetchone()
        if current is None: raise LookupError("Track was not found.")
        previous=str(current["review_status"])
        connection.execute("UPDATE trajectory_latest SET review_status=?,review_reason=?,reviewed_by=?,reviewed_at=? WHERE registry_id=? AND track_id=?",(decision,reason.strip(),actor_user_id,now,registry_id,track_id))
        if previous!=decision or detail.get("review_reason")!=reason.strip():
            connection.execute("INSERT INTO trajectory_events(registry_id,track_id,sector_id,device_id,event_type,occurred_at,actor_user_id,previous_value,new_value,reason) VALUES(?,?,?,?,'hq_review',?,?,?,?,?)",(registry_id,track_id,detail["sector_id"],detail["device_id"],now,actor_user_id,previous,decision,reason.strip()))
        connection.commit()
    return get_track_detail(project_root,registry_id,track_id)

def latest_trajectory_assessments(project_root: str, sector_scope: str | None = None) -> list[dict[str, Any]]:
    tracks=list_tracks(project_root,sector_scope,active_only=True)
    result=[]
    for track in tracks[:50]:
        detail=get_track_detail(project_root,int(track["registry_id"]),str(track["track_id"]),sector_scope)
        if detail is not None: result.append(detail)
    return result


def revoke_authorized_drone(project_root: str, registry_id: Any) -> bool:
    if isinstance(registry_id,bool) or not isinstance(registry_id,int) or registry_id<=0:
        raise ValueError("Registry ID must be a positive integer.")
    database=headquarters_database(project_root)
    database.parent.mkdir(parents=True,exist_ok=True)
    with _connect(database) as connection:
        _initialize(connection)
        cursor=connection.execute("UPDATE authorized_drones SET status='revoked',updated_at=CURRENT_TIMESTAMP WHERE registry_id=? AND status='approved'",(registry_id,))
    return cursor.rowcount>0

