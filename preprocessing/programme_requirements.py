"""Extract explicit Bulletin lists; retain uncertain layouts for review."""

import argparse
import re
from pathlib import Path

from preprocessing.dataset import write_dataset
from preprocessing.pdf_extractor import extract_pdf_text

CODE = r'[A-Z]{2,8}\*?\s+(?:[FGK]\d{3}[A-Z]?|Z[CG]\d{3}[A-Z]?)'
ROW = re.compile(r'^(' + CODE + r')\s+(.+?)\s+(\d+|-)\s+(\d+|-)\s+(\d+)(\*?)$')
CATEGORY = re.compile(r'^(CORE COURSES|DISCIPLINE ELECTIVE COURSES|Electives)(?:\s+L P U)?\s*$', re.I)
PAGE = re.compile(r'^\s*(IV|V)-(\d+)\s*$', re.M)


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
    for key in ('units', 'course_count'):
        if record.get(key) is not None and (type(record[key]) is not int or record[key] < 0):
            flag('malformed_numeric_value', 'error')
    if record.get('kind') == 'unresolved_choice':
        flag('incomplete_choice_structure')
    if record.get('needs_verification'):
        flag('manual_verification')
    return {'is_valid': not any(i['severity'] == 'error' for i in issues), 'issues': issues}


def extract_programme_requirements(pages):
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
    return dict(source_file=pages[0]['source_file'], pages_processed=len(pages),
                programmes=programmes, requirements=requirements, unresolved_sections=unresolved,
                limitations=['Programme headings remain separate source occurrences; no inferred equivalence.',
                             'Unparsed tables, footnotes and scope conditions remain in programme evidence.',
                             'Semester columns and higher-degree/WILP structures require review.',
                             'Elective options are not mandatory course requirements.'])


def build_programme_requirements(source_path, output_path):
    source, output = Path(source_path).resolve(), Path(output_path).resolve()
    if output.suffix != '.json' or source.parent in output.parents:
        raise ValueError('Output must be JSON outside the raw source directory')
    result = extract_programme_requirements(extract_pdf_text(source))
    write_dataset(result, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/raw/bulletin.pdf')
    parser.add_argument('--output', default='data/processed/programme_requirements.json')
    args = parser.parse_args()
    result = build_programme_requirements(args.source, args.output)
    print(f"Wrote {len(result['requirements'])} requirements")
