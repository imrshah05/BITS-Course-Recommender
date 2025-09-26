import json
import unittest

from backend.course_catalogue import CourseCatalogue
from backend.prerequisites import PrerequisiteEngine


def record(codes, source="course.pdf", prerequisites=None, status="usable",
           identity_type=None, record_verify=False):
    identity_type = identity_type or (
        "unresolved" if not codes else "single" if len(codes) == 1 else "multiple")
    return {
        "candidate_status": status,
        "metadata": {
            "course_code": {"value": " / ".join(codes), "sources": []} if codes else None,
            "course_codes": codes,
            "course_identity_type": identity_type,
            "course_title": {"value": "Course", "sources": []},
            "department_division": {"value": "Department", "sources": []},
            "units": {"value": 4, "sources": []},
        },
        "prerequisites": prerequisites,
        "instructors": [], "syllabus": [], "evaluation": [],
        "exams": {"midsemester": None, "comprehensive": None},
        "attendance": [], "makeup": [], "observations": [],
        "source": {"source_file": source, "page_numbers": [1, 2]},
        "validation": {"is_valid": not record_verify,
                       "needs_verification": record_verify, "issues": []},
    }


def courses(*codes, status="completed"):
    return [{"normalized_course_code": code, "status": status} for code in codes]


def history(completed=(), ongoing=()):
    return {"completed_courses": courses(*completed),
            "ongoing_courses": courses(*ongoing, status="ongoing")}


def requirement(operator, *codes, verify=False, source="target.pdf"):
    return {
        "kind": "courses",
        "operator": operator,
        "course_codes": list(codes),
        "needs_verification": verify,
        "sources": [{"source_file": source, "page_number": 1,
                     "text": f"Prerequisite: {' / '.join(codes)}"}],
    }


def engine(*records):
    return PrerequisiteEngine(CourseCatalogue(list(records)))


def issue_codes(result):
    return [issue["code"] for issue in result["validation"]["issues"]]


class PrerequisiteEngineTests(unittest.TestCase):
    def test_one_completed_prerequisite_is_satisfied(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213"))).evaluate("CS F214", history(["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "satisfied")
        self.assertEqual(result["completed_matches"], ["CS F213"])

    def test_missing_completed_prerequisite_is_not_satisfied(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213"))).evaluate("CS F214", history())
        self.assertEqual(result["prerequisite_state"], "not_satisfied")
        self.assertEqual(result["unmet_prerequisites"], ["CS F213"])

    def test_ongoing_prerequisite_does_not_satisfy(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213"))).evaluate("CS F214", history(ongoing=["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "not_satisfied")
        self.assertEqual(result["ongoing_matches"], ["CS F213"])

    def test_explicit_no_prerequisite(self):
        none = {"kind": "none", "needs_verification": False,
                "sources": [{"source_file": "target.pdf", "page_number": 1,
                             "text": "Prerequisite: None"}]}
        result = engine(record(["CS F214"], prerequisites=none)).evaluate(
            "CS F214", history())
        self.assertEqual(result["prerequisite_state"], "no_explicit_prerequisite")

    def test_missing_information_is_unknown(self):
        result = engine(record(["CS F214"])).evaluate("CS F214", history())
        self.assertEqual(result["prerequisite_state"], "unknown")
        self.assertIn("prerequisite_information_absent", issue_codes(result))

    def test_prerequisite_specific_verification_is_ambiguous(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213", verify=True))).evaluate(
                "CS F214", history(["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "ambiguous")
        self.assertIn("prerequisite_evidence_needs_verification", issue_codes(result))

    def test_unrelated_record_verification_does_not_taint_prerequisite(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213"), record_verify=True)).evaluate(
                "CS F214", history(["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "satisfied")

    def test_conflicting_source_records_are_ambiguous(self):
        result = engine(
            record(["CS F214"], "one.pdf", requirement("all", "CS F213")),
            record(["CS F214"], "two.pdf", requirement("all", "MATH F101")),
        ).evaluate("CS F214", history(["CS F213", "MATH F101"]))
        self.assertEqual(result["prerequisite_state"], "ambiguous")
        self.assertIn("conflicting_prerequisite_information", issue_codes(result))

    def test_identical_source_records_are_compatible(self):
        result = engine(
            record(["CS F214"], "one.pdf", requirement("all", "CS F213")),
            record(["CS F214"], "two.pdf", requirement("all", "CS F213")),
        ).evaluate("CS F214", history(["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "satisfied")
        self.assertEqual(len(result["prerequisite_requirements"]["evidence"]), 4)

    def test_explicit_all_requires_every_course(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "MATH F101", "CS F213"))).evaluate(
                "CS F214", history(["CS F213"]))
        self.assertEqual(result["prerequisite_state"], "not_satisfied")
        self.assertEqual(result["unmet_prerequisites"], ["MATH F101"])

    def test_explicit_any_accepts_one_course(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "any", "MATH F101", "CS F213"))).evaluate(
                "CS F214", history(["MATH F101"]))
        self.assertEqual(result["prerequisite_state"], "satisfied")
        self.assertEqual(result["completed_matches"], ["MATH F101"])

    def test_malformed_structure_is_ambiguous(self):
        malformed = {"kind": "courses", "course_codes": ["CS F213"]}
        result = engine(record(["CS F214"], prerequisites=malformed)).evaluate(
            "CS F214", history())
        self.assertEqual(result["prerequisite_state"], "ambiguous")
        self.assertIn("malformed_prerequisite_structure", issue_codes(result))

    def test_unknown_target_course(self):
        result = engine(record(["CS F214"])).evaluate("BIO F999", history())
        self.assertEqual(result["target_resolution_status"], "unknown")
        self.assertEqual(result["prerequisite_state"], "unknown")

    def test_unusable_target_identity(self):
        unusable = record(["CS F214"], status="unusable_identity")
        result = engine(unusable).evaluate("CS F214", history())
        self.assertEqual(result["target_resolution_status"], "unusable_identity")
        self.assertIn("unusable_target_identity", issue_codes(result))

    def test_exact_normalized_identity_matching(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "CS F213"))).evaluate("csf214", history(["csf213"]))
        self.assertEqual(result["prerequisite_state"], "satisfied")

    def test_shared_handout_identity_is_not_equivalence(self):
        target = record(["BITS U416"], prerequisites=requirement("all", "BITS U415"))
        shared = record(["BITS F415", "BITS U415"], identity_type="multiple")
        result = engine(target, shared).evaluate("BITS U416", history(["BITS F415"]))
        self.assertEqual(result["prerequisite_state"], "not_satisfied")
        self.assertEqual(result["unmet_prerequisites"], ["BITS U415"])

    def test_source_evidence_is_preserved(self):
        result = engine(record(["CS F214"], "target.pdf", requirement(
            "all", "CS F213"))).evaluate("CS F214", history())
        evidence = result["prerequisite_requirements"]["evidence"]
        self.assertTrue(any(item.get("text", "").startswith("Prerequisite")
                            for item in evidence))
        self.assertTrue(any(item.get("record_id") == "target.pdf" for item in evidence))

    def test_deterministic_prerequisite_ordering(self):
        result = engine(record(["CS F214"], prerequisites=requirement(
            "all", "MATH F101", "CS F213"))).evaluate("CS F214", history())
        self.assertEqual(result["prerequisite_requirements"]["course_codes"],
                         ["CS F213", "MATH F101"])
        self.assertEqual(result["unmet_prerequisites"], ["CS F213", "MATH F101"])

    def test_result_is_json_serializable(self):
        result = engine(record(["CS F214"])).evaluate("CS F214", history())
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_real_catalogue_preserves_conservative_prerequisite_states(self):
        catalogue = CourseCatalogue.load()
        analysis = PrerequisiteEngine(catalogue).analyse_catalogue()
        self.assertEqual(analysis["total_candidate_identities"], 442)
        self.assertEqual(analysis["machine_evaluable_count"], 0)
        self.assertEqual(analysis["explicit_no_prerequisite_count"], 3)
        self.assertEqual(analysis["unknown_count"], 432)
        self.assertEqual(analysis["ambiguous_count"], 7)
        self.assertEqual(analysis["prerequisite_bearing_verification_required_count"], 7)


if __name__ == "__main__":
    unittest.main()
