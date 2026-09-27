"""Build deterministic source-backed semantic profiles for usable courses."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json

from backend.course_catalogue import CourseCatalogue
from backend.source_course_catalogue import SourceCourseCatalogue


SEMANTIC_FIELDS = (
    "title", "content", "topics", "instructors", "evaluation",
    "exam_information", "attendance_or_makeup", "units",
    "department_or_division",
)


@dataclass(frozen=True)
class SemanticProfileIssue:
    code: str
    severity: str
    path: str
    message: str


class CourseSemanticProfileBuilder:
    """Build semantic profiles from an existing Task 4.1 catalogue index."""

    def __init__(self, catalogue=None):
        if catalogue is None:
            catalogue = CourseCatalogue.load()
        elif not isinstance(catalogue, CourseCatalogue):
            catalogue = CourseCatalogue.from_dict(catalogue)
        self.catalogue = catalogue

    def build_one(self, course_code):
        """Build one profile for a usable catalogue identity, or return None."""
        identity = self.catalogue.get(course_code)
        return _profile(identity) if identity is not None else None

    def build_all(self):
        """Build one deterministic profile per usable catalogue identity."""
        profiles = [_profile(identity) for identity in self.catalogue.list_courses()]
        result = {
            "profiles": profiles,
            "excluded_catalogue_records": deepcopy(self.catalogue.excluded_records),
            "catalogue_summary": deepcopy(self.catalogue.validation.get("summary") or {}),
        }
        result["validation"] = validate_semantic_profiles(result)
        result["summary"] = _summary(result)
        return result


class UnifiedCourseSemanticProfileBuilder:
    """Build rich handout profiles and conservative Bulletin-only profiles."""

    def __init__(self, source_catalogue=None, handout_builder=None):
        self.source_catalogue = source_catalogue or SourceCourseCatalogue.load()
        self.handout_builder = handout_builder or CourseSemanticProfileBuilder()

    def build_one(self, course_code):
        rich = self.handout_builder.build_one(course_code)
        if rich is not None:
            return rich
        identity = self.source_catalogue.get(course_code)
        return _source_profile(identity) if identity is not None else None

    def build_all(self):
        profiles = [self.build_one(code) for code in self.source_catalogue.course_codes()]
        profiles = [profile for profile in profiles if profile is not None]
        result = {"profiles": profiles,
                  "catalogue_summary": deepcopy(self.source_catalogue.summary)}
        result["validation"] = validate_semantic_profiles(result)
        result["summary"] = _summary(result)
        return result


def validate_semantic_profiles(result):
    """Validate profile identity, evidence, availability, conflicts, and text."""
    issues = []
    seen = set()
    profiles = result.get("profiles", []) if isinstance(result, dict) else []
    if not isinstance(profiles, list):
        _issue(issues, "malformed_profile_collection", "error", "profiles",
               "Semantic profiles must be a list")
        profiles = []
    for index, profile in enumerate(profiles):
        path = f"profiles[{index}]"
        if not isinstance(profile, dict):
            _issue(issues, "malformed_semantic_profile", "error", path,
                   "Semantic profile must be a mapping")
            continue
        code = profile.get("course_code")
        if not isinstance(code, str) or not code.strip():
            _issue(issues, "semantic_identity_missing", "error", path,
                   "Semantic profile has no usable catalogue identity")
        elif code in seen:
            _issue(issues, "duplicate_semantic_profile_identity", "error", path,
                   f"Semantic profile identity {code} occurs more than once")
        else:
            seen.add(code)
        availability = profile.get("data_availability")
        if not isinstance(availability, dict):
            _issue(issues, "malformed_data_availability", "error", path,
                   "Data availability must be a mapping")
        else:
            actual = _availability(profile)
            for field in SEMANTIC_FIELDS:
                if availability.get(field) is not actual[field]:
                    _issue(issues, "data_availability_mismatch", "error",
                           f"{path}.data_availability.{field}",
                           f"Availability for {field} does not match profile data")
        if not profile.get("source_evidence", {}).get("course_identity"):
            _issue(issues, "semantic_source_traceability_missing", "error", path,
                   "Course identity lacks source traceability")
        if not isinstance(profile.get("searchable_text"), str):
            _issue(issues, "malformed_searchable_text", "error", path,
                   "Searchable text must be text")
        noise = ("eligibility_state", "recommendation_safe", "prerequisite_state",
                 "validation error", "source_file")
        searchable = profile.get("searchable_text") or ""
        if any(token in searchable.casefold() for token in noise):
            _issue(issues, "searchable_text_contains_policy_noise", "error", path,
                   "Searchable text contains unsupported policy or source noise")
        conflicts = profile.get("uncertainty", {}).get("conflicts") or {}
        if "course_title" in conflicts and profile.get("title", {}).get("display_value"):
            _issue(issues, "conflicting_title_collapsed", "error", path,
                   "Conflicting titles were collapsed into one display value")
        if ("department_division" in conflicts and
                profile.get("department_or_division", {}).get("display_value")):
            _issue(issues, "conflicting_department_collapsed", "error", path,
                   "Conflicting departments were collapsed into one display value")
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {"is_valid": not errors, "issues": errors + warnings,
            "error_count": len(errors), "warning_count": len(warnings),
            "duplicate_profile_identity_count": sum(
                issue.code == "duplicate_semantic_profile_identity" for issue in issues)}


def _profile(identity):
    records = identity["source_records"]
    conflicts = deepcopy(identity.get("metadata_conflicts") or {})
    title = _metadata_field(records, "course_title", "course_title" in conflicts)
    department = _metadata_field(
        records, "department_division", "department_division" in conflicts)
    units = _metadata_field(records, "units", "units" in conflicts)
    content = _merge_items(records, "syllabus")
    topics = [deepcopy(item) for item in content]
    instructors = _instructors(records)
    evaluation = _merge_items(records, "evaluation")
    exams = {
        "midsemester": _exam_claims(records, "midsemester"),
        "comprehensive": _exam_claims(records, "comprehensive"),
    }
    policies = {"attendance": _merge_items(records, "attendance"),
                "makeup": _merge_items(records, "makeup")}
    evidence = _source_evidence(records, title, department, units, content,
                                instructors, evaluation, exams, policies)
    profile = {
        "course_code": identity["normalized_course_code"],
        "catalogue_identity": {
            "source_record_count": identity["source_record_count"],
            "has_multiple_source_records": identity["has_multiple_source_records"],
            "has_shared_source_record": identity["has_shared_source_record"],
        },
        "title": title,
        "department_or_division": department,
        "content": content,
        "topics": topics,
        "instructors": instructors,
        "evaluation": evaluation,
        "exam_information": exams,
        "attendance_or_makeup": policies,
        "units": units,
        "searchable_text": _searchable_text(
            identity["normalized_course_code"], title, content, topics),
        "source_evidence": evidence,
        "uncertainty": {
            "has_multiple_source_records": identity["has_multiple_source_records"],
            "has_shared_source_record": identity["has_shared_source_record"],
            "needs_verification": identity["needs_verification"],
            "conflicts": conflicts,
            "missing_fields": [],
        },
        "validation": {"is_valid": True, "issues": [],
                       "error_count": 0, "warning_count": 0},
    }
    profile["data_availability"] = _availability(profile)
    profile["uncertainty"]["missing_fields"] = [
        field for field, available in profile["data_availability"].items()
        if not available]
    return profile


def _source_profile(identity):
    """Represent only metadata explicitly retained by the unified catalogue."""
    metadata = identity.get("metadata") or {}
    title = _source_metadata_field(metadata.get("course_title"))
    department = _source_metadata_field(metadata.get("department_division"))
    units = _source_metadata_field(metadata.get("units"))
    identity_sources = _unified_identity_sources(identity)
    uncertainty = deepcopy(identity.get("uncertainty") or {})
    profile = {
        "course_code": identity["course_code"],
        "catalogue_identity": {
            "source_record_count": sum(len(value) for value in
                                       (identity.get("provenance") or {}).values()),
            "has_multiple_source_records": False,
            "has_shared_source_record": False,
            "handout_available": bool((identity.get("availability") or {}).get("handout")),
            "bulletin_available": bool((identity.get("availability") or {}).get("bulletin")),
        },
        "title": title,
        "department_or_division": department,
        "content": [], "topics": [], "instructors": [], "evaluation": [],
        "exam_information": {"midsemester": [], "comprehensive": []},
        "attendance_or_makeup": {"attendance": [], "makeup": []},
        "units": units,
        "searchable_text": _searchable_text(identity["course_code"], title, [], []),
        "source_evidence": {
            "course_identity": identity_sources,
            "title": deepcopy(title["sources"]),
            "department_or_division": deepcopy(department["sources"]),
            "units": deepcopy(units["sources"]),
            "content": [], "topics": [], "instructors": [], "evaluation": [],
            "exam_information": [], "attendance_or_makeup": [],
        },
        "uncertainty": {
            "has_multiple_source_records": False,
            "has_shared_source_record": False,
            "needs_verification": bool(
                (identity.get("identity") or {}).get("needs_verification") or
                uncertainty.get("metadata_conflicts")),
            "conflicts": {name: deepcopy((metadata.get(name) or {}).get("values") or [])
                          for name in uncertainty.get("metadata_conflicts") or []},
            "missing_fields": [],
            "bulletin_relationships_need_verification": bool(
                uncertainty.get("bulletin_relationships_need_verification")),
        },
        "validation": {"is_valid": True, "issues": [],
                       "error_count": 0, "warning_count": 0},
    }
    profile["data_availability"] = _availability(profile)
    profile["uncertainty"]["missing_fields"] = [
        field for field, available in profile["data_availability"].items()
        if not available]
    return profile


def _source_metadata_field(field):
    field = field if isinstance(field, dict) else {}
    sources = []
    for provenance in field.get("provenance") or []:
        sources = _merge_sources(sources, provenance.get("sources") or [])
    return {"display_value": deepcopy(field.get("value")),
            "values": deepcopy(field.get("values") or []),
            "sources": sources}


def _unified_identity_sources(identity):
    sources = []
    provenance = identity.get("provenance") or {}
    for record in provenance.get("bulletin_records") or []:
        sources = _merge_sources(sources, record.get("sources") or [])
    for record in provenance.get("handout_records") or []:
        source_file = record.get("source_file")
        for page in record.get("page_numbers") or []:
            sources = _merge_sources(sources, [{
                "source_file": source_file, "page_number": page,
                "text": identity.get("course_code"),
            }])
    return sources


def _metadata_field(records, field, conflicting):
    values = []
    evidence = []
    for record in records:
        raw = record.get(field)
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None or value == "":
            continue
        if value not in values:
            values.append(deepcopy(value))
        evidence = _merge_sources(evidence, raw.get("sources") if isinstance(raw, dict) else [])
    return {"display_value": values[0] if len(values) == 1 and not conflicting else None,
            "values": values, "sources": evidence}


def _merge_items(records, field):
    merged = []
    signatures = {}
    for record in records:
        raw_items = record.get(field) or []
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item = deepcopy(raw)
            sources = item.pop("sources", [])
            signature = _stable(item)
            if signature in signatures:
                target = merged[signatures[signature]]
                target["sources"] = _merge_sources(target.get("sources"), sources)
            else:
                item["sources"] = _merge_sources([], sources)
                signatures[signature] = len(merged)
                merged.append(item)
    return merged


def _instructors(records):
    people = []
    indexes = {}
    for record in records:
        for group in record.get("instructors") or []:
            if not isinstance(group, dict):
                continue
            for name in group.get("names") or []:
                if not isinstance(name, str) or not name.strip():
                    continue
                cleaned = " ".join(name.split())
                key = cleaned.casefold()
                if key not in indexes:
                    indexes[key] = len(people)
                    people.append({"name": cleaned, "labels": [], "sources": []})
                person = people[indexes[key]]
                label = group.get("label")
                if isinstance(label, str) and label and label not in person["labels"]:
                    person["labels"].append(label)
                person["sources"] = _merge_sources(person["sources"], group.get("sources"))
    return people


def _exam_claims(records, exam_type):
    claims = []
    signatures = set()
    for record in records:
        claim = (record.get("exams") or {}).get(exam_type)
        if not isinstance(claim, dict):
            continue
        signature = _stable(claim)
        if signature not in signatures:
            signatures.add(signature)
            claims.append(deepcopy(claim))
    return claims


def _searchable_text(code, title, content, topics):
    values = [code]
    if title.get("display_value"):
        values.append(str(title["display_value"]))
    for item in content + topics:
        for key in ("heading", "text"):
            value = item.get(key)
            if isinstance(value, str):
                cleaned = " ".join(value.split())
                if cleaned and cleaned not in values:
                    values.append(cleaned)
    return "\n".join(values)


def _source_evidence(records, title, department, units, content, instructors,
                     evaluation, exams, policies):
    identity_sources = []
    for record in records:
        source = record.get("source") or {}
        identity_sources = _merge_sources(identity_sources, [{
            "source_file": source.get("source_file"),
            "page_numbers": deepcopy(source.get("page_numbers") or []),
            "record_id": record.get("record_id"),
        }])
    return {
        "course_identity": identity_sources,
        "title": deepcopy(title["sources"]),
        "department_or_division": deepcopy(department["sources"]),
        "units": deepcopy(units["sources"]),
        "content": _item_sources(content),
        "topics": _item_sources(content),
        "instructors": _item_sources(instructors),
        "evaluation": _item_sources(evaluation),
        "exam_information": _nested_sources(exams),
        "attendance_or_makeup": _nested_sources(policies),
    }


def _availability(profile):
    return {
        "title": bool(profile.get("title", {}).get("values")),
        "content": bool(profile.get("content")),
        "topics": bool(profile.get("topics")),
        "instructors": bool(profile.get("instructors")),
        "evaluation": bool(profile.get("evaluation")),
        "exam_information": any(profile.get("exam_information", {}).values()),
        "attendance_or_makeup": any(profile.get("attendance_or_makeup", {}).values()),
        "units": bool(profile.get("units", {}).get("values")),
        "department_or_division": bool(
            profile.get("department_or_division", {}).get("values")),
    }


def _item_sources(items):
    sources = []
    for item in items:
        sources = _merge_sources(sources, item.get("sources"))
    return sources


def _nested_sources(value):
    sources = []
    if isinstance(value, dict):
        sources = _merge_sources(sources, value.get("sources"))
        for item in value.values():
            sources = _merge_sources(sources, _nested_sources(item))
    elif isinstance(value, list):
        for item in value:
            sources = _merge_sources(sources, _nested_sources(item))
    return sources


def _merge_sources(existing, new):
    result = list(existing or [])
    for source in new or []:
        if isinstance(source, dict) and source not in result:
            result.append(deepcopy(source))
    return result


def _summary(result):
    profiles = result["profiles"]
    availability = {field: sum(profile["data_availability"][field]
                               for profile in profiles)
                    for field in SEMANTIC_FIELDS}
    return {
        "usable_catalogue_identity_count": result["catalogue_summary"].get(
            "candidate_identity_count", len(profiles)),
        "semantic_profile_count": len(profiles),
        "excluded_catalogue_record_count": len(result["excluded_catalogue_records"]),
        "duplicate_profile_identity_count": result["validation"].get(
            "duplicate_profile_identity_count", 0),
        "availability_counts": availability,
        "multiple_source_profile_count": sum(
            profile["uncertainty"]["has_multiple_source_records"] for profile in profiles),
        "conflict_profile_count": sum(
            bool(profile["uncertainty"]["conflicts"]) for profile in profiles),
        "uncertain_profile_count": sum(
            profile["uncertainty"]["needs_verification"] or
            profile["uncertainty"]["has_multiple_source_records"] or
            bool(profile["uncertainty"]["conflicts"])
            for profile in profiles),
        "searchable_text_count": sum(
            bool(profile["searchable_text"].strip()) for profile in profiles),
        "profiles_without_descriptive_text_count": sum(
            not profile["data_availability"]["title"] and
            not profile["data_availability"]["content"] for profile in profiles),
    }


def _stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _issue(issues, code, severity, path, message):
    issue = SemanticProfileIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
