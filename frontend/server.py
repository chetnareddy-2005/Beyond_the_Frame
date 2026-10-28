"""Serve the static dashboard and proxy its authenticated API requests."""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)
FRONTEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_ROOT.parent
EVIDENCE_ROOT = PROJECT_ROOT / "results" / "caltech_rgb_tracking" / "phase4_uncertain_evidence"
RUN_ID_PATTERN = r"RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}"
APP_ROUTES = {
    "/",
    "/login",
    "/dashboard",
    "/photo-detection",
    "/live-cctv",
    "/video-analysis",
    "/results",
    "/history",
    "/alerts",
    "/reports",
    "/settings",
    "/authorization",
    "/headquarters-sectors",
    "/headquarters-drones",
    "/headquarters-users",
    "/sector-dashboard",
    "/sector-registry",
}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BeyondTheFrameDashboard/1.0"

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _contained_file(root: Path, relative_path: str) -> Path | None:
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _serve_file(self, root: Path, relative_path: str) -> None:
        path = self._contained_file(root, relative_path)
        if path is None:
            self._send(404, "text/plain; charset=utf-8", b"Not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, content_type, path.read_bytes())

    def _proxy_api(self, path: str) -> None:
        api_origin = self.server.api_origin
        try:
            headers = {"Accept": "application/json"}
            if self.headers.get("Cookie"):
                headers["Cookie"] = self.headers["Cookie"]
            if self.headers.get("Range"):
                headers["Range"] = self.headers["Range"]
            request = Request(f"{api_origin}{path}", headers=headers)
            with urlopen(request, timeout=10) as response:
                response_headers = response.headers.get_all("Set-Cookie", [])
                forwarded_headers = {
                    name: response.headers[name]
                    for name in (
                        "X-Input-Source",
                        "X-Live-Reference-ID",
                        "X-Frame-Available",
                        "Accept-Ranges",
                        "Content-Range",
                        "Content-Disposition",
                    )
                    if response.headers.get(name)
                }
                if response_headers:
                    forwarded_headers["Set-Cookie"] = response_headers[0]
                self._send(
                    response.status,
                    response.headers.get("Content-Type", "application/octet-stream"),
                    response.read(),
                    forwarded_headers,
                )
        except HTTPError as error:
            self._send(error.code, "application/json; charset=utf-8", error.read())
        except (URLError, TimeoutError, OSError) as error:
            LOGGER.warning("Backend API proxy request failed for %s: %s", path, error)
            self._send(
                502,
                "application/json; charset=utf-8",
                b'{"error":"backend_unavailable","message":"The backend API is unavailable"}',
            )

    @staticmethod
    def _is_allowed_post_path(path: str) -> bool:
        fixed = {
            "/api/auth/login", "/api/auth/logout", "/api/auth/change-password",
            "/api/authorization/evaluate", "/api/authorization/config",
            "/api/headquarters/sectors", "/api/headquarters/drones",
            "/api/authorization/telemetry", "/api/sector-registry/requests",
            "/api/sector-registry/password-reset", "/api/input/prepare",
            "/api/input/cctv/stop", "/api/phase3/frame", "/api/phase3/stop",
            "/api/phase4/detect", "/api/photo/detect", "/api/phase5/track",
            "/api/phase5/reset", "/api/phase6/temporal",
        }
        if path in fixed:
            return True
        return any(re.fullmatch(pattern, path) for pattern in (
            r"/api/headquarters/drones/[1-9][0-9]*/revoke",
            r"/api/headquarters/registry/REG_[0-9a-f]{32}/decision",
            r"/api/headquarters/password-reset/RST_[0-9a-f]{32}/decision",
            r"/api/runs/RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}/(start|cancel|reanalyze|resume)",
            r"/api/videos/V-[1-9][0-9]*/delete",
        ))

    def _proxy_post(self, path: str) -> None:
        is_auth = path in {"/api/auth/login", "/api/auth/logout", "/api/auth/change-password"}
        is_authorization = path in {"/api/authorization/evaluate", "/api/authorization/config"}
        is_headquarters_sectors = path in {
            "/api/headquarters/sectors", "/api/headquarters/drones", "/api/authorization/telemetry",
            "/api/sector-registry/requests", "/api/sector-registry/password-reset",
        } or bool(re.fullmatch(r"/api/headquarters/drones/[1-9][0-9]*/revoke", path)) or bool(
            re.fullmatch(r"/api/headquarters/registry/REG_[0-9a-f]{32}/decision", path)
        ) or bool(re.fullmatch(r"/api/headquarters/password-reset/RST_[0-9a-f]{32}/decision", path))
        is_input = path in {
            "/api/input/prepare",
            "/api/input/cctv/stop",
            "/api/phase3/frame",
            "/api/phase3/stop",
            "/api/phase4/detect",
            "/api/photo/detect",
            "/api/phase5/track",
            "/api/phase5/reset",
            "/api/phase6/temporal",
        }
        is_run = path == "/api/runs" or re.fullmatch(
            r"/api/runs/RUN_[0-9]{8}_[0-9]{6}_[0-9a-f]{24}/(start|cancel|reanalyze|resume)",
            path,
        ) or re.fullmatch(r"/api/videos/V-[1-9][0-9]*/delete", path)
        if not self._is_allowed_post_path(path):
            self._send(404, "application/json; charset=utf-8", b'{"error":"not_found","message":"Endpoint does not exist"}')
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(411, "application/json; charset=utf-8", b'{"error":"content_length_required","message":"Content-Length is required"}')
            return
        max_length = (
            22_000_000
            if path == "/api/sector-registry/requests"
            else 20 * 1024 * 1024
            if path == "/api/photo/detect"
            else 8192
            if is_auth
            or is_authorization
            or is_headquarters_sectors
            or path
            in {
                "/api/input/cctv/stop",
                "/api/phase3/frame",
                "/api/phase3/stop",
                "/api/phase4/detect",
                "/api/phase5/track",
                "/api/phase5/reset",
                "/api/phase6/temporal",
            }
            else 250 * 1024 * 1024 + 65536
        )
        if content_length < 0 or content_length > max_length:
            self._send(413, "application/json; charset=utf-8", b'{"error":"upload_too_large","message":"Upload exceeds the proxy size limit"}')
            return
        body = self.rfile.read(content_length)
        if len(body) != content_length:
            self._send(400, "application/json; charset=utf-8", b'{"error":"invalid_request","message":"Request body was incomplete"}')
            return
        headers = {
            "Accept": "application/json",
            "Content-Type": self.headers.get("Content-Type", "application/octet-stream"),
        }
        if self.headers.get("Cookie"):
            headers["Cookie"] = self.headers["Cookie"]
        try:
            request = Request(f"{self.server.api_origin}{path}", data=body, headers=headers, method="POST")
            with urlopen(request, timeout=120) as response:
                response_headers = response.headers.get_all("Set-Cookie", [])
                self._send(
                    response.status,
                    "application/json; charset=utf-8",
                    response.read(),
                    {"Set-Cookie": response_headers[0]} if response_headers else None,
                )
        except HTTPError as error:
            response_headers = error.headers.get_all("Set-Cookie", [])
            self._send(
                error.code,
                "application/json; charset=utf-8",
                error.read(),
                {"Set-Cookie": response_headers[0]} if response_headers else None,
            )
        except (URLError, TimeoutError, OSError) as error:
            LOGGER.warning("Backend API proxy POST failed for %s: %s", path, error)
            self._send(
                502,
                "application/json; charset=utf-8",
                b'{"error":"backend_unavailable","message":"The backend API is unavailable"}',
            )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if parsed.query:
            self._send(400, "text/plain; charset=utf-8", b"Query parameters are not supported")
            return
        if path in APP_ROUTES or re.fullmatch(
            rf"/(?:results|reports)/{RUN_ID_PATTERN}",
            path,
        ):
            self._serve_file(FRONTEND_ROOT, "index.html")
            return
        if path.startswith("/api/"):
            self._proxy_api(path)
            return
        if path.startswith("/evidence/"):
            self._serve_file(EVIDENCE_ROOT, path.removeprefix("/evidence/"))
            return
        relative_path = "index.html" if path == "/" else path.lstrip("/")
        self._serve_file(FRONTEND_ROOT, relative_path)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if parsed.query:
            self._send(400, "application/json; charset=utf-8", b'{"error":"malformed_request","message":"Query parameters are not supported"}')
            return
        self._proxy_post(path)

    def _method_not_allowed(self) -> None:
        self._send(405, "text/plain; charset=utf-8", b"Only GET requests are supported")

    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format_string % args)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], api_origin: str):
        super().__init__(address, DashboardHandler)
        self.api_origin = api_origin.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--api-origin",
        default=os.environ.get("BTF_API_ORIGIN", "http://127.0.0.1:8000"),
        help="Origin of the existing read-only backend API",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = DashboardServer((args.host, args.port), args.api_origin)
    LOGGER.info("Dashboard listening on http://%s:%s", *server.server_address)
    LOGGER.info("Proxying API requests to %s", server.api_origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down dashboard server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
