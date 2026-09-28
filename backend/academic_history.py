"""Resolve normalized student academic history against the course catalogue."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from backend.student_profile import StudentProfile
from preprocessing.course_codes import normalize_course_code


DEFAULT_CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data/processed/courses.json"


@dataclass(frozen=True)
class HistoryIssue:
    code: str
    severity: str
    path: str
    message: str


class CourseCatalogue:
    """Reusable identity index over compact Phase 2 course records."""

    def __init__(self, records):
        self.records = list(records)
        self._index = defaultdict(list)
        self._unusable_codes = set()
        for record in self.records:
            metadata = record.get("metadata") or {}
            identities = metadata.get("course_codes") or []
            usable = record.get("candidate_status") == "usable"
            for identity in identities:
                code = normalize_course_code(identity)
                if not code:
                    continue
                if usable:
                    self._index[code].append(record)
                else:
                    self._unusable_codes.add(code)

    @classmethod
    def from_dict(cls, dataset):
        if not isinstance(dataset, dict) or not isinstance(dataset.get("records"), list):
            raise ValueError("Course catalogue must contain a records list")
        return cls(dataset["records"])

    @classmethod
    def load(cls, path=DEFAULT_CATALOGUE_PATH):
        """Load and index an existing processed catalogue without PDF work."""
        with Path(path).open(encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))

    @classmethod
    def load_source_backed(cls):
        """Load deterministic identities from both handouts and the Bulletin."""
        from backend.source_course_catalogue import source_backed_legacy_records
        return cls(source_backed_legacy_records())

    def matches(self, course_code):
        return list(self._index.get(course_code, ()))

    def has_unusable_identity(self, course_code):
        return course_code in self._unusable_codes


def resolve_academic_history(profile, catalogue):
    """Resolve completed and ongoing profile entries into JSON-compatible history."""
    if isinstance(profile, StudentProfile):
        profile = profile.to_dict()
    if not isinstance(profile, dict):
        raise TypeError("Profile must be a StudentProfile or normalized mapping")
    if not isinstance(catalogue, CourseCatalogue):
        catalogue = CourseCatalogue.from_dict(catalogue)

    issues = []
    completed = _resolve_list(profile.get("completed_courses", []), "completed_courses",
                              catalogue, issues)
    ongoing = _resolve_list(profile.get("ongoing_courses", []), "ongoing_courses",
                            catalogue, issues)
    validation = {
        "is_valid": not any(issue.severity == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
    }
    return {
        "programme": profile.get("programme"),
        "second_programme": profile.get("second_programme"),
        "current_academic_year": profile.get("current_academic_year"),
        "current_semester": profile.get("current_semester"),
        "completed_courses": completed,
        "ongoing_courses": ongoing,
        "profile_validation": deepcopy(profile.get("validation")),
        "validation": validation,
    }


def _resolve_list(entries, path, catalogue, issues):
    resolved = []
    for index, entry in enumerate(entries):
        if hasattr(entry, "to_dict"):
            entry = entry.to_dict()
        if not isinstance(entry, dict):
            raise ValueError(f"{path}[{index}] is not a normalized course entry")
        item_path = f"{path}[{index}]"
        code = entry.get("normalized_course_code")
        matches = catalogue.matches(code) if code else []
        summaries = [_catalogue_summary(record, code) for record in matches]
        resolution_status = "matched" if summaries else ("unmatched" if code else "unresolved")

        if code and not summaries:
            _issue(issues, "catalogue_match_missing", "warning", item_path,
                   f"No supplied handout record matches {code}")
            if catalogue.has_unusable_identity(code):
                _issue(issues, "unusable_catalogue_identity_ignored", "warning", item_path,
                       f"An unusable catalogue record exposing {code} was excluded")
        if len(summaries) > 1:
            _issue(issues, "multiple_catalogue_matches", "warning", item_path,
                   f"{code} matches {len(summaries)} catalogue records")
        if _has_conflicting_metadata(summaries):
            _issue(issues, "conflicting_catalogue_metadata", "warning", item_path,
                   f"Catalogue records for {code} disagree on title or units")
        for summary in summaries:
            if summary["needs_verification"] or not summary["is_valid"]:
                _issue(issues, "catalogue_record_needs_verification", "warning", item_path,
                       f"Catalogue record {summary['record_reference']} requires verification")
        if entry.get("units") is not None and _units_conflict(entry["units"], summaries):
            _issue(issues, "student_catalogue_units_conflict", "warning", item_path + ".units",
                   f"Student-supplied units for {code} differ from catalogue units")

        resolved.append({
            "course_code": entry.get("course_code"),
            "normalized_course_code": code,
            "status": entry.get("status"),
            "reported_status": entry.get("reported_status"),
            "grade": entry.get("grade"),
            "student_units": entry.get("units"),
            "resolution_status": resolution_status,
            "catalogue_matches": summaries,
        })
    return resolved


def _catalogue_summary(record, matched_code):
    metadata = record.get("metadata") or {}
    source = record.get("source") or {}
    validation = record.get("validation") or {}
    return {
        "matched_course_code": matched_code,
        "course_codes": list(metadata.get("course_codes") or []),
        "course_identity_type": metadata.get("course_identity_type"),
        "title": _field_value(metadata.get("course_title")),
        "catalogue_units": _field_value(metadata.get("units")),
        "record_reference": source.get("source_file"),
        "source": {
            "source_file": source.get("source_file"),
            "page_numbers": list(source.get("page_numbers") or []),
        },
        "candidate_status": record.get("candidate_status"),
        "is_valid": validation.get("is_valid") is True,
        "needs_verification": bool(validation.get("needs_verification")),
    }


def _field_value(field):
    return field.get("value") if isinstance(field, dict) else field


def _has_conflicting_metadata(summaries):
    for key in ("title", "catalogue_units"):
        values = {_stable_value(summary.get(key)) for summary in summaries
                  if summary.get(key) is not None}
        if len(values) > 1:
            return True
    return False


def _stable_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _units_conflict(student_units, summaries):
    student_value = _numeric_units(student_units)
    catalogue_values = {_numeric_units(summary.get("catalogue_units")) for summary in summaries}
    catalogue_values.discard(None)
    return student_value is not None and bool(catalogue_values) and any(
        value != student_value for value in catalogue_values)


def _numeric_units(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _issue(issues, code, severity, path, message):
    issue = HistoryIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
