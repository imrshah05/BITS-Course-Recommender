import json
import unittest

from backend.course_catalogue import CourseCatalogue
from backend.eligibility import EligibilityEngine


def prerequisite(kind="none", codes=None, operator="all", verify=False):
    data = {
        "kind": kind,
        "needs_verification": verify,
        "sources": [{"source_file": "course.pdf", "page_number": 1,
                     "text": "Prerequisite evidence"}],
    }
    if kind == "courses":
        data.update({"operator": operator, "course_codes": codes or []})
    if kind == "unresolved":
        data.update({"text": "Knowledge of algorithms", "diagnostics": ["free_form"],
                     "machine_evaluable": False})
    return data


def record(code, prereq=None, source=None, identities=None):
    identities = identities or [code]
    source = source or code.replace(" ", "_") + ".pdf"
    return {
        "candidate_status": "usable",
        "metadata": {
            "course_code": {"value": " / ".join(identities), "sources": []},
            "course_codes": identities,
            "course_identity_type": "single" if len(identities) == 1 else "multiple",
            "course_title": {"value": "Course", "sources": []},
            "department_division": {"value": "Department", "sources": []},
            "units": {"value": 4, "sources": []},
        },
        "prerequisites": prereq,
        "instructors": [], "syllabus": [], "evaluation": [],
        "exams": {"midsemester": None, "comprehensive": None},
        "attendance": [], "makeup": [], "observations": [],
        "source": {"source_file": source, "page_numbers": [1]},
        "validation": {"is_valid": True, "needs_verification": False, "issues": []},
    }


def course_entries(*codes, status="completed"):
    return [{"normalized_course_code": code, "status": status,
             "resolution_status": "matched", "catalogue_matches": []}
            for code in codes]


def history(completed=(), ongoing=(), valid=True):
    return {
        "programme": "COMPUTER SCIENCE",
        "completed_courses": course_entries(*completed),
        "ongoing_courses": course_entries(*ongoing, status="ongoing"),
        "profile_validation": {"is_valid": valid, "issues": []},
        "validation": {"is_valid": valid, "issues": []},
    }


def engine(*records):
    return EligibilityEngine(CourseCatalogue(list(records)))


def restriction(state, code="restriction_state"):
    return [{"restriction_id": "restriction-1", "state": state,
             "reason_code": code, "message": f"Restriction is {state}",
             "sources": [{"source_file": "rules.json", "page_number": 2}]}]


class EligibilityEngineTests(unittest.TestCase):
    def test_satisfied_prerequisite_is_eligible(self):
        result = engine(record("CS F214", prerequisite(
            "courses", ["CS F213"]))).evaluate("CS F214", history(["CS F213"]))
        self.assertEqual(result["eligibility_state"], "eligible")

    def test_unmet_prerequisite_is_ineligible(self):
        result = engine(record("CS F214", prerequisite(
            "courses", ["CS F213"]))).evaluate("CS F214", history())
        self.assertEqual(result["eligibility_state"], "ineligible")
        self.assertIn("prerequisite_not_satisfied", result["reason_codes"])

    def test_explicit_no_prerequisite_is_eligible(self):
        result = engine(record("CS F214", prerequisite())).evaluate("CS F214", history())
        self.assertEqual(result["eligibility_state"], "eligible")

    def test_unknown_prerequisite_is_not_eligible(self):
        result = engine(record("CS F214")).evaluate("CS F214", history())
        self.assertEqual(result["eligibility_state"], "unknown")
        self.assertIn("prerequisite_unknown", result["reason_codes"])

    def test_ambiguous_prerequisite_is_not_eligible(self):
        result = engine(record("CS F214", prerequisite("unresolved", verify=True))).evaluate(
            "CS F214", history())
        self.assertEqual(result["eligibility_state"], "ambiguous")

    def test_ongoing_prerequisite_does_not_satisfy(self):
        result = engine(record("CS F214", prerequisite(
            "courses", ["CS F213"]))).evaluate(
                "CS F214", history(ongoing=["CS F213"]))
        self.assertEqual(result["eligibility_state"], "ineligible")
        self.assertEqual(result["prerequisite_evaluation"]["ongoing_matches"], ["CS F213"])

    def test_completed_prerequisite_does_satisfy(self):
        result = engine(record("CS F214", prerequisite(
            "courses", ["CS F213"]))).evaluate(
                "CS F214", history(completed=["CS F213"]))
        self.assertEqual(result["prerequisite_evaluation"]["completed_matches"], ["CS F213"])
        self.assertEqual(result["eligibility_state"], "eligible")

    def test_completed_target_is_excluded(self):
        result = engine(record("CS F214", prerequisite())).evaluate(
            "CS F214", history(completed=["CS F214"]))
        self.assertEqual(result["eligibility_state"], "ineligible")
        self.assertTrue(result["already_completed"])
        self.assertIn("target_already_completed", result["reason_codes"])

    def test_ongoing_target_is_excluded(self):
        result = engine(record("CS F214", prerequisite())).evaluate(
            "CS F214", history(ongoing=["CS F214"]))
        self.assertEqual(result["eligibility_state"], "ineligible")
        self.assertTrue(result["already_ongoing"])

    def test_known_disqualifier_overrides_unknown_prerequisite(self):
        result = engine(record("CS F214")).evaluate(
            "CS F214", history(), restriction("failed", "programme_excluded"))
        self.assertEqual(result["eligibility_state"], "ineligible")
        self.assertIn("programme_excluded", result["reason_codes"])
        self.assertTrue(result["unknown_conditions"])

    def test_ambiguous_restriction_prevents_eligibility(self):
        result = engine(record("CS F214", prerequisite())).evaluate(
            "CS F214", history(), restriction("ambiguous", "restriction_conflict"))
        self.assertEqual(result["eligibility_state"], "ambiguous")
        self.assertIn("restriction_conflict", result["reason_codes"])

    def test_unresolved_candidate_identity_is_unknown(self):
        result = engine(record("CS F214")).evaluate("BIO F999", history())
        self.assertEqual(result["eligibility_state"], "unknown")
        self.assertEqual(result["candidate_resolution_status"], "unknown")

    def test_invalid_student_history_is_ambiguous(self):
        result = engine(record("CS F214", prerequisite())).evaluate(
            "CS F214", history(valid=False))
        self.assertEqual(result["eligibility_state"], "ambiguous")
        self.assertFalse(result["validation"]["is_valid"])

    def test_multiple_compatible_handouts_do_not_block(self):
        result = engine(record("CS F214", prerequisite(), "one.pdf"),
                        record("CS F214", prerequisite(), "two.pdf")).evaluate(
                            "CS F214", history())
        self.assertEqual(result["eligibility_state"], "eligible")
        self.assertEqual(len(result["candidate_sources"]), 2)

    def test_prerequisite_source_evidence_survives(self):
        result = engine(record("CS F214", prerequisite(
            "courses", ["CS F213"]))).evaluate("CS F214", history())
        failed = next(item for item in result["failed_conditions"]
                      if item["condition"] == "prerequisite")
        self.assertTrue(any(source.get("text") == "Prerequisite evidence"
                            for source in failed["sources"]))

    def test_batch_preserves_all_four_groups(self):
        catalogue = CourseCatalogue([
            record("CS F211", prerequisite()),
            record("CS F212", prerequisite("courses", ["CS F111"])),
            record("CS F213"),
            record("CS F214", prerequisite("unresolved", verify=True)),
        ])
        result = EligibilityEngine(catalogue).evaluate_all(history())
        self.assertEqual({state: len(items) for state, items in result["groups"].items()},
                         {"eligible": 1, "ineligible": 1, "unknown": 1, "ambiguous": 1})
        self.assertEqual(result["summary"]["missing_prerequisite_became_eligible_count"], 0)

    def test_restriction_sources_survive(self):
        result = engine(record("CS F214")).evaluate(
            "CS F214", history(), restriction("failed"))
        check = result["deterministic_restrictions_checked"][0]
        self.assertEqual(check["sources"][0]["source_file"], "rules.json")

    def test_output_is_json_serializable(self):
        result = engine(record("CS F214", prerequisite())).evaluate("CS F214", history())
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
