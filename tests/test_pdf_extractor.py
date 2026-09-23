import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfReader

from preprocessing.pdf_extractor import PDFExtractionError, extract_pdf_text


class PDFExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "sample.pdf"

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            extract_pdf_text(self.path)

    def test_directory(self):
        with self.assertRaises(IsADirectoryError):
            extract_pdf_text(self.path.parent)

    def test_wrong_extension(self):
        path = self.path.with_suffix(".txt")
        path.write_text("Not a PDF")
        with self.assertRaises(ValueError):
            extract_pdf_text(path)

    def test_fake_pdf(self):
        self.path.write_text("Not a PDF")
        with self.assertRaisesRegex(ValueError, "header"):
            extract_pdf_text(self.path)

    def test_corrupt_pdf(self):
        self.path.write_bytes(b"%PDF-1.7\n")
        with self.assertRaises(PDFExtractionError) as caught:
            extract_pdf_text(self.path)
        self.assertIsNotNone(caught.exception.__cause__)

    @patch("preprocessing.pdf_extractor.PdfReader")
    def test_page_records_and_file_closure(self, reader_class):
        self.path = self.path.with_suffix(".PDF")
        self.path.write_bytes(b"%PDF-1.7\n")
        reader_class.return_value = Mock(
            is_encrypted=False,
            pages=[Mock(extract_text=Mock(return_value="Example text")),
                   Mock(extract_text=Mock(return_value=""))],
        )
        self.assertEqual(extract_pdf_text(str(self.path)), [
            {"source_file": "sample.PDF", "page_number": 1, "text": "Example text"},
            {"source_file": "sample.PDF", "page_number": 2, "text": ""},
        ])
        self.assertTrue(reader_class.call_args.args[0].closed)

    @patch("preprocessing.pdf_extractor.PdfReader")
    def test_extraction_failure(self, reader_class):
        self.path.write_bytes(b"%PDF-1.7\n")
        error = RuntimeError("Unreadable page")
        reader_class.return_value = Mock(
            is_encrypted=False,
            pages=[Mock(extract_text=Mock(side_effect=error))],
        )
        with self.assertRaisesRegex(PDFExtractionError, "page 1") as caught:
            extract_pdf_text(self.path)
        self.assertIs(caught.exception.__cause__, error)
        self.assertTrue(reader_class.call_args.args[0].closed)

    @patch("preprocessing.pdf_extractor.PdfReader")
    def test_encrypted_pdf(self, reader_class):
        self.path.write_bytes(b"%PDF-1.7\n")
        reader_class.return_value = Mock(is_encrypted=True)
        with self.assertRaisesRegex(PDFExtractionError, "Encrypted"):
            extract_pdf_text(self.path)

    def test_supplied_pdf(self):
        raw_dir = Path(__file__).resolve().parents[1] / "data" / "raw"
        pdf_path = next((path for path in sorted(raw_dir.rglob("*"))
                         if path.is_file() and path.suffix.lower() == ".pdf"), None)
        if pdf_path is None:
            self.skipTest("No supplied PDF in data/raw/ yet")

        records = extract_pdf_text(pdf_path)
        self.assertTrue(records)
        with pdf_path.open("rb") as stream:
            self.assertEqual(len(records), len(PdfReader(stream).pages))
        self.assertEqual([r["page_number"] for r in records],
                         list(range(1, len(records) + 1)))
        for record in records:
            self.assertEqual(record["source_file"], pdf_path.name)
            self.assertIsInstance(record["text"], str)
