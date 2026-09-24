import unittest
from pathlib import Path

from preprocessing.course_metadata import extract_course_metadata
from preprocessing.pdf_extractor import extract_pdf_text


class CourseMetadataTests(unittest.TestCase):
    def page(self, text, number=1, filename="handout.pdf"):
        return {"source_file": filename, "page_number": number, "text": text}

    def parse(self, text):
        return extract_course_metadata([self.page(text)])

    def test_course_code(self):
        result = self.parse("Course Number: CS F111")
        self.assertEqual(result["course_code"]["value"], "CS F111")

    def test_course_title(self):
        result = self.parse("Course Title: Introduction to Computing")
        self.assertEqual(result["course_title"]["value"], "Introduction to Computing")

    def test_explicit_department(self):
        result = self.parse("Department: Computer Science\nCourse No: CS F111")
        self.assertEqual(result["department_division"]["value"], "Computer Science")

    def test_division_header(self):
        for header in ["Academic—Undergraduate Studies Division", "AUGS/ AGSR Division",
                       "Instruction Division", "Department of Mathematics"]:
            with self.subTest(header=header):
                result = self.parse(header + "\nCourse No: CS F111")
                self.assertEqual(result["department_division"]["value"], header)

    def test_units_preserve_representation(self):
        for value in ["3", "3-1-4", "3 (2+1)", "3-0-0-3"]:
            with self.subTest(value=value):
                result = self.parse("Credit Units: " + value)
                self.assertEqual(result["units"]["value"], value)

    def test_inline_labels(self):
        result = self.parse("Course No: MF F218, Unit: 3-1-4 Course Title: Transport Phenomena")
        self.assertEqual(result["course_code"]["value"], "MF F218")
        self.assertEqual(result["units"]["value"], "3-1-4")
        self.assertEqual(result["course_title"]["value"], "Transport Phenomena")

    def test_whitespace_line_breaks_and_case(self):
        result = self.parse("course\n NUMBER : cs  f 111\nCOURSE\nTITLE:\nIntroduction to\nComputing")
        self.assertEqual(result["course_code"]["value"], "CS F111")
        self.assertEqual(result["course_title"]["value"], "Introduction to Computing")

    def test_units_on_separate_line(self):
        result = self.parse("Units:\n3\nCourse Title: Computing")
        self.assertEqual(result["units"]["value"], "3")
        self.assertEqual(result["course_title"]["value"], "Computing")

    def test_labels_first_column(self):
        result = self.parse("Course No\nCourse Title\n: CS F301\n: Programming Languages\n")
        self.assertEqual(result["course_code"]["value"], "CS F301")
        self.assertEqual(result["course_title"]["value"], "Programming Languages")

    def test_missing_fields_not_inferred_from_filename_or_prose(self):
        result = extract_course_metadata([self.page(
            "This handout discusses computing and mentions CS F111.",
            filename="001_CS_F111.pdf",
        )])
        for field in ["course_code", "course_title", "department_division", "units"]:
            self.assertIsNone(result[field])

    def test_unknown_markers(self):
        result = self.parse("Course No: N/A\nCourse Title: Unknown\nDepartment: Not provided\nUnits: TBD")
        for field in ["course_code", "course_title", "department_division", "units"]:
            self.assertIsNone(result[field])

    def test_malformed_codes_not_guessed(self):
        for code in ["GS F F366", "MPBA 523", "CS F12", "CS F1234", "Computing"]:
            with self.subTest(code=code):
                self.assertIsNone(self.parse("Course No: " + code)["course_code"])

    def test_source_filename_page_and_evidence(self):
        text = "Course No: CS F111\nCourse Title: Computing\nUnits: 3-0-3"
        result = extract_course_metadata([
            self.page("", number=1, filename="original.pdf"),
            self.page(text, number=4, filename="original.pdf"),
        ])
        self.assertEqual(result["source_file"], "original.pdf")
        for field, label in [("course_code", "Course No"), ("course_title", "Course Title"),
                             ("units", "Units")]:
            source = result[field]["sources"][0]
            self.assertEqual(source["source_file"], "original.pdf")
            self.assertEqual(source["page_number"], 4)
            self.assertIn(label, source["text"])
            self.assertIn(source["text"], text)

    def test_shared_course_codes(self):
        for codes in ["CS F213 / MAC F212", "ME F216 & MF F216", "EEE / INSTR/ECE F366, F367"]:
            with self.subTest(codes=codes):
                self.assertEqual(self.parse("Course No: " + codes)["course_code"]["value"], codes)

    def test_combined_code_and_title(self):
        result = self.parse("Course Number & Title: CS/SS G 527 Cloud Computing")
        self.assertEqual(result["course_code"]["value"], "CS/SS G527")
        self.assertEqual(result["course_title"]["value"], "Cloud Computing")

    def test_swapped_labels_not_guessed(self):
        result = self.parse("Course No: Converter Technologies\nCourse Title: EEE G554")
        self.assertIsNone(result["course_code"])
        self.assertIsNone(result["course_title"])

    def test_conflicting_codes_on_same_page(self):
        result = self.parse("Course No: CS F111\nCourse No: CS F112")
        self.assertIsNone(result["course_code"])

    def test_earliest_page_preferred(self):
        result = extract_course_metadata([
            self.page("Course Title: Later heading", number=3),
            self.page("Course Title: Original heading", number=1),
        ])
        self.assertEqual(result["course_title"]["value"], "Original heading")
        self.assertEqual(result["course_title"]["sources"][0]["page_number"], 1)

    def test_mixed_source_files_rejected(self):
        with self.assertRaises(ValueError):
            extract_course_metadata([self.page("", filename="a.pdf"),
                                     self.page("", filename="b.pdf")])

    def test_empty_input(self):
        self.assertEqual(extract_course_metadata([]), {
            "source_file": None, "course_code": None, "course_title": None,
            "department_division": None, "units": None,
        })

    def test_real_supplied_handout(self):
        path = Path(__file__).resolve().parents[1] / "data/raw/handouts/001_AN_F314.pdf"
        if not path.is_file():
            self.skipTest("Supplied handout 001_AN_F314.pdf is unavailable")
        pages = extract_pdf_text(path)
        result = extract_course_metadata(pages)
        self.assertEqual(result["source_file"], path.name)
        self.assertEqual(result["course_code"]["value"], "AN F314")
        self.assertEqual(result["course_title"]["value"], "Introduction to Flight")
        self.assertEqual(result["department_division"]["value"],
                         "ACADEMIC - UNDER GRADUATE STUDIES DIVISION")
        self.assertIsNone(result["units"])
        for field in ["course_code", "course_title", "department_division"]:
            source = result[field]["sources"][0]
            self.assertEqual(source["source_file"], path.name)
            self.assertEqual(source["page_number"], 1)
            self.assertIn(source["text"], pages[0]["text"])
