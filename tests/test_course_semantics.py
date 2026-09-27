import json
import unittest
from copy import deepcopy

from backend.course_catalogue import CourseCatalogue
from backend.course_semantics import (
    CourseSemanticProfileBuilder,
    UnifiedCourseSemanticProfileBuilder,
    validate_semantic_profiles,
)
from backend.source_course_catalogue import SourceCourseCatalogue


def source(text="Evidence", page=1, filename="course.pdf"):
    return {"source_file": filename, "page_number": page, "text": text}


def field(value, text=None):
    return {"value": value, "sources": [source(text or str(value))]}


def record(code="CS F211", title="Data Structures", department="Computer Science",
           units=None, syllabus=None, instructors=None, evaluation=None,
           exams=None, attendance=None, makeup=None, filename="course.pdf",
           status="usable", verify=False, identities=None):
    identities = identities or [code]
    return {
        "record_id": filename,
        "candidate_status": status,
        "metadata": {
            "course_code": field(code), "course_codes": identities,
            "course_identity_type": "single" if len(identities) == 1 else "multiple",
            "course_title": field(title) if title is not None else None,
            "department_division": field(department) if department is not None else None,
            "units": field(units) if units is not None else None,
        },
        "prerequisites": None,
        "instructors": instructors or [], "syllabus": syllabus or [],
        "evaluation": evaluation or [],
        "exams": exams or {"midsemester": None, "comprehensive": None},
        "attendance": attendance or [], "makeup": makeup or [],
        "observations": [],
        "source": {"source_file": filename, "page_numbers": [1, 2]},
        "validation": {"is_valid": True, "needs_verification": verify, "issues": []},
    }


def syllabus(text="Trees and graph algorithms", heading="Course content"):
    return [{"heading": heading, "text": text, "sources": [source(text)]}]


def instructor(name="Ada Lovelace"):
    return [{"label": "Instructor-in-Charge", "names": [name],
             "text": name, "sources": [source(name)]}]


def evaluation(name="Project", weight="40%"):
    return [{"name": name, "weightage": weight, "marks": None,
             "sources": [source(f"{name} {weight}")]}]


def exams():
    return {"midsemester": {"name": field("Mid-Semester exam"),
                             "weightage": field("30%")},
            "comprehensive": {"name": field("Comprehensive exam"),
                               "weightage": field("40%")}}


def policy(heading="Attendance Policy", text="Attendance is required"):
    return [{"heading": heading, "text": text, "sources": [source(text)]}]


def build(*records):
    return CourseSemanticProfileBuilder(CourseCatalogue(list(records))).build_all()


def one(*records):
    return build(*records)["profiles"][0]


class CourseSemanticProfileTests(unittest.TestCase):
    def test_minimal_valid_profile(self):
        profile = one(record(title=None, department=None))
        self.assertEqual(profile["course_code"], "CS F211")
        self.assertTrue(profile["validation"]["is_valid"])

    def test_rich_profile(self):
        profile = one(record(units="3-1-4", syllabus=syllabus(),
                             instructors=instructor(), evaluation=evaluation(),
                             exams=exams(), attendance=policy(), makeup=policy(
                                 "Make-up Policy", "Make-up requests are reviewed")))
        self.assertTrue(all(profile["data_availability"].values()))

    def test_title_preservation(self):
        profile = one(record(title="Advanced Algorithms"))
        self.assertEqual(profile["title"]["display_value"], "Advanced Algorithms")

    def test_content_preservation(self):
        profile = one(record(syllabus=syllabus("Dynamic programming")))
        self.assertEqual(profile["content"][0]["text"], "Dynamic programming")

    def test_explicit_topics_preserve_syllabus_items(self):
        profile = one(record(syllabus=syllabus("Graph traversal", "Module 2")))
        self.assertEqual(profile["topics"][0]["heading"], "Module 2")
        self.assertEqual(profile["topics"][0]["text"], "Graph traversal")

    def test_instructor_deduplication(self):
        groups = instructor("Ada Lovelace") + instructor(" ada   lovelace ")
        profile = one(record(instructors=groups))
        self.assertEqual(len(profile["instructors"]), 1)

    def test_evaluation_preservation(self):
        profile = one(record(evaluation=evaluation("Project", "50%")))
        self.assertEqual(profile["evaluation"][0]["weightage"], "50%")

    def test_exam_information_preservation(self):
        profile = one(record(exams=exams()))
        self.assertEqual(len(profile["exam_information"]["midsemester"]), 1)
        self.assertTrue(profile["source_evidence"]["exam_information"])

    def test_attendance_and_makeup_preservation(self):
        profile = one(record(attendance=policy(), makeup=policy("Make-up Policy")))
        self.assertEqual(len(profile["attendance_or_makeup"]["attendance"]), 1)
        self.assertEqual(len(profile["attendance_or_makeup"]["makeup"]), 1)

    def test_units_preservation(self):
        profile = one(record(units="3-1-4"))
        self.assertEqual(profile["units"]["display_value"], "3-1-4")

    def test_department_preservation(self):
        profile = one(record(department="Mathematics"))
        self.assertEqual(profile["department_or_division"]["display_value"], "Mathematics")

    def test_missing_optional_metadata_is_not_error(self):
        profile = one(record(title=None, department=None))
        self.assertFalse(profile["data_availability"]["content"])
        self.assertIn("content", profile["uncertainty"]["missing_fields"])
        self.assertTrue(profile["validation"]["is_valid"])

    def test_multiple_source_records(self):
        profile = one(record(filename="one.pdf"), record(filename="two.pdf"))
        self.assertTrue(profile["uncertainty"]["has_multiple_source_records"])
        self.assertEqual(len(profile["source_evidence"]["course_identity"]), 2)

    def test_identical_metadata_is_deduplicated(self):
        profile = one(record(filename="one.pdf", syllabus=syllabus()),
                      record(filename="two.pdf", syllabus=syllabus()))
        self.assertEqual(profile["title"]["values"], ["Data Structures"])
        self.assertEqual(len(profile["content"]), 1)

    def test_conflicting_titles_are_not_collapsed(self):
        profile = one(record(filename="one.pdf", title="Title One"),
                      record(filename="two.pdf", title="Title Two"))
        self.assertIsNone(profile["title"]["display_value"])
        self.assertEqual(set(profile["title"]["values"]), {"Title One", "Title Two"})
        self.assertIn("course_title", profile["uncertainty"]["conflicts"])

    def test_conflicting_departments_are_not_collapsed(self):
        profile = one(record(filename="one.pdf", department="Division One"),
                      record(filename="two.pdf", department="Division Two"))
        self.assertIsNone(profile["department_or_division"]["display_value"])
        self.assertIn("department_division", profile["uncertainty"]["conflicts"])

    def test_source_traceability(self):
        profile = one(record(syllabus=syllabus("Networks")))
        self.assertEqual(profile["source_evidence"]["content"][0]["text"], "Networks")

    def test_data_availability_flags(self):
        profile = one(record(syllabus=syllabus(), instructors=instructor()))
        self.assertTrue(profile["data_availability"]["content"])
        self.assertTrue(profile["data_availability"]["instructors"])
        self.assertFalse(profile["data_availability"]["evaluation"])

    def test_searchable_text_is_deterministic(self):
        profile = one(record(title="Algorithms", syllabus=syllabus(
            "Trees and graphs", "Course content")))
        self.assertEqual(profile["searchable_text"],
                         "CS F211\nAlgorithms\nCourse content\nTrees and graphs")

    def test_searchable_text_excludes_policy_and_source_noise(self):
        profile = one(record(syllabus=syllabus("Algorithms"), attendance=policy()))
        text = profile["searchable_text"]
        self.assertNotIn("course.pdf", text)
        self.assertNotIn("Attendance is required", text)
        self.assertNotIn("eligibility", text.casefold())

    def test_unusable_identity_is_excluded(self):
        result = build(record(status="unusable_identity"))
        self.assertEqual(result["profiles"], [])
        self.assertEqual(result["summary"]["excluded_catalogue_record_count"], 1)

    def test_duplicate_profile_identity_is_detected(self):
        result = build(record())
        result["profiles"].append(deepcopy(result["profiles"][0]))
        validation = validate_semantic_profiles(result)
        self.assertEqual(validation["duplicate_profile_identity_count"], 1)

    def test_json_compatibility(self):
        result = build(record(syllabus=syllabus()))
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_in_memory_catalogue_operation(self):
        catalogue = CourseCatalogue([record(code="BIO F101")])
        builder = CourseSemanticProfileBuilder(catalogue)
        self.assertEqual(builder.build_one("BIO F101")["course_code"], "BIO F101")

    def test_no_generated_topics_or_semantic_expansion(self):
        profile = one(record(syllabus=syllabus("AI")))
        self.assertEqual(profile["topics"][0]["text"], "AI")
        self.assertNotIn("machine learning", profile["searchable_text"].casefold())

    def test_shared_multi_identity_record_builds_each_catalogue_identity(self):
        result = build(record(code="CS F211", identities=["CS F211", "BITS F211"]))
        self.assertEqual({profile["course_code"] for profile in result["profiles"]},
                         {"CS F211", "BITS F211"})
        self.assertTrue(all(profile["uncertainty"]["has_shared_source_record"]
                            for profile in result["profiles"]))

    def test_unknown_identity_returns_none(self):
        builder = CourseSemanticProfileBuilder(CourseCatalogue([record()]))
        self.assertIsNone(builder.build_one("BIO F999"))

    def test_real_bulletin_only_profile_uses_title_without_inventing_metadata(self):
        builder = UnifiedCourseSemanticProfileBuilder()
        profile = builder.build_one("CS F437")
        self.assertEqual(profile["title"]["display_value"],
                         "Generative Artificial Intelligence")
        self.assertEqual(profile["content"], [])
        self.assertEqual(profile["topics"], [])
        self.assertEqual(profile["evaluation"], [])
        self.assertFalse(profile["catalogue_identity"]["handout_available"])
        self.assertTrue(profile["catalogue_identity"]["bulletin_available"])
        self.assertTrue(profile["source_evidence"]["course_identity"])

    def test_unified_builder_preserves_rich_handout_profile(self):
        handout_builder = CourseSemanticProfileBuilder(CourseCatalogue([record()]))
        builder = UnifiedCourseSemanticProfileBuilder(
            SourceCourseCatalogue({"records": [], "summary": {}}), handout_builder)
        profile = builder.build_one("CS F211")
        self.assertEqual(profile["title"]["display_value"], "Data Structures")
        self.assertTrue(profile["catalogue_identity"]["source_record_count"])


if __name__ == "__main__":
    unittest.main()
