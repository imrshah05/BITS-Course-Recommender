import unittest
from pathlib import Path

from preprocessing.policies import extract_handout_policies
from preprocessing.pdf_extractor import extract_pdf_text


class PolicyTests(unittest.TestCase):
    def parse(self, text, number=2):
        return extract_handout_policies([{'source_file': 'sample.pdf', 'page_number': number, 'text': text}])

    def test_attendance(self):
        self.assertEqual(self.parse('Attendance Policy: Attend all classes.')['attendance'][0]['text'], 'Attend all classes.')

    def test_percentage_and_consequence_preserved(self):
        body = 'Minimum attendance is 75%. Students below this threshold receive NC.'
        self.assertEqual(self.parse('Attendance Policy: ' + body)['attendance'][0]['text'], body)

    def test_multiline(self):
        body = 'Attend lectures regularly.\nParticipation is expected.'
        self.assertEqual(self.parse('8. Attendance Policy:\n' + body)['attendance'][0]['text'], body)

    def test_makeup(self):
        body = 'Makeup is allowed only in genuine cases.'
        self.assertEqual(self.parse('Makeup Policy: ' + body)['makeup'][0]['text'], body)

    def test_permission_and_documents(self):
        body = 'Prior permission from the instructor and a medical certificate are required.'
        self.assertEqual(self.parse('Make-up Policy:\n' + body)['makeup'][0]['text'], body)

    def test_exam_scope_preserved(self):
        body = 'Makeup applies to the midsemester exam only. No makeup for quizzes.'
        self.assertEqual(self.parse('Make-up Policy: ' + body)['makeup'][0]['text'], body)

    def test_spelling_variations(self):
        for heading in ['Makeup Policy', 'Make-up Policy', 'Make -up Policy', 'Make up Policy', 'MAKE-UP POLICY']:
            with self.subTest(heading=heading):
                self.assertEqual(len(self.parse(heading + ': Medical emergencies only.')['makeup']), 1)

    def test_both_and_boundaries(self):
        r = self.parse('8. Attendance Policy: Attend regularly.\n9. Make-up Policy: Genuine cases only.\n10. Notices: See LMS.')
        self.assertEqual(r['attendance'][0]['text'], 'Attend regularly.')
        self.assertEqual(r['makeup'][0]['text'], 'Genuine cases only.')

    def test_missing_attendance(self):
        self.assertEqual(self.parse('Makeup Policy: No makeup is allowed.')['attendance'], [])

    def test_missing_makeup(self):
        self.assertEqual(self.parse('Attendance Policy: Attend regularly.')['makeup'], [])

    def test_unrelated_keywords(self):
        r = self.parse('Mark attendance on the app.\nThe makeup artist is absent.\nMidsemester examination: 30%.')
        self.assertEqual(r['attendance'], [])
        self.assertEqual(r['makeup'], [])

    def test_no_inferred_threshold(self):
        r = self.parse('Attendance Policy: As per AUGS guidelines.')
        self.assertEqual(r['attendance'][0]['text'], 'As per AUGS guidelines.')
        self.assertNotIn('75', r['attendance'][0]['text'])

    def test_traceability(self):
        text = 'Attendance Policy:\nAttend all classes.'
        r = self.parse(text)
        self.assertEqual(r['source_file'], 'sample.pdf')
        self.assertEqual(r['attendance'][0]['sources'][0],
                         {'source_file': 'sample.pdf', 'page_number': 2, 'text': text})

    def test_signature_boundary(self):
        r = self.parse('Make-up Policy: Medical emergencies only.\nInstructor-in-charge\nAda Lovelace')
        self.assertEqual(r['makeup'][0]['text'], 'Medical emergencies only.')

    def test_empty_policy(self):
        self.assertEqual(self.parse('Attendance Policy: N/A')['attendance'], [])

    def test_mixed_sources(self):
        with self.assertRaises(ValueError):
            extract_handout_policies([{'source_file': 'a'}, {'source_file': 'b'}])

    def test_real_handout(self):
        path = Path(__file__).resolve().parents[1] / 'data/raw/handouts/075_CE_F211.pdf'
        if not path.exists():
            self.skipTest('Supplied handout unavailable')
        r = extract_handout_policies(extract_pdf_text(path))
        self.assertIn('no minimum percentage', r['attendance'][0]['text'])
        self.assertIn('accompanied by proof', r['makeup'][0]['text'])
        self.assertNotIn('Chamber', r['attendance'][0]['text'])
        self.assertEqual(r['makeup'][0]['sources'][0]['page_number'], 5)
