"""Extract explicitly headed attendance and makeup policy text."""

import re
from typing import List, Optional, TypedDict

from preprocessing.pdf_extractor import PageRecord
from preprocessing.course_metadata import FieldSource


class PolicySection(TypedDict):
    heading: str
    text: str
    sources: List[FieldSource]


class HandoutPolicies(TypedDict):
    source_file: Optional[str]
    attendance: List[PolicySection]
    makeup: List[PolicySection]


_POLICY = re.compile(
    r'(?im)^[ \t]*(?:(?:\d+|[IVX]+)[.)]\s*)?'
    r'(?P<heading>(?P<kind>Attendance|Make\s*-?\s*up)\s+Polic(?:y|ies))'
    r'[ \t]*(?::|(?=\n|$))'
)
_BOUNDARY = re.compile(
    r'(?im)^[ \t]*(?:(?:\d+|[IVX]+)[.)][ \t]*)?'
    r'(?:Notices?|Notes?|(?:Chamber|Office)\s+Consultation(?:\s+Hours?)?|'
    r'General\s+Instructions|Evaluation(?:\s+Scheme)?|Course\s+Plan|'
    r'Instructor[ -]*in[ -]*charge|Attendance\s+Policy|Make\s*-?\s*up\s+Policy)\b(?=[ \t]*(?::|\n|$|\())'
    r'|^[ \t]*(?:\d+|[IVX]+)[.)][ \t]+[A-Z][A-Za-z /&()-]{2,70}:'
)
_EMPTY = re.compile(r'(?:N/?A|Not\s+(?:available|specified)|TBD|[-–—]+)$', re.I)


def extract_handout_policies(pages: List[PageRecord]) -> HandoutPolicies:
    """Preserve policy wording in page-local sections, without deriving rules.

    Only explicit policy headings establish context. An absent policy returns an
    empty list. Page continuations without a repeated heading are not inferred.
    Percentages, consequences, permissions and conditions remain in source text.
    """
    files = {page['source_file'] for page in pages}
    if len(files) > 1:
        raise ValueError('Expected pages from one handout')
    result: HandoutPolicies = {'source_file': next(iter(files), None), 'attendance': [], 'makeup': []}
    for page in sorted(pages, key=lambda p: p['page_number']):
        text = page['text']
        headings = list(_POLICY.finditer(text))
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            boundary = _BOUNDARY.search(text, heading.end(), end)
            if boundary:
                end = boundary.start()
            body = text[heading.end():end].strip()
            if not body or _EMPTY.fullmatch(' '.join(body.split())):
                continue
            kind = 'attendance' if heading['kind'].lower() == 'attendance' else 'makeup'
            result[kind].append({
                'heading': ' '.join(heading['heading'].split()),
                'text': body,
                'sources': [{'source_file': page['source_file'], 'page_number': page['page_number'],
                             'text': text[heading.start():end].strip()}],
            })
    return result
