import unittest
from pathlib import Path

from preprocessing.handout_content import extract_handout_content
from preprocessing.pdf_extractor import extract_pdf_text


class HandoutContentTests(unittest.TestCase):
    def parse(self, text, page=1):
        return extract_handout_content([{'source_file': 'sample.pdf', 'page_number': page, 'text': text}])

    def test_one_instructor(self):
        self.assertEqual(self.parse('Instructor: Ada Lovelace')['instructors'][0]['names'], ['Ada Lovelace'])

    def test_in_charge(self):
        block = self.parse('Instructor-in-Charge: Dr. Ada Lovelace')['instructors'][0]
        self.assertEqual(block['label'], 'Instructor-in-Charge')
        self.assertEqual(block['names'], ['Dr. Ada Lovelace'])

    def test_multiple_instructors(self):
        block = self.parse('Team of Instructors: Ada Lovelace, Alan Turing and Grace Hopper')['instructors'][0]
        self.assertEqual(block['names'], ['Ada Lovelace', 'Alan Turing', 'Grace Hopper'])

    def test_whitespace(self):
        block = self.parse('Instructor\n in charge :\n Dr.\n Ada\n Lovelace')['instructors'][0]
        self.assertEqual(block['names'], ['Dr. Ada Lovelace'])

    def test_columns(self):
        text = 'Course No\nCourse Title\nInstructor-in-Charge\nInstructors\n: CS F111\n: Computing\n: Ada Lovelace\n: Alan Turing, Grace Hopper\n'
        result = self.parse(text)
        self.assertEqual([b['names'] for b in result['instructors']],
                         [['Ada Lovelace'], ['Alan Turing', 'Grace Hopper']])

    def test_contacts(self):
        block = self.parse('Instructor: Ada Lovelace (ada@example.com), Alan Turing (Room 123)')['instructors'][0]
        self.assertEqual(block['names'], ['Ada Lovelace', 'Alan Turing'])

    def test_syllabus(self):
        result = self.parse('Syllabus:\nVectors and matrices.\nEvaluation Scheme:\nNot content.')
        self.assertEqual(result['syllabus'][0]['text'], 'Vectors and matrices.')

    def test_multiline_topics(self):
        content = 'Module 1: Algebra\n1. Vectors\n2. Matrices\nModule 2: Calculus'
        result = self.parse('4. Course Content:\n' + content + '\n5. Attendance Policy:\nRules')
        self.assertEqual(result['syllabus'][0]['text'], content)

    def test_course_plan(self):
        result = self.parse('6. Course Plan:\nWeek Topic\n1 Algebra\n2 Calculus\n7. Evaluation:\nExam')
        self.assertEqual(result['syllabus'][0]['text'], 'Week Topic\n1 Algebra\n2 Calculus')

    def test_missing_instructors(self):
        self.assertEqual(self.parse('Syllabus: Algebra')['instructors'], [])

    def test_missing_syllabus(self):
        self.assertEqual(self.parse('Instructor: Ada Lovelace')['syllabus'], [])

    def test_no_fabrication(self):
        result = self.parse('Ada Lovelace discusses algebra and syllabus design.')
        self.assertEqual(result['instructors'], [])
        self.assertEqual(result['syllabus'], [])

    def test_placeholders(self):
        result = self.parse('Instructor: To be announced\nSyllabus: N/A')
        self.assertEqual(result['instructors'], [])
        self.assertEqual(result['syllabus'], [])

    def test_traceability(self):
        text = 'Instructor: Ada Lovelace\nCourse Description:\nAlgebra and calculus.'
        result = self.parse(text, 3)
        self.assertEqual(result['source_file'], 'sample.pdf')
        for item in result['instructors'] + result['syllabus']:
            source = item['sources'][0]
            self.assertEqual(source['source_file'], 'sample.pdf')
            self.assertEqual(source['page_number'], 3)
            self.assertIn(source['text'], text)

    def test_no_unlabelled_page_continuation(self):
        pages = [{'source_file': 'a.pdf', 'page_number': 1, 'text': 'Syllabus: Algebra'},
                 {'source_file': 'a.pdf', 'page_number': 2, 'text': 'Unlabelled material'}]
        self.assertEqual(len(extract_handout_content(pages)['syllabus']), 1)

    def test_mixed_sources(self):
        with self.assertRaises(ValueError):
            extract_handout_content([{'source_file': 'a.pdf', 'page_number': 1, 'text': ''},
                                     {'source_file': 'b.pdf', 'page_number': 2, 'text': ''}])

    def test_real_handout(self):
        path = Path(__file__).resolve().parents[1] / 'data/raw/handouts/001_AN_F314.pdf'
        if not path.exists():
            self.skipTest('Supplied handout unavailable')
        result = extract_handout_content(extract_pdf_text(path))
        self.assertIn('Biswadip Shome', [name for b in result['instructors'] for name in b['names']])
        self.assertTrue(any('aircraft flight' in s['text'] for s in result['syllabus']))
        self.assertTrue(any(s['heading'] == 'Course Plan' for s in result['syllabus']))
