import unittest

from preprocessing.course_codes import normalize_course_code, parse_course_codes


class CourseCodeTests(unittest.TestCase):
    def test_source_backed_families(self):
        for value in ('MATH U101', 'BITS C790T', 'BITS E574', 'BITS F101-1'):
            self.assertEqual(normalize_course_code(value), value)

    def test_spacing_normalization(self):
        self.assertEqual(normalize_course_code('gsf213'), 'GS F213')

    def test_rejects_malformed(self):
        for value in ('CS F21', 'course CS F211', 'CS 211', 'F211'):
            self.assertIsNone(normalize_course_code(value))

    def test_explicit_multiple_codes(self):
        codes, resolved = parse_course_codes('CE F434/BITS F494')
        self.assertEqual(codes, ['CE F434', 'BITS F494'])
        self.assertTrue(resolved)

    def test_shared_prefix_group(self):
        codes, resolved = parse_course_codes('CS/SS G527')
        self.assertEqual(codes, ['CS G527', 'SS G527'])
        self.assertTrue(resolved)

    def test_multiple_prefixes_and_designators(self):
        codes, resolved = parse_course_codes('EEE / INSTR/ECE F366, F367')
        self.assertEqual(codes, ['EEE F366', 'EEE F367', 'INSTR F366', 'INSTR F367',
                                 'ECE F366', 'ECE F367'])
        self.assertTrue(resolved)

    def test_unresolved_expression_preserves_partial_codes(self):
        codes, resolved = parse_course_codes('CS F211 with unknown text')
        self.assertEqual(codes, ['CS F211'])
        self.assertFalse(resolved)
