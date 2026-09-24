import unittest
from pathlib import Path

from preprocessing.evaluation import extract_evaluation_components
from preprocessing.pdf_extractor import extract_pdf_text


class EvaluationTests(unittest.TestCase):
    def parse(self, text):
        return extract_evaluation_components([{'source_file': 'sample.pdf', 'page_number': 3, 'text': text}])

    def test_simple(self):
        rows = self.parse('Evaluation Scheme:\nComponent Weightage\nQuiz 20%')
        self.assertEqual(rows[0]['name'], 'Quiz')
        self.assertEqual(rows[0]['weightage'], '20%')
        self.assertIsNone(rows[0]['marks'])

    def test_multiple_and_unusual_names(self):
        rows = self.parse('Evaluation:\nComponent Marks\nDesign critique 25\nPortfolio review 75')
        self.assertEqual([(r['name'], r['marks']) for r in rows], [('Design critique', '25'), ('Portfolio review', '75')])

    def test_explicit_columns(self):
        rows = self.parse('Evaluation:\nComponent | Marks | Weightage (%) | Count | Duration | Schedule | Remarks\n'
                          'Quiz 1 | 10 | 5 | 1 | 20 minutes | Week 3 | Closed book')
        for key, expected in {'name': 'Quiz 1', 'marks': '10', 'weightage': '5', 'count': '1',
                              'duration': '20 minutes', 'schedule': 'Week 3', 'remarks': 'Closed book'}.items():
            self.assertEqual(rows[0][key], expected)

    def test_missing_numeric_cells(self):
        rows = self.parse('Evaluation:\nComponent | Marks | Weightage\nPresentation | | -')
        self.assertIsNone(rows[0]['marks'])
        self.assertIsNone(rows[0]['weightage'])

    def test_separate_lines(self):
        rows = self.parse('Evaluation:\nComponent\nMarks\nWeightage\nQuiz\n10\n20%\nProject\n40\n80%')
        self.assertEqual([(r['name'], r['marks'], r['weightage']) for r in rows],
                         [('Quiz', '10', '20%'), ('Project', '40', '80%')])

    def test_column_major_ambiguity_rejected(self):
        self.assertEqual(self.parse('Evaluation:\nComponent\nMarks\nQuiz\nProject\n10\n40'), [])

    def test_missing_section(self):
        self.assertEqual(self.parse('Component Weightage\nQuiz 20%'), [])

    def test_no_fabrication_from_prose(self):
        self.assertEqual(self.parse('Evaluation:\nQuizzes may be conducted.'), [])

    def test_traceability(self):
        text = 'Evaluation:\nComponent Marks\nQuiz 20'
        source = self.parse(text)[0]['sources'][0]
        self.assertEqual(source['source_file'], 'sample.pdf')
        self.assertEqual(source['page_number'], 3)
        self.assertIn(source['text'], text)

    def test_schedule_and_numbered_rows(self):
        rows = self.parse('3. Evaluation Scheme:\nS.No. Components Weightage % Due Date\n1. Project Outline 10 21.08.2026')
        self.assertEqual(rows[0]['name'], 'Project Outline')
        self.assertEqual(rows[0]['weightage'], '10')
        self.assertEqual(rows[0]['schedule'], '21.08.2026')

    def test_policy_boundary(self):
        rows = self.parse('Evaluation:\nComponent Marks\nQuiz 20\nNotes:\nExtra 30')
        self.assertEqual(len(rows), 1)

    def test_exam_is_ordinary_component(self):
        rows = self.parse('Evaluation:\nComponent Marks\nComprehensive examination 100')
        self.assertEqual(rows[0]['name'], 'Comprehensive examination')
        self.assertIsNone(rows[0]['schedule'])

    def test_real_handout(self):
        path = Path(__file__).resolve().parents[1] / 'data/raw/handouts/121_CHE_F266.pdf'
        if not path.exists():
            self.skipTest('Supplied handout unavailable')
        rows = extract_evaluation_components(extract_pdf_text(path))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]['name'], 'Project Outline & Plan of Work')
        self.assertEqual(rows[0]['weightage'], '10')
        self.assertEqual(rows[-1]['name'], 'Final Seminar and Viva')
        self.assertEqual(rows[-1]['weightage'], '20')
