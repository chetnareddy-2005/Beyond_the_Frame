from __future__ import annotations

import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from frontend.server import DashboardHandler, DashboardServer


class DashboardServerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DashboardServer(("127.0.0.1", 0), "http://127.0.0.1:8000")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_sector_registry_no_longer_exposes_phone_otp_routes(self):
        self.assertFalse(DashboardHandler._is_allowed_post_path("/api/sector-registry/phone-otp/send"))
        self.assertFalse(DashboardHandler._is_allowed_post_path("/api/sector-registry/phone-otp/verify"))
        self.assertTrue(DashboardHandler._is_allowed_post_path("/api/sector-registry/requests"))

    def test_all_page_and_detail_urls_serve_the_authenticated_app_shell(self):
        run_id = "RUN_20261003_141356_b7f92b69fd4d6559113218b4"
        routes = (
            "/dashboard",
            "/live-cctv",
            "/video-analysis",
            "/results",
            "/results/" + run_id,
            "/history",
            "/alerts",
            "/reports",
            "/reports/" + run_id,
            "/settings",
        )
        for route in routes:
            with self.subTest(route=route):
                with urlopen(self.base_url + route, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'id="dashboard-view"', response.read())

    def test_malformed_detail_route_is_not_treated_as_an_application_page(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base_url + "/results/not-a-run", timeout=5)
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
