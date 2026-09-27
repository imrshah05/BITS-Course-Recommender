from io import BytesIO
import json
import os
import socket
import unittest
from unittest.mock import patch

from backend.api import application
from backend.gemini import (
    GeminiClient,
    GeminiError,
    GeminiIntentExtractor,
    GeminiSemanticMatchingStrategy,
    configured_intent_parser,
    configured_recommendation_engine,
    gemini_configuration,
)
from backend.intent_parser import StudentIntentParser
from backend.preference_matching import CoursePreferenceMatcher


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


def gemini_response(value):
    return json.dumps({"candidates": [{"content": {"parts": [{
        "text": json.dumps(value)
    }]}}]}).encode()


def intent_output(**updates):
    value = {"preferences": [], "workload": None, "evaluation": []}
    value.update(updates)
    return value


def preference(category="interests", value="artificial intelligence",
               phrase="AI", priority="medium", constraint="soft"):
    return {"category": category, "value": value, "priority": priority,
            "constraint": constraint, "source_phrase": phrase}


def source(text="Machine learning", page=2):
    return {"source_file": "course.pdf", "page_number": page, "text": text}


def semantic_profile(content="Machine learning systems"):
    return {
        "course_code": "CS F401",
        "title": {"display_value": "Intelligent Systems",
                  "values": ["Intelligent Systems"],
                  "sources": [source("Intelligent Systems", 1)]},
        "content": [{"heading": "Content", "text": content,
                     "sources": [source(content)]}],
        "topics": [], "evaluation": [],
        "exam_information": {"midsemester": [], "comprehensive": []},
        "attendance_or_makeup": {"attendance": [], "makeup": []},
        "uncertainty": {"missing_fields": [], "conflicts": {}},
        "source_evidence": {"course_identity": [source("CS F401", 1)]},
    }


def api_request(path, payload):
    body = json.dumps(payload).encode()
    environ = {"PATH_INFO": path, "REQUEST_METHOD": "POST",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": BytesIO(body)}
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    response = b"".join(application(environ, start_response))
    return captured["status"], json.loads(response)


class GeminiClientTests(unittest.TestCase):
    def test_client_uses_server_side_key_and_structured_schema(self):
        captured = {}

        def transport(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return gemini_response({"answer": "ok"})

        client = GeminiClient(api_key="secret", model="test-model", transport=transport)
        result = client.generate_structured("prompt", {"type": "object"})
        request = captured["request"]
        payload = json.loads(request.data)
        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(request.get_header("X-goog-api-key"), "secret")
        self.assertNotIn("secret", request.full_url)
        self.assertEqual(payload["generationConfig"]["responseMimeType"],
                         "application/json")
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"],
                         {"type": "object"})

    def test_public_configuration_never_exposes_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "top-secret",
                                     "GEMINI_MODEL": "chosen-model"}, clear=False):
            configuration = gemini_configuration()
        self.assertEqual(configuration, {"configured": True, "model": "chosen-model"})
        self.assertNotIn("top-secret", json.dumps(configuration))

    def test_socket_timeout_becomes_fallback_error(self):
        def transport(request, timeout):
            raise socket.timeout("read timed out")

        client = GeminiClient(api_key="secret", transport=transport)
        with self.assertRaisesRegex(GeminiError, "unusable response"):
            client.generate_structured("prompt", {"type": "object"})


class GeminiIntentTests(unittest.TestCase):
    def test_structured_intent_is_validated_and_normalized(self):
        client = FakeClient(intent_output(preferences=[preference()]))
        result = StudentIntentParser(GeminiIntentExtractor(client)).parse(
            "I am interested in AI courses")
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["preferences"]["interests"][0]["value"],
                         "artificial intelligence")
        self.assertEqual(result["extraction_evidence"][0]["source_phrase"], "AI")
        self.assertEqual(result["extraction_evidence"][0]["status"], "ai_interpreted")

    def test_workload_and_evaluation_are_supported(self):
        output = intent_output(
            workload={"value": "low", "priority": "high", "constraint": "soft",
                      "source_phrase": "light workload"},
            evaluation=[{"key": "avoid_midsemester_exam", "value": True,
                         "priority": "medium", "constraint": "hard",
                         "source_phrase": "must have no midsem"}])
        query = "I want a light workload and must have no midsem"
        result = configured_intent_parser(FakeClient(output)).parse(query)
        self.assertEqual(result["preferences"]["workload_preference"]["value"], "low")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["avoid_midsemester_exam"]["value"])

    def test_unexpected_academic_field_triggers_deterministic_fallback(self):
        output = intent_output(preferences=[preference()])
        output["eligibility_state"] = "eligible"
        result = configured_intent_parser(FakeClient(output)).parse("I like algorithms.")
        self.assertEqual(result["preferences"]["interests"][0]["value"], "algorithms")
        self.assertIn("gemini_unavailable_or_invalid",
                      {item["reason"] for item in result["uninterpreted"]})
        self.assertNotIn("eligibility_state", json.dumps(result))

    def test_non_source_backed_evidence_triggers_fallback(self):
        output = intent_output(preferences=[preference(phrase="not in query")])
        result = configured_intent_parser(FakeClient(output)).parse("I like algorithms.")
        self.assertEqual(result["preferences"]["interests"][0]["value"], "algorithms")

    def test_missing_configuration_uses_deterministic_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            result = configured_intent_parser().parse("I want to study probability.")
        self.assertEqual(result["preferences"]["preferred_topics"][0]["value"],
                         "probability")
        self.assertIn("gemini_unavailable_or_invalid",
                      {item["reason"] for item in result["uninterpreted"]})


class GeminiMatchingTests(unittest.TestCase):
    def test_configured_engine_reuses_existing_pipeline_with_ai_adapters(self):
        engine = configured_recommendation_engine(FakeClient(intent_output()))
        self.assertIsInstance(engine.intent_parser.extractor, GeminiIntentExtractor)
        self.assertIsInstance(engine.preference_matcher.strategy,
                              GeminiSemanticMatchingStrategy)

    def test_semantic_match_uses_only_selected_source_field(self):
        strategy = GeminiSemanticMatchingStrategy(
            FakeClient({"matching_field_indexes": [2]}))
        result = CoursePreferenceMatcher(strategy).match_one(
            {"interests": ["AI"]}, semantic_profile())
        evidence = result["positive_evidence"][0]
        self.assertEqual(result["match_state"], "strong_match")
        self.assertEqual(evidence["matched_text"], "Machine learning systems")
        self.assertEqual(evidence["source_references"][0]["source_file"], "course.pdf")

    def test_duplicate_indexes_are_deduplicated_deterministically(self):
        strategy = GeminiSemanticMatchingStrategy(
            FakeClient({"matching_field_indexes": [2, 0, 2]}))
        result = CoursePreferenceMatcher(strategy).match_one(
            {"interests": ["AI"]}, semantic_profile())
        self.assertEqual([item["course_field"] for item in result["positive_evidence"]],
                         ["title", "content.text"])

    def test_invalid_index_falls_back_to_lexical_matching(self):
        strategy = GeminiSemanticMatchingStrategy(
            FakeClient({"matching_field_indexes": [99]}))
        result = CoursePreferenceMatcher(strategy).match_one(
            {"interests": ["machine learning"]}, semantic_profile())
        self.assertEqual(result["match_state"], "strong_match")
        self.assertEqual(result["positive_evidence"][0]["course_field"], "content.text")

    def test_invalid_index_cannot_invent_a_semantic_match(self):
        strategy = GeminiSemanticMatchingStrategy(
            FakeClient({"matching_field_indexes": [99]}))
        result = CoursePreferenceMatcher(strategy).match_one(
            {"interests": ["finance"]}, semantic_profile())
        self.assertEqual(result["match_state"], "no_match")

    def test_empty_model_match_is_respected(self):
        strategy = GeminiSemanticMatchingStrategy(
            FakeClient({"matching_field_indexes": []}))
        result = CoursePreferenceMatcher(strategy).match_one(
            {"interests": ["AI"]}, semantic_profile())
        self.assertEqual(result["match_state"], "no_match")

    def test_one_provider_failure_uses_fallback_for_rest_of_batch(self):
        client = FakeClient(error=GeminiError("timeout"))
        matcher = CoursePreferenceMatcher(GeminiSemanticMatchingStrategy(client))
        result = matcher.match_all(
            {"interests": ["machine learning"]},
            [semantic_profile(), {**semantic_profile(), "course_code": "CS F402"}])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(result["matches"]), 2)
        self.assertTrue(result["validation"]["is_valid"])


class GeminiDashboardTests(unittest.TestCase):
    def test_intent_endpoint_returns_validated_preferences(self):
        parser = configured_intent_parser(FakeClient(intent_output(
            preferences=[preference(value="ai", phrase="AI")])))
        with patch("backend.api._intent_parser", return_value=parser):
            status, response = api_request("/api/intent", {"query": "I like AI"})
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["intent"]["preferences"]["interests"][0]["value"],
                         "ai")

    def test_intent_endpoint_rejects_extra_fields(self):
        status, response = api_request("/api/intent", {"query": "AI", "admin": True})
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(response["error"], "invalid_intent_payload")

    def test_dashboard_contains_query_and_recommendation_regions(self):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        body = b"".join(application({"PATH_INFO": "/", "REQUEST_METHOD": "GET"},
                                    start_response)).decode()
        self.assertIn('id="recommendation-form"', body)
        self.assertIn('name="preference_query"', body)
        self.assertIn('id="recommendation-results"', body)


if __name__ == "__main__":
    unittest.main()
