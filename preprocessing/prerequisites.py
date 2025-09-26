"""Extract explicit prerequisite fields without interpreting free-form knowledge."""

from copy import deepcopy
import re

from preprocessing.course_codes import CODE, normalize_course_code


_HEADING = re.compile(
    r"(?im)^[ \t]*(?:(?P<number>\d+(?:\.\d+)*)[.)]?[ \t]+)?"
    r"(?P<heading>pre[- ]?requisites?)[ \t]*"
    r"(?P<colon>:[ \t]*(?P<inline>[^\n]*))?$"
)
_BOUNDARY = re.compile(
    r"(?im)^[ \t]*(?:\d+(?:\.\d+)*[.)]?[ \t]+[A-Z][^\n]{0,100}|"
    r"(?:corequisites?|course objectives?|scope|books?|text(?: and reference)? books?|"
    r"course plan|evaluation|attendance|make[ -]?up|notices?|chamber consultation hour|"
    r"slo mapping)\b[ \t]*:?)"
)
_NONE = re.compile(r"(?i)^(?:N\s*/?\s*A|NONE|NIL|NO[ \t]+PRE[- ]?REQUISITES?)\.?$")
_CONDITION = re.compile(
    r"(?i)\b(?:permission|consent|minimum|grade|standing|programme|program|concurrent|"
    r"corequisite|co-requisite|department|instructor)\b"
)
_CODE = re.compile(rf"(?<![A-Z0-9*]){CODE}(?![A-Z0-9])", re.I)


def extract_prerequisites(pages, target_course_codes=None):
    """Return one source-local prerequisite structure or ``None``.

    Unnumbered headings must use a colon. This excludes observed course-plan
    table cells and incidental prose while retaining the supplied field styles.
    """
    filenames = {page["source_file"] for page in pages}
    if len(filenames) > 1:
        raise ValueError("Expected pages from one handout")
    matches = []
    for page in sorted(pages, key=lambda item: item["page_number"]):
        text = page["text"]
        for heading in _HEADING.finditer(text):
            if heading.group("number") is None and heading.group("colon") is None:
                continue
            start = heading.end()
            boundary = _BOUNDARY.search(text, start)
            end = boundary.start() if boundary else len(text)
            inline = heading.group("inline") or ""
            following = text[start:end].strip()
            body = "\n".join(part for part in (inline.strip(), following) if part).strip()
            evidence = text[heading.start():end].strip()
            matches.append(_structure(
                body,
                heading.group("heading"),
                page["source_file"],
                page["page_number"],
                evidence,
                target_course_codes or [],
            ))
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    first = _semantic_signature(matches[0])
    if all(_semantic_signature(item) == first for item in matches[1:]):
        combined = deepcopy(matches[0])
        combined["sources"] = [source for item in matches for source in item["sources"]]
        return combined
    return {
        "kind": "unresolved",
        "operator": None,
        "course_codes": [],
        "text": "\n\n".join(item["text"] for item in matches),
        "heading": "Multiple prerequisite fields",
        "target_course_codes": list(target_course_codes or []),
        "needs_verification": True,
        "machine_evaluable": False,
        "diagnostics": ["conflicting_prerequisite_fields"],
        "sources": [source for item in matches for source in item["sources"]],
    }


def _structure(body, heading, source_file, page_number, evidence, target_codes):
    compact = " ".join(body.split()).strip()
    source = {"source_file": source_file, "page_number": page_number,
              "text": evidence}
    common = {
        "operator": None,
        "course_codes": [],
        "text": body,
        "heading": " ".join(heading.split()),
        "target_course_codes": list(target_codes),
        "needs_verification": False,
        "machine_evaluable": False,
        "diagnostics": [],
        "sources": [source],
    }
    if _NONE.fullmatch(compact):
        return {"kind": "none", **common}
    parsed = _course_structure(compact)
    if parsed:
        return {"kind": "courses", **common, **parsed, "machine_evaluable": True}
    diagnostics = []
    if not compact:
        diagnostics.append("empty_or_truncated_prerequisite_field")
    elif _CODE.search(compact):
        diagnostics.append("ambiguous_prerequisite_relationship")
    else:
        diagnostics.append("free_form_prerequisite_not_machine_evaluable")
    return {"kind": "unresolved", **common, "needs_verification": True,
            "diagnostics": diagnostics}


def _course_structure(text):
    if not text or _CONDITION.search(text):
        return None
    matches = list(_CODE.finditer(text))
    if not matches:
        return None
    codes = []
    pieces = []
    cursor = 0
    for match in matches:
        pieces.append(text[cursor:match.start()])
        code = normalize_course_code(match.group())
        if not code:
            return None
        if code not in codes:
            codes.append(code)
        cursor = match.end()
    pieces.append(text[cursor:])
    residue = " ".join(pieces)
    residue = re.sub(r"(?i)\b(?:courses?|and|or)\b|[,&/;:.()\-]", " ", residue)
    if residue.strip():
        return None
    between = text
    for match in reversed(matches):
        between = between[:match.start()] + " # " + between[match.end():]
    has_and = bool(re.search(r"(?i)\bAND\b|&", between))
    has_or = bool(re.search(r"(?i)\bOR\b", between))
    if has_and and has_or:
        return None
    if len(codes) > 1 and not (has_and or has_or):
        return None
    return {"operator": "any" if has_or else "all", "course_codes": sorted(codes)}


def _semantic_signature(item):
    return item["kind"], item.get("operator"), tuple(item.get("course_codes") or []), item["text"]
