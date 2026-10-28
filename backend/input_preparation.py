"""Phase 2 input validation and short-lived live-stream preparation."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from backend.runs import RunError, RunManager, _safe_upload_extension


def _safe_rtsp_reference(parts: SplitResult) -> str:
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, "", "", ""))


def _validate_rtsp_url(value: Any) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise RunError(400, "invalid_rtsp_url", "Provide a valid RTSP URL.")
    if any(ord(character) < 32 for character in value):
        raise RunError(400, "invalid_rtsp_url", "Provide a valid RTSP URL.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise RunError(400, "invalid_rtsp_url", "Provide a valid RTSP URL.") from error
    host = parts.hostname or ""
    valid_host = False
    try:
        ipaddress.ip_address(host)
        valid_host = True
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
            valid_host = (
                len(ascii_host) <= 253
                and bool(re.fullmatch(r"[A-Za-z0-9.-]+", ascii_host))
                and all(label and len(label) <= 63 for label in ascii_host.rstrip(".").split("."))
            )
        except UnicodeError:
            valid_host = False
    if (
        parts.scheme.casefold() not in {"rtsp", "rtsps"}
        or not host
        or not valid_host
        or parts.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise RunError(400, "invalid_rtsp_url", "Provide a valid RTSP URL.")
    return value, _safe_rtsp_reference(parts)


def _video_capture(source: str):
    import cv2

    params = [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
        5000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC,
        5000,
    ]
    return cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)


def _capture_metadata(capture: Any) -> dict[str, Any]:
    import cv2

    def finite_positive(prop: int, *, integer: bool = False):
        try:
            value = float(capture.get(prop))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return int(value) if integer else value

    fps = finite_positive(cv2.CAP_PROP_FPS)
    width = finite_positive(cv2.CAP_PROP_FRAME_WIDTH, integer=True)
    height = finite_positive(cv2.CAP_PROP_FRAME_HEIGHT, integer=True)
    frame_count = finite_positive(cv2.CAP_PROP_FRAME_COUNT, integer=True)
    duration = frame_count / fps if frame_count and fps else None
    return {
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "duration_seconds": duration,
    }


@dataclass
class LiveInputSession:
    reference_id: str
    capture: Any
    metadata: dict[str, Any]
    state_path: Path
    session_key: str
    source_url: str
    last_frame: Any
    connection_status: str = "connected"


class InputPreparer:
    def __init__(self, runs: RunManager, capture_factory: Callable[[str], Any] | None = None):
        self.runs = runs
        self.capture_factory = capture_factory or _video_capture
        self._live_captures: dict[int, LiveInputSession] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _live_frame_metadata(frame: Any) -> dict[str, int]:
        shape = getattr(frame, "shape", None)
        if not isinstance(shape, (tuple, list)) or len(shape) not in (2, 3):
            raise RunError(422, "invalid_frame", "The CCTV source returned an invalid image frame.")
        height, width = shape[:2]
        if (
            not isinstance(height, Integral)
            or not isinstance(width, Integral)
            or height <= 0
            or width <= 0
        ):
            raise RunError(422, "invalid_frame", "The CCTV source returned invalid frame dimensions.")
        return {
            "width": int(width),
            "height": int(height),
            "decoded_frame_width": int(width),
            "decoded_frame_height": int(height),
        }

    def mark_cctv_frame_unavailable(self, user_id: int, reference_id: str) -> None:
        with self._lock:
            active = self._live_captures.get(user_id)
            if active is None or not secrets.compare_digest(active.reference_id, reference_id):
                return
            active.last_frame = None
            active.connection_status = "frame_unavailable"
            self.runs.update_input_state(
                active.state_path,
                connection_status=active.connection_status,
                frame_available=False,
                readiness="FRAME_UNAVAILABLE",
            )

    def _inspect_video(self, video_path: Path, input_type: str) -> dict[str, Any]:
        thermal_frame_metadata = None
        if input_type == "thermal":
            try:
                from multimodal.thermal_adapter import ThermalAdapter

                adapter = ThermalAdapter(enabled=True, thermal_source=str(video_path))
                frame = adapter.read_frame(str(video_path), 0)
                prepared_frame = adapter.preprocess_frame(frame)
                if prepared_frame is not None:
                    thermal_frame_metadata = adapter.build_frame_statistics(prepared_frame)
            except Exception as error:
                raise RunError(422, "invalid_video", "Thermal video could not be decoded.") from error
            if frame is None or prepared_frame is None:
                raise RunError(422, "invalid_video", "Thermal video could not be decoded.")
        capture = None
        try:
            capture = self.capture_factory(str(video_path))
            if not capture.isOpened():
                raise RunError(422, "invalid_video", "Video could not be opened or decoded.")
            success, frame = capture.read()
            if not success or frame is None or getattr(frame, "size", 0) == 0:
                raise RunError(422, "invalid_video", "Video could not be decoded.")
            metadata = _capture_metadata(capture)
            metadata["decoded_frame_width"] = int(frame.shape[1])
            metadata["decoded_frame_height"] = int(frame.shape[0])
            if thermal_frame_metadata is not None:
                metadata["thermal_first_frame_intensity"] = thermal_frame_metadata
                metadata["thermal_preprocessing"] = (
                    "first-frame grayscale intensity normalized to uint8; not temperature-calibrated"
                )
            return metadata
        except RunError:
            raise
        except Exception as error:
            raise RunError(422, "invalid_video", "Video could not be decoded.") from error
        finally:
            if capture is not None:
                capture.release()

    def prepare_upload(self, input_type: str, filename: str, content: bytes, user_id: int = 1):
        if input_type not in {"rgb", "thermal", "radar"}:
            raise RunError(400, "unsupported_input_type", "Select RGB, Thermal, or Radar.")
        if input_type == "radar":
            raise RunError(
                415,
                "unsupported_radar_format",
                "Radar upload is unavailable because no supported input file format is defined.",
            )
        extension = _safe_upload_extension(filename)
        if not content:
            raise RunError(400, "empty_upload", "The selected video is empty.")
        if len(content) > self.runs.max_upload_bytes:
            raise RunError(413, "upload_too_large", "Video exceeds the configured size limit.")
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(content)
                temporary.flush()
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
        try:
            metadata = self._inspect_video(temporary_path, input_type)
        finally:
            temporary_path.unlink(missing_ok=True)
        record = self.runs.create_run(
            filename,
            content,
            source_type="upload",
            input_type=input_type,
            input_status="validated",
            input_metadata=metadata,
            prepared=True,
            owner_id=user_id,
        )
        return record

    def test_cctv(self, user_id: int, rtsp_url: Any) -> dict[str, Any]:
        source, safe_reference = _validate_rtsp_url(rtsp_url)
        capture = None
        try:
            capture = self.capture_factory(source)
            if not capture.isOpened():
                raise RunError(502, "stream_unavailable", "The CCTV stream is unavailable.")
            success, frame = capture.read()
            if not success or frame is None or getattr(frame, "size", 0) == 0:
                raise RunError(502, "frame_read_failure", "Connected to the CCTV stream, but could not read a frame.")
            metadata = _capture_metadata(capture)
            metadata["decoded_frame_width"] = int(frame.shape[1])
            metadata["decoded_frame_height"] = int(frame.shape[0])
            return {
                "ok": True,
                "source": "cctv",
                "status": "connected",
                "metadata": metadata,
                "input_reference": safe_reference,
            }
        except RunError:
            raise
        except Exception as error:
            raise RunError(502, "connection_failed", "Could not connect to the CCTV stream.") from error
        finally:
            if capture is not None:
                capture.release()

    def start_cctv(
        self, user_id: int, rtsp_url: Any, session_token: str | None = None
    ) -> dict[str, Any]:
        source, safe_reference = _validate_rtsp_url(rtsp_url)
        self.stop_cctv(user_id)
        capture = None
        try:
            capture = self.capture_factory(source)
            if not capture.isOpened():
                raise RunError(502, "stream_unavailable", "The CCTV stream is unavailable.")
            success, frame = capture.read()
            if not success or frame is None or getattr(frame, "size", 0) == 0:
                raise RunError(502, "frame_read_failure", "Connected to the CCTV stream, but could not read a frame.")
            metadata = self._live_frame_metadata(frame)
            reference = f"cctv:{secrets.token_urlsafe(18)}"
            state_path = self.runs.record_input_state(
                source_type="cctv",
                input_type=None,
                input_status="connected",
                input_reference=reference,
                live_reference_id=reference,
                source_url=safe_reference,
                metadata=metadata,
                connection_status="connected",
                frame_available=True,
                readiness="READY_FOR_PHASE_3",
            )
            session_key = (
                hashlib.sha256(session_token.encode("ascii")).hexdigest()
                if session_token and session_token.isascii()
                else ""
            )
            with self._lock:
                self._live_captures[user_id] = LiveInputSession(
                    reference_id=reference,
                    capture=capture,
                    metadata=metadata,
                    state_path=state_path,
                    session_key=session_key,
                    source_url=safe_reference,
                    last_frame=frame.copy(),
                )
            capture = None
            return self.get_cctv_handoff(user_id, reference)
        except RunError:
            raise
        except Exception as error:
            raise RunError(502, "connection_failed", "Could not connect to the CCTV stream.") from error
        finally:
            if capture is not None:
                capture.release()

    def get_cctv_handoff(self, user_id: int, reference_id: str) -> dict[str, Any]:
        with self._lock:
            active = self._live_captures.get(user_id)
            if active is None or not secrets.compare_digest(active.reference_id, reference_id):
                raise RunError(404, "live_input_not_found", "The live CCTV input is unavailable.")
            return self._handoff_payload(active)

    @staticmethod
    def _handoff_payload(active: LiveInputSession) -> dict[str, Any]:
        available = active.last_frame is not None
        return {
            "ok": True,
            "status": active.connection_status,
            "source_type": "cctv",
            "input_type": None,
            "live_reference_id": active.reference_id,
            "input_reference": active.reference_id,
            "connection_status": active.connection_status,
            "frame_available": available,
            "metadata": dict(active.metadata),
            "readiness": "READY_FOR_PHASE_3" if available else "FRAME_UNAVAILABLE",
            "source_url": active.source_url,
        }

    def get_current_cctv_frame(self, user_id: int, reference_id: str) -> tuple[Any, dict[str, Any]]:
        with self._lock:
            active = self._live_captures.get(user_id)
            if active is None or not secrets.compare_digest(active.reference_id, reference_id):
                raise RunError(404, "live_input_not_found", "The live CCTV input is unavailable.")
            try:
                success, frame = active.capture.read()
            except Exception as error:
                active.last_frame = None
                active.connection_status = "frame_unavailable"
                self.runs.update_input_state(
                    active.state_path,
                    connection_status=active.connection_status,
                    frame_available=False,
                    readiness="FRAME_UNAVAILABLE",
                )
                raise RunError(
                    502,
                    "frame_read_failure",
                    "The CCTV stream did not provide a frame.",
                ) from error
            if not success or frame is None or getattr(frame, "size", 0) == 0:
                active.last_frame = None
                active.connection_status = "frame_unavailable"
                self.runs.update_input_state(
                    active.state_path,
                    connection_status=active.connection_status,
                    frame_available=False,
                    readiness="FRAME_UNAVAILABLE",
                )
                raise RunError(502, "frame_read_failure", "The CCTV stream did not provide a frame.")
            active.last_frame = frame
            active.connection_status = "connected"
            try:
                active.metadata = self._live_frame_metadata(frame)
            except RunError:
                active.last_frame = None
                active.connection_status = "frame_unavailable"
                self.runs.update_input_state(
                    active.state_path,
                    connection_status=active.connection_status,
                    frame_available=False,
                    readiness="FRAME_UNAVAILABLE",
                )
                raise
            self.runs.update_input_state(
                active.state_path,
                connection_status=active.connection_status,
                frame_available=True,
                metadata=active.metadata,
                readiness="READY_FOR_PHASE_3",
            )
            return frame.copy(), self._handoff_payload(active)

    def stop_cctv(self, user_id: int) -> dict[str, Any]:
        with self._lock:
            active = self._live_captures.pop(user_id, None)
        if active is not None:
            try:
                active.capture.release()
            finally:
                self.runs.update_input_state(
                    active.state_path,
                    input_status="stopped",
                    input_reference=None,
                    live_reference_id=None,
                    connection_status="stopped",
                    frame_available=False,
                    readiness="STOPPED",
                    stopped_at=datetime.now(timezone.utc).isoformat(),
                )
        return {
            "ok": True,
            "source_type": "cctv",
            "status": "stopped",
            "live_reference_invalidated": active is not None,
        }

    def stop_cctv_for_session(self, session_token: str | None) -> None:
        if not session_token or not session_token.isascii():
            return
        session_key = hashlib.sha256(session_token.encode("ascii")).hexdigest()
        with self._lock:
            user_ids = [
                user_id
                for user_id, active in self._live_captures.items()
                if active.session_key and secrets.compare_digest(active.session_key, session_key)
            ]
        for user_id in user_ids:
            self.stop_cctv(user_id)

    def close_all(self) -> None:
        with self._lock:
            active_captures = list(self._live_captures.values())
            self._live_captures.clear()
        for active in active_captures:
            try:
                active.capture.release()
            finally:
                self.runs.update_input_state(
                    active.state_path,
                    input_status="stopped",
                    input_reference=None,
                    live_reference_id=None,
                    connection_status="stopped",
                    frame_available=False,
                    readiness="STOPPED",
                    stopped_at=datetime.now(timezone.utc).isoformat(),
                )
