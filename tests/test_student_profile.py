import json
import unittest

from backend.student_profile import StudentProfile, normalize_student_profile


KNOWN = {"CS F211", "MATH F101", "BITS U103"}


def minimal(**updates):
    profile = {
        "programme": "B.E. Computer Science",
        "current_academic_year": 2,
        "current_semester": 1,
        "completed_courses": [],
        "ongoing_courses": [],
    }
    profile.update(updates)
    return profile


def issue_codes(profile):
    return [issue["code"] for issue in profile["validation"]["issues"]]


class StudentProfileTests(unittest.TestCase):
    def test_valid_minimal_profile(self):
        profile = normalize_student_profile(minimal(), KNOWN)
        self.assertTrue(profile["validation"]["is_valid"])
        self.assertIsNone(profile["second_programme"])

    def test_valid_full_profile(self):
        profile = normalize_student_profile(minimal(
            second_programme="M.Sc. Mathematics",
            completed_courses=[{"course_code": "CS F211", "grade": "a-", "units": 4}],
            ongoing_courses=[{"course_code": "MATH F101", "units": 3}],
        ), KNOWN)
        self.assertTrue(profile["validation"]["is_valid"])
        self.assertEqual(profile["completed_courses"][0]["grade"], "A-")
        self.assertEqual(profile["completed_courses"][0]["status"], "completed")

    def test_harmless_course_code_formatting_is_normalized(self):
        profile = normalize_student_profile(minimal(completed_courses=["  csf211  "]), KNOWN)
        entry = profile["completed_courses"][0]
        self.assertEqual(entry["normalized_course_code"], "CS F211")
        self.assertEqual(entry["catalogue_status"], "known")

    def test_missing_programme(self):
        profile = normalize_student_profile(minimal(programme=""), KNOWN)
        self.assertIn("missing_programme", issue_codes(profile))
        self.assertFalse(profile["validation"]["is_valid"])

    def test_invalid_year(self):
        profile = normalize_student_profile(minimal(current_academic_year=0), KNOWN)
        self.assertIn("invalid_academic_year", issue_codes(profile))

    def test_invalid_semester(self):
        profile = normalize_student_profile(minimal(current_semester=3), KNOWN)
        self.assertIn("invalid_semester", issue_codes(profile))

    def test_malformed_course_code(self):
        profile = normalize_student_profile(minimal(completed_courses=["CS F21"]), KNOWN)
        self.assertIn("malformed_course_code", issue_codes(profile))
        self.assertEqual(profile["completed_courses"][0]["catalogue_status"], "malformed")

    def test_valid_unknown_course_is_warning(self):
        profile = normalize_student_profile(minimal(completed_courses=["BIO F999"]), KNOWN)
        self.assertTrue(profile["validation"]["is_valid"])
        self.assertIn("unknown_course_code", issue_codes(profile))
        self.assertEqual(profile["completed_courses"][0]["catalogue_status"], "unknown")

    def test_duplicate_completed_course(self):
        profile = normalize_student_profile(
            minimal(completed_courses=["CS F211", "csf211"]), KNOWN)
        self.assertIn("duplicate_course", issue_codes(profile))

    def test_duplicate_ongoing_course(self):
        profile = normalize_student_profile(
            minimal(ongoing_courses=["MATH F101", "math f101"]), KNOWN)
        self.assertIn("duplicate_course", issue_codes(profile))

    def test_course_cannot_be_completed_and_ongoing(self):
        profile = normalize_student_profile(
            minimal(completed_courses=["CS F211"], ongoing_courses=["cs f211"]), KNOWN)
        self.assertIn("incompatible_course_status", issue_codes(profile))

    def test_missing_optional_grade_and_units(self):
        profile = normalize_student_profile(minimal(completed_courses=["CS F211"]), KNOWN)
        entry = profile["completed_courses"][0]
        self.assertIsNone(entry["grade"])
        self.assertIsNone(entry["units"])
        self.assertTrue(profile["validation"]["is_valid"])

    def test_malformed_grade(self):
        profile = normalize_student_profile(
            minimal(completed_courses=[{"course_code": "CS F211", "grade": 10}]), KNOWN)
        self.assertIn("malformed_grade", issue_codes(profile))

    def test_malformed_units(self):
        profile = normalize_student_profile(
            minimal(completed_courses=[{"course_code": "CS F211", "units": -1}]), KNOWN)
        self.assertIn("malformed_units", issue_codes(profile))

    def test_status_mismatch(self):
        profile = normalize_student_profile(
            minimal(ongoing_courses=[{"course_code": "CS F211", "status": "completed"}]), KNOWN)
        self.assertIn("incompatible_course_status", issue_codes(profile))

    def test_serialization_and_model_interface(self):
        model = StudentProfile.from_dict(minimal(current_academic_year="2", current_semester="1"), KNOWN)
        output = model.to_dict()
        self.assertEqual((output["current_academic_year"], output["current_semester"]), (2, 1))
        self.assertEqual(json.loads(json.dumps(output)), output)

    def test_catalogue_check_can_be_deferred(self):
        profile = normalize_student_profile(minimal(completed_courses=["CS F211"]))
        self.assertEqual(profile["completed_courses"][0]["catalogue_status"], "not_checked")
        self.assertTrue(profile["validation"]["is_valid"])


if __name__ == "__main__":
    unittest.main()
