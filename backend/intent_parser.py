"""Parse literal natural-language preferences into the Task 5.1 model."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import re

from backend.student_preferences import normalize_student_preferences


@dataclass(frozen=True)
class IntentParserIssue:
    code: str
    severity: str
    path: str
    message: str


class IntentExtractor:
    """Provider-independent interface for structured intent extraction."""

    method = "custom"

    def extract(self, query):
        raise NotImplementedError


class DeterministicIntentExtractor(IntentExtractor):
    """Conservative literal phrase extractor with no semantic expansion."""

    method = "deterministic_rules"

    def extract(self, query):
        preferences = {name: [] for name in (
            "interests", "preferred_topics", "avoided_topics", "career_goals")}
        evidence = []
        unsupported = []
        occupied = []

        for match in re.finditer(
                r"(?i)\b(?:i\s+)?(?:absolutely\s+)?(?:do\s+not|don't|dont)\s+want\s+"
                r"(?:an?\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\s+(?:class|classes|course|courses)\b",
                query):
            unsupported.append(_unsupported(match, "timetable_request", self.method))
            occupied.append(match.span())

        evaluation_patterns = (
            ("prefer_projects", r"(?i)\b(?:(?:i(?:'d| would)?\s+)?(?:prefer|like|want)\s+(?:courses?\s+with\s+)?|preferably\s+with\s+)projects?\b", True),
            ("prefer_quizzes", r"(?i)\b(?:i(?:'d| would)?\s+)?(?:prefer|like|want)\s+(?:courses?\s+with\s+)?quizzes?\b", True),
            ("prefer_continuous_evaluation", r"(?i)\b(?:i(?:'d| would)?\s+)?(?:prefer|like|want)\s+continuous\s+evaluation\b", True),
            ("avoid_heavy_exams", r"(?i)\b(?:i\s+want\s+fewer\s+exams|(?:i\s+)?(?:want\s+to\s+)?avoid\s+exam[- ]heavy\s+courses?|(?:i\s+)?don't\s+want\s+exam[- ]heavy\s+courses?)\b", True),
            ("avoid_attendance_heavy", r"(?i)\b(?:i\s+)?(?:don't\s+want|do\s+not\s+want|avoid)\s+attendance[- ]heavy\s+courses?\b", True),
            ("avoid_midsemester_exam", r"(?i)\b(?:i\s+)?(?:don't\s+want|do\s+not\s+want|avoid)\s+(?:a\s+)?mid(?:semester|sem)[- ]?exam\b", True),
            ("avoid_comprehensive_exam", r"(?i)\b(?:i\s+)?(?:don't\s+want|do\s+not\s+want|avoid)\s+(?:a\s+)?comprehensive\s+exam\b", True),
        )
        evaluation = {}
        for key, pattern, value in evaluation_patterns:
            for match in re.finditer(pattern, query):
                if _overlaps(match.span(), occupied):
                    continue
                option = _option(value, match.group(0))
                evaluation[key] = option
                evidence.append(_evidence(
                    "evaluation_preferences." + key, str(value).lower(), match,
                    self.method, option["constraint"], option["priority"]))
                occupied.append(match.span())
        if evaluation:
            preferences["evaluation_preferences"] = evaluation

        workload_patterns = (
            ("low", r"(?i)\b(?:i\s+want\s+)?(?:a\s+)?(?:light|low)\s+workload\b"),
            ("moderate", r"(?i)\b(?:a\s+)?moderate\s+workload(?:\s+is\s+fine)?\b"),
            ("high", r"(?i)\b(?:i\s+)?(?:don't\s+mind|do\s+not\s+mind|want)\s+(?:a\s+)?(?:heavy|high)\s+workload\b"),
        )
        for value, pattern in workload_patterns:
            match = re.search(pattern, query)
            if match and not _overlaps(match.span(), occupied):
                option = _option(value, match.group(0))
                preferences["workload_preference"] = option
                evidence.append(_evidence("workload_preference", value, match,
                                          self.method, option["constraint"],
                                          option["priority"]))
                occupied.append(match.span())
                break

        value_end = (r"(?=\s+(?:and|but)\s+i(?:'d|'m|\s+(?:would|want|like|don't|do|am))"
                     r"|\s*,?\s*preferably|[.!?,;]|$)")
        text_patterns = (
            ("career_goals", r"(?i)\b(?:i\s+)?want\s+to\s+work\s+in\s+(?P<value>.*?)" + value_end),
            ("career_goals", r"(?i)\b(?:i(?:'m| am)\s+)?interested\s+in\s+(?:a\s+)?career\s+in\s+(?P<value>.*?)" + value_end),
            ("career_goals", r"(?i)\b(?:i\s+)?want\s+something\s+useful\s+for\s+(?P<value>.*?)" + value_end),
            ("avoided_topics", r"(?i)\b(?:(?:i\s+)?(?:absolutely\s+)?(?:do\s+not|don't|dont)\s+want|must\s+avoid|avoid|(?:i(?:'m| am)\s+)?not\s+interested\s+in)\s+(?P<value>.*?)" + value_end),
            ("preferred_topics", r"(?i)\b(?:i\s+)?want\s+to\s+study\s+(?P<value>.*?)" + value_end),
            ("preferred_topics", r"(?i)\b(?:i\s+)?want\s+(?:a\s+)?courses?\s+(?:about|on|covering)\s+(?P<value>.*?)" + value_end),
            ("preferred_topics", r"(?i)\b(?:i\s+)?want\s+something\s+related\s+to\s+(?P<value>.*?)" + value_end),
            ("preferred_topics", r"(?i)\b(?:i(?:'d| would)?\s+)?prefer\s+(?!courses?\s+with\s+)(?P<value>.*?)" + value_end),
            ("interests", r"(?i)\b(?:i(?:'m| am)\s+)?interested\s+in\s+(?!a\s+career\s+in\s+)(?P<value>.*?)" + value_end),
            ("interests", r"(?i)\bi\s+like\s+(?!quizzes?\b)(?P<value>.*?)" + value_end),
            ("interests", r"(?i)\b(?P<value>[^.!?,;]+?)\s+is\s+very\s+important\s+to\s+me\b"),
        )
        for preference_type, pattern in text_patterns:
            for match in re.finditer(pattern, query):
                if _overlaps(match.span(), occupied):
                    continue
                phrase = match.group(0)
                option = _option(None, phrase)
                values = _literal_values(match.group("value"))
                for value in values:
                    item = {"value": value, "priority": option["priority"],
                            "constraint": option["constraint"]}
                    preferences[preference_type].append(item)
                    evidence.append(_evidence(preference_type, value, match, self.method,
                                              option["constraint"], option["priority"]))
                if values:
                    occupied.append(match.span())

        preferences = {key: value for key, value in preferences.items() if value not in ([], {})}
        if not evidence and not unsupported:
            unsupported.append({"text": query, "start": 0, "end": len(query),
                                "reason": "no_reliable_preference_extracted",
                                "method": self.method})
        return {"preferences": preferences, "evidence": evidence,
                "uninterpreted": unsupported}


class StudentIntentParser:
    """Orchestrate extraction and Task 5.1 normalization/validation."""

    def __init__(self, extractor=None):
        self.extractor = extractor or DeterministicIntentExtractor()

    def parse(self, query):
        issues = []
        if not isinstance(query, str):
            _issue(issues, "query_not_text", "error", "query",
                   "Natural-language query must be text")
            return _result(query, {}, [], [{"text": deepcopy(query),
                                            "reason": "query_not_text"}],
                           getattr(self.extractor, "method", "custom"), issues)
        if not query.strip():
            _issue(issues, "query_empty", "error", "query",
                   "Natural-language query cannot be empty")
            return _result(query, {"raw_query": query}, [], [{"text": query,
                                                               "reason": "query_empty"}],
                           getattr(self.extractor, "method", "custom"), issues)
        try:
            extracted = self.extractor.extract(query)
        except Exception as error:
            _issue(issues, "extractor_failure", "error", "extractor", str(error))
            extracted = {}
        preferences, evidence, uninterpreted = _validate_extractor_output(
            extracted, issues)
        preferences["raw_query"] = query
        return _result(query, preferences, evidence, uninterpreted,
                       getattr(self.extractor, "method", type(self.extractor).__name__), issues)


def parse_student_intent(query, extractor=None):
    """Convenience interface for a single natural-language query."""
    return StudentIntentParser(extractor).parse(query)


def _validate_extractor_output(raw, issues):
    if not isinstance(raw, dict):
        _issue(issues, "malformed_extractor_output", "error", "extractor",
               "Extractor output must be a mapping")
        return {}, [], []
    preferences = raw.get("preferences", {})
    evidence = raw.get("evidence", [])
    uninterpreted = raw.get("uninterpreted", [])
    if not isinstance(preferences, dict):
        _issue(issues, "malformed_extractor_preferences", "error",
               "extractor.preferences", "Extracted preferences must be a mapping")
        preferences = {}
    if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
        _issue(issues, "malformed_extraction_evidence", "error", "extractor.evidence",
               "Extraction evidence must be a list of mappings")
        evidence = []
    if not isinstance(uninterpreted, list):
        _issue(issues, "malformed_uninterpreted_input", "error",
               "extractor.uninterpreted", "Uninterpreted input must be a list")
        uninterpreted = []
    return deepcopy(preferences), deepcopy(evidence), deepcopy(uninterpreted)


def _result(query, preferences, evidence, uninterpreted, method, parser_issues):
    try:
        normalized = normalize_student_preferences(preferences)
    except (TypeError, ValueError) as error:
        _issue(parser_issues, "preference_normalization_failure", "error",
               "preferences", str(error))
        normalized = normalize_student_preferences({})
    diagnostics = [asdict(issue) for issue in parser_issues]
    if not evidence and isinstance(query, str) and query.strip():
        diagnostics.append({"code": "no_reliable_preference_extracted",
                            "severity": "warning", "path": "query",
                            "message": "No reliable structured preference was extracted"})
    preference_validation = normalized.get("validation") or {}
    parser_errors = [item for item in diagnostics if item.get("severity") == "error"]
    validation = {
        "is_valid": not parser_errors and preference_validation.get("is_valid", False),
        "parser_error_count": len(parser_errors),
        "preference_error_count": preference_validation.get("error_count", 0),
        "issues": diagnostics + deepcopy(preference_validation.get("issues") or []),
    }
    return {"original_query": deepcopy(query), "preferences": normalized,
            "extraction_evidence": evidence, "uninterpreted": uninterpreted,
            "parser_diagnostics": diagnostics, "extraction_method": method,
            "validation": validation}


def _literal_values(raw):
    cleaned = " ".join(raw.strip().split())
    cleaned = re.sub(r"(?i)^(?:courses?\s+(?:about|on)\s+)", "", cleaned)
    cleaned = re.sub(r"(?i)\s+courses?$", "", cleaned)
    cleaned = re.sub(r"(?i)^(?:something\s+(?:about|related\s+to)\s+)", "", cleaned)
    cleaned = re.sub(r"(?i)\s+(?:courses?|topics?)$", "", cleaned)
    values = [" ".join(value.split()) for value in re.split(r"(?i)\s+(?:and|or)\s+", cleaned)]
    return [value for value in values if value]


def _option(value, phrase):
    lowered = phrase.casefold()
    constraint = "hard" if any(token in lowered for token in (
        "absolutely", "must avoid", "only ")) else "soft"
    priority = "high" if any(token in lowered for token in (
        "very important", "top priority", "especially")) else "medium"
    return {"value": value, "priority": priority, "constraint": constraint}


def _evidence(preference_type, value, match, method, constraint, priority):
    return {"preference_type": preference_type, "value": value,
            "source_phrase": match.group(0), "start": match.start(), "end": match.end(),
            "extraction_method": method, "status": "explicit",
            "constraint": constraint, "priority": priority}


def _unsupported(match, reason, method):
    return {"text": match.group(0), "start": match.start(), "end": match.end(),
            "reason": reason, "method": method}


def _overlaps(span, occupied):
    return any(span[0] < end and start < span[1] for start, end in occupied)


def _issue(issues, code, severity, path, message):
    issue = IntentParserIssue(code, severity, path, message)
    if issue not in issues:
        issues.append(issue)
