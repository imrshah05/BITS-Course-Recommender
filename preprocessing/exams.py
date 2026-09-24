"""Extract explicitly associated exam fields without interpreting policies."""

import re
from typing import Dict, List, Optional

from preprocessing.course_metadata import MetadataField
from preprocessing.pdf_extractor import PageRecord
from preprocessing.evaluation import extract_evaluation_components

_NAME = r'(?:Mid[\s-]*(?:semester|sem|term)|Comprehensive|Compre)(?:\s+(?:Examination|Exam|Test))?'
_EXAM = re.compile(rf'(?im)^[ \t]*(?:\d+[.)][ \t]+)?(?P<name>{_NAME})\b')
_FULL_NAME = re.compile(rf'{_NAME}$', re.I)
_DATE = r'(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})'
_TIME = r'\d{1,2}[:.]\d{2}\s*(?:AM|PM)(?:\s*[-–]\s*\d{1,2}[:.]\d{2}\s*(?:AM|PM))?'
_TOKEN = re.compile(
    rf'(?P<date>(?:Date\s*:\s*|will\s+be\s+held\s+on\s+|on\s+)?{_DATE})'
    rf'|(?P<time>(?:Time\s*:\s*|at\s+)?{_TIME})'
    r'|(?P<duration>(?:Duration\s*:\s*)?\d+(?:\s*[-–]\s*\d+)?\s*(?:minutes?|mins?\.?|hours?|hrs?\.?))'
    r'|(?P<marks>(?:Marks\s*:\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s+marks))'
    r'|(?P<weightage>(?:Weightage\s*:\s*)?\d+(?:\.\d+)?\s*%)'
    r'|(?P<format>(?:Open|Closed)[ -]+(?:Book|textbook\d*|class\s+notes))'
    r'|(?P<slot>\((?:FN|AN)\d?\))', re.I,
)
_FIELDS = ('name', 'date', 'time', 'duration', 'marks', 'weightage', 'format')


def _kind(name):
    return 'midsemester' if name.lower().startswith('mid') else 'comprehensive'


def extract_exam_details(pages: List[PageRecord]) -> Dict[str, Optional[Dict[str, Optional[MetadataField]]]]:
    """Return separate exam records with evidence for every populated field.

    Only adjacent, recognized field tokens are consumed. Bare numbers and slots
    are never interpreted as marks, percentages, or clock times. Conflicting
    field values become None. Names without any supported detail are omitted.
    """
    if len({p['source_file'] for p in pages}) > 1:
        raise ValueError('Expected pages from one handout')
    candidates = {kind: {field: [] for field in _FIELDS} for kind in ('midsemester', 'comprehensive')}

    def add(kind, field, value, sources):
        candidates[kind][field].append({'value': ' '.join(value.split()), 'sources': sources})

    for row in extract_evaluation_components(pages):
        if not _FULL_NAME.fullmatch(row['name']):
            continue
        kind = _kind(row['name'])
        add(kind, 'name', row['name'], row['sources'])
        for field in ('marks', 'weightage', 'duration'):
            if row[field]:
                add(kind, field, row[field], row['sources'])

    for page in pages:
        for match in _EXAM.finditer(page['text']):
            kind = _kind(match['name'])
            remaining = page['text'][match.end():match.end() + 350]
            fields = []
            consumed = 0
            while remaining:
                separator = re.match(r'[\s:;|,\u2022\uf0b7-]*', remaining).group()
                consumed += len(separator)
                remaining = remaining[len(separator):]
                token = _TOKEN.match(remaining)
                if not token:
                    break
                field = token.lastgroup
                if field != 'slot':
                    value = re.sub(r'^(?:Date|Time|Duration|Marks|Weightage)\s*:\s*|^(?:will\s+be\s+held\s+on|on|at)\s+', '', token.group(), flags=re.I)
                    if field == 'marks':
                        value = re.sub(r'\s+marks$', '', value, flags=re.I)
                    fields.append((field, value))
                consumed += token.end()
                remaining = remaining[token.end():]
            if fields:
                evidence = page['text'][match.start():match.end() + consumed].strip()
                sources = [{'source_file': page['source_file'], 'page_number': page['page_number'], 'text': evidence}]
                add(kind, 'name', match['name'], sources)
                for field, value in fields:
                    add(kind, field, value, sources)
    result = {}
    for kind, fields in candidates.items():
        if not fields['name']:
            result[kind] = None
            continue
        record = {}
        for field, values in fields.items():
            unique = {}
            for item in values:
                key = item['value'].casefold()
                if field == 'name':
                    key = kind
                if key in unique:
                    unique[key]['sources'].extend(item['sources'])
                else:
                    unique[key] = item
            record[field] = next(iter(unique.values())) if len(unique) == 1 else None
        result[kind] = record
    return result
