import json
import unittest
from unittest.mock import patch

from backend.api import application
from backend.dashboard_options import DashboardOptionsService


class FakeCourseCatalogue:
    def list_courses(self):
        return [
            {"normalized_course_code": "CS F211", "source_records": [
                {"course_title": {"value": "Data Structures and Algorithms"}}]},
            {"normalized_course_code": "BITS F110", "source_records": [
                {"course_title": "Engineering Graphics"},
                {"course_title": "Conflicting title"}]},
        ]


class FakeRuleCatalogue:
    records = [
        {"scope": {"programme": "M.Sc. Mathematics"}},
        {"scope": {"programme": "B.E. Computer Science"}},
        {"scope": {"programme": " B.E. Computer Science "}},
        {"scope": {"programme": None}},
    ]

    def resolve_scope(self, programme):
        normalized = programme.strip()
        return [normalized] if normalized in {
            "B.E. Computer Science", "M.Sc. Mathematics"} else []


class DashboardOptionsTests(unittest.TestCase):
    def setUp(self):
        self.service = DashboardOptionsService(
            FakeCourseCatalogue(), FakeRuleCatalogue())

    def test_options_are_stable_and_source_backed(self):
        result = self.service.options()
        self.assertEqual(result["programmes"], [
            "B.E. Computer Science", "M.Sc. Mathematics"])
        self.assertEqual(result["academic_years"], [1, 2, 3, 4])
        self.assertEqual(result["semesters"], [1, 2])
        self.assertEqual(result["summary"], {
            "programme_count": 2, "course_count": 2})

    def test_course_labels_use_unambiguous_catalogue_title(self):
        courses = self.service.options()["courses"]
        self.assertEqual(courses[0], {
            "course_code": "CS F211",
            "course_title": "Data Structures and Algorithms",
            "label": "CS F211 — Data Structures and Algorithms",
        })
        self.assertEqual(courses[1], {
            "course_code": "BITS F110", "course_title": None,
            "label": "BITS F110",
        })

    def test_real_unified_catalogue_contains_bulletin_only_courses(self):
        courses = {item["course_code"]: item
                   for item in DashboardOptionsService().options()["courses"]}
        self.assertEqual(courses["CS F211"]["course_title"],
                         "Data Structures & Algorithms")
        self.assertEqual(courses["CS F212"]["course_title"], "Database Systems")
        self.assertEqual(courses["CS F437"]["course_title"],
                         "Generative Artificial Intelligence")

    def test_options_endpoint_returns_dashboard_contract(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        with patch("backend.api._dashboard_options", return_value=self.service):
            body = b"".join(application({
                "PATH_INFO": "/api/options", "REQUEST_METHOD": "GET"},
                start_response))
        payload = json.loads(body)
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(payload["options"]["summary"]["course_count"], 2)

    def test_options_endpoint_is_read_only(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        body = b"".join(application({
            "PATH_INFO": "/api/options", "REQUEST_METHOD": "POST"},
            start_response))
        self.assertEqual(captured["status"], "405 Method Not Allowed")
        self.assertEqual(json.loads(body)["error"], "method_not_allowed")


class DashboardPolishStaticTests(unittest.TestCase):
    def _asset(self, path):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        body = b"".join(application({"PATH_INFO": path, "REQUEST_METHOD": "GET"},
                                    start_response)).decode()
        self.assertEqual(captured["status"], "200 OK")
        return body

    def test_dashboard_has_single_recommendation_flow_and_searchable_pickers(self):
        html = self._asset("/")
        self.assertIn('id="recommendation-form"', html)
        self.assertIn('id="programme-suggestions"', html)
        self.assertIn('id="completed-course-search"', html)
        self.assertNotIn('id="ongoing-course-search"', html)
        self.assertNotIn("Ongoing courses", html)
        self.assertIn('id="suggested-courses-panel"', html)
        self.assertIn('id="confirm-all-suggestions"', html)
        self.assertNotIn("Validate profile", html)
        self.assertNotIn("Dashboard foundation", html)
        self.assertNotIn("Backend connection", html)
        self.assertNotIn('name="completed_courses"', html)
        self.assertNotIn('name="ongoing_courses"', html)

    def test_frontend_loads_catalogue_and_sends_empty_ongoing_courses(self):
        script = self._asset("/assets/app.js")
        self.assertIn('fetch("/api/options"', script)
        self.assertIn('fetch("/api/recommendations"', script)
        self.assertIn('fetch("/api/course-suggestions"', script)
        self.assertIn("normalizeProgrammeSearch", script)
        self.assertIn('courseEntries("completed")', script)
        self.assertIn('ongoing_courses: []', script)
        self.assertNotIn('courseEntries("ongoing")', script)
        self.assertNotIn('fetch("/api/student-profile"', script)
        profile_function = script.split("function profilePayload()", 1)[1].split("function localValidationIssues", 1)[0]
        self.assertNotIn("suggested", profile_function)

    def test_frontend_renders_suggestions_without_marking_them_completed(self):
        script = self._asset("/assets/app.js")
        self.assertIn("function renderCourseSuggestions()", script)
        self.assertIn('confirm.textContent = "Confirm completed"', script)
        self.assertIn('dismiss.textContent = "Remove"', script)
        self.assertIn("function confirmSuggestion(item)", script)
        before_confirmation = script.split(
            "function refreshCourseSuggestions()", 1)[1].split(
                "function confirmSuggestion(item)", 1)[0]
        self.assertNotIn("dashboardState.completed.set", before_confirmation)

    def test_frontend_limits_related_display_and_handles_fallback(self):
        script = self._asset("/assets/app.js")
        self.assertIn("RELATED_DISPLAY_LIMIT = 8", script)
        self.assertIn("gemini_unavailable_or_invalid", script)
        self.assertIn("No matching courses yet.", script)
        self.assertNotIn("Matched preference:", script)
        self.assertNotIn("Course evidence:", script)
        self.assertNotIn("Requirement fit:", script)
        self.assertIn("result.validation?.is_valid !== true", script)

    def test_required_markers_and_result_cards_are_compact(self):
        html = self._asset("/")
        styles = self._asset("/assets/styles.css")
        script = self._asset("/assets/app.js")
        self.assertIn('class="required-marker"', html)
        self.assertIn(".field-label", styles)
        self.assertIn("grid-auto-rows: 1fr", styles)
        self.assertIn("Eligibility verification required", script)
        self.assertNotIn("course-explanation", script)
        self.assertNotIn("course-evidence", script)


if __name__ == "__main__":
    unittest.main()
