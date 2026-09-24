"""Extract a directory of handouts without stopping on an individual failure."""

from pathlib import Path
from typing import List, TypedDict, Union

from preprocessing.pdf_extractor import PageRecord, extract_pdf_text


class HandoutDocument(TypedDict):
    source_file: str
    pages: List[PageRecord]


class HandoutFailure(TypedDict):
    source_file: str
    error: str


class BatchResult(TypedDict):
    documents: List[HandoutDocument]
    failures: List[HandoutFailure]


def discover_handouts(directory: Union[str, Path]) -> List[Path]:
    """Find visible PDFs directly in a handout directory, sorted by filename."""
    folder = Path(directory)
    if not folder.exists():
        raise FileNotFoundError(f"Handout directory does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Expected a handout directory: {folder}")
    return sorted(
        (path for path in folder.iterdir()
         if not path.name.startswith(".")
         and path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name,
    )


def process_handouts(directory: Union[str, Path]) -> BatchResult:
    """Return extracted documents and per-file errors; write no output files.

    Invalid directory paths raise immediately. Each PDF is handled independently,
    and successful documents retain the extractor's page records unchanged.
    """
    result: BatchResult = {"documents": [], "failures": []}
    for path in discover_handouts(directory):
        try:
            pages = extract_pdf_text(path)
        except Exception as exc:
            result["failures"].append({
                "source_file": path.name,
                "error": f"{type(exc).__name__}: {exc}",
            })
        else:
            result["documents"].append({"source_file": path.name, "pages": pages})
    return result
