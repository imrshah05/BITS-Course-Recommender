"""Conservative extraction of labelled course metadata from handout pages."""

import re
from typing import Dict, List, Optional, TypedDict

from preprocessing.pdf_extractor import PageRecord


class FieldSource(TypedDict):
    source_file: str
    page_number: int
    text: str


class MetadataField(TypedDict):
    value: str
    sources: List[FieldSource]


class CourseMetadata(TypedDict):
    source_file: Optional[str]
    course_code: Optional[MetadataField]
    course_title: Optional[MetadataField]
    department_division: Optional[MetadataField]
    units: Optional[MetadataField]


_LABEL = re.compile(
    r"\b(?P<combined>Course\s*(?:Number|Code|No\.?)\s*(?:&|and)\s*Title)"
    r"|\b(?P<code>Course\s*(?:Number|Code|No\.?)(?!\w))"
    r"|\b(?P<title>Course\s*(?:Title|Name)|(?:Name|Title)\s+of\s+the\s+course)\b"
    r"|\b(?P<units>(?:Credit\s+)?Units?|Credits?|L\s*-\s*T\s*-\s*P\s*-\s*U)\s*(?=[:=])"
    r"|\b(?P<department>Department(?:\s*/\s*Division)?|Division)\s*(?=[:=])"
    r"|\b(?P<stop>(?:(?:Name\s+of\s+the|Team\s+of|Course|Additional|Other|Practical|Tutorial|Lecture|Lab|Co)[ -]+)?"
    r"Instructors?(?:\s*\(s\))?(?:[ -]+in[ -]+charge)?|(?:Lecture|Lab|Date|Email|Chamber)(?=\s*[:=])|Pre-?requisite(?:\s+of\s+the\s+Course)?)\b",
    re.I,
)
_SECTION = re.compile(
    r"(?:^|\n)\s*(?:(?:\d+|[IVX]+)[.)]\s*)?(?:Course\s+Description|Scope\s+(?:and|&)\s+Objective|"
    r"Course\s+Learning|Text\s*Books?|Reference\s*Books?|Evaluation\s+Scheme)\b(?!\.)", re.I,
)
_CODE = r"[A-Z]{2,8}(?:\s*/\s*[A-Z]{2,8})*\s*[FUGCE]\s*\d{3}[A-Z]?"
_CODES = re.compile(rf"{_CODE}(?:\s*(?:/|,|&|and)\s*(?:{_CODE}|[A-Z]\s*\d{{3}}[A-Z]?))*", re.I)
_DIVISION = re.compile(
    r"\bAcademic\s*[-–—]?\s*(?:Under\s*Graduate\s+Studies|"
    r"Graduate\s+Studies\s*(?:&|and)\s*Research)\s+Division\b"
    r"|^[ \t]*(?:AUGSD?|AGSRD?)(?:\s*[/&–—-]\s*(?:AUGSD?|AGSRD?))*[ \t]*(?:Division\b)?[ \t]*$"
    r"|\bInstruction\s+Division\b"
    r"|^[ \t]*Department\s+of[ \t]+[^\n:]+$", re.I | re.M,
)
_UNKNOWN = re.compile(r"(?:N/?A|None|Unknown|Not\s+(?:specified|available|provided)|TBD|[-–—]+)", re.I)


def _clean(text: str) -> str:
    marker = re.search(r"(?:^|\n)[ \t]*(?:\d+[.)]?|[a-z]\))[ \t]*$", text)
    # Remove trailing list markers only after a value, not a standalone credit count.
    if marker and text[:marker.start()].strip(" :\t\n=,"):
        text = text[:marker.start()]
    value = " ".join(text.split()).strip(" :\t=,")
    return value[1:-1].strip() if value.startswith("[") and value.endswith("]") else value


def _entries(text: str):
    """Pair adjacent labels with values, including labels-first PDF columns."""
    lines = text.splitlines(keepends=True)
    offset = 0
    line_index = 0
    while line_index < len(lines):
        start = line_index
        block = []
        while line_index < len(lines):
            label = _LABEL.fullmatch(lines[line_index].strip().rstrip(":"))
            if label is None:
                break
            block.append(label)
            line_index += 1
        if len(block) >= 2:
            rows = []
            cursor = line_index
            while cursor < len(lines) and len(rows) < len(block):
                if lines[cursor].strip():
                    if not lines[cursor].lstrip().startswith(":"):
                        break
                    rows.append(lines[cursor])
                cursor += 1
            if len(rows) == len(block):
                excerpt = "".join(lines[start:cursor])
                for label, row in zip(block, rows):
                    yield label.lastgroup, row, excerpt
                text = text[:offset] + " " * len(excerpt) + text[offset + len(excerpt):]
                offset += len(excerpt)
                line_index = cursor
                continue
        line_index = max(start + 1, line_index)
        offset += len("".join(lines[start:line_index]))
    labels = list(_LABEL.finditer(text))
    i = 0
    while i < len(labels):
        end = i
        while end + 1 < len(labels) and not _clean(text[labels[end].end():labels[end + 1].start()]):
            end += 1
        limit = labels[end + 1].start() if end + 1 < len(labels) else len(text)
        tail = text[labels[end].end():limit]
        if end > i:
            values = re.split(r"(?:^|\n)[ \t]*:[ \t]*", tail)
            values = [v for v in values if v.strip()]
            # A column block is usable only when every label has a value row.
            if len(values) == end - i + 1:
                for label, value in zip(labels[i:end + 1], values):
                    yield label.lastgroup, value, text[label.start():limit]
        else:
            yield labels[i].lastgroup, tail, text[labels[i].start():limit]
        i = end + 1


def _value(kind: str, text: str) -> Optional[str]:
    value = _clean(text)
    if not value or _UNKNOWN.fullmatch(value):
        return None
    if kind == "code":
        value = re.sub(r"\s*\([\d\s]+\)$", "", value)
        if not _CODES.fullmatch(value):
            return None
        value = re.sub(r"([A-Z])\s+(\d{3})", r"\1\2", value.upper())
    elif kind == "units":
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:\s*[-–+/]\s*\d+(?:\.\d+)?)*(?:\s*\([\d\s+./–-]+\))?", value):
            return None
    elif kind in ("title", "department"):
        if kind == "title" and _CODES.fullmatch(value):
            return None
        if len(value) > 180 or not re.search(r"[A-Za-z]", value):
            return None
        if re.search(r"[:=]|\b(?:instructor|course\s+description)\b", value, re.I):
            return None
    return value


def extract_course_metadata(pages: List[PageRecord]) -> CourseMetadata:
    """Read explicit metadata only; missing or conflicting values in the earliest matching page are None.

    Shared course codes stay together as printed. Division headers identify the
    document's issuing division, not an inferred teaching department. Sources
    retain page numbers and original text excerpts. A call accepts one handout.
    """
    filenames = {p["source_file"] for p in pages}
    if len(filenames) > 1:
        raise ValueError("Metadata extraction expects pages from a single handout")
    result: CourseMetadata = {
        "source_file": next(iter(filenames), None),
        "course_code": None, "course_title": None,
        "department_division": None, "units": None,
    }
    candidates: Dict[str, List[MetadataField]] = {key: [] for key in result if key != "source_file"}
    mapping = {"code": "course_code", "title": "course_title",
               "department": "department_division", "units": "units"}
    for page in sorted(pages, key=lambda p: p["page_number"]):
        text = page["text"]
        section = _SECTION.search(text)
        header = text[:section.start()] if section else text
        # Numbered section markers are boundaries, not part of a metadata value.
        header = re.sub(r"(?:\d+|[IVX]+)\.\s*$", "", header)
        entries = []
        for kind, raw_value, evidence in _entries(header):
            if kind == "combined":
                combined = re.fullmatch(rf"({_CODE})\s+(.+)", _clean(raw_value), re.I)
                if combined:
                    entries.extend([("code", combined[1], evidence),
                                    ("title", combined[2], evidence)])
            else:
                entries.append((kind, raw_value, evidence))
        entries.extend(("department", m.group(), m.group()) for m in _DIVISION.finditer(header))
        for kind, raw_value, evidence in entries:
            if kind not in mapping:
                continue
            value = _value(kind, raw_value)
            if value is not None:
                existing = candidates[mapping[kind]]
                if existing and existing[0]["sources"][0]["page_number"] != page["page_number"]:
                    continue
                existing.append({
                    "value": value,
                    "sources": [{"source_file": page["source_file"],
                                 "page_number": page["page_number"], "text": evidence.strip()}],
                })
    for field, found in candidates.items():
        unique: Dict[str, MetadataField] = {}
        for candidate in found:
            key = candidate["value"].casefold()
            if key in unique:
                unique[key]["sources"].extend(candidate["sources"])
            else:
                unique[key] = candidate
        if len(unique) == 1:
            result[field] = next(iter(unique.values()))
        elif unique and field == "department_division":
            result[field] = {
                "value": " / ".join(item["value"] for item in unique.values()),
                "sources": [s for item in unique.values() for s in item["sources"]],
            }
    return result
