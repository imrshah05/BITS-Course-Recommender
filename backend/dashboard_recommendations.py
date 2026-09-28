"""Dashboard-facing integration of the existing academic and recommendation pipeline."""

from copy import deepcopy
import re

from backend.course_catalogue import CourseCatalogue
from backend.course_policy import CoursePolicyService
from backend.course_semantics import (
    CourseSemanticProfileBuilder, UnifiedCourseSemanticProfileBuilder)
from backend.gemini import configured_recommendation_engine
from backend.recommendation_explanations import RecommendationExplanationService
from backend.student_profile import normalize_student_profile
from backend.source_course_catalogue import SourceCourseCatalogue


class DashboardRecommendationService:
    """Run one profile and query through deterministic policy and preference ranking."""

    def __init__(self, course_catalogue=None, policy_service=None,
                 semantic_builder=None, source_catalogue=None,
                 recommendation_engine=None,
                 explanation_service=None):
        injected_handout_catalogue = course_catalogue is not None
        self.course_catalogue = course_catalogue or CourseCatalogue.load_source_backed()
        self.source_catalogue = source_catalogue or (
            _CourseCodeSourceCatalogue(self.course_catalogue)
            if injected_handout_catalogue else SourceCourseCatalogue.load())
        self.policy_service = policy_service or CoursePolicyService(
            course_catalogue=self.course_catalogue)
        self.semantic_builder = semantic_builder or UnifiedCourseSemanticProfileBuilder(
            self.source_catalogue, CourseSemanticProfileBuilder(self.course_catalogue))
        self.recommendation_engine = (
            recommendation_engine or configured_recommendation_engine())
        self.explanation_service = explanation_service or RecommendationExplanationService()

    def recommend(self, raw_profile, query):
        """Return dashboard-ready recommendations without weakening policy decisions."""
        profile = normalize_student_profile(
            raw_profile, self.source_catalogue.course_codes())
        if not profile["validation"]["is_valid"]:
            return _invalid_profile_result(profile, query)

        policy = self.policy_service.evaluate(profile)
        policy_candidates = {
            item.get("normalized_course_code"): deepcopy(item)
            for item in policy.get("all_candidates") or []
            if isinstance(item, dict) and item.get("normalized_course_code")
        }
        excluded_codes = {
            item.get("normalized_course_code") for item in policy.get("excluded_candidates") or []
            if isinstance(item, dict) and item.get("normalized_course_code")
        }
        discovery_codes = [code for code in self.source_catalogue.course_codes()
                           if code not in excluded_codes]
        semantic_profiles = [
            semantic for code in sorted(discovery_codes)
            for semantic in [self.semantic_builder.build_one(code)]
            if semantic is not None
        ]
        semantic_by_code = {item["course_code"]: item for item in semantic_profiles}
        all_discovery_candidates = [
            policy_candidates.get(code) or _unresolved_policy_candidate(
                code, semantic_by_code.get(code))
            for code in sorted(semantic_by_code)
        ]
        intent = self.recommendation_engine.intent_parser.parse(query)
        semantic_profiles = _discovery_subset(semantic_profiles, intent)
        discovery_codes = {item["course_code"] for item in semantic_profiles}
        discovery_candidates = [item for item in all_discovery_candidates
                                if item.get("normalized_course_code") in discovery_codes]
        discovery_filter = {
            "candidates": discovery_candidates,
            "validation": deepcopy(policy.get("validation") or {"is_valid": False}),
        }
        recommendation = self.recommendation_engine.recommend(
            query,
            semantic_profiles={
                "profiles": semantic_profiles,
                "validation": {"is_valid": True, "issues": []},
            },
            candidate_pool={"candidates": discovery_candidates,
                            "validation": {"is_valid": True, "issues": []}},
            requirement_filter_result=discovery_filter,
            intent_result=intent,
        )

        confirmed = deepcopy(
            recommendation.get("ranking", {}).get("confirmed_recommendations") or [])
        ranked_verification = deepcopy(
            recommendation.get("ranking", {}).get("verification_required") or [])
        verification = [item for item in ranked_verification
                        if _preference_relevant(item)]
        backlog = [_backlog_item(item) for item in all_discovery_candidates
                   if item.get("recommendation_safe") is not True]
        excluded = deepcopy(policy.get("excluded_candidates") or [])

        titles = {profile["course_code"]: profile.get("title", {}).get("display_value")
                  for profile in semantic_profiles}
        for item in confirmed + verification:
            if isinstance(item, dict) and not item.get("course_title"):
                item["course_title"] = titles.get(item.get("course_code")) or _course_title(
                    item.get("policy") or {})

        validation = _validation(profile, policy, recommendation)
        result = {
            "student_profile": profile,
            "academic_requirements": deepcopy(
                policy.get("academic_requirement_summary") or {}),
            "intent": deepcopy(recommendation.get("intent") or {}),
            "confirmed_recommendations": confirmed,
            "verification_required": verification,
            "academic_verification_backlog": backlog,
            "excluded_courses": excluded,
            "pipeline": {
                "course_policy_status": policy.get("status"),
                "course_policy_summary": deepcopy(policy.get("summary") or {}),
                "recommendation_summary": deepcopy(
                    recommendation.get("summary") or {}),
                "academic_verification_backlog_count": len(backlog),
                "incomplete_data": deepcopy(policy.get("incomplete_data") or {}),
            },
            "summary": {
                "confirmed_recommendation_count": len(confirmed),
                "verification_required_count": len(verification),
                "academic_verification_backlog_count": len(backlog),
                "excluded_course_count": len(excluded),
            },
            "validation": validation,
        }
        return self.explanation_service.explain(result)


def build_dashboard_recommendations(raw_profile, query, **kwargs):
    """Convenience entry point for one dashboard recommendation request."""
    return DashboardRecommendationService(**kwargs).recommend(raw_profile, query)


def _preference_relevant(item):
    preference = item.get("preference_match") if isinstance(item, dict) else None
    if not isinstance(preference, dict):
        return False
    return bool(preference.get("matched_preferences") or
                preference.get("conflicting_preferences"))


def _discovery_subset(profiles, intent):
    """Use explicit source text to bound semantic matching before Gemini calls."""
    preferences = intent.get("preferences") if isinstance(intent, dict) else {}
    phrases = []
    for category in ("interests", "preferred_topics", "avoided_topics"):
        for item in (preferences or {}).get(category) or []:
            value = item.get("value") if isinstance(item, dict) else item
            tokens = _tokens(value)
            if tokens:
                phrases.append(tokens)
    if not phrases:
        return profiles
    selected = []
    for profile in profiles:
        searchable = _tokens(_profile_searchable_text(profile))
        token_set = set(searchable)
        if any(_contains_tokens(searchable, phrase) or
               any(len(token) >= 4 and token in token_set for token in phrase)
               for phrase in phrases):
            selected.append(profile)
    return selected


def _profile_searchable_text(profile):
    value = profile.get("searchable_text") if isinstance(profile, dict) else None
    if isinstance(value, str) and value.strip():
        return value
    values = [profile.get("course_code", "")]
    values.extend((profile.get("title") or {}).get("values") or [])
    for field in (profile.get("content") or []) + (profile.get("topics") or []):
        if isinstance(field, dict):
            values.extend(field.get(key, "") for key in ("heading", "text"))
    return "\n".join(item for item in values if isinstance(item, str))


def _tokens(value):
    return re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE) \
        if isinstance(value, str) else []


def _contains_tokens(tokens, wanted):
    return any(tokens[index:index + len(wanted)] == wanted
               for index in range(len(tokens) - len(wanted) + 1))


def _unresolved_policy_candidate(code, semantic_profile):
    sources = deepcopy(
        ((semantic_profile or {}).get("source_evidence") or {}).get("course_identity") or [])
    return {
        "normalized_course_code": code,
        "recommendation_safe": False,
        "eligibility_state": "unknown",
        "candidate_pool_state": "verification_required",
        "requirement_filter_state": "relationship_not_established",
        "requirement_matches": [],
        "source_references": sources,
        "reason_codes": ["course_absent_from_phase4_handout_catalogue",
                         "eligibility_evidence_unavailable"],
        "eligibility_result": {
            "eligibility_state": "unknown", "already_completed": False,
            "already_ongoing": False,
        },
        "validation": {"is_valid": True, "issues": []},
        "identity_provenance": "unified_source_course_catalogue",
    }


def _backlog_item(candidate):
    return {
        "course_code": candidate.get("normalized_course_code"),
        "eligibility_state": candidate.get("eligibility_state"),
        "requirement_filter_state": candidate.get("requirement_filter_state"),
        "reasons": deepcopy(candidate.get("reason_codes") or []),
        "source_references": deepcopy(candidate.get("source_references") or []),
    }


class _CourseCodeSourceCatalogue:
    """Compatibility view used by tests and explicitly injected catalogues."""

    def __init__(self, catalogue):
        self.catalogue = catalogue

    def course_codes(self):
        return self.catalogue.course_codes()


def _course_title(candidate):
    for record in candidate.get("source_records") or []:
        if not isinstance(record, dict):
            continue
        title = record.get("course_title")
        if isinstance(title, dict) and isinstance(title.get("value"), str):
            return title["value"]
    return None


def _validation(profile, policy, recommendation):
    stages = {
        "student_profile": profile.get("validation") or {},
        "course_policy": policy.get("validation") or {},
        "recommendation_engine": recommendation.get("validation") or {},
    }
    issues = []
    for stage, validation in stages.items():
        if validation.get("is_valid") is not True:
            issues.append({
                "code": "invalid_pipeline_stage",
                "severity": "error",
                "path": stage,
                "message": f"{stage} did not produce a valid result",
            })
        for issue in validation.get("issues") or []:
            if isinstance(issue, dict):
                preserved = deepcopy(issue)
                preserved["stage"] = stage
                issues.append(preserved)
    errors = [item for item in issues if item.get("severity") == "error"]
    return {"is_valid": not errors, "issues": issues,
            "error_count": len(errors),
            "warning_count": sum(item.get("severity") == "warning" for item in issues)}


def _invalid_profile_result(profile, query):
    return {
        "student_profile": profile,
        "academic_requirements": {},
        "intent": {"original_query": deepcopy(query)},
        "confirmed_recommendations": [],
        "verification_required": [],
        "academic_verification_backlog": [],
        "excluded_courses": [],
        "pipeline": {"course_policy_status": "not_run",
                     "course_policy_summary": {}, "recommendation_summary": {},
                     "academic_verification_backlog_count": 0,
                     "incomplete_data": {"has_incomplete_data": True}},
        "summary": {"confirmed_recommendation_count": 0,
                    "verification_required_count": 0,
                    "academic_verification_backlog_count": 0,
                    "excluded_course_count": 0},
        "validation": {
            "is_valid": False,
            "issues": deepcopy(profile["validation"].get("issues") or []),
            "error_count": profile["validation"].get("error_count", 0),
            "warning_count": profile["validation"].get("warning_count", 0),
        },
    }
