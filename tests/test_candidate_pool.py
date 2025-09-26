import json
import unittest
from copy import deepcopy

from backend.candidate_pool import (
    CandidatePoolBuilder,
    filter_candidates,
    validate_candidate_pool,
)
from backend.course_catalogue import CourseCatalogue


def prerequisite(kind="none", verify=False):
    if kind is None:
        return None
    return {
        "kind": kind,
        "needs_verification": verify,
        "text": "Knowledge of algorithms" if kind == "unresolved" else None,
        "diagnostics": ["free_form"] if kind == "unresolved" else [],
        "machine_evaluable": kind != "unresolved",
        "sources": [{"source_file": "handout.pdf", "page_number": 1,
                     "text": "Prerequisite evidence"}],
    }


def record(code, prereq="none", source=None, title="Course"):
    source = source or code.replace(" ", "_") + ".pdf"
    return {
        "candidate_status": "usable",
        "metadata": {
            "course_code": {"value": code, "sources": []},
            "course_codes": [code],
            "course_identity_type": "single",
            "course_title": {"value": title, "sources": []},
            "department_division": {"value": "Department", "sources": []},
            "units": {"value": 4, "sources": []},
        },
        "prerequisites": prerequisite(prereq, prereq == "unresolved"),
        "instructors": [], "syllabus": [], "evaluation": [],
        "exams": {"midsemester": None, "comprehensive": None},
        "attendance": [], "makeup": [], "observations": [],
        "source": {"source_file": source, "page_numbers": [1]},
        "validation": {"is_valid": True, "needs_verification": False, "issues": []},
    }


def history(completed=(), ongoing=()):
    def entries(codes, status):
        return [{"normalized_course_code": code, "status": status,
                 "resolution_status": "matched", "catalogue_matches": []}
                for code in codes]
    return {
        "programme": "COMPUTER SCIENCE",
        "completed_courses": entries(completed, "completed"),
        "ongoing_courses": entries(ongoing, "ongoing"),
        "profile_validation": {"is_valid": True, "issues": []},
        "validation": {"is_valid": True, "issues": []},
    }


def requirement_summary(code="CS F211", category="discipline_core",
                        programme="COMPUTER SCIENCE", role="primary"):
    requirement = {
        "rule_id": "rule-1", "rule_type": "required_course",
        "completion_status": "remaining", "course_code": code,
        "alternatives": None,
        "sources": [{"source_file": "academic_rules.json", "page_number": 10}],
    }
    return {
        "requirement_progress": {
            "programme_progress": [{
                "requested_programme_role": role,
                "programme": programme,
                "categories": [{
                    "normalized_category": category,
                    "remaining_requirements": [requirement],
                    "satisfied_requirements": [],
                    "partially_satisfied_requirements": [],
                    "unevaluable_requirements": [],
                }],
                "uncategorized_requirements": [],
            }],
            "unresolved_requirements": [],
        }
    }


def build(records, completed=(), ongoing=(), requirements=None):
    catalogue = CourseCatalogue(records)
    return CandidatePoolBuilder(catalogue).build(
        history(completed, ongoing), requirements)


def only(pool):
    return (pool["confirmed_candidates"] +
            pool["verification_required_candidates"] +
            pool["excluded_candidates"])[0]


class CandidatePoolTests(unittest.TestCase):
    def test_eligible_course_enters_confirmed_pool(self):
        result = build([record("CS F211")])
        self.assertEqual(result["confirmed_candidates"][0]["normalized_course_code"],
                         "CS F211")

    def test_unknown_course_requires_verification(self):
        result = build([record("CS F211", None)])
        self.assertEqual(result["verification_required_candidates"][0]
                         ["eligibility_state"], "unknown")

    def test_ambiguous_course_requires_verification(self):
        result = build([record("CS F211", "unresolved")])
        self.assertEqual(result["verification_required_candidates"][0]
                         ["eligibility_state"], "ambiguous")

    def test_ineligible_course_enters_excluded_pool(self):
        result = build([record("CS F211")], completed=["CS F211"])
        self.assertEqual(result["excluded_candidates"][0]["candidate_pool_state"],
                         "excluded")

    def test_completed_target_remains_explicitly_excluded(self):
        entry = build([record("CS F211")], completed=["CS F211"])[
            "excluded_candidates"][0]
        self.assertTrue(entry["eligibility_result"]["already_completed"])
        self.assertIn("target_already_completed", entry["reason_codes"])

    def test_ongoing_target_remains_explicitly_excluded(self):
        entry = build([record("CS F211")], ongoing=["CS F211"])[
            "excluded_candidates"][0]
        self.assertTrue(entry["eligibility_result"]["already_ongoing"])

    def test_missing_prerequisite_never_enters_confirmed_pool(self):
        result = build([record("CS F211", None)])
        self.assertEqual(result["confirmed_candidates"], [])

    def test_explicit_requirement_relevance_is_retained(self):
        entry = only(build([record("CS F211")], requirements=requirement_summary()))
        self.assertEqual(entry["requirement_relevance"]["state"], "explicit")
        self.assertEqual(entry["requirement_relevance"]["relationships"][0]["rule_id"],
                         "rule-1")

    def test_absent_relevance_is_not_established(self):
        entry = only(build([record("CS F211")]))
        self.assertEqual(entry["requirement_relevance"]["state"], "not_established")

    def test_relevance_cannot_override_ineligibility(self):
        result = build([record("CS F211")], completed=["CS F211"],
                       requirements=requirement_summary())
        self.assertEqual(result["excluded_candidates"][0]
                         ["requirement_relevance"]["state"], "explicit")

    def test_eligible_course_can_have_unknown_relevance(self):
        entry = build([record("CS F211")])["confirmed_candidates"][0]
        self.assertEqual(entry["requirement_relevance"]["state"], "not_established")

    def test_multiple_handouts_remain_one_candidate(self):
        result = build([record("CS F211", source="one.pdf"),
                        record("CS F211", source="two.pdf")])
        self.assertEqual(result["summary"]["total_candidate_count"], 1)
        self.assertEqual(len(result["confirmed_candidates"][0]["source_records"]), 2)

    def test_conflicting_metadata_remains_preserved(self):
        entry = only(build([record("CS F211", source="one.pdf", title="One"),
                            record("CS F211", source="two.pdf", title="Two")]))
        self.assertIn("course_title", entry["course_identity"]["metadata_conflicts"])

    def test_source_evidence_survives(self):
        entry = only(build([record("CS F211")], requirements=requirement_summary()))
        files = {source.get("source_file") for source in entry["source_references"]}
        self.assertEqual(files, {"CS_F211.pdf", "academic_rules.json"})

    def test_duplicate_candidate_identity_is_detected(self):
        result = build([record("CS F211")])
        duplicate = deepcopy(result["confirmed_candidates"][0])
        duplicate["candidate_pool_state"] = "excluded"
        duplicate["eligibility_state"] = "ineligible"
        duplicate["eligibility_result"]["eligibility_state"] = "ineligible"
        result["excluded_candidates"].append(duplicate)
        validation = validate_candidate_pool(result)
        self.assertEqual(validation["duplicate_candidate_identity_count"], 1)
        self.assertFalse(validation["is_valid"])

    def test_malformed_eligibility_cannot_enter_confirmed_pool(self):
        result = build([record("CS F211")])
        entry = result["confirmed_candidates"][0]
        entry["eligibility_state"] = "broken"
        self.assertIn("eligibility_pool_state_mismatch",
                      {issue["code"] for issue in validate_candidate_pool(result)["issues"]})

    def test_filtering_by_pool_state(self):
        result = build([record("CS F211"), record("CS F212", None)])
        self.assertEqual([item["normalized_course_code"] for item in
                          filter_candidates(result, pool_state="confirmed")], ["CS F211"])

    def test_filtering_by_explicit_category(self):
        result = build([record("CS F211"), record("CS F212")],
                       requirements=requirement_summary())
        matches = filter_candidates(result, normalized_category="discipline_core")
        self.assertEqual([item["normalized_course_code"] for item in matches], ["CS F211"])

    def test_filtering_by_programme_scope(self):
        result = build([record("CS F211")], requirements=requirement_summary())
        matches = filter_candidates(result, programme_scope="COMPUTER SCIENCE")
        self.assertEqual(len(matches), 1)

    def test_batch_counts_are_consistent(self):
        result = build([record("CS F211"), record("CS F212", None),
                        record("CS F213", "unresolved")], completed=["CS F211"])
        summary = result["summary"]
        self.assertEqual(summary["total_candidate_count"],
                         summary["confirmed_candidate_count"] +
                         summary["verification_required_candidate_count"] +
                         summary["excluded_candidate_count"])
        self.assertEqual(summary["unconfirmed_eligibility_in_confirmed_pool_count"], 0)
        self.assertTrue(result["validation"]["is_valid"])

    def test_uncategorized_relationship_is_ambiguous(self):
        summary = requirement_summary()
        programme = summary["requirement_progress"]["programme_progress"][0]
        requirement = programme["categories"][0]["remaining_requirements"].pop()
        programme["uncategorized_requirements"] = [requirement]
        entry = only(build([record("CS F211")], requirements=summary))
        self.assertEqual(entry["requirement_relevance"]["state"], "ambiguous")
        self.assertTrue(entry["requires_manual_verification"])

    def test_output_is_json_serializable(self):
        result = build([record("CS F211")], requirements=requirement_summary())
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
