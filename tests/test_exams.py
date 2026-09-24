import unittest
from pathlib import Path
from preprocessing.exams import extract_exam_details
from preprocessing.pdf_extractor import extract_pdf_text


class ExamTests(unittest.TestCase):
    def parse(self, text):
        return extract_exam_details([{'source_file': 'sample.pdf', 'page_number': 4, 'text': text}])

    def test_midsemester_variations(self):
        for name in ['Midsem', 'Mid-Sem', 'Mid Semester', 'Midsemester Examination', 'Mid-term exam']:
            with self.subTest(name=name):
                self.assertEqual(self.parse(name + ' 30%')['midsemester']['weightage']['value'], '30%')

    def test_comprehensive_variations(self):
        for name in ['Compre', 'Comprehensive', 'Comprehensive Examination']:
            self.assertIsNotNone(self.parse(name + ' 40%')['comprehensive'])

    def test_both_and_multiline(self):
        r = self.parse('Mid-Semester\nexam\n90 minutes 30%\nComprehensive\nExam 180 minutes 40%')
        self.assertEqual(r['midsemester']['duration']['value'], '90 minutes')
        self.assertEqual(r['comprehensive']['weightage']['value'], '40%')

    def test_dates_times_and_format(self):
        r = self.parse('Midsem exam will be held on 9th October 2026 at 4:00 PM-5:30 PM Closed book')['midsemester']
        self.assertEqual(r['date']['value'], '9th October 2026')
        self.assertEqual(r['time']['value'], '4:00 PM-5:30 PM')
        self.assertEqual(r['format']['value'], 'Closed book')

    def test_marks(self):
        self.assertEqual(self.parse('Compre: Marks: 100')['comprehensive']['marks']['value'], '100')

    def test_reuse_evaluation_table(self):
        r = self.parse('Evaluation:\nComponent | Marks | Weightage\nMidsem exam | 30 | 20')['midsemester']
        self.assertEqual(r['marks']['value'], '30')
        self.assertEqual(r['weightage']['value'], '20')

    def test_separate_cells(self):
        r = self.parse('Evaluation:\nComponent\nMarks\nMidsem exam\n30')['midsemester']
        self.assertEqual(r['marks']['value'], '30')

    def test_missing(self):
        self.assertEqual(self.parse('No exam information.'), {'midsemester': None, 'comprehensive': None})

    def test_missing_fields(self):
        r = self.parse('Midsem 30%')['midsemester']
        for field in ['date', 'time', 'duration', 'marks', 'format']:
            self.assertIsNone(r[field])

    def test_policy_and_negative_mentions(self):
        for text in ['Makeup for Midsem exam lasts 90 minutes.', 'Midsem exam is not conducted.',
                     'Midsem report 30%', 'Midsemester Examination\n8 Transactions and databases']:
            self.assertIsNone(self.parse(text)['midsemester'])

    def test_does_not_borrow_next_row(self):
        r = self.parse('Midsem 30%\nQuiz 10% 12/10/26 Closed Book')['midsemester']
        self.assertIsNone(r['date'])
        self.assertIsNone(r['format'])

    def test_slots_not_times(self):
        r = self.parse('Midsem 30% 09/10/26 (AN1)')['midsemester']
        self.assertEqual(r['date']['value'], '09/10/26')
        self.assertIsNone(r['time'])

    def test_traceability(self):
        text = 'Midsem 30% 09/10/26'
        r = self.parse(text)['midsemester']
        for field in ['name', 'weightage', 'date']:
            s = r[field]['sources'][0]
            self.assertEqual(s['source_file'], 'sample.pdf')
            self.assertEqual(s['page_number'], 4)
            self.assertIn(s['text'], text)

    def test_conflicting_dates(self):
        self.assertIsNone(self.parse('Midsem 30% 09/10/26\nMidsem 30% 10/10/26')['midsemester']['date'])

    def test_real_handout(self):
        path = Path(__file__).resolve().parents[1] / 'data/raw/handouts/001_AN_F314.pdf'
        if not path.exists(): self.skipTest('Supplied handout unavailable')
        r = extract_exam_details(extract_pdf_text(path))
        self.assertEqual(r['midsemester']['date']['value'], '09/10/26')
        self.assertEqual(r['comprehensive']['date']['value'], '14/12/26')
        self.assertEqual(r['midsemester']['weightage']['value'], '30%')
        self.assertEqual(r['comprehensive']['duration']['value'], '180 minutes')
