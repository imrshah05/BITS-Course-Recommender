import unittest

from backend.course_catalogue import CourseCatalogue
from backend.elective_policy import ElectivePolicyEngine
from backend.eligibility import EligibilityEngine


SOURCE = {"source_file": "source.pdf", "page_number": 1, "text": "policy"}


def regulations():
    return {"structured_policies": [
        {"id": name, "rule_type": name, "needs_verification": False,
         "sources": [SOURCE], "validation": {"is_valid": True}}
        for name in (
            "open_elective_host_region",
            "cross_discipline_prior_preparation",
            "course_prerequisite_independent",
            "dual_degree_del_to_opel",
        )
    ]}


def requirement_data():
    programmes = [
        {"id": "cs", "name": "B.E. Computer Science", "context": "semester-wise chart"},
        {"id": "civil", "name": "B.E. Civil", "context": "semester-wise chart"},
        {"id": "physics", "name": "M.Sc. Physics", "context": "semester-wise chart"},
        {"id": "huel", "name": "Pool of Humanities courses for first degree programmes",
         "context": "institutional course pool"},
    ]
    requirements = [
        {"id": "cs-core", "kind": "required_course", "programme_name": "B.E. Computer Science",
         "course_code": "CS F211", "year": 3, "semester": 1,
         "needs_verification": False, "sources": [SOURCE]},
        {"id": "civil-member", "kind": "discipline_membership",
         "programme_name": "B.E. Civil", "course_code": "CE F416",
         "needs_verification": False, "sources": [SOURCE]},
        {"id": "cs-del", "kind": "elective_membership",
         "programme_name": "B.E. Computer Science", "course_code": "CS F407",
         "needs_verification": False, "sources": [SOURCE]},
        {"id": "physics-del", "kind": "elective_membership",
         "programme_name": "M.Sc. Physics", "course_code": "PHY F312",
         "needs_verification": False, "sources": [SOURCE]},
        {"id": "huel-member", "kind": "elective_option", "programme_id": "huel",
         "programme_name": "Pool of Humanities courses for first degree programmes",
         "category": "Humanities Electives", "course_code": "HSS F221",
         "needs_verification": False, "sources": [SOURCE]},
    ]
    return {"programmes": programmes, "requirements": requirements}


def history(programmes=("B.E. Computer Science",), completed=()):
    return {
        "programme": programmes[0],
        "second_programme": programmes[1] if len(programmes) > 1 else None,
        "completed_courses": [
            {"normalized_course_code": code, "resolution_status": "matched"}
            for code in completed],
        "ongoing_courses": [],
        "profile_validation": {"is_valid": True},
        "validation": {"is_valid": True},
    }


def summary(programmes=("B.E. Computer Science",)):
    return {"requirement_progress": {"programme_progress": [
        {"programme": programme,
         "requested_programme_role": "primary" if index == 0 else "second",
         "categories": [{
             "normalized_category": "open_elective",
             "satisfied_requirements": [], "partially_satisfied_requirements": [],
             "remaining_requirements": [],
             "unevaluable_requirements": [{
                 "rule_id": f"opel-{index}", "rule_type": "category_total",
                 "completion_status": "unevaluable", "sources": [SOURCE],
             }],
         }]}
        for index, programme in enumerate(programmes)
    ]}}


def course_record(code):
    return {
        "candidate_status": "usable",
        "metadata": {"course_codes": [code], "course_identity_type": "single",
                     "course_code": {"value": code},
                     "course_title": {"value": "Course"},
                     "department_division": {"value": None}, "units": {"value": 3}},
        "prerequisites": None,
        "source": {"source_file": "bulletin.pdf", "page_numbers": [1]},
        "validation": {"is_valid": True, "needs_verification": False},
    }


class ElectivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ElectivePolicyEngine(requirement_data(), regulations())

    def test_year_two_cross_discipline_course_fails_preparation_gate(self):
        check = self.policy.cross_discipline_restrictions(history())["CE F416"][0]
        self.assertEqual(check["state"], "failed")
        self.assertEqual(check["reason_code"],
                         "cross_discipline_prior_preparation_not_met")

    def test_passed_gate_does_not_turn_missing_prerequisite_into_eligible(self):
        academic_history = history(completed=("CS F211",))
        checks = self.policy.cross_discipline_restrictions(academic_history)
        catalogue = CourseCatalogue([course_record("CE F416")])
        result = EligibilityEngine(catalogue).evaluate(
            "CE F416", academic_history, checks["CE F416"])
        self.assertEqual(checks["CE F416"][0]["state"], "passed")
        self.assertEqual(result["eligibility_state"], "unknown")
        self.assertIn("prerequisite_unknown", result["reason_codes"])

    def test_outside_discipline_gets_potential_open_elective_relationship(self):
        index = {"explicit": {}, "ambiguous": {}}
        self.policy.add_open_elective_relationships(index, summary())
        relationship = index["explicit"]["CE F416"][0]
        self.assertEqual(relationship["normalized_category"], "open_elective")
        self.assertTrue(relationship["potential_relationship"])
        self.assertEqual(relationship["completion_status"], "unevaluable")

    def test_dual_degree_del_counts_toward_other_degree_opel(self):
        programmes = ("B.E. Computer Science", "M.Sc. Physics")
        index = {"explicit": {}, "ambiguous": {}}
        self.policy.add_open_elective_relationships(index, summary(programmes))
        cs_relationships = index["explicit"]["CS F407"]
        self.assertEqual([item["programme_scope"] for item in cs_relationships],
                         ["M.Sc. Physics"])
        self.assertEqual(cs_relationships[0]["relationship_basis"],
                         "dual_degree_discipline_elective_counts_as_other_open_elective")
        civil_scopes = {item["programme_scope"]
                        for item in index["explicit"]["CE F416"]}
        self.assertEqual(civil_scopes,
                         {"B.E. Computer Science", "M.Sc. Physics"})

    def test_own_del_and_huel_take_precedence_until_satisfied(self):
        data = summary()
        categories = data["requirement_progress"]["programme_progress"][0]["categories"]
        for normalized in ("discipline_elective", "humanities_elective"):
            categories.append({
                "normalized_category": normalized,
                "satisfied_requirements": [], "partially_satisfied_requirements": [],
                "remaining_requirements": [{
                    "rule_id": normalized, "completion_status": "remaining",
                    "sources": [SOURCE]}], "unevaluable_requirements": [],
            })
        index = {"explicit": {}, "ambiguous": {}}
        self.policy.add_open_elective_relationships(index, data)
        self.assertNotIn("CS F407", index["explicit"])
        self.assertNotIn("HSS F221", index["explicit"])

        for category in categories[1:]:
            record = category["remaining_requirements"].pop()
            record["completion_status"] = "satisfied"
            category["satisfied_requirements"].append(record)
        index = {"explicit": {}, "ambiguous": {}}
        self.policy.add_open_elective_relationships(index, data)
        self.assertEqual(index["explicit"]["CS F407"][0]["relationship_basis"],
                         "own_discipline_elective_after_del_accounted")
        self.assertEqual(index["explicit"]["HSS F221"][0]["relationship_basis"],
                         "humanities_elective_after_huel_accounted")


if __name__ == "__main__":
    unittest.main()
