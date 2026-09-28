import json
import unittest
from copy import deepcopy

from backend.course_catalogue import CourseCatalogue
from backend.course_policy import CoursePolicyService, validate_course_policy_result


def record(code, prerequisite="none", source=None):
    if prerequisite is None:
        prerequisite_data = None
    else:
        prerequisite_data = {
            "kind": prerequisite,
            "needs_verification": prerequisite == "unresolved",
            "text": "Free-form prerequisite" if prerequisite == "unresolved" else None,
            "diagnostics": ["free_form"] if prerequisite == "unresolved" else [],
            "machine_evaluable": prerequisite != "unresolved",
            "sources": [{"source_file": "course.pdf", "page_number": 1}],
        }
    return {
        "candidate_status": "usable",
        "metadata": {
            "course_code": {"value": code, "sources": []},
            "course_codes": [code], "course_identity_type": "single",
            "course_title": {"value": "Course", "sources": []},
            "department_division": {"value": "Department", "sources": []},
            "units": {"value": 4, "sources": []},
        },
        "prerequisites": prerequisite_data,
        "instructors": [], "syllabus": [], "evaluation": [],
        "exams": {"midsemester": None, "comprehensive": None},
        "attendance": [], "makeup": [], "observations": [],
        "source": {"source_file": source or code.replace(" ", "_") + ".pdf",
                   "page_numbers": [1]},
        "validation": {"is_valid": True, "needs_verification": False, "issues": []},
    }


def history_entry(code, status):
    return {"normalized_course_code": code, "status": status,
            "resolution_status": "matched", "catalogue_matches": []}


def requirement_record(code, rule, state):
    return {"rule_id": rule, "rule_type": "required_course",
            "completion_status": state, "course_code": code,
            "alternatives": None,
            "sources": [{"source_file": "rules.json", "page_number": 10}]}


def academic_summary(completed=(), ongoing=(), requirements=(), status="complete",
                     incomplete=False):
    programmes = {}
    collections = {"satisfied": "satisfied_requirements",
                   "partially_satisfied": "partially_satisfied_requirements",
                   "remaining": "remaining_requirements",
                   "unevaluable": "unevaluable_requirements"}
    for code, rule, state, programme, category in requirements:
        key = (programme, category)
        group = programmes.setdefault(key, {name: [] for name in collections.values()})
        group[collections[state]].append(requirement_record(code, rule, state))
    programme_groups = {}
    for (programme, category), values in programmes.items():
        group = programme_groups.setdefault(programme, {
            "requested_programme_role": "primary", "programme": programme,
            "categories": [], "uncategorized_requirements": []})
        group["categories"].append({"normalized_category": category, **values})
    return {
        "status": status,
        "student": {"programme": "COMPUTER SCIENCE", "second_programme": None,
                    "current_academic_year": 2, "current_semester": 1},
        "academic_history": {
            "completed_courses": [history_entry(code, "completed") for code in completed],
            "ongoing_courses": [history_entry(code, "ongoing") for code in ongoing],
            "unresolved_courses": [],
            "profile_validation": {"is_valid": True, "issues": []},
            "validation": {"is_valid": True, "issues": []},
        },
        "requirement_progress": {"programme_progress": list(programme_groups.values()),
                                 "unresolved_requirements": [],
                                 "duplicate_requirements": []},
        "programme_resolution": [], "descriptive_information": [],
        "incomplete_data": {"has_incomplete_data": incomplete},
        "summary": {},
        "validation": {"is_valid": status in ("complete", "complete_with_incomplete_data"),
                       "errors": [], "warnings": []},
    }


class StubAcademicService:
    def __init__(self, summary):
        self.summary = summary
        self.calls = 0

    def build_summary(self, raw_profile):
        self.calls += 1
        return deepcopy(self.summary)


def service(records, summary):
    catalogue = CourseCatalogue(records)
    academic = StubAcademicService(summary)
    return CoursePolicyService(catalogue, academic), academic


class CoursePolicyTests(unittest.TestCase):
    def test_elective_membership_uses_remaining_category_total(self):
        summary = academic_summary(requirements=[
            (None, "total", "remaining", "B. E. Computer Science",
             "discipline_elective")])
        summary["requirement_progress"]["programme_progress"][0]["categories"][0][
            "remaining_requirements"][0]["rule_type"] = "category_total"
        summary["descriptive_information"] = [{
            "rule_id": "membership", "rule_type": "elective_membership",
            "course_code": "CS F437", "normalized_category": "discipline_elective",
            "scope": {"programme": "B. E. Computer Science"},
            "sources": [{"source_file": "bulletin.pdf", "page_number": 318}],
        }]
        result = service([record("CS F437", None)], summary)[0].evaluate({})
        course = result["verification_required_candidates"][0]
        self.assertEqual(course["requirement_filter_state"],
                         "matches_remaining_requirement")
        self.assertEqual(course["requirement_matches"][0]["membership_rule_id"],
                         "membership")
        self.assertEqual(course["eligibility_state"], "unknown")
        self.assertFalse(course["recommendation_safe"])

    def test_normal_successful_orchestration(self):
        evaluator, academic = service([record("CS F211")], academic_summary())
        result = evaluator.evaluate({"programme": "COMPUTER SCIENCE"})
        self.assertEqual(academic.calls, 1)
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["summary"]["total_candidate_count"], 1)

    def test_confirmed_relevant_candidate_enters_phase_five_safe_input(self):
        summary = academic_summary(requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        self.assertEqual(result["phase5_input"]["recommendation_safe_candidates"][0]
                         ["normalized_course_code"], "CS F211")

    def test_eligible_irrelevant_candidate_is_not_safe(self):
        result = service([record("CS F211")], academic_summary())[0].evaluate({})
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_relevant_unknown_candidate_remains_verification_required(self):
        summary = academic_summary(requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211", None)], summary)[0].evaluate({})
        self.assertEqual(result["verification_required_candidates"][0]
                         ["eligibility_state"], "unknown")
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_ambiguous_candidate_remains_verification_required(self):
        result = service([record("CS F211", "unresolved")], academic_summary())[0].evaluate({})
        self.assertEqual(result["verification_required_candidates"][0]
                         ["eligibility_state"], "ambiguous")

    def test_ineligible_candidate_remains_excluded(self):
        summary = academic_summary(completed=["CS F211"])
        result = service([record("CS F211")], summary)[0].evaluate({})
        self.assertEqual(result["excluded_candidates"][0]["eligibility_state"], "ineligible")

    def test_completed_target_does_not_reenter_safe_pool(self):
        summary = academic_summary(completed=["CS F211"], requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_ongoing_target_does_not_reenter_safe_pool(self):
        summary = academic_summary(ongoing=["CS F211"], requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_multiple_programmes_remain_separate(self):
        summary = academic_summary(requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core"),
            ("CS F211", "r2", "remaining", "ECONOMICS", "discipline_elective")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        states = result["recommendation_safe_candidates"][0]["programme_requirement_states"]
        self.assertEqual({item["programme_scope"] for item in states},
                         {"COMPUTER SCIENCE", "ECONOMICS"})

    def test_source_references_survive_complete_pipeline(self):
        summary = academic_summary(requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        safe = result["recommendation_safe_candidates"][0]
        self.assertTrue(safe["source_references"])
        self.assertEqual(safe["requirement_matches"][0]["sources"][0]["source_file"],
                         "rules.json")

    def test_incomplete_data_is_preserved(self):
        summary = academic_summary(incomplete=True)
        result = service([record("CS F211", None)], summary)[0].evaluate({})
        self.assertTrue(result["incomplete_data"]["has_incomplete_data"])
        codes = {item["code"] for item in result["incomplete_data"]["categories"]}
        self.assertIn("missing_prerequisite_information", codes)

    def test_duplicate_identities_are_diagnosed(self):
        result = service([record("CS F211")], academic_summary())[0].evaluate({})
        result["all_candidates"].append(deepcopy(result["all_candidates"][0]))
        validation = validate_course_policy_result(result)
        self.assertIn("duplicate_candidate_identity",
                      {item["code"] for item in validation["issues"]})

    def test_inconsistent_safe_component_state_is_diagnosed(self):
        summary = academic_summary(requirements=[
            ("CS F211", "r1", "remaining", "COMPUTER SCIENCE", "discipline_core")])
        result = service([record("CS F211")], summary)[0].evaluate({})
        result["recommendation_safe_candidates"][0]["eligibility_state"] = "unknown"
        validation = validate_course_policy_result(result)
        self.assertIn("safe_candidate_not_eligible",
                      {item["code"] for item in validation["issues"]})

    def test_malformed_profile_stops_safely(self):
        summary = academic_summary(status="invalid_student_profile", incomplete=True)
        summary["validation"]["is_valid"] = False
        result = service([record("CS F211")], summary)[0].evaluate(None)
        self.assertEqual(result["status"], "invalid_student_profile")
        self.assertEqual(result["summary"]["total_candidate_count"], 0)

    def test_unresolved_programme_stops_safely(self):
        summary = academic_summary(status="unresolved_programme", incomplete=True)
        summary["validation"]["is_valid"] = False
        result = service([record("CS F211")], summary)[0].evaluate({})
        self.assertEqual(result["status"], "unresolved_programme")
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_injected_in_memory_data_requires_no_pdf_processing(self):
        evaluator, _ = service([record("CS F211")], academic_summary())
        self.assertEqual(evaluator.course_catalogue.course_codes(), ["CS F211"])
        self.assertEqual(evaluator.evaluate({})["summary"]["catalogue_candidate_count"], 1)

    def test_phase_five_contract_performs_no_ranking(self):
        result = service([record("CS F211")], academic_summary())[0].evaluate({})
        self.assertFalse(result["phase5_input"]["contract"]["ranking_performed"])
        serialized = json.dumps(result)
        self.assertNotIn("recommendation_score", serialized)

    def test_output_is_json_serializable(self):
        result = service([record("CS F211")], academic_summary())[0].evaluate({})
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
