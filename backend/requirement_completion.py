"""Evaluate completed coursework against resolved executable requirements."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import math

from preprocessing.course_codes import normalize_course_code


@dataclass(frozen=True)
class CompletionIssue:
    code: str
    severity: str
    path: str
    message: str


def evaluate_requirement_completion(academic_history, requirement_resolution):
    """Evaluate only safe executable rules selected by Task 3.3."""
    if not isinstance(academic_history, dict) or not isinstance(requirement_resolution, dict):
        raise TypeError("Academic history and requirement resolution must be mappings")

    issues = []
    _check_upstream_validation(academic_history, requirement_resolution, issues)
    completed = _course_index(academic_history.get("completed_courses", []), "completed_courses")
    ongoing = _course_index(academic_history.get("ongoing_courses", []), "ongoing_courses")
    history_issues = (academic_history.get("validation") or {}).get("issues", [])

    groups = {
        "satisfied": [],
        "remaining": [],
        "partially_satisfied": [],
        "unevaluable": [],
    }
    for index, rule in enumerate(requirement_resolution.get(
            "applicable_executable_requirements", [])):
        result = _evaluate_rule(rule, completed, ongoing, history_issues, issues, index)
        groups[result["completion_status"]].append(result)

    descriptive = requirement_resolution.get("applicable_descriptive_rules", [])
    unresolved = requirement_resolution.get("excluded_unresolved_rules", [])
    excluded = ([_excluded_rule(rule, "descriptive_or_non_executable")
                 for rule in descriptive] +
                [_excluded_rule(rule, "unscoped_or_unresolved")
                 for rule in unresolved])
    if descriptive:
        _issue(issues, "descriptive_rules_not_evaluated", "warning", "requirements",
               f"Excluded {len(descriptive)} descriptive rules from completion evaluation")
    inherited = deepcopy(requirement_resolution.get("incomplete_data") or {})
    inherited["descriptive_rule_count"] = len(descriptive)
    inherited["excluded_unresolved_rule_count"] = len(unresolved)
    inherited["has_incomplete_data"] = bool(
        inherited.get("has_unscoped_course_rules") or descriptive or unresolved or
        groups["unevaluable"])
    if inherited["has_incomplete_data"]:
        _issue(issues, "incomplete_requirement_data", "warning", "incomplete_data",
               "Some source-backed rules remain descriptive, unresolved, or unevaluable")

    validation = {
        "is_valid": not any(issue.severity == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
    }
    return {
        "satisfied_requirements": groups["satisfied"],
        "remaining_requirements": groups["remaining"],
        "partially_satisfied_requirements": groups["partially_satisfied"],
        "unevaluable_requirements": groups["unevaluable"],
        "excluded_rules": excluded,
        "incomplete_data": inherited,
        "validation": validation,
    }


def _evaluate_rule(rule, completed, ongoing, history_issues, issues, index):
    path = f"applicable_executable_requirements[{index}]"
    if rule.get("classification") != "deterministic":
        return _unevaluable(rule, "rule_not_deterministic", issues, path,
                            "Rule was not classified as deterministic")
    if rule.get("needs_verification"):
        return _unevaluable(rule, "source_rule_requires_verification", issues, path,
                            "Rule unexpectedly retains a verification requirement")
    if not (rule.get("scope") or {}).get("programme"):
        return _unevaluable(rule, "rule_lacks_programme_scope", issues, path,
                            "Rule has no usable programme scope")

    rule_type = rule.get("rule_type")
    if rule_type == "required_course":
        return _evaluate_required_course(rule, completed, ongoing, issues, path)
    if rule_type == "choice":
        return _evaluate_choice(rule, completed, ongoing, issues, path)
    if rule_type in ("category_total", "quantity"):
        return _evaluate_numeric(rule, completed, ongoing, history_issues, issues, path)
    return _unevaluable(rule, "unsupported_executable_rule_type", issues, path,
                        f"Executable rule type {rule_type!r} is not measurable")


def _evaluate_required_course(rule, completed, ongoing, issues, path):
    code = normalize_course_code(rule.get("course_code"))
    if not code:
        return _unevaluable(rule, "executable_rule_missing_fields", issues, path,
                            "Required-course rule lacks a valid course code")
    completed_matches = _matching_courses(completed, {code})
    ongoing_matches = _matching_courses(ongoing, {code})
    status = "satisfied" if completed_matches else "remaining"
    return _result(rule, status, completed_matches, ongoing_matches, {
        "course_count": {"required": 1, "completed": len(completed_matches),
                         "remaining": 0 if completed_matches else 1}
    })


def _evaluate_choice(rule, completed, ongoing, issues, path):
    alternatives = rule.get("alternatives")
    if not isinstance(alternatives, dict) or not isinstance(alternatives.get("options"), list):
        return _unevaluable(rule, "unresolved_alternative_group", issues, path,
                            "Alternative group is missing explicit options")
    options = [normalize_course_code(option) for option in alternatives["options"]]
    required = alternatives.get("select_count")
    if (not options or any(option is None for option in options) or
            type(required) is not int or required < 1 or required > len(set(options))):
        return _unevaluable(rule, "unresolved_alternative_group", issues, path,
                            "Alternative group has invalid options or selection count")
    eligible = set(options)
    completed_matches = _matching_courses(completed, eligible)
    ongoing_matches = _matching_courses(ongoing, eligible)
    completed_count = len(completed_matches)
    remaining = max(required - completed_count, 0)
    status = "satisfied" if remaining == 0 else (
        "partially_satisfied" if completed_count else "remaining")
    return _result(rule, status, completed_matches, ongoing_matches, {
        "course_count": {"required": required, "completed": completed_count,
                         "remaining": remaining}
    })


def _evaluate_numeric(rule, completed, ongoing, history_issues, issues, path):
    eligible_values = rule.get("eligible_course_codes")
    if not isinstance(eligible_values, list) or not eligible_values:
        code = ("missing_course_category_membership" if rule.get("normalized_category")
                else "requirement_cannot_be_measured")
        return _unevaluable(rule, code, issues, path,
                            "Numeric requirement lacks explicit eligible course identities")
    eligible = {normalize_course_code(value) for value in eligible_values}
    if None in eligible:
        return _unevaluable(rule, "executable_rule_missing_fields", issues, path,
                            "Numeric requirement contains malformed eligible course identities")

    required_count = _required_minimum(rule, "count")
    required_units = _required_minimum(rule, "units")
    if required_count is None and required_units is None:
        return _unevaluable(rule, "requirement_cannot_be_measured", issues, path,
                            "Numeric requirement has no supported minimum or required amount")

    completed_matches = _matching_courses(completed, eligible)
    ongoing_matches = _matching_courses(ongoing, eligible)
    measurements = {}
    if required_count is not None:
        amount = len(completed_matches)
        measurements["course_count"] = {
            "required": required_count,
            "completed": amount,
            "remaining": max(required_count - amount, 0),
        }
    if required_units is not None:
        units = []
        unit_problem = None
        for match in completed_matches:
            value, reason = _reliable_units(match, history_issues)
            if reason:
                unit_problem = reason
                break
            units.append(value)
        if unit_problem:
            return _unevaluable(rule, "missing_or_conflicting_units", issues, path,
                                "Completed eligible courses have missing or conflicting units",
                                completed_matches, ongoing_matches)
        amount = sum(units)
        measurements["units"] = {
            "required": required_units,
            "completed": amount,
            "remaining": max(required_units - amount, 0),
        }

    remaining_values = [measure["remaining"] for measure in measurements.values()]
    if all(value == 0 for value in remaining_values):
        status = "satisfied"
    elif completed_matches:
        status = "partially_satisfied"
    else:
        status = "remaining"
    return _result(rule, status, completed_matches, ongoing_matches, measurements)


def _required_minimum(rule, measure):
    required = rule.get("required_" + measure)
    minimum = rule.get("min_" + measure)
    value = required if _nonnegative_number(required) else minimum
    return value if _nonnegative_number(value) else None


def _course_index(entries, collection):
    index = {}
    for position, entry in enumerate(entries):
        code = normalize_course_code(entry.get("normalized_course_code")) if isinstance(entry, dict) else None
        if code and code not in index:
            preserved = deepcopy(entry)
            preserved["normalized_course_code"] = code
            preserved["history_path"] = f"{collection}[{position}]"
            index[code] = preserved
    return index


def _matching_courses(index, eligible):
    return [deepcopy(index[code]) for code in sorted(eligible) if code in index]


def _reliable_units(entry, history_issues):
    path = entry.get("history_path", "")
    conflict_codes = {"student_catalogue_units_conflict", "conflicting_catalogue_metadata"}
    if any(issue.get("code") in conflict_codes and issue.get("path", "").startswith(path)
           for issue in history_issues):
        return None, "conflicting"
    student = _numeric(entry.get("student_units"))
    if student is not None:
        return student, None
    catalogue = {_numeric(match.get("catalogue_units"))
                 for match in entry.get("catalogue_matches", [])}
    catalogue.discard(None)
    if len(catalogue) == 1:
        return catalogue.pop(), None
    return None, "conflicting" if len(catalogue) > 1 else "missing"


def _numeric(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if math.isfinite(parsed) and parsed >= 0:
            return int(parsed) if parsed.is_integer() else parsed
    return None


def _nonnegative_number(value):
    return _numeric(value) is not None


def _result(rule, status, completed, ongoing, measurements, reason=None):
    return {
        "rule_id": rule.get("rule_id"),
        "requested_programme_role": rule.get("requested_programme_role"),
        "scope": deepcopy(rule.get("scope")),
        "scope_kind": rule.get("scope_kind"),
        "rule_type": rule.get("rule_type"),
        "category": rule.get("category"),
        "normalized_category": rule.get("normalized_category"),
        "course_code": rule.get("course_code"),
        "course_title": rule.get("course_title"),
        "alternatives": deepcopy(rule.get("alternatives")),
        "completion_status": status,
        "completed_matching_courses": completed,
        "ongoing_matching_courses": ongoing,
        "measurements": measurements,
        "unevaluable_reason": reason,
        "classification": rule.get("classification"),
        "needs_verification": bool(rule.get("needs_verification")),
        "source_document": rule.get("source_document"),
        "source_heading": rule.get("source_heading"),
        "sources": deepcopy(rule.get("sources") or []),
    }


def _excluded_rule(rule, reason):
    preserved = deepcopy(rule)
    preserved["reason"] = reason
    return preserved


def _unevaluable(rule, code, issues, path, message, completed=None, ongoing=None):
    _issue(issues, code, "warning", path, message)
    return _result(rule, "unevaluable", completed or [], ongoing or [], {}, code)


def _check_upstream_validation(history, resolution, issues):
    profile_validation = history.get("profile_validation") or {}
    if profile_validation.get("is_valid") is False:
        _issue(issues, "invalid_student_profile", "error", "academic_history.profile_validation",
               "Student profile validation failed")
    if (history.get("validation") or {}).get("is_valid") is False:
        _issue(issues, "invalid_academic_history", "error", "academic_history.validation",
               "Academic history validation failed")
    if (resolution.get("validation") or {}).get("is_valid") is False:
        _issue(issues, "invalid_programme_resolution", "error", "requirement_resolution.validation",
               "Programme requirement resolution failed")


def _issue(issues, code, severity, path, message):
    issue = CompletionIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
