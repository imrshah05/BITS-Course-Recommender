import json
import unittest
from copy import deepcopy

from backend.requirement_filter import (
    filter_by_remaining_requirements,
    validate_requirement_filter,
)


def relationship(rule="r1", programme="COMPUTER SCIENCE",
                 category="discipline_core", sources=True):
    return {
        "rule_id": rule,
        "programme_scope": programme,
        "requested_programme_role": "primary",
        "normalized_category": category,
        "completion_status": "remaining",
        "relationship_source": "remaining_requirements",
        "sources": ([{"source_file": "rules.json", "page_number": 10}]
                    if sources else []),
    }


def candidate(code="CS F211", pool_state="confirmed", eligibility="eligible",
              relationships=None, unresolved=None):
    relationships = relationships if relationships is not None else []
    unresolved = unresolved if unresolved is not None else []
    return {
        "normalized_course_code": code,
        "course_identity": {"normalized_course_code": code},
        "source_records": [{"source": {"source_file": "course.pdf",
                                        "page_numbers": [1]}}],
        "source_references": [{"source_file": "course.pdf", "page_numbers": [1]}],
        "eligibility_state": eligibility,
        "eligibility_result": {"eligibility_state": eligibility},
        "candidate_pool_state": pool_state,
        "requirement_relevance": {
            "state": "explicit" if relationships else "not_established",
            "relationships": relationships,
            "unresolved_relationships": unresolved,
        },
        "reason_codes": [],
        "validation": {"is_valid": True, "issues": []},
    }


def pool(*candidates):
    result = {"confirmed_candidates": [],
              "verification_required_candidates": [],
              "excluded_candidates": []}
    names = {"confirmed": "confirmed_candidates",
             "verification_required": "verification_required_candidates",
             "excluded": "excluded_candidates"}
    for item in candidates:
        result[names[item["candidate_pool_state"]]].append(item)
    return result


def requirement(rule="r1", state="remaining", programme="COMPUTER SCIENCE",
                category="discipline_core", sources=True):
    collections = {"satisfied": "satisfied_requirements",
                   "partially_satisfied": "partially_satisfied_requirements",
                   "remaining": "remaining_requirements",
                   "unevaluable": "unevaluable_requirements"}
    category_data = {name: [] for name in collections.values()}
    category_data.update({"normalized_category": category})
    category_data[collections[state]].append({
        "rule_id": rule,
        "completion_status": state,
        "course_code": "CS F211",
        "sources": ([{"source_file": "rules.json", "page_number": 10}]
                    if sources else []),
    })
    return {"requirement_progress": {"programme_progress": [{
        "requested_programme_role": "primary",
        "programme": programme,
        "categories": [category_data],
        "uncategorized_requirements": [],
    }], "unresolved_requirements": []}}


def combine(*summaries):
    programmes = []
    for summary in summaries:
        programmes.extend(summary["requirement_progress"]["programme_progress"])
    return {"requirement_progress": {"programme_progress": programmes,
                                     "unresolved_requirements": []}}


class RequirementFilterTests(unittest.TestCase):
    def test_confirmed_remaining_match_is_recommendation_safe(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])), requirement())
        self.assertEqual(result["candidates"][0]["requirement_filter_state"],
                         "matches_remaining_requirement")
        self.assertTrue(result["candidates"][0]["recommendation_safe"])

    def test_satisfied_match_is_not_recommendation_safe(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])), requirement(state="satisfied"))
        self.assertEqual(result["candidates"][0]["requirement_filter_state"],
                         "matches_satisfied_requirement")
        self.assertFalse(result["candidates"][0]["recommendation_safe"])

    def test_no_relationship_is_not_safe(self):
        result = filter_by_remaining_requirements(pool(candidate()), requirement())
        self.assertEqual(result["candidates"][0]["requirement_filter_state"],
                         "relationship_not_established")
        self.assertEqual(result["recommendation_safe_candidates"], [])

    def test_unknown_eligibility_remaining_match_requires_verification(self):
        item = candidate(pool_state="verification_required", eligibility="unknown",
                         relationships=[relationship()])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertFalse(result["candidates"][0]["recommendation_safe"])
        self.assertEqual(result["candidates"][0]["candidate_pool_state"],
                         "verification_required")

    def test_ambiguous_eligibility_is_not_safe(self):
        item = candidate(pool_state="verification_required", eligibility="ambiguous",
                         relationships=[relationship()])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertFalse(result["candidates"][0]["recommendation_safe"])

    def test_excluded_course_remains_excluded(self):
        item = candidate(pool_state="excluded", eligibility="ineligible",
                         relationships=[relationship()])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertEqual(result["pool_state_groups"]["excluded"][0]
                         ["candidate_pool_state"], "excluded")

    def test_unevaluable_requirement_is_unavailable(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])),
            requirement(state="unevaluable"))
        self.assertEqual(result["candidates"][0]["requirement_filter_state"],
                         "requirement_unavailable")
        self.assertFalse(result["candidates"][0]["recommendation_safe"])

    def test_multiple_programmes_remain_separate(self):
        relationships = [relationship("r1", "COMPUTER SCIENCE"),
                         relationship("r2", "ECONOMICS", "discipline_elective")]
        requirements = combine(
            requirement("r1", programme="COMPUTER SCIENCE"),
            requirement("r2", programme="ECONOMICS", category="discipline_elective"))
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=relationships)), requirements)
        states = result["candidates"][0]["programme_requirement_states"]
        self.assertEqual({item["programme_scope"] for item in states},
                         {"COMPUTER SCIENCE", "ECONOMICS"})

    def test_course_relevant_to_only_one_programme(self):
        requirements = combine(requirement("r1"),
                               requirement("r2", programme="ECONOMICS"))
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship("r1")])), requirements)
        states = result["candidates"][0]["programme_requirement_states"]
        self.assertEqual([item["programme_scope"] for item in states],
                         ["COMPUTER SCIENCE"])

    def test_missing_programme_scope_prevents_match(self):
        item = candidate(relationships=[relationship(programme=None)])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertEqual(result["candidates"][0]["requirement_filter_state"], "ambiguous")
        self.assertFalse(result["candidates"][0]["recommendation_safe"])

    def test_malformed_relationship_produces_diagnostics(self):
        item = candidate(relationships=["bad"])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertIn("malformed_requirement_relationship",
                      result["candidates"][0]["requirement_filter_reason_codes"])

    def test_source_evidence_survives(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])), requirement())
        match = result["candidates"][0]["requirement_matches"][0]
        self.assertEqual(match["sources"][0]["source_file"], "rules.json")

    def test_duplicate_identities_are_detected(self):
        result = filter_by_remaining_requirements(
            pool(candidate(), candidate()), requirement())
        self.assertEqual(result["validation"]["duplicate_candidate_identity_count"], 1)
        self.assertFalse(result["validation"]["is_valid"])

    def test_does_not_invent_academic_category(self):
        result = filter_by_remaining_requirements(pool(candidate()), requirement())
        serialized = json.dumps(result)
        for value in ("CDC", "DEL", "HUEL", "OPEL"):
            self.assertNotIn(value, serialized)

    def test_confirmed_eligibility_alone_is_insufficient(self):
        result = filter_by_remaining_requirements(pool(candidate()), requirement())
        self.assertEqual(result["summary"]["recommendation_safe_count"], 0)

    def test_partial_requirement_counts_as_remaining(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])),
            requirement(state="partially_satisfied"))
        self.assertTrue(result["candidates"][0]["recommendation_safe"])

    def test_missing_source_traceability_prevents_match(self):
        item = candidate(relationships=[relationship(sources=False)])
        result = filter_by_remaining_requirements(pool(item), requirement())
        self.assertEqual(result["candidates"][0]["requirement_filter_state"], "ambiguous")

    def test_validation_rejects_unsafe_safe_candidate(self):
        result = filter_by_remaining_requirements(pool(candidate()), requirement())
        result["candidates"][0]["recommendation_safe"] = True
        validation = validate_requirement_filter(result)
        codes = {issue["code"] for issue in validation["issues"]}
        self.assertIn("unsafe_candidate_without_remaining_match", codes)

    def test_output_is_json_serializable(self):
        result = filter_by_remaining_requirements(
            pool(candidate(relationships=[relationship()])), requirement())
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
