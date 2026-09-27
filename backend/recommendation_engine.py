"""Orchestrate intent, semantic matching, Phase 4 policy, and ranking."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from backend.course_semantics import CourseSemanticProfileBuilder
from backend.intent_parser import StudentIntentParser
from backend.preference_matching import CoursePreferenceMatcher
from backend.recommendation_ranking import RecommendationRanker
from backend.requirement_filter import filter_by_remaining_requirements


@dataclass(frozen=True)
class EngineIssue:
    code: str
    severity: str
    path: str
    message: str


class RecommendationEngine:
    """Run the complete recommendation pipeline without duplicating stage logic."""

    def __init__(self, intent_parser=None, semantic_builder=None,
                 preference_matcher=None, candidate_pool_builder=None,
                 ranker=None, requirement_filter=None):
        self.intent_parser = intent_parser or StudentIntentParser()
        self.semantic_builder = semantic_builder or CourseSemanticProfileBuilder()
        self.preference_matcher = preference_matcher or CoursePreferenceMatcher()
        self.candidate_pool_builder = candidate_pool_builder
        self.ranker = ranker or RecommendationRanker()
        self.requirement_filter = requirement_filter or filter_by_remaining_requirements

    def recommend(self, query, academic_history=None, academic_requirements=None,
                  restrictions_by_course=None, semantic_profiles=None,
                  candidate_pool=None, requirement_filter_result=None,
                  intent_result=None):
        issues = []
        intent = deepcopy(intent_result) if intent_result is not None \
            else self.intent_parser.parse(query)
        preferences = intent.get("preferences") if isinstance(intent, dict) else {}
        profiles_result = self._profiles(semantic_profiles, issues)
        profiles = profiles_result.get("profiles", [])
        preference_matches = self.preference_matcher.match_all(intent, profiles)
        pool = self._candidate_pool(candidate_pool, academic_history,
                                     academic_requirements, restrictions_by_course, issues)
        filtered = self._filtered(requirement_filter_result, pool,
                                  academic_requirements, issues)
        ranking = self.ranker.rank(preference_matches, filtered)
        self._validate_stage_identities(profiles, preference_matches, filtered, ranking, issues)
        diagnostics = [asdict(issue) for issue in issues]
        result = {
            "query": deepcopy(query),
            "intent": intent,
            "preferences": deepcopy(preferences),
            "semantic_profiles": {
                "count": len(profiles),
                "validation": deepcopy(profiles_result.get("validation") or {}),
                "summary": deepcopy(profiles_result.get("summary") or {}),
            },
            "preference_matches": preference_matches,
            "candidate_pool": pool,
            "requirement_filter": filtered,
            "ranking": ranking,
            "pipeline_diagnostics": diagnostics,
        }
        result["summary"] = {
            "semantic_profile_count": len(profiles),
            "preference_match_count": len(preference_matches.get("matches", [])),
            "candidate_count": _candidate_count(pool),
            "confirmed_recommendation_count": len(ranking.get("confirmed_recommendations", [])),
            "verification_required_count": len(ranking.get("verification_required", [])),
            "excluded_count": _excluded_count(pool, filtered),
        }
        result["validation"] = _validation(result, issues)
        return result

    def _profiles(self, supplied, issues):
        if supplied is None:
            try:
                supplied = self.semantic_builder.build_all()
            except Exception as error:
                _issue(issues, "semantic_profile_stage_failure", "error",
                       "semantic_profiles", str(error))
                return {"profiles": [], "validation": {"is_valid": False}}
        if isinstance(supplied, list):
            return {"profiles": deepcopy(supplied), "validation": {"is_valid": True}}
        if not isinstance(supplied, dict) or not isinstance(supplied.get("profiles"), list):
            _issue(issues, "malformed_semantic_profiles", "error", "semantic_profiles",
                   "Semantic profile stage must contain a profiles list")
            return {"profiles": [], "validation": {"is_valid": False}}
        return deepcopy(supplied)

    def _candidate_pool(self, supplied, history, requirements, restrictions, issues):
        if supplied is not None:
            return deepcopy(supplied)
        if self.candidate_pool_builder is None:
            _issue(issues, "policy_input_unavailable", "warning", "candidate_pool",
                   "Phase 4 candidate pool was not supplied")
            return {"candidates": [], "confirmed_candidates": [],
                    "verification_required_candidates": [], "excluded_candidates": [],
                    "validation": {"is_valid": False}}
        if history is None:
            _issue(issues, "academic_history_unavailable", "warning", "academic_history",
                   "Student academic history is required to build the Phase 4 pool")
            return {"candidates": [], "validation": {"is_valid": False}}
        try:
            return deepcopy(self.candidate_pool_builder.build(
                history, requirements, restrictions))
        except Exception as error:
            _issue(issues, "candidate_pool_stage_failure", "error", "candidate_pool", str(error))
            return {"candidates": [], "validation": {"is_valid": False}}

    def _filtered(self, supplied, pool, requirements, issues):
        if supplied is not None:
            return deepcopy(supplied)
        if not isinstance(pool, dict) or not pool.get("candidates") and not any(
                pool.get(name) for name in ("confirmed_candidates", "verification_required_candidates",
                                             "excluded_candidates")):
            _issue(issues, "requirement_filter_input_unavailable", "warning",
                   "requirement_filter", "Phase 4 requirement-filter input was not available")
            return {"candidates": [], "validation": {"is_valid": False}}
        if requirements is None:
            _issue(issues, "academic_requirements_unavailable", "warning",
                   "academic_requirements", "Academic requirements are required for filtering")
            return {"candidates": [], "validation": {"is_valid": False}}
        try:
            return deepcopy(self.requirement_filter(pool, requirements))
        except Exception as error:
            _issue(issues, "requirement_filter_stage_failure", "error",
                   "requirement_filter", str(error))
            return {"candidates": [], "validation": {"is_valid": False}}

    def _validate_stage_identities(self, profiles, matches, filtered, ranking, issues):
        profile_codes = _codes(profiles, "course_code")
        match_codes = _codes(matches.get("matches", []), "course_code")
        filtered_codes = _codes(filtered.get("candidates", []), "normalized_course_code")
        ranked_codes = _codes(
            ranking.get("confirmed_recommendations", []) +
            ranking.get("verification_required", []), "course_code")
        if len(profile_codes) != len(profiles):
            _issue(issues, "duplicate_semantic_profile_identity", "error",
                   "semantic_profiles", "Semantic profile identities are not unique")
        if len(match_codes) != len(matches.get("matches", [])):
            _issue(issues, "duplicate_preference_match_identity", "error",
                   "preference_matches", "Preference match identities are not unique")
        if len(filtered_codes) != len(filtered.get("candidates", [])):
            _issue(issues, "duplicate_filtered_candidate_identity", "error",
                   "requirement_filter", "Filtered candidate identities are not unique")
        if len(ranked_codes) != len(ranking.get("confirmed_recommendations", [])) + len(
                ranking.get("verification_required", [])):
            _issue(issues, "duplicate_ranked_identity", "error", "ranking",
                   "Ranked identities are not unique")
        if profile_codes != match_codes:
            _issue(issues, "semantic_match_identity_mismatch", "error",
                   "preference_matches", "Semantic profile and preference-match identities differ")
        expected_ranked = profile_codes | filtered_codes
        if ranked_codes != expected_ranked:
            _issue(issues, "ranked_identity_set_mismatch", "error", "ranking",
                   "Ranked identities do not equal the combined match and policy identities")
        confirmed_codes = _codes(ranking.get("confirmed_recommendations", []), "course_code")
        if not confirmed_codes.issubset(filtered_codes):
            _issue(issues, "confirmed_identity_missing_from_policy", "error", "ranking",
                   "A confirmed recommendation has no filtered Phase 4 identity")


def run_recommendation(query, **kwargs):
    """Convenience entry point for one end-to-end recommendation request."""
    return RecommendationEngine().recommend(query, **kwargs)


def _codes(items, key):
    return {item.get(key) for item in items if isinstance(item, dict) and item.get(key)}


def _excluded_count(pool, filtered):
    if isinstance(pool, dict) and isinstance(pool.get("excluded_candidates"), list):
        return len(pool["excluded_candidates"])
    if isinstance(filtered, dict):
        return sum(item.get("candidate_pool_state") == "excluded"
                   for item in filtered.get("candidates", []) if isinstance(item, dict))
    return 0


def _candidate_count(pool):
    if not isinstance(pool, dict):
        return 0
    if isinstance(pool.get("candidates"), list):
        return len(pool["candidates"])
    return sum(len(pool.get(name, [])) for name in (
        "confirmed_candidates", "verification_required_candidates", "excluded_candidates")
        if isinstance(pool.get(name), list))


def _validation(result, issues):
    stage_validations = []
    stage_keys = ("intent", "semantic_profiles", "preference_matches", "candidate_pool",
                  "requirement_filter", "ranking")
    for key in stage_keys:
        value = result.get(key) or {}
        validation = value.get("validation") if isinstance(value, dict) else None
        if isinstance(validation, dict):
            stage_validations.append((key, validation))
    all_issues = [asdict(issue) for issue in issues]
    for key, validation in stage_validations:
        if validation.get("is_valid") is False:
            item = {"code": "invalid_upstream_stage", "severity": "error",
                    "path": key, "message": f"{key} validation failed"}
            if item not in all_issues:
                all_issues.append(item)
        for item in validation.get("issues", []):
            if item not in all_issues:
                all_issues.append(deepcopy(item))
    errors = [item for item in all_issues if item.get("severity") == "error"]
    warnings = [item for item in all_issues if item.get("severity") == "warning"]
    return {"is_valid": not errors, "issues": all_issues,
            "error_count": len(errors), "warning_count": len(warnings)}


def _issue(issues, code, severity, path, message):
    issue = EngineIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
