"""Extract evaluation rows only when their column associations are explicit."""

import re
from typing import List, Optional, TypedDict

from preprocessing.pdf_extractor import PageRecord
from preprocessing.course_metadata import FieldSource


class EvaluationComponent(TypedDict):
    name: str
    marks: Optional[str]
    weightage: Optional[str]
    count: Optional[str]
    duration: Optional[str]
    schedule: Optional[str]
    remarks: Optional[str]
    sources: List[FieldSource]


_START = re.compile(r'(?im)^\s*(?:\d+[.)]\s*)?Evaluation(?:\s+(?:Scheme|Components?|Plan))?\s*:?\s*$')
_END = re.compile(r'(?im)^\s*(?:\d+[.)]\s*)?(?:Notes?|Chamber|Make[ -]?up|Attendance|Notices?|Mid-semester Grading|End-semester Grading|The evaluation)\b')
_COLUMNS = {
    'component': 'name', 'components': 'name', 'evaluation component': 'name',
    'marks': 'marks', 'weightage': 'weightage', 'weightage %': 'weightage',
    'weightage (%)': 'weightage', 'wt (%)': 'weightage', 'percentage': 'weightage',
    'count': 'count', 'number': 'count', 'duration': 'duration',
    'date': 'schedule', 'due date': 'schedule', 'date & time': 'schedule',
    'date of completion': 'schedule', 'schedule': 'schedule',
    'remarks': 'remarks', 'comments': 'remarks',
}
_NUM = re.compile(r'\d+(?:\.\d+)?(?:\s*%)?')


def _column(value):
    return _COLUMNS.get(' '.join(value.lower().split()))


def _record(values, page, evidence):
    name = values.get('name', '').strip()
    if not name or not re.search(r'[A-Za-z]', name) or name.lower() in ('total', 'overall', 'component', 'components'):
        return None
    result = {key: None for key in ('marks', 'weightage', 'count', 'duration', 'schedule', 'remarks')}
    for key, value in values.items():
        value = value.strip()
        if key == 'name' or not value or value.lower() in ('-', 'n/a', 'na'):
            continue
        if key in ('marks', 'weightage', 'count') and not _NUM.fullmatch(value):
            return None
        result[key] = value
    return dict(name=name, **result, sources=[{
        'source_file': page['source_file'], 'page_number': page['page_number'], 'text': evidence.strip(),
    }])


def _table_rows(section, page):
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        # Delimited tables retain both column order and optional empty cells.
        if '|' in line:
            headers = [_column(cell.strip()) for cell in line.strip('|').split('|')]
            if not headers or headers[0] != 'name' or None in headers or len(set(headers)) != len(headers):
                continue
            for row in lines[index + 1:]:
                cells = [c.strip() for c in row.strip('|').split('|')]
                if len(cells) != len(headers):
                    break
                record = _record(dict(zip(headers, cells)), page, line + '\n' + row)
                if record:
                    yield record
            return
        # Some PDFs emit one cell per line, in row order.
        if _column(line) == 'name':
            headers = ['name']
            cursor = index + 1
            while cursor < len(lines) and _column(lines[cursor]):
                headers.append(_column(lines[cursor]))
                cursor += 1
            if len(headers) > 1 and len(set(headers)) == len(headers):
                while cursor + len(headers) <= len(lines):
                    cells = lines[cursor:cursor + len(headers)]
                    record = _record(dict(zip(headers, cells)), page, '\n'.join(lines[index:cursor + len(headers)]))
                    if record is None:
                        break
                    yield record
                    cursor += len(headers)
                return
        header = re.fullmatch(
            r'(?:S\.?No\.?\s+)?(?:Evaluation\s+)?Components?\s+'
            r'(?P<metric>Weightage(?:\s*\(?%\)?)?|Wt\s*\(%\)|Marks)'
            r'(?:\s+(?P<tail>Due Date|Date of Completion|Date & Time|Schedule))?', line, re.I)
        if not header:
            continue
        metric = 'marks' if header['metric'].lower() == 'marks' else 'weightage'
        for row in lines[index + 1:]:
            # Reject ambiguous numeric layouts instead of shifting values between columns.
            match = re.fullmatch(r'(?:\d+\.\s*)?(?P<name>[A-Za-z][A-Za-z0-9 /&()._-]*?)\s+'
                                 r'(?P<value>\d+(?:\.\d+)?\s*%?)(?:\s+(?P<tail>.*))?', row)
            if not match:
                break
            if match['tail'] and not header['tail']:
                break
            if match['tail'] and re.match(r'^\d+(?:\.\d+)?(?:\s|$)', match['tail']):
                break  # Ambiguous numeric name/count/weight columns.
            values = {'name': match['name'], metric: match['value']}
            if header['tail']:
                values['schedule'] = match['tail'] or ''
            record = _record(values, page, line + '\n' + row)
            if record:
                yield record
        return


def extract_evaluation_components(pages: List[PageRecord]) -> List[EvaluationComponent]:
    """Return page-local evaluation rows; preserve values without calculating them.

    Only recognized table headers establish numeric meaning. Unsupported or
    ambiguous layouts return no rows. Schedule text is kept as a table cell,
    without interpreting exam dates, slots, or policies.
    """
    if len({p['source_file'] for p in pages}) > 1:
        raise ValueError('Expected pages from one handout')
    result = []
    for page in sorted(pages, key=lambda p: p['page_number']):
        text = page['text']
        starts = list(_START.finditer(text))
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            section = text[start.end():end]
            stop = _END.search(section)
            if stop:
                section = section[:stop.start()]
            result.extend(_table_rows(section, page))
    return result
