def _source_scoped_track_key(source, track_id):
    """Return a source-safe identity while preserving the legacy track_id field."""
    source_name = str(source).strip() if source is not None else "__unknown__"
    return f"{source_name}::{int(track_id)}"


class TrackletMemory:
    """Group tracking observations and retain simple per-track history."""

    def __init__(self):
        self._tracks = {}
        self._legacy_index = {}
        self._latest_frame = None

    def add_observation(self, observation):
        source = observation.get("source", "__unknown__")
        track_id = int(observation["track_id"])
        frame = int(observation["frame"])
        x1, y1, x2, y2 = (
            float(observation[key]) for key in ("x1", "y1", "x2", "y2")
        )
        composite_key = observation.get("source_track_id") or _source_scoped_track_key(source, track_id)
        key = composite_key
        track = self._tracks.setdefault(
            key,
            {
                "track_id": track_id,
                "source": source,
                "source_track_id": key,
                "frames": [],
                "class_ids": [],
                "class_names": [],
                "confidences": [],
                "timestamps_sec": [],
                "bounding_boxes": [],
                "centers": [],
                "widths": [],
                "heights": [],
            },
        )
        self._legacy_index.setdefault(track_id, key)
        width = x2 - x1
        height = y2 - y1
        track["frames"].append(frame)
        track["class_ids"].append(int(observation["class_id"]))
        track["class_names"].append(observation["class_name"])
        track["confidences"].append(float(observation["confidence"]))
        track["timestamps_sec"].append(observation.get("timestamp_sec"))
        track["bounding_boxes"].append((x1, y1, x2, y2))
        track["centers"].append(((x1 + x2) / 2, (y1 + y2) / 2))
        track["widths"].append(width)
        track["heights"].append(height)
        self._latest_frame = frame if self._latest_frame is None else max(self._latest_frame, frame)

    def get_track(self, track_id, source=None):
        if source is not None:
            return self._tracks.get(_source_scoped_track_key(source, track_id))
        legacy_key = self._legacy_index.get(int(track_id))
        if legacy_key is None:
            return None
        candidate = self._tracks.get(legacy_key)
        if candidate is not None:
            return candidate
        return None

    def get_active_tracks(self):
        return [
            track for track in self._tracks.values()
            if track["frames"] and track["frames"][-1] == self._latest_frame
        ]

    def get_all_tracks(self):
        return list(self._tracks.values())
