"""Orchestrate the complete Phase 3 academic-requirement workflow."""

from copy import deepcopy

from backend.academic_history import CourseCatalogue, resolve_academic_history
from backend.category_progress import build_category_progress
from backend.programme_requirements import AcademicRuleCatalogue, resolve_programme_requirements
from backend.requirement_completion import evaluate_requirement_completion
from backend.student_profile import StudentProfile


class AcademicRequirementService:
    """Reusable Phase 3 service backed by preloaded catalogue indexes."""

    def __init__(self, course_catalogue=None, rule_catalogue=None):
        self.course_catalogue = _course_catalogue(course_catalogue)
        self.rule_catalogue = _rule_catalogue(rule_catalogue)

    def build_summary(self, raw_profile):
        """Build a JSON-compatible academic requirement summary."""
        try:
            profile = StudentProfile.from_dict(raw_profile)
        except (TypeError, ValueError) as error:
            issue = {
                "code": "invalid_profile_input",
                "severity": "error",
                "path": "student",
                "message": str(error),
                "stages": ["student_profile"],
            }
            return _stopped_summary(None, "invalid_student_profile", [issue])

        profile_data = profile.to_dict()
        if not profile.validation.get("is_valid"):
            diagnostics = _combine_diagnostics(("student_profile", profile.validation))
            return _stopped_summary(profile_data, "invalid_student_profile", diagnostics)

        history = resolve_academic_history(profile, self.course_catalogue)
        requirements = resolve_programme_requirements(profile, self.rule_catalogue)
        validations = [
            ("student_profile", profile.validation),
            ("academic_history", history.get("validation")),
            ("programme_requirements", requirements.get("validation")),
        ]

        if not (requirements.get("validation") or {}).get("is_valid", False):
            diagnostics = _combine_diagnostics(*validations)
            result = _base_result(profile_data)
            result["status"] = "unresolved_programme"
            result["academic_history"] = _history_summary(history)
            result["requirement_progress"] = _empty_progress()
            result["programme_resolution"] = deepcopy(
                requirements.get("programme_resolutions") or [])
            result["descriptive_information"] = deepcopy(
                requirements.get("applicable_descriptive_rules") or [])
            result["incomplete_data"] = _incomplete_data(
                requirements.get("incomplete_data"), "programme_resolution_failed")
            result["summary"] = _summary(result)
            result["validation"] = _validation(diagnostics)
            return result

        completion = evaluate_requirement_completion(history, requirements)
        progress = build_category_progress(completion)
        validations.extend([
            ("requirement_completion", completion.get("validation")),
            ("category_progress", progress.get("validation")),
        ])
        diagnostics = _combine_diagnostics(*validations)

        result = _base_result(profile_data)
        result["status"] = "complete" if not progress["incomplete_data"].get(
            "has_incomplete_data") else "complete_with_incomplete_data"
        result["academic_history"] = _history_summary(history)
        result["requirement_progress"] = {
            "programme_progress": deepcopy(progress["programme_progress"]),
            "unresolved_requirements": deepcopy(progress["unresolved_requirements"]),
            "duplicate_requirements": deepcopy(progress["duplicate_requirements"]),
        }
        result["programme_resolution"] = deepcopy(
            requirements.get("programme_resolutions") or [])
        result["descriptive_information"] = deepcopy(
            progress.get("descriptive_information") or [])
        result["incomplete_data"] = deepcopy(progress.get("incomplete_data") or {})
        result["incomplete_data"]["excluded_unresolved_rules"] = deepcopy(
            progress.get("excluded_unresolved_rules") or [])
        result["summary"] = _summary(result)
        result["validation"] = _validation(diagnostics)
        return result


def build_academic_requirement_summary(raw_profile, course_catalogue=None,
                                       rule_catalogue=None):
    """Convenience interface for a single Phase 3 summary request."""
    return AcademicRequirementService(course_catalogue, rule_catalogue).build_summary(raw_profile)


def _course_catalogue(value):
    if value is None:
        return CourseCatalogue.load()
    if isinstance(value, CourseCatalogue):
        return value
    return CourseCatalogue.from_dict(value)


def _rule_catalogue(value):
    if value is None:
        return AcademicRuleCatalogue.load()
    if isinstance(value, AcademicRuleCatalogue):
        return value
    return AcademicRuleCatalogue.from_dict(value)


def _base_result(profile):
    student = None
    if profile is not None:
        student = {key: profile.get(key) for key in (
            "programme", "second_programme", "current_academic_year", "current_semester")}
    return {
        "status": None,
        "student": student,
        "academic_history": _empty_history(),
        "programme_resolution": [],
        "requirement_progress": _empty_progress(),
        "descriptive_information": [],
        "incomplete_data": {},
        "summary": {},
        "validation": {},
    }


def _stopped_summary(profile, reason, diagnostics):
    result = _base_result(profile)
    result["status"] = reason
    result["incomplete_data"] = {
        "has_incomplete_data": True,
        "calculation_stopped_reason": reason,
    }
    result["summary"] = _summary(result)
    result["validation"] = _validation(diagnostics)
    return result


def _history_summary(history):
    completed = deepcopy(history.get("completed_courses") or [])
    ongoing = deepcopy(history.get("ongoing_courses") or [])
    unresolved = [
        {"course_code": item.get("course_code"),
         "normalized_course_code": item.get("normalized_course_code"),
         "status": item.get("status"),
         "resolution_status": item.get("resolution_status")}
        for item in completed + ongoing if item.get("resolution_status") != "matched"
    ]
    return {
        "completed_courses": completed,
        "ongoing_courses": ongoing,
        "unresolved_courses": unresolved,
    }


def _empty_history():
    return {"completed_courses": [], "ongoing_courses": [], "unresolved_courses": []}


def _empty_progress():
    return {
        "programme_progress": [],
        "unresolved_requirements": [],
        "duplicate_requirements": [],
    }


def _incomplete_data(upstream, stopped_reason):
    result = deepcopy(upstream or {})
    result["has_incomplete_data"] = True
    result["calculation_stopped_reason"] = stopped_reason
    return result


def _summary(result):
    history = result["academic_history"]
    programmes = result["requirement_progress"]["programme_progress"]
    counts = {state: 0 for state in (
        "satisfied", "partially_satisfied", "remaining", "unevaluable")}
    category_count = 0
    uncategorized_count = 0
    for programme in programmes:
        categories = programme.get("categories") or []
        category_count += len(categories)
        uncategorized_count += len(programme.get("uncategorized_requirements") or [])
        for category in categories:
            for state in counts:
                counts[state] += len(category.get(f"{state}_requirements") or [])
        for requirement in programme.get("uncategorized_requirements") or []:
            state = requirement.get("completion_status")
            if state in counts:
                counts[state] += 1
    return {
        "completed_course_count": len(history["completed_courses"]),
        "ongoing_course_count": len(history["ongoing_courses"]),
        "unresolved_course_count": len(history["unresolved_courses"]),
        "satisfied_requirement_count": counts["satisfied"],
        "partially_satisfied_requirement_count": counts["partially_satisfied"],
        "remaining_requirement_count": counts["remaining"],
        "unevaluable_requirement_count": counts["unevaluable"],
        "explicit_category_count": category_count,
        "uncategorized_requirement_count": uncategorized_count,
        "has_incomplete_data": bool(result["incomplete_data"].get("has_incomplete_data")),
    }


def _combine_diagnostics(*stage_validations):
    combined = {}
    for stage, validation in stage_validations:
        if not isinstance(validation, dict):
            continue
        issues = validation.get("issues") or []
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            key = tuple(_stable(issue.get(field)) for field in (
                "code", "severity", "path", "message"))
            if key not in combined:
                preserved = {field: deepcopy(issue.get(field)) for field in (
                    "code", "severity", "path", "message")}
                preserved["stages"] = [stage]
                combined[key] = preserved
            elif stage not in combined[key]["stages"]:
                combined[key]["stages"].append(stage)
    return list(combined.values())


def _stable(value):
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return repr(value)


def _validation(diagnostics):
    errors = [item for item in diagnostics if item.get("severity") == "error"]
    warnings = [item for item in diagnostics if item.get("severity") == "warning"]
    return {
        "is_valid": not errors,
        "status": "invalid" if errors else ("valid_with_warnings" if warnings else "valid"),
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
