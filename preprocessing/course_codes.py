"""Source-backed course-code grammar and conservative identity parsing."""

import re


PREFIX = r"[A-Z]{2,8}\*?"
DESIGNATOR = r"(?:[FUGKCE]\d{3}[A-Z]?|Z[CG]\d{3}[A-Z]?)(?:-\d+)?"
CODE = rf"{PREFIX}\s+{DESIGNATOR}"
_FLEXIBLE_DESIGNATOR = r"(?:[FUGKCE]\s*\d{3}[A-Z]?|Z[CG]\s*\d{3}[A-Z]?)(?:-\d+)?"
_SINGLE = re.compile(rf"({PREFIX})\s*({_FLEXIBLE_DESIGNATOR})", re.I)
_PREFIX_GROUP = re.compile(
    rf"((?:{PREFIX}\s*/\s*)+{PREFIX})\s+"
    rf"({DESIGNATOR}(?:\s*[,/&]\s*(?:{DESIGNATOR}))*)",
    re.I,
)


def normalize_course_code(value):
    """Normalize one complete source-backed identifier, or return None."""
    if not isinstance(value, str):
        return None
    match = _SINGLE.fullmatch(value.strip())
    if not match:
        return None
    designator = re.sub(r"\s+", "", match[2]).upper()
    return f"{match[1].upper()} {designator}"


def parse_course_codes(value):
    """Return explicit identities and whether the complete expression was resolved.

    Prefix groups such as ``CS/SS G527`` and explicitly listed designators such
    as ``EEE/INSTR F366, F367`` are expanded without selecting an alternative.
    The original expression remains the authoritative evidence elsewhere.
    """
    if not isinstance(value, str) or not value.strip():
        return [], False
    text = " ".join(value.upper().split())
    single = normalize_course_code(text)
    if single:
        return [single], True

    codes = []
    consumed = [False] * len(text)

    def add(code):
        if code not in codes:
            codes.append(code)

    for match in _PREFIX_GROUP.finditer(text):
        prefixes = [item.strip() for item in match[1].split("/")]
        designators = [item.strip() for item in re.split(r"[,/&]", match[2])]
        for prefix in prefixes:
            for designator in designators:
                code = normalize_course_code(f"{prefix} {designator}")
                if code:
                    add(code)
        for index in range(match.start(), match.end()):
            consumed[index] = True

    for match in _SINGLE.finditer(text):
        if any(consumed[match.start():match.end()]):
            continue
        designator = re.sub(r"\s+", "", match[2]).upper()
        add(f"{match[1].upper()} {designator}")
        for index in range(match.start(), match.end()):
            consumed[index] = True

    residue = "".join(character for index, character in enumerate(text) if not consumed[index])
    residue = re.sub(r"\b(?:AND|OR)\b|[/,&()]", "", residue)
    resolved = bool(codes) and not residue.strip()
    return codes, resolved
