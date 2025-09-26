import unittest
from pathlib import Path

from preprocessing.pdf_extractor import extract_pdf_text
from preprocessing.prerequisites import extract_prerequisites


def page(text, number=1, source="course.pdf"):
    return {"source_file": source, "page_number": number, "text": text}


class PrerequisiteExtractionTests(unittest.TestCase):
    def test_standard_prerequisite_heading(self):
        result = extract_prerequisites([page(
            "Prerequisite: CS F213\nCourse Objectives: Learn")])
        self.assertEqual(result["kind"], "courses")
        self.assertEqual(result["course_codes"], ["CS F213"])

    def test_observed_hyphenated_numbered_heading(self):
        result = extract_prerequisites([page(
            "7. Pre-requisites: CS F213\n8. Chamber Consultation Hour: Later")])
        self.assertEqual(result["heading"], "Pre-requisites")
        self.assertEqual(result["course_codes"], ["CS F213"])

    def test_observed_uppercase_section_heading(self):
        result = extract_prerequisites([page(
            "2. PRE-REQUISITES\nCS F213\n3. SCOPE\nTopics")])
        self.assertEqual(result["kind"], "courses")
        self.assertEqual(result["sources"][0]["text"], "2. PRE-REQUISITES\nCS F213")

    def test_explicit_all(self):
        result = extract_prerequisites([page(
            "Prerequisites: CS F213 and MATH F101\nCourse Plan: Topics")])
        self.assertEqual((result["operator"], result["course_codes"]),
                         ("all", ["CS F213", "MATH F101"]))

    def test_explicit_any(self):
        result = extract_prerequisites([page(
            "Prerequisites: CS F213 or MATH F101\nCourse Plan: Topics")])
        self.assertEqual((result["operator"], result["course_codes"]),
                         ("any", ["CS F213", "MATH F101"]))

    def test_explicit_no_prerequisite_variants(self):
        for value in ("None", "Nil", "N/A", "NA", "No prerequisite"):
            with self.subTest(value=value):
                result = extract_prerequisites([page(
                    f"Prerequisite: {value}\nCourse Objectives: Learn")])
                self.assertEqual(result["kind"], "none")
                self.assertFalse(result["needs_verification"])

    def test_missing_section(self):
        self.assertIsNone(extract_prerequisites([page("Course Description: Topics")]))

    def test_incidental_word_is_not_a_formal_field(self):
        self.assertIsNone(extract_prerequisites([page(
            "Understanding algebra is a prerequisite for later topics.")]))

    def test_unnumbered_table_cell_is_not_a_formal_field(self):
        self.assertIsNone(extract_prerequisites([page(
            "Course Plan: Lectures Topic\nPrerequisites\nBasics of Machine Learning")]))

    def test_malformed_course_code_is_unresolved(self):
        result = extract_prerequisites([page(
            "Prerequisite: CS F21\nCourse Objectives: Learn")])
        self.assertEqual(result["kind"], "unresolved")
        self.assertTrue(result["needs_verification"])

    def test_free_form_knowledge_is_unresolved(self):
        result = extract_prerequisites([page(
            "Prerequisites: Knowledge of linear algebra and Python\n3. Books: Text")])
        self.assertEqual(result["kind"], "unresolved")
        self.assertFalse(result["machine_evaluable"])
        self.assertIn("free_form_prerequisite_not_machine_evaluable",
                      result["diagnostics"])

    def test_conditions_prevent_course_only_interpretation(self):
        result = extract_prerequisites([page(
            "Prerequisite: CS F213 with minimum grade B\nCourse Plan: Topics")])
        self.assertEqual(result["kind"], "unresolved")
        self.assertIn("ambiguous_prerequisite_relationship", result["diagnostics"])

    def test_source_and_page_traceability(self):
        result = extract_prerequisites([page(
            "Prerequisite: CS F213\nCourse Objectives: Learn", 4, "trace.pdf")],
            ["CS F214"])
        self.assertEqual(result["target_course_codes"], ["CS F214"])
        self.assertEqual(result["sources"][0]["source_file"], "trace.pdf")
        self.assertEqual(result["sources"][0]["page_number"], 4)

    def test_compatible_multiple_fields_retain_sources(self):
        result = extract_prerequisites([
            page("Prerequisite: CS F213\nCourse Objectives: Learn", 1),
            page("Prerequisites: CS F213\nCourse Plan: Topics", 2),
        ])
        self.assertEqual(result["kind"], "courses")
        self.assertEqual([item["page_number"] for item in result["sources"]], [1, 2])

    def test_conflicting_multiple_fields_require_verification(self):
        result = extract_prerequisites([
            page("Prerequisite: CS F213\nCourse Objectives: Learn", 1),
            page("Prerequisite: MATH F101\nCourse Plan: Topics", 2),
        ])
        self.assertEqual(result["kind"], "unresolved")
        self.assertTrue(result["needs_verification"])
        self.assertIn("conflicting_prerequisite_fields", result["diagnostics"])

    def test_deterministic_course_ordering(self):
        result = extract_prerequisites([page(
            "Prerequisite: MATH F101 and CS F213\nCourse Plan: Topics")])
        self.assertEqual(result["course_codes"], ["CS F213", "MATH F101"])

    def test_real_explicit_absence_handout(self):
        path = Path(__file__).resolve().parents[1] / "data/raw/handouts/119_CHE_F213.pdf"
        if not path.exists():
            self.skipTest("Supplied handout unavailable")
        result = extract_prerequisites(extract_pdf_text(path), ["CHE F213"])
        self.assertEqual(result["kind"], "none")
        self.assertEqual(result["sources"][0]["source_file"], path.name)
        self.assertEqual(result["sources"][0]["page_number"], 7)


if __name__ == "__main__":
    unittest.main()
