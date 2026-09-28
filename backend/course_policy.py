"""Orchestrate the deterministic Phase 3 to Phase 4 course-policy pipeline."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from backend.academic_requirements import AcademicRequirementService
from backend.candidate_pool import CandidatePoolBuilder
from backend.course_catalogue import CourseCatalogue
from backend.requirement_filter import filter_by_remaining_requirements
from preprocessing.course_codes import normalize_course_code


SUCCESS_STATUSES = ("complete", "complete_with_incomplete_data")


@dataclass(frozen=True)
class CoursePolicyIssue:
    code: str
    severity: str
    path: str
    message: str


class CoursePolicyService:
    """Reusable Phase 4 service backed by preloaded catalogue and rule indexes."""

    def __init__(self, course_catalogue=None, academic_requirement_service=None,
                 candidate_pool_builder=None, requirement_filter=None):
        if course_catalogue is None:
            course_catalogue = CourseCatalogue.load_source_backed()
        elif not isinstance(course_catalogue, CourseCatalogue):
            course_catalogue = CourseCatalogue.from_dict(course_catalogue)
        self.course_catalogue = course_catalogue
        self.academic_requirement_service = (
            academic_requirement_service or AcademicRequirementService())
        self.candidate_pool_builder = (
            candidate_pool_builder or CandidatePoolBuilder(course_catalogue))
        self.requirement_filter = requirement_filter or filter_by_remaining_requirements

    def evaluate(self, raw_profile):
        """Evaluate one profile through the existing Phase 3 and Phase 4 services."""
        academic = self.academic_requirement_service.build_summary(raw_profile)
        if academic.get("status") not in SUCCESS_STATUSES:
            return _stopped_result(academic, self.course_catalogue)

        candidate_pool = self.candidate_pool_builder.build(
            academic.get("academic_history") or {}, academic)
        filtered = self.requirement_filter(candidate_pool, academic)
        result = _result(academic, self.course_catalogue, candidate_pool, filtered)
        result["validation"] = validate_course_policy_result(result)
        if not result["validation"]["is_valid"]:
            result["status"] = "invalid_component_result"
        return result


def evaluate_course_policy(raw_profile, course_catalogue=None,
                           academic_requirement_service=None):
    """Convenience interface for a single complete Phase 4 evaluation."""
    return CoursePolicyService(course_catalogue, academic_requirement_service).evaluate(
        raw_profile)


def validate_course_policy_result(result):
    """Validate the Phase 5 safety contract and cross-component counts."""
    issues = []
    if not isinstance(result, dict):
        _issue(issues, "malformed_policy_result", "error", "result",
               "Course-policy result must be a mapping")
        return _validation(issues)
    catalogue_codes = set(result.get("catalogue_course_codes") or [])
    candidates = _all_candidates(result)
    by_code = {}
    for index, candidate in enumerate(candidates):
        path = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            _issue(issues, "malformed_component_candidate", "error", path,
                   "Candidate must be a mapping")
            continue
        code = normalize_course_code(candidate.get("normalized_course_code"))
        if code is None:
            _issue(issues, "candidate_identity_missing", "error", path,
                   "Candidate has no valid normalized identity")
            continue
        if code in by_code:
            _issue(issues, "duplicate_candidate_identity", "error", path,
                   f"Candidate identity {code} occurs more than once")
        else:
            by_code[code] = candidate

    safe = result.get("recommendation_safe_candidates") or []
    safe_codes = set()
    for index, candidate in enumerate(safe):
        path = f"recommendation_safe_candidates[{index}]"
        if not isinstance(candidate, dict):
            _issue(issues, "malformed_safe_candidate", "error", path,
                   "Safe candidate must be a mapping")
            continue
        code = normalize_course_code(candidate.get("normalized_course_code"))
        if code is None or code not in catalogue_codes:
            _issue(issues, "safe_identity_missing_from_catalogue", "error", path,
                   "Recommendation-safe identity is absent from the catalogue")
        if code in safe_codes:
            _issue(issues, "duplicate_safe_candidate_identity", "error", path,
                   "Recommendation-safe identity occurs more than once")
        safe_codes.add(code)
        if candidate.get("eligibility_state") != "eligible":
            _issue(issues, "safe_candidate_not_eligible", "error", path,
                   "Recommendation-safe candidate is not eligible")
        if candidate.get("candidate_pool_state") != "confirmed":
            _issue(issues, "safe_candidate_not_confirmed", "error", path,
                   "Recommendation-safe candidate is not in the confirmed pool")
        if candidate.get("requirement_filter_state") != "matches_remaining_requirement":
            _issue(issues, "safe_candidate_not_remaining_relevant", "error", path,
                   "Recommendation-safe candidate lacks a remaining-requirement match")
        if candidate.get("eligibility_result", {}).get("already_completed"):
            _issue(issues, "completed_candidate_marked_safe", "error", path,
                   "Completed target course re-entered the safe pool")
        if candidate.get("eligibility_result", {}).get("already_ongoing"):
            _issue(issues, "ongoing_candidate_marked_safe", "error", path,
                   "Ongoing target course re-entered the safe pool")
        _validate_matches(candidate.get("requirement_matches"), issues, path)
        if not candidate.get("source_references"):
            _issue(issues, "safe_candidate_traceability_missing", "error", path,
                   "Recommendation-safe candidate lacks source references")

    expected_total = len(candidates)
    summary = result.get("summary") or {}
    if summary.get("total_candidate_count") != expected_total:
        _issue(issues, "candidate_count_mismatch", "error", "summary",
               "Final candidate count does not reconcile")
    pool_total = sum(summary.get(name, 0) for name in (
        "confirmed_candidate_count", "verification_required_candidate_count",
        "excluded_candidate_count"))
    if pool_total != expected_total:
        _issue(issues, "pool_count_mismatch", "error", "summary",
               "Candidate-pool counts do not reconcile")
    if summary.get("recommendation_safe_count") != len(safe):
        _issue(issues, "safe_count_mismatch", "error", "summary",
               "Recommendation-safe count does not reconcile")
    _validate_programme_separation(candidates, issues)
    return _validation(issues)


def _result(academic, catalogue, pool, filtered):
    candidates = deepcopy(filtered.get("candidates") or [])
    safe = deepcopy(filtered.get("recommendation_safe_candidates") or [])
    verification = [deepcopy(item) for item in candidates
                    if item.get("candidate_pool_state") == "verification_required"]
    excluded = [deepcopy(item) for item in candidates
                if item.get("candidate_pool_state") == "excluded"]
    diagnostics = _diagnostics(academic, pool, filtered)
    incomplete = _incomplete_data(academic, pool, filtered)
    summary = _summary(catalogue, pool, filtered)
    phase5_input = {
        "recommendation_safe_candidates": deepcopy(safe),
        "verification_required_candidates": deepcopy(verification),
        "excluded_candidate_explanations": [
            {"normalized_course_code": item.get("normalized_course_code"),
             "reason_codes": deepcopy(item.get("reason_codes") or []),
             "source_references": deepcopy(item.get("source_references") or [])}
            for item in excluded],
        "incomplete_data": deepcopy(incomplete),
        "diagnostics": deepcopy(diagnostics),
        "contract": {
            "confirmed_recommendations_must_use": "recommendation_safe_candidates",
            "verification_required_is_not_confirmed": True,
            "ranking_performed": False,
        },
    }
    return {
        "status": "complete_with_incomplete_data" if incomplete["has_incomplete_data"]
                  else "complete",
        "student": deepcopy(academic.get("student")),
        "academic_history": deepcopy(academic.get("academic_history") or {}),
        "academic_requirement_summary": deepcopy(academic),
        "candidate_catalogue_summary": deepcopy(catalogue.validation.get("summary") or {}),
        "catalogue_course_codes": catalogue.course_codes(),
        "prerequisite_eligibility_summary": deepcopy(pool.get("eligibility_summary") or {}),
        "candidate_pool_summary": deepcopy(pool.get("summary") or {}),
        "requirement_filter_summary": deepcopy(filtered.get("summary") or {}),
        "recommendation_safe_candidates": safe,
        "verification_required_candidates": verification,
        "excluded_candidates": excluded,
        "all_candidates": candidates,
        "diagnostics": diagnostics,
        "incomplete_data": incomplete,
        "source_traceability": _traceability(candidates),
        "phase5_input": phase5_input,
        "summary": summary,
        "validation": {},
    }


def _stopped_result(academic, catalogue):
    diagnostics = _diagnostics(academic, {}, {})
    result = {
        "status": academic.get("status") or "invalid_student_profile",
        "student": deepcopy(academic.get("student")),
        "academic_history": deepcopy(academic.get("academic_history") or {}),
        "academic_requirement_summary": deepcopy(academic),
        "candidate_catalogue_summary": deepcopy(catalogue.validation.get("summary") or {}),
        "catalogue_course_codes": catalogue.course_codes(),
        "prerequisite_eligibility_summary": {}, "candidate_pool_summary": {},
        "requirement_filter_summary": {}, "recommendation_safe_candidates": [],
        "verification_required_candidates": [], "excluded_candidates": [],
        "all_candidates": [], "diagnostics": diagnostics,
        "incomplete_data": deepcopy(academic.get("incomplete_data") or {
            "has_incomplete_data": True}),
        "source_traceability": {"candidate_count": 0,
                                "candidates_with_source_references": 0},
        "phase5_input": {
            "recommendation_safe_candidates": [],
            "verification_required_candidates": [],
            "excluded_candidate_explanations": [],
            "incomplete_data": deepcopy(academic.get("incomplete_data") or {}),
            "diagnostics": deepcopy(diagnostics),
            "contract": {"confirmed_recommendations_must_use":
                         "recommendation_safe_candidates",
                         "verification_required_is_not_confirmed": True,
                         "ranking_performed": False},
        },
        "summary": {"total_candidate_count": 0, "confirmed_candidate_count": 0,
                    "verification_required_candidate_count": 0,
                    "excluded_candidate_count": 0, "recommendation_safe_count": 0},
    }
    result["validation"] = validate_course_policy_result(result)
    return result


def _diagnostics(academic, pool, filtered):
    diagnostics = []
    sources = (
        ("academic_requirements", (academic.get("validation") or {}).get("errors", [])),
        ("academic_requirements", (academic.get("validation") or {}).get("warnings", [])),
        ("candidate_pool", (pool.get("validation") or {}).get("issues", [])),
        ("requirement_filter", filtered.get("requirement_diagnostics") or []),
        ("requirement_filter", (filtered.get("validation") or {}).get("issues", [])),
    )
    for component, items in sources:
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                preserved = deepcopy(item)
                preserved["component"] = component
                if preserved not in diagnostics:
                    diagnostics.append(preserved)
    return diagnostics


def _incomplete_data(academic, pool, filtered):
    eligibility = pool.get("eligibility_summary") or {}
    filter_summary = filtered.get("summary") or {}
    states = filter_summary.get("requirement_filter_state_counts") or {}
    categories = []
    for code, count in (
        ("missing_prerequisite_information", eligibility.get(
            "prerequisite_state_counts", {}).get("unknown", 0)),
        ("ambiguous_prerequisite_evidence", eligibility.get(
            "prerequisite_state_counts", {}).get("ambiguous", 0)),
        ("requirement_relationship_not_established", states.get(
            "relationship_not_established", 0)),
        ("requirement_unavailable", states.get("requirement_unavailable", 0)),
        ("ambiguous_requirement_relationship", states.get("ambiguous", 0)),
    ):
        if count:
            categories.append({"code": code, "count": count})
    upstream = deepcopy(academic.get("incomplete_data") or {})
    return {"has_incomplete_data": bool(categories) or bool(
                upstream.get("has_incomplete_data")),
            "categories": categories, "academic_requirements": upstream}


def _summary(catalogue, pool, filtered):
    pool_summary = pool.get("summary") or {}
    filter_summary = filtered.get("summary") or {}
    eligibility = pool.get("eligibility_summary") or {}
    return {
        "catalogue_candidate_count": len(catalogue),
        "total_candidate_count": filter_summary.get("total_candidate_count", 0),
        "eligible_count": eligibility.get("eligible_count", 0),
        "ineligible_count": eligibility.get("ineligible_count", 0),
        "unknown_eligibility_count": eligibility.get("unknown_count", 0),
        "ambiguous_eligibility_count": eligibility.get("ambiguous_count", 0),
        "confirmed_candidate_count": pool_summary.get("confirmed_candidate_count", 0),
        "verification_required_candidate_count": pool_summary.get(
            "verification_required_candidate_count", 0),
        "excluded_candidate_count": pool_summary.get("excluded_candidate_count", 0),
        "remaining_requirement_match_count": filter_summary.get(
            "requirement_filter_state_counts", {}).get(
                "matches_remaining_requirement", 0),
        "relationship_not_established_count": filter_summary.get(
            "requirement_filter_state_counts", {}).get(
                "relationship_not_established", 0),
        "requirement_unavailable_count": filter_summary.get(
            "requirement_filter_state_counts", {}).get("requirement_unavailable", 0),
        "ambiguous_requirement_count": filter_summary.get(
            "requirement_filter_state_counts", {}).get("ambiguous", 0),
        "recommendation_safe_count": filter_summary.get("recommendation_safe_count", 0),
    }


def _traceability(candidates):
    return {"candidate_count": len(candidates),
            "candidates_with_source_references": sum(
                bool(item.get("source_references")) for item in candidates),
            "safe_matches_with_rule_and_sources": sum(
                bool(match.get("rule_id") and match.get("sources"))
                for item in candidates if item.get("recommendation_safe")
                for match in item.get("requirement_matches") or [])}


def _all_candidates(result):
    candidates = result.get("all_candidates")
    return candidates if isinstance(candidates, list) else []


def _validate_matches(matches, issues, path):
    if not isinstance(matches, list) or not matches:
        _issue(issues, "safe_requirement_match_missing", "error", path,
               "Recommendation-safe candidate lacks structured requirement matches")
        return
    for index, match in enumerate(matches):
        if not isinstance(match, dict) or not match.get("rule_id") or not match.get("sources"):
            _issue(issues, "safe_requirement_traceability_missing", "error",
                   f"{path}.requirement_matches[{index}]",
                   "Requirement match lacks rule identity or source evidence")


def _validate_programme_separation(candidates, issues):
    for index, candidate in enumerate(candidates):
        states = candidate.get("programme_requirement_states") or []
        scopes = [item.get("programme_scope") for item in states if isinstance(item, dict)]
        if len(scopes) != len(set(scopes)):
            _issue(issues, "programme_scope_merged", "error", f"candidates[{index}]",
                   "Programme-specific requirement states are not separated")


def _validation(issues):
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {"is_valid": not errors, "issues": errors + warnings,
            "error_count": len(errors), "warning_count": len(warnings)}


def _issue(issues, code, severity, path, message):
    issue = CoursePolicyIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
