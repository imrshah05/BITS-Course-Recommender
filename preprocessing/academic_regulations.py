"""Preserve regulations as scoped section policies, not executable eligibility rules."""

import argparse
import re
from pathlib import Path

from preprocessing.dataset import write_dataset
from preprocessing.pdf_extractor import extract_pdf_text

_HEADING = re.compile(r'^\s*(\d{1,2})\.\s+(.+?)\s*$')
_CONTENTS = re.compile(r'^\s*(\d{1,2})\.\s+(.+?)\s+(\d+)\s*$')
_NUMBER = re.compile(r'\b(minimum|maximum)\s+(CGPA)\s+of\s+(\d+(?:\.\d+)?)\b', re.I)


def validate_rule(rule):
    """Validate provenance and numeric observations without repairing policy text."""
    issues = []
    def issue(code, severity, message):
        issues.append(dict(code=code, severity=severity, message=message))
    sources = rule.get('sources') or []
    if not sources or any(not s.get('source_file') or type(s.get('page_number')) is not int
                          or s['page_number'] < 1 for s in sources):
        issue('missing_source', 'error', 'A filename and positive PDF page number are required.')
    if not rule.get('text') or not sources or any(not s.get('text', '').strip() for s in sources):
        issue('missing_evidence', 'error', 'Policy and page evidence must be retained.')
    if not rule.get('scope_and_exceptions'):
        issue('incomplete_scope', 'error', 'Full policy context is required.')
    observations = rule.get('values', [])
    for value in observations:
        number = value.get('value')
        if type(number) not in (int, float) or not 0 <= number <= 10 or value.get('metric') != 'CGPA':
            issue('malformed_value', 'error', 'Expected an explicit CGPA value between zero and ten.')
        if not value.get('evidence') or value.get('source') not in sources:
            issue('missing_value_evidence', 'error', 'Numeric observations require page evidence.')
    for comparator in ('minimum', 'maximum'):
        if len({v.get('value') for v in observations if v.get('comparator') == comparator}) > 1:
            issue('multiple_scoped_values', 'warning', 'Different values may have different scopes; do not merge them.')
    if re.search(r'\d', rule.get('text', '')):
        issue('uninterpreted_numbers', 'warning', 'Numbers, references and table relationships are not fully interpreted.')
    if rule.get('needs_verification'):
        issue('human_verification', 'warning', 'Clause boundaries, conditions and exceptions require review before automation.')
    return {'is_valid': not any(i['severity'] == 'error' for i in issues), 'issues': issues}


def extract_academic_regulations(pages):
    """Use the document's contents list to identify sections across physical PDF pages.

    Clause labels have inconsistent reading order. Keep whole sections together so
    conditions and continuations are not attached to an unrelated numbered clause.
    """
    if not pages:
        raise ValueError('No regulations pages supplied')
    filenames = {p['source_file'] for p in pages}
    numbers = [p['page_number'] for p in pages]
    if len(filenames) != 1 or any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError('Expected one source with unique positive page numbers')
    pages = sorted(pages, key=lambda p: p['page_number'])
    headings = []
    contents_page = None
    for page in pages:
        if re.search(r'^\s*Contents\s*$', page['text'], re.M | re.I):
            headings = [(m[1], m[2]) for line in page['text'].splitlines()
                        for m in [_CONTENTS.match(line)] if m]
            contents_page = page['page_number']
            break
    if not headings:
        raise ValueError('No numbered contents entries found; section boundaries need review')
    rules = []
    active = None
    expected = 0
    for page in pages:
        if page['page_number'] <= contents_page:
            continue
        # Exclude cover/back matter after the document's final substantive page.
        if active and active['section']['number'] == headings[-1][0] and re.search(r'leadinnovate', page['text'], re.I):
            break
        buffer = []
        def flush():
            if active and ''.join(buffer).strip():
                active['sources'].append(dict(source_file=page['source_file'],
                                              page_number=page['page_number'], text='\n'.join(buffer)))
            buffer.clear()
        for line in page['text'].splitlines():
            match = _HEADING.match(line)
            if match and expected < len(headings):
                number, title = headings[expected]
                prefix = ' '.join(title.lower().split()[:3])
                candidate = ' '.join(match[2].lower().split())
                if match[1] == number and candidate.startswith(prefix):
                    flush()
                    active = dict(id=f'section-{number}', section=dict(number=number, heading=title),
                                  category=title, rule_type='descriptive_policy', sources=[])
                    rules.append(active)
                    expected += 1
            buffer.append(line)
        flush()
    if expected != len(headings):
        raise ValueError(f'Located {expected} of {len(headings)} sections; refusing incomplete output')
    for rule in rules:
        rule['text'] = '\n'.join(s['text'] for s in rule['sources'])
        # Full context is intentional: extracting condition fragments can lose exceptions.
        rule['scope_and_exceptions'] = rule['text']
        rule['clause_labels'] = re.findall(r'^\s*(\d+\.\d+[a-z]?)\s*$', rule['text'], re.M)
        rule['values'] = []
        for source in rule['sources']:
            for match in _NUMBER.finditer(source['text']):
                rule['values'].append(dict(metric='CGPA', comparator=match[1].lower(),
                                           value=float(match[3]), evidence=match[0], source=source,
                                           scope_resolved=False))
        # Only a complete, standalone statement can be treated as a hard value.
        body = '\n'.join(rule['text'].splitlines()[1:]).strip()
        standalone = bool(re.fullmatch(
            r'(?:The\s+)?(?:minimum|maximum)\s+CGPA\s+of\s+\d+(?:\.\d+)?\s+is\s+required\.',
            body, re.I))
        rule['needs_verification'] = not standalone
        rule['machine_checkable'] = standalone
        if standalone:
            rule['rule_type'] = 'explicit_numeric_requirement'
            for value in rule['values']:
                value['scope_resolved'] = True
        rule['validation'] = validate_rule(rule)
    policies = _elective_policies(pages)
    return dict(source_file=pages[0]['source_file'], pages_processed=len(pages), rules=rules,
                structured_policies=policies,
                representation='section_policies_with_numeric_observations',
                limitations=['Clause labels are preserved without assigning paragraph boundaries.',
                             'Numeric observations are not independently executable constraints.',
                             'Tables and cross-references require verification against the PDF.'])


def _elective_policies(pages):
    """Retain only complete, explicit elective rules needed for deterministic checks."""
    indexed = {page['page_number']: page for page in pages}

    def evidence(page_number, phrases):
        candidates = ([indexed[page_number]] if page_number in indexed else []) + [
            page for page in pages if page.get('page_number') != page_number]
        page = next((item for item in candidates if all(
            phrase.casefold() in item.get('text', '').casefold() for phrase in phrases)), None)
        if not page:
            return None
        text = page.get('text', '')
        return {'source_file': page['source_file'],
                'page_number': page['page_number'], 'text': text}

    structural = [item for item in (
        evidence(9, ['Open Electives', 'discipline requirement']),
        evidence(10, ['Normally any elective course', 'Dual degree students'])) if item]
    registration = [item for item in (
        evidence(15, ['outside his/her own discipline', 'prerequisite']),
        evidence(16, ["degree other than student's own degree(s)",
                      'third year first semester'])) if item]
    prerequisite = evidence(15, ['Before a student can register', 'prerequisite'])
    definitions = [
        dict(id='policy-open-elective-host-region',
             rule_type='open_elective_host_region',
             category='Open Electives', normalized_category='open_elective',
             outside_own_disciplines=True, outside_humanities_pool=True,
             discipline_and_humanities_precedence=True,
             specialized_courses_excluded=True, sources=structural + registration),
        dict(id='policy-cross-discipline-prior-preparation',
             rule_type='cross_discipline_prior_preparation',
             applies_to=['discipline_core', 'discipline_elective'],
             outside_students_own_degrees=True,
             required_through={'year': 3, 'semester': 1}, sources=registration),
        dict(id='policy-course-prerequisite-independent',
             rule_type='course_prerequisite_independent',
             prerequisite_required_for_registration=True,
             sources=[prerequisite] if prerequisite else []),
        dict(id='policy-dual-degree-del-to-opel',
             rule_type='dual_degree_del_to_opel',
             source_category='discipline_elective',
             target_category='open_elective',
             between_students_own_degrees=True, sources=structural),
    ]
    for policy in definitions:
        policy['needs_verification'] = False
        policy['validation'] = {
            'is_valid': bool(policy['sources']), 'issues': [] if policy['sources'] else [
                {'code': 'missing_source', 'severity': 'error'}],
        }
    return definitions


def build_academic_regulations(source_path, output_path):
    output = Path(output_path).resolve()
    source = Path(source_path).resolve()
    if output.suffix.lower() != '.json' or output == source or source.parent in output.parents:
        raise ValueError('Output must be JSON outside the source directory')
    result = extract_academic_regulations(extract_pdf_text(source))
    write_dataset(result, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/raw/Academic-Regulations-2023.pdf')
    parser.add_argument('--output', default='data/processed/academic_regulations.json')
    args = parser.parse_args()
    result = build_academic_regulations(args.source, args.output)
    print(f"Wrote {len(result['rules'])} section policies from {result['pages_processed']} pages")
