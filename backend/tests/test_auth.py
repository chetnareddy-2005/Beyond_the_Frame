from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from backend.app import create_server
from backend.auth import AuthManager, DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME, is_valid_new_password
from backend.config import get_settings


class AuthenticationAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.database_path = root / "auth" / "users.sqlite3"
        cls.server = create_server(
            port=0,
            run_runner=lambda *_args: None,
            runs_root=root / "runs",
            auth_db_path=cls.database_path,
            auth_username=DEVELOPMENT_USERNAME,
            auth_password=DEVELOPMENT_PASSWORD,
        )
        assert cls.server.registry_purger_thread is None  # Test servers must not purge the developer project database.
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp_dir.cleanup()

    def request(self, path, method="GET", payload=None, cookie=None):
        headers = {}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if cookie:
            headers["Cookie"] = cookie
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), response.headers
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_login_protected_endpoints_and_logout(self):
        status, session, _ = self.request("/api/auth/session")
        self.assertEqual((status, session), (200, {"authenticated": False}))
        self.assertEqual(self.request("/api/health")[0], 200)
        self.assertEqual(self.request("/api/overview")[0], 401)
        self.assertEqual(self.request("/api/runs")[0], 401)
        self.assertEqual(self.request("/api/auth/history")[0], 401)

        invalid_payloads = (
            {},
            {"username": DEVELOPMENT_USERNAME, "password": ""},
            {"username": "unknown-user", "password": DEVELOPMENT_PASSWORD},
            {"username": DEVELOPMENT_USERNAME, "password": "wrong-password"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                status, response, headers = self.request(
                    "/api/auth/login", "POST", payload=payload
                )
                self.assertEqual(status, 401)
                self.assertEqual(response["message"], "Invalid username or password.")
                self.assertNotIn("Set-Cookie", headers)

        status, response, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        self.assertTrue(response["authenticated"])
        self.assertEqual(response["username"], DEVELOPMENT_USERNAME)
        self.assertEqual(response["role"], "headquarters_admin")
        set_cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        cookie = set_cookie.split(";", 1)[0]

        status, session, _ = self.request("/api/auth/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(session["authenticated"])
        self.assertEqual(session["username"], DEVELOPMENT_USERNAME)
        self.assertEqual(session["role"], "headquarters_admin")
        _, history, _ = self.request("/api/auth/history", cookie=cookie)
        self.assertEqual(len(history["sessions"]), 1)
        self.assertTrue(history["sessions"][0]["active"])
        self.assertEqual(self.request("/api/overview", cookie=cookie)[0], 200)
        boundary = "phase-one-auth-upload-boundary"
        upload_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="video"; filename="sample.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
            "test video\r\n"
            f"--{boundary}--\r\n"
        ).encode()

        def upload(upload_cookie=None):
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            if upload_cookie:
                headers["Cookie"] = upload_cookie
            request = Request(
                self.base_url + "/api/runs",
                data=upload_body,
                headers=headers,
                method="POST",
            )
            try:
                with urlopen(request, timeout=5) as response:
                    return response.status
            except HTTPError as error:
                return error.code

        self.assertEqual(upload(), 401)
        self.assertEqual(upload(cookie), 201)

        connection = sqlite3.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT username, password_hash FROM users"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], DEVELOPMENT_USERNAME)
        self.assertTrue(rows[0][1].startswith("pbkdf2_sha256$600000$"))
        self.assertNotEqual(rows[0][1], DEVELOPMENT_PASSWORD)
        self.assertNotIn(DEVELOPMENT_PASSWORD.encode(), self.database_path.read_bytes())

        status, response, logout_headers = self.request(
            "/api/auth/logout", "POST", cookie=cookie
        )
        self.assertEqual((status, response), (200, {"authenticated": False}))
        self.assertIn("Max-Age=0", logout_headers["Set-Cookie"])
        self.assertEqual(self.request("/api/auth/session", cookie=cookie)[1], {"authenticated": False})
        self.assertEqual(self.request("/api/overview", cookie=cookie)[0], 401)
        status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"username": DEVELOPMENT_USERNAME, "password": DEVELOPMENT_PASSWORD},
        )
        self.assertEqual(status, 200)
        renewed_cookie = headers["Set-Cookie"].split(";", 1)[0]
        _, history, _ = self.request("/api/auth/history", cookie=renewed_cookie)
        self.assertEqual(len(history["sessions"]), 2)
        ended = next(session for session in history["sessions"] if not session["active"])
        self.assertIsInstance(ended["duration_seconds"], int)
        self.request("/api/auth/logout", "POST", cookie=renewed_cookie)

    def test_explicit_bootstrap_credentials_are_accepted(self):
        root = Path(self.temp_dir.name) / "explicit-bootstrap"
        auth = AuthManager(
            root / "users.sqlite3",
            bootstrap_username="bootstrap-admin",
            bootstrap_password="bootstrap-password",
        )
        self.assertEqual(auth.authenticate("bootstrap-admin", "bootstrap-password"), (1, "bootstrap-admin"))

    def test_missing_bootstrap_credentials_fail_fast(self):
        root = Path(self.temp_dir.name) / "missing-bootstrap"
        with self.assertRaises(RuntimeError):
            AuthManager(root / "users.sqlite3")

    def test_first_login_password_rule_accepts_only_8_to_12_character_complex_passwords(self):
        for password in ("Abcdef1!", "North-2026!", "A1b!2345678"):
            with self.subTest(password_length=len(password)):
                self.assertTrue(is_valid_new_password(password))
        for password in (
            "Ab1!", "Abcdefghijkl1!", "abcdefgh1!", "ABCDEFGH1!",
            "Abcdefgh!", "Abcdefg1", "Abcdef1 ", "ébcdefg1!",
        ):
            with self.subTest(password_length=len(password)):
                self.assertFalse(is_valid_new_password(password))

    def test_change_password_enforces_complex_rule_without_changing_hash_on_rejection(self):
        root = Path(self.temp_dir.name) / "password-rule"
        auth = AuthManager(root / "users.sqlite3", bootstrap_username="hq", bootstrap_password="Valid-HQ-Password1")
        account = auth.create_sector_account("S1", username="head@example.test")
        with sqlite3.connect(root / "users.sqlite3") as connection:
            before = connection.execute(
                "SELECT password_hash FROM users WHERE user_id=?", (account["user_id"],)
            ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "8 to 12 characters"):
            auth.change_password(account["user_id"], account["temporary_password"], "missing-symbol1A")
        with sqlite3.connect(root / "users.sqlite3") as connection:
            after = connection.execute(
                "SELECT password_hash FROM users WHERE user_id=?", (account["user_id"],)
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertTrue(auth.change_password(account["user_id"], account["temporary_password"], "Abcdef1!"))

    def test_sector_login_requires_matching_selection_and_hq_login_is_unchanged(self):
        root = Path(self.temp_dir.name) / "login-role-selection"
        auth = AuthManager(root / "users.sqlite3", bootstrap_username="hq", bootstrap_password="Valid-HQ-Password1")
        account = auth.create_sector_account("S1", username="head@example.test")
        self.assertIsNone(auth.authenticate("head@example.test", account["temporary_password"], expected_role="sector"))
        self.assertIsNone(auth.authenticate("head@example.test", account["temporary_password"], "S2", "sector"))
        self.assertEqual(auth.authenticate("head@example.test", account["temporary_password"], "S1", "sector"),
                         (account["user_id"], "head@example.test"))
        self.assertEqual(auth.authenticate("hq", "Valid-HQ-Password1", expected_role="headquarters_admin"), (1, "hq"))


class ApprovedSectorLoginListAPITests(unittest.TestCase):
    def test_approved_sector_list_is_public_and_empty_without_approved_sectors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = get_settings(root)
            server = create_server(
                project_root=root,
                port=0,
                run_runner=lambda *_args: None,
                runs_root=root / "runs",
                auth_db_path=root / "auth" / "users.sqlite3",
                auth_username="test-admin",
                auth_password="Test-Password1!",
                detector_service=SimpleNamespace(close=lambda: None),
                project_data=SimpleNamespace(settings=settings),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                request = Request(f"http://{host}:{port}/api/sector-registry/approved-sectors")
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), {"sectors": []})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class SectorOperationalAccessAPITests(unittest.TestCase):
    def test_sector_run_access_is_owner_scoped_and_hq_apis_remain_restricted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = get_settings(root)
            server = create_server(
                project_root=root,
                port=0,
                run_runner=lambda *_args: None,
                runs_root=root / "runs",
                auth_db_path=root / "auth" / "users.sqlite3",
                auth_username="isolated-hq",
                auth_password="Test-HQ1!",
                detector_service=SimpleNamespace(close=lambda: None),
                project_data=SimpleNamespace(settings=settings),
            )
            account = server.auth_manager.create_sector_account("S1", username="sector@example.test")
            password = "Sector1!"
            self.assertTrue(
                server.auth_manager.change_password(
                    account["user_id"], account["temporary_password"], password
                )
            )
            other_run = server.run_manager.create_run(
                "hq-owned.mp4", b"hq-only-video", owner_id=1
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with patch("backend.app.is_sector_active", return_value=True):
                try:
                    host, port = server.server_address
                    base_url = f"http://{host}:{port}"
                    login = Request(
                        base_url + "/api/auth/login",
                        data=json.dumps({
                            "username": "sector@example.test",
                            "password": password,
                            "account_type": "sector",
                            "sector_number": "S1",
                        }).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(login, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                        sector_user_id = json.loads(response.read())["user_id"]

                    boundary = "sector-isolated-upload"
                    body = (
                        f"--{boundary}\r\n"
                        'Content-Disposition: form-data; name="video"; filename="sector.mp4"\r\n'
                        "Content-Type: video/mp4\r\n\r\n"
                        "sector-owned-video\r\n"
                        f"--{boundary}--\r\n"
                    ).encode()
                    upload = Request(
                        base_url + "/api/runs",
                        data=body,
                        headers={
                            "Cookie": cookie,
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                        },
                        method="POST",
                    )
                    with urlopen(upload, timeout=5) as response:
                        self.assertEqual(response.status, 201)
                        sector_run_id = json.loads(response.read())["run_id"]

                    def get(path):
                        request = Request(base_url + path, headers={"Cookie": cookie})
                        try:
                            with urlopen(request, timeout=5) as response:
                                return response.status, json.loads(response.read())
                        except HTTPError as error:
                            return error.code, json.loads(error.read())

                    status, runs = get("/api/runs")
                    self.assertEqual(status, 200)
                    self.assertEqual([run["run_id"] for run in runs["runs"]], [sector_run_id])
                    self.assertEqual(get(f"/api/runs/{sector_run_id}")[0], 200)
                    self.assertEqual(get(f"/api/runs/{other_run.run_id}")[0], 404)
                    self.assertEqual(get("/api/auth/history")[0], 200)
                    self.assertEqual(get("/api/overview")[0], 403)
                    self.assertEqual(get("/api/headquarters/users")[0], 403)
                    self.assertEqual(get("/api/headquarters/dashboard-trends")[0], 403)
                    self.assertEqual(get(f"/api/headquarters/sectors/S1/personnel-history")[0], 403)
                    dashboard_status, sector_dashboard = get("/api/sector/dashboard")
                    self.assertEqual(dashboard_status, 200, sector_dashboard)
                    self.assertEqual(sector_dashboard["sector"]["id"], "S1")
                    self.assertIn("summary", sector_dashboard)
                    self.assertGreater(sector_user_id, 1)

                    phase_reset = Request(
                        base_url + "/api/phase5/reset",
                        data=b"",
                        headers={"Cookie": cookie},
                        method="POST",
                    )
                    with urlopen(phase_reset, timeout=5) as response:
                        self.assertEqual(response.status, 200)

                    invalid_fusion = Request(
                        base_url + "/api/phase9/fuse",
                        data=b"{}",
                        headers={
                            "Cookie": cookie,
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )
                    try:
                        urlopen(invalid_fusion, timeout=5)
                    except HTTPError as error:
                        self.assertEqual(error.code, 400)
                        self.assertEqual(json.loads(error.read())["error"], "missing_run_id")
                    else:
                        self.fail("Sector POST should pass role gating and fail on its missing run ID.")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
