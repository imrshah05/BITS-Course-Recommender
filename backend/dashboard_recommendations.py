"""Dashboard-facing integration of the existing academic and recommendation pipeline."""

from copy import deepcopy

from backend.course_catalogue import CourseCatalogue
from backend.course_policy import CoursePolicyService
from backend.course_semantics import CourseSemanticProfileBuilder
from backend.gemini import configured_recommendation_engine
from backend.recommendation_explanations import RecommendationExplanationService
from backend.student_profile import normalize_student_profile


class DashboardRecommendationService:
    """Run one profile and query through deterministic policy and preference ranking."""

    def __init__(self, course_catalogue=None, policy_service=None,
                 semantic_builder=None, recommendation_engine=None,
                 explanation_service=None):
        self.course_catalogue = course_catalogue or CourseCatalogue.load()
        self.policy_service = policy_service or CoursePolicyService(
            course_catalogue=self.course_catalogue)
        self.semantic_builder = semantic_builder or CourseSemanticProfileBuilder(
            self.course_catalogue)
        self.recommendation_engine = (
            recommendation_engine or configured_recommendation_engine())
        self.explanation_service = explanation_service or RecommendationExplanationService()

    def recommend(self, raw_profile, query):
        """Return dashboard-ready recommendations without weakening policy decisions."""
        profile = normalize_student_profile(
            raw_profile, self.course_catalogue.course_codes())
        if not profile["validation"]["is_valid"]:
            return _invalid_profile_result(profile, query)

        policy = self.policy_service.evaluate(profile)
        safe_candidates = deepcopy(policy.get("recommendation_safe_candidates") or [])
        safe_codes = {
            item.get("normalized_course_code") for item in safe_candidates
            if isinstance(item, dict) and item.get("normalized_course_code")
        }
        semantic_profiles = [
            semantic for code in sorted(safe_codes)
            for semantic in [self.semantic_builder.build_one(code)]
            if semantic is not None
        ]
        safe_filter = {
            "candidates": safe_candidates,
            "validation": deepcopy(policy.get("validation") or {"is_valid": False}),
        }
        recommendation = self.recommendation_engine.recommend(
            query,
            semantic_profiles={
                "profiles": semantic_profiles,
                "validation": {"is_valid": True, "issues": []},
            },
            candidate_pool={"candidates": safe_candidates,
                            "validation": {"is_valid": True, "issues": []}},
            requirement_filter_result=safe_filter,
        )

        confirmed = deepcopy(
            recommendation.get("ranking", {}).get("confirmed_recommendations") or [])
        ranked_verification = deepcopy(
            recommendation.get("ranking", {}).get("verification_required") or [])
        policy_verification = _policy_verification(policy, safe_codes)
        verification = _merge_verification(ranked_verification, policy_verification)
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
            "excluded_courses": excluded,
            "pipeline": {
                "course_policy_status": policy.get("status"),
                "course_policy_summary": deepcopy(policy.get("summary") or {}),
                "recommendation_summary": deepcopy(
                    recommendation.get("summary") or {}),
                "incomplete_data": deepcopy(policy.get("incomplete_data") or {}),
            },
            "summary": {
                "confirmed_recommendation_count": len(confirmed),
                "verification_required_count": len(verification),
                "excluded_course_count": len(excluded),
            },
            "validation": validation,
        }
        return self.explanation_service.explain(result)


def build_dashboard_recommendations(raw_profile, query, **kwargs):
    """Convenience entry point for one dashboard recommendation request."""
    return DashboardRecommendationService(**kwargs).recommend(raw_profile, query)


def _policy_verification(policy, safe_codes):
    records = []
    for candidate in policy.get("all_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        code = candidate.get("normalized_course_code")
        if code in safe_codes or candidate.get("candidate_pool_state") == "excluded":
            continue
        records.append({
            "course_code": code,
            "ranking_group": "verification_required",
            "reasons": deepcopy(candidate.get("reason_codes") or []) + [
                "phase4_recommendation_safety_not_confirmed"],
            "source_references": deepcopy(candidate.get("source_references") or []),
            "eligibility_state": candidate.get("eligibility_state"),
            "requirement_filter_state": candidate.get("requirement_filter_state"),
            "policy": deepcopy(candidate),
        })
    return records


def _merge_verification(ranked, policy):
    merged = {}
    for item in ranked + policy:
        if not isinstance(item, dict):
            continue
        code = item.get("course_code") or item.get("normalized_course_code")
        if code and code not in merged:
            merged[code] = deepcopy(item)
    return [merged[code] for code in sorted(merged)]


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
        "excluded_courses": [],
        "pipeline": {"course_policy_status": "not_run",
                     "course_policy_summary": {}, "recommendation_summary": {},
                     "incomplete_data": {"has_incomplete_data": True}},
        "summary": {"confirmed_recommendation_count": 0,
                    "verification_required_count": 0, "excluded_course_count": 0},
        "validation": {
            "is_valid": False,
            "issues": deepcopy(profile["validation"].get("issues") or []),
            "error_count": profile["validation"].get("error_count", 0),
            "warning_count": profile["validation"].get("warning_count", 0),
        },
    }
