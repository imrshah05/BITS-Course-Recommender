"""Normalize and validate structured student course preferences."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field


PRIORITIES = ("low", "medium", "high")
CONSTRAINT_KINDS = ("soft", "hard")
WORKLOAD_VALUES = ("low", "moderate", "high", "unspecified")
TEXT_DIMENSIONS = ("interests", "preferred_topics", "avoided_topics", "career_goals")
EVALUATION_PREFERENCES = (
    "prefer_projects",
    "prefer_continuous_evaluation",
    "avoid_heavy_exams",
    "prefer_quizzes",
    "avoid_attendance_heavy",
    "avoid_midsemester_exam",
    "avoid_comprehensive_exam",
)


@dataclass(frozen=True)
class PreferenceIssue:
    code: str
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class PreferenceItem:
    value: str
    original_value: str
    priority: str = "medium"
    constraint: str = "soft"

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PreferenceOption:
    value: object
    priority: str = "medium"
    constraint: str = "soft"

    def to_dict(self):
        return asdict(self)


@dataclass
class StudentPreferences:
    interests: list = field(default_factory=list)
    preferred_topics: list = field(default_factory=list)
    avoided_topics: list = field(default_factory=list)
    career_goals: list = field(default_factory=list)
    workload_preference: PreferenceOption = field(
        default_factory=lambda: PreferenceOption("unspecified"))
    evaluation_preferences: dict = field(default_factory=dict)
    raw_query: object = None
    uninterpreted_inputs: list = field(default_factory=list)
    validation: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        """Construct a deterministic JSON-compatible preference model."""
        if not isinstance(data, dict):
            raise TypeError("Student preference input must be a mapping")
        issues = []
        uninterpreted = []
        dimensions = {
            name: _text_preferences(data.get(name, []), name, issues, uninterpreted)
            for name in TEXT_DIMENSIONS
        }
        workload = _workload(data.get("workload_preference"), issues, uninterpreted)
        evaluation = _evaluation_preferences(
            data.get("evaluation_preferences", {}), issues, uninterpreted)
        raw_query = _raw_query(data.get("raw_query"), issues, uninterpreted)

        preferred = {item.value for item in dimensions["preferred_topics"]}
        avoided = {item.value for item in dimensions["avoided_topics"]}
        for value in sorted(preferred & avoided):
            _issue(issues, "conflicting_topic_preference", "error", "topics",
                   f"Topic {value!r} is both preferred and avoided")

        known = set(TEXT_DIMENSIONS) | {
            "workload_preference", "evaluation_preferences", "raw_query"}
        for key in data:
            if key not in known:
                _issue(issues, "unsupported_preference_field", "warning", key,
                       f"Unsupported preference field {key!r}")
                uninterpreted.append({"path": key, "value": deepcopy(data[key]),
                                      "reason": "unsupported_preference_field"})

        validation = _validation(issues)
        return cls(**dimensions, workload_preference=workload,
                   evaluation_preferences=evaluation, raw_query=raw_query,
                   uninterpreted_inputs=uninterpreted, validation=validation)

    def to_dict(self):
        """Return the normalized model using JSON-compatible values."""
        return {
            **{name: [item.to_dict() for item in getattr(self, name)]
               for name in TEXT_DIMENSIONS},
            "workload_preference": self.workload_preference.to_dict(),
            "evaluation_preferences": {
                key: value.to_dict() for key, value in self.evaluation_preferences.items()},
            "raw_query": self.raw_query,
            "uninterpreted_inputs": deepcopy(self.uninterpreted_inputs),
            "validation": deepcopy(self.validation),
        }


def normalize_student_preferences(data):
    """Normalize structured preference input into a serializable mapping."""
    return StudentPreferences.from_dict(data).to_dict()


def _text_preferences(raw, path, issues, uninterpreted):
    if raw is None:
        return []
    if not isinstance(raw, list):
        _invalid(issues, uninterpreted, "invalid_preference_collection", path, raw,
                 "Preference collection must be a list")
        return []
    items = []
    seen = set()
    for index, value in enumerate(raw):
        item_path = f"{path}[{index}]"
        if isinstance(value, str):
            original, priority, constraint = value, "medium", "soft"
        elif isinstance(value, dict):
            original = value.get("value")
            priority = _enum(value.get("priority", "medium"), PRIORITIES,
                             item_path + ".priority", "invalid_preference_priority",
                             issues, uninterpreted)
            constraint = _enum(value.get("constraint", "soft"), CONSTRAINT_KINDS,
                               item_path + ".constraint", "invalid_constraint_kind",
                               issues, uninterpreted)
            extra = set(value) - {"value", "priority", "constraint"}
            for key in sorted(extra):
                _invalid(issues, uninterpreted, "unsupported_preference_attribute",
                         item_path + "." + key, value[key],
                         f"Unsupported preference attribute {key!r}", severity="warning")
        else:
            _invalid(issues, uninterpreted, "invalid_preference_item", item_path, value,
                     "Preference item must be text or a mapping")
            continue
        if not isinstance(original, str):
            _invalid(issues, uninterpreted, "invalid_preference_value", item_path, value,
                     "Preference value must be text")
            continue
        cleaned = " ".join(original.split())
        if not cleaned:
            _invalid(issues, uninterpreted, "empty_preference_value", item_path, value,
                     "Preference value cannot be empty")
            continue
        if priority is None or constraint is None:
            continue
        normalized = cleaned.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(PreferenceItem(normalized, cleaned, priority, constraint))
    return items


def _workload(raw, issues, uninterpreted):
    if raw is None:
        return PreferenceOption("unspecified")
    if isinstance(raw, str):
        value, priority, constraint = raw, "medium", "soft"
    elif isinstance(raw, dict):
        value = raw.get("value", "unspecified")
        priority = _enum(raw.get("priority", "medium"), PRIORITIES,
                         "workload_preference.priority", "invalid_preference_priority",
                         issues, uninterpreted)
        constraint = _enum(raw.get("constraint", "soft"), CONSTRAINT_KINDS,
                           "workload_preference.constraint", "invalid_constraint_kind",
                           issues, uninterpreted)
    else:
        _invalid(issues, uninterpreted, "invalid_workload_preference",
                 "workload_preference", raw,
                 "Workload preference must be text or a mapping")
        return PreferenceOption("unspecified")
    normalized = value.strip().casefold() if isinstance(value, str) else None
    if normalized not in WORKLOAD_VALUES:
        _invalid(issues, uninterpreted, "unsupported_workload_preference",
                 "workload_preference", raw,
                 f"Workload preference must be one of {WORKLOAD_VALUES}")
        return PreferenceOption("unspecified")
    if priority is None or constraint is None:
        return PreferenceOption(normalized)
    return PreferenceOption(normalized, priority, constraint)


def _evaluation_preferences(raw, issues, uninterpreted):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _invalid(issues, uninterpreted, "invalid_evaluation_preferences",
                 "evaluation_preferences", raw,
                 "Evaluation preferences must be a mapping")
        return {}
    output = {}
    for key, value in raw.items():
        path = f"evaluation_preferences.{key}"
        if key not in EVALUATION_PREFERENCES:
            _invalid(issues, uninterpreted, "unsupported_evaluation_preference", path,
                     value, f"Unsupported evaluation preference {key!r}",
                     severity="warning")
            continue
        if isinstance(value, bool):
            option_value, priority, constraint = value, "medium", "soft"
        elif isinstance(value, dict):
            option_value = value.get("value")
            priority = _enum(value.get("priority", "medium"), PRIORITIES,
                             path + ".priority", "invalid_preference_priority",
                             issues, uninterpreted)
            constraint = _enum(value.get("constraint", "soft"), CONSTRAINT_KINDS,
                               path + ".constraint", "invalid_constraint_kind",
                               issues, uninterpreted)
        else:
            _invalid(issues, uninterpreted, "invalid_evaluation_preference", path,
                     value, "Evaluation preference must be boolean or a mapping")
            continue
        if not isinstance(option_value, bool):
            _invalid(issues, uninterpreted, "invalid_evaluation_preference_value", path,
                     value, "Evaluation preference value must be boolean")
            continue
        if priority is not None and constraint is not None:
            output[key] = PreferenceOption(option_value, priority, constraint)
    return output


def _raw_query(raw, issues, uninterpreted):
    if raw is None:
        return None
    if not isinstance(raw, str):
        _invalid(issues, uninterpreted, "invalid_raw_query", "raw_query", raw,
                 "Raw query must be text")
        return None
    cleaned = raw.strip()
    if not cleaned:
        _invalid(issues, uninterpreted, "empty_raw_query", "raw_query", raw,
                 "Raw query cannot be empty")
        return None
    return raw


def _enum(raw, allowed, path, code, issues, uninterpreted):
    value = raw.strip().casefold() if isinstance(raw, str) else None
    if value not in allowed:
        _invalid(issues, uninterpreted, code, path, raw,
                 f"Value must be one of {allowed}")
        return None
    return value


def _invalid(issues, uninterpreted, code, path, value, message, severity="error"):
    _issue(issues, code, severity, path, message)
    uninterpreted.append({"path": path, "value": deepcopy(value), "reason": code})


def _validation(issues):
    errors = [asdict(issue) for issue in issues if issue.severity == "error"]
    warnings = [asdict(issue) for issue in issues if issue.severity == "warning"]
    return {"is_valid": not errors, "issues": errors + warnings,
            "error_count": len(errors), "warning_count": len(warnings)}


def _issue(issues, code, severity, path, message):
    issue = PreferenceIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
