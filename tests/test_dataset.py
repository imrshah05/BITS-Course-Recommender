import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from preprocessing.dataset import dataset_from_batch, write_dataset, build_course_dataset


class DatasetTests(unittest.TestCase):
    def document(self, filename, title='Computing'):
        return {'source_file': filename, 'pages': [{'source_file': filename, 'page_number': 1,
                'text': f'Course No: CS F111\nCourse Title: {title}'}]}

    def batch(self):
        return {'documents': [self.document('b.pdf'), self.document('a.pdf')], 'failures': []}

    def test_multiple_and_ordering(self):
        data = dataset_from_batch(self.batch())
        self.assertEqual([r['source']['source_file'] for r in data['records']], ['a.pdf', 'b.pdf'])

    def test_same_code_different_content(self):
        batch = self.batch(); batch['documents'][0] = self.document('b.pdf', 'Different content')
        rows = dataset_from_batch(batch)['records']
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['metadata']['course_title'], rows[1]['metadata']['course_title'])

    def test_missing_values(self):
        row = dataset_from_batch(self.batch())['records'][0]
        self.assertIsNone(row['metadata']['units'])
        self.assertEqual(row['evaluation'], [])

    def test_validation_preserved(self):
        row = dataset_from_batch(self.batch())['records'][0]
        self.assertTrue(any(i['code'] == 'missing_department_division' for i in row['validation']['issues']))

    def test_source_evidence(self):
        row = dataset_from_batch(self.batch())['records'][0]
        source = row['metadata']['course_code']['sources'][0]
        self.assertEqual(source['source_file'], 'a.pdf')
        self.assertEqual(source['page_number'], 1)
        self.assertIn('CS F111', source['text'])

    def test_json_roundtrip_and_directory_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'processed/courses.json'
            data = dataset_from_batch(self.batch())
            write_dataset(data, target)
            self.assertEqual(json.loads(target.read_text()), data)

    def test_rerun_equivalent_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'courses.json'
            with patch('preprocessing.dataset.process_handouts', return_value=self.batch()):
                build_course_dataset(Path(folder) / 'raw', target)
                first = target.read_bytes()
                build_course_dataset(Path(folder) / 'raw', target)
                self.assertEqual(first, target.read_bytes())

    def test_input_order_does_not_change_output(self):
        batch = self.batch(); first = dataset_from_batch(batch)
        batch['documents'].reverse()
        self.assertEqual(first, dataset_from_batch(batch))

    def test_failures_retained(self):
        batch = self.batch(); batch['failures'] = [{'source_file': 'bad.pdf', 'error': 'Unreadable PDF'}]
        data = dataset_from_batch(batch)
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(data['failures'][0]['stage'], 'pdf_extraction')

    def test_normalization_failure_does_not_drop_other_records(self):
        batch = self.batch(); batch['documents'].append({'source_file': 'bad.pdf', 'pages': [{}]})
        data = dataset_from_batch(batch)
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(data['failures'][0]['source_file'], 'bad.pdf')
        self.assertEqual(data['failures'][0]['stage'], 'normalization')

    def test_serialization_failure_preserves_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'courses.json'; target.write_text('original')
            with self.assertRaises(ValueError):
                write_dataset({'value': float('nan')}, target)
            self.assertEqual(target.read_text(), 'original')

    def test_source_output_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                build_course_dataset(folder, Path(folder) / 'courses.json')

    def test_real_byte_identical_handouts_preserved(self):
        source = Path(__file__).resolve().parents[1] / 'data/raw/handouts/001_AN_F314.pdf'
        if not source.exists(): self.skipTest('Supplied handout unavailable')
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / 'raw'; raw.mkdir()
            for filename in ['a.pdf', 'b.pdf']:
                shutil.copyfile(source, raw / filename)
            data = build_course_dataset(raw, Path(folder) / 'out/courses.json')
            self.assertEqual(len(data['records']), 2)
            self.assertEqual(data['failures'], [])
            for row, filename in zip(data['records'], ['a.pdf', 'b.pdf']):
                self.assertEqual(row['source']['source_file'], filename)
                self.assertEqual(row['metadata']['course_code']['value'], 'AN F314')
                self.assertEqual(row['metadata']['course_code']['sources'][0]['source_file'], filename)
