import json
import unittest
from pathlib import Path

from backend.academic_history import CourseCatalogue, resolve_academic_history
from backend.category_progress import build_category_progress
from backend.programme_requirements import AcademicRuleCatalogue, resolve_programme_requirements
from backend.requirement_completion import evaluate_requirement_completion
from backend.student_profile import StudentProfile


def record(rule_id, state, category="discipline_core", role="primary",
           programme="COMPUTER SCIENCE", measurement=None, source_category=None, **updates):
    data = {
        "rule_id": rule_id,
        "requested_programme_role": role,
        "scope": {"programme": programme},
        "rule_type": "required_course",
        "category": source_category if source_category is not None else (
            "Core Courses" if category else None),
        "normalized_category": category,
        "course_code": "CS F211",
        "course_title": "Data Structures",
        "alternatives": None,
        "completion_status": state,
        "completed_matching_courses": [],
        "ongoing_matching_courses": [],
        "measurements": measurement or {},
        "unevaluable_reason": None,
        "source_document": "Bulletin",
        "source_heading": programme,
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": "CS F211 Data Structures"}],
    }
    data.update(updates)
    return data


def completion(*records, excluded=None, incomplete=None):
    output = {
        "satisfied_requirements": [],
        "partially_satisfied_requirements": [],
        "remaining_requirements": [],
        "unevaluable_requirements": [],
        "excluded_rules": excluded or [],
        "incomplete_data": incomplete or {},
        "validation": {"is_valid": True, "issues": [], "error_count": 0,
                       "warning_count": 0},
    }
    for item in records:
        output[f"{item['completion_status']}_requirements"].append(item)
    return output


def categories(result, role="primary"):
    programme = next(item for item in result["programme_progress"]
                     if item["requested_programme_role"] == role)
    return {item["normalized_category"]: item for item in programme["categories"]}


def issue_codes(result):
    return [item["code"] for item in result["validation"]["issues"]]


class CategoryProgressTests(unittest.TestCase):
    def test_groups_satisfied_and_remaining_in_one_category(self):
        result = build_category_progress(completion(
            record("r1", "satisfied"), record("r2", "remaining", course_code="CS F213")))
        core = categories(result)["discipline_core"]
        self.assertEqual(core["counts"], {"satisfied": 1, "partially_satisfied": 0,
                                         "remaining": 1, "unevaluable": 0, "total": 2})
        self.assertEqual(core["source_categories"], ["Core Courses"])

    def test_keeps_separate_categories(self):
        result = build_category_progress(completion(
            record("r1", "satisfied"),
            record("r2", "remaining", "discipline_elective", source_category="Electives")))
        self.assertEqual(set(categories(result)), {"discipline_core", "discipline_elective"})

    def test_preserves_uncategorized_requirement_without_inference(self):
        result = build_category_progress(completion(record(
            "r1", "remaining", None, course_code="CS F211",
            course_title="Computer Programming", department="Computer Science")))
        programme = result["programme_progress"][0]
        self.assertEqual(programme["categories"], [])
        self.assertEqual(programme["uncategorized_requirements"][0]["course_code"], "CS F211")

    def test_primary_and_second_programmes_are_separate(self):
        result = build_category_progress(completion(
            record("r1", "satisfied"),
            record("r2", "remaining", role="second", programme="MATHEMATICS")))
        self.assertEqual([(item["requested_programme_role"], item["programme"])
                          for item in result["programme_progress"]],
                         [("primary", "COMPUTER SCIENCE"), ("second", "MATHEMATICS")])

    def test_ongoing_course_does_not_increase_completed_progress(self):
        ongoing = [{"normalized_course_code": "CS F211"}]
        measure = {"course_count": {"required": 1, "completed": 0, "remaining": 1}}
        result = build_category_progress(completion(record(
            "r1", "remaining", measurement=measure, ongoing_matching_courses=ongoing)))
        core = categories(result)["discipline_core"]
        self.assertEqual(core["numeric_progress"]["course_count"]["completed"], 0)
        self.assertEqual(core["remaining_requirements"][0]["ongoing_matching_courses"], ongoing)

    def test_partially_satisfied_category(self):
        result = build_category_progress(completion(record("r1", "partially_satisfied")))
        self.assertEqual(categories(result)["discipline_core"]["counts"]["partially_satisfied"], 1)

    def test_unevaluable_requirement_is_retained(self):
        result = build_category_progress(completion(record(
            "r1", "unevaluable", unevaluable_reason="missing_course_category_membership")))
        item = categories(result)["discipline_core"]["unevaluable_requirements"][0]
        self.assertEqual(item["unevaluable_reason"], "missing_course_category_membership")

    def test_safely_aggregates_distinct_required_courses(self):
        measure = {"course_count": {"required": 1, "completed": 1, "remaining": 0}}
        result = build_category_progress(completion(
            record("r1", "satisfied", measurement=measure, course_code="CS F211"),
            record("r2", "satisfied", measurement=measure, course_code="CS F213")))
        progress = categories(result)["discipline_core"]["numeric_progress"]["course_count"]
        self.assertEqual((progress["required"], progress["completed"], progress["remaining"]),
                         (2, 2, 0))

    def test_incompatible_numeric_measurements_remain_individual(self):
        measure = {"course_count": {"required": 2, "completed": 1, "remaining": 1}}
        result = build_category_progress(completion(
            record("r1", "partially_satisfied", measurement=measure,
                   rule_type="category_total"),
            record("r2", "remaining", measurement=measure, rule_type="quantity")))
        progress = categories(result)["discipline_core"]["numeric_progress"]["course_count"]
        self.assertEqual(progress["aggregation_status"], "individual_only")
        self.assertIn("incompatible_numeric_measurements", issue_codes(result))

    def test_course_counts_and_units_are_never_added_together(self):
        measure = {"course_count": {"required": 2, "completed": 1, "remaining": 1},
                   "units": {"required": 8, "completed": 4, "remaining": 4}}
        result = build_category_progress(completion(record(
            "r1", "partially_satisfied", measurement=measure, rule_type="category_total")))
        progress = categories(result)["discipline_core"]["numeric_progress"]
        self.assertEqual(progress["course_count"]["required"], 2)
        self.assertEqual(progress["units"]["required"], 8)

    def test_duplicate_rule_is_not_double_counted(self):
        result = build_category_progress(completion(
            record("r1", "satisfied"), record("r1", "remaining")))
        self.assertEqual(categories(result)["discipline_core"]["counts"]["total"], 1)
        self.assertEqual(len(result["duplicate_requirements"]), 1)
        self.assertIn("duplicate_requirement_id", issue_codes(result))

    def test_malformed_record_is_reported(self):
        data = completion()
        data["remaining_requirements"] = ["bad"]
        result = build_category_progress(data)
        self.assertFalse(result["validation"]["is_valid"])
        self.assertIn("malformed_completion_record", issue_codes(result))

    def test_unexpected_state_is_reported(self):
        data = completion()
        data["remaining_requirements"] = [record("r1", "unknown")]
        result = build_category_progress(data)
        self.assertIn("unexpected_completion_state", issue_codes(result))

    def test_upstream_incomplete_data_is_propagated(self):
        incomplete = {"has_incomplete_data": True, "unscoped_course_rule_count": 93}
        result = build_category_progress(completion(incomplete=incomplete))
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertIn("upstream_incomplete_data", issue_codes(result))

    def test_descriptive_and_unresolved_rules_are_preserved(self):
        excluded = [
            {"rule_id": "d1", "reason": "descriptive_or_non_executable"},
            {"rule_id": "u1", "reason": "unscoped_or_unresolved"},
        ]
        result = build_category_progress(completion(excluded=excluded))
        self.assertEqual(result["descriptive_information"][0]["rule_id"], "d1")
        self.assertEqual(result["excluded_unresolved_rules"][0]["rule_id"], "u1")

    def test_does_not_invent_curriculum_abbreviations(self):
        result = build_category_progress(completion(record(
            "r1", "remaining", "discipline_elective",
            source_category="Discipline Electives")))
        serialized = json.dumps(result)
        for invented in ("CDC", "DEL", "HUEL", "OPEL"):
            self.assertNotIn(invented, serialized)

    def test_humanities_pool_remains_descriptive(self):
        pool = {"rule_id": "h1", "reason": "descriptive_or_non_executable",
                "scope_kind": "institutional", "category": "Humanities Electives",
                "normalized_category": "humanities_elective", "course_code": "HSS F221"}
        result = build_category_progress(completion(excluded=[pool]))
        self.assertEqual(result["programme_progress"], [])
        self.assertEqual(result["descriptive_information"][0]["course_code"], "HSS F221")

    def test_missing_programme_context_remains_unresolved(self):
        result = build_category_progress(completion(record(
            "r1", "remaining", role=None, programme=None)))
        self.assertEqual(len(result["unresolved_requirements"]), 1)
        self.assertIn("requirement_missing_programme_role", issue_codes(result))
        self.assertIn("requirement_missing_programme_scope", issue_codes(result))

    def test_output_is_json_serializable(self):
        result = build_category_progress(completion(record("r1", "remaining")))
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_real_phase_three_chain(self):
        root = Path(__file__).resolve().parents[1]
        profile = StudentProfile.from_dict({
            "programme": "COMPUTER SCIENCE", "current_academic_year": 2,
            "current_semester": 1, "completed_courses": ["CS F213"]})
        academic_history = resolve_academic_history(
            profile, CourseCatalogue.load(root / "data/processed/courses.json"))
        requirements = resolve_programme_requirements(
            profile, AcademicRuleCatalogue.load(root / "data/processed/academic_rules.json"))
        evaluated = evaluate_requirement_completion(academic_history, requirements)
        result = build_category_progress(evaluated)

        self.assertEqual(result["programme_progress"][0]["requested_programme_role"], "primary")
        self.assertTrue(result["programme_progress"][0]["categories"])
        grouped_ids = {item["rule_id"]
                       for category in result["programme_progress"][0]["categories"]
                       for state in ("satisfied", "partially_satisfied", "remaining", "unevaluable")
                       for item in category[f"{state}_requirements"]}
        evaluated_ids = {item["rule_id"] for name in (
            "satisfied_requirements", "partially_satisfied_requirements",
            "remaining_requirements", "unevaluable_requirements")
                         for item in evaluated[name] if item["normalized_category"]}
        self.assertEqual(grouped_ids, evaluated_ids)
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertEqual(len(result["excluded_unresolved_rules"]), 93)


if __name__ == "__main__":
    unittest.main()
