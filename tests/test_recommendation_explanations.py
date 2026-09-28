import json
import unittest

from backend.gemini import GeminiError
from backend.recommendation_explanations import RecommendationExplanationService


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate_structured(self, prompt, schema):
        self.calls.append((prompt, schema))
        if self.error:
            raise self.error
        return self.result


def item(code="CS F211", confirmed=True):
    return {
        "course_code": code,
        "course_title": "Machine Learning",
        "eligibility_state": "eligible" if confirmed else "unknown",
        "requirement_filter_state": (
            "matches_remaining_requirement" if confirmed
            else "relationship_not_established"),
        "reasons": (["preference_match_ranked_after_policy_safety"] if confirmed
                    else ["prerequisite_unknown",
                          "phase4_recommendation_safety_not_confirmed"]),
        "preference_match": {
            "matched_preferences": ([{"value": "machine learning"}]
                                    if confirmed else [])},
        "source_references": [{"source_file": "course.pdf", "page_number": 1}],
    }


def results(confirmed=None, verification=None):
    return {"confirmed_recommendations": confirmed or [],
            "verification_required": verification or []}


def generated(code="CS F211", status="confirmed", text=None, fact_ids=None):
    return {"explanations": [{
        "course_code": code, "status": status,
        "fact_ids": fact_ids or ["status", "eligibility", "preferences"],
        "text": text or (
            "CS F211 is confirmed by deterministic checks and matches your "
            "machine learning preference."),
    }]}


class RecommendationExplanationTests(unittest.TestCase):
    def test_valid_gemini_explanation_is_grounded(self):
        client = FakeClient(generated())
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        explanation = result["confirmed_recommendations"][0]["explanation"]
        self.assertEqual(explanation["method"], "gemini")
        self.assertEqual(explanation["grounded_fact_ids"],
                         ["status", "eligibility", "preferences"])
        prompt = client.calls[0][0]
        self.assertIn("deterministic eligibility state is eligible", prompt)
        self.assertIn("Source-backed preference matches", prompt)

    def test_unavailable_gemini_uses_deterministic_explanation(self):
        client = FakeClient(error=GeminiError("unavailable"))
        result = RecommendationExplanationService(client).explain(
            results(verification=[item(confirmed=False)]))
        explanation = result["verification_required"][0]["explanation"]
        self.assertEqual(explanation["method"], "deterministic")
        self.assertIn("prerequisite information is incomplete", explanation["text"])

    def test_changed_status_is_rejected(self):
        client = FakeClient(generated(status="verification_required"))
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        self.assertEqual(result["confirmed_recommendations"][0]
                         ["explanation"]["method"], "deterministic")

    def test_uncited_fact_is_rejected(self):
        client = FakeClient(generated(fact_ids=["invented_fact"]))
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        self.assertEqual(result["confirmed_recommendations"][0]
                         ["explanation"]["method"], "deterministic")

    def test_another_course_identity_is_rejected(self):
        client = FakeClient(generated(text="CS F999 is a confirmed recommendation."))
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        self.assertEqual(result["confirmed_recommendations"][0]
                         ["explanation"]["method"], "deterministic")

    def test_verification_cannot_be_described_as_confirmed(self):
        client = FakeClient(generated(
            status="verification_required", code="CS F212",
            text="CS F212 is a confirmed recommendation.",
            fact_ids=["status", "eligibility"]))
        result = RecommendationExplanationService(client).explain(
            results(verification=[item("CS F212", confirmed=False)]))
        self.assertEqual(result["verification_required"][0]
                         ["explanation"]["method"], "deterministic")

    def test_unsupported_prerequisite_claim_is_rejected(self):
        client = FakeClient(generated(
            text="CS F211 is confirmed because every prerequisite is satisfied."))
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        self.assertEqual(result["confirmed_recommendations"][0]
                         ["explanation"]["method"], "deterministic")

    def test_changed_eligibility_state_is_rejected(self):
        client = FakeClient(generated(text="CS F211 has unknown eligibility."))
        result = RecommendationExplanationService(client).explain(
            results(confirmed=[item()]))
        self.assertEqual(result["confirmed_recommendations"][0]
                         ["explanation"]["method"], "deterministic")

    def test_ai_generation_is_bounded_but_all_items_get_fallback_text(self):
        entries = [item(f"CS F21{index}", confirmed=False) for index in range(3)]
        client = FakeClient({"explanations": []})
        result = RecommendationExplanationService(
            client, max_ai_explanations=1).explain(results(verification=entries))
        self.assertEqual(len(result["verification_required"]), 3)
        self.assertTrue(all(entry["explanation"]["method"] == "deterministic"
                            for entry in result["verification_required"]))
        prompt_payload = client.calls[0][0]
        self.assertIn("CS F210", prompt_payload)
        self.assertNotIn("CS F211", prompt_payload)

    def test_input_is_not_mutated(self):
        original = results(confirmed=[item()])
        RecommendationExplanationService(FakeClient(generated())).explain(original)
        self.assertNotIn("explanation", original["confirmed_recommendations"][0])

    def test_json_compatible(self):
        result = RecommendationExplanationService(
            FakeClient(error=GeminiError("offline"))).explain(
                results(confirmed=[item()]))
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
