"""Optional Gemini adapters for preference extraction and semantic matching."""

from copy import deepcopy
import json
import os
import socket
import urllib.error
import urllib.request

from backend.intent_parser import DeterministicIntentExtractor, StudentIntentParser
from backend.preference_matching import (
    CoursePreferenceMatcher,
    LexicalMatchingStrategy,
    MatchingStrategy,
)
from backend.recommendation_engine import RecommendationEngine


DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
PRIORITIES = {"low", "medium", "high"}
CONSTRAINTS = {"soft", "hard"}
TEXT_CATEGORIES = {"interests", "preferred_topics", "avoided_topics", "career_goals"}
EVALUATION_KEYS = {
    "prefer_projects", "prefer_continuous_evaluation", "avoid_heavy_exams",
    "prefer_quizzes", "avoid_attendance_heavy", "avoid_midsemester_exam",
    "avoid_comprehensive_exam",
}


class GeminiError(RuntimeError):
    """A safe, key-free error raised for Gemini configuration or response failures."""


class GeminiClient:
    """Small REST client for Gemini structured output without an SDK dependency."""

    def __init__(self, api_key=None, model=None, endpoint=DEFAULT_ENDPOINT,
                 timeout_seconds=15, transport=None):
        self.api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _urlopen

    @property
    def configured(self):
        return bool(isinstance(self.api_key, str) and self.api_key.strip())

    def generate_structured(self, prompt, schema):
        if not self.configured:
            raise GeminiError("Gemini is not configured")
        url = f"{self.endpoint}/models/{self.model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        try:
            raw = self.transport(request, self.timeout_seconds)
            response = json.loads(raw.decode("utf-8"))
            text = response["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError,
                urllib.error.URLError, TimeoutError, socket.timeout) as error:
            raise GeminiError("Gemini returned an unusable response") from error
        if not isinstance(result, dict):
            raise GeminiError("Gemini structured output must be an object")
        return result


class GeminiIntentExtractor:
    """Extract preferences with Gemini and fall back to deterministic rules."""

    method = "gemini_with_deterministic_fallback"

    def __init__(self, client=None, fallback=None):
        self.client = client or GeminiClient()
        self.fallback = fallback or DeterministicIntentExtractor()

    def extract(self, query):
        try:
            raw = self.client.generate_structured(_intent_prompt(query), INTENT_SCHEMA)
            return _validated_intent(raw, query)
        except (GeminiError, ValueError, TypeError):
            result = self.fallback.extract(query)
            result["uninterpreted"] = list(result.get("uninterpreted") or []) + [{
                "text": query,
                "reason": "gemini_unavailable_or_invalid",
                "method": self.method,
            }]
            return result


class GeminiSemanticMatchingStrategy(MatchingStrategy):
    """Select source-backed semantic fields with deterministic lexical fallback."""

    name = "gemini_semantic_with_lexical_fallback"

    def __init__(self, client=None, fallback=None):
        self.client = client or GeminiClient()
        self.fallback = fallback or LexicalMatchingStrategy()
        self._batch_failed = False

    def begin_batch(self):
        """Reset request-local provider availability before matching a collection."""
        self._batch_failed = False

    def find_matches(self, phrase, fields):
        usable = [field for field in fields if _valid_course_field(field)]
        if not usable:
            return []
        if self._batch_failed:
            return self.fallback.find_matches(phrase, usable)
        try:
            raw = self.client.generate_structured(
                _matching_prompt(phrase, usable), MATCH_SCHEMA)
            indexes = _validated_match_indexes(raw, len(usable))
        except (GeminiError, ValueError, TypeError):
            self._batch_failed = True
            return self.fallback.find_matches(phrase, usable)
        return [{
            "course_field": usable[index]["course_field"],
            "matched_text": usable[index]["text"],
            "source_references": deepcopy(usable[index].get("sources") or []),
        } for index in indexes]


def configured_intent_parser(client=None):
    """Return the existing intent pipeline with optional Gemini understanding."""
    return StudentIntentParser(GeminiIntentExtractor(client=client))


def configured_preference_matcher(client=None):
    """Return the existing matcher with optional Gemini semantic matching."""
    return CoursePreferenceMatcher(GeminiSemanticMatchingStrategy(client=client))


def configured_recommendation_engine(client=None, **kwargs):
    """Inject Gemini preference adapters into the existing safe recommendation engine."""
    return RecommendationEngine(
        intent_parser=configured_intent_parser(client),
        preference_matcher=configured_preference_matcher(client),
        **kwargs,
    )


def gemini_configuration():
    """Expose non-secret configuration for health/config responses."""
    client = GeminiClient()
    return {"configured": client.configured, "model": client.model}


def _urlopen(request, timeout):
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _intent_prompt(query):
    return (
        "Extract only preferences explicitly supported by the student's query. "
        "Do not add academic rules, eligibility, prerequisites, course categories, "
        "or recommendation claims. source_phrase must be an exact substring of the query. "
        "Use hard only for explicit must/only/avoid constraints.\n\nStudent query:\n" + query
    )


def _matching_prompt(phrase, fields):
    records = [{"index": index, "course_field": field["course_field"],
                "text": field["text"]} for index, field in enumerate(fields)]
    return (
        "Select indexes whose supplied course text is semantically relevant to the exact "
        "student preference. Return no indexes when evidence is insufficient. Do not infer "
        "prerequisites, workload, career relevance, course categories, or academic rules.\n"
        f"Preference: {phrase}\nCourse fields: {json.dumps(records, ensure_ascii=False)}"
    )


def _validated_intent(raw, query):
    if set(raw) != {"preferences", "workload", "evaluation"}:
        raise ValueError("Unexpected intent fields")
    rows = raw["preferences"]
    evaluations = raw["evaluation"]
    if not isinstance(rows, list) or not isinstance(evaluations, list):
        raise ValueError("Preference collections must be lists")
    preferences = {category: [] for category in TEXT_CATEGORIES}
    evidence = []
    for row in rows:
        _exact_keys(row, {"category", "value", "priority", "constraint", "source_phrase"})
        category = row["category"]
        value = _text(row["value"])
        priority, constraint = _priority_constraint(row)
        start, end, phrase = _source_span(query, row["source_phrase"])
        if category not in TEXT_CATEGORIES:
            raise ValueError("Unsupported preference category")
        preferences[category].append({"value": value, "priority": priority,
                                      "constraint": constraint})
        evidence.append(_ai_evidence(category, value, phrase, start, end,
                                     priority, constraint))
    workload = raw["workload"]
    if workload is not None:
        _exact_keys(workload, {"value", "priority", "constraint", "source_phrase"})
        if workload["value"] not in {"low", "moderate", "high"}:
            raise ValueError("Unsupported workload value")
        priority, constraint = _priority_constraint(workload)
        start, end, phrase = _source_span(query, workload["source_phrase"])
        preferences["workload_preference"] = {
            "value": workload["value"], "priority": priority, "constraint": constraint}
        evidence.append(_ai_evidence("workload_preference", workload["value"], phrase,
                                     start, end, priority, constraint))
    evaluation_output = {}
    for row in evaluations:
        _exact_keys(row, {"key", "value", "priority", "constraint", "source_phrase"})
        if row["key"] not in EVALUATION_KEYS or not isinstance(row["value"], bool):
            raise ValueError("Unsupported evaluation preference")
        priority, constraint = _priority_constraint(row)
        start, end, phrase = _source_span(query, row["source_phrase"])
        evaluation_output[row["key"]] = {
            "value": row["value"], "priority": priority, "constraint": constraint}
        evidence.append(_ai_evidence("evaluation_preferences." + row["key"],
                                     row["value"], phrase, start, end,
                                     priority, constraint))
    if evaluation_output:
        preferences["evaluation_preferences"] = evaluation_output
    preferences = {key: value for key, value in preferences.items() if value not in ([], {})}
    return {"preferences": preferences, "evidence": evidence, "uninterpreted": []}


def _validated_match_indexes(raw, field_count):
    if set(raw) != {"matching_field_indexes"} or not isinstance(
            raw["matching_field_indexes"], list):
        raise ValueError("Malformed semantic match output")
    indexes = raw["matching_field_indexes"]
    if any(isinstance(index, bool) or not isinstance(index, int)
           or index < 0 or index >= field_count for index in indexes):
        raise ValueError("Semantic match index is out of range")
    return sorted(set(indexes))


def _valid_course_field(field):
    return (isinstance(field, dict) and isinstance(field.get("course_field"), str)
            and isinstance(field.get("text"), str) and field["text"].strip()
            and isinstance(field.get("sources", []), list))


def _exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Structured output has unexpected fields")


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Preference value must be non-empty text")
    return " ".join(value.split())


def _priority_constraint(row):
    if row["priority"] not in PRIORITIES or row["constraint"] not in CONSTRAINTS:
        raise ValueError("Invalid priority or constraint")
    return row["priority"], row["constraint"]


def _source_span(query, source_phrase):
    phrase = _text(source_phrase)
    start = query.casefold().find(phrase.casefold())
    if start < 0:
        raise ValueError("Evidence phrase is not present in the query")
    end = start + len(phrase)
    return start, end, query[start:end]


def _ai_evidence(category, value, phrase, start, end, priority, constraint):
    return {"preference_type": category, "value": value,
            "source_phrase": phrase, "start": start, "end": end,
            "extraction_method": "gemini", "status": "ai_interpreted",
            "constraint": constraint, "priority": priority}


_PREFERENCE_ITEM = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": sorted(TEXT_CATEGORIES)},
        "value": {"type": "string"},
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
        "constraint": {"type": "string", "enum": sorted(CONSTRAINTS)},
        "source_phrase": {"type": "string"},
    },
    "required": ["category", "value", "priority", "constraint", "source_phrase"],
}
_OPTION = {
    "type": ["object", "null"], "additionalProperties": False,
    "properties": {
        "value": {"type": "string", "enum": ["low", "moderate", "high"]},
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
        "constraint": {"type": "string", "enum": sorted(CONSTRAINTS)},
        "source_phrase": {"type": "string"},
    },
    "required": ["value", "priority", "constraint", "source_phrase"],
}
_EVALUATION_ITEM = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "key": {"type": "string", "enum": sorted(EVALUATION_KEYS)},
        "value": {"type": "boolean"},
        "priority": {"type": "string", "enum": sorted(PRIORITIES)},
        "constraint": {"type": "string", "enum": sorted(CONSTRAINTS)},
        "source_phrase": {"type": "string"},
    },
    "required": ["key", "value", "priority", "constraint", "source_phrase"],
}
INTENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "preferences": {"type": "array", "items": _PREFERENCE_ITEM},
        "workload": _OPTION,
        "evaluation": {"type": "array", "items": _EVALUATION_ITEM},
    },
    "required": ["preferences", "workload", "evaluation"],
}
MATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"matching_field_indexes": {
        "type": "array", "items": {"type": "integer"}}},
    "required": ["matching_field_indexes"],
}
