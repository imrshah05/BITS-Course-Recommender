from copy import deepcopy
from pathlib import Path
import unittest

from preprocessing.handout_record import normalize_handout, build_handout_record
from preprocessing.pdf_extractor import extract_pdf_text


class HandoutRecordTests(unittest.TestCase):
    def field(self, value):
        return {'value': value, 'sources': [{'source_file': 'a.pdf', 'page_number': 1, 'text': 'Original evidence\n  unchanged'}]}

    def fixture(self):
        sources = self.field('x')['sources']
        return {
            'source': {'source_file': 'a.pdf', 'page_numbers': [1, 2]},
            'metadata': {k: self.field(v) for k, v in {'course_code': 'CS F111', 'course_title': 'Computing',
                         'department_division': 'Instruction Division', 'units': '3-0-3'}.items()},
            'instructors': [{'label': 'Instructor', 'names': ['Ada Lovelace'], 'text': 'Ada Lovelace', 'sources': sources}],
            'syllabus': [{'heading': 'Course Content', 'text': 'Algebra\n  Calculus', 'sources': sources}],
            'evaluation': [{'name': 'Quiz', 'weightage': '20%', 'marks': '10', 'sources': sources}],
            'exams': {'midsemester': {'name': self.field('Midsem'), 'weightage': self.field('30%')}, 'comprehensive': None},
            'attendance': [{'heading': 'Attendance Policy', 'text': 'Attend\n all classes.', 'sources': sources}],
            'makeup': [], 'observations': [],
        }

    def codes(self, result):
        return {i['code'] for i in result['validation']['issues']}

    def test_complete_record(self):
        r = normalize_handout(self.fixture())
        self.assertTrue(r['validation']['is_valid'])
        self.assertEqual(r['validation']['issues'], [])
        self.assertEqual(r['metadata']['course_codes'], ['CS F111'])
        self.assertEqual(r['metadata']['course_identity_type'], 'single')
        self.assertEqual(r['candidate_status'], 'usable')
        self.assertIn('evaluation', r)

    def test_structured_composite_identity(self):
        data = self.fixture(); data['metadata']['course_code'] = self.field('CE F434/BITS F494')
        r = normalize_handout(data)
        self.assertEqual(r['metadata']['course_code']['value'], 'CE F434/BITS F494')
        self.assertEqual(r['metadata']['course_codes'], ['CE F434', 'BITS F494'])
        self.assertEqual(r['metadata']['course_identity_type'], 'multiple')
        self.assertEqual(r['original']['metadata']['course_code']['value'], 'CE F434/BITS F494')
        self.assertEqual(r['candidate_status'], 'usable')

    def test_unidentified_record_is_not_candidate(self):
        data = self.fixture(); data['metadata']['course_code'] = None
        r = normalize_handout(data)
        self.assertEqual(r['metadata']['course_codes'], [])
        self.assertEqual(r['metadata']['course_identity_type'], 'unresolved')
        self.assertEqual(r['candidate_status'], 'unusable_identity')

    def test_whitespace(self):
        data = self.fixture()
        data['metadata']['course_title']['value'] = '  Introduction   to\n Computing '
        self.assertEqual(normalize_handout(data)['metadata']['course_title']['value'], 'Introduction to Computing')

    def test_course_code_spacing(self):
        data = self.fixture(); data['metadata']['course_code'] = self.field('cs f 111')
        r = normalize_handout(data)
        self.assertEqual(r['metadata']['course_code']['value'], 'CS F111')
        self.assertEqual(r['original']['metadata']['course_code']['value'], 'cs f 111')

    def test_blank_optional_value(self):
        data = self.fixture(); data['metadata']['units'] = self.field('   ')
        r = normalize_handout(data)
        self.assertIsNone(r['metadata']['units'])
        self.assertTrue(r['validation']['is_valid'])

    def test_invalid_observation_structure(self):
        data = self.fixture(); data['observations'] = [None]
        self.assertIn('invalid_structure', self.codes(normalize_handout(data)))

    def test_missing_lists(self):
        data = self.fixture(); data['makeup'] = None
        self.assertEqual(normalize_handout(data)['makeup'], [])

    def test_exact_duplicate(self):
        data = self.fixture(); data['instructors'] *= 2
        r = normalize_handout(data)
        self.assertEqual(len(r['instructors']), 1)
        self.assertIn('duplicate_item', self.codes(r))

    def test_duplicate_names(self):
        data = self.fixture(); data['instructors'][0]['names'] *= 2
        r = normalize_handout(data)
        self.assertEqual(r['instructors'][0]['names'], ['Ada Lovelace'])
        self.assertIn('duplicate_instructor', self.codes(r))

    def test_missing_code(self):
        data = self.fixture(); data['metadata']['course_code'] = None
        self.assertIn('missing_course_code', self.codes(normalize_handout(data)))

    def test_malformed_code(self):
        data = self.fixture(); data['metadata']['course_code'] = self.field('CS 12')
        r = normalize_handout(data)
        self.assertIn('malformed_course_code', self.codes(r))
        self.assertEqual(r['metadata']['course_code']['value'], 'CS 12')

    def test_missing_title(self):
        data = self.fixture(); data['metadata']['course_title'] = None
        self.assertIn('missing_course_title', self.codes(normalize_handout(data)))

    def test_optional_units(self):
        data = self.fixture(); data['metadata']['units'] = None
        self.assertTrue(normalize_handout(data)['validation']['is_valid'])

    def test_department_warning(self):
        data = self.fixture(); data['metadata']['department_division'] = None
        r = normalize_handout(data)
        self.assertTrue(r['validation']['is_valid'])
        self.assertEqual(r['validation']['issues'][0]['severity'], 'warning')

    def test_conflicts_preserved(self):
        pages = [{'source_file': 'a.pdf', 'page_number': i, 'text': f'Course No: CS F11{i}\nCourse Title: Computing\nMidsem 30% {i:02}/10/26'} for i in (1, 2)]
        r = build_handout_record(pages)
        paths = {i['path'] for i in r['validation']['issues'] if i['code'] == 'conflicting_values'}
        self.assertIn('metadata.course_code', paths)
        self.assertIn('exams.midsemester.date', paths)
        self.assertEqual(len(r['observations']), 2)
        self.assertTrue(r['validation']['needs_verification'])

    def test_malformed_evaluation(self):
        data = self.fixture(); data['evaluation'] = [{'marks': 'abc', 'sources': self.field('x')['sources']}]
        codes = self.codes(normalize_handout(data))
        self.assertIn('malformed_evaluation', codes)
        self.assertIn('invalid_numeric', codes)

    def test_invalid_percentages(self):
        for value in ('101%', '-5%'):
            data = self.fixture(); data['evaluation'][0]['weightage'] = value
            self.assertIn('invalid_percentage', self.codes(normalize_handout(data)))

    def test_invalid_exam_percentage(self):
        data = self.fixture(); data['exams']['midsemester']['weightage'] = self.field('120%')
        self.assertIn('invalid_percentage', self.codes(normalize_handout(data)))

    def test_traceability(self):
        data = self.fixture(); data['metadata']['course_code']['sources'][0]['page_number'] = 8
        self.assertIn('incomplete_traceability', self.codes(normalize_handout(data)))

    def test_preserves_evidence_and_input(self):
        data = self.fixture(); before = deepcopy(data)
        r = normalize_handout(data)
        self.assertEqual(data, before)
        self.assertEqual(r['original'], before)
        self.assertEqual(r['syllabus'][0]['text'], before['syllabus'][0]['text'])
        self.assertEqual(r['attendance'][0]['text'], before['attendance'][0]['text'])
        self.assertEqual(r['metadata']['course_code']['sources'], before['metadata']['course_code']['sources'])

    def test_real_handout(self):
        path = Path(__file__).resolve().parents[1] / 'data/raw/handouts/001_AN_F314.pdf'
        if not path.exists(): self.skipTest('Supplied handout unavailable')
        r = build_handout_record(extract_pdf_text(path))
        self.assertEqual(r['source']['source_file'], path.name)
        self.assertEqual(r['metadata']['course_code']['value'], 'AN F314')
        self.assertTrue(r['instructors'])
        self.assertTrue(r['makeup'])
        self.assertTrue(r['validation']['is_valid'])
