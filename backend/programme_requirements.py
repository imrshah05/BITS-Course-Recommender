"""Resolve source-backed academic rules for requested programme scopes."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from backend.student_profile import StudentProfile


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "data/processed/academic_rules.json"
COURSE_BEARING = {"required_course", "elective_option"}


@dataclass(frozen=True)
class RequirementIssue:
    code: str
    severity: str
    path: str
    message: str


class AcademicRuleCatalogue:
    """Reusable index of rules by their explicit Phase 2 programme scope."""

    def __init__(self, records):
        self.records = list(records)
        self._rules_by_scope = defaultdict(list)
        self._scope_names = defaultdict(set)
        self._institutional_scopes = []
        self.unscoped_course_rules = []

        for rule in self.records:
            scope = rule.get("scope") or {}
            programme = scope.get("programme")
            if isinstance(programme, str) and programme.strip():
                self._rules_by_scope[programme].append(rule)
                self._scope_names[_normalize_scope(programme)].add(programme)
                qualifier = _institutional_qualifier(programme)
                if qualifier and programme not in self._institutional_scopes:
                    self._institutional_scopes.append(programme)
            elif rule.get("rule_type") in COURSE_BEARING:
                self.unscoped_course_rules.append(rule)

            programme_code = scope.get("programme_code")
            if isinstance(programme_code, str) and programme_code.strip() and programme:
                self._scope_names[_normalize_scope(programme_code)].add(programme)

        self._institutional_scopes.sort(key=_normalize_scope)

    @classmethod
    def from_dict(cls, dataset):
        if not isinstance(dataset, dict) or not isinstance(dataset.get("records"), list):
            raise ValueError("Academic-rule dataset must contain a records list")
        return cls(dataset["records"])

    @classmethod
    def load(cls, path=DEFAULT_RULES_PATH):
        """Load and index the existing processed rules without preprocessing PDFs."""
        with Path(path).open(encoding="utf-8") as stream:
            return cls.from_dict(json.load(stream))

    def resolve_scope(self, requested_programme):
        key = _normalize_scope(requested_programme)
        matches = sorted(self._scope_names.get(key, ()), key=_normalize_scope)
        return matches

    def rules_for_scope(self, scope_name):
        return list(self._rules_by_scope.get(scope_name, ()))

    def applicable_institutional_scopes(self, requested_programme):
        requested = _normalize_scope(requested_programme)
        return [scope for scope in self._institutional_scopes
                if _institutional_qualifier(scope) in requested]


def resolve_programme_requirements(profile_or_history, catalogue):
    """Return applicable rules without evaluating student completion."""
    if isinstance(profile_or_history, StudentProfile):
        profile_or_history = profile_or_history.to_dict()
    if not isinstance(profile_or_history, dict):
        raise TypeError("Input must be a StudentProfile or normalized mapping")
    if not isinstance(catalogue, AcademicRuleCatalogue):
        catalogue = AcademicRuleCatalogue.from_dict(catalogue)

    requested = [
        ("primary", profile_or_history.get("programme")),
        ("second", profile_or_history.get("second_programme")),
    ]
    issues = []
    resolutions = []
    executable = []
    descriptive = []

    for role, programme in requested:
        if programme is None and role == "second":
            continue
        resolution = _resolve_one(role, programme, catalogue, issues)
        resolutions.append(resolution)
        executable.extend(resolution["executable_requirements"])
        descriptive.extend(resolution["descriptive_rules"])

    excluded = [_rule_summary(rule, None, "unscoped")
                for rule in catalogue.unscoped_course_rules]
    if excluded:
        counts = Counter(rule["rule_type"] for rule in excluded)
        _issue(issues, "unscoped_course_rules_excluded", "warning", "academic_rules",
               f"Excluded {len(excluded)} unscoped course-bearing rules: {dict(counts)}")

    validation = {
        "is_valid": not any(issue.severity == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
    }
    return {
        "requested_programmes": [
            {"role": role, "programme": programme}
            for role, programme in requested if programme is not None
        ],
        "programme_resolutions": resolutions,
        "applicable_executable_requirements": executable,
        "applicable_descriptive_rules": descriptive,
        "excluded_unresolved_rules": excluded,
        "incomplete_data": {
            "has_unscoped_course_rules": bool(excluded),
            "unscoped_course_rule_count": len(excluded),
            "unscoped_course_rules_by_type": dict(Counter(
                rule["rule_type"] for rule in excluded)),
        },
        "validation": validation,
    }


def _resolve_one(role, programme, catalogue, issues):
    path = "programme" if role == "primary" else "second_programme"
    matches = catalogue.resolve_scope(programme)
    if not matches:
        code = "programme_not_resolved" if role == "primary" else "second_programme_not_resolved"
        _issue(issues, code, "error", path,
               f"No academic-rule scope exactly matches {programme!r} after formatting normalization")
        return _resolution(role, programme, "unresolved", [], [], [])
    if len(matches) > 1:
        code = ("ambiguous_programme_resolution" if role == "primary"
                else "ambiguous_second_programme_resolution")
        _issue(issues, code, "error", path,
               f"{programme!r} matches multiple source scopes: {matches}")
        return _resolution(role, programme, "ambiguous", matches, [], [])

    direct_scope = matches[0]
    scopes = [(direct_scope, "programme")]
    for institutional in catalogue.applicable_institutional_scopes(programme):
        if institutional != direct_scope:
            scopes.append((institutional, "institutional"))

    executable = []
    descriptive = []
    verification_count = 0
    unresolved_alternatives = 0
    for scope_name, scope_kind in scopes:
        for rule in catalogue.rules_for_scope(scope_name):
            summary = _rule_summary(rule, role, scope_kind)
            if rule.get("classification") == "deterministic" and not rule.get("needs_verification"):
                executable.append(summary)
            else:
                descriptive.append(summary)
                verification_count += bool(rule.get("needs_verification"))
                unresolved_alternatives += rule.get("rule_type") == "unresolved_choice"

    if descriptive:
        _issue(issues, "descriptive_rules_excluded_from_calculation", "warning", path,
               f"Retained {len(descriptive)} descriptive rules outside executable calculations")
    if verification_count:
        _issue(issues, "rules_need_verification", "warning", path,
               f"{verification_count} applicable rules retain verification flags")
    if unresolved_alternatives:
        _issue(issues, "unresolved_alternative_structure", "warning", path,
               f"{unresolved_alternatives} applicable alternative structures are unresolved")

    return _resolution(role, programme, "resolved", [name for name, _ in scopes],
                       executable, descriptive)


def _resolution(role, requested, status, scopes, executable, descriptive):
    return {
        "role": role,
        "requested_programme": requested,
        "normalized_request": _normalize_scope(requested),
        "status": status,
        "matched_scopes": scopes,
        "executable_requirements": executable,
        "descriptive_rules": descriptive,
    }


def _rule_summary(rule, requested_role, scope_kind):
    return {
        "rule_id": rule.get("rule_id"),
        "requested_programme_role": requested_role,
        "scope_kind": scope_kind,
        "scope": deepcopy(rule.get("scope")),
        "rule_type": rule.get("rule_type"),
        "category": rule.get("category"),
        "normalized_category": rule.get("normalized_category"),
        "course_code": rule.get("course_code"),
        "course_title": rule.get("course_title"),
        "units": rule.get("units"),
        "required_count": rule.get("required_count"),
        "required_units": rule.get("required_units"),
        "min_count": rule.get("min_count"),
        "max_count": rule.get("max_count"),
        "min_units": rule.get("min_units"),
        "max_units": rule.get("max_units"),
        "alternatives": deepcopy(rule.get("alternatives")),
        "classification": rule.get("classification"),
        "needs_verification": bool(rule.get("needs_verification")),
        "source_document": rule.get("source_document"),
        "source_heading": rule.get("source_heading"),
        "sources": deepcopy(rule.get("sources") or []),
    }


def _normalize_scope(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).casefold()
    return re.sub(r"\s*([.,/&()\-–])\s*", r"\1", value).strip()


def _institutional_qualifier(scope_name):
    normalized = _normalize_scope(scope_name)
    if not normalized.startswith("pool of ") or " for " not in normalized:
        return None
    return normalized.rsplit(" for ", 1)[1]


def _issue(issues, code, severity, path, message):
    issue = RequirementIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
