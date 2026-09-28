import json
import unittest
from copy import deepcopy

from backend.recommendation_ranking import (
    RecommendationRanker,
    rank_recommendations as _rank_recommendations,
    validate_ranking_result,
)


def source(code):
    return [{"source_file": f"{code}.pdf", "page_numbers": [1]}]


def candidate(code, safe=True, eligibility="eligible", pool="confirmed",
             filter_state="matches_remaining_requirement", scopes=None):
    return {
        "normalized_course_code": code,
        "recommendation_safe": safe,
        "eligibility_state": eligibility,
        "candidate_pool_state": pool,
        "requirement_filter_state": filter_state,
        "requirement_matches": [{"rule_id": "R1", "programme_scope": scope,
                                  "normalized_category": "DEL",
                                  "requirement_state": "remaining",
                                  "sources": source(code)}
                                 for scope in (scopes or ["MSc Computer Science"])],
        "source_references": source(code),
        "reason_codes": ["matches_remaining_requirement"],
        "validation": {"is_valid": True},
        "eligibility_result": {"eligibility_state": eligibility},
    }


def rank_recommendations(preference_matches, requirement_filter):
    """Supply the validated stage wrappers produced by Tasks 5.4 and Phase 4."""
    if isinstance(preference_matches, list):
        preference_matches = {"matches": preference_matches,
                              "validation": {"is_valid": True, "issues": []}}
    if isinstance(requirement_filter, dict) and "validation" not in requirement_filter:
        requirement_filter = deepcopy(requirement_filter)
        requirement_filter["validation"] = {"is_valid": True, "issues": []}
    return _rank_recommendations(preference_matches, requirement_filter)


def match(code, state="strong_match", matched=None, conflicts=None,
          unknown=None, score_fields=True):
    matched = matched or [{"value": "machine learning", "priority": "high",
                           "constraint": "soft", "assessment_state": "matched",
                           "evidence": [{"source_references": source(code),
                                         "matched_text": "machine learning"}]}]
    conflicts = conflicts or []
    unknown = unknown or []
    return {
        "course_code": code, "match_state": state,
        "matched_preferences": matched, "conflicting_preferences": conflicts,
        "unknown_preferences": unknown, "unmatched_preferences": [],
        "positive_evidence": [{"source_references": source(code),
                                "matched_text": "machine learning"}] if matched else [],
        "negative_evidence": [], "data_availability": {"content": True},
        "uncertainty": {"needs_verification": False}, "diagnostics": [],
        "validation": {"is_valid": True, "issues": []},
    }


class RecommendationRankingTests(unittest.TestCase):
    def test_verification_ranking_prefers_title_and_programme_evidence(self):
        relevant = candidate("CS F437", safe=False, eligibility="unknown",
                             pool="verification_required")
        incidental = candidate("BIOT F424", safe=False, eligibility="unknown",
                               pool="verification_required",
                               filter_state="relationship_not_established")
        relevant_match = match("CS F437")
        relevant_match["positive_evidence"][0]["course_field"] = "title.display_value"
        incidental_match = match("BIOT F424")
        incidental_match["positive_evidence"][0]["course_field"] = "content[0].text"
        result = rank_recommendations([incidental_match, relevant_match], {
            "candidates": [incidental, relevant]})
        self.assertEqual([item["course_code"] for item in result["verification_required"]],
                         ["CS F437", "BIOT F424"])
        self.assertGreater(
            result["verification_required"][0]["ranking"]["grounded_relevance_weight"],
            result["verification_required"][1]["ranking"]["grounded_relevance_weight"])

    def test_only_safe_eligible_remaining_candidates_are_confirmed(self):
        result = rank_recommendations([match("CS F211")],
                                      {"candidates": [candidate("CS F211")]})
        self.assertEqual([item["course_code"] for item in result["confirmed_recommendations"]],
                         ["CS F211"])

    def test_unknown_policy_is_verification_required(self):
        result = rank_recommendations([match("CS F211")], {
            "candidates": [candidate("CS F211", safe=False, eligibility="unknown",
                                      pool="verification_required")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"][0]["reasons"][0],
                         "policy_safety_not_confirmed")

    def test_ambiguous_ineligible_completed_and_ongoing_not_confirmed(self):
        entries = [
            candidate("CS F211", False, "ambiguous", "verification_required"),
            candidate("CS F212", False, "ineligible", "excluded"),
            candidate("CS F213", False, "eligible", "verification_required"),
            candidate("CS F214", False, "eligible", "verification_required"),
        ]
        entries[2]["eligibility_result"]["already_completed"] = True
        entries[3]["eligibility_result"]["already_ongoing"] = True
        result = rank_recommendations([match(code) for code in ("CS F211", "CS F212", "CS F213", "CS F214")],
                                      {"candidates": entries})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(len(result["verification_required"]), 4)

    def test_preference_score_never_changes_policy_safety(self):
        result = rank_recommendations(
            [match("CS F211", matched=[{"value": "x", "priority": "high",
                                        "constraint": "soft", "assessment_state": "matched",
                                        "evidence": [{"source_references": source("CS F211")}]}])],
            {"candidates": [candidate("CS F211", safe=False, eligibility="unknown",
                                       pool="verification_required")]})
        self.assertFalse(result["confirmed_recommendations"])

    def test_hard_conflict_is_verification_required(self):
        conflict = {"value": "hardware", "priority": "high", "constraint": "hard",
                    "assessment_state": "conflict", "evidence": [
                        {"source_references": source("CS F211")}]} 
        result = rank_recommendations([match("CS F211", conflicts=[conflict])],
                                      {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"][0]["reasons"][0],
                         "hard_preference_conflict")

    def test_soft_conflict_remains_explainable(self):
        conflict = {"value": "hardware", "priority": "medium", "constraint": "soft",
                    "assessment_state": "conflict", "evidence": [
                        {"source_references": source("CS F211")}]} 
        result = rank_recommendations([match("CS F211", conflicts=[conflict])],
                                      {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"][0]["preference_match"]
                         ["conflicting_preferences"][0]["constraint"], "soft")

    def test_no_match_is_not_confirmed(self):
        result = rank_recommendations([match("CS F211", state="no_match", matched=[])],
                                      {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_insufficient_evidence_is_not_confirmed(self):
        result = rank_recommendations(
            [match("CS F211", state="insufficient_evidence", matched=[],
                   unknown=[{"value": "x"}])],
            {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_deterministic_score_and_tie_breaking(self):
        result = rank_recommendations(
            [match("CS F212"), match("CS F211")],
            {"candidates": [candidate("CS F212"), candidate("CS F211")]})
        self.assertEqual([x["course_code"] for x in result["confirmed_recommendations"]],
                         ["CS F211", "CS F212"])
        self.assertEqual(result["confirmed_recommendations"][0]["ranking"]["score"], 3)

    def test_priority_weight_is_transparent(self):
        item = match("CS F211", matched=[{"value": "x", "priority": "low",
                                          "constraint": "soft", "assessment_state": "matched",
                                          "evidence": [{"source_references": source("CS F211")}]}])
        result = rank_recommendations([item], {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"][0]["ranking"]
                         ["matched_priority_weight"], 1)

    def test_requirement_relationship_and_scope_preserved(self):
        result = rank_recommendations([match("CS F211")], {
            "candidates": [candidate("CS F211", scopes=["Programme A", "Programme B"])]})
        self.assertEqual(len(result["confirmed_recommendations"][0]
                         ["requirement_relationship"]), 2)

    def test_multiple_programme_scopes_remain_separate(self):
        item = rank_recommendations([match("CS F211")], {
            "candidates": [candidate("CS F211", scopes=["A", "B"])]})
        scopes = [x["programme_scope"] for x in item["confirmed_recommendations"][0]
                  ["requirement_relationship"]]
        self.assertEqual(scopes, ["A", "B"])

    def test_source_and_uncertainty_preserved(self):
        item = match("CS F211")
        item["uncertainty"] = {"needs_verification": True}
        result = rank_recommendations([item], {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        ranked = result["verification_required"][0]
        self.assertTrue(ranked["source_references"])
        self.assertEqual(ranked["uncertainty"]["needs_verification"], True)

    def test_recommendation_safe_flag_does_not_override_inconsistent_policy(self):
        entry = candidate("CS F211", safe=True, eligibility="unknown",
                          pool="verification_required", filter_state="ambiguous")
        result = rank_recommendations([match("CS F211")], {"candidates": [entry]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_completed_or_ongoing_course_cannot_be_confirmed(self):
        for field in ("already_completed", "already_ongoing"):
            entry = candidate("CS F211")
            entry["eligibility_result"][field] = True
            result = rank_recommendations([match("CS F211")], {"candidates": [entry]})
            self.assertEqual(result["confirmed_recommendations"], [])

    def test_invalid_candidate_validation_cannot_be_confirmed(self):
        entry = candidate("CS F211")
        entry["validation"] = {"is_valid": False, "issues": []}
        result = rank_recommendations([match("CS F211")], {"candidates": [entry]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_invalid_filter_validation_without_issues_propagates(self):
        result = rank_recommendations(
            [match("CS F211")],
            {"candidates": [candidate("CS F211")],
             "validation": {"is_valid": False, "issues": []}})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_missing_upstream_validation_prevents_confirmation(self):
        result = _rank_recommendations(
            {"matches": [match("CS F211")]},
            {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_invalid_preference_match_validation_cannot_be_confirmed(self):
        item = match("CS F211")
        item["validation"] = {"is_valid": False, "issues": []}
        result = rank_recommendations([item], {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_zero_safe_candidates_returns_empty_confirmed(self):
        result = rank_recommendations([match("CS F211")], {
            "candidates": [candidate("CS F211", safe=False, eligibility="unknown",
                                      pool="verification_required")]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_missing_policy_record_is_not_confirmed(self):
        result = rank_recommendations([match("CS F211")], {"candidates": []})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"][0]["eligibility_state"], None)

    def test_malformed_inputs_fail_safe(self):
        result = rank_recommendations("malformed", [])
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_json_compatibility(self):
        result = rank_recommendations([match("CS F211")],
                                      {"candidates": [candidate("CS F211")]})
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_no_policy_fields_are_created_as_preference_fields(self):
        result = rank_recommendations([match("CS F211")],
                                      {"candidates": [candidate("CS F211")]})
        self.assertNotIn("recommendation_safe", result["confirmed_recommendations"][0]
                         ["preference_match"])

    def test_candidate_without_match_is_verification_required(self):
        result = rank_recommendations([], {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(len(result["verification_required"]), 1)

    def test_requirement_filter_collection_is_supported(self):
        result = RecommendationRanker().rank({
            "matches": [match("CS F211")],
            "validation": {"is_valid": True, "issues": []},
        }, {
            "recommendation_safe_candidates": [candidate("CS F211")],
            "verification_required_candidates": [], "excluded_candidates": [],
            "validation": {"is_valid": True, "issues": []},
        })
        self.assertEqual(len(result["confirmed_recommendations"]), 1)

    def test_empty_sources_and_malformed_requirement_never_confirm(self):
        entry = candidate("CS F211")
        entry["source_references"] = []
        entry["requirement_matches"] = [{}]
        item = match("CS F211")
        item["positive_evidence"] = [{"source_references": []}]
        result = rank_recommendations([item], {"candidates": [entry]})
        self.assertEqual(result["confirmed_recommendations"], [])

    def test_duplicate_candidate_identity_never_confirms(self):
        result = rank_recommendations(
            [match("CS F211")],
            {"candidates": [candidate("CS F211"), candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_duplicate_preference_identity_never_confirms(self):
        result = rank_recommendations(
            [match("CS F211"), match("CS F211")],
            {"candidates": [candidate("CS F211")]})
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_malformed_collection_entries_are_reported(self):
        result = _rank_recommendations(
            {"matches": [match("CS F211"), "bad"],
             "validation": {"is_valid": True, "issues": []}},
            {"candidates": [candidate("CS F211"), None],
             "validation": {"is_valid": True, "issues": []}})
        self.assertEqual(result["confirmed_recommendations"], [])
        codes = {item["code"] for item in result["validation"]["issues"]}
        self.assertIn("malformed_preference_match_entry", codes)
        self.assertIn("malformed_candidate_entry", codes)

    def test_final_validation_independently_rechecks_safety(self):
        result = rank_recommendations([match("CS F211")],
                                      {"candidates": [candidate("CS F211")]})
        result["confirmed_recommendations"][0]["policy"]["eligibility_state"] = "unknown"
        validation = validate_ranking_result(result)
        self.assertFalse(validation["is_valid"])
        self.assertIn("unsafe_confirmed_recommendation",
                      {item["code"] for item in validation["issues"]})


if __name__ == "__main__":
    unittest.main()
