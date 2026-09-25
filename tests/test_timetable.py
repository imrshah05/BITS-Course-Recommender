from copy import deepcopy
import unittest
from preprocessing.timetable import extract_timetable, validate_record


def page(rows, number=1):
    return dict(source_file='timetable.pdf', page_number=number,
                text='II. COURSEWISE TIMETABLE FIRST SEMESTER 2026-2027\nH\n' + rows)


ROW = '2423 AN   F314 INTRODUCTION TO FLIGHT 3 - - - 3 L1 NAME ONE 1233 M W F 10 09/10 AN1 14/12 FN'


class TimetableTests(unittest.TestCase):
    def record(self, row=ROW):
        return extract_timetable([page(row)])['records'][0]

    def test_course(self):
        r = self.record()
        self.assertEqual(r['course_code'], 'AN F314')
        self.assertEqual(r['course_title'], 'INTRODUCTION TO FLIGHT')

    def test_multiple_courses(self):
        result = extract_timetable([page(ROW + '\n' + ROW.replace('AN   F314', 'BIO F211'))])
        self.assertEqual(result['summary']['unique_course_codes'], 2)

    def test_sections_types(self):
        result = extract_timetable([page(ROW + '\nTutorial T1 Name Two 6103 Th 7\nPractical P1 Name Three 2206 M 6 7')])
        self.assertEqual({r['section_type'] for r in result['records']}, {'L', 'T', 'P'})
        self.assertEqual(result['summary']['unique_sections'], 3)

    def test_meetings(self):
        r = self.record(ROW.replace('M W F 10', 'M W 5 Th 10'))
        self.assertEqual(r['meetings'][0]['days'], ['M', 'W'])
        self.assertEqual(r['meetings'][1]['hours'], [10])
        self.assertIsNone(r['start_time'])

    def test_room_instructor(self):
        r = self.record()
        self.assertEqual(r['room'], '1233')
        self.assertEqual(r['instructors'], ['NAME ONE'])

    def test_exam_separation(self):
        r = self.record()
        self.assertEqual(r['midsem_slot'], dict(date='09/10', session='AN1'))
        self.assertEqual(r['compre_slot'], dict(date='14/12', session='FN'))
        self.assertEqual(r['meetings'][0]['hours'], [10])

    def test_missing_optional(self):
        r = self.record(ROW.split('09/10')[0])
        self.assertIsNone(r['midsem_slot'])
        self.assertFalse(r['needs_verification'])

    def test_cancelled(self):
        result = extract_timetable([page(ROW + '\nP2 CANCLED')])
        r = next(r for r in result['records'] if r['section'] == 'P2')
        self.assertEqual(r['status'], 'cancelled')
        self.assertEqual(r['instructors'], [])

    def test_continuation_evidence(self):
        r = self.record(ROW + '\nName Two\nName Three')
        self.assertTrue(r['needs_verification'])
        self.assertEqual(len(r['continuation_evidence']), 2)
        self.assertIn('Name Three', r['sources'][-1]['text'])

    def test_malformed_code(self):
        self.assertFalse(self.record(ROW.replace('F314', 'F31'))['validation']['is_valid'])

    def test_bad_hours(self):
        self.assertFalse(self.record(ROW.replace('F 10', 'F 11'))['validation']['is_valid'])

    def test_ambiguous_days(self):
        self.assertTrue(self.record(ROW.replace('M W F', 'ZZ'))['needs_verification'])

    def test_orphan_section(self):
        r = self.record('L1 NAME 1233 M 1')
        self.assertFalse(r['validation']['is_valid'])
        self.assertIsNone(r['course_code'])

    def test_duplicates_and_conflicts(self):
        result = extract_timetable([page(ROW + '\n' + ROW)])
        self.assertEqual(result['summary']['duplicates'], 1)
        result = extract_timetable([page(ROW + '\n' + ROW.replace('1233', '1234'))])
        self.assertTrue(all(any(i['code']=='conflicting_section_occurrences' for i in r['validation']['issues']) for r in result['records']))

    def test_cross_page_source(self):
        result = extract_timetable([page(ROW), page('Tutorial T1 Name 1233 Th 1', 2)])
        r = next(r for r in result['records'] if r['section']=='T1')
        self.assertEqual(r['sources'][0]['page_number'], 2)
        self.assertEqual(r['course_source']['page_number'], 1)
        self.assertEqual(r['course_source']['source_file'], 'timetable.pdf')

    def test_traceability_validation(self):
        r = self.record(); r['sources'] = []
        self.assertFalse(validate_record(r)['is_valid'])

    def test_deterministic(self):
        pages = [page(ROW), page(ROW.replace('AN   F314', 'BIO F211'), 2)]
        before = deepcopy(pages)
        self.assertEqual(extract_timetable(pages), extract_timetable(list(reversed(pages))))
        self.assertEqual(pages, before)
