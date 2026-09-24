"""Extract explicit instructor blocks and page-local course-content sections."""

import re
from typing import List, Optional, TypedDict

from preprocessing.pdf_extractor import PageRecord
from preprocessing.course_metadata import FieldSource


class InstructorBlock(TypedDict):
    label: str
    names: List[str]
    text: str
    sources: List[FieldSource]


class ContentSection(TypedDict):
    heading: str
    text: str
    sources: List[FieldSource]


class HandoutContent(TypedDict):
    source_file: Optional[str]
    instructors: List[InstructorBlock]
    syllabus: List[ContentSection]


_INSTRUCTOR = (
    r"(?:(?:Team\s+of|Course|Additional|Other|Lecture|Tutorial|Practical|Lab|Co)"
    r"[\s/-]+)*Instructors?(?:\s*\(s\))?(?:[\s-]+in[\s-]+charge)?"
)
_LABEL = re.compile(
    rf"\b(?P<instructor>{_INSTRUCTOR})\b|\b(?P<other>Course\s*(?:No\.?|Number|Code|Title|Name)|"
    r"Email|Chamber|Office\s+Phone|Date|Credit\s+Units)\b", re.I,
)
# Non-content headings are boundaries only; their contents are never returned.
_HEADING = re.compile(
    r"(?:^|\n)[ \t]*(?:(?:\d+|[IVX]+)[.)]\s*)?"
    r"(?P<title>Syllabus|Course\s+Contents?|Course\s+Description|Course\s+Plan|"
    r"Scope(?:\s+(?:and|&)\s+Objectives?)?|(?:Course\s+)?Learning\s+Outcomes?|"
    r"Text\s*Books?(?:\s*(?:and|&)\s*References?)?|Reference\s*(?:Books?|Materials)?|"
    r"Evaluation(?:\s+(?:Scheme|Components?|Plan))?|Attendance(?:\s+Policy)?|"
    r"Make[ -]?up(?:\s+Policy)?|Prerequisites?|Restrictions?|Notices?|Important\s+Instructions)"
    r"[ \t]*(?::|(?=\n|$))", re.I,
)
_CONTENT = re.compile(r"(?:Syllabus|Course\s+(?:Contents?|Description|Plan))$", re.I)
_UNKNOWN = re.compile(r"(?:N/?A|None|Nil|TBD|To be announced.*|Not available|[-–—]+)$", re.I)


def _compact(text):
    return " ".join(text.split()).strip(" :")


def _names(text):
    # Contact details and parenthetical annotations remain in the evidence only.
    value = re.sub(r"\([^)]*\)|<[^>]*>", " ", text)
    value = re.sub(r"\S+@\S+", " ", value)
    value = re.sub(r"\b(?:Lecture|Tutorial|Practical)\s*:", " ", value, flags=re.I)
    value = re.sub(r"\s+(?=(?:Prof\.|Dr\.)\s)", ";", value)
    names = []
    for part in re.split(r"[,;]|\band\b|&", value):
        name = _compact(part)
        bare = re.sub(r"^(?:Prof\.?|Professor|Dr\.?|Mr\.?|Ms\.?|Mrs\.?)\s+", "", name, flags=re.I)
        if (not _UNKNOWN.fullmatch(name) and 2 <= len(bare.split()) <= 7
                and re.fullmatch(r"[^\W\d_]+(?:[ .’'\-]+[^\W\d_]+)*\.?", bare)
                and not re.search(r"\b(?:supervisor|mentor|dean|announced|instructor|email|room)\b", bare, re.I)):
            if name not in names:
                names.append(name)
    return names


def _instructor_entries(header):
    # Handle the labels-first column layout without interpreting other fields.
    column = re.compile(
        rf"(?P<labels>(?:(?:{_INSTRUCTOR}|Course\s*(?:No\.?|Title))[ \t]*\n)+)"
        r"(?P<values>(?:[ \t]*:[^\n]*\n?)+)", re.I,
    )
    for match in column.finditer(header):
        labels = match['labels'].splitlines()
        values = match['values'].splitlines()
        if len(labels) == len(values):
            for label, value in zip(labels, values):
                if re.fullmatch(_INSTRUCTOR, label.strip(), re.I):
                    yield label, value, match.group()
    header = column.sub(lambda m: ' ' * len(m.group()), header)
    labels = list(_LABEL.finditer(header))
    for index, match in enumerate(labels):
        if match.lastgroup != 'instructor':
            continue
        end = labels[index + 1].start() if index + 1 < len(labels) else len(header)
        yield match.group(), header[match.end():end], header[match.start():end]


def extract_handout_content(pages: List[PageRecord]) -> HandoutContent:
    """Return explicit instructor blocks and content sections with page evidence.

    Names are conservative candidates from labelled blocks, never inferred from
    filenames. Sections stop at the next recognized heading or page boundary;
    unlabelled continuation pages are not guessed to belong to a syllabus.
    """
    filenames = {page['source_file'] for page in pages}
    if len(filenames) > 1:
        raise ValueError('Expected pages from one handout')
    result: HandoutContent = {'source_file': next(iter(filenames), None),
                              'instructors': [], 'syllabus': []}
    for page in sorted(pages, key=lambda p: p['page_number']):
        text = page['text']
        headings = list(_HEADING.finditer(text))
        header = text[:headings[0].start()] if headings else text
        for label, value, evidence in _instructor_entries(header):
            names = _names(value)
            if not names:
                continue
            result['instructors'].append({
                'label': _compact(label), 'names': names, 'text': value.strip(' :\n\t'),
                'sources': [{'source_file': page['source_file'], 'page_number': page['page_number'],
                             'text': evidence.strip()}],
            })
        for index, heading in enumerate(headings):
            if not _CONTENT.fullmatch(heading['title']):
                continue
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = text[heading.end():end].strip()
            if body and not _UNKNOWN.fullmatch(_compact(body)):
                result['syllabus'].append({
                    'heading': _compact(heading['title']), 'text': body,
                    'sources': [{'source_file': page['source_file'], 'page_number': page['page_number'],
                                 'text': text[heading.start():end].strip()}],
                })
    return result
