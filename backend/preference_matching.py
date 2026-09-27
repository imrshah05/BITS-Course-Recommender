"""Match student preferences to source-backed course semantic profiles."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import re

from backend.student_preferences import normalize_student_preferences


POSITIVE_CATEGORIES = ("interests", "preferred_topics")
PRIORITY_WEIGHTS = {"low": 1, "medium": 2, "high": 3}
FORBIDDEN_POLICY_FIELDS = (
    "eligibility_state", "prerequisite_state", "recommendation_safe",
    "candidate_pool_state", "requirement_filter_state",
)


@dataclass(frozen=True)
class PreferenceMatchIssue:
    code: str
    severity: str
    path: str
    message: str


class MatchingStrategy:
    """Provider-independent interface for matching a phrase to course fields."""

    name = "custom"

    def find_matches(self, phrase, fields):
        raise NotImplementedError


class LexicalMatchingStrategy(MatchingStrategy):
    """Token-boundary-aware literal matching without synonym expansion."""

    name = "literal_lexical"

    def find_matches(self, phrase, fields):
        wanted = _tokens(phrase)
        if not wanted:
            return []
        matches = []
        for field in fields:
            text = field.get("text")
            tokens = _tokens(text)
            if any(tokens[index:index + len(wanted)] == wanted
                   for index in range(len(tokens) - len(wanted) + 1)):
                matches.append({
                    "course_field": field["course_field"],
                    "matched_text": _excerpt(text, wanted),
                    "source_references": deepcopy(field.get("sources") or []),
                })
        return matches


class CoursePreferenceMatcher:
    """Evaluate preference relevance independently of academic eligibility."""

    def __init__(self, strategy=None):
        self.strategy = strategy or LexicalMatchingStrategy()

    def match_one(self, preferences, semantic_profile):
        issues = []
        model, preference_evidence = _preference_model(preferences, issues)
        profile = _semantic_profile(semantic_profile, issues)
        assessments = []
        if profile is not None:
            descriptive = _descriptive_fields(profile)
            evidence_available = bool(descriptive)
            for category in POSITIVE_CATEGORIES + ("avoided_topics",):
                for item in model.get(category, []):
                    matches = self.strategy.find_matches(item["value"], descriptive)
                    if matches:
                        state = "conflict" if category == "avoided_topics" else "matched"
                        reason = ("avoided_topic_present" if state == "conflict"
                                  else "literal_descriptive_match")
                    elif evidence_available:
                        state, reason = "not_matched", "literal_phrase_absent"
                    else:
                        state, reason = "unassessable", "descriptive_evidence_unavailable"
                    assessments.append(_assessment(category, item, state, reason, matches))
            for item in model.get("career_goals", []):
                assessments.append(_assessment(
                    "career_goals", item, "unassessable",
                    "career_relationship_not_available", []))
            workload = model.get("workload_preference") or {}
            if workload.get("value") not in (None, "unspecified"):
                assessments.append(_assessment(
                    "workload_preference", workload, "unassessable",
                    "workload_evidence_not_available", []))
            assessments.extend(_evaluation_assessments(model, profile, self.strategy))

        _attach_preference_evidence(assessments, preference_evidence)

        result = _result(profile, assessments, self.strategy.name, issues)
        result["validation"] = validate_preference_match(result, model, profile)
        return result

    def match_all(self, preferences, semantic_profiles):
        issues = []
        begin_batch = getattr(self.strategy, "begin_batch", None)
        if callable(begin_batch):
            begin_batch()
        if isinstance(semantic_profiles, dict):
            profiles = semantic_profiles.get("profiles")
        else:
            profiles = semantic_profiles
        if not isinstance(profiles, list):
            profiles = []
            _issue(issues, "malformed_semantic_profile_collection", "error", "profiles",
                   "Semantic profiles must be a list or a profiles collection")
        matches = [self.match_one(preferences, profile) for profile in profiles]
        seen = set()
        duplicates = []
        for index, match in enumerate(matches):
            code = match.get("course_code")
            if code and code in seen:
                duplicates.append(code)
                _issue(issues, "duplicate_course_match_identity", "error",
                       f"matches[{index}].course_code", f"Duplicate course identity {code}")
            seen.add(code)
        return {
            "matches": matches,
            "matching_strategy": self.strategy.name,
            "validation": {
                "is_valid": not any(issue.severity == "error" for issue in issues),
                "issues": [asdict(issue) for issue in issues],
                "duplicate_course_codes": sorted(set(duplicates)),
            },
        }


def match_course_preferences(preferences, semantic_profile, strategy=None):
    """Match one preference model and semantic profile."""
    return CoursePreferenceMatcher(strategy).match_one(preferences, semantic_profile)


def validate_preference_match(result, preferences, profile):
    """Validate identity, preference provenance, evidence, and scope boundaries."""
    issues = [PreferenceMatchIssue(
        item.get("code", "matcher_diagnostic"),
        item.get("severity", "error"),
        item.get("path", "matcher"),
        item.get("message", "Preference matcher diagnostic"),
    ) for item in result.get("diagnostics", []) if isinstance(item, dict)]
    if profile is None:
        _issue(issues, "malformed_semantic_profile", "error", "semantic_profile",
               "A usable semantic profile is required")
    elif result.get("course_code") != profile.get("course_code"):
        _issue(issues, "course_identity_mismatch", "error", "course_code",
               "Match identity does not equal semantic profile identity")
    supplied = {
        (category, item.get("value"))
        for category in POSITIVE_CATEGORIES + ("avoided_topics", "career_goals")
        for item in preferences.get(category, []) if isinstance(item, dict)
    }
    workload = preferences.get("workload_preference") or {}
    if workload.get("value") not in (None, "unspecified"):
        supplied.add(("workload_preference", workload.get("value")))
    supplied.update(
        ("evaluation_preferences." + key, option.get("value"))
        for key, option in (preferences.get("evaluation_preferences") or {}).items()
        if isinstance(option, dict))
    profile_sources = _all_sources(profile) if profile else []
    for index, assessment in enumerate(result.get("preference_assessments") or []):
        path = f"preference_assessments[{index}]"
        key = (assessment.get("preference_category"), assessment.get("value"))
        if key not in supplied:
            _issue(issues, "preference_not_supplied", "error", path,
                   "Assessment was not derived from the supplied preference model")
        state = assessment.get("assessment_state")
        evidence = assessment.get("evidence") or []
        if state in ("matched", "conflict") and not evidence:
            _issue(issues, "positive_or_negative_evidence_missing", "error", path,
                   "Matched and conflicting assessments require evidence")
        if state == "unassessable" and evidence:
            _issue(issues, "unknown_evidence_classified_as_match", "error", path,
                   "Unassessable preferences cannot carry match evidence")
        if assessment.get("preference_category") == "avoided_topics" and state == "matched":
            _issue(issues, "avoided_topic_classified_positive", "error", path,
                   "Avoided-topic evidence must be a conflict")
        for match in evidence:
            for source in match.get("source_references") or []:
                if source not in profile_sources:
                    _issue(issues, "untraceable_match_source", "error", path,
                           "Match source does not occur in the semantic profile")
    if any(key in result for key in FORBIDDEN_POLICY_FIELDS):
        _issue(issues, "phase_four_policy_field_created", "error", "result",
               "Preference matching must not create Phase 4 policy fields")
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {"is_valid": not errors, "issues": errors + warnings,
            "error_count": len(errors), "warning_count": len(warnings)}


def _preference_model(raw, issues):
    if not isinstance(raw, dict):
        _issue(issues, "malformed_preference_model", "error", "preferences",
               "Preference model must be a mapping")
        return normalize_student_preferences({}), []
    extraction_evidence = raw.get("extraction_evidence", [])
    if isinstance(raw.get("preferences"), dict):
        raw = raw["preferences"]
    if not isinstance(extraction_evidence, list):
        extraction_evidence = []
    allowed = {"interests", "preferred_topics", "avoided_topics", "career_goals",
               "workload_preference", "evaluation_preferences", "raw_query"}
    try:
        model = normalize_student_preferences({key: deepcopy(value) for key, value in raw.items()
                                               if key in allowed})
    except (TypeError, ValueError) as error:
        _issue(issues, "malformed_preference_model", "error", "preferences", str(error))
        return normalize_student_preferences({}), extraction_evidence
    if not model["validation"]["is_valid"]:
        _issue(issues, "invalid_preference_model", "error", "preferences",
               "Preference model failed Task 5.1 validation")
    return model, deepcopy(extraction_evidence)


def _attach_preference_evidence(assessments, extraction_evidence):
    for assessment in assessments:
        category = assessment["preference_category"]
        value = str(assessment.get("value")).casefold()
        matches = []
        for evidence in extraction_evidence:
            if not isinstance(evidence, dict):
                continue
            evidence_category = evidence.get("preference_type")
            evidence_value = str(evidence.get("value")).casefold()
            if evidence_category == category and evidence_value == value:
                matches.append(deepcopy(evidence))
        assessment["preference_extraction_evidence"] = matches
        assessment["extraction_status"] = (
            matches[0].get("status") if matches else "not_provided")


def _semantic_profile(raw, issues):
    if not isinstance(raw, dict):
        _issue(issues, "malformed_semantic_profile", "error", "semantic_profile",
               "Semantic profile must be a mapping")
        return None
    code = raw.get("course_code")
    if not isinstance(code, str) or not code.strip():
        _issue(issues, "semantic_profile_identity_missing", "error", "course_code",
               "Semantic profile must preserve a course identity")
        return None
    return deepcopy(raw)


def _descriptive_fields(profile):
    fields = []
    title = profile.get("title") or {}
    for value in title.get("values") or []:
        if isinstance(value, str) and value.strip():
            fields.append({"course_field": "title", "text": value,
                           "sources": deepcopy(title.get("sources") or [])})
    for name in ("topics", "content"):
        for item in profile.get(name) or []:
            if not isinstance(item, dict):
                continue
            for key in ("heading", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    fields.append({"course_field": f"{name}.{key}", "text": value,
                                   "sources": deepcopy(item.get("sources") or [])})
    return _deduplicate_fields(fields)


def _evaluation_assessments(model, profile, strategy):
    results = []
    evaluation_fields = _object_fields(profile.get("evaluation") or [], "evaluation")
    exam = profile.get("exam_information") or {}
    policy = profile.get("attendance_or_makeup") or {}
    for key, option in (model.get("evaluation_preferences") or {}).items():
        category = "evaluation_preferences." + key
        if not option.get("value"):
            results.append(_assessment(category, option, "unassessable",
                                       "inactive_boolean_preference", []))
            continue
        if key == "prefer_projects":
            results.append(_literal_evaluation(category, option, "project",
                                                evaluation_fields, strategy))
        elif key == "prefer_quizzes":
            results.append(_literal_evaluation(category, option, "quiz",
                                                evaluation_fields, strategy))
        elif key == "prefer_continuous_evaluation":
            results.append(_literal_evaluation(category, option, "continuous evaluation",
                                                evaluation_fields, strategy))
        elif key in ("avoid_heavy_exams", "avoid_attendance_heavy"):
            results.append(_assessment(category, option, "unassessable",
                                       "intensity_not_deterministically_available", []))
        elif key in ("avoid_midsemester_exam", "avoid_comprehensive_exam"):
            exam_key = "midsemester" if key == "avoid_midsemester_exam" else "comprehensive"
            fields = _object_fields(exam.get(exam_key) or [], f"exam_information.{exam_key}")
            if fields:
                evidence = [{"course_field": item["course_field"],
                             "matched_text": _excerpt(item["text"], _tokens(item["text"])),
                             "source_references": deepcopy(item.get("sources") or [])}
                            for item in fields[:1]]
                results.append(_assessment(category, option, "conflict",
                                           "explicit_exam_present", evidence))
            else:
                results.append(_assessment(category, option, "unassessable",
                                           "exam_evidence_unavailable", []))
    return results


def _literal_evaluation(category, option, phrase, fields, strategy):
    if not fields:
        return _assessment(category, option, "unassessable",
                           "evaluation_evidence_unavailable", [])
    matches = strategy.find_matches(phrase, fields)
    return _assessment(category, option, "matched" if matches else "not_matched",
                       "literal_evaluation_match" if matches else "literal_phrase_absent",
                       matches)


def _object_fields(value, prefix):
    fields = []
    if isinstance(value, dict):
        sources = value.get("sources") or []
        for key, item in value.items():
            if key == "sources":
                continue
            if isinstance(item, str) and item.strip():
                fields.append({"course_field": f"{prefix}.{key}", "text": item,
                               "sources": deepcopy(sources)})
            else:
                fields.extend(_object_fields(item, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            fields.extend(_object_fields(item, f"{prefix}[{index}]"))
    return fields


def _assessment(category, item, state, reason, evidence):
    return {
        "preference_category": category,
        "value": item.get("value"),
        "original_value": item.get("original_value", item.get("value")),
        "priority": item.get("priority", "medium"),
        "constraint": item.get("constraint", "soft"),
        "assessment_state": state,
        "reason_code": reason,
        "evidence": evidence,
    }


def _result(profile, assessments, strategy, issues):
    matched = [item for item in assessments if item["assessment_state"] == "matched"]
    conflicts = [item for item in assessments if item["assessment_state"] == "conflict"]
    unmatched = [item for item in assessments if item["assessment_state"] == "not_matched"]
    unknown = [item for item in assessments if item["assessment_state"] == "unassessable"]
    positive = [item for item in assessments
                if item["preference_category"] in POSITIVE_CATEGORIES or
                item["preference_category"].startswith("evaluation_preferences.prefer_")]
    assessable_positive = [item for item in positive
                           if item["assessment_state"] != "unassessable"]
    matched_weight = sum(PRIORITY_WEIGHTS[item["priority"]] for item in matched)
    assessable_weight = sum(PRIORITY_WEIGHTS[item["priority"]]
                            for item in assessable_positive)
    if conflicts:
        state = "conflict"
    elif assessments and all(item["assessment_state"] == "matched"
                             for item in assessments):
        state = "strong_match"
    elif matched:
        state = "partial_match"
    elif assessable_positive:
        state = "no_match"
    else:
        state = "insufficient_evidence"
    uncertainty = deepcopy((profile or {}).get("uncertainty") or {})
    return {
        "course_code": (profile or {}).get("course_code"),
        "match_state": state,
        "preference_assessments": assessments,
        "matched_preferences": matched,
        "unmatched_preferences": unmatched,
        "conflicting_preferences": conflicts,
        "unknown_preferences": unknown,
        "positive_evidence": [evidence for item in matched for evidence in item["evidence"]],
        "negative_evidence": [evidence for item in conflicts for evidence in item["evidence"]],
        "source_references": _merge_sources(
            [source for item in matched + conflicts for evidence in item["evidence"]
             for source in evidence.get("source_references") or []]),
        "data_availability": deepcopy((profile or {}).get("data_availability") or {}),
        "uncertainty": uncertainty,
        "aggregation": {
            "matched_priority_weight": matched_weight,
            "assessable_positive_priority_weight": assessable_weight,
            "has_hard_conflict": any(item["constraint"] == "hard" for item in conflicts),
        },
        "matching_strategy": strategy,
        "diagnostics": [asdict(issue) for issue in issues],
    }


def _tokens(value):
    if not isinstance(value, str):
        return []
    return re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)


def _excerpt(text, wanted, limit=180):
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    normalized = _tokens(cleaned)
    start = 0
    for index in range(len(normalized) - len(wanted) + 1):
        if normalized[index:index + len(wanted)] == wanted:
            start = max(0, cleaned.casefold().find(wanted[0]) - 40)
            break
    excerpt = cleaned[start:start + limit]
    return ("…" if start else "") + excerpt + ("…" if start + limit < len(cleaned) else "")


def _deduplicate_fields(fields):
    result = []
    seen = set()
    for field in fields:
        signature = (field["course_field"], field["text"], repr(field.get("sources")))
        if signature not in seen:
            seen.add(signature)
            result.append(field)
    return result


def _all_sources(value):
    sources = []
    if isinstance(value, dict):
        if isinstance(value.get("sources"), list):
            sources.extend(value["sources"])
        for item in value.values():
            sources.extend(_all_sources(item))
    elif isinstance(value, list):
        for item in value:
            sources.extend(_all_sources(item))
    return _merge_sources(sources)


def _merge_sources(sources):
    result = []
    for source in sources:
        if source not in result:
            result.append(deepcopy(source))
    return result


def _issue(issues, code, severity, path, message):
    issue = PreferenceMatchIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
