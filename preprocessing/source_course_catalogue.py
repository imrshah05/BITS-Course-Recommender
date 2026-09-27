"""Merge source-backed course identities without inventing missing metadata."""

from copy import deepcopy
import json
from pathlib import Path

from preprocessing.course_codes import normalize_course_code


DEFAULT_COURSES_PATH = Path("data/processed/courses.json")
DEFAULT_BULLETIN_PATH = Path("data/processed/programme_requirements.json")
DEFAULT_OUTPUT_PATH = Path("data/processed/course_catalogue.json")

IDENTITY_UNCERTAINTY_CODES = {
    "ambiguous_course_identity", "incomplete_choice_structure",
    "malformed_course_code", "malformed_course_identity",
    "unresolved_course_identity",
}


def build_source_course_catalogue(courses_path=DEFAULT_COURSES_PATH,
                                  bulletin_path=DEFAULT_BULLETIN_PATH,
                                  output_path=DEFAULT_OUTPUT_PATH):
    """Build and write the deterministic union of handout and Bulletin identities."""
    courses = _load(courses_path)
    bulletin = _load(bulletin_path)
    result = merge_source_course_catalogue(courses, bulletin)
    _write_dataset(result, output_path)
    return result


def merge_source_course_catalogue(courses, bulletin):
    """Merge structured sources while retaining availability and field provenance."""
    if not isinstance(courses, dict) or not isinstance(courses.get("records"), list):
        raise ValueError("courses must contain a records list")
    if not isinstance(bulletin, dict) or not isinstance(
            bulletin.get("requirements"), list):
        raise ValueError("Bulletin data must contain a requirements list")

    indexed = {}
    for index, record in enumerate(courses["records"]):
        if not isinstance(record, dict) or record.get("candidate_status") != "usable":
            continue
        metadata = record.get("metadata") or {}
        codes = metadata.get("course_codes")
        if not isinstance(codes, list):
            codes = []
        for raw_code in codes:
            code = normalize_course_code(raw_code)
            if code is None:
                continue
            entry = indexed.setdefault(code, _entry(code))
            entry["availability"]["handout"] = True
            contribution = {
                "source_record_index": index,
                "source_file": (record.get("source") or {}).get("source_file"),
                "page_numbers": deepcopy((record.get("source") or {}).get("page_numbers") or []),
                "title": deepcopy(metadata.get("course_title")),
                "department_division": deepcopy(metadata.get("department_division")),
                "units": deepcopy(metadata.get("units")),
                "needs_verification": bool(
                    (record.get("validation") or {}).get("needs_verification")),
            }
            entry["provenance"]["handout_records"].append(contribution)
            _add_field(entry, "course_title", metadata.get("course_title"),
                       "handout", contribution)
            _add_field(entry, "department_division", metadata.get("department_division"),
                       "handout", contribution)
            _add_field(entry, "units", metadata.get("units"), "handout", contribution)

    for index, record in enumerate(bulletin["requirements"]):
        if not isinstance(record, dict):
            continue
        code = normalize_course_code(record.get("course_code"))
        if code is None:
            continue
        entry = indexed.setdefault(code, _entry(code))
        issues = (record.get("validation") or {}).get("issues") or []
        issue_codes = sorted({item.get("code") for item in issues
                              if isinstance(item, dict) and item.get("code")})
        identity_uncertain = any(
            issue in IDENTITY_UNCERTAINTY_CODES or
            any(token in issue for token in ("malformed_course", "course_identity"))
            for issue in issue_codes)
        sources = deepcopy(record.get("sources") or [])
        contribution = {
            "requirement_id": record.get("id"),
            "kind": record.get("kind"),
            "course_title": record.get("course_title"),
            "units": record.get("units"),
            "category": record.get("category"),
            "programme_name": record.get("programme_name"),
            "year": record.get("year"),
            "semester": record.get("semester"),
            "needs_verification": bool(record.get("needs_verification")),
            "identity_needs_verification": identity_uncertain,
            "verification_reasons": issue_codes,
            "sources": sources,
        }
        entry["availability"]["bulletin"] = True
        entry["provenance"]["bulletin_records"].append(contribution)
        if identity_uncertain:
            entry["identity"]["needs_verification"] = True
            entry["identity"]["verification_reasons"].extend(issue_codes)
        if record.get("needs_verification"):
            entry["uncertainty"]["bulletin_relationships_need_verification"] = True
        _add_field(entry, "course_title", record.get("course_title"),
                   "bulletin", contribution)
        _add_field(entry, "units", record.get("units"), "bulletin", contribution)

    records = []
    for code in sorted(indexed):
        entry = indexed[code]
        for field in entry["metadata"].values():
            field["values"] = sorted(field["values"], key=lambda value: str(value).casefold())
            field["value"] = field["values"][0] if len(field["values"]) == 1 else None
            field["needs_verification"] = len(field["values"]) > 1
        entry["identity"]["verification_reasons"] = sorted(set(
            entry["identity"]["verification_reasons"]))
        entry["uncertainty"]["metadata_conflicts"] = sorted(
            name for name, field in entry["metadata"].items()
            if field["needs_verification"])
        entry["uncertainty"]["needs_verification"] = bool(
            entry["identity"]["needs_verification"] or
            entry["uncertainty"]["metadata_conflicts"] or
            entry["uncertainty"]["bulletin_relationships_need_verification"])
        records.append(entry)

    handout = sum(item["availability"]["handout"] for item in records)
    bulletin_count = sum(item["availability"]["bulletin"] for item in records)
    overlap = sum(all(item["availability"].values()) for item in records)
    deterministic = sum(not item["identity"]["needs_verification"] for item in records)
    return {
        "schema_version": 1,
        "records": records,
        "summary": {
            "distinct_course_count": len(records),
            "handout_backed_count": handout,
            "bulletin_backed_count": bulletin_count,
            "overlap_count": overlap,
            "handout_only_count": handout - overlap,
            "bulletin_only_count": bulletin_count - overlap,
            "deterministic_identity_count": deterministic,
            "identity_verification_required_count": len(records) - deterministic,
        },
    }


def _entry(code):
    return {
        "course_code": code,
        "identity": {"value": code, "needs_verification": False,
                     "verification_reasons": []},
        "availability": {"handout": False, "bulletin": False},
        "metadata": {
            "course_title": _field(),
            "department_division": _field(),
            "units": _field(),
        },
        "provenance": {"handout_records": [], "bulletin_records": []},
        "uncertainty": {
            "needs_verification": False,
            "metadata_conflicts": [],
            "bulletin_relationships_need_verification": False,
        },
    }


def _field():
    return {"value": None, "values": [], "needs_verification": False,
            "provenance": []}


def _add_field(entry, name, raw, source_kind, contribution):
    value = raw.get("value") if isinstance(raw, dict) else raw
    if value is None or value == "" or isinstance(value, (dict, list)):
        return
    field = entry["metadata"][name]
    if value not in field["values"]:
        field["values"].append(deepcopy(value))
    reference = {
        "source_kind": source_kind,
        "source_file": contribution.get("source_file"),
        "requirement_id": contribution.get("requirement_id"),
        "sources": deepcopy(contribution.get("sources") or []),
    }
    if reference not in field["provenance"]:
        field["provenance"].append(reference)


def _load(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_dataset(dataset, path):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dataset, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
