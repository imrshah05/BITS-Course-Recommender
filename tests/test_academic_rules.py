from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from preprocessing.academic_rules import normalize_academic_rules, normalize_record, build_academic_rules


def record(**fields):
    row = dict(id='r1', kind='category_total', category='Discipline Core', programme_name='Programme A',
               units=12, course_count=4, needs_verification=False,
               sources=[dict(source_file='bulletin.pdf', page_number=3, text='Programme A: 12 units, 4 courses')])
    row.update(fields)
    return row


def normalized(**fields):
    return normalize_record(record(**fields), 'Bulletin', '/requirements/0')


class AcademicRulesTests(unittest.TestCase):
    def test_regulations(self):
        row = record(rule_type='descriptive_policy', scope_and_exceptions='Except with approval', needs_verification=True)
        result = normalize_academic_rules({'rules': [row]}, {})['records'][0]
        self.assertEqual(result['source_document'], 'Academic Regulations')
        self.assertEqual(result['scope_and_exceptions'], 'Except with approval')
        self.assertEqual(result['classification'], 'descriptive')

    def test_bulletin(self):
        row = normalized()
        self.assertEqual((row['required_count'], row['required_units']), (4, 12))
        self.assertEqual(row['classification'], 'deterministic')

    def test_optional_unknown(self):
        row = normalized()
        self.assertIsNone(row['scope']['campus'])
        self.assertIsNone(row['prerequisites'])
        self.assertTrue(row['validation']['is_valid'])

    def test_safe_category(self):
        row = normalized(category=' CORE   COURSES ')
        self.assertEqual(row['category_key'], 'core courses')
        self.assertEqual(row['original']['category'], ' CORE   COURSES ')

    def test_no_category_equivalence(self):
        self.assertNotEqual(normalized(category='CDC')['category_key'], normalized(category='Core Courses')['category_key'])

    def test_course_code(self):
        row = normalized(kind='required_course', course_code=' cs   F211 ', course_title=' Algorithms ')
        self.assertEqual(row['course_code'], 'CS F211')
        self.assertEqual(row['course_title'], 'Algorithms')

    def test_malformed_code(self):
        row = normalized(course_code='CS F21')
        self.assertFalse(row['validation']['is_valid'])
        self.assertEqual(row['course_code'], 'CS F21')

    def test_numeric_strings(self):
        row = normalized(units='12.0', course_count='4')
        self.assertEqual(row['required_units'], 12)
        self.assertIs(type(row['required_count']), int)

    def test_negative_fractional_boolean(self):
        for value in [-1, 1.5, True, 'unknown']:
            self.assertFalse(normalized(course_count=value)['validation']['is_valid'])

    def test_min_max(self):
        row = normalized(kind='quantity', comparator='min', units='15', course_count=None)
        self.assertEqual(row['min_units'], 15)
        self.assertIsNone(row['required_units'])
        self.assertFalse(normalized(min_units=20, max_units=10)['validation']['is_valid'])

    def test_choices(self):
        choice = dict(select_count=1, options=['CS F211', 'CS F212'])
        row = normalized(kind='choice', alternatives=choice)
        self.assertEqual(row['alternatives'], choice)
        self.assertEqual(row['classification'], 'deterministic')

    def test_malformed_choices(self):
        for choice in [[], {'select_count': 3, 'options': ['CS F211', 'CS F212']}, {'options': []}]:
            self.assertFalse(normalized(kind='choice', alternatives=choice)['validation']['is_valid'])
        self.assertTrue(normalized(kind='unresolved_choice')['needs_verification'])

    def test_traceability_original(self):
        row = normalized()
        self.assertEqual(row['original_reference'], '/requirements/0')
        self.assertEqual(row['sources'][0]['page_number'], 3)
        self.assertEqual(row['original'], record())
        self.assertFalse(normalized(sources=[])['validation']['is_valid'])

    def test_verification_and_flags(self):
        self.assertTrue(normalized(needs_verification=True)['needs_verification'])
        row = normalized(validation={'is_valid': False, 'issues': []})
        self.assertIn('inconsistent_source_validation', [i['code'] for i in row['validation']['issues']])

    def test_missing_required_course_and_scope(self):
        self.assertFalse(normalized(kind='required_course')['validation']['is_valid'])
        row = normalized(programme_name=None)
        self.assertTrue(row['needs_verification'])
        self.assertTrue(row['validation']['is_valid'])

    def test_duplicates_preserved(self):
        data = normalize_academic_rules({}, {'requirements': [record(), record(id='r2')]})
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(data['validation_summary']['duplicates'], 1)

    def test_cross_source_conflict(self):
        data = normalize_academic_rules({'rules': [record(units=15)]}, {'requirements': [record()]})
        self.assertEqual(len(data['possible_cross_source_conflicts']), 1)
        self.assertTrue(all(r['needs_verification'] for r in data['records']))

    def test_no_conflict_for_different_scope_or_category(self):
        for changed in [dict(programme_name='Programme B'), dict(category='CDC'), dict(needs_verification=True)]:
            data = normalize_academic_rules({'rules': [record(**changed)]}, {'requirements': [record(units=99)]})
            self.assertFalse(data['possible_cross_source_conflicts'])

    def test_unresolved_retained(self):
        original = {'source': record()['sources'][0], 'needs_verification': True, 'section': 'IV-2'}
        data = normalize_academic_rules({}, {'unresolved_sections': [original]})
        self.assertEqual(data['records'][0]['original'], original)
        self.assertEqual(data['records'][0]['rule_type'], 'unresolved_section')

    def test_determinism_and_no_mutation(self):
        data = {'requirements': [record(), record(id='r2')]}; before = deepcopy(data)
        first = normalize_academic_rules({}, data)
        self.assertEqual(first, normalize_academic_rules({}, data))
        self.assertEqual(first['records'], sorted(first['records'], key=lambda r: r['rule_id']))
        self.assertEqual(data, before)

    def test_build_and_input_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, out = [Path(folder) / n for n in ('a.json', 'b.json', 'out.json')]
            a.write_text(json.dumps({'rules': []})); b.write_text(json.dumps({'requirements': [record()]}))
            result = build_academic_rules(a, b, out)
            self.assertEqual(json.loads(out.read_text()), result)
            self.assertEqual(len(result['source_inputs']), 2)
            with self.assertRaises(ValueError):
                build_academic_rules(a, b, a)
