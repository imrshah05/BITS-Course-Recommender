"""Normalize structured regulations and Bulletin records without reading PDFs."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from preprocessing.course_codes import CODE, normalize_course_code
from preprocessing.dataset import write_dataset

SCOPE = ('programme', 'programme_code', 'degree', 'branch', 'campus', 'cohort', 'semester', 'year')
NUMERIC = ('units', 'required_count', 'required_units', 'min_units', 'max_units', 'min_count', 'max_count')
EVALUABLE = {'required_course', 'category_total', 'quantity', 'choice'}


def clean(value):
    return ' '.join(value.split()) if isinstance(value, str) else value


def number(value):
    if isinstance(value, str) and re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value.strip()):
        value = float(value)
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def issue(rule, code, severity='warning'):
    entry = dict(code=code, severity=severity)
    if entry not in rule['validation']['issues']:
        rule['validation']['issues'].append(entry)


def normalize_category(label, programme, kind):
    """Map only terminology whose meaning is explicit in the retained context."""
    original = clean(label)
    context = (programme or {}).get('context')
    key = original.casefold() if isinstance(original, str) else None
    normalized = None
    basis = None
    direct = {
        'discipline core': 'discipline_core',
        'discipline electives': 'discipline_elective',
        'discipline elective courses': 'discipline_elective',
        'humanities electives': 'humanities_elective',
        'open electives': 'open_elective',
    }
    if key in direct:
        normalized, basis = direct[key], 'explicit source category label'
    elif key == 'core courses' and context == 'discipline course list':
        normalized, basis = 'discipline_core', 'Bulletin discipline course-list context'
    elif key == 'core courses' and context == 'minor':
        normalized, basis = 'minor_core', 'Bulletin minor context'
    elif key == 'electives' and context == 'minor':
        normalized, basis = 'minor_elective', 'Bulletin minor context'
    elif kind == 'quantity' and context == 'minor':
        normalized, basis = 'minor_total', 'explicit Courses & Units requirement for named minor'
    return {'original': original, 'normalized': normalized, 'source_supported': normalized is not None,
            'basis': basis}


def validate_rule(rule):
    """Validate the common schema without guessing missing scope or quantities."""
    rule['validation'] = {'issues': []}
    if not rule.get('rule_type'):
        issue(rule, 'missing_rule_type', 'error')
    sources = rule.get('sources')
    if not isinstance(sources, list) or not sources or any(
        not isinstance(s, dict) or not s.get('source_file') or
        type(s.get('page_number')) is not int or s['page_number'] < 1 or
        not isinstance(s.get('text'), str) or not s['text'].strip() for s in sources):
        issue(rule, 'missing_source_traceability', 'error')
    code = rule.get('course_code')
    if code is not None and (not isinstance(code, str) or not re.fullmatch(CODE, code)):
        issue(rule, 'malformed_course_code', 'error')
    if rule['rule_type'] == 'required_course' and not code:
        issue(rule, 'missing_required_course', 'error')
    for key in NUMERIC:
        value = rule.get(key)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                                  or value < 0 or 'count' in key and value != int(value)):
            issue(rule, 'invalid_' + key, 'error')
    for field in ('units', 'count'):
        low, high = rule.get('min_' + field), rule.get('max_' + field)
        if type(low) in (int, float) and type(high) in (int, float) and low > high:
            issue(rule, 'contradictory_' + field + '_bounds', 'error')
    if rule['source_document'] == 'Bulletin' and not rule['scope'].get('programme'):
        issue(rule, 'missing_programme_scope')
    choice = rule.get('alternatives')
    if choice is not None:
        valid = isinstance(choice, dict) and isinstance(choice.get('options'), list) and len(choice['options']) >= 2
        if valid:
            count = choice.get('select_count')
            valid = type(count) is int and 0 < count <= len(choice['options']) and all(choice['options'])
        if not valid:
            issue(rule, 'malformed_alternatives', 'error')
    elif rule['rule_type'] in ('choice', 'unresolved_choice'):
        issue(rule, 'unresolved_alternatives')
    original = rule['original']
    previous = original.get('validation', {})
    old_issues = previous.get('issues', [])
    if previous.get('is_valid') is not None and previous['is_valid'] != (not any(i.get('severity') == 'error' for i in old_issues)):
        issue(rule, 'inconsistent_source_validation')
    waivers = set(rule.get('normalization', {}).get('waived_source_issues', []))
    for old in old_issues:
        if old.get('code') in waivers:
            continue
        issue(rule, 'source:' + old.get('code', 'unspecified'), old.get('severity', 'warning'))
    if original.get('machine_checkable') and original.get('needs_verification'):
        issue(rule, 'inconsistent_source_classification')
    if rule['rule_type'] in ('listed_course', 'elective_option', 'unresolved_section', 'unresolved_choice'):
        issue(rule, 'not_an_executable_requirement')
    if rule['rule_type'] in EVALUABLE:
        enough = bool(rule['scope'].get('programme'))
        if rule['rule_type'] in ('category_total', 'quantity'):
            enough = enough and any(rule.get(k) is not None for k in NUMERIC)
        if rule['rule_type'] == 'choice':
            enough = enough and choice is not None
        if not enough:
            issue(rule, 'insufficient_evaluable_fields')
    rule['needs_verification'] = bool(rule['needs_verification'] or rule['validation']['issues'])
    finalize(rule)
    return rule['validation']


def finalize(rule):
    rule['validation']['is_valid'] = not any(i['severity'] == 'error' for i in rule['validation']['issues'])
    rule['classification'] = ('deterministic' if rule['rule_type'] in EVALUABLE
                              and not rule['needs_verification'] and rule['validation']['is_valid']
                              else 'descriptive')


def normalize_record(record, document, pointer, programme=None):
    original = deepcopy(record)
    programme = programme or {}
    kind = record.get('rule_type') or record.get('kind')
    scope = {key: clean(record.get(key)) for key in SCOPE}
    scope['programme'] = clean(record.get('programme_name') or record.get('programme') or programme.get('name'))
    scope['programme_code'] = clean(record.get('programme_code', programme.get('programme_code')))
    category_normalization = normalize_category(record.get('category'), programme, kind)
    rule = dict(rule_id=document.lower().replace(' ', '-') + ':' + pointer,
                rule_type=kind, category=clean(record.get('category')),
                category_key=clean(record.get('category')).casefold() if isinstance(record.get('category'), str) else None,
                normalized_category=category_normalization['normalized'],
                category_normalization=category_normalization,
                scope=scope, course_code=clean(record.get('course_code')),
                course_title=clean(record.get('course_title')), source_document=document,
                sources=deepcopy(record.get('sources', [])), source_heading=deepcopy(record.get('section', programme.get('name'))),
                original_reference=pointer, original=original,
                programme_reference=record.get('programme_id'),
                alternatives=deepcopy(record.get('alternatives')),
                prerequisites=deepcopy(record.get('prerequisites')), restrictions=deepcopy(record.get('restrictions')),
                conditions=deepcopy(record.get('conditions')), exceptions=deepcopy(record.get('exceptions')),
                scope_and_exceptions=deepcopy(record.get('scope_and_exceptions')),
                observations=deepcopy(record.get('values', [])),
                needs_verification=bool(record.get('needs_verification', False)), normalization={})
    if isinstance(rule['course_code'], str):
        candidate = normalize_course_code(rule['course_code'])
        if candidate:
            rule['course_code'] = candidate
    for key in NUMERIC:
        rule[key] = number(record.get(key))
    rule['required_count'] = number(record.get('required_count', record.get('course_count')))
    if kind in ('category_total', 'quantity'):
        rule['required_units'] = number(record.get('required_units', record.get('units')))
        comparator = record.get('comparator')
        if comparator in ('min', 'max'):
            rule[comparator + '_units'] = rule['required_units']
            rule[comparator + '_count'] = rule['required_count']
            rule['required_units'] = rule['required_count'] = None
        rule['units'] = None
    if (kind == 'quantity' and programme.get('context') == 'minor'
            and scope['programme'] and record.get('comparator') in ('min', 'max')
            and sum(rule.get(key) is not None for key in ('min_units', 'max_units', 'min_count', 'max_count')) == 1
            and rule['sources']):
        rule['needs_verification'] = False
        rule['normalization']['explicit_numeric_scope'] = True
        rule['normalization']['waived_source_issues'] = ['manual_verification']
    if kind == 'choice' and rule.get('alternatives'):
        rule['needs_verification'] = False
        rule['normalization']['explicit_alternative_group'] = True
        rule['normalization']['waived_source_issues'] = ['incomplete_choice_structure', 'manual_verification']
    validate_rule(rule)
    return rule


def _explicit_choice(record, programme, used_groups):
    """Recover a choice only when retained programme evidence prints A/or/B."""
    if record.get('kind') != 'unresolved_choice' or not programme or record.get('sources', [{}])[0].get('text', '').strip().lower() != 'or':
        return record
    pairs = []
    pattern = re.compile(rf'({CODE})\s*\n\s*(?:\n\s*)?or\s*\n\s*({CODE})', re.I)
    for source in programme.get('sources', []):
        for match in pattern.finditer(source.get('text', '')):
            pair = tuple(normalize_course_code(value) for value in match.groups())
            if all(pair):
                pairs.append((pair, source))
    unique = {(pair, source['page_number']): source for pair, source in pairs}
    if len(unique) != 1:
        return record
    (pair, _), source = next(iter(unique.items()))
    group = (programme['id'], pair, source['page_number'])
    if group in used_groups:
        return record
    used_groups.add(group)
    adapted = deepcopy(record)
    adapted.update(kind='choice', alternatives={'select_count': 1, 'options': list(pair)},
                   sources=[deepcopy(source)], needs_verification=False)
    return adapted


def normalize_academic_rules(regulations, bulletin):
    """Retain each source occurrence, including unresolved Bulletin sections."""
    programmes = {p['id']: p for p in bulletin.get('programmes', [])}
    rules = []
    used_choice_groups = set()
    for i, record in enumerate(regulations.get('rules', [])):
        rules.append(normalize_record(record, 'Academic Regulations', f'/rules/{i}'))
    for i, record in enumerate(bulletin.get('requirements', [])):
        programme = programmes.get(record.get('programme_id'))
        adapted = _explicit_choice(record, programme, used_choice_groups)
        rules.append(normalize_record(adapted, 'Bulletin', f'/requirements/{i}', programme))
    for i, record in enumerate(bulletin.get('unresolved_sections', [])):
        adapted = dict(record, kind='unresolved_section', sources=[record.get('source', {})])
        rule = normalize_record(adapted, 'Bulletin', f'/unresolved_sections/{i}')
        rule['original'] = deepcopy(record)
        rules.append(rule)
    duplicates, conflicts = [], []
    seen, comparable = {}, defaultdict(list)
    for rule in rules:
        # Ignore record IDs but require equal normalized content and exact evidence.
        fingerprint = {k: rule[k] for k in ('source_document', 'rule_type', 'category_key', 'scope',
                       'course_code', 'course_title', 'sources', 'alternatives', 'conditions',
                       'exceptions', 'scope_and_exceptions', 'observations') + NUMERIC}
        key = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False)
        if key in seen:
            duplicates.append([seen[key]['rule_id'], rule['rule_id']])
            for entry in (seen[key], rule):
                issue(entry, 'duplicate_source_record'); entry['needs_verification'] = True
        else:
            seen[key] = rule
        # No fuzzy programme/category matching and no comparison of unscoped text.
        if rule['scope']['programme'] and rule['rule_type'] in EVALUABLE and not rule['needs_verification']:
            key = json.dumps([rule['rule_type'], rule['category_key'], rule['scope'], rule['course_code'],
                              rule['conditions'], rule['exceptions'], rule['scope_and_exceptions'], rule['alternatives']], sort_keys=True)
            comparable[key].append(rule)
    for group in comparable.values():
        for index, left in enumerate(group):
            for right in group[index+1:]:
                if left['source_document'] == right['source_document']:
                    continue
                shared = [k for k in NUMERIC if left[k] is not None and right[k] is not None]
                if shared and any(left[k] != right[k] for k in shared):
                    conflicts.append(dict(rule_ids=[left['rule_id'], right['rule_id']], fields=shared,
                                          reason='Different explicit values under identical recorded scope; neither source selected'))
                    for entry in (left, right):
                        issue(entry, 'possible_cross_source_conflict'); entry['needs_verification'] = True
    for rule in rules:
        finalize(rule)
    rules.sort(key=lambda r: r['rule_id'])
    summary = dict(total_records=len(rules), by_source=dict(Counter(r['source_document'] for r in rules)),
                   by_rule_type=dict(Counter(r['rule_type'] for r in rules)),
                   by_category=dict(Counter(r['category_key'] for r in rules if r['category_key'])),
                   deterministic=sum(r['classification'] == 'deterministic' for r in rules),
                   descriptive=sum(r['classification'] == 'descriptive' for r in rules),
                   needs_verification=sum(r['needs_verification'] for r in rules),
                   warnings=sum(i['severity'] == 'warning' for r in rules for i in r['validation']['issues']),
                   errors=sum(i['severity'] == 'error' for r in rules for i in r['validation']['issues']),
                   missing_traceability=sum(any(i['code'] == 'missing_source_traceability' for i in r['validation']['issues']) for r in rules),
                   duplicates=len(duplicates), possible_cross_source_conflicts=len(conflicts))
    return dict(schema_version=1, records=rules, validation_summary=summary,
                duplicate_records=duplicates, possible_cross_source_conflicts=conflicts,
                verification_rule_ids=[r['rule_id'] for r in rules if r['needs_verification']],
                programme_contexts=deepcopy(bulletin.get('programmes', [])),
                source_metadata={name: {k: deepcopy(v) for k, v in data.items() if k not in ('rules', 'requirements', 'programmes', 'unresolved_sections')}
                                 for name, data in [('Academic Regulations', regulations), ('Bulletin', bulletin)]})


def build_academic_rules(regulations_path, bulletin_path, output_path):
    inputs = [Path(regulations_path), Path(bulletin_path)]
    output = Path(output_path)
    if output.suffix != '.json' or output.resolve() in [p.resolve() for p in inputs] or output.name in ('courses.json', 'academic_regulations.json', 'programme_requirements.json'):
        raise ValueError('Output must be a separate academic-rules JSON file')
    payloads = [p.read_bytes() for p in inputs]
    result = normalize_academic_rules(*(json.loads(payload) for payload in payloads))
    result['source_inputs'] = [dict(filename=p.name, sha256=hashlib.sha256(payload).hexdigest()) for p, payload in zip(inputs, payloads)]
    write_dataset(result, output)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regulations', default='data/processed/academic_regulations.json')
    parser.add_argument('--bulletin', default='data/processed/programme_requirements.json')
    parser.add_argument('--output', default='data/processed/academic_rules.json')
    args = parser.parse_args()
    result = build_academic_rules(args.regulations, args.bulletin, args.output)
    print(json.dumps(result['validation_summary'], indent=2))
