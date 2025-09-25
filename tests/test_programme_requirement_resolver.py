import json
import unittest
from pathlib import Path

from backend.programme_requirements import AcademicRuleCatalogue, resolve_programme_requirements
from backend.student_profile import StudentProfile


def rule(rule_id, programme, rule_type="required_course", classification="deterministic",
         verify=False, **updates):
    data = {
        "rule_id": rule_id,
        "scope": {"programme": programme, "programme_code": None},
        "rule_type": rule_type,
        "category": "Core Courses",
        "normalized_category": "discipline_core",
        "course_code": "CS F211" if rule_type == "required_course" else None,
        "course_title": "Data Structures",
        "units": 4 if rule_type == "required_course" else None,
        "required_count": None,
        "required_units": None,
        "min_count": None,
        "max_count": None,
        "min_units": None,
        "max_units": None,
        "alternatives": None,
        "classification": classification,
        "needs_verification": verify,
        "source_document": "Bulletin",
        "source_heading": programme,
        "sources": [{"source_file": "bulletin.pdf", "page_number": 10,
                     "text": "CS F211 Data Structures 3 1 4"}],
    }
    data.update(updates)
    return data


def profile(programme="COMPUTER SCIENCE", second=None):
    return StudentProfile.from_dict({
        "programme": programme,
        "second_programme": second,
        "current_academic_year": 2,
        "current_semester": 1,
    })


def resolve(rules, programme="COMPUTER SCIENCE", second=None):
    return resolve_programme_requirements(
        profile(programme, second), AcademicRuleCatalogue.from_dict({"records": rules}))


def issue_codes(result):
    return [issue["code"] for issue in result["validation"]["issues"]]


class ProgrammeRequirementResolverTests(unittest.TestCase):
    def test_single_programme_resolves(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE")])
        self.assertEqual(result["programme_resolutions"][0]["status"], "resolved")

    def test_harmless_formatting_normalizes(self):
        result = resolve([rule("r1", "B. E. COMPUTER SCIENCE")], "  b.e. computer science ")
        self.assertEqual(result["programme_resolutions"][0]["matched_scopes"],
                         ["B. E. COMPUTER SCIENCE"])

    def test_unknown_programme_remains_unresolved(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE")], "UNKNOWN PROGRAMME")
        self.assertEqual(result["programme_resolutions"][0]["status"], "unresolved")
        self.assertIn("programme_not_resolved", issue_codes(result))

    def test_ambiguous_programme_is_not_guessed(self):
        rules = [rule("r1", "B. E. COMPUTER SCIENCE"), rule("r2", "B.E. COMPUTER SCIENCE")]
        result = resolve(rules, "B.E. COMPUTER SCIENCE")
        self.assertEqual(result["programme_resolutions"][0]["status"], "ambiguous")
        self.assertEqual(result["applicable_executable_requirements"], [])

    def test_second_programme_resolves_independently(self):
        rules = [rule("r1", "COMPUTER SCIENCE"), rule("r2", "MATHEMATICS")]
        result = resolve(rules, second="MATHEMATICS")
        self.assertEqual([item["status"] for item in result["programme_resolutions"]],
                         ["resolved", "resolved"])
        self.assertEqual({item["requested_programme_role"]
                          for item in result["applicable_executable_requirements"]},
                         {"primary", "second"})

    def test_unknown_second_programme_is_reported_independently(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE")], second="UNKNOWN PROGRAMME")
        self.assertEqual(result["programme_resolutions"][1]["status"], "unresolved")
        self.assertIn("second_programme_not_resolved", issue_codes(result))

    def test_executable_and_descriptive_are_separate(self):
        rules = [rule("r1", "COMPUTER SCIENCE"),
                 rule("r2", "COMPUTER SCIENCE", "elective_option", "descriptive", True,
                      course_code="CS F407")]
        result = resolve(rules)
        self.assertEqual(len(result["applicable_executable_requirements"]), 1)
        self.assertEqual(len(result["applicable_descriptive_rules"]), 1)
        self.assertIn("descriptive_rules_excluded_from_calculation", issue_codes(result))

    def test_unscoped_rules_are_excluded_and_reported(self):
        rules = [rule("r1", "COMPUTER SCIENCE"),
                 rule("r2", None, course_code="BBA F121")]
        result = resolve(rules)
        self.assertEqual(len(result["excluded_unresolved_rules"]), 1)
        self.assertEqual(len(result["applicable_executable_requirements"]), 1)
        self.assertTrue(result["incomplete_data"]["has_unscoped_course_rules"])
        self.assertIn("unscoped_course_rules_excluded", issue_codes(result))

    def test_verification_flag_survives(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE", classification="descriptive", verify=True)])
        self.assertTrue(result["applicable_descriptive_rules"][0]["needs_verification"])
        self.assertIn("rules_need_verification", issue_codes(result))

    def test_required_course_is_preserved(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE", course_code="CS F211")])
        requirement = result["applicable_executable_requirements"][0]
        self.assertEqual((requirement["rule_type"], requirement["course_code"]),
                         ("required_course", "CS F211"))

    def test_elective_option_is_preserved_as_descriptive(self):
        elective = rule("r1", "COMPUTER SCIENCE", "elective_option", "descriptive",
                        course_code="CS F407")
        result = resolve([elective])
        self.assertEqual(result["applicable_descriptive_rules"][0]["course_code"], "CS F407")

    def test_numeric_requirement_is_preserved(self):
        numeric = rule("r1", "COMPUTER SCIENCE", "category_total",
                       normalized_category="discipline_core", required_units=48,
                       required_count=14, course_code=None)
        result = resolve([numeric])
        output = result["applicable_executable_requirements"][0]
        self.assertEqual((output["required_units"], output["required_count"]), (48, 14))

    def test_explicit_alternative_group_is_preserved(self):
        choice = rule("r1", "COMPUTER SCIENCE", "choice", alternatives={
            "options": ["CS F211", "BITS F232"], "select_count": 1}, course_code=None)
        result = resolve([choice])
        self.assertEqual(result["applicable_executable_requirements"][0]["alternatives"]["select_count"], 1)

    def test_unresolved_alternative_warns(self):
        unresolved = rule("r1", "COMPUTER SCIENCE", "unresolved_choice", "descriptive", True,
                          course_code=None)
        result = resolve([unresolved])
        self.assertIn("unresolved_alternative_structure", issue_codes(result))

    def test_first_degree_institutional_pool_uses_explicit_scope(self):
        pool = "Pool of Humanities courses for first degree programmes"
        rules = [rule("r1", "INTEGRATED FIRST DEGREE PROGRAMMES", "category_total",
                      required_units=8, course_code=None),
                 rule("h1", pool, "elective_option", "descriptive", True,
                      normalized_category="humanities_elective", course_code="HSS F221")]
        result = resolve(rules, "INTEGRATED FIRST DEGREE PROGRAMMES")
        resolution = result["programme_resolutions"][0]
        self.assertIn(pool, resolution["matched_scopes"])
        humanities = [item for item in result["applicable_descriptive_rules"]
                      if item["normalized_category"] == "humanities_elective"]
        self.assertEqual(len(humanities), 1)
        self.assertEqual(humanities[0]["scope_kind"], "institutional")

    def test_source_references_survive(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE")])
        output = result["applicable_executable_requirements"][0]
        self.assertEqual(output["sources"][0]["page_number"], 10)
        self.assertEqual(output["source_heading"], "COMPUTER SCIENCE")

    def test_result_is_json_serializable(self):
        result = resolve([rule("r1", "COMPUTER SCIENCE")])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_real_academic_rules_integration(self):
        path = Path(__file__).resolve().parents[1] / "data/processed/academic_rules.json"
        catalogue = AcademicRuleCatalogue.load(path)
        result = resolve_programme_requirements(
            profile("INTEGRATED FIRST DEGREE PROGRAMMES"), catalogue)
        resolution = result["programme_resolutions"][0]
        self.assertEqual(resolution["status"], "resolved")
        self.assertTrue(result["applicable_executable_requirements"])
        self.assertTrue(result["applicable_descriptive_rules"])
        self.assertEqual(result["incomplete_data"]["unscoped_course_rule_count"], 93)
        self.assertTrue(all(item["scope"]["programme"] is None
                            for item in result["excluded_unresolved_rules"]))
        self.assertTrue(any(item["normalized_category"] == "humanities_elective"
                            for item in result["applicable_descriptive_rules"]))


if __name__ == "__main__":
    unittest.main()
