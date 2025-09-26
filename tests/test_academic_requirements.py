import json
import unittest

from backend.academic_history import CourseCatalogue
from backend.academic_requirements import (
    AcademicRequirementService,
    _combine_diagnostics,
    build_academic_requirement_summary,
)
from backend.programme_requirements import AcademicRuleCatalogue


def course(code, title="Course", units=4, identities=None):
    identities = identities or [code]
    return {
        "metadata": {
            "course_codes": identities,
            "course_identity_type": "single" if len(identities) == 1 else "multiple",
            "course_title": {"value": title, "sources": []},
            "units": {"value": units, "sources": []},
        },
        "source": {"source_file": f"{code.replace(' ', '_')}.pdf", "page_numbers": [1]},
        "candidate_status": "usable",
        "validation": {"is_valid": True, "needs_verification": False, "issues": []},
    }


def rule(rule_id, programme="COMPUTER SCIENCE", code="CS F211",
         category="discipline_core", rule_type="required_course", **updates):
    data = {
        "rule_id": rule_id,
        "scope": {"programme": programme, "programme_code": None},
        "rule_type": rule_type,
        "category": "Core Courses" if category else None,
        "normalized_category": category,
        "course_code": code,
        "course_title": "Data Structures",
        "units": 4,
        "required_count": None,
        "required_units": None,
        "min_count": None,
        "max_count": None,
        "min_units": None,
        "max_units": None,
        "alternatives": None,
        "classification": "deterministic",
        "needs_verification": False,
        "source_document": "Bulletin",
        "source_heading": programme,
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": f"{code} Data Structures"}],
    }
    data.update(updates)
    return data


def profile(**updates):
    data = {
        "programme": "COMPUTER SCIENCE",
        "current_academic_year": 2,
        "current_semester": 1,
        "completed_courses": [],
        "ongoing_courses": [],
    }
    data.update(updates)
    return data


def service(courses=None, rules=None):
    return AcademicRequirementService(
        CourseCatalogue.from_dict({"records": courses or []}),
        AcademicRuleCatalogue.from_dict({"records": rules or []}),
    )


def category(result, name="discipline_core", role="primary"):
    programme = next(item for item in result["requirement_progress"]["programme_progress"]
                     if item["requested_programme_role"] == role)
    return next(item for item in programme["categories"]
                if item["normalized_category"] == name)


class AcademicRequirementServiceTests(unittest.TestCase):
    def test_valid_single_programme_profile(self):
        result = service([course("CS F211")], [rule("r1")]).build_summary(profile())
        self.assertTrue(result["validation"]["is_valid"])
        self.assertEqual(result["student"]["programme"], "COMPUTER SCIENCE")
        self.assertEqual(len(result["programme_resolution"]), 1)

    def test_valid_second_programme_remains_separate(self):
        rules = [rule("r1"), rule("r2", "MATHEMATICS", "MATH F101")]
        result = service([], rules).build_summary(profile(second_programme="MATHEMATICS"))
        progress = result["requirement_progress"]["programme_progress"]
        self.assertEqual([(item["requested_programme_role"], item["programme"])
                          for item in progress],
                         [("primary", "COMPUTER SCIENCE"), ("second", "MATHEMATICS")])

    def test_invalid_profile_stops_safely(self):
        result = service([], [rule("r1")]).build_summary(profile(current_semester=3))
        self.assertEqual(result["status"], "invalid_student_profile")
        self.assertEqual(result["programme_resolution"], [])
        self.assertFalse(result["validation"]["is_valid"])

    def test_non_mapping_profile_stops_safely(self):
        result = service([], []).build_summary("bad")
        self.assertEqual(result["status"], "invalid_student_profile")
        self.assertEqual(result["validation"]["errors"][0]["code"], "invalid_profile_input")

    def test_unresolved_programme_stops_completion(self):
        result = service([], [rule("r1")]).build_summary(profile(programme="UNKNOWN"))
        self.assertEqual(result["status"], "unresolved_programme")
        self.assertEqual(result["requirement_progress"]["programme_progress"], [])
        self.assertEqual(result["programme_resolution"][0]["status"], "unresolved")

    def test_completed_course_appears_in_history(self):
        result = service([course("CS F211")], [rule("r1")]).build_summary(
            profile(completed_courses=["CS F211"]))
        item = result["academic_history"]["completed_courses"][0]
        self.assertEqual((item["normalized_course_code"], item["resolution_status"]),
                         ("CS F211", "matched"))

    def test_ongoing_course_remains_ongoing(self):
        result = service([course("CS F211")], [rule("r1")]).build_summary(
            profile(ongoing_courses=["CS F211"]))
        self.assertEqual(result["academic_history"]["ongoing_courses"][0]["status"], "ongoing")
        self.assertEqual(result["summary"]["satisfied_requirement_count"], 0)

    def test_unmatched_valid_course_remains_visible(self):
        result = service([], [rule("r1")]).build_summary(
            profile(completed_courses=["BIO F999"]))
        self.assertEqual(result["academic_history"]["unresolved_courses"][0]
                         ["normalized_course_code"], "BIO F999")
        self.assertTrue(any(item["code"] == "catalogue_match_missing"
                            for item in result["validation"]["warnings"]))

    def test_satisfied_requirement_survives(self):
        result = service([course("CS F211")], [rule("r1")]).build_summary(
            profile(completed_courses=["CS F211"]))
        self.assertEqual(category(result)["satisfied_requirements"][0]["rule_id"], "r1")

    def test_remaining_requirement_survives(self):
        result = service([], [rule("r1")]).build_summary(profile())
        self.assertEqual(category(result)["remaining_requirements"][0]["rule_id"], "r1")

    def test_partially_satisfied_requirement_survives(self):
        choice = rule("choice", code=None, rule_type="choice", alternatives={
            "options": ["CS F211", "CS F213"], "select_count": 2})
        result = service([course("CS F211")], [choice]).build_summary(
            profile(completed_courses=["CS F211"]))
        self.assertEqual(category(result)["partially_satisfied_requirements"][0]
                         ["rule_id"], "choice")

    def test_unevaluable_requirement_survives(self):
        numeric = rule("total", code=None, rule_type="category_total", required_units=8)
        result = service([], [numeric]).build_summary(profile())
        item = category(result)["unevaluable_requirements"][0]
        self.assertEqual(item["unevaluable_reason"], "missing_course_category_membership")

    def test_explicit_category_survives(self):
        result = service([], [rule("r1", category="discipline_elective")]).build_summary(profile())
        self.assertEqual(category(result, "discipline_elective")["source_categories"],
                         ["Core Courses"])

    def test_uncategorized_requirement_survives_without_inference(self):
        result = service([], [rule("r1", category=None)]).build_summary(profile())
        programme = result["requirement_progress"]["programme_progress"][0]
        self.assertEqual(programme["categories"], [])
        self.assertEqual(programme["uncategorized_requirements"][0]["rule_id"], "r1")

    def test_descriptive_information_is_not_executable(self):
        pool = rule("h1", programme="COMPUTER SCIENCE", code="HSS F221",
                    category="humanities_elective", rule_type="elective_option",
                    classification="descriptive", needs_verification=True)
        result = service([], [rule("r1"), pool]).build_summary(profile())
        self.assertEqual(result["descriptive_information"][0]["rule_id"], "h1")
        self.assertEqual(result["summary"]["remaining_requirement_count"], 1)

    def test_incomplete_data_is_propagated(self):
        unscoped = rule("u1", programme=None)
        result = service([], [rule("r1"), unscoped]).build_summary(profile())
        self.assertTrue(result["incomplete_data"]["has_incomplete_data"])
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 1)

    def test_known_93_unscoped_rules_remain_excluded(self):
        unscoped = [rule(f"u{i}", programme=None, code=f"BIO F{i:03d}") for i in range(93)]
        result = service([], [rule("r1"), *unscoped]).build_summary(profile())
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertEqual(len(result["incomplete_data"]["excluded_unresolved_rules"]), 93)

    def test_duplicate_diagnostics_are_conservatively_deduplicated(self):
        issue = {"code": "same", "severity": "warning", "path": "course[0]",
                 "message": "Same warning"}
        other = {**issue, "path": "course[1]"}
        combined = _combine_diagnostics(
            ("one", {"issues": [issue]}),
            ("two", {"issues": [issue, other]}))
        self.assertEqual(len(combined), 2)
        self.assertEqual(combined[0]["stages"], ["one", "two"])

    def test_source_traceability_survives(self):
        result = service([course("CS F211")], [rule("r1")]).build_summary(
            profile(completed_courses=["CS F211"]))
        item = category(result)["satisfied_requirements"][0]
        self.assertEqual(item["sources"][0]["page_number"], 10)
        self.assertEqual(item["source_heading"], "COMPUTER SCIENCE")

    def test_no_category_is_inferred_from_course_code(self):
        result = service([], [rule("r1", category=None, code="CS F211")]).build_summary(profile())
        serialized = json.dumps(result)
        self.assertNotIn("discipline_core", serialized)
        self.assertNotIn("CDC", serialized)

    def test_no_programme_alias_is_invented(self):
        result = service([], [rule("r1", programme="COMPUTER SCIENCE")]).build_summary(
            profile(programme="B.E. COMPUTER SCIENCE"))
        self.assertEqual(result["status"], "unresolved_programme")

    def test_shared_handout_does_not_invent_course_equivalence(self):
        shared = course("BITS F415", identities=["BITS F415", "BITS U415"])
        result = service([shared], [rule("r1", code="BITS U415")]).build_summary(
            profile(completed_courses=["BITS F415"]))
        self.assertEqual(category(result)["remaining_requirements"][0]["course_code"],
                         "BITS U415")

    def test_summary_counts_are_correct_and_no_percentage_exists(self):
        rules = [rule("done", code="CS F211"), rule("left", code="CS F213")]
        result = service([course("CS F211")], rules).build_summary(
            profile(completed_courses=["CS F211"], ongoing_courses=["CS F213"]))
        summary = result["summary"]
        self.assertEqual((summary["completed_course_count"], summary["ongoing_course_count"]),
                         (1, 1))
        self.assertEqual((summary["satisfied_requirement_count"],
                          summary["remaining_requirement_count"]), (1, 1))
        self.assertNotIn("completion_percentage", summary)

    def test_convenience_function_accepts_injected_datasets(self):
        result = build_academic_requirement_summary(
            profile(), {"records": []}, {"records": [rule("r1")]})
        self.assertEqual(result["summary"]["remaining_requirement_count"], 1)

    def test_result_is_json_serializable(self):
        result = service([], [rule("r1")]).build_summary(profile())
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_real_data_end_to_end(self):
        result = AcademicRequirementService().build_summary(profile(
            completed_courses=["CS F213"], ongoing_courses=["CS F211"]))
        self.assertIn(result["status"], ("complete", "complete_with_incomplete_data"))
        self.assertEqual(result["student"]["programme"], "COMPUTER SCIENCE")
        self.assertEqual(result["summary"]["completed_course_count"], 1)
        self.assertEqual(result["summary"]["ongoing_course_count"], 1)
        self.assertTrue(result["requirement_progress"]["programme_progress"][0]["categories"])
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertEqual(len(result["incomplete_data"]["excluded_unresolved_rules"]), 93)
        requirements = [item
                        for programme in result["requirement_progress"]["programme_progress"]
                        for category_item in programme["categories"]
                        for state in ("satisfied", "partially_satisfied", "remaining", "unevaluable")
                        for item in category_item[f"{state}_requirements"]]
        self.assertTrue(any(item["sources"] for item in requirements))
        self.assertNotIn("completion_percentage", result["summary"])


if __name__ == "__main__":
    unittest.main()
