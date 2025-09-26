"""Filter candidate courses by explicit remaining academic requirements."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from preprocessing.course_codes import normalize_course_code


FILTER_STATES = (
    "matches_remaining_requirement",
    "matches_satisfied_requirement",
    "relationship_not_established",
    "requirement_unavailable",
    "ambiguous",
)
POOL_COLLECTIONS = (
    "confirmed_candidates",
    "verification_required_candidates",
    "excluded_candidates",
)
REQUIREMENT_COLLECTIONS = {
    "satisfied_requirements": "satisfied",
    "partially_satisfied_requirements": "partially_satisfied",
    "remaining_requirements": "remaining",
    "unevaluable_requirements": "unevaluable",
}


@dataclass(frozen=True)
class RequirementFilterIssue:
    code: str
    severity: str
    path: str
    message: str


def filter_by_remaining_requirements(candidate_pool, academic_requirements):
    """Classify every candidate without combining eligibility and relevance."""
    if not isinstance(candidate_pool, dict):
        raise TypeError("Candidate pool must be a mapping")
    requirement_index, requirement_issues = _requirement_index(academic_requirements)
    candidates = []
    for collection in POOL_COLLECTIONS:
        entries = candidate_pool.get(collection, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            candidates.append(_classify_candidate(entry, requirement_index))
    candidates.sort(key=lambda item: item.get("normalized_course_code") or "")
    result = {
        "candidates": candidates,
        "recommendation_safe_candidates": [
            deepcopy(item) for item in candidates if item["recommendation_safe"]],
        "state_groups": {
            state: [deepcopy(item) for item in candidates
                    if item["requirement_filter_state"] == state]
            for state in FILTER_STATES
        },
        "pool_state_groups": {
            state: [deepcopy(item) for item in candidates
                    if item.get("candidate_pool_state") == state]
            for state in ("confirmed", "verification_required", "excluded")
        },
        "requirement_diagnostics": [asdict(issue) for issue in requirement_issues],
    }
    result["validation"] = validate_requirement_filter(result)
    result["summary"] = _summary(result)
    return result


def validate_requirement_filter(result):
    """Validate identities, explicit matches, safety criteria, and traceability."""
    issues = []
    seen = {}
    candidates = result.get("candidates", []) if isinstance(result, dict) else []
    if not isinstance(candidates, list):
        candidates = []
        _issue(issues, "malformed_candidate_collection", "error", "candidates",
               "Filtered candidates must be a list")
    for index, candidate in enumerate(candidates):
        path = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            _issue(issues, "malformed_candidate", "error", path,
                   "Filtered candidate must be a mapping")
            continue
        code = normalize_course_code(candidate.get("normalized_course_code"))
        if code is None:
            _issue(issues, "candidate_identity_missing", "error", path,
                   "Candidate has no valid normalized identity")
        elif code in seen:
            _issue(issues, "duplicate_candidate_identity", "error", path,
                   f"Candidate identity {code} also occurs at {seen[code]}")
        else:
            seen[code] = path
        pool_state = candidate.get("candidate_pool_state")
        if pool_state not in ("confirmed", "verification_required", "excluded"):
            _issue(issues, "malformed_candidate_pool_state", "error", path,
                   f"Unsupported candidate pool state {pool_state!r}")
        if not isinstance(candidate.get("eligibility_result"), dict):
            _issue(issues, "missing_eligibility_result", "error", path,
                   "Candidate has no structured eligibility result")
        state = candidate.get("requirement_filter_state")
        if state not in FILTER_STATES:
            _issue(issues, "inconsistent_requirement_state", "error", path,
                   f"Unsupported requirement-filter state {state!r}")
        matches = candidate.get("requirement_matches")
        if not isinstance(matches, list):
            _issue(issues, "malformed_requirement_relationship", "error", path,
                   "Requirement matches must be a list")
            matches = []
        for match_index, match in enumerate(matches):
            match_path = f"{path}.requirement_matches[{match_index}]"
            if not isinstance(match, dict):
                _issue(issues, "malformed_requirement_relationship", "error", match_path,
                       "Requirement match must be a mapping")
                continue
            if not match.get("programme_scope"):
                _issue(issues, "requirement_programme_scope_missing", "error", match_path,
                       "Programme-specific match lacks programme scope")
            if not match.get("rule_id"):
                _issue(issues, "requirement_rule_identity_missing", "error", match_path,
                       "Explicit match lacks requirement identity")
            if not match.get("sources"):
                _issue(issues, "requirement_source_traceability_missing", "error", match_path,
                       "Explicit match lacks source traceability")
            if match.get("requirement_state") not in (
                    "satisfied", "partially_satisfied", "remaining", "unevaluable"):
                _issue(issues, "inconsistent_requirement_state", "error", match_path,
                       "Requirement match has an unsupported state")
        if candidate.get("recommendation_safe"):
            if pool_state != "confirmed" or candidate.get("eligibility_state") != "eligible":
                _issue(issues, "unsafe_candidate_not_eligible", "error", path,
                       "Recommendation-safe candidate is not confirmed eligible")
            if state != "matches_remaining_requirement" or not matches:
                _issue(issues, "unsafe_candidate_without_remaining_match", "error", path,
                       "Recommendation-safe candidate lacks an explicit remaining match")
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {
        "is_valid": not errors,
        "issues": errors + warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "duplicate_candidate_identity_count": sum(
            issue.code == "duplicate_candidate_identity" for issue in issues),
    }


def _classify_candidate(raw_candidate, requirement_index):
    candidate = deepcopy(raw_candidate) if isinstance(raw_candidate, dict) else {}
    code = normalize_course_code(candidate.get("normalized_course_code"))
    relevance = candidate.get("requirement_relevance")
    relationships = relevance.get("relationships", []) if isinstance(relevance, dict) else []
    unresolved = relevance.get("unresolved_relationships", []) \
        if isinstance(relevance, dict) else []
    matches = []
    rejected = []
    malformed = not isinstance(relationships, list)
    for relationship in relationships if isinstance(relationships, list) else []:
        match, reason = _match_relationship(relationship, requirement_index)
        if match:
            matches.append(match)
        else:
            rejected.append({"relationship": deepcopy(relationship), "reason": reason})
            malformed = malformed or reason.startswith("malformed_")
    if unresolved:
        rejected.extend({"relationship": deepcopy(item),
                         "reason": "ambiguous_candidate_relationship"}
                        for item in unresolved)

    states = {item["requirement_state"] for item in matches}
    if malformed or unresolved or rejected and not matches:
        state = "ambiguous"
    elif states & {"remaining", "partially_satisfied"}:
        state = "matches_remaining_requirement"
    elif "satisfied" in states:
        state = "matches_satisfied_requirement"
    elif "unevaluable" in states:
        state = "requirement_unavailable"
    else:
        state = "relationship_not_established"
    safe = (candidate.get("candidate_pool_state") == "confirmed" and
            candidate.get("eligibility_state") == "eligible" and
            state == "matches_remaining_requirement" and bool(matches))
    candidate.update({
        "requirement_filter_state": state,
        "requirement_matches": matches,
        "unresolved_requirement_relationships": rejected,
        "programme_requirement_states": _programme_states(matches),
        "recommendation_safe": safe,
        "requirement_filter_reason_codes": _reason_codes(state, candidate, rejected),
    })
    return candidate


def _match_relationship(relationship, index):
    if not isinstance(relationship, dict):
        return None, "malformed_requirement_relationship"
    rule_id = relationship.get("rule_id")
    programme = relationship.get("programme_scope")
    category = relationship.get("normalized_category")
    if not rule_id:
        return None, "malformed_missing_rule_identity"
    if not programme:
        return None, "malformed_missing_programme_scope"
    if not category:
        return None, "malformed_missing_category"
    record = index.get((rule_id, programme, category))
    if record is None:
        return None, "relationship_not_found_in_requirement_summary"
    if not relationship.get("sources") or not record.get("sources"):
        return None, "malformed_missing_source_traceability"
    match = deepcopy(relationship)
    match["requirement_state"] = record["requirement_state"]
    match["requirement_record"] = deepcopy(record["record"])
    match["sources"] = deepcopy(record["sources"])
    return match, None


def _requirement_index(summary):
    index = {}
    issues = []
    if not isinstance(summary, dict):
        _issue(issues, "malformed_academic_requirements", "error", "requirements",
               "Academic requirement summary must be a mapping")
        return index, issues
    progress = summary.get("requirement_progress") or summary
    programmes = progress.get("programme_progress", []) if isinstance(progress, dict) else []
    if not isinstance(programmes, list):
        _issue(issues, "malformed_programme_progress", "error", "programme_progress",
               "Programme progress must be a list")
        return index, issues
    for programme_index, programme in enumerate(programmes):
        if not isinstance(programme, dict):
            continue
        scope = programme.get("programme")
        for category_index, category in enumerate(programme.get("categories") or []):
            if not isinstance(category, dict):
                continue
            normalized_category = category.get("normalized_category")
            for collection, expected_state in REQUIREMENT_COLLECTIONS.items():
                for record_index, record in enumerate(category.get(collection) or []):
                    if not isinstance(record, dict):
                        continue
                    path = (f"programme_progress[{programme_index}].categories"
                            f"[{category_index}].{collection}[{record_index}]")
                    rule_id = record.get("rule_id")
                    actual = record.get("completion_status")
                    if actual != expected_state:
                        _issue(issues, "inconsistent_requirement_state", "error", path,
                               "Requirement state conflicts with its collection")
                        continue
                    if not rule_id or not scope or not normalized_category:
                        _issue(issues, "malformed_requirement_context", "warning", path,
                               "Requirement lacks rule, programme, or category identity")
                        continue
                    key = (rule_id, scope, normalized_category)
                    index[key] = {"requirement_state": actual,
                                  "record": deepcopy(record),
                                  "sources": deepcopy(record.get("sources") or [])}
    return index, issues


def _programme_states(matches):
    output = {}
    for match in matches:
        programme = match["programme_scope"]
        item = output.setdefault(programme, {"programme_scope": programme,
                                             "categories": {}, "rule_ids": []})
        category = match["normalized_category"]
        item["categories"].setdefault(category, [])
        state = match["requirement_state"]
        if state not in item["categories"][category]:
            item["categories"][category].append(state)
        if match["rule_id"] not in item["rule_ids"]:
            item["rule_ids"].append(match["rule_id"])
    return [output[key] for key in sorted(output)]


def _reason_codes(state, candidate, rejected):
    reasons = list(candidate.get("reason_codes") or [])
    reasons.append(state)
    reasons.extend(item["reason"] for item in rejected)
    return list(dict.fromkeys(reasons))


def _summary(result):
    candidates = result["candidates"]
    state_counts = {state: len(result["state_groups"][state]) for state in FILTER_STATES}
    category_counts = {}
    for candidate in candidates:
        for match in candidate["requirement_matches"]:
            if match["requirement_state"] not in ("remaining", "partially_satisfied"):
                continue
            key = (match["programme_scope"], match["normalized_category"])
            category_counts[key] = category_counts.get(key, 0) + 1
    return {
        "total_candidate_count": len(candidates),
        "confirmed_candidate_count": len(result["pool_state_groups"]["confirmed"]),
        "verification_required_candidate_count": len(
            result["pool_state_groups"]["verification_required"]),
        "excluded_candidate_count": len(result["pool_state_groups"]["excluded"]),
        "requirement_filter_state_counts": state_counts,
        "recommendation_safe_count": len(result["recommendation_safe_candidates"]),
        "remaining_match_breakdown": [
            {"programme_scope": key[0], "normalized_category": key[1], "count": count}
            for key, count in sorted(category_counts.items())],
    }


def _issue(issues, code, severity, path, message):
    issue = RequirementFilterIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
