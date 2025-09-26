import json
import unittest

from backend.student_preferences import StudentPreferences, normalize_student_preferences


def issue_codes(result):
    return {issue["code"] for issue in result["validation"]["issues"]}


class StudentPreferencesTests(unittest.TestCase):
    def test_minimal_valid_input(self):
        result = normalize_student_preferences({"interests": ["machine learning"]})
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["interests"][0]["value"], "machine learning")

    def test_fully_populated_input(self):
        result = normalize_student_preferences({
            "interests": ["Economics"],
            "preferred_topics": ["Optimization"],
            "avoided_topics": ["Hardware"],
            "career_goals": ["Quantitative Finance"],
            "workload_preference": "moderate",
            "evaluation_preferences": {"prefer_projects": True},
            "raw_query": "Suggest a project course",
        })
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["career_goals"][0]["value"], "quantitative finance")

    def test_empty_input_is_valid_and_unspecified(self):
        result = normalize_student_preferences({})
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["workload_preference"]["value"], "unspecified")
        self.assertEqual(result["evaluation_preferences"], {})

    def test_interests_are_trimmed_and_case_normalized(self):
        result = normalize_student_preferences({"interests": ["  Machine   Learning "]})
        item = result["interests"][0]
        self.assertEqual(item["value"], "machine learning")
        self.assertEqual(item["original_value"], "Machine Learning")

    def test_duplicates_are_removed_case_insensitively(self):
        result = normalize_student_preferences(
            {"interests": ["Algorithms", " algorithms ", "ALGORITHMS"]})
        self.assertEqual(len(result["interests"]), 1)

    def test_preferred_topics_are_distinct(self):
        result = normalize_student_preferences({"preferred_topics": ["Statistics"]})
        self.assertEqual(result["preferred_topics"][0]["value"], "statistics")
        self.assertEqual(result["interests"], [])

    def test_avoided_topics_are_explicit(self):
        result = normalize_student_preferences({"avoided_topics": ["Hardware"]})
        self.assertEqual(result["avoided_topics"][0]["value"], "hardware")

    def test_career_goals_are_preserved_without_course_claims(self):
        result = normalize_student_preferences({"career_goals": ["Research"]})
        self.assertEqual(result["career_goals"][0]["original_value"], "Research")
        self.assertNotIn("courses", result["career_goals"][0])

    def test_valid_workload_enum(self):
        result = normalize_student_preferences({"workload_preference": "High"})
        self.assertEqual(result["workload_preference"]["value"], "high")

    def test_invalid_workload_enum(self):
        result = normalize_student_preferences({"workload_preference": "extreme"})
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("unsupported_workload_preference", issue_codes(result))
        self.assertEqual(result["workload_preference"]["value"], "unspecified")

    def test_evaluation_preferences(self):
        result = normalize_student_preferences({"evaluation_preferences": {
            "prefer_projects": True,
            "avoid_midsemester_exam": False,
        }})
        self.assertTrue(result["evaluation_preferences"]["prefer_projects"]["value"])
        self.assertFalse(result["evaluation_preferences"]
                         ["avoid_midsemester_exam"]["value"])

    def test_preference_priority(self):
        result = normalize_student_preferences({"interests": [{
            "value": "Probability", "priority": "high"}]})
        self.assertEqual(result["interests"][0]["priority"], "high")

    def test_hard_and_soft_representation(self):
        result = normalize_student_preferences({
            "preferred_topics": [{"value": "Statistics", "constraint": "soft"}],
            "avoided_topics": [{"value": "Hardware", "constraint": "hard"}],
        })
        self.assertEqual(result["preferred_topics"][0]["constraint"], "soft")
        self.assertEqual(result["avoided_topics"][0]["constraint"], "hard")

    def test_raw_query_is_preserved_verbatim(self):
        query = "  Suggest an AI-related course  "
        result = normalize_student_preferences({"raw_query": query})
        self.assertEqual(result["raw_query"], query)

    def test_empty_string_is_rejected_and_retained(self):
        result = normalize_student_preferences({"interests": ["   "]})
        self.assertIn("empty_preference_value", issue_codes(result))
        self.assertEqual(result["uninterpreted_inputs"][0]["value"], "   ")

    def test_wrong_data_types_are_reported(self):
        result = normalize_student_preferences({
            "interests": "machine learning",
            "evaluation_preferences": [],
            "raw_query": 123,
        })
        self.assertFalse(result["validation"]["is_valid"])
        self.assertGreaterEqual(result["validation"]["error_count"], 3)

    def test_exact_preferred_avoided_conflict(self):
        result = normalize_student_preferences({
            "preferred_topics": ["AI"], "avoided_topics": [" ai "]})
        self.assertIn("conflicting_topic_preference", issue_codes(result))
        self.assertFalse(result["validation"]["is_valid"])

    def test_no_semantic_synonym_inference(self):
        result = normalize_student_preferences({
            "preferred_topics": ["AI"], "avoided_topics": ["machine learning"]})
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["preferred_topics"][0]["value"], "ai")
        self.assertEqual(result["avoided_topics"][0]["value"], "machine learning")

    def test_unspecified_does_not_mean_negative(self):
        result = normalize_student_preferences({"interests": ["Economics"]})
        self.assertEqual(result["avoided_topics"], [])
        self.assertEqual(result["evaluation_preferences"], {})

    def test_json_serialization(self):
        model = StudentPreferences.from_dict({"interests": ["Algorithms"]})
        result = model.to_dict()
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_invalid_priority_is_reported(self):
        result = normalize_student_preferences({"interests": [{
            "value": "Algorithms", "priority": "urgent"}]})
        self.assertIn("invalid_preference_priority", issue_codes(result))
        self.assertEqual(result["interests"], [])

    def test_academic_policy_is_not_introduced(self):
        result = normalize_student_preferences({"interests": ["Algorithms"]})
        serialized = json.dumps(result)
        for forbidden in ("eligibility_state", "prerequisite_state",
                          "recommendation_safe", "requirement_filter_state"):
            self.assertNotIn(forbidden, serialized)

    def test_unsupported_input_is_retained(self):
        result = normalize_student_preferences({"preferred_instructor": "Professor X"})
        self.assertTrue(result["validation"]["is_valid"])
        self.assertIn("unsupported_preference_field", issue_codes(result))
        self.assertEqual(result["uninterpreted_inputs"][0]["value"], "Professor X")

    def test_invalid_top_level_type_raises(self):
        with self.assertRaises(TypeError):
            StudentPreferences.from_dict([])


if __name__ == "__main__":
    unittest.main()
