import json
import unittest

from backend.recommendation_engine import RecommendationEngine, run_recommendation


def source(code):
    return [{"source_file": f"{code}.pdf", "page_numbers": [1]}]


def profile(code, title="Machine Learning"):
    return {
        "course_code": code,
        "title": {"values": [title], "display_value": title,
                  "sources": [{"source_file": f"{code}.pdf", "page_number": 1}]},
        "content": [], "topics": [], "evaluation": [],
        "exam_information": {"midsemester": [], "comprehensive": []},
        "attendance_or_makeup": {"attendance": [], "makeup": []},
        "data_availability": {"title": True, "content": False, "topics": False},
        "source_evidence": {"course_identity": source(code)},
        "uncertainty": {"needs_verification": False},
    }


def candidate(code, safe=True):
    return {
        "normalized_course_code": code, "recommendation_safe": safe,
        "eligibility_state": "eligible" if safe else "unknown",
        "candidate_pool_state": "confirmed" if safe else "verification_required",
        "requirement_filter_state": "matches_remaining_requirement" if safe else "ambiguous",
        "requirement_matches": [{"rule_id": "R1", "programme_scope": "Programme A",
                                  "normalized_category": "DEL",
                                  "requirement_state": "remaining",
                                  "sources": source(code)}],
        "source_references": source(code), "reason_codes": [],
        "eligibility_result": {"eligibility_state": "eligible" if safe else "unknown"},
        "validation": {"is_valid": True},
    }


class FakeBuilder:
    def build_all(self):
        return {"profiles": [profile("CS F211"), profile("CS F212")],
                "validation": {"is_valid": True}}


class FakePoolBuilder:
    def build(self, history, requirements, restrictions):
        return {"confirmed_candidates": [candidate("CS F211")],
                "verification_required_candidates": [candidate("CS F212", False)],
                "excluded_candidates": [], "candidates": [],
                "validation": {"is_valid": True}}


class DuplicateRanker:
    def rank(self, matches, filtered):
        item = {"course_code": "CS F211"}
        return {"confirmed_recommendations": [item, item],
                "verification_required": [],
                "validation": {"is_valid": False, "issues": []}}


class MismatchedRanker:
    def rank(self, matches, filtered):
        return {"confirmed_recommendations": [],
                "verification_required": [{"course_code": "CS F999"}],
                "validation": {"is_valid": True, "issues": []}}


class RecommendationEngineTests(unittest.TestCase):
    def engine(self):
        return RecommendationEngine(semantic_builder=FakeBuilder(),
                                    candidate_pool_builder=FakePoolBuilder())

    def filtered(self):
        return {"candidates": [candidate("CS F211"), candidate("CS F212", False)],
                "validation": {"is_valid": True}}

    def test_query_runs_through_all_stages(self):
        result = self.engine().recommend(
            "I want to study machine learning.",
            academic_history={}, academic_requirements={},
            requirement_filter_result=self.filtered())
        self.assertEqual(result["intent"]["preferences"]["preferred_topics"][0]["value"],
                         "machine learning")
        self.assertEqual(result["summary"]["semantic_profile_count"], 2)
        self.assertEqual(result["summary"]["preference_match_count"], 2)

    def test_only_safe_course_is_confirmed(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         academic_requirements={},
                                         requirement_filter_result=self.filtered())
        self.assertEqual([x["course_code"] for x in result["ranking"]
                          ["confirmed_recommendations"]], ["CS F211"])

    def test_verification_course_remains_separate(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         requirement_filter_result=self.filtered())
        self.assertEqual([x["course_code"] for x in result["ranking"]
                          ["verification_required"]], ["CS F212"])

    def test_supplied_candidate_pool_and_filter_are_reused(self):
        result = self.engine().recommend(
            "I want to study machine learning.", semantic_profiles=[profile("CS F211")],
            candidate_pool={"candidates": [candidate("CS F211")]},
            requirement_filter_result=self.filtered())
        self.assertEqual(result["summary"]["candidate_count"], 1)

    def test_phase_four_builder_is_used_when_filter_not_supplied(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         academic_history={}, academic_requirements={})
        self.assertEqual(result["summary"]["candidate_count"], 0)
        self.assertEqual(result["ranking"]["confirmed_recommendations"], [])

    def test_empty_confirmed_is_valid_when_policy_input_missing(self):
        result = self.engine().recommend("I want to study machine learning.")
        self.assertEqual(result["ranking"]["confirmed_recommendations"], [])
        self.assertEqual(result["summary"]["verification_required_count"], 2)

    def test_no_academic_eligibility_is_inferred(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         requirement_filter_result={"candidates": []})
        self.assertEqual(result["ranking"]["confirmed_recommendations"], [])

    def test_query_and_intent_traceability_preserved(self):
        query = "  I want to study machine learning.  "
        result = self.engine().recommend(query, requirement_filter_result=self.filtered())
        self.assertEqual(result["query"], query)
        self.assertEqual(result["intent"]["original_query"], query)

    def test_stage_identity_validation(self):
        builder = RecommendationEngine(semantic_builder=FakeBuilder())
        result = builder.recommend("I like machine learning.",
                                   semantic_profiles=[profile("CS F211"), profile("CS F211")],
                                   requirement_filter_result={"candidates": []})
        self.assertFalse(result["validation"]["is_valid"])

    def test_reasons_and_requirement_scope_survive(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         requirement_filter_result=self.filtered())
        item = result["ranking"]["confirmed_recommendations"][0]
        self.assertEqual(item["requirement_relationship"][0]["programme_scope"],
                         "Programme A")
        self.assertTrue(item["reasons"])

    def test_json_compatibility(self):
        result = self.engine().recommend("I want to study machine learning.",
                                         requirement_filter_result=self.filtered())
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_deterministic_repeated_run(self):
        engine = self.engine()
        first = engine.recommend("I want to study machine learning.",
                                 requirement_filter_result=self.filtered())
        second = engine.recommend("I want to study machine learning.",
                                  requirement_filter_result=self.filtered())
        self.assertEqual(first, second)

    def test_malformed_query_fails_safe(self):
        result = self.engine().recommend(None, requirement_filter_result=self.filtered())
        self.assertEqual(result["ranking"]["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_malformed_profiles_fail_safe(self):
        result = RecommendationEngine(semantic_builder=FakeBuilder()).recommend(
            "I like machine learning.", semantic_profiles="bad",
            requirement_filter_result={"candidates": []})
        self.assertFalse(result["validation"]["is_valid"])

    def test_convenience_entry_point_accepts_injected_outputs(self):
        result = run_recommendation(
            "I want to study machine learning.",
            semantic_profiles={"profiles": [profile("CS F211")]},
            requirement_filter_result={
                "candidates": [candidate("CS F211")],
                "validation": {"is_valid": True, "issues": []},
            })
        self.assertEqual(len(result["ranking"]["confirmed_recommendations"]), 1)

    def test_invalid_stage_without_issues_makes_engine_invalid(self):
        profiles = {"profiles": [profile("CS F211")],
                    "validation": {"is_valid": False, "issues": []}}
        result = self.engine().recommend(
            "I want to study machine learning.", semantic_profiles=profiles,
            requirement_filter_result={"candidates": [candidate("CS F211")]})
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("invalid_upstream_stage",
                      {item["code"] for item in result["validation"]["issues"]})

    def test_duplicate_ranked_identity_is_reported_without_crashing(self):
        engine = RecommendationEngine(semantic_builder=FakeBuilder(),
                                      ranker=DuplicateRanker())
        result = engine.recommend(
            "I want to study machine learning.",
            requirement_filter_result={"candidates": [candidate("CS F211")]})
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("duplicate_ranked_identity",
                      {item["code"] for item in result["validation"]["issues"]})

    def test_ranked_identity_count_mismatch_is_invalid(self):
        engine = RecommendationEngine(semantic_builder=FakeBuilder(),
                                      ranker=MismatchedRanker())
        result = engine.recommend(
            "I want to study machine learning.",
            semantic_profiles=[profile("CS F211")],
            requirement_filter_result={"candidates": []})
        self.assertIn("ranked_identity_set_mismatch",
                      {item["code"] for item in result["validation"]["issues"]})


if __name__ == "__main__":
    unittest.main()
