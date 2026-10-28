"""Phase 3 frame acquisition, validation, and general preparation."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from backend.input_preparation import InputPreparer
from backend.runs import RunError, RunManager


class FrameSource(Protocol):
    def read_next(self) -> "PreparedFrame | None":
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class PreparedFrame:
    frame: np.ndarray
    source_type: str
    input_type: str | None
    run_id: str | None
    live_reference_id: str | None
    frame_index: int | None
    timestamp_sec: float | None
    width: int
    height: int
    channels: int
    dtype: str
    validation_status: str
    preprocessing_status: str

    def metadata(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "input_type": self.input_type,
            "run_id": self.run_id,
            "live_reference_id": self.live_reference_id,
            "frame_index": self.frame_index,
            "timestamp_sec": self.timestamp_sec,
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "dtype": self.dtype,
            "validation_status": self.validation_status,
            "preprocessing_status": self.preprocessing_status,
        }


def _validated_array(frame: Any) -> tuple[np.ndarray, int, int, int]:
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise RunError(422, "invalid_frame", "The acquired frame is empty or is not an image array.")
    if frame.ndim not in (2, 3):
        raise RunError(422, "invalid_frame", "The acquired frame must have two or three dimensions.")
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise RunError(422, "invalid_frame", "The acquired frame dimensions must be positive.")
    channels = 1 if frame.ndim == 2 else frame.shape[2]
    if channels not in (1, 3, 4):
        raise RunError(422, "invalid_frame", "The acquired frame has an unsupported channel count.")
    if not np.issubdtype(frame.dtype, np.number) or np.issubdtype(frame.dtype, np.bool_):
        raise RunError(422, "invalid_frame", "The acquired frame must use a numeric image datatype.")
    if np.issubdtype(frame.dtype, np.floating) and not np.isfinite(frame).all():
        raise RunError(422, "invalid_frame", "The acquired frame contains non-finite values.")
    prepared = np.ascontiguousarray(frame.copy())
    return prepared, int(width), int(height), int(channels)


def _prepared_frame(
    frame: Any,
    *,
    source_type: str,
    input_type: str | None,
    run_id: str | None,
    live_reference_id: str | None,
    frame_index: int | None,
    preprocessing_status: str,
) -> PreparedFrame:
    image, width, height, channels = _validated_array(frame)
    return PreparedFrame(
        frame=image,
        source_type=source_type,
        input_type=input_type,
        run_id=run_id,
        live_reference_id=live_reference_id,
        frame_index=frame_index,
        timestamp_sec=None,
        width=width,
        height=height,
        channels=channels,
        dtype=str(image.dtype),
        validation_status="valid",
        preprocessing_status=preprocessing_status,
    )


def _opencv_capture(source: str):
    import cv2

    return cv2.VideoCapture(source)


class RGBVideoFrameSource:
    """Sequential source for a validated, stored RGB video."""

    def __init__(
        self,
        video_path: Path,
        run_id: str,
        capture_factory: Callable[[str], Any] | None = None,
        frame_count: int | None = None,
    ):
        self.video_path = video_path
        self.run_id = run_id
        self.capture_factory = capture_factory or _opencv_capture
        self.capture: Any = None
        self.frame_index = 0
        self.frame_count: int | None = frame_count
        self.closed = False
        try:
            self.capture = self.capture_factory(str(video_path))
            if not self.capture.isOpened():
                raise RunError(422, "video_unavailable", "The stored video could not be opened.")
            try:
                import cv2

                count = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
                if self.frame_count is None and np.isfinite(count) and count > 0:
                    self.frame_count = int(count)
            except (ImportError, TypeError, ValueError):
                self.frame_count = None
        except Exception as error:
            self.close()
            if isinstance(error, RunError):
                raise
            raise RunError(422, "video_unavailable", "The stored video could not be opened.") from error

    def read_next(self) -> PreparedFrame | None:
        if self.closed:
            return None
        if self.frame_count is not None and self.frame_index >= self.frame_count:
            self.close()
            return None
        try:
            success, frame = self.capture.read()
            if not success or frame is None or getattr(frame, "size", 0) == 0:
                try:
                    import cv2

                    position = float(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
                except (ImportError, TypeError, ValueError):
                    position = -1
                if self.frame_count is not None and position >= self.frame_count:
                    self.close()
                    return None
                raise RunError(422, "frame_read_failed", "A video frame could not be decoded.")
            prepared = _prepared_frame(
                frame,
                source_type="upload",
                input_type="rgb",
                run_id=self.run_id,
                live_reference_id=None,
                frame_index=self.frame_index,
                preprocessing_status="validated_contiguous_copy",
            )
            self.frame_index += 1
            return prepared
        except RunError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise RunError(422, "frame_read_failed", "A video frame could not be decoded.") from error

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.capture is not None:
            self.capture.release()
            self.capture = None


class ThermalVideoFrameSource:
    """Sequential interface over the existing Thermal adapter."""

    def __init__(self, video_path: Path, run_id: str, frame_count: int | None):
        from multimodal.thermal_adapter import ThermalAdapter

        self.video_path = video_path
        self.run_id = run_id
        self.frame_count = frame_count
        self.frame_index = 0
        self.adapter = ThermalAdapter(enabled=True, thermal_source=str(video_path))
        self.closed = False

    def read_next(self) -> PreparedFrame | None:
        if self.closed:
            return None
        if self.frame_count is not None and self.frame_index >= self.frame_count:
            self.close()
            return None
        index = self.frame_index
        try:
            raw_frame = self.adapter.read_frame(str(self.video_path), index)
            if raw_frame is None:
                if self.frame_count is not None and index >= self.frame_count:
                    self.close()
                    return None
                raise RunError(422, "frame_read_failed", "A Thermal video frame could not be decoded.")
            processed = self.adapter.preprocess_frame(raw_frame)
            if processed is None:
                raise RunError(422, "invalid_frame", "The Thermal frame could not be validated or normalized.")
            prepared = _prepared_frame(
                processed,
                source_type="upload",
                input_type="thermal",
                run_id=self.run_id,
                live_reference_id=None,
                frame_index=index,
                preprocessing_status="normalized_intensity_grayscale_uint8_not_temperature_calibrated",
            )
            self.frame_index += 1
            return prepared
        except RunError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise RunError(422, "frame_read_failed", "A Thermal video frame could not be decoded.") from error

    def close(self) -> None:
        self.closed = True


class Phase3FrameManager:
    """Owns prepared-frame readers and exposes one source-neutral acquisition API."""

    def __init__(
        self,
        runs: RunManager,
        input_preparer: InputPreparer,
        capture_factory: Callable[[str], Any] | None = None,
    ):
        self.runs = runs
        self.input_preparer = input_preparer
        self.capture_factory = capture_factory
        self._sources: dict[int, tuple[str, str, str, FrameSource]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _session_key(session_token: str | None) -> str:
        if not session_token or not session_token.isascii():
            return ""
        return hashlib.sha256(session_token.encode("ascii")).hexdigest()

    def _upload_source(self, run_id: str, input_type: str) -> FrameSource:
        if input_type not in ("rgb", "thermal"):
            raise RunError(400, "invalid_frame_source", "Choose a prepared RGB or Thermal upload.")
        record = self.runs.get(run_id)
        if (
            record.status != "prepared"
            or record.source_type != "upload"
            or record.input_type != input_type
        ):
            raise RunError(409, "input_not_prepared", "Select a validated, prepared RGB or Thermal upload.")
        video_path = record.root / "input" / record.input_filename
        if not video_path.is_file():
            raise RunError(404, "input_not_found", "The prepared video is unavailable.")
        frame_count = record.input_metadata.get("frame_count")
        if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count <= 0:
            frame_count = None
        if input_type == "rgb":
            source = RGBVideoFrameSource(
                video_path,
                run_id,
                self.capture_factory,
                frame_count=frame_count,
            )
        else:
            source = ThermalVideoFrameSource(video_path, run_id, frame_count)
        return source

    def acquire_next(
        self,
        user_id: int,
        session_token: str | None,
        *,
        source_type: str,
        input_type: str | None = None,
        run_id: str | None = None,
        live_reference_id: str | None = None,
    ) -> PreparedFrame | None:
        if source_type == "cctv":
            if not isinstance(live_reference_id, str) or not live_reference_id:
                raise RunError(400, "missing_live_reference", "A live CCTV reference is required.")
            frame, _ = self.input_preparer.get_current_cctv_frame(user_id, live_reference_id)
            try:
                return _prepared_frame(
                    frame,
                    source_type="cctv",
                    input_type=None,
                    run_id=None,
                    live_reference_id=live_reference_id,
                    frame_index=None,
                    preprocessing_status="validated_contiguous_copy",
                )
            except RunError:
                self.input_preparer.mark_cctv_frame_unavailable(user_id, live_reference_id)
                raise
        if (
            source_type != "upload"
            or not isinstance(run_id, str)
            or not run_id
            or input_type not in ("rgb", "thermal")
        ):
            raise RunError(400, "invalid_frame_source", "Choose a prepared RGB, Thermal, or CCTV input.")

        session_key = self._session_key(session_token)
        if not session_key:
            raise RunError(401, "authentication_required", "Authentication is required.")
        with self._lock:
            active = self._sources.get(user_id)
            if (
                active is None
                or active[0] != session_key
                or active[1] != run_id
                or active[2] != input_type
            ):
                if active is not None:
                    active[3].close()
                    self._sources.pop(user_id, None)
                source = self._upload_source(run_id, input_type)
                self._sources[user_id] = (session_key, run_id, input_type, source)
            else:
                source = active[3]
            try:
                frame = source.read_next()
            except Exception:
                source.close()
                self._sources.pop(user_id, None)
                raise
            if frame is None:
                source.close()
                self._sources.pop(user_id, None)
            return frame

    def close_user(self, user_id: int) -> None:
        with self._lock:
            active = self._sources.pop(user_id, None)
        if active is not None:
            active[3].close()

    def close_run(self, user_id: int, run_id: str) -> bool:
        with self._lock:
            active = self._sources.get(user_id)
            if active is None or active[1] != run_id:
                return False
            self._sources.pop(user_id)
        active[3].close()
        return True

    def close_session(self, session_token: str | None) -> None:
        session_key = self._session_key(session_token)
        if not session_key:
            return
        with self._lock:
            user_ids = [
                user_id
                for user_id, active in self._sources.items()
                if active[0] == session_key
            ]
        for user_id in user_ids:
            self.close_user(user_id)

    def close_all(self) -> None:
        with self._lock:
            sources = [active[3] for active in self._sources.values()]
            self._sources.clear()
        for source in sources:
            source.close()
