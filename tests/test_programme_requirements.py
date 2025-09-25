import copy
import tempfile
import unittest
from pathlib import Path
from pypdf import PdfReader, PdfWriter

from preprocessing.pdf_extractor import extract_pdf_text
from preprocessing.programme_requirements import extract_programme_requirements, validate_requirement


def page(text, number=1):
    return dict(source_file='bulletin.pdf', page_number=number, text='IV-106\n' + text)


def catalogue(rows='CS F211 Data Structures & Algorithms 3 1 4'):
    return [page('List of Courses for B.E. / M.Sc. / B.Pharm.\nCOMPUTER SCIENCE\nCORE COURSES L P U\n' + rows)]


class ProgrammeRequirementsTests(unittest.TestCase):
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
