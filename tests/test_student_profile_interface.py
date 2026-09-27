from io import BytesIO
import json
import unittest
from unittest.mock import patch

from backend.api import application


KNOWN_CODES = ("CS F211", "MATH F101", "BITS U103")


def request(path, method="GET", payload=None, raw_body=None):
    captured = {}
    if raw_body is None:
        raw_body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    environ = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(raw_body)),
        "wsgi.input": BytesIO(raw_body),
    }

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    with patch("backend.api._known_course_codes", return_value=KNOWN_CODES):
        body = b"".join(application(environ, start_response))
    return captured["status"], captured["headers"], json.loads(body)


def profile(**updates):
    value = {
        "programme": "B.E. Computer Science",
        "second_programme": None,
        "current_academic_year": 2,
        "current_semester": 1,
        "completed_courses": [{"course_code": "CS F211"}],
        "ongoing_courses": [{"course_code": "MATH F101"}],
    }
    value.update(updates)
    return value


class StudentProfileInterfaceTests(unittest.TestCase):
    def test_valid_profile_is_normalized_for_backend(self):
        status, _, result = request("/api/student-profile", "POST", profile())
        normalized = result["profile"]
        self.assertEqual(status, "200 OK")
        self.assertTrue(normalized["validation"]["is_valid"])
        self.assertEqual(normalized["completed_courses"][0]["normalized_course_code"],
                         "CS F211")
        self.assertEqual(normalized["completed_courses"][0]["status"], "completed")
        self.assertEqual(normalized["ongoing_courses"][0]["status"], "ongoing")

    def test_optional_second_programme_is_preserved(self):
        status, _, result = request(
            "/api/student-profile", "POST", profile(second_programme="M.Sc. Mathematics"))
        self.assertEqual(status, "200 OK")
        self.assertEqual(result["profile"]["second_programme"], "M.Sc. Mathematics")

    def test_invalid_required_fields_return_422_with_issues(self):
        status, _, result = request(
            "/api/student-profile", "POST",
            profile(programme="", current_academic_year=0, current_semester=3))
        self.assertEqual(status, "422 Unprocessable Entity")
        codes = {issue["code"] for issue in result["profile"]["validation"]["issues"]}
        self.assertEqual(codes, {"missing_programme", "invalid_academic_year",
                                 "invalid_semester"})

    def test_completed_and_ongoing_duplicate_is_rejected(self):
        status, _, result = request(
            "/api/student-profile", "POST",
            profile(ongoing_courses=[{"course_code": "csf211"}]))
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertIn("incompatible_course_status",
                      {issue["code"] for issue in result["profile"]["validation"]["issues"]})

    def test_malformed_course_code_is_rejected(self):
        status, _, result = request(
            "/api/student-profile", "POST",
            profile(completed_courses=[{"course_code": "not a course"}]))
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertIn("malformed_course_code",
                      {issue["code"] for issue in result["profile"]["validation"]["issues"]})

    def test_unknown_but_valid_course_is_a_warning(self):
        status, _, result = request(
            "/api/student-profile", "POST",
            profile(completed_courses=[{"course_code": "BIO F999"}]))
        self.assertEqual(status, "200 OK")
        self.assertTrue(result["profile"]["validation"]["is_valid"])
        self.assertIn("unknown_course_code",
                      {issue["code"] for issue in result["profile"]["validation"]["issues"]})

    def test_invalid_json_returns_400(self):
        status, _, result = request(
            "/api/student-profile", "POST", raw_body=b"{not-json")
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(result["error"], "invalid_json")

    def test_non_mapping_json_returns_400(self):
        status, _, result = request("/api/student-profile", "POST", ["bad"])
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(result["error"], "invalid_profile_payload")

    def test_empty_body_returns_400(self):
        status, _, result = request("/api/student-profile", "POST")
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(result["error"], "empty_request_body")

    def test_dashboard_contains_profile_inputs(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        body = b"".join(application({"PATH_INFO": "/", "REQUEST_METHOD": "GET"},
                                    start_response)).decode()
        self.assertEqual(captured["status"], "200 OK")
        for field in ("programme", "second_programme", "current_academic_year",
                      "current_semester", "completed_courses", "ongoing_courses"):
            self.assertIn(f'name="{field}"', body)
        self.assertNotIn("recommendation-query", body)

    def test_frontend_posts_backend_compatible_profile(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        script = b"".join(application({"PATH_INFO": "/assets/app.js",
                                       "REQUEST_METHOD": "GET"}, start_response)).decode()
        self.assertIn('fetch("/api/student-profile"', script)
        self.assertIn('fetch("/api/recommendations"', script)
        self.assertIn("completed_courses: courseEntries", script)
        self.assertIn("ongoing_courses: courseEntries", script)


if __name__ == "__main__":
    unittest.main()
