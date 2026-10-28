"""Deterministic kinematic motion-pattern classifier; not an identity or hostility detector."""
from __future__ import annotations
import math
from datetime import datetime
from typing import Any

MAX_TRAIL_POINTS = 120
CLASSIFIER_VERSION = "kinematic-rules-v1"
EARTH_LAT_M, EARTH_LON_M = 111132.0, 111320.0
HOVER_SECONDS, HOVER_RADIUS_M, HOVER_ALT_RANGE_M = 3.0, 3.0, 2.0
DRONE_MIN_SPEED, DRONE_MAX_CV, DRONE_MAX_HEADING_VAR = 2.0, 0.20, 0.08
DRONE_TURN_RAD, DRONE_TURN_RATIO, DRONE_TURN_JERK = math.radians(60), 0.80, 6.5
BIRD_MIN_HEADING_VAR, BIRD_MIN_CV, BIRD_MIN_VZ, BIRD_MAX_JERK = 0.25, 0.15, 0.5, 12.0


def _num(value: Any, field: str, i: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Point {i + 1} {field} must be a finite number.")
    return float(value)


def _parse(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("points must be an array of timestamped GPS coordinates.")
    if len(raw) > MAX_TRAIL_POINTS:
        raise ValueError(f"A trajectory window may contain at most {MAX_TRAIL_POINTS} points.")
    result, previous = [], None
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Point {i + 1} must be an object.")
        lat, lon = _num(item.get("latitude"), "latitude", i), _num(item.get("longitude"), "longitude", i)
        alt = _num(item.get("altitude_m", item.get("altitude")), "altitude_m", i)
        value = item.get("timestamp")
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f"Point {i + 1} timestamp must be ISO 8601 with timezone.")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"Point {i + 1} timestamp must be ISO 8601 with timezone.") from error
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError(f"Point {i + 1} timestamp must include a timezone.")
        seconds = stamp.timestamp()
        if previous is not None and seconds <= previous:
            raise ValueError("points must be strictly chronological with no duplicate timestamps.")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"Point {i + 1} coordinates are outside valid GPS bounds.")
        previous = seconds
        result.append({"latitude": lat, "longitude": lon, "altitude_m": alt, "timestamp": value, "_time": seconds})
    return result


def _delta(a: float, b: float) -> float:
    return (a - b + math.pi) % (2 * math.pi) - math.pi


def _distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat = math.radians((a["latitude"] + b["latitude"]) / 2)
    dx = (b["longitude"] - a["longitude"]) * math.cos(lat) * EARTH_LON_M
    dy = (b["latitude"] - a["latitude"]) * EARTH_LAT_M
    dz = b["altitude_m"] - a["altitude_m"]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def classify_points(raw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    points = _parse(raw)
    empty = {"average_velocity": 0.0, "heading_variance": 0.0, "max_jerk": 0.0, "is_hovering": False}
    if len(points) < 3:
        return {"derived_classification": "unknown", "metrics": empty, "reason_code": "INSUFFICIENT_DATA",
                "explanation": "At least three chronological GPS observations are required for a motion-pattern estimate."}, points
    vectors, speeds, headings, vertical = [], [], [], []
    for a, b in zip(points, points[1:]):
        dt = b["_time"] - a["_time"]
        lat = math.radians((a["latitude"] + b["latitude"]) / 2)
        dx = (b["longitude"] - a["longitude"]) * math.cos(lat) * EARTH_LON_M
        dy = (b["latitude"] - a["latitude"]) * EARTH_LAT_M
        dz = b["altitude_m"] - a["altitude_m"]
        v = (dx / dt, dy / dt, dz / dt)
        vectors.append(v)
        speeds.append(math.sqrt(sum(x*x for x in v)))
        vertical.append(v[2])
        if math.hypot(dx, dy) >= 0.5:
            headings.append((math.atan2(dy, dx), len(speeds) - 1))
    acc = []
    for i in range(1, len(vectors)):
        dt = points[i+1]["_time"] - points[i]["_time"]
        acc.append(tuple((vectors[i][k] - vectors[i-1][k]) / dt for k in range(3)))
    jerks = []
    for i in range(1, len(acc)):
        dt = points[i+2]["_time"] - points[i+1]["_time"]
        j = tuple((acc[i][k] - acc[i-1][k]) / dt for k in range(3))
        jerks.append(math.sqrt(sum(x*x for x in j)))
    mean = sum(speeds) / len(speeds)
    cv = math.sqrt(sum((x-mean)**2 for x in speeds) / len(speeds)) / max(mean, 0.01)
    hvar = 0.0
    if len(headings) >= 2:
        center = math.atan2(sum(math.sin(angle) for angle, _ in headings), sum(math.cos(angle) for angle, _ in headings))
        hvar = sum(_delta(angle, center)**2 for angle, _ in headings) / (len(headings)-1)
    max_jerk = max(jerks, default=0.0)
    hovering = False
    for start, origin in enumerate(points[:-1]):
        for end in range(start+1, len(points)):
            window = points[start:end+1]
            if window[-1]["_time"] - origin["_time"] <= HOVER_SECONDS:
                continue
            heights = [p["altitude_m"] for p in window]
            if all(_distance(origin, p) <= HOVER_RADIUS_M for p in window) and max(heights)-min(heights) <= HOVER_ALT_RANGE_M:
                hovering = True
                break
        if hovering:
            break
    metrics = {"average_velocity": round(mean, 3), "heading_variance": round(hvar, 6),
               "max_jerk": round(max_jerk, 3), "is_hovering": hovering}
    label, reason = "unknown", "INSUFFICIENT_DATA"
    if hovering:
        label, reason = "drone", "SUSTAINED_HOVER_SEQUENCE"
    else:
        sharp = False
        for (a, segment_a), (b, segment_b) in zip(headings, headings[1:]):
            ratio = min(speeds[segment_a], speeds[segment_b]) / max(speeds[segment_a], speeds[segment_b], 0.01)
            if abs(_delta(b, a)) >= DRONE_TURN_RAD and ratio >= DRONE_TURN_RATIO and max_jerk >= DRONE_TURN_JERK:
                sharp = True
                break
        if (mean >= DRONE_MIN_SPEED and cv <= DRONE_MAX_CV and hvar <= DRONE_MAX_HEADING_VAR) or sharp:
            label, reason = "drone", "LINEAR_MOTION_DETECTED"
        elif hvar >= BIRD_MIN_HEADING_VAR and cv >= BIRD_MIN_CV and max(abs(v) for v in vertical) >= BIRD_MIN_VZ and max_jerk <= BIRD_MAX_JERK:
            label, reason = "bird", "ERRATIC_BIOLOGICAL_FLIGHT"
    explanations = {
        "SUSTAINED_HOVER_SEQUENCE": "The observations remained within the configured hover radius and altitude band for over three seconds.",
        "LINEAR_MOTION_DETECTED": "Motion matched the configured stable-speed/stable-heading rule or a sharp-turn rule; this is not an identity or intent finding.",
        "ERRATIC_BIOLOGICAL_FLIGHT": "Heading variation, speed variation, vertical movement, and jerk met the configured bird-like heuristic; this is not a species identification.",
        "INSUFFICIENT_DATA": "The available metrics did not meet a configured motion-pattern rule, so the result remains unknown.",
    }
    return {"derived_classification": label, "metrics": metrics, "reason_code": reason,
            "explanation": explanations[reason]}, points
