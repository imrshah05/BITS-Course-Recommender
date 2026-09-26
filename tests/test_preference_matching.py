import json
import unittest
from copy import deepcopy

from backend.preference_matching import (
    CoursePreferenceMatcher,
    LexicalMatchingStrategy,
    MatchingStrategy,
    match_course_preferences,
)


def source(text="Evidence", page=1, filename="course.pdf"):
    return {"source_file": filename, "page_number": page, "text": text}


def profile(title="Data Structures", content="Trees and graph algorithms",
            evaluation=None, exams=None, uncertainty=None, code="CS F211"):
    title_sources = [source(title)] if title else []
    items = ([{"heading": "Course content", "text": content,
               "sources": [source(content, 2)]}] if content else [])
    return {
        "course_code": code,
        "title": {"display_value": title, "values": [title] if title else [],
                  "sources": title_sources},
        "content": deepcopy(items), "topics": deepcopy(items),
        "evaluation": evaluation or [],
        "exam_information": exams or {"midsemester": [], "comprehensive": []},
        "attendance_or_makeup": {"attendance": [], "makeup": []},
        "data_availability": {"title": bool(title), "content": bool(content),
                              "topics": bool(content), "evaluation": bool(evaluation),
                              "exam_information": bool(exams)},
        "source_evidence": {"course_identity": [source(code)]},
        "uncertainty": uncertainty or {"missing_fields": [], "conflicts": {}},
    }


def prefs(**kwargs):
    return kwargs


def states(result):
    return [item["assessment_state"] for item in result["preference_assessments"]]


class AlwaysStrategy(MatchingStrategy):
    name = "always"

    def find_matches(self, phrase, fields):
        if not fields:
            return []
        return [{"course_field": fields[0]["course_field"], "matched_text": phrase,
                 "source_references": deepcopy(fields[0].get("sources") or [])}]


class PreferenceMatchingTests(unittest.TestCase):
    def test_exact_title_match(self):
        self.assertEqual(match_course_preferences(
            prefs(preferred_topics=["Data Structures"]), profile())["match_state"],
            "strong_match")

    def test_case_insensitive_title_match(self):
        result = match_course_preferences(prefs(preferred_topics=["data structures"]),
                                          profile(title="DATA STRUCTURES"))
        self.assertEqual(states(result), ["matched"])

    def test_syllabus_match(self):
        result = match_course_preferences(prefs(preferred_topics=["graph algorithms"]),
                                          profile())
        self.assertEqual(states(result), ["matched"])

    def test_explicit_topic_match(self):
        result = match_course_preferences(prefs(preferred_topics=["trees"]), profile())
        self.assertTrue(any(e["course_field"].startswith("topics")
                            for e in result["positive_evidence"]))

    def test_multiword_phrase(self):
        result = match_course_preferences(prefs(interests=["machine learning"]),
                                          profile(content="Applied machine learning"))
        self.assertEqual(states(result), ["matched"])

    def test_punctuation_normalization(self):
        result = match_course_preferences(prefs(interests=["machine learning"]),
                                          profile(content="machine-learning methods"))
        self.assertEqual(states(result), ["matched"])

    def test_whitespace_normalization(self):
        result = match_course_preferences(prefs(interests=["machine   learning"]),
                                          profile(content="machine\n learning"))
        self.assertEqual(states(result), ["matched"])

    def test_no_synonym_expansion(self):
        result = match_course_preferences(prefs(interests=["AI"]),
                                          profile(content="Machine learning"))
        self.assertEqual(states(result), ["not_matched"])

    def test_no_abbreviation_expansion(self):
        result = match_course_preferences(prefs(interests=["ML"]),
                                          profile(content="Machine learning"))
        self.assertEqual(states(result), ["not_matched"])

    def test_short_token_false_positive_prevention(self):
        result = match_course_preferences(prefs(interests=["AI"]),
                                          profile(content="Training algorithms"))
        self.assertEqual(states(result), ["not_matched"])

    def test_preferred_topic_positive_match(self):
        result = match_course_preferences(prefs(preferred_topics=["graph"]), profile())
        self.assertEqual(result["matched_preferences"][0]["preference_category"],
                         "preferred_topics")

    def test_interest_positive_match(self):
        result = match_course_preferences(prefs(interests=["trees"]), profile())
        self.assertEqual(result["matched_preferences"][0]["preference_category"],
                         "interests")

    def test_avoided_topic_conflict(self):
        result = match_course_preferences(prefs(avoided_topics=["hardware"]),
                                          profile(content="Computer hardware organization"))
        self.assertEqual(result["match_state"], "conflict")

    def test_hard_avoided_topic_conflict(self):
        result = match_course_preferences(
            prefs(avoided_topics=[{"value": "hardware", "constraint": "hard"}]),
            profile(content="Hardware design"))
        self.assertTrue(result["aggregation"]["has_hard_conflict"])

    def test_soft_avoided_topic_conflict(self):
        result = match_course_preferences(prefs(avoided_topics=["hardware"]),
                                          profile(content="Hardware design"))
        self.assertFalse(result["aggregation"]["has_hard_conflict"])

    def test_missing_descriptive_evidence(self):
        result = match_course_preferences(prefs(interests=["algorithms"]),
                                          profile(title=None, content=None))
        self.assertEqual(states(result), ["unassessable"])

    def test_evidence_exists_but_not_matched(self):
        result = match_course_preferences(prefs(interests=["economics"]), profile())
        self.assertEqual(states(result), ["not_matched"])

    def test_multiple_positive_preferences(self):
        result = match_course_preferences(
            prefs(interests=["trees"], preferred_topics=["graph"]), profile())
        self.assertEqual(len(result["matched_preferences"]), 2)

    def test_partial_match(self):
        result = match_course_preferences(prefs(interests=["trees", "economics"]),
                                          profile())
        self.assertEqual(result["match_state"], "partial_match")

    def test_match_with_unassessable_positive_is_partial(self):
        result = match_course_preferences(
            prefs(interests=["trees"], career_goals=["research"]), profile())
        self.assertEqual(result["match_state"], "partial_match")

    def test_strong_match(self):
        result = match_course_preferences(prefs(interests=["trees", "graph"]), profile())
        self.assertEqual(result["match_state"], "strong_match")

    def test_no_match(self):
        result = match_course_preferences(prefs(interests=["economics"]), profile())
        self.assertEqual(result["match_state"], "no_match")

    def test_insufficient_evidence(self):
        result = match_course_preferences(prefs(interests=["economics"]),
                                          profile(title=None, content=None))
        self.assertEqual(result["match_state"], "insufficient_evidence")

    def test_priority_weighting(self):
        result = match_course_preferences(
            prefs(interests=[{"value": "trees", "priority": "high"}]), profile())
        self.assertEqual(result["aggregation"]["matched_priority_weight"], 3)

    def test_project_preference_with_explicit_evidence(self):
        evaluation = [{"name": "Project", "weightage": "40%",
                       "sources": [source("Project 40%", 2)]}]
        result = match_course_preferences(
            prefs(evaluation_preferences={"prefer_projects": True}),
            profile(evaluation=evaluation))
        self.assertEqual(states(result), ["matched"])

    def test_quiz_preference_with_explicit_evidence(self):
        evaluation = [{"name": "Quiz", "sources": [source("Quiz", 2)]}]
        result = match_course_preferences(
            prefs(evaluation_preferences={"prefer_quizzes": True}),
            profile(evaluation=evaluation))
        self.assertEqual(states(result), ["matched"])

    def test_evaluation_preference_missing_evidence(self):
        result = match_course_preferences(
            prefs(evaluation_preferences={"prefer_projects": True}), profile())
        self.assertEqual(states(result), ["unassessable"])

    def test_heavy_exam_remains_unassessable(self):
        result = match_course_preferences(
            prefs(evaluation_preferences={"avoid_heavy_exams": True}),
            profile(evaluation=[{"name": "Exam", "sources": [source()]}]))
        self.assertEqual(states(result), ["unassessable"])

    def test_workload_remains_unassessable(self):
        result = match_course_preferences(prefs(workload_preference="low"), profile())
        self.assertEqual(states(result), ["unassessable"])

    def test_career_goal_remains_unassessable(self):
        result = match_course_preferences(prefs(career_goals=["quantitative finance"]),
                                          profile(content="Probability and economics"))
        self.assertEqual(states(result), ["unassessable"])

    def test_source_evidence_preservation(self):
        result = match_course_preferences(prefs(interests=["trees"]), profile())
        self.assertEqual(result["source_references"][0]["source_file"], "course.pdf")

    def test_uncertainty_preservation(self):
        uncertainty = {"needs_verification": True,
                       "conflicts": {"course_title": ["A", "B"]}}
        result = match_course_preferences(prefs(interests=["trees"]),
                                          profile(uncertainty=uncertainty))
        self.assertEqual(result["uncertainty"], uncertainty)

    def test_multiple_source_records_preserved(self):
        item = profile()
        item["uncertainty"]["has_multiple_source_records"] = True
        item["title"]["sources"].append(source("Data Structures", 1, "two.pdf"))
        result = match_course_preferences(prefs(interests=["data structures"]), item)
        self.assertEqual(len(result["source_references"]), 2)

    def test_malformed_preference_input(self):
        result = match_course_preferences([], profile())
        self.assertFalse(result["validation"]["is_valid"])

    def test_malformed_semantic_profile(self):
        result = match_course_preferences(prefs(interests=["trees"]), [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_duplicate_course_identity_detection(self):
        result = CoursePreferenceMatcher().match_all(
            prefs(interests=["trees"]), [profile(), profile()])
        self.assertFalse(result["validation"]["is_valid"])
        self.assertEqual(result["validation"]["duplicate_course_codes"], ["CS F211"])

    def test_json_compatibility(self):
        result = match_course_preferences(prefs(interests=["trees"]), profile())
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_injected_matching_strategy(self):
        result = CoursePreferenceMatcher(AlwaysStrategy()).match_one(
            prefs(interests=["economics"]), profile())
        self.assertEqual(result["match_state"], "strong_match")
        self.assertEqual(result["matching_strategy"], "always")

    def test_no_eligibility_fields_introduced(self):
        serialized = json.dumps(match_course_preferences(
            prefs(interests=["trees"]), profile()))
        self.assertNotIn("eligibility_state", serialized)

    def test_no_recommendation_safe_field_invented(self):
        serialized = json.dumps(match_course_preferences(
            prefs(interests=["trees"]), profile()))
        self.assertNotIn("recommendation_safe", serialized)

    def test_deterministic_repeated_output(self):
        matcher = CoursePreferenceMatcher()
        first = matcher.match_one(prefs(interests=["trees"]), profile())
        second = matcher.match_one(prefs(interests=["trees"]), profile())
        self.assertEqual(first, second)

    def test_task_five_three_extraction_status_is_preserved(self):
        parsed = {
            "preferences": {"interests": ["trees"]},
            "extraction_evidence": [{"preference_type": "interests",
                                     "value": "trees", "status": "explicit",
                                     "source_phrase": "I like trees"}],
        }
        result = match_course_preferences(parsed, profile())
        assessment = result["preference_assessments"][0]
        self.assertEqual(assessment["extraction_status"], "explicit")
        self.assertEqual(assessment["preference_extraction_evidence"][0]
                         ["source_phrase"], "I like trees")


if __name__ == "__main__":
    unittest.main()
