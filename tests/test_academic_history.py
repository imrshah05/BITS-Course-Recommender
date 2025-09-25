import json
import unittest
from pathlib import Path

from backend.academic_history import CourseCatalogue, resolve_academic_history
from backend.student_profile import StudentProfile


def record(codes, title="Algorithms", units=4, source="course.pdf", *,
           identity_type=None, candidate_status="usable", valid=True, verify=False):
    return {
        "metadata": {
            "course_codes": codes,
            "course_identity_type": identity_type or ("single" if len(codes) == 1 else "multiple"),
            "course_title": {"value": title, "sources": []} if title is not None else None,
            "units": {"value": units, "sources": []} if units is not None else None,
        },
        "source": {"source_file": source, "page_numbers": [1]},
        "candidate_status": candidate_status,
        "validation": {"is_valid": valid, "needs_verification": verify, "issues": []},
    }


def profile(completed=None, ongoing=None):
    return StudentProfile.from_dict({
        "programme": "B.E. Computer Science",
        "current_academic_year": 2,
        "current_semester": 1,
        "completed_courses": completed or [],
        "ongoing_courses": ongoing or [],
    })


def resolve(records, completed=None, ongoing=None):
    return resolve_academic_history(profile(completed, ongoing),
                                    CourseCatalogue.from_dict({"records": records}))


def issue_codes(history):
    return [issue["code"] for issue in history["validation"]["issues"]]


class AcademicHistoryTests(unittest.TestCase):
    def test_completed_course_resolves(self):
        history = resolve([record(["CS F211"])], completed=["CS F211"])
        self.assertEqual(history["completed_courses"][0]["resolution_status"], "matched")

    def test_ongoing_course_resolves_and_lists_remain_separate(self):
        history = resolve([record(["CS F211"]), record(["MATH F101"], source="math.pdf")],
                          completed=["CS F211"], ongoing=["MATH F101"])
        self.assertEqual(history["completed_courses"][0]["normalized_course_code"], "CS F211")
        self.assertEqual(history["ongoing_courses"][0]["normalized_course_code"], "MATH F101")
        self.assertEqual(history["ongoing_courses"][0]["status"], "ongoing")

    def test_title_and_catalogue_units_are_attached(self):
        history = resolve([record(["CS F211"], "Data Structures", 4)], completed=["CS F211"])
        match = history["completed_courses"][0]["catalogue_matches"][0]
        self.assertEqual((match["title"], match["catalogue_units"]), ("Data Structures", 4))

    def test_student_grade_and_units_are_preserved(self):
        course = {"course_code": "CS F211", "grade": "A", "units": 4}
        history = resolve([record(["CS F211"])], completed=[course])
        entry = history["completed_courses"][0]
        self.assertEqual((entry["grade"], entry["student_units"]), ("A", 4))

    def test_match_through_shared_identity(self):
        history = resolve([record(["BITS F415", "BITS U415"], source="shared.pdf")],
                          completed=["BITS U415"])
        match = history["completed_courses"][0]["catalogue_matches"][0]
        self.assertEqual(match["matched_course_code"], "BITS U415")
        self.assertEqual(match["course_identity_type"], "multiple")

    def test_shared_handout_does_not_create_equivalence(self):
        catalogue = CourseCatalogue.from_dict({"records": [
            record(["BITS F415", "BITS U415"], source="shared.pdf")
        ]})
        f_match = resolve_academic_history(profile(completed=["BITS F415"]), catalogue)
        u_match = resolve_academic_history(profile(completed=["BITS U415"]), catalogue)
        self.assertEqual(f_match["completed_courses"][0]["normalized_course_code"], "BITS F415")
        self.assertEqual(u_match["completed_courses"][0]["normalized_course_code"], "BITS U415")

    def test_valid_course_absent_from_catalogue_is_retained(self):
        history = resolve([], completed=["BIO F999"])
        entry = history["completed_courses"][0]
        self.assertEqual(entry["resolution_status"], "unmatched")
        self.assertEqual(entry["normalized_course_code"], "BIO F999")
        self.assertIn("catalogue_match_missing", issue_codes(history))
        self.assertTrue(history["validation"]["is_valid"])

    def test_multiple_catalogue_records_are_all_preserved(self):
        records = [record(["CS F211"], source="one.pdf"),
                   record(["CS F211"], source="two.pdf")]
        history = resolve(records, completed=["CS F211"])
        self.assertEqual(len(history["completed_courses"][0]["catalogue_matches"]), 2)
        self.assertIn("multiple_catalogue_matches", issue_codes(history))

    def test_conflicting_catalogue_metadata_warns(self):
        records = [record(["CS F211"], "Algorithms", 4, "one.pdf"),
                   record(["CS F211"], "Different title", 3, "two.pdf")]
        history = resolve(records, completed=["CS F211"])
        self.assertIn("conflicting_catalogue_metadata", issue_codes(history))

    def test_student_catalogue_units_conflict_warns(self):
        history = resolve([record(["CS F211"], units=4)],
                          completed=[{"course_code": "CS F211", "units": 3}])
        self.assertIn("student_catalogue_units_conflict", issue_codes(history))
        self.assertEqual(history["completed_courses"][0]["student_units"], 3)
        self.assertEqual(history["completed_courses"][0]["catalogue_matches"][0]["catalogue_units"], 4)

    def test_catalogue_record_requiring_verification_warns(self):
        history = resolve([record(["CS F211"], verify=True)], completed=["CS F211"])
        self.assertIn("catalogue_record_needs_verification", issue_codes(history))

    def test_unusable_identity_is_not_matched(self):
        unusable = record(["CS F211"], candidate_status="unusable_identity", valid=False, verify=True)
        history = resolve([unusable], completed=["CS F211"])
        self.assertEqual(history["completed_courses"][0]["resolution_status"], "unmatched")
        self.assertIn("unusable_catalogue_identity_ignored", issue_codes(history))

    def test_normalized_output_is_json_serializable(self):
        history = resolve([record(["CS F211"])], completed=["csf211"])
        self.assertEqual(json.loads(json.dumps(history)), history)
        self.assertEqual(history["completed_courses"][0]["normalized_course_code"], "CS F211")

    def test_real_processed_catalogue(self):
        path = Path(__file__).resolve().parents[1] / "data/processed/courses.json"
        catalogue = CourseCatalogue.load(path)
        history = resolve_academic_history(profile(completed=["AN F314", "BITS U415"]), catalogue)
        self.assertEqual([entry["resolution_status"] for entry in history["completed_courses"]],
                         ["matched", "matched"])
        self.assertEqual(history["completed_courses"][0]["catalogue_matches"][0]["title"],
                         "Introduction to Flight")
        self.assertEqual(history["completed_courses"][1]["catalogue_matches"][0]["course_identity_type"],
                         "multiple")


if __name__ == "__main__":
    unittest.main()
