"""Rank preference matches only after Phase 4 recommendation-safety decisions."""

from copy import deepcopy
from dataclasses import asdict, dataclass


RANKABLE_STATES = ("strong_match", "partial_match")
UNSAFE_STATES = {"unknown", "ambiguous", "ineligible"}


@dataclass(frozen=True)
class RankingIssue:
    code: str
    severity: str
    path: str
    message: str


class RecommendationRanker:
    """Combine existing policy safety with explainable preference relevance."""

    def rank(self, preference_matches, requirement_filter):
        issues = []
        matches_valid = _upstream_valid(preference_matches, "preference_matches", issues)
        policy_valid = _upstream_valid(requirement_filter, "requirement_filter", issues)
        matches = _match_collection(preference_matches, issues)
        candidates = _candidate_collection(requirement_filter, issues)
        candidate_index = {}
        for index, candidate in enumerate(candidates):
            code = candidate.get("normalized_course_code") or candidate.get("course_code")
            if not isinstance(code, str) or not code.strip():
                _issue(issues, "candidate_identity_missing", "error",
                       f"candidates[{index}]", "Candidate has no course identity")
                continue
            if code in candidate_index:
                _issue(issues, "duplicate_candidate_identity", "error",
                       f"candidates[{index}]", f"Duplicate candidate identity {code}")
            candidate_index[code] = candidate
        match_index = {}
        for index, match in enumerate(matches):
            code = match.get("course_code")
            if not isinstance(code, str) or not code.strip():
                _issue(issues, "match_identity_missing", "error",
                       f"matches[{index}]", "Preference match has no course identity")
                continue
            if code in match_index:
                _issue(issues, "duplicate_match_identity", "error",
                       f"matches[{index}]", f"Duplicate match identity {code}")
            match_index[code] = match

        inputs_valid = not any(issue.severity == "error" for issue in issues)

        confirmed = []
        verification = []
        for code in sorted(set(candidate_index) | set(match_index)):
            candidate = candidate_index.get(code)
            match = match_index.get(code)
            item = _combine(code, candidate, match, issues,
                            matches_valid=matches_valid and inputs_valid,
                            policy_valid=policy_valid and inputs_valid)
            if item["ranking_group"] == "confirmed":
                confirmed.append(item)
            elif item["ranking_group"] == "verification_required":
                verification.append(item)
        confirmed.sort(key=lambda item: (-item["ranking"]["score"],
                                         -item["ranking"]["matched_priority_weight"],
                                         item["course_code"]))
        verification.sort(key=lambda item: (-item["ranking"]["grounded_relevance_weight"],
                                            -item["ranking"]["score"],
                                            item["course_code"]))
        result = {
            "confirmed_recommendations": confirmed,
            "verification_required": verification,
            "ranking_strategy": "policy_safety_then_preference_evidence",
            "diagnostics": [asdict(issue) for issue in issues],
        }
        result["summary"] = {
            "confirmed_count": len(confirmed),
            "verification_required_count": len(verification),
            "safe_candidate_count": sum(
                bool(item.get("policy", {}).get("recommendation_safe"))
                for item in confirmed + verification),
        }
        result["validation"] = validate_ranking_result(result)
        return result


def rank_recommendations(preference_matches, requirement_filter):
    """Convenience wrapper around :class:`RecommendationRanker`."""
    return RecommendationRanker().rank(preference_matches, requirement_filter)


def validate_ranking_result(result):
    """Validate safety boundaries, identity uniqueness, and traceability."""
    issues = [RankingIssue(
        item.get("code", "ranking_diagnostic"), item.get("severity", "error"),
        item.get("path", "ranking"), item.get("message", "Ranking diagnostic"),
    ) for item in (result.get("diagnostics") or []) if isinstance(item, dict)]
    seen = set()
    for collection, expected in (("confirmed_recommendations", "confirmed"),
                                 ("verification_required", "verification_required")):
        entries = result.get(collection, []) if isinstance(result, dict) else []
        if not isinstance(entries, list):
            _issue(issues, "malformed_ranking_collection", "error", collection,
                   "Ranking collection must be a list")
            continue
        for index, item in enumerate(entries):
            path = f"{collection}[{index}]"
            code = item.get("course_code") if isinstance(item, dict) else None
            if not isinstance(code, str) or not code.strip():
                _issue(issues, "ranked_identity_missing", "error", path,
                       "Ranked item has no course identity")
                continue
            if code in seen:
                _issue(issues, "duplicate_ranked_identity", "error", path,
                       f"Course {code} occurs more than once")
            seen.add(code)
            if item.get("ranking_group") != expected:
                _issue(issues, "inconsistent_ranking_group", "error", path,
                       "Item is in the wrong ranking collection")
            policy = item.get("policy") or {}
            if expected == "confirmed":
                preference = item.get("preference_match") or {}
                if not _policy_is_consistently_safe(policy, True):
                    _issue(issues, "unsafe_confirmed_recommendation", "error", path,
                           "Confirmed course fails independent Phase 4 safety checks")
                if (preference.get("match_state") not in RANKABLE_STATES or
                        not preference.get("matched_preferences") or
                        preference.get("conflicting_preferences") or
                        preference.get("validation_is_valid") is not True or
                        (preference.get("uncertainty") or {}).get("needs_verification")):
                    _issue(issues, "invalid_confirmed_preference_match", "error", path,
                           "Confirmed course fails independent preference checks")
                if not _preference_sources_valid(preference):
                    _issue(issues, "confirmed_preference_traceability_missing", "error", path,
                           "Confirmed preference evidence lacks source traceability")
            if not _sources_valid(item.get("source_references")):
                _issue(issues, "missing_source_references", "error", path,
                       "Ranking item must preserve nonempty source references")
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {"is_valid": not errors, "issues": errors + warnings,
            "error_count": len(errors), "warning_count": len(warnings)}


def _match_collection(value, issues):
    if isinstance(value, dict):
        value = value.get("matches", value.get("results", []))
    if not isinstance(value, list):
        _issue(issues, "malformed_preference_matches", "error", "matches",
               "Preference matches must be a list or match collection")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            _issue(issues, "malformed_preference_match_entry", "error",
                   f"matches[{index}]", "Preference match entry must be a mapping")
            continue
        result.append(deepcopy(item))
    return result


def _candidate_collection(value, issues):
    if isinstance(value, dict):
        if isinstance(value.get("candidates"), list):
            value = value["candidates"]
        else:
            value = (value.get("recommendation_safe_candidates", []) +
                     value.get("verification_required_candidates", []) +
                     value.get("excluded_candidates", []))
    if not isinstance(value, list):
        _issue(issues, "malformed_requirement_filter", "error", "candidates",
               "Requirement-filter output must contain candidate entries")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            _issue(issues, "malformed_candidate_entry", "error",
                   f"candidates[{index}]", "Candidate entry must be a mapping")
            continue
        result.append(deepcopy(item))
    return result


def _combine(code, candidate, match, issues, matches_valid=True, policy_valid=True):
    policy = _policy(candidate)
    preference = _preference(match)
    safe = _policy_is_consistently_safe(policy, policy_valid)
    state = preference["match_state"]
    hard_conflict = any(item.get("constraint") == "hard"
                        for item in preference["conflicting_preferences"])
    has_positive = bool(preference["matched_preferences"])
    has_conflict = bool(preference["conflicting_preferences"])
    needs_verification = bool((preference.get("uncertainty") or {}).get(
        "needs_verification"))
    preference_valid = matches_valid and preference.get("validation_is_valid", True)
    rankable = safe and preference_valid and state in RANKABLE_STATES and has_positive \
        and not has_conflict and not hard_conflict and not needs_verification
    group = "confirmed" if rankable else "verification_required"
    if not candidate:
        _issue(issues, "match_without_policy_record", "warning", f"courses[{code}]",
               "Preference match has no Phase 4 policy record")
    if state in UNSAFE_STATES or not safe:
        reason = "policy_safety_not_confirmed"
    elif not preference_valid:
        reason = "preference_match_validation_failed"
    elif needs_verification:
        reason = "semantic_evidence_needs_verification"
    elif has_conflict:
        reason = "hard_preference_conflict" if hard_conflict else "preference_conflict"
    elif not has_positive:
        reason = "preference_relevance_not_confirmed"
    else:
        reason = "preference_match_ranked_after_policy_safety"
    matched_weight = sum(_priority_weight(item.get("priority"))
                         for item in preference["matched_preferences"])
    conflict_weight = sum(_priority_weight(item.get("priority"))
                          for item in preference["conflicting_preferences"])
    evidence_weight = _grounded_evidence_weight(preference)
    programme_weight = 4 if (
        policy.get("requirement_filter_state") == "matches_remaining_requirement" and
        policy.get("requirement_matches")) else 0
    grounded_weight = evidence_weight + programme_weight
    score = matched_weight - conflict_weight
    return {
        "course_code": code,
        "ranking_group": group,
        "ranking": {
            "score": score,
            "matched_priority_weight": matched_weight,
            "conflict_priority_weight": conflict_weight,
            "grounded_relevance_weight": grounded_weight,
            "components": [
                {"component": "matched_preference_evidence", "weight": matched_weight},
                {"component": "preference_conflicts", "weight": -conflict_weight},
                {"component": "source_field_relevance", "weight": evidence_weight},
                {"component": "programme_requirement_relevance", "weight": programme_weight},
            ],
        },
        "preference_match": preference,
        "policy": policy,
        "requirement_relationship": deepcopy(
            (candidate or {}).get("requirement_matches") or
            (candidate or {}).get("requirement_relevance") or {}),
        "eligibility_state": policy.get("eligibility_state"),
        "requirement_filter_state": policy.get("requirement_filter_state"),
        "reasons": [reason] + list(policy.get("reason_codes") or []),
        "source_references": _source_references(candidate, preference),
        "data_availability": deepcopy(preference.get("data_availability") or {}),
        "uncertainty": deepcopy(preference.get("uncertainty") or {}),
        "diagnostics": deepcopy(preference.get("diagnostics") or []),
    }


def _policy(candidate):
    candidate = candidate or {}
    return {
        "recommendation_safe": candidate.get("recommendation_safe") is True,
        "candidate_pool_state": candidate.get("candidate_pool_state"),
        "eligibility_state": candidate.get("eligibility_state"),
        "requirement_filter_state": candidate.get("requirement_filter_state"),
        "requirement_matches": deepcopy(candidate.get("requirement_matches") or []),
        "reason_codes": deepcopy(candidate.get("reason_codes") or []),
        "validation": deepcopy(candidate.get("validation") or {}),
        "eligibility_result": deepcopy(candidate.get("eligibility_result") or {}),
        "source_references": deepcopy(candidate.get("source_references") or []),
    }


def _policy_is_consistently_safe(policy, upstream_valid):
    eligibility = policy.get("eligibility_result") or {}
    validation = policy.get("validation") or {}
    return (
        upstream_valid and
        policy.get("recommendation_safe") is True and
        policy.get("candidate_pool_state") == "confirmed" and
        policy.get("eligibility_state") == "eligible" and
        eligibility.get("eligibility_state") == "eligible" and
        not eligibility.get("already_completed") and
        not eligibility.get("already_ongoing") and
        policy.get("requirement_filter_state") == "matches_remaining_requirement" and
        bool(policy.get("requirement_matches")) and
        _requirement_matches_valid(policy.get("requirement_matches")) and
        _sources_valid(policy.get("source_references")) and
        validation.get("is_valid") is True
    )


def _preference(match):
    match = match or {}
    assessments = match.get("preference_assessments") or []
    if not isinstance(assessments, list):
        assessments = []
    return {
        "match_state": match.get("match_state", "insufficient_evidence"),
        "matched_preferences": deepcopy(match.get("matched_preferences") or
                                          [item for item in assessments
                                           if item.get("assessment_state") == "matched"]),
        "unmatched_preferences": deepcopy(match.get("unmatched_preferences") or
                                            [item for item in assessments
                                             if item.get("assessment_state") == "not_matched"]),
        "conflicting_preferences": deepcopy(match.get("conflicting_preferences") or
                                              [item for item in assessments
                                               if item.get("assessment_state") == "conflict"]),
        "unknown_preferences": deepcopy(match.get("unknown_preferences") or
                                          [item for item in assessments
                                           if item.get("assessment_state") == "unassessable"]),
        "positive_evidence": deepcopy(match.get("positive_evidence") or []),
        "negative_evidence": deepcopy(match.get("negative_evidence") or []),
        "data_availability": deepcopy(match.get("data_availability") or {}),
        "uncertainty": deepcopy(match.get("uncertainty") or {}),
        "diagnostics": deepcopy(match.get("diagnostics") or []),
        "validation_is_valid": (match.get("validation") or {}).get("is_valid") is True,
    }


def _upstream_valid(value, path, issues):
    if not isinstance(value, dict):
        _issue(issues, "missing_upstream_validation", "error", path,
               f"{path} must be a validated stage result")
        return False
    validation = value.get("validation")
    if not isinstance(validation, dict) or validation.get("is_valid") is not True:
        _issue(issues, "invalid_upstream_validation", "error", path,
               f"{path} validation is missing or failed")
        return False
    return True


def _requirement_matches_valid(matches):
    if not isinstance(matches, list) or not matches:
        return False
    has_remaining = False
    for match in matches:
        if not isinstance(match, dict):
            return False
        if not all(isinstance(match.get(key), str) and match.get(key).strip()
                   for key in ("rule_id", "programme_scope", "normalized_category")):
            return False
        if not _sources_valid(match.get("sources")):
            return False
        state = match.get("requirement_state")
        if state in ("remaining", "partially_satisfied"):
            has_remaining = True
        elif state not in ("satisfied", "unevaluable"):
            return False
    return has_remaining


def _preference_sources_valid(preference):
    evidence = preference.get("positive_evidence") or []
    return bool(evidence) and all(
        isinstance(item, dict) and _sources_valid(item.get("source_references"))
        for item in evidence)


def _sources_valid(sources):
    return (isinstance(sources, list) and bool(sources) and
            all(isinstance(source, dict) and
                isinstance(source.get("source_file"), str) and
                bool(source.get("source_file").strip())
                for source in sources))


def _source_references(candidate, preference):
    references = []
    for source in ((candidate or {}).get("source_references") or []):
        if source not in references:
            references.append(deepcopy(source))
    for evidence in preference.get("positive_evidence", []) + preference.get("negative_evidence", []):
        for source in evidence.get("source_references") or []:
            if source not in references:
                references.append(deepcopy(source))
    return references


def _priority_weight(value):
    return {"low": 1, "medium": 2, "high": 3}.get(value, 0)


def _grounded_evidence_weight(preference):
    """Prefer explicit title/topic evidence over incidental body-text mentions."""
    weights = []
    for evidence in preference.get("positive_evidence") or []:
        field = evidence.get("course_field") if isinstance(evidence, dict) else None
        if not isinstance(field, str):
            continue
        key = field.casefold()
        if key.startswith("title"):
            weights.append(6)
        elif key.startswith("topics"):
            weights.append(4)
        elif key.startswith("content"):
            weights.append(2)
        else:
            weights.append(1)
    return max(weights, default=0)


def _issue(issues, code, severity, path, message):
    issue = RankingIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
