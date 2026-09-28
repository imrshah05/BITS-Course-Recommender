import copy
import tempfile
import unittest
from pathlib import Path
from pypdf import PdfReader, PdfWriter

from preprocessing.pdf_extractor import extract_pdf_text
from preprocessing.programme_requirements import (
    connect_discipline_elective_lists, extract_programme_requirements,
    propagate_first_degree_open_electives,
    recover_first_degree_open_elective_requirement, validate_requirement,
)


def page(text, number=1):
    return dict(source_file='bulletin.pdf', page_number=number, text='IV-106\n' + text)


def catalogue(rows='CS F211 Data Structures & Algorithms 3 1 4'):
    return [page('List of Courses for B.E. / M.Sc. / B.Pharm.\nCOMPUTER SCIENCE\nCORE COURSES L P U\n' + rows)]


class ProgrammeRequirementsTests(unittest.TestCase):
    def test_ranged_open_elective_requirement_is_recovered_and_inherited(self):
        source = {"source_file": "bulletin.pdf", "page_number": 209,
                  "text": "The category-wise structure of each program:\n"
                          "Open Electives 15 to 27 5 to 9"}
        dataset = {
            "source_file": "bulletin.pdf",
            "programmes": [
                {"id": "general", "name": "INTEGRATED FIRST DEGREE PROGRAMMES",
                 "context": "category-wise structure", "sources": [source]},
                {"id": "cs", "name": "B. E. Computer Science",
                 "context": "semester-wise chart", "sources": [{
                     "source_file": "bulletin.pdf", "page_number": 217,
                     "text": "B. E. Computer Science chart"}]},
            ],
            "requirements": [],
        }
        recover_first_degree_open_elective_requirement(dataset)
        propagate_first_degree_open_electives(dataset)
        rules = [item for item in dataset["requirements"]
                 if item.get("category") == "Open Electives"]
        self.assertEqual(len(rules), 2)
        inherited = next(item for item in rules
                         if item["programme_name"] == "B. E. Computer Science")
        self.assertEqual((inherited["min_units"], inherited["max_units"]), (15, 27))
        self.assertEqual((inherited["min_count"], inherited["max_count"]), (5, 9))
        self.assertEqual(inherited["inherited_from_programme"],
                         "INTEGRATED FIRST DEGREE PROGRAMMES")

    def test_complete_core_set_connects_chart_to_elective_membership(self):
        source = {"source_file": "bulletin.pdf", "page_number": 1, "text": "evidence"}
        dataset = {
            "programmes": [
                {"id": "chart", "name": "B. E. Computer Science",
                 "context": "semester-wise chart", "sources": [source]},
                {"id": "list", "name": "COMPUTER SCIENCE",
                 "context": "discipline course list", "sources": [source]},
            ],
            "requirements": [
                {"id": "total", "kind": "category_total", "programme_id": "chart",
                 "programme_name": "B. E. Computer Science", "category": "Discipline Core",
                 "course_count": 2, "sources": [source]},
                *[{"id": f"chart-{code}", "kind": "required_course",
                   "programme_id": "chart", "programme_name": "B. E. Computer Science",
                   "course_code": code, "needs_verification": False, "sources": [source]}
                  for code in ("CS F211", "CS F212")],
                *[{"id": f"core-{code}", "kind": "required_course",
                   "programme_id": "list", "programme_name": "COMPUTER SCIENCE",
                   "category": "CORE COURSES", "course_code": code, "sources": [source]}
                  for code in ("CS F211", "CS F212")],
                {"id": "elective", "kind": "elective_option", "programme_id": "list",
                 "programme_name": "COMPUTER SCIENCE", "category": "DISCIPLINE ELECTIVE COURSES",
                 "course_code": "CS F437", "course_title": "Generative Artificial Intelligence",
                 "units": 4, "sources": [source]},
            ],
        }
        result = connect_discipline_elective_lists(dataset)
        membership = [item for item in result["requirements"]
                      if item.get("kind") == "elective_membership"]
        self.assertEqual([(item["programme_name"], item["course_code"])
                          for item in membership],
                         [("B. E. Computer Science", "CS F437")])
        self.assertFalse(membership[0]["needs_verification"])

    def test_heading_name_and_scope(self):
        result = extract_programme_requirements(catalogue())
        self.assertEqual(result['programmes'][0]['name'], 'COMPUTER SCIENCE')
        self.assertEqual(result['requirements'][0]['programme_id'], result['programmes'][0]['id'])

    def test_required_course_title_units(self):
        row = extract_programme_requirements(catalogue())['requirements'][0]
        self.assertEqual((row['kind'], row['course_code'], row['course_title'], row['units']),
                         ('required_course', 'CS F211', 'Data Structures & Algorithms', 4))

    def test_elective_not_required(self):
        row = extract_programme_requirements(catalogue('DISCIPLINE ELECTIVE COURSES\nCS F407 Artificial Intelligence 3 0 3'))['requirements'][0]
        self.assertEqual(row['kind'], 'elective_option')

    def test_multiline(self):
        row = extract_programme_requirements(catalogue('CS F211 Data Structures &\nAlgorithms\n3 1 4'))['requirements'][0]
        self.assertEqual(row['course_title'], 'Data Structures & Algorithms')

    def test_totals_and_chart_scope(self):
        p = page('Semester-wise Pattern for Students Admitted to B. E. Computer Science Programme\nYear First Semester U Second Semester U\nI\nDiscipline Core -48 Units (14 Courses)')
        result = extract_programme_requirements([p])
        row = next(r for r in result['requirements'] if r['kind'] == 'category_total')
        self.assertEqual((row['units'], row['course_count']), (48, 14))
        self.assertIsNone(row['semester'])
        self.assertIn('First Semester', result['programmes'][0]['sources'][0]['text'])

    def test_minor_quantities_and_footnotes(self):
        p = page('Minor in Aeronautics\n06 courses (min) 18 units (min)\nCore Courses\nAN F311 Principles of Aerodynamics 3 0 3\n* Special conditions apply')
        result = extract_programme_requirements([p])
        self.assertEqual(len([r for r in result['requirements'] if r['kind'] == 'quantity']), 2)
        self.assertIn('* Special conditions', result['requirements'][-1]['sources'][-1]['text'])

    def test_missing_unknown(self):
        row = extract_programme_requirements(catalogue())['requirements'][0]
        self.assertIsNone(row['year'])
        self.assertIsNone(extract_programme_requirements(catalogue())['programmes'][0]['programme_code'])

    def test_malformed_code_validation(self):
        row = extract_programme_requirements(catalogue())['requirements'][0]
        row['course_code'] = 'CS F21'
        self.assertFalse(validate_requirement(row)['is_valid'])

    def test_malformed_numeric_and_source(self):
        self.assertFalse(validate_requirement({'units': -1, 'sources': []})['is_valid'])

    def test_ambiguous_choice_not_mandatory(self):
        result = extract_programme_requirements(catalogue('MATH F212 Optimization 3 0 3\nOR\nME F344 Engineering Optimization 2 0 2'))
        self.assertFalse(any(r['kind'] == 'required_course' for r in result['requirements']))
        self.assertTrue(any(r['kind'] == 'unresolved_choice' for r in result['requirements']))

    def test_damaged_table_retained(self):
        result = extract_programme_requirements(catalogue('CS F211 / CS F212\nData Algorithms\n3 4'))
        self.assertTrue(result['requirements'][-1]['needs_verification'])
        self.assertIn('Data Algorithms', str(result['requirements'][-1]['sources']))

    def test_source_traceability(self):
        row = extract_programme_requirements(catalogue())['requirements'][0]
        self.assertEqual(row['sources'][0]['source_file'], 'bulletin.pdf')
        self.assertEqual(row['sources'][0]['page_number'], 1)
        self.assertIn(row['course_code'], row['sources'][0]['text'])

    def test_unknown_scope_not_inferred(self):
        result = extract_programme_requirements([page('List of Courses for B.E.\nCORE COURSES L P U\nCS F211 Algorithms 3 0 3')])
        self.assertIsNone(result['requirements'][0]['programme_name'])
        self.assertTrue(result['requirements'][0]['needs_verification'])

    def test_scope_switch(self):
        result = extract_programme_requirements(catalogue('CS F211 Algorithms 3 0 3\nCHEMISTRY\nCORE COURSES L P U\nCHEM F211 Chemistry 3 0 3'))
        self.assertEqual([p['name'] for p in result['programmes']], ['COMPUTER SCIENCE', 'CHEMISTRY'])

    def test_explicit_institutional_pool_scope_and_boundary(self):
        pages = [page('List of Courses for B.E.\nCORE COURSES L P U\nBBA F121 Business Ethics 3 0 3\n'
                      'DISCIPLINE ELECTIVE COURSES L P U\n'
                      'Pool of Humanities courses for first degree programmes:\n'
                      'HSS F221 Readings from Drama 3 0 3', 10),
                 page('HSS F222 Linguistics 3 0 3\nOther Courses\n'
                      'BITS F211 Introduction to IPR 1 0 1', 11)]
        result = extract_programme_requirements(pages)
        rows = {row['course_code']: row for row in result['requirements'] if row['course_code']}
        scope = 'Pool of Humanities courses for first degree programmes'
        self.assertIsNone(rows['BBA F121']['programme_name'])
        self.assertEqual(rows['HSS F221']['programme_name'], scope)
        self.assertEqual(rows['HSS F222']['programme_name'], scope)
        self.assertEqual(rows['HSS F221']['category'], 'Humanities Electives')
        self.assertIsNone(rows['BITS F211']['programme_name'])
        self.assertFalse(rows['HSS F221']['needs_verification'])

    def test_determinism_no_mutation(self):
        pages = catalogue(); original = copy.deepcopy(pages)
        self.assertEqual(extract_programme_requirements(pages), extract_programme_requirements(pages))
        self.assertEqual(pages, original)

    def test_real_supplied_page(self):
        # A temporary one-page copy keeps integration fast; source PDF is read-only.
        source = Path(__file__).resolve().parents[1] / 'data/raw/bulletin.pdf'
        with tempfile.TemporaryDirectory() as folder:
            writer = PdfWriter()
            with source.open('rb') as stream:
                writer.add_page(PdfReader(stream).pages[337])
                target = Path(folder) / 'bulletin.pdf'
                writer.write(target)
            pages = extract_pdf_text(target)
            pages[0]['page_number'] = 338
            result = extract_programme_requirements(pages)
        self.assertIn('Minor in Aeronautics', [p['name'] for p in result['programmes']])
        self.assertTrue(any(r['course_code'] == 'AN F311' and r['units'] == 3 for r in result['requirements']))
        self.assertTrue(all(r['sources'][0]['page_number'] == 338 for r in result['requirements']))


class RealSemesterChartRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / 'data/raw/bulletin.pdf'
        numbers = [217, 233, 292]
        with source.open('rb') as stream:
            reader = PdfReader(stream)
            cls.pages = [{
                'source_file': 'bulletin.pdf', 'page_number': number,
                'text': reader.pages[number - 1].extract_text() or '',
            } for number in numbers]
        from preprocessing.pdf_extractor import extract_pdf_layout_pages
        from preprocessing.programme_requirements import _load_catalogue_codes
        cls.layout_pages = extract_pdf_layout_pages(source, numbers)
        cls.known_codes = _load_catalogue_codes(
            source.parents[1] / 'processed/course_catalogue.json')
        cls.result = extract_programme_requirements(
            cls.pages, cls.layout_pages, cls.known_codes)

    def rows(self, programme):
        programme_id = next(item['id'] for item in self.result['programmes']
                            if item['name'] == programme)
        return {item['course_code']: item for item in self.result['requirements']
                if item.get('programme_id') == programme_id and item.get('course_code')}

    def test_be_computer_science_year_and_semester(self):
        rows = self.rows('B. E. Computer Science')
        self.assertEqual((rows['BITS F103']['year'], rows['BITS F103']['semester']),
                         (1, 1))
        self.assertEqual((rows['CS F211']['year'], rows['CS F211']['semester']),
                         (2, 2))
        self.assertEqual((rows['CS F351']['year'], rows['CS F351']['semester']),
                         (3, 1))
        self.assertTrue(rows['ECON F211']['needs_verification'])
        self.assertTrue(rows['MGTS F211']['needs_verification'])

    def test_msc_physics_year_and_semester(self):
        rows = self.rows('M. Sc. Physics')
        self.assertEqual((rows['PHY F211']['year'], rows['PHY F211']['semester']),
                         (2, 1))
        self.assertEqual((rows['PHY F241']['year'], rows['PHY F241']['semester']),
                         (2, 2))
        self.assertEqual((rows['PHY F341']['year'], rows['PHY F341']['semester']),
                         (3, 2))

    def test_composite_dual_degree_chart(self):
        rows = self.rows('M.Sc. Physics with B.E. Computer Science')
        self.assertEqual((rows['PHY F211']['year'], rows['PHY F211']['semester']),
                         (2, 1))
        self.assertEqual((rows['CS F211']['year'], rows['CS F211']['semester']),
                         (3, 2))
        self.assertEqual((rows['CS F351']['year'], rows['CS F351']['semester']),
                         (4, 1))
        self.assertFalse(any(item['year'] == 1 for item in rows.values()))
        self.assertTrue(rows['BITS F423T']['needs_verification'])
        reference = next(item for item in self.result['requirements']
                         if item['programme_name'] ==
                         'M.Sc. Physics with B.E. Computer Science'
                         and item['kind'] == 'curriculum_reference')
        self.assertEqual(reference['referenced_programme_name'], 'M. Sc. Physics')
        self.assertEqual(reference['covered_periods'], [
            {'year': 1, 'semester': 1}, {'year': 1, 'semester': 2}])
        self.assertFalse(reference['needs_verification'])
        self.assertEqual(reference['sources'][0]['page_number'], 292)

    def test_catalogue_validation_and_traceability(self):
        rows = self.rows('B. E. Computer Science')
        self.assertEqual(rows['BITS F103']['catalogue_status'], 'matched')
        self.assertFalse(rows['BITS F103']['needs_verification'])
        self.assertEqual(rows['BITS F103']['sources'][0]['page_number'], 217)
        self.assertIn('BITS', rows['BITS F103']['sources'][0]['text'])
        self.assertEqual(rows['BITS F101']['catalogue_status'], 'matched')
        self.assertFalse(rows['BITS F101']['needs_verification'])
