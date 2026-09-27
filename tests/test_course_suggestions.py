import json
import unittest
from unittest.mock import patch
from io import BytesIO

from backend.api import application
from backend.course_suggestions import CourseSuggestionService


class FakeCatalogue:
    def __init__(self):
        self.codes = {"CS F111", "MATH F101", "BIO F101", "CS F211"}

    def contains(self, code):
        return code in self.codes

    def get(self, code):
        return {"source_records": [{"course_title": {
            "value": {"CS F111": "Computer Programming",
                      "MATH F101": "Multivariable Calculus",
                      "BIO F101": "Biological Sciences",
                      "CS F211": "Data Structures"}[code]}}]}


def record(code, programme, year, semester, verification=False):
    return {
        "kind": "required_course", "course_code": code,
        "programme_name": programme, "year": year, "semester": semester,
        "needs_verification": verification,
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": f"{code} source"}],
    }


def dataset(records):
    return {
        "programmes": [
            {"name": "B.E. Computer Science"},
            {"name": "M.Sc. Mathematics"},
            {"name": "M.Sc. Mathematics with B.E. Computer Science",
             "context": "composite dual-degree chart"},
        ],
        "requirements": records,
    }


def reference(composite, target, periods, verification=False):
    return {
        "kind": "curriculum_reference", "programme_name": composite,
        "referenced_programme_name": target,
        "reference_type": "same_as_first_degree_programme",
        "referenced_programme_role": "first_degree",
        "covered_periods": [
            {"year": year, "semester": semester} for year, semester in periods],
        "needs_verification": verification,
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": "Same as First degree Programme"}],
    }


class CourseSuggestionServiceTests(unittest.TestCase):
    def test_only_explicit_prior_semesters_are_suggested(self):
        service = CourseSuggestionService(dataset([
            record("CS F111", "B.E. Computer Science", 1, 1),
            record("MATH F101", "B.E. Computer Science", 1, 2),
            record("CS F211", "B.E. Computer Science", 2, 1),
        ]), FakeCatalogue())
        result = service.suggest(["B.E. Computer Science"], 2, 1)
        self.assertEqual([item["course_code"] for item in result["suggestions"]],
                         ["CS F111", "MATH F101"])
        self.assertTrue(all(item["suggestion_state"] ==
                            "awaiting_student_confirmation"
                            for item in result["suggestions"]))

    def test_two_programmes_are_kept_as_separate_source_scopes(self):
        requirements = dataset([
            record("CS F111", "B.E. Computer Science", 1, 1),
            record("BIO F101", "M.Sc. Mathematics", 1, 1),
            record("MATH F101", "B.E. Computer Science", 1, 1),
            record("MATH F101", "M.Sc. Mathematics", 1, 1),
        ])
        requirements["programmes"] = requirements["programmes"][:2]
        service = CourseSuggestionService(requirements, FakeCatalogue())
        result = service.suggest(
            ["B.E. Computer Science", "M.Sc. Mathematics"], 1, 2)
        by_code = {item["course_code"]: item for item in result["suggestions"]}
        self.assertEqual(by_code["MATH F101"]["programme_scopes"], [
            "B.E. Computer Science", "M.Sc. Mathematics"])
        self.assertEqual(result["summary"]["resolved_programme_count"], 2)

    def test_explicit_composite_chart_replaces_individual_patterns(self):
        service = CourseSuggestionService(dataset([
            record("CS F111", "B.E. Computer Science", 1, 1),
            record("BIO F101", "M.Sc. Mathematics", 1, 1),
            record("CS F211", "M.Sc. Mathematics with B.E. Computer Science", 2, 1),
        ]), FakeCatalogue())
        result = service.suggest(
            ["M.Sc. Mathematics", "B.E. Computer Science"], 2, 2)
        self.assertEqual(result["resolution_mode"], "composite_dual_degree_chart")
        self.assertEqual(result["resolved_programmes"], [
            "M.Sc. Mathematics with B.E. Computer Science"])
        self.assertEqual([item["course_code"] for item in result["suggestions"]],
                         ["CS F211"])

    def test_composite_inherits_only_explicitly_referenced_periods(self):
        requirements = dataset([
            record("CS F111", "M.Sc. Mathematics", 1, 1),
            record("MATH F101", "M.Sc. Mathematics", 1, 2),
            record("BIO F101", "M.Sc. Mathematics", 2, 1),
            reference("M.Sc. Mathematics with B.E. Computer Science",
                      "M.Sc. Mathematics", [(1, 1), (1, 2)]),
        ])
        service = CourseSuggestionService(requirements, FakeCatalogue())
        result = service.suggest(
            ["B.E. Computer Science", "M.Sc. Mathematics"], 2, 2)
        self.assertEqual([item["course_code"] for item in result["suggestions"]],
                         ["CS F111", "MATH F101"])
        self.assertTrue(all(item["curriculum_source_programme"] ==
                            "M.Sc. Mathematics" for item in result["suggestions"]))

    def test_ambiguous_composite_reference_is_not_followed(self):
        requirements = dataset([
            record("CS F111", "M.Sc. Mathematics", 1, 1),
            reference("M.Sc. Mathematics with B.E. Computer Science",
                      None, [(1, 1)], True),
        ])
        result = CourseSuggestionService(requirements, FakeCatalogue()).suggest(
            ["B.E. Computer Science", "M.Sc. Mathematics"], 2, 1)
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["limitations"][0]["code"],
                         "ambiguous_curriculum_reference")

    def test_unverified_or_unstructured_records_are_never_suggested(self):
        service = CourseSuggestionService(dataset([
            record("CS F111", "B.E. Computer Science", 1, 1, True),
            record("CS F211", "B.E. Computer Science", None, None),
        ]), FakeCatalogue())
        result = service.suggest(["B.E. Computer Science"], 2, 1)
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["limitations"][0]["code"],
                         "semester_assignment_unavailable")

    def test_real_processed_data_returns_prior_semester_courses(self):
        result = CourseSuggestionService().suggest(
            ["M.Sc. Mathematics"], 2, 1)
        self.assertEqual(result["resolved_programmes"], ["M.Sc. Mathematics"])
        self.assertGreater(len(result["suggestions"]), 0)
        self.assertFalse(result["limitations"])
        self.assertTrue(all(
            (item["expected_year"], item["expected_semester"]) < (2, 1)
            for item in result["suggestions"]))
        self.assertIn("BITS F103",
                      {item["course_code"] for item in result["suggestions"]})

    def test_real_be_computer_science_year_two_semester_one(self):
        result = CourseSuggestionService().suggest(
            ["B. E. Computer Science"], 2, 1)
        self.assertEqual([item["course_code"] for item in result["suggestions"]], [
            "BIO F101", "BITS F101", "BITS F102", "BITS F103", "BITS F111",
            "BITS F112", "BITS K101", "CHEM F101", "CS F111", "EEE F111",
            "MATH F101", "MATH F102", "MATH F113", "PHY F101"])

    def test_real_msc_physics_year_two_semester_two(self):
        result = CourseSuggestionService().suggest(["M. Sc. Physics"], 2, 2)
        self.assertEqual([item["course_code"] for item in result["suggestions"]], [
            "BIO F101", "BITS F101", "BITS F102", "BITS F103", "BITS F111",
            "BITS F112", "BITS F225", "BITS K101", "CHEM F101", "CS F111",
            "EEE F111", "MATH F101", "MATH F102", "MATH F113", "MATH F211",
            "PHY F101", "PHY F211", "PHY F212", "PHY F213", "PHY F214"])

    def test_real_physics_computer_science_pair_uses_composite_chart(self):
        result = CourseSuggestionService().suggest(
            ["M. Sc. Physics", "B. E. Computer Science"], 4, 1)
        self.assertEqual(result["resolution_mode"], "composite_dual_degree_chart")
        self.assertEqual(result["resolved_programmes"], [
            "M.Sc. Physics with B.E. Computer Science"])
        self.assertEqual([item["course_code"] for item in result["suggestions"]], [
            "BIO F101", "BITS F101", "BITS F102", "BITS F103", "BITS F111",
            "BITS F112", "BITS K101", "CHEM F101", "CS F111", "CS F211",
            "CS F212", "CS F213", "CS F214", "CS F215", "CS F222", "CS F241",
            "EEE F111", "MATH F101", "MATH F102", "MATH F113", "MATH F211",
            "PHY F101", "PHY F211", "PHY F212", "PHY F213", "PHY F214",
            "PHY F241", "PHY F242", "PHY F243", "PHY F311", "PHY F312",
            "PHY F313", "PHY F341", "PHY F342", "PHY F343", "PHY F344"])

    def test_real_physics_computer_science_pair_inherits_fourteen_year_one_courses(self):
        result = CourseSuggestionService().suggest(
            ["B.E. Computer Science", "M.Sc. Physics"], 2, 1)
        self.assertEqual([item["course_code"] for item in result["suggestions"]], [
            "BIO F101", "BITS F101", "BITS F102", "BITS F103", "BITS F111",
            "BITS F112", "BITS K101", "CHEM F101", "CS F111", "EEE F111",
            "MATH F101", "MATH F102", "MATH F113", "PHY F101"])
        self.assertTrue(all(item["suggestion_state"] ==
                            "awaiting_student_confirmation"
                            for item in result["suggestions"]))
        self.assertTrue(all(item["curriculum_source_programme"] == "M. Sc. Physics"
                            for item in result["suggestions"]))

    def test_invalid_request_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "programmes"):
            CourseSuggestionService(dataset([]), FakeCatalogue()).suggest([], 1, 1)


class CourseSuggestionApiTests(unittest.TestCase):
    def test_api_returns_unconfirmed_suggestions(self):
        service = CourseSuggestionService(dataset([
            record("CS F111", "B.E. Computer Science", 1, 1),
        ]), FakeCatalogue())
        body = json.dumps({
            "programmes": ["B.E. Computer Science"],
            "current_academic_year": 1, "current_semester": 2,
        }).encode()
        environ = {"PATH_INFO": "/api/course-suggestions", "REQUEST_METHOD": "POST",
                   "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body)}
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        with patch("backend.api._course_suggestions", return_value=service):
            response = json.loads(b"".join(application(environ, start_response)))
        self.assertEqual(captured["status"], "200 OK")
        suggestion = response["course_suggestions"]["suggestions"][0]
        self.assertEqual(suggestion["suggestion_state"],
                         "awaiting_student_confirmation")

    def test_api_rejects_extra_fields(self):
        body = json.dumps({"programmes": ["X"], "current_academic_year": 1,
                           "current_semester": 1, "completed": True}).encode()
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        response = json.loads(b"".join(application({
            "PATH_INFO": "/api/course-suggestions", "REQUEST_METHOD": "POST",
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body)},
            start_response)))
        self.assertEqual(captured["status"], "400 Bad Request")
        self.assertEqual(response["error"], "invalid_course_suggestion_payload")


if __name__ == "__main__":
    unittest.main()
