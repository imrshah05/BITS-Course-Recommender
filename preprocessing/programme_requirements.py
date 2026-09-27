"""Extract explicit Bulletin lists; retain uncertain layouts for review."""

import argparse
from copy import deepcopy
import json
import re
from pathlib import Path

from preprocessing.course_codes import CODE, normalize_course_code
from preprocessing.dataset import write_dataset
from preprocessing.pdf_extractor import extract_pdf_layout_pages, extract_pdf_text

ROW = re.compile(r'^(' + CODE + r')\s+(.+?)\s+(\d+|-)\s+(\d+|-)\s+(\d+)(\*?)$')
CATEGORY = re.compile(r'^(CORE COURSES|DISCIPLINE ELECTIVE COURSES|Electives)(?:\s+L P U)?\s*$', re.I)
PAGE = re.compile(r'^\s*(IV|V)-(\d+)\s*$', re.M)
SCOPED_POOL = re.compile(r'\b(Pool of [A-Za-z ]+ courses for [A-Za-z ]+?\s+programmes):?', re.I)
POOL_BOUNDARY = re.compile(r'^(Other Courses|List of Audit Type Courses|MINOR PROGRAMMES FOR FIRST)', re.I | re.M)


def validate_requirement(record):
    issues = []
    def flag(code, severity='warning'):
        issues.append({'code': code, 'severity': severity})
    if not record.get('programme_name'):
        flag('missing_programme_scope')
    if not record.get('sources') or any(not s.get('text') or not s.get('source_file')
                                       or type(s.get('page_number')) is not int
                                       or s['page_number'] < 1 for s in record['sources']):
        flag('incomplete_source', 'error')
    if record.get('course_code') and not re.fullmatch(CODE, record['course_code']):
        flag('malformed_course_code', 'error')
    if record.get('course_code') and record.get('catalogue_status') in ('unknown', 'not_checked'):
        flag('course_not_validated_against_catalogue')
    for key in ('units', 'course_count'):
        if record.get(key) is not None and (type(record[key]) is not int or record[key] < 0):
            flag('malformed_numeric_value', 'error')
    if record.get('kind') == 'unresolved_choice':
        flag('incomplete_choice_structure')
    if record.get('needs_verification'):
        flag('manual_verification')
    return {'is_valid': not any(i['severity'] == 'error' for i in issues), 'issues': issues}


def recover_scoped_course_pools(dataset):
    """Split explicitly scoped institutional pools from an unnamed catalogue block."""
    programmes = dataset.get('programmes', [])
    requirements = dataset.get('requirements', [])
    humanities_ids = {item['id'] for item in programmes
                      if (item.get('name') or '').lower().startswith('pool of humanities courses')}
    for requirement in requirements:
        if requirement.get('programme_id') in humanities_ids:
            requirement['category'] = 'Humanities Electives'
            requirement['validation'] = validate_requirement(requirement)
    for parent in list(programmes):
        if parent.get('name') or parent.get('context') != 'discipline course list':
            continue
        pages = {source['page_number']: source for source in parent.get('sources', [])}
        starts = []
        for page_number, source_record in pages.items():
            for match in SCOPED_POOL.finditer(source_record['text']):
                name = ' '.join(match[1].split())
                starts.append((page_number, match.start(), name, match[0]))
        for start_page, start_offset, name, heading in starts:
            end = None
            for page_number in sorted(number for number in pages if number >= start_page):
                text = pages[page_number]['text']
                search_from = start_offset + len(heading) if page_number == start_page else 0
                boundary = POOL_BOUNDARY.search(text[search_from:])
                if boundary:
                    end = (page_number, search_from + boundary.start())
                    break
            scoped = []
            for requirement in requirements:
                if requirement.get('programme_id') != parent['id'] or not requirement.get('course_code'):
                    continue
                source_record = requirement['sources'][0]
                page_number = source_record['page_number']
                page_text = pages.get(page_number, {}).get('text', '')
                position = page_text.find(requirement['course_code'])
                point = (page_number, position)
                if position >= 0 and point > (start_page, start_offset) and (end is None or point < end):
                    scoped.append(requirement)
            if not scoped:
                continue
            programme_id = f'programme-{len(programmes)+1:04}'
            evidence = dict(source_file=dataset['source_file'], page_number=start_page, text=heading)
            programmes.append(dict(id=programme_id, name=name, programme_code=None,
                                   context='institutional course pool', sources=[evidence]))
            for requirement in scoped:
                requirement['programme_id'] = programme_id
                requirement['programme_name'] = name
                if name.lower().startswith('pool of humanities courses'):
                    requirement['category'] = 'Humanities Electives'
                requirement['needs_verification'] = False
                requirement['validation'] = validate_requirement(requirement)
    return dataset


def _chart_name(text):
    chart = re.search(
        r'Semester\s*-?wise\s+Pattern for Students Admitted to\s+(.+?)\s+Programme\b',
        text, re.I | re.S)
    dual = re.search(
        r'Semester\s*-?wise\s+pattern for composite Dual Degree Programme\s*s?\s+'
        r'(.+?)\s+Year\b', text, re.I | re.S)
    match = dual or chart
    return ' '.join(match[1].split()).strip('() ') if match else None


def _name_key(value):
    return re.sub(r'[^a-z0-9]+', '', value.casefold()) if isinstance(value, str) else ''


def _semester_chart_references(layout_text, programme_name, programme_names):
    """Retain explicit composite-chart references without expanding their courses."""
    parts = re.split(r'\s+with\s+', programme_name, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return []
    candidates = [name for name in programme_names
                  if _name_key(name) == _name_key(parts[0])]
    referenced = candidates[0] if len(candidates) == 1 else None
    lines = layout_text.splitlines()
    headers = [line for line in lines
               if 'First Semester' in line and 'Second Semester' in line]
    if not headers:
        return []
    header = headers[0]
    boundary = (header.find('First Semester') + header.find('Second Semester')) // 2
    if boundary <= 0:
        return []
    references = []
    for line in lines:
        marker = re.match(r'^\s*(I|II|III|IV|V)\s+', line[:boundary])
        if not marker:
            continue
        first, second = line[:boundary], line[boundary:]
        phrase = r'\bSame as First degree Programme\b'
        covered = []
        if re.search(phrase, first, re.I):
            covered.append(1)
        if re.search(phrase, second, re.I):
            covered.append(2)
        if not covered:
            continue
        year = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5}[marker[1]]
        references.append({
            'reference_type': 'same_as_first_degree_programme',
            'referenced_programme_role': 'first_degree',
            'referenced_programme_name': referenced,
            'covered_periods': [
                {'year': year, 'semester': semester} for semester in covered],
            'needs_verification': referenced is None,
            'evidence': line.strip(),
        })
    return references


def _semester_chart_courses(layout_text, programme_name, known_course_codes):
    """Return only course identities whose year and table column are explicit."""
    lines = layout_text.splitlines()
    headers = [line for line in lines
               if 'First Semester' in line and 'Second Semester' in line]
    if not headers:
        return []
    header = headers[0]
    first_start = header.find('First Semester')
    second_start = header.find('Second Semester')
    if first_start < 0 or second_start <= first_start:
        return []
    boundary = (first_start + second_start) // 2

    def is_total(line):
        if re.search(CODE, line):
            return False
        totals = re.findall(r'\b\d+(?:\s*/\s*\d+)?(?:\s*\(min\))?\b', line, re.I)
        return len(totals) >= 2

    blocks, pending = [], []
    chart_started = False
    for line in lines:
        if 'First Semester' in line and 'Second Semester' in line:
            if pending:
                blocks.append(pending)
                pending = []
            chart_started = True
            continue
        if not chart_started:
            continue
        if re.match(r'^\s*Summer\b', line, re.I):
            if pending:
                blocks.append(pending)
                pending = []
            continue
        if re.match(r'^\s*(?:Discipline Core|\*?Discipline Electives)', line, re.I):
            if pending:
                blocks.append(pending)
                pending = []
            break
        pending.append(line)
        if is_total(line):
            blocks.append(pending)
            pending = []
    if pending:
        blocks.append(pending)

    rows = []
    for block in blocks:
        markers = []
        for line in block:
            match = re.match(r'^\s*(I|II|III|IV|V)\s+(?:\S|$)', line[:boundary])
            if match:
                markers.append(match[1])
        if len(set(markers)) != 1:
            continue
        year = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5}[markers[0]]
        block_rows = []
        alternative_lines = {1: set(), 2: set()}
        for index, line in enumerate(block):
            for semester, segment in ((1, line[:boundary]), (2, line[boundary:])):
                if re.search(r'\bor\b', segment, re.I):
                    alternative_lines[semester].add(index)
                for match in re.finditer(
                        r'\b([A-Z]{2,8}\*?)\s+'
                        r'((?:[FUGKCE]\d{3}[A-Z]?|Z[CG]\d{3}[A-Z]?)(?:-\d+)?)\b',
                        segment):
                    code = normalize_course_code(f'{match[1]} {match[2]}')
                    if code:
                        block_rows.append(dict(
                            course_code=code, year=year, semester=semester,
                            line_index=index, evidence=line.strip()))
        by_semester = {(year, semester): [] for semester in (1, 2)}
        for row in block_rows:
            by_semester[(year, row['semester'])].append(row)
        for group in by_semester.values():
            group.sort(key=lambda item: item['line_index'])
            for position, row in enumerate(group):
                previous = group[position - 1]['line_index'] if position else -1
                following = (group[position + 1]['line_index']
                             if position + 1 < len(group) else len(block))
                row['alternative_context'] = any(
                    previous < line_index < following
                    for line_index in alternative_lines[row['semester']])
        rows.extend(block_rows)

    output, seen = [], set()
    for row in rows:
        key = (row['course_code'], row['year'], row['semester'])
        if key in seen:
            continue
        seen.add(key)
        code = row['course_code']
        catalogue_status = ('not_checked' if known_course_codes is None else
                            'matched' if code in known_course_codes else 'unknown')
        uncertainty_reasons = []
        if row['alternative_context']:
            uncertainty_reasons.append('alternative_context')
        if catalogue_status != 'matched':
            uncertainty_reasons.append('catalogue_identity_unknown')
        if code.endswith('T') or re.search(
                r'\b(?:Thesis|Practice School)\b', row['evidence'], re.I):
            uncertainty_reasons.append('thesis_or_practice_school')
        output.append(dict(
            course_code=code, course_title=None,
            year=row['year'], semester=row['semester'],
            category='Semester-wise curriculum',
            catalogue_status=catalogue_status,
            chart_uncertainty_reasons=uncertainty_reasons,
            needs_verification=bool(uncertainty_reasons),
            evidence=row['evidence']))
    return sorted(output, key=lambda item: (
        item['year'], item['semester'], item['course_code']))


def _load_catalogue_codes(path):
    if not path.is_file():
        raise FileNotFoundError(f'Course catalogue is required for chart validation: {path}')
    with path.open(encoding='utf-8') as stream:
        dataset = __import__('json').load(stream)
    codes = set()
    for record in dataset.get('records', []):
        direct = normalize_course_code(record.get('course_code')) \
            if isinstance(record, dict) else None
        identity = record.get('identity') if isinstance(record, dict) else None
        if direct and (not isinstance(identity, dict) or
                       identity.get('needs_verification') is not True):
            codes.add(direct)
        metadata = record.get('metadata') if isinstance(record, dict) else None
        if not isinstance(metadata, dict):
            continue
        for value in metadata.get('course_codes') or []:
            code = normalize_course_code(value)
            if code:
                codes.add(code)
    if not codes:
        raise ValueError('Course catalogue contains no usable course identities')
    return codes


def revalidate_programme_requirement_identities(dataset, known_course_codes):
    """Revalidate chart identities after the unified source catalogue exists."""
    result = deepcopy(dataset)
    known = set(known_course_codes)
    for record in result.get('requirements', []):
        if (record.get('kind') != 'required_course' or
                record.get('category') != 'Semester-wise curriculum' or
                not record.get('course_code')):
            continue
        matched = record['course_code'] in known
        record['catalogue_status'] = 'matched' if matched else 'unknown'
        reasons = [reason for reason in record.get('chart_uncertainty_reasons', [])
                   if reason != 'catalogue_identity_unknown']
        if not matched:
            reasons.append('catalogue_identity_unknown')
        record['chart_uncertainty_reasons'] = reasons
        record['needs_verification'] = bool(reasons)
        record['validation'] = validate_requirement(record)
    return result


def revalidate_programme_requirements_file(requirements_path, catalogue_path):
    requirements_path = Path(requirements_path)
    with requirements_path.open(encoding='utf-8') as stream:
        dataset = json.load(stream)
    result = revalidate_programme_requirement_identities(
        dataset, _load_catalogue_codes(Path(catalogue_path)))
    write_dataset(result, requirements_path)
    return result


def extract_programme_requirements(pages, layout_pages=None, known_course_codes=None):
    """Parse clear L/P/U course lists and retain complete programme context.

    Semester chart columns are not reconstructed. Programme names are source
    headings, not canonical identities inferred from course prefixes.
    """
    if not pages or len({p['source_file'] for p in pages}) != 1:
        raise ValueError('Expected page records from one Bulletin')
    numbers = [p['page_number'] for p in pages]
    if any(type(n) is not int or n < 1 for n in numbers) or len(numbers) != len(set(numbers)):
        raise ValueError('Expected unique positive PDF page numbers')
    programmes, requirements, unresolved = [], [], []
    layout_pages = {page["page_number"]: page for page in (layout_pages or [])}
    known_course_codes = set(known_course_codes) if known_course_codes is not None else None
    active = None
    catalogue = False
    category = None
    pending = []

    def source(page, text):
        return dict(source_file=page['source_file'], page_number=page['page_number'], text=text)

    def programme(name, context, evidence):
        nonlocal active, category, pending
        active = dict(id=f'programme-{len(programmes)+1:04}', name=name, programme_code=None,
                      context=context, sources=[evidence])
        programmes.append(active)
        category, pending = None, []

    def add(kind, evidence, **fields):
        record = dict(id=f'requirement-{len(requirements)+1:05}', kind=kind,
                      programme_id=active['id'] if active else None,
                      programme_name=active['name'] if active else None,
                      category=category, course_code=None, course_title=None, units=None,
                      course_count=None, year=None, semester=None,
                      needs_verification=True, sources=[evidence])
        record.update(fields)
        record['validation'] = validate_requirement(record)
        requirements.append(record)

    for page in sorted(pages, key=lambda p: p['page_number']):
        text = page['text']
        printed = PAGE.search(text)
        if not printed:
            continue
        part, _ = printed.groups()
        lines = [line.strip() for line in text.splitlines() if line.strip() and not PAGE.fullmatch(line)]
        whole = source(page, text)
        if 'List of Courses for B.E.' in text:
            catalogue = True
            active, category, pending = None, None, []
        if 'MINOR PROGRAMMES FOR FIRST' in text or part == 'V' or '2+2 INTERNATIONAL COLLABORATION' in text:
            catalogue = False
            active, category, pending = None, None, []
        if 'The category-wise structure of each program:' in text:
            programme('INTEGRATED FIRST DEGREE PROGRAMMES', 'category-wise structure', whole)
            add('programme_structure', whole)
            for line in lines:
                m = re.fullmatch(r'(.+?)\s+(\d+)\s+(\d+)', line)
                if m and not re.search(CODE, line):
                    add('category_total', source(page, line), category=m[1], units=int(m[2]),
                        course_count=int(m[3]), needs_verification=False)
            active = None
            continue
        # Nominal charts carry full scope and footnotes, but not recoverable columns.
        chart = re.search(r'Semester\s*-?wise\s+Pattern for Students Admitted to\s+(.+?)\s+Programme\b', text, re.I | re.S)
        dual = re.search(r'Semester\s*-?wise\s+pattern for composite Dual Degree Programme\s*s?\s+(.+?)\s+Year\b', text, re.I | re.S)
        if not catalogue and (chart or dual):
            match = dual or chart
            name = ' '.join(match[1].split()).strip('() ')
            programme(name, 'composite dual-degree chart' if dual else 'semester-wise chart', whole)
            add('programme_structure', whole)
            for m in re.finditer(r'(Discipline Core|Discipline Electives)\s*[-–]?\s*(\d+)\s*Units\s*\(\s*(\d+)\s*Courses\s*\)', text, re.I):
                add('category_total', source(page, m[0]), category=m[1], units=int(m[2]), course_count=int(m[3]), needs_verification=False)
            if re.search(r'\bor\b', text, re.I):
                add('unresolved_choice', whole)
            layout_page = layout_pages.get(page['page_number'])
            if layout_page:
                for reference in _semester_chart_references(
                        layout_page['text'], active['name'],
                        [item['name'] for item in programmes[:-1]]):
                    add('curriculum_reference',
                        source(page, reference.pop('evidence')), **reference)
                for row in _semester_chart_courses(
                        layout_page['text'], active['name'], known_course_codes):
                    add('required_course', source(page, row.pop('evidence')), **row)
            active, category = None, None
            continue
        # A fresh uppercase heading immediately before CORE COURSES is a scope boundary.
        starts = {}
        if catalogue:
            for i, line in enumerate(lines):
                if re.fullmatch(r'CORE COURSES(?:\s+L P U)?', line, re.I):
                    j = i - 1
                    while j >= 0 and re.fullmatch(r'[A-Z][A-Z ,&–\-()]+', lines[j]) and not CATEGORY.fullmatch(lines[j]):
                        j -= 1
                    starts[j+1 if j+1 < i else i] = (' '.join(lines[j+1:i]) or None)
        consumed = False
        for i, line in enumerate(lines):
            if i in starts:
                programme(starts[i], 'discipline course list', source(page, line))
            minor = re.fullmatch(r'Minor in .+', line)
            if minor and part == 'IV' and not catalogue:
                programme(line, 'minor', source(page, line))
            if active:
                if whole not in active['sources']:
                    active['sources'].append(whole)
                consumed = True
            cat = CATEGORY.fullmatch(line)
            if cat:
                category, pending = cat[1], []
                continue
            if not active:
                continue
            # Explicit minor totals may be on separate lines.
            for m in re.finditer(r'\b(\d+)\s+(courses|units)\s*\((min|max)\)', line, re.I):
                add('quantity', source(page, line), **{'course_count' if m[2].lower() == 'courses' else 'units': int(m[1])}, comparator=m[3].lower())
            if re.fullmatch(r'or', line, re.I) or '/' in line and re.search(CODE, line):
                add('unresolved_choice', source(page, line))
                pending = []
                continue
            if re.match(r'^' + CODE + r'\b', line):
                pending = [line]
            elif pending and not re.match(r'^(Course |\*|Track|Pool|Note)', line):
                pending.append(line)
            else:
                pending = []
            if re.fullmatch(r'[A-Z][A-Z ,&–\-()]+', line) and not pending and line not in ('L P U', 'OR'):
                category = None
            joined = ' '.join(pending)
            match = ROW.fullmatch(joined)
            if match and category and len(re.findall(CODE, joined)) == 1:
                add(('listed_course' if re.search(r'\bor\b', text, re.I) else 'required_course') if category.lower() == 'core courses' else 'elective_option',
                    source(page, '\n'.join(pending)), course_code=' '.join(match[1].split()),
                    course_title=match[2], units=int(match[5]), needs_verification=bool(match[6]) or active['name'] is None)
                pending = []
            elif len(pending) > 5:
                pending = []
        if not consumed:
            unresolved.append(dict(source=whole, section=printed[0].strip(), needs_verification=True,
                                   reason='Programme scope or table relationships not reliably resolved'))
        # Minor context cannot bleed into other programme families.
        if active and active['context'] == 'minor' and ('2+2 INTERNATIONAL' in text or part != 'IV'):
            active, category = None, None
        pending = []
    # Retain a policy record for every list scope, including unparsed rows and notes.
    for item in programmes:
        if item['context'] in ('minor', 'discipline course list'):
            active = item
            category = None
            add('programme_evidence', item['sources'][0], sources=item['sources'])
    result = dict(source_file=pages[0]['source_file'], pages_processed=len(pages),
                  programmes=programmes, requirements=requirements, unresolved_sections=unresolved,
                  limitations=['Programme headings remain separate source occurrences; no inferred equivalence.',
                               'Unparsed tables, footnotes and scope conditions remain in programme evidence.',
                               'Only chart rows with explicit layout-supported year and semester columns are structured; ambiguous rows remain for review.',
                               'Elective options are not mandatory course requirements.'])
    return recover_scoped_course_pools(result)


def build_programme_requirements(source_path, output_path, catalogue_path=None):
    source, output = Path(source_path).resolve(), Path(output_path).resolve()
    if output.suffix != '.json' or source.parent in output.parents:
        raise ValueError('Output must be JSON outside the raw source directory')
    pages = extract_pdf_text(source)
    chart_pages = [page['page_number'] for page in pages if _chart_name(page['text'])]
    layout_pages = extract_pdf_layout_pages(source, chart_pages)
    catalogue = Path(catalogue_path).resolve() if catalogue_path else output.parent / 'courses.json'
    known_codes = _load_catalogue_codes(catalogue)
    result = extract_programme_requirements(
        pages, layout_pages=layout_pages, known_course_codes=known_codes)
    write_dataset(result, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/raw/bulletin.pdf')
    parser.add_argument('--output', default='data/processed/programme_requirements.json')
    args = parser.parse_args()
    result = build_programme_requirements(args.source, args.output)
    print(f"Wrote {len(result['requirements'])} requirements")
