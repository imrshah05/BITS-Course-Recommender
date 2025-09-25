"""Parse the coursewise timetable while preserving section and page evidence."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re

from preprocessing.dataset import write_dataset
from preprocessing.course_codes import CODE
from preprocessing.pdf_extractor import extract_pdf_text

HEADING = 'II. COURSEWISE TIMETABLE'
COURSE = re.compile(r'^(\d{3,})\s+([A-Z]+\s+\S+)\s+(.+?)\s+((?:(?:\d+(?:\.\d+)?|-)[ ]+){4}(?:\d+(?:\.\d+)?|-))\s+(.*)$')
SECTION = re.compile(r'^(?:(Tutorial|Practical)\s+)?([LTP]\d+)\s*(.*)$', re.I)
EXAM = re.compile(r'\b(\d{1,2}/\d{1,2})\s+(FN[12]?|AN[12]?)\b')
MEETING = re.compile(r'((?:(?:Th|M|T|W|F|S)\s+)+)((?:\d+\s*)+)')


def flag(record, code, severity='warning'):
    entry = dict(code=code, severity=severity)
    if entry not in record['validation']['issues']:
        record['validation']['issues'].append(entry)


def validate_record(record):
    if not record.get('course_code'):
        flag(record, 'missing_course_code', 'error')
    elif not re.fullmatch(CODE, record['course_code']):
        flag(record, 'malformed_course_code', 'error')
    if not re.fullmatch(r'[LTP]\d+', record.get('section') or ''):
        flag(record, 'missing_or_malformed_section', 'error')
    if not record.get('sources') or any(not s.get('text') or not s.get('source_file') or
                                       type(s.get('page_number')) is not int or s['page_number'] < 1
                                       for s in record['sources']):
        flag(record, 'missing_traceability', 'error')
    for meeting in record['meetings']:
        if not meeting['days'] or any(d not in ('M', 'T', 'W', 'Th', 'F', 'S') for d in meeting['days']):
            flag(record, 'malformed_days', 'error')
        if not meeting['hours'] or any(type(h) is not int or h < 1 or h > 10 for h in meeting['hours']):
            flag(record, 'malformed_hours', 'error')
    for key in ('midsem_slot', 'compre_slot'):
        slot = record.get(key)
        if slot:
            day, month = map(int, slot['date'].split('/'))
            if not (1 <= day <= 31 and 1 <= month <= 12):
                flag(record, 'malformed_exam_date', 'error')
    record['needs_verification'] = bool(record['validation']['issues'])
    record['validation']['is_valid'] = not any(i['severity'] == 'error' for i in record['validation']['issues'])
    return record['validation']


def _summary(records, unresolved_count, duplicate_count=None):
    groups = {(record.get('computer_code'), record.get('course_code'), record.get('section'))
              for record in records}
    return dict(records=len(records), unique_course_codes=len({r['course_code'] for r in records if r['course_code']}),
                unique_sections=len(groups), section_types=dict(Counter(r['section_type'] for r in records)),
                meetings=sum(len(r['meetings']) for r in records),
                instructors=sum(bool(r['instructors']) for r in records), rooms=sum(bool(r['room']) for r in records),
                midsem_slots=sum(bool(r['midsem_slot']) for r in records), compre_slots=sum(bool(r['compre_slot']) for r in records),
                needs_verification=sum(r['needs_verification'] for r in records),
                duplicates=(duplicate_count if duplicate_count is not None else
                            sum(any(i['code'] == 'duplicate_record' for i in r['validation']['issues']) for r in records) // 2),
                warnings=sum(i['severity'] == 'warning' for r in records for i in r['validation']['issues']),
                errors=sum(i['severity'] == 'error' for r in records for i in r['validation']['issues']),
                missing_traceability=sum(any(i['code'] == 'missing_traceability' for i in r['validation']['issues']) for r in records),
                unresolved_lines=unresolved_count)


def revalidate_timetable_dataset(dataset):
    """Reapply current code validation to existing records without reading the PDF."""
    result = deepcopy(dataset)
    for record in result.get('records', []):
        retained = [issue for issue in (record.get('validation') or {}).get('issues', [])
                    if issue.get('code') not in ('malformed_course_code', 'missing_course_code')]
        record['validation'] = {'issues': retained}
        record['needs_verification'] = bool(retained)
        validate_record(record)
    result['summary'] = _summary(result.get('records', []), len(result.get('unresolved_evidence', [])),
                                 (result.get('summary') or {}).get('duplicates'))
    return result


def extract_timetable(pages):
    """Keep one record per section occurrence; do not infer missing exam slots."""
    if not pages or len({p['source_file'] for p in pages}) != 1:
        raise ValueError('Expected pages from one timetable')
    numbers = [p['page_number'] for p in pages]
    if len(set(numbers)) != len(numbers) or any(type(n) is not int or n < 1 for n in numbers):
        raise ValueError('Page numbers must be unique positive integers')
    records, unresolved, legends, notes = [], [], [], []
    course = None
    current = None
    def evidence(page, text):
        return dict(source_file=page['source_file'], page_number=page['page_number'], text=text)
    for page in sorted(pages, key=lambda p: p['page_number']):
        text = page['text']
        if 'L E G E N D' in text:
            legends.append(evidence(page, text))
        if HEADING not in text:
            current = None
            continue
        # Data starts after the repeated column header's final H line.
        lines = text.splitlines()
        start = next((i+1 for i, line in enumerate(lines) if line.strip() == 'H'), None)
        if start is None:
            unresolved.append(evidence(page, text)); current = None; course = None
            continue
        for line in lines[start:]:
            line = line.strip()
            if not line:
                continue
            source = evidence(page, line)
            if line.startswith('Note:'):
                notes.append(source); current = None
                continue
            new = COURSE.match(line)
            body = line
            if new:
                course = dict(computer_code=new[1], course_code=' '.join(new[2].split()),
                              course_title=new[3], credit_text=new[4], course_source=source)
                body = new[5]
                current = None
            elif re.match(r'^\d{3,}\s+[A-Z]', line):
                unresolved.append(source); course = None; current = None
                continue
            section = SECTION.match(body)
            if section:
                current = dict(course_code=course['course_code'] if course else None,
                               course_title=course['course_title'] if course else None,
                               computer_code=course['computer_code'] if course else None,
                               course_source=deepcopy(course['course_source']) if course else None,
                               section=section[2].upper(), section_type=section[2][0].upper(),
                               section_label=section[1], instructors=[], room=None, meetings=[],
                               start_time=None, end_time=None, midsem_slot=None, compre_slot=None,
                               status=None, source_heading=HEADING, sources=[source],
                               validation={'issues': []})
                records.append(current)
                rest = section[3].strip()
                if re.fullmatch(r'CANC(?:EL)?LED|CANCELED|CANCLED', rest, re.I):
                    current['status'] = 'cancelled'
                    continue
                for match in EXAM.finditer(rest):
                    key = 'midsem_slot' if match[2][-1].isdigit() else 'compre_slot'
                    value = dict(date=match[1], session=match[2])
                    if current[key] and current[key] != value:
                        flag(current, 'conflicting_exam_slots')
                        current[key] = None
                    else:
                        current[key] = value
                rest = EXAM.sub('', rest).strip()
                room = re.fullmatch(r'(.*?)\s+(\d{4}[\w-]*)\s+(.+)', rest)
                if room:
                    current['room'] = room[2]
                    if room[1].strip():
                        current['instructors'].append(room[1].strip())
                    meeting_text = room[3] + ' '
                    for match in MEETING.finditer(meeting_text):
                        current['meetings'].append(dict(days=match[1].split(), hours=[int(n) for n in match[2].split()],
                                                        text=match[0].strip()))
                    if MEETING.sub('', meeting_text).strip() or not current['meetings']:
                        flag(current, 'ambiguous_day_hour_fields')
                elif rest and re.fullmatch(r'[A-Za-z .()\-]+', rest):
                    current['instructors'].append(rest)
                elif rest:
                    flag(current, 'ambiguous_instructor_room_or_meeting')
                if not current['meetings']:
                    flag(current, 'missing_meeting_information')
            elif current:
                current['sources'].append(source)
                # Continuation names lack section delimiters: retain as candidates.
                flag(current, 'unresolved_continuation')
                current.setdefault('continuation_evidence', []).append(source)
            else:
                unresolved.append(source)
    seen, groups = {}, defaultdict(list)
    duplicates = 0
    for record in records:
        key = tuple(record.get(k) for k in ('computer_code', 'course_code', 'section'))
        facts = {k: record[k] for k in ('course_title', 'instructors', 'room', 'meetings', 'midsem_slot', 'compre_slot', 'status')}
        signature = json.dumps([key, facts], sort_keys=True)
        if signature in seen:
            duplicates += 1
            flag(record, 'duplicate_record'); flag(seen[signature], 'duplicate_record')
        else:
            seen[signature] = record
        groups[key].append((record, facts))
    for group in groups.values():
        if len({json.dumps(facts, sort_keys=True) for _, facts in group}) > 1:
            for record, _ in group:
                flag(record, 'conflicting_section_occurrences')
    for record in records:
        validate_record(record)
    records.sort(key=lambda r: (r['course_code'] or '', r['computer_code'] or '', r['section_type'], int(r['section'][1:]),
                                r['sources'][0]['page_number'], r['sources'][0]['text']))
    summary = _summary(records, len(unresolved), duplicates)
    return dict(source_file=pages[0]['source_file'], pages_processed=len(pages), records=records,
                summary=summary, legend_sources=legends, notes=notes, unresolved_evidence=unresolved,
                limitations=['Hours and day abbreviations are retained; clock times are not inferred.',
                             'Continuation names/rows are evidence, not guessed instructor associations.',
                             'Missing exam slots are not copied between sections.'])


def build_timetable(source_path, output_path):
    source, output = Path(source_path).resolve(), Path(output_path).resolve()
    if output.suffix != '.json' or source.parent in output.parents or output.name in ('courses.json', 'academic_rules.json', 'academic_regulations.json', 'programme_requirements.json'):
        raise ValueError('Output must be a separate timetable JSON outside the source directory')
    result = extract_timetable(extract_pdf_text(source))
    write_dataset(result, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/raw/timetable.pdf')
    parser.add_argument('--output', default='data/processed/timetable.json')
    args = parser.parse_args()
    print(json.dumps(build_timetable(args.source, args.output)['summary'], indent=2))
