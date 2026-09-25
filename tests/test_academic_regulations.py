import copy
import unittest
from pathlib import Path

from preprocessing.academic_regulations import extract_academic_regulations, validate_rule
from preprocessing.pdf_extractor import extract_pdf_text


def fixture(text='A minimum CGPA of 4.50 is required, unless approval is granted.'):
    return [dict(source_file='rules.pdf', page_number=1, text='Contents\n1. Graduation 2'),
            dict(source_file='rules.pdf', page_number=2, text='1. Graduation\n' + text)]


class RegulationsTests(unittest.TestCase):
    def rule(self, text=None):
        return extract_academic_regulations(fixture() if text is None else fixture(text))['rules'][0]

    def test_heading(self):
        self.assertEqual(self.rule()['section'], {'number': '1', 'heading': 'Graduation'})

    def test_explicit_numeric_observation(self):
        value = self.rule()['values'][0]
        self.assertEqual((value['metric'], value['comparator'], value['value']), ('CGPA', 'minimum', 4.5))
        self.assertFalse(value['scope_resolved'])

    def test_standalone_explicit_requirement(self):
        rule = self.rule('The minimum CGPA of 4.50 is required.')
        self.assertTrue(rule['machine_checkable'])
        self.assertEqual(rule['rule_type'], 'explicit_numeric_requirement')

    def test_scope_and_exception(self):
        text = 'For higher degree students, a minimum CGPA of 5.50 is required, except with approval.'
        rule = self.rule(text)
        self.assertIn(text, rule['scope_and_exceptions'])
        self.assertTrue(rule['needs_verification'])
        self.assertFalse(rule['machine_checkable'])

    def test_descriptive_not_constraint(self):
        rule = self.rule('The Dean may permit additional courses.')
        self.assertEqual(rule['values'], [])
        self.assertEqual(rule['rule_type'], 'descriptive_policy')

    def test_missing_values(self):
        self.assertEqual(self.rule('Consult the appropriate committee.')['values'], [])

    def test_table_not_guessed(self):
        rule = self.rule('Programme Units\nA B\n64 72')
        self.assertEqual(rule['values'], [])
        self.assertIn('uninterpreted_numbers', [i['code'] for i in rule['validation']['issues']])

    def test_traceability(self):
        rule = self.rule()
        source = rule['sources'][0]
        self.assertEqual((source['source_file'], source['page_number']), ('rules.pdf', 2))
        self.assertIn(rule['values'][0]['evidence'], source['text'])

    def test_continuation(self):
        pages = fixture('Conditions apply:')
        pages.append(dict(source_file='rules.pdf', page_number=3, text='except where approved.\n1.01'))
        rule = extract_academic_regulations(pages)['rules'][0]
        self.assertIn('except where approved.', rule['scope_and_exceptions'])
        self.assertEqual(rule['clause_labels'], ['1.01'])
        self.assertEqual([s['page_number'] for s in rule['sources']], [2, 3])

    def test_malformed(self):
        rule = self.rule()
        rule['values'][0]['value'] = 'unknown'
        rule['sources'] = []
        rule['scope_and_exceptions'] = None
        result = validate_rule(rule)
        self.assertFalse(result['is_valid'])
        self.assertTrue({'malformed_value', 'missing_source', 'incomplete_scope'} <= {i['code'] for i in result['issues']})

    def test_different_scoped_values_flagged(self):
        rule = self.rule('A minimum CGPA of 4.50 applies to A; a minimum CGPA of 5.50 applies to B.')
        self.assertIn('multiple_scoped_values', [i['code'] for i in rule['validation']['issues']])

    def test_deterministic_without_mutation(self):
        pages = fixture()
        original = copy.deepcopy(pages)
        self.assertEqual(extract_academic_regulations(pages), extract_academic_regulations(list(reversed(pages))))
        self.assertEqual(pages, original)

    def test_missing_section_fails(self):
        pages = fixture()
        pages[0]['text'] += '\n2. Registration 3'
        with self.assertRaises(ValueError):
            extract_academic_regulations(pages)

    def test_missing_contents_fails(self):
        with self.assertRaises(ValueError):
            extract_academic_regulations(fixture()[1:])

    def test_real_regulations(self):
        source = Path(__file__).resolve().parents[1] / 'data/raw/Academic-Regulations-2023.pdf'
        result = extract_academic_regulations(extract_pdf_text(source))
        self.assertEqual(result['pages_processed'], 70)
        self.assertEqual(len(result['rules']), 13)
        graduation = result['rules'][8]
        self.assertIn('minor', graduation['text'])
        self.assertIn(4.5, [v['value'] for v in graduation['values']])
        self.assertTrue(all(r['validation']['is_valid'] and r['sources'] for r in result['rules']))
