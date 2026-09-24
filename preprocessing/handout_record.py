"""Combine existing extractor outputs, normalize formatting, and flag quality issues."""

from copy import deepcopy
import re

from preprocessing.course_metadata import extract_course_metadata, _CODES
from preprocessing.handout_content import extract_handout_content
from preprocessing.evaluation import extract_evaluation_components
from preprocessing.exams import extract_exam_details
from preprocessing.policies import extract_handout_policies

_METADATA = ('course_code', 'course_title', 'department_division', 'units')
_LISTS = ('instructors', 'syllabus', 'evaluation', 'attendance', 'makeup')


def normalize_handout(extracted):
    """Normalize combined outputs without mutating them; retain originals verbatim.

    Errors mark invalid structure or required identifiers. Warnings mark quality
    concerns. Conflicts require verification and preserve all observations.
    """
    original = deepcopy(extracted)
    issues = []

    def issue(code, path, severity, message):
        item = dict(code=code, path=path, severity=severity, message=message)
        if item not in issues:
            issues.append(item)

    def clean(value, key=''):
        if key in ('sources', 'text', 'source_file'):
            return deepcopy(value)
        if isinstance(value, str):
            return ' '.join(value.split()) or None
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items()}
        return deepcopy(value)

    record = clean(extracted)
    record.setdefault('source', {'source_file': None, 'page_numbers': []})
    record.setdefault('metadata', {})
    record.setdefault('exams', {'midsemester': None, 'comprehensive': None})
    record.setdefault('observations', [])
    if not isinstance(record['metadata'], dict):
        issue('invalid_structure', 'metadata', 'error', 'Expected metadata object.')
    else:
        for field in _METADATA:
            record['metadata'].setdefault(field, None)
            if isinstance(record['metadata'][field], dict) and record['metadata'][field].get('value') is None:
                record['metadata'][field] = None
    code = record['metadata'].get('course_code') if isinstance(record['metadata'], dict) else None
    if isinstance(code, dict) and isinstance(code.get('value'), str):
        match = re.fullmatch(r'([A-Za-z]{2,8})\s+([FUGCEfugce])\s*(\d{3}[A-Za-z]?)', code['value'])
        if match:
            code['value'] = f'{match[1]} {match[2]}{match[3]}'.upper()
    for key in _LISTS:
        if record.get(key) is None:
            record[key] = []
        if not isinstance(record[key], list):
            issue('invalid_structure', key, 'error', 'Expected a list.')
            continue
        unique = []
        for item in record[key]:
            if item in unique:
                issue('duplicate_item', key, 'warning', 'Removed an identical extracted item.')
            else:
                unique.append(item)
        record[key] = unique

    source = record['source']
    source = source if isinstance(source, dict) else {}
    filename = source.get('source_file')
    page_numbers = source.get('page_numbers', [])
    if (not isinstance(filename, str) or not filename.strip() or not isinstance(page_numbers, list)
            or not page_numbers or any(type(n) is not int or n < 1 for n in page_numbers)):
        issue('invalid_source', 'source', 'error', 'Expected a filename and positive page numbers.')
    else:
        if len(page_numbers) != len(set(page_numbers)):
            issue('duplicate_page', 'source.page_numbers', 'warning', 'Duplicate page numbers supplied.')
        record['source']['page_numbers'] = sorted(set(page_numbers))

    def trace(item, path):
        sources = item.get('sources')
        if not isinstance(sources, list) or not sources:
            issue('incomplete_traceability', path, 'error', 'Missing source evidence.')
            return
        for s in sources:
            if (not isinstance(s, dict) or s.get('source_file') != filename
                    or type(s.get('page_number')) is not int or s.get('page_number', 0) < 1
                    or not isinstance(page_numbers, list) or s.get('page_number') not in page_numbers
                    or not isinstance(s.get('text'), str) or not s.get('text', '').strip()):
                issue('incomplete_traceability', path, 'error', 'Invalid filename, page, or evidence.')

    def field_value(item, path):
        if item is None:
            return None
        if not isinstance(item, dict) or not isinstance(item.get('value'), str) or not item['value']:
            issue('invalid_field', path, 'error', 'Expected a nonempty value with sources.')
            return None
        trace(item, path)
        return item['value']

    def numeric(value, path, percent=False):
        if value is None:
            return
        if not isinstance(value, str) or not re.fullmatch(r'-?\d+(?:\.\d+)?\s*%?', value):
            issue('invalid_numeric', path, 'error', 'Expected an explicit numeric value.')
            return
        number = float(value.rstrip('% ').strip())
        if number < 0 or (percent and number > 100):
            issue('invalid_percentage' if percent else 'invalid_numeric', path, 'error', 'Value outside permitted range.')
        if not percent and '%' in value:
            issue('invalid_numeric', path, 'error', 'Percentage cannot be used as marks or count.')

    metadata = record['metadata'] if isinstance(record['metadata'], dict) else {}
    for key in _METADATA:
        value = field_value(metadata.get(key), 'metadata.' + key)
        if value is None and key != 'units':
            issue('missing_' + key, 'metadata.' + key, 'warning' if key == 'department_division' else 'error', 'Field is missing or unusable.')
        if key == 'course_code' and value:
            if not _CODES.fullmatch(value):
                issue('malformed_course_code', 'metadata.course_code', 'error', 'Code does not match the existing parser grammar.')
            elif not re.fullmatch(r'[A-Z]{2,8} [FUGCE]\d{3}[A-Z]?', value):
                issue('complex_course_code', 'metadata.course_code', 'needs_verification', 'Shared or compact code retained without expansion.')

    for key in _LISTS:
        if not isinstance(record[key], list):
            continue
        for i, item in enumerate(record[key]):
            path = f'{key}[{i}]'
            if not isinstance(item, dict):
                issue('invalid_structure', path, 'error', 'Expected an extracted item object.')
                continue
            trace(item, path)
            if key == 'evaluation':
                if not isinstance(item.get('name'), str) or not item['name']:
                    issue('malformed_evaluation', path, 'error', 'Evaluation component has no name.')
                for field in ('marks', 'weightage', 'count'):
                    numeric(item.get(field), path + '.' + field, field == 'weightage')
            elif key == 'instructors':
                names = item.get('names')
                if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names):
                    issue('invalid_structure', path, 'error', 'Expected nonempty instructor names.')
                elif len(names) != len(set(names)):
                    issue('duplicate_instructor', path, 'warning', 'Removed identical names within the same block.')
                    item['names'] = list(dict.fromkeys(names))
            if key != 'evaluation' and (not isinstance(item.get('text'), str) or not item['text'].strip()):
                issue('invalid_structure', path, 'error', 'Missing extracted text.')

    exams = record['exams']
    if not isinstance(exams, dict):
        issue('invalid_structure', 'exams', 'error', 'Expected exam object.')
        exams = {}
    for kind in ('midsemester', 'comprehensive'):
        exam = exams.get(kind)
        if exam is None:
            continue
        if not isinstance(exam, dict):
            issue('invalid_structure', 'exams.' + kind, 'error', 'Expected exam field object.')
            continue
        for field, item in exam.items():
            value = field_value(item, f'exams.{kind}.{field}')
            if field in ('marks', 'weightage'):
                numeric(value, f'exams.{kind}.{field}', field == 'weightage')

    # Per-page extractor outputs retain conflicts hidden by whole-document selection.
    seen = {}
    observations = record['observations']
    if not isinstance(observations, list):
        issue('invalid_structure', 'observations', 'error', 'Expected per-page observation list.')
        observations = []
    for observation in observations:
        if (not isinstance(observation, dict) or not isinstance(observation.get('metadata', {}), dict)
                or not isinstance(observation.get('exams', {}), dict)):
            issue('invalid_structure', 'observations', 'error', 'Expected per-page extractor objects.')
            continue
        for key, item in observation.get('metadata', {}).items():
            if isinstance(item, dict) and isinstance(item.get('value'), str):
                seen.setdefault('metadata.' + key, set()).add(' '.join(item['value'].split()).casefold())
        for kind, exam in observation.get('exams', {}).items():
            if exam is not None and not isinstance(exam, dict):
                issue('invalid_structure', 'observations.exams', 'error', 'Expected exam object.')
                continue
            for field, item in (exam or {}).items():
                if field != 'name' and isinstance(item, dict) and isinstance(item.get('value'), str):
                    seen.setdefault(f'exams.{kind}.{field}', set()).add(' '.join(item['value'].split()).casefold())
    for path, values in seen.items():
        if len(values) > 1:
            issue('conflicting_values', path, 'needs_verification', 'Different page values retained in observations; no correction made.')
    record['original'] = original
    record['validation'] = {
        'is_valid': not any(i['severity'] == 'error' for i in issues),
        'needs_verification': any(i['severity'] in ('error', 'needs_verification') for i in issues),
        'issues': issues,
    }
    return record


def build_handout_record(pages):
    """Combine existing extractors for one handout; write no dataset files."""
    if len({p['source_file'] for p in pages}) > 1:
        raise ValueError('Expected pages from one handout')
    content = extract_handout_content(pages)
    policies = extract_handout_policies(pages)
    metadata = extract_course_metadata(pages)
    return normalize_handout({
        'source': {'source_file': metadata['source_file'], 'page_numbers': [p['page_number'] for p in pages]},
        'metadata': {k: metadata[k] for k in _METADATA},
        'instructors': content['instructors'], 'syllabus': content['syllabus'],
        'evaluation': extract_evaluation_components(pages), 'exams': extract_exam_details(pages),
        'attendance': policies['attendance'], 'makeup': policies['makeup'],
        'observations': [{'page_number': p['page_number'], 'metadata': extract_course_metadata([p]),
                          'exams': extract_exam_details([p])} for p in pages],
    })
