from io import BytesIO
import json
import unittest
from unittest.mock import patch

from backend.api import application
from backend.dashboard_recommendations import DashboardRecommendationService
from backend.gemini import GeminiError, configured_recommendation_engine
from backend.recommendation_engine import RecommendationEngine


def source(code="CS F211"):
    return [{"source_file": f"{code}.pdf", "page_number": 1, "text": code}]


def profile_input(**updates):
    result = {
        "programme": "Programme A", "second_programme": None,
        "current_academic_year": 2, "current_semester": 1,
        "completed_courses": [], "ongoing_courses": [],
    }
    result.update(updates)
    return result


def semantic(code="CS F211", title="Machine Learning"):
    return {
        "course_code": code,
        "title": {"values": [title], "display_value": title,
                  "sources": source(code)},
        "content": [], "topics": [], "evaluation": [],
        "exam_information": {"midsemester": [], "comprehensive": []},
        "attendance_or_makeup": {"attendance": [], "makeup": []},
        "data_availability": {"title": True, "content": False, "topics": False},
        "source_evidence": {"course_identity": source(code)},
        "uncertainty": {"needs_verification": False},
    }


def candidate(code="CS F211", safe=True, pool_state=None):
    pool_state = pool_state or ("confirmed" if safe else "verification_required")
    eligible = safe
    return {
        "normalized_course_code": code,
        "recommendation_safe": safe,
        "eligibility_state": "eligible" if eligible else "unknown",
        "candidate_pool_state": pool_state,
        "requirement_filter_state": (
            "matches_remaining_requirement" if safe else "relationship_not_established"),
        "requirement_matches": ([{
            "rule_id": "R1", "programme_scope": "Programme A",
            "normalized_category": "DEL", "requirement_state": "remaining",
            "sources": source(code),
        }] if safe else []),
        "source_references": source(code),
        "reason_codes": [] if safe else ["eligibility_not_confirmed"],
        "eligibility_result": {
            "eligibility_state": "eligible" if eligible else "unknown",
            "already_completed": False, "already_ongoing": False,
        },
        "validation": {"is_valid": True, "issues": []},
    }


class FakeCatalogue:
    def course_codes(self):
        return ["CS F211", "CS F212", "CS F213"]


class FakeSourceCatalogue:
    def __init__(self, codes):
        self.codes = codes

    def course_codes(self):
        return list(self.codes)


class FakePolicyService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def evaluate(self, profile):
        self.calls.append(profile)
        return self.result


class FakeSemanticBuilder:
    def __init__(self, profiles):
        self.profiles = {item["course_code"]: item for item in profiles}
        self.calls = []

    def build_one(self, code):
        self.calls.append(code)
        return self.profiles.get(code)


class FailingGeminiClient:
    def generate_structured(self, prompt, schema):
        raise GeminiError("unavailable")


def policy_result(safe=None, verification=None, excluded=None, valid=True):
    safe = safe or []
    verification = verification or []
    excluded = excluded or []
    all_candidates = safe + verification + excluded
    return {
        "status": "complete",
        "academic_requirement_summary": {
            "status": "complete", "summary": {"remaining_requirement_count": 1}},
        "recommendation_safe_candidates": safe,
        "verification_required_candidates": verification,
        "excluded_candidates": excluded,
        "all_candidates": all_candidates,
        "summary": {"total_candidate_count": len(all_candidates),
                    "recommendation_safe_count": len(safe)},
        "incomplete_data": {"has_incomplete_data": False},
        "validation": {"is_valid": valid, "issues": []},
    }


class DashboardRecommendationTests(unittest.TestCase):
    def service(self, policy, profiles, engine=None):
        return DashboardRecommendationService(
            course_catalogue=FakeCatalogue(),
            policy_service=FakePolicyService(policy),
            semantic_builder=FakeSemanticBuilder(profiles),
            recommendation_engine=engine or RecommendationEngine(),
        )

    def test_complete_flow_confirms_only_policy_safe_match(self):
        safe = candidate()
        result = self.service(policy_result(safe=[safe]), [semantic()]).recommend(
            profile_input(), "I want to study machine learning.")
        self.assertEqual([item["course_code"] for item in
                          result["confirmed_recommendations"]], ["CS F211"])
        self.assertEqual(result["academic_requirements"]["status"], "complete")
        self.assertTrue(result["validation"]["is_valid"])

    def test_unconfirmed_policy_course_cannot_be_promoted(self):
        unsafe = candidate("CS F212", safe=False)
        result = self.service(policy_result(verification=[unsafe]), [
            semantic("CS F212", "Machine Learning Systems")]).recommend(
            profile_input(), "I want to study machine learning.")
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"][0]["course_code"], "CS F212")
        self.assertIn("policy_safety_not_confirmed",
                      result["verification_required"][0]["reasons"])

    def test_excluded_course_stays_excluded(self):
        excluded = candidate("CS F213", safe=False, pool_state="excluded")
        result = self.service(policy_result(excluded=[excluded]), []).recommend(
            profile_input(), "I like machine learning.")
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual(result["verification_required"], [])
        self.assertEqual(result["excluded_courses"][0]["normalized_course_code"],
                         "CS F213")

    def test_safe_and_unresolved_courses_receive_semantic_matching(self):
        safe = candidate()
        unsafe = candidate("CS F212", safe=False)
        builder = FakeSemanticBuilder([semantic(), semantic("CS F212")])
        service = DashboardRecommendationService(
            course_catalogue=FakeCatalogue(),
            policy_service=FakePolicyService(policy_result(
                safe=[safe], verification=[unsafe])),
            semantic_builder=builder,
            recommendation_engine=RecommendationEngine(),
        )
        service.recommend(profile_input(), "I like machine learning.")
        self.assertEqual(builder.calls, ["CS F211", "CS F212", "CS F213"])

    def test_irrelevant_policy_backlog_is_not_displayed_as_recommendations(self):
        unsafe = candidate("CS F212", safe=False)
        result = self.service(policy_result(verification=[unsafe]), [
            semantic("CS F212", "Biological Sciences")]).recommend(
            profile_input(), "I am interested in artificial intelligence.")
        self.assertEqual(result["verification_required"], [])
        self.assertEqual(result["academic_verification_backlog"][0]["course_code"],
                         "CS F212")

    def test_bulletin_only_semantic_match_requires_verification(self):
        builder = FakeSemanticBuilder([
            semantic("CS F437", "Generative Artificial Intelligence"),
            semantic("BIO F101", "Introduction to Biological Sciences"),
        ])
        service = DashboardRecommendationService(
            course_catalogue=FakeCatalogue(),
            source_catalogue=FakeSourceCatalogue(["CS F437", "BIO F101"]),
            policy_service=FakePolicyService(policy_result()),
            semantic_builder=builder,
            recommendation_engine=RecommendationEngine(),
        )
        result = service.recommend(
            profile_input(), "I am interested in artificial intelligence.")
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertEqual([item["course_code"] for item in
                          result["verification_required"]], ["CS F437"])
        self.assertEqual(result["verification_required"][0]["eligibility_state"],
                         "unknown")
        self.assertEqual({item["course_code"] for item in
                          result["academic_verification_backlog"]},
                         {"BIO F101", "CS F437"})

    def test_invalid_profile_stops_before_policy(self):
        policy = FakePolicyService(policy_result())
        service = DashboardRecommendationService(
            course_catalogue=FakeCatalogue(), policy_service=policy,
            semantic_builder=FakeSemanticBuilder([]),
            recommendation_engine=RecommendationEngine())
        result = service.recommend(profile_input(programme=""), "I like AI.")
        self.assertFalse(result["validation"]["is_valid"])
        self.assertEqual(result["pipeline"]["course_policy_status"], "not_run")
        self.assertEqual(policy.calls, [])

    def test_gemini_failure_uses_deterministic_fallback(self):
        safe = candidate()
        engine = configured_recommendation_engine(FailingGeminiClient())
        result = self.service(policy_result(safe=[safe]), [semantic()], engine).recommend(
            profile_input(), "I want to study machine learning.")
        self.assertEqual(len(result["confirmed_recommendations"]), 1)
        self.assertIn("gemini_unavailable_or_invalid",
                      {item.get("reason") for item in result["intent"]["uninterpreted"]})

    def test_invalid_policy_validation_prevents_confirmation(self):
        safe = candidate()
        result = self.service(
            policy_result(safe=[safe], valid=False), [semantic()]).recommend(
                profile_input(), "I want to study machine learning.")
        self.assertEqual(result["confirmed_recommendations"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_result_counts_reconcile(self):
        safe = candidate()
        unsafe = candidate("CS F212", safe=False)
        excluded = candidate("CS F213", safe=False, pool_state="excluded")
        result = self.service(policy_result(
            safe=[safe], verification=[unsafe], excluded=[excluded]),
            [semantic(), semantic("CS F212", "Machine Learning Systems")]).recommend(
                profile_input(), "I like machine learning.")
        self.assertEqual(result["summary"], {
            "confirmed_recommendation_count": 1,
            "verification_required_count": 1,
            "academic_verification_backlog_count": 1,
            "excluded_course_count": 1,
        })


class FakeDashboardService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def recommend(self, profile, query):
        self.calls.append((profile, query))
        return self.result


def api_request(payload):
    body = json.dumps(payload).encode()
    environ = {"PATH_INFO": "/api/recommendations", "REQUEST_METHOD": "POST",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body)}
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    response = b"".join(application(environ, start_response))
    return captured["status"], json.loads(response)


class DashboardRecommendationApiTests(unittest.TestCase):
    def test_endpoint_accepts_profile_and_query(self):
        expected = {"validation": {"is_valid": True}, "summary": {
            "confirmed_recommendation_count": 0,
            "verification_required_count": 0, "excluded_course_count": 0}}
        service = FakeDashboardService(expected)
        with patch("backend.api._recommendation_service", return_value=service):
            status, response = api_request(
                {"profile": profile_input(), "query": "I like AI."})
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["recommendations"], expected)
        self.assertEqual(service.calls[0][1], "I like AI.")

    def test_endpoint_rejects_malformed_contract(self):
        status, response = api_request({"query": "I like AI."})
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(response["error"], "invalid_recommendation_payload")

    def test_invalid_pipeline_result_returns_422(self):
        service = FakeDashboardService({"validation": {"is_valid": False}})
        with patch("backend.api._recommendation_service", return_value=service):
            status, _ = api_request(
                {"profile": profile_input(), "query": "I like AI."})
        self.assertEqual(status, "422 Unprocessable Entity")

    def test_dashboard_contains_progress_and_result_regions(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        body = b"".join(application(
            {"PATH_INFO": "/", "REQUEST_METHOD": "GET"}, start_response)).decode()
        self.assertEqual(captured["status"], "200 OK")
        for element_id in ("recommendation-results", "academic-progress",
                           "confirmed-results", "verification-results"):
            self.assertIn(f'id="{element_id}"', body)

    def test_frontend_renders_explanations_and_preference_evidence(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        script = b"".join(application(
            {"PATH_INFO": "/assets/app.js", "REQUEST_METHOD": "GET"},
            start_response)).decode()
        self.assertIn("item.explanation?.text", script)
        self.assertIn("Matched preference:", script)
        self.assertIn("remaining_requirement_count", script)
        self.assertIn("query-relevant to verify", script)


if __name__ == "__main__":
    unittest.main()
