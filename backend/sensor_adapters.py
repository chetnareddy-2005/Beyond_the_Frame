"""Normalize declared sensor/replay observations; no hardware connection is implied."""
from __future__ import annotations
import csv
import io
import json
import math
from typing import Any

SOURCE_TYPES = {"gps_tracker", "radar", "electro_optical", "acoustic", "json_replay", "csv_replay", "mock"}
MAX_OBSERVATIONS = 120
MAX_METADATA_BYTES = 2048


def normalize_observation(item: Any, *, default_source_type: str = "gps_tracker", default_source_id: str = "manual") -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Each observation must be a JSON object.")
    source_type = item.get("source_type", default_source_type)
    source_id = item.get("source_id", default_source_id)
    if not isinstance(source_type, str) or source_type not in SOURCE_TYPES:
        raise ValueError("source_type must be a supported GPS, radar, electro-optical, acoustic, replay, or mock value.")
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id.strip()) > 128:
        raise ValueError("source_id must contain 1 to 128 characters.")
    timestamp = item.get("observed_at", item.get("timestamp"))
    latitude = item.get("latitude")
    longitude = item.get("longitude")
    altitude = item.get("altitude_m", item.get("altitude"))
    coords = item.get("coordinates")
    if isinstance(coords, dict):
        latitude = coords.get("latitude", latitude)
        longitude = coords.get("longitude", longitude)
        altitude = coords.get("altitude_m", altitude)
    elif isinstance(coords, (list, tuple)) and len(coords) >= 2:
        longitude, latitude = coords[0], coords[1]
        if len(coords) > 2:
            altitude = coords[2]
    result = {"source_type": source_type, "source_id": source_id.strip(), "timestamp": timestamp,
              "latitude": latitude, "longitude": longitude, "altitude_m": altitude,
              "sensor_class": item.get("sensor_class"), "sensor_confidence": item.get("sensor_confidence"),
              "metadata": item.get("metadata", {})}
    metadata = result["metadata"]
    if not isinstance(metadata, dict) or len(json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_METADATA_BYTES:
        raise ValueError("metadata must be a JSON object no larger than 2 KiB.")
    if result["sensor_class"] is not None and (not isinstance(result["sensor_class"], str) or len(result["sensor_class"]) > 40):
        raise ValueError("sensor_class must be a string of at most 40 characters.")
    confidence = result["sensor_confidence"]
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("sensor_confidence must be a finite number from 0 to 1.")
    return result


def normalize_json_window(observations: Any, *, source_type: str = "gps_tracker", source_id: str = "manual") -> list[dict[str, Any]]:
    if not isinstance(observations, list) or len(observations) > MAX_OBSERVATIONS:
        raise ValueError(f"observations must be an array containing no more than {MAX_OBSERVATIONS} entries.")
    return [normalize_observation(item, default_source_type=source_type, default_source_id=source_id) for item in observations]


def replay_csv(text: str, *, source_id: str = "csv-replay") -> list[dict[str, Any]]:
    """Parse a bounded CSV replay with timestamp,latitude,longitude,altitude_m columns."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 48_000:
        raise ValueError("CSV replay must be text no larger than 48 KiB.")
    reader = csv.DictReader(io.StringIO(text))
    required = {"timestamp", "latitude", "longitude", "altitude_m"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("CSV replay requires timestamp,latitude,longitude,altitude_m columns.")
    rows = []
    for row in reader:
        try:
            raw = {**row, "latitude": float(row["latitude"]), "longitude": float(row["longitude"]), "altitude_m": float(row["altitude_m"]), "source_type": "csv_replay", "source_id": source_id}
        except (TypeError, ValueError) as error:
            raise ValueError("CSV replay contains a non-numeric coordinate or altitude.") from error
        rows.append(normalize_observation(raw))
        if len(rows) > MAX_OBSERVATIONS:
            raise ValueError(f"CSV replay may contain no more than {MAX_OBSERVATIONS} observations.")
    return rows
