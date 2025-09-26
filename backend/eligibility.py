"""Conservative course eligibility decisions over source-backed conditions."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass

from backend.course_catalogue import CourseCatalogue
from backend.prerequisites import PrerequisiteEngine
from preprocessing.course_codes import normalize_course_code


ELIGIBILITY_STATES = ("eligible", "ineligible", "unknown", "ambiguous")
RESTRICTION_STATES = ("passed", "failed", "unknown", "ambiguous")


@dataclass(frozen=True)
class EligibilityIssue:
    code: str
    severity: str
    path: str
    message: str


class EligibilityEngine:
    """Evaluate candidates with reusable catalogue and prerequisite indexes."""

    def __init__(self, catalogue, prerequisite_engine=None):
        if not isinstance(catalogue, CourseCatalogue):
            catalogue = CourseCatalogue.from_dict(catalogue)
        self.catalogue = catalogue
        self.prerequisite_engine = prerequisite_engine or PrerequisiteEngine(catalogue)

    def evaluate(self, course_code, academic_history, restriction_checks=None):
        """Evaluate one candidate using conservative condition precedence.

        Proven failures take precedence over uncertainty. Without a failure,
        ambiguity takes precedence over unknown information; eligibility requires
        every applicable condition to have passed.
        """
        if not isinstance(academic_history, dict):
            raise TypeError("Academic history must be a mapping")
        issues = []
        normalized = normalize_course_code(course_code)
        candidate = self.catalogue.get(course_code) if normalized else None
        prerequisite = self.prerequisite_engine.evaluate(course_code, academic_history)
        restrictions = _restriction_checks(restriction_checks, issues)
        completed_codes = _history_codes(
            academic_history.get("completed_courses"), "completed_courses", issues)
        ongoing_codes = _history_codes(
            academic_history.get("ongoing_courses"), "ongoing_courses", issues)
        history_valid = _history_is_valid(academic_history, issues)

        already_completed = bool(normalized and normalized in completed_codes)
        already_ongoing = bool(normalized and normalized in ongoing_codes)
        passed = []
        failed = []
        unknown = []
        ambiguous = []

        candidate_status = prerequisite["target_resolution_status"]
        if candidate is None:
            unknown.append(_condition("candidate_identity", "candidate_identity_unresolved",
                                      "No usable candidate identity is available",
                                      prerequisite.get("source_records")))
        else:
            passed.append(_condition("candidate_identity", "candidate_identity_resolved",
                                     "Candidate identity is source-backed",
                                     _candidate_sources(candidate)))

        if already_completed:
            failed.append(_condition("target_history", "target_already_completed",
                                     "The target course is already completed"))
        elif already_ongoing:
            failed.append(_condition("target_history", "target_already_ongoing",
                                     "The target course is already ongoing"))
        elif history_valid:
            passed.append(_condition("target_history", "target_not_in_student_history",
                                     "The target course is neither completed nor ongoing"))
        else:
            ambiguous.append(_condition("student_history", "invalid_student_history",
                                        "Student history validation failed"))

        prerequisite_state = prerequisite["prerequisite_state"]
        if prerequisite_state in ("satisfied", "no_explicit_prerequisite"):
            passed.append(_condition("prerequisite", "prerequisite_condition_passed",
                                     "The source-backed prerequisite condition passed",
                                     _prerequisite_sources(prerequisite)))
        elif prerequisite_state == "not_satisfied":
            failed.append(_condition("prerequisite", "prerequisite_not_satisfied",
                                     "An explicit prerequisite is not satisfied",
                                     _prerequisite_sources(prerequisite)))
        elif prerequisite_state == "ambiguous":
            ambiguous.append(_condition("prerequisite", "prerequisite_ambiguous",
                                        "Prerequisite evidence is ambiguous",
                                        _prerequisite_sources(prerequisite)))
        else:
            unknown.append(_condition("prerequisite", "prerequisite_unknown",
                                      "Prerequisite information is unavailable",
                                      _prerequisite_sources(prerequisite)))

        for check in restrictions:
            condition = _condition("academic_restriction", check["reason_code"],
                                   check["message"], check["sources"],
                                   restriction_id=check.get("restriction_id"))
            if check["state"] == "passed":
                passed.append(condition)
            elif check["state"] == "failed":
                failed.append(condition)
            elif check["state"] == "ambiguous":
                ambiguous.append(condition)
            else:
                unknown.append(condition)

        if failed:
            state = "ineligible"
        elif ambiguous:
            state = "ambiguous"
        elif unknown:
            state = "unknown"
        else:
            state = "eligible"
        reason_codes = _unique(item["reason_code"] for item in
                               failed + ambiguous + unknown)
        explanation = _explanation(state, failed, ambiguous, unknown)
        validation = _validation(issues, prerequisite.get("validation"))
        return {
            "requested_course_code": course_code,
            "normalized_course_code": normalized,
            "candidate_resolution_status": candidate_status,
            "eligibility_state": state,
            "already_completed": already_completed,
            "already_ongoing": already_ongoing,
            "prerequisite_evaluation": deepcopy(prerequisite),
            "deterministic_restrictions_checked": restrictions,
            "passed_conditions": passed,
            "failed_conditions": failed,
            "unknown_conditions": unknown,
            "ambiguous_conditions": ambiguous,
            "reason_codes": reason_codes,
            "explanation": explanation,
            "candidate_sources": _candidate_sources(candidate) if candidate else [],
            "validation": validation,
        }

    def evaluate_all(self, academic_history, restrictions_by_course=None):
        """Evaluate every indexed candidate once and retain all result groups."""
        restrictions_by_course = restrictions_by_course or {}
        if not isinstance(restrictions_by_course, dict):
            raise TypeError("Restrictions by course must be a mapping")
        groups = {state: [] for state in ELIGIBILITY_STATES}
        prerequisite_counts = Counter()
        completed_count = 0
        ongoing_count = 0
        restriction_affected = 0
        for code in self.catalogue.course_codes():
            checks = restrictions_by_course.get(code, [])
            result = self.evaluate(code, academic_history, checks)
            groups[result["eligibility_state"]].append(result)
            prerequisite_counts[result["prerequisite_evaluation"]["prerequisite_state"]] += 1
            completed_count += result["already_completed"]
            ongoing_count += result["already_ongoing"]
            restriction_affected += any(
                check["state"] != "passed" for check in result["deterministic_restrictions_checked"])
        return {
            "groups": groups,
            "summary": {
                "total_candidate_count": len(self.catalogue),
                **{f"{state}_count": len(groups[state]) for state in ELIGIBILITY_STATES},
                "already_completed_count": completed_count,
                "already_ongoing_count": ongoing_count,
                "prerequisite_state_counts": dict(sorted(prerequisite_counts.items())),
                "other_restriction_affected_count": restriction_affected,
                "missing_prerequisite_became_eligible_count": sum(
                    result["prerequisite_evaluation"]["prerequisite_state"] == "unknown"
                    for result in groups["eligible"]),
            },
        }


def _restriction_checks(checks, issues):
    if checks is None:
        return []
    if not isinstance(checks, list):
        _issue(issues, "malformed_restriction_checks", "error", "restriction_checks",
               "Restriction checks must be a list")
        return [{"restriction_id": None, "state": "ambiguous",
                 "reason_code": "malformed_restriction_checks",
                 "message": "Restriction checks could not be validated", "sources": []}]
    result = []
    for index, raw in enumerate(checks):
        path = f"restriction_checks[{index}]"
        if not isinstance(raw, dict) or raw.get("state") not in RESTRICTION_STATES:
            _issue(issues, "malformed_restriction_check", "error", path,
                   "Restriction check requires a supported state")
            result.append({"restriction_id": None, "state": "ambiguous",
                           "reason_code": "malformed_restriction_check",
                           "message": "Restriction check could not be validated", "sources": []})
            continue
        sources = raw.get("sources") or []
        if not isinstance(sources, list):
            sources = []
            _issue(issues, "malformed_restriction_sources", "error", path + ".sources",
                   "Restriction sources must be a list")
        result.append({
            "restriction_id": raw.get("restriction_id"),
            "state": raw["state"],
            "reason_code": raw.get("reason_code") or "academic_restriction_" + raw["state"],
            "message": raw.get("message") or f"Academic restriction {raw['state']}",
            "sources": deepcopy(sources),
        })
    return result


def _history_codes(entries, path, issues):
    if not isinstance(entries, list):
        _issue(issues, "malformed_student_history", "error", path,
               "Student history course collection must be a list")
        return set()
    codes = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _issue(issues, "malformed_student_history", "error", f"{path}[{index}]",
                   "Student history entries must be normalized mappings")
            continue
        code = normalize_course_code(entry.get("normalized_course_code"))
        if code:
            codes.add(code)
    return codes


def _history_is_valid(history, issues):
    validations = [history.get("profile_validation"), history.get("validation")]
    invalid = any(isinstance(item, dict) and item.get("is_valid") is False
                  for item in validations)
    if invalid:
        _issue(issues, "invalid_student_history", "error", "academic_history",
               "Profile or academic-history validation failed")
    return not invalid and not any(issue.severity == "error" for issue in issues)


def _candidate_sources(candidate):
    return [{"record_id": record.get("record_id"),
             "source_file": (record.get("source") or {}).get("source_file"),
             "page_numbers": deepcopy((record.get("source") or {}).get("page_numbers") or [])}
            for record in candidate.get("source_records", [])]


def _prerequisite_sources(prerequisite):
    requirement = prerequisite.get("prerequisite_requirements")
    if isinstance(requirement, dict):
        return deepcopy(requirement.get("evidence") or [])
    ambiguity = prerequisite.get("ambiguity")
    if isinstance(ambiguity, dict):
        return deepcopy(ambiguity.get("records") or [])
    return deepcopy(prerequisite.get("source_records") or [])


def _condition(kind, reason_code, message, sources=None, restriction_id=None):
    return {"condition": kind, "reason_code": reason_code, "message": message,
            "restriction_id": restriction_id, "sources": deepcopy(sources or [])}


def _explanation(state, failed, ambiguous, unknown):
    if state == "ineligible":
        return "Ineligible because: " + "; ".join(item["message"] for item in failed)
    if state == "ambiguous":
        return "Eligibility is ambiguous because: " + "; ".join(
            item["message"] for item in ambiguous)
    if state == "unknown":
        return "Eligibility is unknown because: " + "; ".join(
            item["message"] for item in unknown)
    return "Eligible based on every available deterministic condition."


def _validation(issues, prerequisite_validation):
    combined = [asdict(issue) for issue in issues]
    for issue in (prerequisite_validation or {}).get("issues", []):
        if issue not in combined:
            combined.append(deepcopy(issue))
    errors = [issue for issue in combined if issue.get("severity") == "error"]
    warnings = [issue for issue in combined if issue.get("severity") == "warning"]
    return {"is_valid": not errors, "issues": combined,
            "error_count": len(errors), "warning_count": len(warnings)}


def _unique(values):
    return list(dict.fromkeys(values))


def _issue(issues, code, severity, path, message):
    issue = EligibilityIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
