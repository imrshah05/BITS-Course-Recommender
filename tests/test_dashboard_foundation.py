import json
import unittest

from backend.api import application


def request(path="/", method="GET"):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application({"PATH_INFO": path, "REQUEST_METHOD": method},
                                start_response))
    return captured["status"], captured["headers"], body


class DashboardFoundationTests(unittest.TestCase):
    def test_health_endpoint(self):
        status, headers, body = request("/api/health")
        payload = json.loads(body)
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_config_exposes_available_features(self):
        status, _, body = request("/api/config")
        payload = json.loads(body)
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload["api_base_path"], "/api")
        self.assertTrue(payload["features"]["student_profile"])
        self.assertTrue(payload["features"]["preference_query"])
        self.assertTrue(payload["features"]["recommendations"])
        self.assertFalse(payload["features"]["timetable"])

    def test_dashboard_shell_is_served(self):
        status, headers, body = request("/")
        text = body.decode()
        self.assertEqual(status, "200 OK")
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("BITS Academic Course Recommender", text)
        self.assertIn('id="api-status"', text)

    def test_stylesheet_is_served(self):
        status, headers, body = request("/assets/styles.css")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/css", headers["Content-Type"])
        self.assertIn(b".dashboard-shell", body)

    def test_javascript_loads_dashboard_options(self):
        status, headers, body = request("/assets/app.js")
        self.assertEqual(status, "200 OK")
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn(b'fetch("/api/options"', body)

    def test_unknown_api_route_is_json_404(self):
        status, headers, body = request("/api/missing")
        self.assertEqual(status, "404 Not Found")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_unknown_static_route_is_404(self):
        status, _, body = request("/missing")
        self.assertEqual(status, "404 Not Found")
        self.assertIn(b"not found", body)

    def test_non_get_method_is_rejected(self):
        status, headers, body = request("/api/health", "POST")
        self.assertEqual(status, "405 Method Not Allowed")
        self.assertEqual(headers["Allow"], "GET")
        self.assertEqual(json.loads(body)["error"], "method_not_allowed")

    def test_static_route_map_prevents_path_traversal(self):
        status, _, _ = request("/assets/../backend/api.py")
        self.assertEqual(status, "404 Not Found")


if __name__ == "__main__":
    unittest.main()
