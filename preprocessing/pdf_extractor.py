"""Page-by-page text extraction for academic PDFs."""

from pathlib import Path
from typing import List, TypedDict, Union

from pypdf import PdfReader


class PageRecord(TypedDict):
    source_file: str
    page_number: int
    text: str


class PDFExtractionError(RuntimeError):
    """A PDF could not be read or its text could not be extracted."""


def extract_pdf_text(path: Union[str, Path]) -> List[PageRecord]:
    """Return one record per page, numbered from 1, including empty pages.

    Invalid paths raise OSError; non-PDF inputs raise ValueError. Reading and
    extraction failures raise PDFExtractionError with the original cause.
    Scanned pages require OCR elsewhere and may return empty text here.
    """
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf_path}")
    if not pdf_path.is_file():
        raise IsADirectoryError(f"Expected a PDF file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {pdf_path}")

    with pdf_path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Missing PDF header: {pdf_path}")
        stream.seek(0)
        page_number = None
        try:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise PDFExtractionError(f"Encrypted PDFs are not supported: {pdf_path}")
            records: List[PageRecord] = []
            for page_number, page in enumerate(reader.pages, start=1):
                records.append({
                    "source_file": pdf_path.name,
                    "page_number": page_number,
                    "text": page.extract_text() or "",
                })
            return records
        except PDFExtractionError:
            raise
        except Exception as exc:
            location = f" (page {page_number})" if page_number is not None else ""
            raise PDFExtractionError(
                f"Could not extract text from {pdf_path}{location}: {exc}"
            ) from exc

def extract_pdf_layout_pages(path: Union[str, Path], page_numbers) -> List[PageRecord]:
    """Extract selected pages with spatial layout preserved.

    This is intentionally separate from ``extract_pdf_text`` so existing parsers
    retain their established default extraction behavior.
    """
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf_path}")
    if not pdf_path.is_file():
        raise IsADirectoryError(f"Expected a PDF file: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file: {pdf_path}")
    requested = sorted(set(page_numbers))
    if any(type(number) is not int or number < 1 for number in requested):
        raise ValueError("Page numbers must be positive integers")
    with pdf_path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Missing PDF header: {pdf_path}")
        stream.seek(0)
        try:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise PDFExtractionError(f"Encrypted PDFs are not supported: {pdf_path}")
            records = []
            for page_number in requested:
                if page_number > len(reader.pages):
                    raise ValueError(f"PDF has no page {page_number}: {pdf_path}")
                records.append({
                    "source_file": pdf_path.name,
                    "page_number": page_number,
                    "text": reader.pages[page_number - 1].extract_text(
                        extraction_mode="layout") or "",
                })
            return records
        except (PDFExtractionError, ValueError):
            raise
        except Exception as exc:
            raise PDFExtractionError(
                f"Could not extract layout text from {pdf_path}: {exc}") from exc
