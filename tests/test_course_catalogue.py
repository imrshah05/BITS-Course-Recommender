import json
import unittest
from pathlib import Path

from backend.course_catalogue import CourseCatalogue


def field(value, source="course.pdf"):
    if value is None:
        return None
    return {"value": value, "sources": [{"source_file": source,
                                           "page_number": 1, "text": str(value)}]}


def record(codes, source="course.pdf", title="Course", department="Department",
           units=4, status="usable", identity_type=None, verify=False):
    identity_type = identity_type or (
        "unresolved" if not codes else "single" if len(codes) == 1 else "multiple")
    raw_code = " / ".join(codes) if codes else None
    return {
        "candidate_status": status,
        "metadata": {
            "course_code": field(raw_code, source),
            "course_codes": codes,
            "course_identity_type": identity_type,
            "course_title": field(title, source),
            "department_division": field(department, source),
            "units": field(units, source),
        },
        "instructors": [{"names": ["Instructor"], "sources": []}],
        "syllabus": [{"text": "Topics", "sources": []}],
        "evaluation": [{"name": "Quiz", "weightage": "10%", "sources": []}],
        "exams": {"midsemester": None, "comprehensive": None},
        "attendance": [],
        "makeup": [],
        "observations": [],
        "source": {"source_file": source, "page_numbers": [1, 2]},
        "validation": {"is_valid": not verify, "needs_verification": verify,
                       "issues": []},
    }


def issue_codes(catalogue):
    return [issue["code"] for issue in catalogue.validation["issues"]]


class CourseCatalogueTests(unittest.TestCase):
    def test_loads_simple_catalogue(self):
        catalogue = CourseCatalogue.from_dict({"records": [record(["CS F211"])]})
        self.assertEqual(len(catalogue), 1)
        self.assertEqual(catalogue.source_record_count, 1)

    def test_single_identity_candidate(self):
        candidate = CourseCatalogue([record(["CS F211"])]).get("CS F211")
        self.assertEqual(candidate["normalized_course_code"], "CS F211")
        self.assertEqual(candidate["source_records"][0]["course_identity_type"], "single")

    def test_shared_explicit_identities_are_each_discoverable(self):
        catalogue = CourseCatalogue([record(
            ["BITS F415", "BITS U415"], identity_type="multiple")])
        self.assertTrue(catalogue.contains("BITS F415"))
        self.assertTrue(catalogue.contains("BITS U415"))
        self.assertEqual(len(catalogue.shared_source_records), 1)
        self.assertEqual(catalogue.get("BITS F415")["normalized_course_code"], "BITS F415")
        self.assertEqual(catalogue.get("BITS U415")["normalized_course_code"], "BITS U415")

    def test_multiple_source_records_are_preserved(self):
        catalogue = CourseCatalogue([
            record(["CS F211"], "one.pdf"), record(["CS F211"], "two.pdf")])
        candidate = catalogue.get("CS F211")
        self.assertEqual(candidate["source_record_count"], 2)
        self.assertTrue(candidate["has_multiple_source_records"])
        self.assertIn("multiple_source_records", issue_codes(catalogue))
        self.assertIn("duplicate_normalized_identity", issue_codes(catalogue))

    def test_lookup_normalizes_only_supported_course_syntax(self):
        catalogue = CourseCatalogue([record(["CS F211"])])
        self.assertIsNotNone(catalogue.get(" csf211 "))
        self.assertIsNone(catalogue.get("CS F21"))

    def test_unknown_lookup(self):
        catalogue = CourseCatalogue([record(["CS F211"])])
        self.assertFalse(catalogue.contains("BIO F999"))
        self.assertEqual(catalogue.source_records_for("BIO F999"), [])

    def test_unusable_identity_is_excluded(self):
        catalogue = CourseCatalogue([record([], status="unusable_identity")])
        self.assertEqual(len(catalogue), 0)
        self.assertEqual(len(catalogue.excluded_records), 1)
        self.assertEqual(catalogue.excluded_records[0]["reason"], "unusable_course_identity")

    def test_verification_state_is_preserved(self):
        catalogue = CourseCatalogue([record(["CS F211"], verify=True)])
        candidate = catalogue.get("CS F211")
        self.assertTrue(candidate["needs_verification"])
        self.assertEqual(len(catalogue.verification_required_records), 1)

    def test_source_traceability_and_metadata_are_preserved(self):
        candidate = CourseCatalogue([record(["CS F211"], "trace.pdf")]).get("CS F211")
        source = candidate["source_records"][0]
        self.assertEqual(source["source"]["source_file"], "trace.pdf")
        self.assertEqual(source["course_title"]["sources"][0]["page_number"], 1)
        self.assertEqual(source["instructors"][0]["names"], ["Instructor"])

    def test_conflicting_metadata_remains_separate(self):
        catalogue = CourseCatalogue([
            record(["CS F211"], "one.pdf", title="Data Structures", units=4),
            record(["CS F211"], "two.pdf", title="Different Title", units=3),
        ])
        candidate = catalogue.get("CS F211")
        self.assertEqual(candidate["metadata_conflicts"]["course_title"],
                         ["Data Structures", "Different Title"])
        self.assertEqual(candidate["metadata_conflicts"]["units"], [3, 4])
        self.assertEqual(len(candidate["source_records"]), 2)
        self.assertIn("conflicting_source_metadata", issue_codes(catalogue))

    def test_missing_optional_metadata_is_nonfatal(self):
        catalogue = CourseCatalogue([
            record(["CS F211"], title=None, department=None, units=None)])
        self.assertTrue(catalogue.validation["is_valid"])
        self.assertIn("missing_optional_metadata", issue_codes(catalogue))

    def test_deterministic_ordering(self):
        catalogue = CourseCatalogue([
            record(["MATH F101"], "z.pdf"),
            record(["CS F211"], "b.pdf"),
            record(["CS F211"], "a.pdf"),
        ])
        self.assertEqual(catalogue.course_codes(), ["CS F211", "MATH F101"])
        sources = [item["source"]["source_file"]
                   for item in catalogue.source_records_for("CS F211")]
        self.assertEqual(sources, ["a.pdf", "b.pdf"])

    def test_malformed_source_record_is_reported_and_excluded(self):
        catalogue = CourseCatalogue(["bad", {"candidate_status": "usable"}])
        self.assertEqual(len(catalogue.excluded_records), 2)
        self.assertFalse(catalogue.validation["is_valid"])
        self.assertIn("malformed_record", issue_codes(catalogue))
        self.assertIn("malformed_metadata", issue_codes(catalogue))

    def test_malformed_dataset_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            CourseCatalogue.from_dict({"courses": []})

    def test_output_is_json_serializable(self):
        catalogue = CourseCatalogue([record(["CS F211"])])
        payload = {"courses": catalogue.list_courses(),
                   "excluded": catalogue.excluded_records,
                   "validation": catalogue.validation}
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_real_processed_catalogue(self):
        root = Path(__file__).resolve().parents[1]
        dataset_path = root / "data/processed/courses.json"
        with dataset_path.open(encoding="utf-8") as stream:
            source_count = len(json.load(stream)["records"])
        catalogue = CourseCatalogue.load(dataset_path)

        self.assertEqual(source_count, 540)
        self.assertEqual(catalogue.source_record_count, 540)
        self.assertEqual(len(catalogue.excluded_records), 9)
        self.assertEqual(len(catalogue.shared_source_records), 82)
        self.assertTrue(catalogue.course_codes())
        self.assertTrue(all(course["source_records"] for course in catalogue))
        shared = catalogue.shared_source_records[0]
        self.assertGreater(len(shared["explicit_course_codes"]), 1)
        for code in shared["explicit_course_codes"]:
            self.assertTrue(catalogue.contains(code))
            self.assertEqual(catalogue.get(code)["normalized_course_code"], code)


if __name__ == "__main__":
    unittest.main()
