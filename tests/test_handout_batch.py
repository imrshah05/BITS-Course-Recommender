import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from preprocessing.handout_batch import discover_handouts, process_handouts
from preprocessing.pdf_extractor import PDFExtractionError


class HandoutBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.folder = Path(self.temp_dir.name)

    def add_file(self, name):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_discovery_order_and_ignored_files(self):
        for name in ["z.pdf", "a.PDF", "m.pdf", "notes.txt", ".DS_Store",
                     ".gitkeep", "._a.pdf", ".hidden.pdf",
                     "__MACOSX/metadata.pdf", ".hidden/other.pdf"]:
            self.add_file(name)
        (self.folder / "directory.pdf").mkdir()
        self.assertEqual([p.name for p in discover_handouts(self.folder)],
                         ["a.PDF", "m.pdf", "z.pdf"])

    @patch("preprocessing.handout_batch.extract_pdf_text")
    def test_multiple_documents_preserve_pages(self, extract):
        b = self.add_file("b.pdf")
        a = self.add_file("a.pdf")
        pages = {
            "a.pdf": [{"source_file": "a.pdf", "page_number": 1, "text": "First"},
                      {"source_file": "a.pdf", "page_number": 2, "text": ""}],
            "b.pdf": [{"source_file": "b.pdf", "page_number": 1, "text": "Second"}],
        }
        extract.side_effect = lambda path: pages[path.name]
        result = process_handouts(str(self.folder))
        self.assertEqual(extract.call_args_list, [call(a), call(b)])
        self.assertEqual(result, {
            "documents": [{"source_file": name, "pages": pages[name]}
                          for name in ["a.pdf", "b.pdf"]],
            "failures": [],
        })

    @patch("preprocessing.handout_batch.extract_pdf_text")
    def test_failure_does_not_stop_remaining_handouts(self, extract):
        paths = [self.add_file(name) for name in ["a.pdf", "b.pdf", "c.pdf"]]
        extract.side_effect = [[], PDFExtractionError("Unreadable page 2"), []]
        result = process_handouts(self.folder)
        self.assertEqual(extract.call_args_list, [call(path) for path in paths])
        self.assertEqual([d["source_file"] for d in result["documents"]],
                         ["a.pdf", "c.pdf"])
        self.assertEqual(result["failures"], [{
            "source_file": "b.pdf",
            "error": "PDFExtractionError: Unreadable page 2",
        }])

    def test_invalid_pdf_is_recorded(self):
        self.add_file("invalid.pdf")
        result = process_handouts(self.folder)
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["failures"][0]["source_file"], "invalid.pdf")
        self.assertIn("Missing PDF header", result["failures"][0]["error"])

    def test_empty_directory(self):
        self.assertEqual(process_handouts(self.folder),
                         {"documents": [], "failures": []})

    def test_missing_directory(self):
        with self.assertRaises(FileNotFoundError):
            process_handouts(self.folder / "missing")

    def test_file_instead_of_directory(self):
        with self.assertRaises(NotADirectoryError):
            process_handouts(self.add_file("file.pdf"))
