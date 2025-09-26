import json
import unittest
from pathlib import Path

from backend.academic_history import CourseCatalogue, resolve_academic_history
from backend.programme_requirements import AcademicRuleCatalogue, resolve_programme_requirements
from backend.requirement_completion import evaluate_requirement_completion
from backend.student_profile import StudentProfile


def history(completed=None, ongoing=None, issues=None):
    def entry(code, status, units=None, catalogue_units=None):
        matches = [] if catalogue_units is None else [{"catalogue_units": catalogue_units}]
        return {"course_code": code, "normalized_course_code": code, "status": status,
                "reported_status": None, "grade": None, "student_units": units,
                "resolution_status": "matched", "catalogue_matches": matches}
    return {
        "completed_courses": [entry(item[0], "completed", *(item[1:]))
                              for item in (completed or [])],
        "ongoing_courses": [entry(item[0], "ongoing", *(item[1:])) for item in (ongoing or [])],
        "profile_validation": {"is_valid": True, "issues": []},
        "validation": {"is_valid": True, "issues": issues or []},
    }


def requirement(rule_id="r1", rule_type="required_course", **updates):
    rule = {
        "rule_id": rule_id,
        "requested_programme_role": "primary",
        "scope": {"programme": "COMPUTER SCIENCE"},
        "rule_type": rule_type,
        "normalized_category": None,
        "course_code": "CS F211" if rule_type == "required_course" else None,
        "alternatives": None,
        "classification": "deterministic",
        "needs_verification": False,
        "source_document": "Bulletin",
        "source_heading": "COMPUTER SCIENCE",
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": "CS F211 Data Structures 3 1 4"}],
        "required_count": None, "required_units": None,
        "min_count": None, "max_count": None,
        "min_units": None, "max_units": None,
    }
    rule.update(updates)
    return rule


def resolution(rules, descriptive=None, unresolved=None, incomplete=None, valid=True):
    return {
        "applicable_executable_requirements": rules,
        "applicable_descriptive_rules": descriptive or [],
        "excluded_unresolved_rules": unresolved or [],
        "incomplete_data": incomplete or {},
        "validation": {"is_valid": valid, "issues": []},
    }


def evaluate(rule_or_rules, completed=None, ongoing=None, **kwargs):
    rules = rule_or_rules if isinstance(rule_or_rules, list) else [rule_or_rules]
    return evaluate_requirement_completion(history(completed, ongoing, kwargs.pop("history_issues", None)),
                                           resolution(rules, **kwargs))


def issue_codes(result):
    return [issue["code"] for issue in result["validation"]["issues"]]


class RequirementCompletionTests(unittest.TestCase):
    def test_required_course_completed(self):
        result = evaluate(requirement(), completed=[("CS F211",)])
        self.assertEqual(result["satisfied_requirements"][0]["completion_status"], "satisfied")

    def test_required_course_absent(self):
        result = evaluate(requirement())
        self.assertEqual(result["remaining_requirements"][0]["completion_status"], "remaining")

    def test_required_course_ongoing_is_remaining(self):
        result = evaluate(requirement(), ongoing=[("CS F211",)])
        item = result["remaining_requirements"][0]
        self.assertEqual(item["ongoing_matching_courses"][0]["normalized_course_code"], "CS F211")

    def test_ongoing_does_not_increment_completed_count(self):
        result = evaluate(requirement(), ongoing=[("CS F211",)])
        self.assertEqual(result["remaining_requirements"][0]["measurements"]["course_count"]["completed"], 0)

    def test_exact_normalized_identity_only(self):
        result = evaluate(requirement(course_code="BITS U415"), completed=[("BITS U415",)])
        self.assertEqual(result["satisfied_requirements"][0]["course_code"], "BITS U415")

    def test_shared_handout_does_not_create_equivalence(self):
        result = evaluate(requirement(course_code="BITS U415"), completed=[("BITS F415",)])
        self.assertEqual(len(result["remaining_requirements"]), 1)

    def test_one_of_alternative_satisfied(self):
        rule = requirement(rule_type="choice", alternatives={
            "options": ["CS F211", "BITS F232"], "select_count": 1})
        result = evaluate(rule, completed=[("BITS F232",)])
        self.assertEqual(len(result["satisfied_requirements"]), 1)

    def test_alternative_not_satisfied(self):
        rule = requirement(rule_type="choice", alternatives={
            "options": ["CS F211", "BITS F232"], "select_count": 1})
        result = evaluate(rule)
        self.assertEqual(len(result["remaining_requirements"]), 1)

    def test_multi_selection_alternative_partially_satisfied(self):
        rule = requirement(rule_type="choice", alternatives={
            "options": ["CS F211", "BITS F232", "CS F213"], "select_count": 2})
        result = evaluate(rule, completed=[("CS F211",)])
        measure = result["partially_satisfied_requirements"][0]["measurements"]["course_count"]
        self.assertEqual(measure, {"required": 2, "completed": 1, "remaining": 1})

    def test_numeric_count_satisfied(self):
        rule = requirement(rule_type="category_total", required_count=2,
                           eligible_course_codes=["CS F211", "CS F213"])
        result = evaluate(rule, completed=[("CS F211",), ("CS F213",)])
        self.assertEqual(len(result["satisfied_requirements"]), 1)

    def test_numeric_count_partially_satisfied(self):
        rule = requirement(rule_type="category_total", required_count=2,
                           eligible_course_codes=["CS F211", "CS F213"])
        result = evaluate(rule, completed=[("CS F211",)])
        self.assertEqual(len(result["partially_satisfied_requirements"]), 1)

    def test_numeric_count_remaining(self):
        rule = requirement(rule_type="category_total", required_count=2,
                           eligible_course_codes=["CS F211", "CS F213"])
        result = evaluate(rule)
        self.assertEqual(len(result["remaining_requirements"]), 1)

    def test_unit_requirement_with_reliable_units(self):
        rule = requirement(rule_type="quantity", required_units=7,
                           eligible_course_codes=["CS F211", "CS F213"])
        result = evaluate(rule, completed=[("CS F211", 4), ("CS F213", 3)])
        self.assertEqual(result["satisfied_requirements"][0]["measurements"]["units"]["completed"], 7)

    def test_unit_requirement_missing_units_is_unevaluable(self):
        rule = requirement(rule_type="quantity", required_units=4,
                           eligible_course_codes=["CS F211"])
        result = evaluate(rule, completed=[("CS F211",)])
        self.assertEqual(len(result["unevaluable_requirements"]), 1)
        self.assertIn("missing_or_conflicting_units", issue_codes(result))

    def test_unit_conflict_is_unevaluable(self):
        rule = requirement(rule_type="quantity", required_units=4,
                           eligible_course_codes=["CS F211"])
        conflicts = [{"code": "student_catalogue_units_conflict", "severity": "warning",
                      "path": "completed_courses[0].units", "message": "conflict"}]
        result = evaluate(rule, completed=[("CS F211", 3, 4)], history_issues=conflicts)
        self.assertEqual(len(result["unevaluable_requirements"]), 1)

    def test_category_requirement_with_explicit_membership(self):
        rule = requirement(rule_type="category_total", normalized_category="discipline_core",
                           required_count=1, eligible_course_codes=["CS F211"])
        result = evaluate(rule, completed=[("CS F211",)])
        self.assertEqual(len(result["satisfied_requirements"]), 1)

    def test_category_without_membership_is_unevaluable(self):
        rule = requirement(rule_type="category_total", normalized_category="discipline_core",
                           required_count=1)
        result = evaluate(rule, completed=[("CS F211",)])
        self.assertIn("missing_course_category_membership", issue_codes(result))

    def test_descriptive_rule_is_never_evaluated(self):
        descriptive = requirement(classification="descriptive", needs_verification=True)
        result = evaluate([], descriptive=[descriptive])
        self.assertEqual(result["satisfied_requirements"], [])
        self.assertEqual(result["excluded_rules"][0]["reason"], "descriptive_or_non_executable")

    def test_verification_required_executable_input_is_unevaluable(self):
        result = evaluate(requirement(needs_verification=True), completed=[("CS F211",)])
        self.assertIn("source_rule_requires_verification", issue_codes(result))

    def test_unscoped_rule_is_not_applied(self):
        result = evaluate(requirement(scope={"programme": None}), completed=[("CS F211",)])
        self.assertIn("rule_lacks_programme_scope", issue_codes(result))

    def test_primary_and_second_programme_rules_remain_separate(self):
        primary = requirement("primary", course_code="CS F211")
        second = requirement("second", course_code="CS F211", requested_programme_role="second",
                             scope={"programme": "MATHEMATICS"})
        result = evaluate([primary, second], completed=[("CS F211",)])
        self.assertEqual({item["requested_programme_role"] for item in result["satisfied_requirements"]},
                         {"primary", "second"})

    def test_source_traceability_survives(self):
        result = evaluate(requirement(), completed=[("CS F211",)])
        item = result["satisfied_requirements"][0]
        self.assertEqual(item["sources"][0]["page_number"], 10)
        self.assertEqual(item["source_heading"], "COMPUTER SCIENCE")

    def test_output_is_json_serializable(self):
        result = evaluate(requirement(), completed=[("CS F211",)])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_incomplete_data_is_preserved(self):
        inherited = {"has_unscoped_course_rules": True, "unscoped_course_rule_count": 93}
        result = evaluate(requirement(), incomplete=inherited)
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertTrue(result["incomplete_data"]["has_incomplete_data"])

    def test_real_backend_integration(self):
        root = Path(__file__).resolve().parents[1]
        profile = StudentProfile.from_dict({
            "programme": "COMPUTER SCIENCE", "current_academic_year": 2,
            "current_semester": 1, "completed_courses": ["CS F213"]})
        academic_history = resolve_academic_history(
            profile, CourseCatalogue.load(root / "data/processed/courses.json"))
        requirements = resolve_programme_requirements(
            profile, AcademicRuleCatalogue.load(root / "data/processed/academic_rules.json"))
        result = evaluate_requirement_completion(academic_history, requirements)
        matched = [item for item in result["satisfied_requirements"]
                   if item["course_code"] == "CS F213"]
        self.assertEqual(len(matched), 1)
        self.assertTrue(result["unevaluable_requirements"] == [])
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)


if __name__ == "__main__":
    unittest.main()
