"""Conservative completed-course suggestions from structured programme records."""

from copy import deepcopy
import json
from pathlib import Path
import re

from backend.source_course_catalogue import SourceCourseCatalogue


DEFAULT_REQUIREMENTS_PATH = (
    Path(__file__).resolve().parents[1] / "data/processed/programme_requirements.json")


class CourseSuggestionService:
    """Suggest only explicitly semester-scoped required courses from source data."""

    def __init__(self, requirements=None, course_catalogue=None,
                 requirements_path=DEFAULT_REQUIREMENTS_PATH):
        if requirements is None:
            with Path(requirements_path).open(encoding="utf-8") as stream:
                requirements = json.load(stream)
        if not isinstance(requirements, dict):
            raise ValueError("Programme requirements must be a mapping")
        self.programmes = list(requirements.get("programmes") or [])
        self.requirements = list(requirements.get("requirements") or [])
        self.course_catalogue = course_catalogue or SourceCourseCatalogue.load()
        self._programme_names = {
            _normalize(item.get("name")): item.get("name")
            for item in self.programmes if isinstance(item, dict)
            and isinstance(item.get("name"), str) and item.get("name").strip()
        }
        self._composite_names = [
            item["name"] for item in self.programmes
            if isinstance(item, dict)
            and item.get("context") == "composite dual-degree chart"
            and isinstance(item.get("name"), str) and item["name"].strip()
        ]

    def suggest(self, programmes, current_academic_year, current_semester):
        requested = _validate_request(
            programmes, current_academic_year, current_semester)
        resolved = []
        unresolved = []
        for value in requested["programmes"]:
            name = self._programme_names.get(_normalize(value))
            if name is None:
                unresolved.append(value)
            elif name not in resolved:
                resolved.append(name)

        resolution_mode = "individual_programmes"
        if len(resolved) == 2 and not unresolved:
            composite = self._composite_for(resolved)
            if composite is not None:
                resolved = [composite]
                resolution_mode = "composite_dual_degree_chart"

        inherited_periods = {}
        reference_limitations = []
        if resolution_mode == "composite_dual_degree_chart":
            inherited_periods, reference_limitations = self._curriculum_references(
                resolved[0])

        current_position = _position(
            requested["current_academic_year"], requested["current_semester"])
        suggestions = {}
        explicit_course_records = 0
        unscoped_course_records = 0
        for record in self.requirements:
            if not isinstance(record, dict) or record.get("kind") != "required_course":
                continue
            scope = record.get("programme_name")
            inherited_scope = inherited_periods.get(scope, set())
            if scope not in resolved and not inherited_scope:
                continue
            code = record.get("course_code")
            if not isinstance(code, str) or not self.course_catalogue.contains(code):
                continue
            year, semester = record.get("year"), record.get("semester")
            if record.get("needs_verification"):
                continue
            if not _valid_period(year, semester):
                unscoped_course_records += 1
                continue
            if scope not in resolved and (year, semester) not in inherited_scope:
                continue
            explicit_course_records += 1
            if _position(year, semester) >= current_position:
                continue
            item = suggestions.setdefault(code, {
                "course_code": code,
                "course_title": _course_title(self.course_catalogue.get(code)),
                "expected_year": year,
                "expected_semester": semester,
                "programme_scopes": [],
                "sources": [],
                "suggestion_state": "awaiting_student_confirmation",
            })
            display_scope = resolved[0] if scope not in resolved else scope
            if display_scope not in item["programme_scopes"]:
                item["programme_scopes"].append(display_scope)
            if scope not in resolved:
                item["curriculum_source_programme"] = scope
            for source in record.get("sources") or []:
                if isinstance(source, dict) and source not in item["sources"]:
                    item["sources"].append(deepcopy(source))

        ordered = [suggestions[code] for code in sorted(suggestions)]
        missing_period_data = bool(resolved and explicit_course_records == 0)
        limitations = list(reference_limitations)
        if missing_period_data:
            limitations.append({
                "code": "semester_assignment_unavailable",
                "message": (
                    "The processed programme data does not retain reliable year and "
                    "semester assignments for required courses, so completed courses "
                    "cannot be suggested safely."),
                "affected_required_course_records": unscoped_course_records,
            })
        if unresolved:
            limitations.append({
                "code": "programme_not_available_in_requirement_charts",
                "message": "No processed programme chart matches: " + ", ".join(unresolved),
            })
        return {
            "suggestions": ordered,
            "requested_programmes": requested["programmes"],
            "resolved_programmes": resolved,
            "resolution_mode": resolution_mode,
            "unresolved_programmes": unresolved,
            "limitations": limitations,
            "summary": {
                "suggestion_count": len(ordered),
                "resolved_programme_count": len(resolved),
                "unresolved_programme_count": len(unresolved),
                "explicit_period_required_course_count": explicit_course_records,
                "required_course_records_missing_period": unscoped_course_records,
            },
        }

    def _curriculum_references(self, composite_name):
        inherited = {}
        limitations = []
        references = [record for record in self.requirements
                      if isinstance(record, dict)
                      and record.get("kind") == "curriculum_reference"
                      and record.get("programme_name") == composite_name]
        for record in references:
            target = record.get("referenced_programme_name")
            periods = record.get("covered_periods")
            valid = (record.get("needs_verification") is not True
                     and isinstance(target, str) and target in self._programme_names.values()
                     and isinstance(periods, list) and periods)
            parsed = set()
            if valid:
                for period in periods:
                    if not isinstance(period, dict) or not _valid_period(
                            period.get("year"), period.get("semester")):
                        valid = False
                        break
                    parsed.add((period["year"], period["semester"]))
            if valid:
                inherited.setdefault(target, set()).update(parsed)
            else:
                limitations.append({
                    "code": "ambiguous_curriculum_reference",
                    "message": (
                        "The composite curriculum contains a source reference that "
                        "could not be resolved uniquely, so its courses were not suggested."),
                    "programme": composite_name,
                    "sources": deepcopy(record.get("sources") or []),
                })
        return inherited, limitations


    def _composite_for(self, resolved):
        left, right = map(_normalize, resolved)
        expected = {left + "with" + right, right + "with" + left}
        matches = [name for name in self._composite_names
                   if _normalize(name) in expected]
        return matches[0] if len(matches) == 1 else None


def _validate_request(programmes, year, semester):
    if not isinstance(programmes, list) or not programmes or any(
            not isinstance(item, str) or not item.strip() for item in programmes):
        raise ValueError("programmes must be a non-empty list of programme names")
    if type(year) is not int or year < 1:
        raise ValueError("current_academic_year must be a positive integer")
    if type(semester) is not int or semester not in (1, 2):
        raise ValueError("current_semester must be 1 or 2")
    return {"programmes": [item.strip() for item in programmes],
            "current_academic_year": year, "current_semester": semester}


def _valid_period(year, semester):
    return type(year) is int and year >= 1 and type(semester) is int and semester in (1, 2)


def _position(year, semester):
    return (year - 1) * 2 + semester


def _normalize(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _course_title(course):
    if not course:
        return None
    metadata = course.get("metadata") or {}
    field = metadata.get("course_title") or {}
    if isinstance(field, dict) and isinstance(field.get("value"), str):
        return field["value"]
    titles = []
    for record in course.get("source_records") or []:
        value = record.get("course_title") if isinstance(record, dict) else None
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and value.strip() and value.strip() not in titles:
            titles.append(value.strip())
    return titles[0] if len(titles) == 1 else None
