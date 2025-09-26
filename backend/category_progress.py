"""Summarize requirement completion results by explicit academic category."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import math


STATES = (
    "satisfied",
    "partially_satisfied",
    "remaining",
    "unevaluable",
)
COLLECTIONS = {
    "satisfied_requirements": "satisfied",
    "partially_satisfied_requirements": "partially_satisfied",
    "remaining_requirements": "remaining",
    "unevaluable_requirements": "unevaluable",
}


@dataclass(frozen=True)
class CategoryProgressIssue:
    code: str
    severity: str
    path: str
    message: str


def build_category_progress(completion):
    """Group Task 3.4 results without reinterpreting their academic meaning."""
    if not isinstance(completion, dict):
        raise TypeError("Requirement completion result must be a mapping")

    issues = []
    programmes = {}
    unresolved = []
    duplicates = []
    seen_rule_ids = set()

    for collection, expected_state in COLLECTIONS.items():
        records = completion.get(collection, [])
        if not isinstance(records, list):
            _issue(issues, "malformed_completion_collection", "error", collection,
                   "Completion collection must be a list")
            continue
        for index, record in enumerate(records):
            path = f"{collection}[{index}]"
            if not isinstance(record, dict):
                _issue(issues, "malformed_completion_record", "error", path,
                       "Completion record must be a mapping")
                unresolved.append({"record": deepcopy(record), "reason": "malformed_record"})
                continue
            record = deepcopy(record)
            state = record.get("completion_status")
            if state not in STATES:
                _issue(issues, "unexpected_completion_state", "error", path,
                       f"Unexpected completion state {state!r}")
                unresolved.append({"record": record, "reason": "unexpected_completion_state"})
                continue
            if state != expected_state:
                _issue(issues, "completion_collection_mismatch", "warning", path,
                       f"Record state {state!r} does not match its collection")

            rule_id = record.get("rule_id")
            if rule_id is not None and rule_id in seen_rule_ids:
                _issue(issues, "duplicate_requirement_id", "warning", path,
                       f"Requirement ID {rule_id!r} occurs more than once")
                duplicates.append(record)
                continue
            if rule_id is not None:
                seen_rule_ids.add(rule_id)

            role = record.get("requested_programme_role")
            programme = (record.get("scope") or {}).get("programme")
            if not isinstance(role, str) or not role.strip():
                _issue(issues, "requirement_missing_programme_role", "warning", path,
                       "Requirement has no requested programme role")
            if not isinstance(programme, str) or not programme.strip():
                _issue(issues, "requirement_missing_programme_scope", "warning", path,
                       "Requirement has no reliable programme scope")
            if not isinstance(role, str) or not role.strip() or not isinstance(programme, str) or not programme.strip():
                unresolved.append({"record": record, "reason": "missing_programme_context"})
                continue

            key = (role, programme)
            group = programmes.setdefault(key, _programme(role, programme))
            category = record.get("normalized_category")
            if category is None:
                group["uncategorized_requirements"].append(record)
                continue
            if not isinstance(category, str) or not category.strip():
                _issue(issues, "invalid_explicit_category", "warning", path,
                       "Explicit normalized category is empty or invalid")
                group["uncategorized_requirements"].append(record)
                continue
            category_group = group["_categories"].setdefault(
                category, _category(category))
            source_category = record.get("category")
            if isinstance(source_category, str) and source_category.strip():
                category_group["source_categories"].add(source_category)
            category_group[f"{state}_requirements"].append(_course_progress(record))

    output_programmes = []
    for group in programmes.values():
        categories = []
        for category in group.pop("_categories").values():
            category["source_categories"] = sorted(category["source_categories"])
            category["counts"] = {
                state: len(category[f"{state}_requirements"]) for state in STATES
            }
            category["counts"]["total"] = sum(category["counts"].values())
            records = [item for state in STATES
                       for item in category[f"{state}_requirements"]]
            category["numeric_progress"] = _numeric_progress(records, issues, category)
            categories.append(category)
        group["categories"] = categories
        group["counts"] = {
            "categories": len(categories),
            "uncategorized_requirements": len(group["uncategorized_requirements"]),
        }
        output_programmes.append(group)

    excluded = deepcopy(completion.get("excluded_rules") or [])
    descriptive = [item for item in excluded
                   if isinstance(item, dict) and
                   item.get("reason") == "descriptive_or_non_executable"]
    unresolved_excluded = [item for item in excluded
                           if not isinstance(item, dict) or
                           item.get("reason") != "descriptive_or_non_executable"]
    incomplete = deepcopy(completion.get("incomplete_data") or {})
    if incomplete.get("has_incomplete_data"):
        _issue(issues, "upstream_incomplete_data", "warning", "incomplete_data",
               "Requirement completion reports incomplete source data")
    upstream_validation = deepcopy(completion.get("validation") or {})
    if upstream_validation.get("is_valid") is False:
        _issue(issues, "upstream_completion_invalid", "error", "validation",
               "Requirement completion validation failed")

    validation = {
        "is_valid": not any(issue.severity == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "error_count": sum(issue.severity == "error" for issue in issues),
        "warning_count": sum(issue.severity == "warning" for issue in issues),
        "upstream": upstream_validation,
    }
    return {
        "programme_progress": output_programmes,
        "unresolved_requirements": unresolved,
        "duplicate_requirements": duplicates,
        "descriptive_information": descriptive,
        "excluded_unresolved_rules": unresolved_excluded,
        "incomplete_data": incomplete,
        "validation": validation,
    }


def _programme(role, programme):
    return {
        "requested_programme_role": role,
        "programme": programme,
        "_categories": {},
        "uncategorized_requirements": [],
    }


def _category(normalized):
    return {
        "normalized_category": normalized,
        "source_categories": set(),
        "satisfied_requirements": [],
        "partially_satisfied_requirements": [],
        "remaining_requirements": [],
        "unevaluable_requirements": [],
    }


def _course_progress(record):
    alternatives = deepcopy(record.get("alternatives"))
    completed = deepcopy(record.get("completed_matching_courses") or [])
    ongoing = deepcopy(record.get("ongoing_matching_courses") or [])
    remaining_alternatives = None
    if isinstance(alternatives, dict) and isinstance(alternatives.get("options"), list):
        completed_codes = {item.get("normalized_course_code") for item in completed
                           if isinstance(item, dict)}
        remaining_alternatives = [option for option in alternatives["options"]
                                  if option not in completed_codes]
    return {
        "rule_id": record.get("rule_id"),
        "rule_type": record.get("rule_type"),
        "completion_status": record.get("completion_status"),
        "course_code": record.get("course_code"),
        "course_title": record.get("course_title"),
        "completed_matching_courses": completed,
        "ongoing_matching_courses": ongoing,
        "alternatives": alternatives,
        "remaining_alternatives": remaining_alternatives,
        "measurements": deepcopy(record.get("measurements") or {}),
        "unevaluable_reason": record.get("unevaluable_reason"),
        "source_document": record.get("source_document"),
        "source_heading": record.get("source_heading"),
        "sources": deepcopy(record.get("sources") or []),
    }


def _numeric_progress(records, issues, category):
    by_measure = defaultdict(list)
    for record in records:
        measurements = record.get("measurements")
        if not isinstance(measurements, dict):
            continue
        for measure, values in measurements.items():
            if not _valid_measurement(values):
                _issue(issues, "malformed_numeric_measurement", "warning",
                       f"category:{category['normalized_category']}",
                       f"Rule {record.get('rule_id')!r} has malformed {measure!r} progress")
                continue
            by_measure[measure].append((record, values))

    result = {}
    for measure, entries in by_measure.items():
        if _safe_to_aggregate(entries):
            result[measure] = {
                "aggregation_status": "aggregated",
                "required": sum(values["required"] for _, values in entries),
                "completed": sum(values["completed"] for _, values in entries),
                "remaining": sum(values["remaining"] for _, values in entries),
                "rule_ids": [record.get("rule_id") for record, _ in entries],
            }
        else:
            result[measure] = {
                "aggregation_status": "individual_only",
                "requirements": [
                    {"rule_id": record.get("rule_id"), **deepcopy(values)}
                    for record, values in entries
                ],
            }
            _issue(issues, "incompatible_numeric_measurements", "warning",
                   f"category:{category['normalized_category']}.{measure}",
                   "Numeric measurements were retained individually because safe aggregation could not be established")
    return result


def _safe_to_aggregate(entries):
    if len(entries) <= 1:
        return True
    rule_types = {record.get("rule_type") for record, _ in entries}
    if rule_types == {"required_course"}:
        codes = [record.get("course_code") for record, _ in entries]
        return all(isinstance(code, str) and code for code in codes) and len(codes) == len(set(codes))
    if rule_types == {"choice"}:
        option_sets = []
        for record, _ in entries:
            alternatives = record.get("alternatives") or {}
            options = alternatives.get("options")
            if not isinstance(options, list) or not options:
                return False
            option_set = set(options)
            if any(option_set & previous for previous in option_sets):
                return False
            option_sets.append(option_set)
        return True
    return False


def _valid_measurement(values):
    return (isinstance(values, dict) and
            all(_number(values.get(key)) for key in ("required", "completed", "remaining")))


def _number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and
            math.isfinite(value) and value >= 0)


def _issue(issues, code, severity, path, message):
    issue = CategoryProgressIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
