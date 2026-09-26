import json
import unittest

from backend.intent_parser import IntentExtractor, StudentIntentParser, parse_student_intent


def values(result, field):
    return [item["value"] for item in result["preferences"][field]]


def evidence_types(result):
    return {item["preference_type"] for item in result["extraction_evidence"]}


class FakeExtractor(IntentExtractor):
    method = "fake"

    def __init__(self, output):
        self.output = output

    def extract(self, query):
        return self.output


class IntentParserTests(unittest.TestCase):
    def test_simple_interest(self):
        result = parse_student_intent("I am interested in economics.")
        self.assertEqual(values(result, "interests"), ["economics"])

    def test_preferred_topic(self):
        result = parse_student_intent("I want to study stochastic processes.")
        self.assertEqual(values(result, "preferred_topics"), ["stochastic processes"])

    def test_avoided_topic(self):
        result = parse_student_intent("I don't want hardware courses.")
        self.assertEqual(values(result, "avoided_topics"), ["hardware"])

    def test_multiple_interests(self):
        result = parse_student_intent("I like economics and algorithms.")
        self.assertEqual(values(result, "interests"), ["economics", "algorithms"])

    def test_multiple_preferred_topics(self):
        result = parse_student_intent("I want to study machine learning or probability.")
        self.assertEqual(values(result, "preferred_topics"),
                         ["machine learning", "probability"])

    def test_explicit_career_goal(self):
        result = parse_student_intent("I want to work in quantitative finance.")
        self.assertEqual(values(result, "career_goals"), ["quantitative finance"])

    def test_low_workload(self):
        result = parse_student_intent("I want a light workload.")
        self.assertEqual(result["preferences"]["workload_preference"]["value"], "low")

    def test_moderate_workload(self):
        result = parse_student_intent("A moderate workload is fine.")
        self.assertEqual(result["preferences"]["workload_preference"]["value"],
                         "moderate")

    def test_high_workload(self):
        result = parse_student_intent("I don't mind a heavy workload.")
        self.assertEqual(result["preferences"]["workload_preference"]["value"], "high")

    def test_project_preference(self):
        result = parse_student_intent("I'd prefer courses with projects.")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["prefer_projects"]["value"])

    def test_quiz_preference(self):
        result = parse_student_intent("I like quizzes.")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["prefer_quizzes"]["value"])

    def test_continuous_evaluation_preference(self):
        result = parse_student_intent("I prefer continuous evaluation.")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["prefer_continuous_evaluation"]["value"])

    def test_avoid_heavy_exams(self):
        result = parse_student_intent("I want to avoid exam-heavy courses.")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["avoid_heavy_exams"]["value"])

    def test_attendance_preference(self):
        result = parse_student_intent("I don't want attendance-heavy courses.")
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["avoid_attendance_heavy"]["value"])

    def test_soft_preference_wording(self):
        result = parse_student_intent("I'd prefer machine learning.")
        self.assertEqual(result["preferences"]["preferred_topics"][0]["constraint"],
                         "soft")

    def test_explicit_hard_constraint_wording(self):
        result = parse_student_intent("I absolutely do not want hardware.")
        self.assertEqual(result["preferences"]["avoided_topics"][0]["constraint"],
                         "hard")

    def test_default_priority(self):
        result = parse_student_intent("I am interested in economics.")
        self.assertEqual(result["preferences"]["interests"][0]["priority"], "medium")

    def test_explicit_high_priority(self):
        result = parse_student_intent("Machine learning is very important to me.")
        self.assertEqual(result["preferences"]["interests"][0]["priority"], "high")

    def test_raw_query_is_preserved_exactly(self):
        query = "  I am interested in AI.  "
        result = parse_student_intent(query)
        self.assertEqual(result["original_query"], query)
        self.assertEqual(result["preferences"]["raw_query"], query)

    def test_evidence_preserves_source_phrase(self):
        result = parse_student_intent("I want to study probability.")
        evidence = result["extraction_evidence"][0]
        self.assertEqual(evidence["source_phrase"], "I want to study probability")
        self.assertEqual(evidence["status"], "explicit")

    def test_ambiguous_query_extracts_nothing(self):
        result = parse_student_intent("I want a good course.")
        self.assertEqual(result["extraction_evidence"], [])
        self.assertEqual(result["uninterpreted"][0]["reason"],
                         "no_reliable_preference_extracted")

    def test_unsupported_timetable_request_is_preserved(self):
        result = parse_student_intent("I don't want an 8 AM class.")
        self.assertEqual(result["uninterpreted"][0]["reason"], "timetable_request")
        self.assertEqual(result["preferences"]["avoided_topics"], [])

    def test_mixed_supported_and_unsupported_query(self):
        result = parse_student_intent(
            "I don't want an 8 AM class, but I'd prefer machine learning.")
        self.assertEqual(values(result, "preferred_topics"), ["machine learning"])
        self.assertEqual(result["uninterpreted"][0]["reason"], "timetable_request")

    def test_contradictory_topics_reach_task_five_one_validation(self):
        result = parse_student_intent(
            "I want to study machine learning. I want to avoid machine learning.")
        self.assertFalse(result["preferences"]["validation"]["is_valid"])
        self.assertIn("conflicting_topic_preference",
                      {item["code"] for item in result["preferences"]
                       ["validation"]["issues"]})

    def test_no_synonym_expansion(self):
        result = parse_student_intent("I am interested in AI.")
        self.assertEqual(values(result, "interests"), ["ai"])
        self.assertNotIn("machine learning", json.dumps(result).casefold())

    def test_no_career_inference_from_interest(self):
        result = parse_student_intent("I like economics.")
        self.assertEqual(result["preferences"]["career_goals"], [])

    def test_no_avoidance_inference(self):
        result = parse_student_intent("I prefer machine learning.")
        self.assertEqual(result["preferences"]["avoided_topics"], [])

    def test_empty_query_fails_safely(self):
        result = parse_student_intent("")
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("query_empty", {item["code"] for item in result["parser_diagnostics"]})

    def test_whitespace_query_fails_safely(self):
        result = parse_student_intent("   ")
        self.assertFalse(result["validation"]["is_valid"])

    def test_non_string_query_fails_safely(self):
        result = parse_student_intent(123)
        self.assertFalse(result["validation"]["is_valid"])
        self.assertEqual(result["uninterpreted"][0]["reason"], "query_not_text")

    def test_malformed_injected_extractor_output(self):
        parser = StudentIntentParser(FakeExtractor("bad"))
        result = parser.parse("A query")
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("malformed_extractor_output",
                      {item["code"] for item in result["parser_diagnostics"]})

    def test_fake_injected_extractor(self):
        extractor = FakeExtractor({
            "preferences": {"interests": ["Robotics"]},
            "evidence": [{"preference_type": "interests", "value": "Robotics",
                          "status": "inferred", "source_phrase": "robots"}],
            "uninterpreted": [],
        })
        result = StudentIntentParser(extractor).parse("robots")
        self.assertEqual(values(result, "interests"), ["robotics"])
        self.assertEqual(result["extraction_method"], "fake")
        self.assertEqual(result["extraction_evidence"][0]["status"], "inferred")

    def test_injected_output_still_uses_task_five_one_validation(self):
        extractor = FakeExtractor({
            "preferences": {"interests": [{"value": "AI", "priority": "urgent"}]},
            "evidence": [], "uninterpreted": []})
        result = StudentIntentParser(extractor).parse("AI")
        self.assertFalse(result["validation"]["is_valid"])
        self.assertEqual(result["preferences"]["interests"], [])

    def test_json_compatibility(self):
        result = parse_student_intent("I like algorithms.")
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_no_phase_four_fields(self):
        result = parse_student_intent("I like algorithms.")
        serialized = json.dumps(result)
        for field in ("eligibility_state", "recommendation_safe",
                      "candidate_pool_state", "requirement_filter_state"):
            self.assertNotIn(field, serialized)

    def test_mixed_query_extracts_each_literal_preference(self):
        query = ("I want something related to machine learning or probability, "
                 "preferably with projects. I don't want hardware courses and "
                 "I'd like a moderate workload.")
        result = parse_student_intent(query)
        self.assertEqual(values(result, "preferred_topics"),
                         ["machine learning", "probability"])
        self.assertEqual(values(result, "avoided_topics"), ["hardware"])
        self.assertTrue(result["preferences"]["evaluation_preferences"]
                        ["prefer_projects"]["value"])
        self.assertEqual(result["preferences"]["workload_preference"]["value"],
                         "moderate")

    def test_quant_phrase_is_literal_goal_without_expansion(self):
        result = parse_student_intent("I want something useful for quantitative finance.")
        self.assertEqual(values(result, "career_goals"), ["quantitative finance"])
        serialized = json.dumps(result).casefold()
        for inferred in ("probability", "statistics", "machine learning", "economics"):
            self.assertNotIn(inferred, serialized)


if __name__ == "__main__":
    unittest.main()
